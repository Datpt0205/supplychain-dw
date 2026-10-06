"""A held memory, from the policy to a person and back, against real Postgres.

The whole lane, with nothing faked but the run the approval no longer has:
`MemoryService.propose` holds a candidate and opens the approval,
`ApproveAndResumeService.decide` (wired as the API wires it) records the
decision and the outbox event in one transaction, the outbox drain claims it,
and the worker's handler settles it. Real Postgres because the boundaries under
test are RLS, a savepoint and a row lock, and none of them exist in memory.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast

import pytest
from pg_test_db import DatabaseUrls
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dw_agent_runtime.approval_flow import ApproveAndResumeService
from dw_kernel.errors import ConflictError, DomainError, NotFoundError, PermissionDeniedError
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_knowledge.adapters.evidence_store import SqlEvidenceStore
from dw_knowledge.contracts import EvidenceRef
from dw_memory.contracts import MemoryType, WriteDecision
from dw_memory.policy import MemoryCandidate, MemoryWritePolicy
from dw_memory.review import MEMORY_REVIEW, MEMORY_REVIEW_DECIDED, require_clearance_for_review
from dw_memory.service import MemoryService
from dw_platform.adapters.persistence.outbox_drain import SqlOutboxDrain
from dw_platform.adapters.persistence.uow import SqlPlatformUnitOfWorkFactory
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.domain.approval import ApprovalStatus
from dw_platform.domain.outbox import OutboxEvent
from dw_worker.consumers.memory import build_review_handler
from dw_worker.consumers.outbox import UndeliverableEventError

pytestmark = pytest.mark.integration

CONTENT = "Anh An cam kết gửi hợp đồng trước thứ Sáu."


@dataclass(frozen=True)
class Source:
    document_id: uuid.UUID
    chunk_id: uuid.UUID
    provenance_hash: str


@dataclass(frozen=True)
class Tenancy:
    """One tenant and workspace per test: the outbox drain reads across tenants,
    so a shared one would hand a test events another test left behind."""

    tenant: uuid.UUID
    workspace: uuid.UUID
    run_id: uuid.UUID
    proposer: uuid.UUID
    internal: Source
    restricted: Source


@dataclass
class Lane:
    memory: MemoryService
    approvals: ApproveAndResumeService
    drain: SqlOutboxDrain
    app: async_sessionmaker[AsyncSession]
    migrator: async_sessionmaker[AsyncSession]


async def _seed_document(
    session: AsyncSession, tenancy: dict[str, uuid.UUID], *, classification: str, body: bytes
) -> Source:
    document_id, chunk_id = uuid.uuid4(), uuid.uuid4()
    digest = hashlib.sha256(body).hexdigest()
    await session.execute(
        text(
            "INSERT INTO knowledge.documents"
            " (id, tenant_id, workspace_id, title, source_uri, created_by, classification, scope)"
            " VALUES (:id, :t, :w, 'Bien ban', 'file://bien-ban', :actor, :c, 'tenant')"
        ),
        {"id": document_id, "actor": uuid.uuid4(), "c": classification, **tenancy},
    )
    await session.execute(
        text(
            "INSERT INTO knowledge.chunks"
            " (id, tenant_id, workspace_id, document_id, seq, content,"
            "  start_offset, end_offset, provenance_hash)"
            " VALUES (:id, :t, :w, :doc, 0, :content, 0, :end, :hash)"
        ),
        {
            "id": chunk_id,
            "doc": document_id,
            "content": body.decode(),
            "end": len(body),
            "hash": digest,
            **tenancy,
        },
    )
    return Source(document_id, chunk_id, digest)


@pytest.fixture
async def lane(worker_db: DatabaseUrls) -> AsyncIterator[Lane]:
    app_engine = create_async_engine(worker_db.app, poolclass=NullPool)
    migrator_engine = create_async_engine(worker_db.migrator, poolclass=NullPool)
    app = async_sessionmaker(app_engine, class_=AsyncSession, expire_on_commit=False)
    clock, ids = SystemClock(), Uuid4Generator()
    yield Lane(
        memory=MemoryService(
            session_factory=app,
            policy=MemoryWritePolicy(),
            clock=clock,
            id_generator=ids,
            evidence_store=SqlEvidenceStore(clock=clock),
        ),
        # As `dw_api.bootstrap.runtime` wires it. No runner and no run store: a
        # memory review has no run, and touching either would fail loudly.
        approvals=ApproveAndResumeService(
            uow_factory=SqlPlatformUnitOfWorkFactory(app),
            runner=cast(Any, None),
            run_store=cast(Any, None),
            clock=clock,
            id_generator=ids,
            strict_approval_prefixes=frozenset({"memory."}),
            decision_guards={MEMORY_REVIEW: require_clearance_for_review},
        ),
        drain=SqlOutboxDrain(app, clock),
        app=app,
        migrator=async_sessionmaker(migrator_engine, class_=AsyncSession, expire_on_commit=False),
    )
    await app_engine.dispose()
    await migrator_engine.dispose()


@pytest.fixture
async def tenancy(lane: Lane) -> Tenancy:
    """Written as the migrator: the fixture lays down source material, it is not
    what RLS is being tested through."""
    ids = {"t": uuid.uuid4(), "w": uuid.uuid4()}
    run_id, proposer = uuid.uuid4(), uuid.uuid4()
    async with lane.migrator() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO platform.worker_runs"
                " (id, thread_id, tenant_id, workspace_id, worker_id, worker_version,"
                "  graph_version, requested_by)"
                " VALUES (:id, :id, :t, :w, 'demo', '1.0.0', '1.0.0', :actor)"
            ),
            {"id": run_id, "actor": proposer, **ids},
        )
        internal = await _seed_document(
            session, ids, classification="internal", body=CONTENT.encode()
        )
        restricted = await _seed_document(
            session, ids, classification="restricted", body=b"Gia von 12.000"
        )
    return Tenancy(ids["t"], ids["w"], run_id, proposer, internal, restricted)


def access(
    tenancy: Tenancy,
    *,
    principal: uuid.UUID | None = None,
    clearance: str = "restricted",
    scopes: frozenset[str] = frozenset({"approvals.decide", "memory.read"}),
    tenant: uuid.UUID | None = None,
    workspace: uuid.UUID | None = None,
) -> AccessContext:
    return AccessContext(
        tenant_id=tenant or tenancy.tenant,
        workspace_id=workspace or tenancy.workspace,
        principal_id=principal or uuid.uuid4(),
        roles=frozenset({"approver"}),
        scopes=scopes,
        clearance=clearance,
        plan_id="professional",
    )


def cite(source: Source, classification: str = "internal") -> EvidenceRef:
    return EvidenceRef(
        evidence_id=uuid.uuid4(),
        source_document_id=source.document_id,
        chunk_id=source.chunk_id,
        source_version="1",
        relevance_score=0.9,
        classification=classification,
        provenance_hash=source.provenance_hash,
    )


async def hold(lane: Lane, tenancy: Tenancy, *, restricted: bool = False) -> uuid.UUID:
    """Propose a candidate the policy holds; return the approval it opened.

    One source is too few to write alone; a restricted one always needs review.
    """
    source = tenancy.restricted if restricted else tenancy.internal
    label = "restricted" if restricted else "internal"
    result = await lane.memory.propose(
        MemoryCandidate(
            worker_id="demo",
            memory_type=MemoryType.COMMITMENT,
            content=CONTENT,
            subject_refs=("crm:account:1",),
            provenance_refs=(cite(source, label),),
            classification=label,
            fact_key="contract_date",
        ),
        access(tenancy, principal=tenancy.proposer),
        created_by_run_id=tenancy.run_id,
    )
    assert result.outcome.decision is WriteDecision.REVIEW
    rows = await rows_of(
        lane,
        tenancy,
        "SELECT id FROM platform.approval_requests WHERE payload->>'candidate_id' = :c",
        c=str(result.candidate_id),
    )
    assert len(rows) == 1
    return cast(uuid.UUID, rows[0].id)


async def rows_of(lane: Lane, tenancy: Tenancy, sql: str, **params: object) -> list[Any]:
    async with lane.app() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenancy.tenant)}
        )
        return list((await session.execute(text(sql), params)).all())


async def decide(
    lane: Lane, approval_id: uuid.UUID, context: AccessContext, *, approve: bool
) -> None:
    await lane.approvals.decide(
        approval_id=approval_id,
        approve=approve,
        comment="đã đối chiếu biên bản",
        context=context,
        authorization=ScopeAuthorizationService(),
    )


async def decided_event(lane: Lane, approval_id: uuid.UUID) -> OutboxEvent:
    """The event the decision wrote, claimed the way the worker claims it."""
    claimed = await lane.drain.claim_batch(
        event_types=[MEMORY_REVIEW_DECIDED], limit=100, max_attempts=100
    )
    [event] = [event for event in claimed if event.aggregate_id == approval_id]
    return event


async def items(lane: Lane, tenancy: Tenancy) -> list[Any]:
    return await rows_of(lane, tenancy, "SELECT * FROM memory.items")


async def audit(lane: Lane, tenancy: Tenancy, action: str) -> list[Any]:
    return await rows_of(
        lane,
        tenancy,
        "SELECT actor_id, details FROM platform.audit_events WHERE action = :a",
        a=action,
    )


# ------------------------------------------------------------- holding --


async def test_a_held_candidate_opens_one_pending_review_without_its_content(
    lane: Lane, tenancy: Tenancy
) -> None:
    approval_id = await hold(lane, tenancy)

    [row] = await rows_of(
        lane, tenancy, "SELECT * FROM platform.approval_requests WHERE id = :id", id=approval_id
    )
    assert row.approval_type == MEMORY_REVIEW
    assert row.status == "pending"
    assert row.run_id is None
    assert row.requested_by == tenancy.proposer
    # Identifiers and the label: the inbox shows this to every holder of
    # `approvals.read`, whatever their clearance.
    assert set(row.payload) == {"candidate_id", "worker_id", "memory_type", "classification"}
    assert CONTENT not in str(row.payload)
    [held] = await audit(lane, tenancy, "memory.write_held_for_review")
    assert held.details["approval_id"] == str(approval_id)


# ------------------------------------------------------------ deciding --


async def test_approving_writes_one_item_with_its_evidence_once(
    lane: Lane, tenancy: Tenancy
) -> None:
    approval_id = await hold(lane, tenancy)
    approver = uuid.uuid4()
    await decide(lane, approval_id, access(tenancy, principal=approver), approve=True)
    event = await decided_event(lane, approval_id)
    handle = build_review_handler(lane.memory)

    assert await handle(event) == "memory review written"
    # At-least-once: the same event again settles nothing new.
    assert await handle(event) == "memory review written"

    [item] = await items(lane, tenancy)
    assert item.content == CONTENT
    assert list(item.subject_refs) == ["crm:account:1"]
    assert item.fact_key == "contract_date"
    evidence = await rows_of(
        lane,
        tenancy,
        "SELECT ie.evidence_id FROM memory.item_evidence ie"
        " JOIN knowledge.evidence e ON e.evidence_id = ie.evidence_id"
        " WHERE ie.memory_id = :m AND e.chunk_id = :c",
        m=item.memory_id,
        c=tenancy.internal.chunk_id,
    )
    assert len(evidence) == 1
    [written] = await audit(lane, tenancy, "memory.written_after_review")
    assert written.actor_id == approver
    assert written.details["approval_id"] == str(approval_id)


async def test_rejecting_writes_nothing_and_says_so(lane: Lane, tenancy: Tenancy) -> None:
    approval_id = await hold(lane, tenancy)
    await decide(lane, approval_id, access(tenancy), approve=False)
    event = await decided_event(lane, approval_id)
    handle = build_review_handler(lane.memory)

    assert await handle(event) == "memory review settled without a write"
    assert await handle(event) == "memory review settled without a write"

    assert await items(lane, tenancy) == []
    assert len(await audit(lane, tenancy, "memory.review_rejected")) == 1
    [candidate] = await rows_of(lane, tenancy, "SELECT decision FROM memory.write_candidates")
    assert candidate.decision == "reject"


async def test_an_approval_does_not_excuse_evidence_that_no_longer_verifies(
    lane: Lane, tenancy: Tenancy
) -> None:
    """The person approved the fact, not a citation the system can no longer
    check: the chunk it quotes was deleted while the review waited."""
    approval_id = await hold(lane, tenancy)
    async with lane.migrator() as session, session.begin():
        await session.execute(
            text("DELETE FROM knowledge.chunks WHERE id = :id"), {"id": tenancy.internal.chunk_id}
        )
    await decide(lane, approval_id, access(tenancy), approve=True)
    event = await decided_event(lane, approval_id)
    handle = build_review_handler(lane.memory)

    with pytest.raises(UndeliverableEventError):
        await handle(event)
    # The retry the outbox makes anyway finds it settled and records nothing new.
    assert await handle(event) == "memory review settled without a write"

    assert await items(lane, tenancy) == []
    assert len(await audit(lane, tenancy, "memory.review_failed")) == 1
    assert await audit(lane, tenancy, "memory.written_after_review") == []


# ------------------------------------------------------ who may decide --


async def _still_pending(lane: Lane, tenancy: Tenancy, approval_id: uuid.UUID) -> None:
    [row] = await rows_of(
        lane,
        tenancy,
        "SELECT status, (SELECT count(*) FROM platform.approval_decisions"
        "  WHERE request_id = :id) AS decisions,"
        " (SELECT count(*) FROM platform.outbox_events WHERE aggregate_id = :id) AS events"
        " FROM platform.approval_requests WHERE id = :id",
        id=approval_id,
    )
    assert (row.status, row.decisions, row.events) == (ApprovalStatus.PENDING.value, 0, 0)


async def test_another_tenant_cannot_decide_it(lane: Lane, tenancy: Tenancy) -> None:
    approval_id = await hold(lane, tenancy)
    outsider = access(tenancy, tenant=uuid.uuid4(), workspace=uuid.uuid4())

    with pytest.raises(NotFoundError):
        await decide(lane, approval_id, outsider, approve=True)

    await _still_pending(lane, tenancy, approval_id)
    assert await items(lane, tenancy) == []


async def test_a_decider_not_cleared_for_the_memory_cannot_decide_it(
    lane: Lane, tenancy: Tenancy
) -> None:
    approval_id = await hold(lane, tenancy, restricted=True)

    with pytest.raises(PermissionDeniedError, match="clearance"):
        await decide(lane, approval_id, access(tenancy, clearance="internal"), approve=True)

    await _still_pending(lane, tenancy, approval_id)


async def test_the_person_whose_run_proposed_it_cannot_approve_it(
    lane: Lane, tenancy: Tenancy
) -> None:
    approval_id = await hold(lane, tenancy)

    with pytest.raises(ConflictError, match="separation of duties"):
        await decide(lane, approval_id, access(tenancy, principal=tenancy.proposer), approve=True)

    await _still_pending(lane, tenancy, approval_id)


# ------------------------------------------------- reading the content --


async def test_the_content_is_served_to_a_cleared_reader_of_the_workspace_only(
    lane: Lane, tenancy: Tenancy
) -> None:
    approval_id = await hold(lane, tenancy, restricted=True)
    [row] = await rows_of(
        lane,
        tenancy,
        "SELECT payload->>'candidate_id' AS c FROM platform.approval_requests WHERE id = :id",
        id=approval_id,
    )
    candidate_id = uuid.UUID(row.c)

    read = await lane.memory.get_candidate(candidate_id, access(tenancy))
    assert read.content == CONTENT
    with pytest.raises(PermissionDeniedError):
        await lane.memory.get_candidate(candidate_id, access(tenancy, clearance="confidential"))
    with pytest.raises(NotFoundError):
        await lane.memory.get_candidate(candidate_id, access(tenancy, workspace=uuid.uuid4()))
    with pytest.raises(NotFoundError):
        await lane.memory.get_candidate(
            candidate_id, access(tenancy, tenant=uuid.uuid4(), workspace=uuid.uuid4())
        )


# ------------------------------------------------- what the label rests on --


async def _approvals(lane: Lane, tenancy: Tenancy) -> list[Any]:
    return await rows_of(
        lane,
        tenancy,
        "SELECT id FROM platform.approval_requests WHERE approval_type = :t",
        t=MEMORY_REVIEW,
    )


async def test_a_held_candidate_claiming_less_than_its_evidence_opens_no_review(
    lane: Lane, tenancy: Tenancy
) -> None:
    """The label on the approval decides who may read the content and who may
    decide. A producer's claim of `internal` over a restricted source would hand
    restricted content to an `internal` reviewer, so the claim is held to the
    cited documents before any approval is opened."""
    with pytest.raises(DomainError, match="lower classification"):
        await lane.memory.propose(
            MemoryCandidate(
                worker_id="demo",
                memory_type=MemoryType.COMMITMENT,
                content="Gia von 12.000",
                subject_refs=("crm:account:1",),
                provenance_refs=(cite(tenancy.restricted, "internal"),),
                classification="internal",
            ),
            access(tenancy, principal=tenancy.proposer),
            created_by_run_id=tenancy.run_id,
        )

    assert await _approvals(lane, tenancy) == []
    assert await rows_of(lane, tenancy, "SELECT id FROM memory.write_candidates") == []
    # Verified, not recorded: the evidence is written when the item is.
    assert await rows_of(lane, tenancy, "SELECT evidence_id FROM knowledge.evidence") == []


async def test_a_held_candidate_citing_nothing_real_reaches_no_one(
    lane: Lane, tenancy: Tenancy
) -> None:
    forged = Source(tenancy.internal.document_id, tenancy.internal.chunk_id, "0" * 64)

    with pytest.raises(DomainError, match="hash"):
        await lane.memory.propose(
            MemoryCandidate(
                worker_id="demo",
                memory_type=MemoryType.COMMITMENT,
                content=CONTENT,
                subject_refs=("crm:account:1",),
                provenance_refs=(cite(forged),),
                classification="internal",
            ),
            access(tenancy, principal=tenancy.proposer),
            created_by_run_id=tenancy.run_id,
        )

    assert await _approvals(lane, tenancy) == []


async def test_holding_a_candidate_writes_no_evidence_yet(lane: Lane, tenancy: Tenancy) -> None:
    await hold(lane, tenancy)

    assert await rows_of(lane, tenancy, "SELECT evidence_id FROM knowledge.evidence") == []
