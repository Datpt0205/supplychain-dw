"""Integration: a stored memory, and the chain that proves where it came from.

Against real Postgres, because every link added in migration 0007 is a database
constraint and none of them can be checked in memory: the CHECK that refuses
empty provenance, the foreign key from a memory to the run that made it, and the
two that tie a memory through `memory.item_evidence` to `knowledge.evidence` and
on to the chunk it quotes.

The fixtures seed a real document, a real chunk and a real run. That is the
change of substance: the previous version of this file invented a document id and
hashed an arbitrary string, which the system now refuses — the citation looked
perfect and referred to nothing.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from runtime_harness import (
    RuntimeUrls,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dw_kernel.errors import DomainError
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_knowledge.adapters.evidence_store import SqlEvidenceStore
from dw_knowledge.contracts import EvidenceRef
from dw_memory import tables
from dw_memory.adapters.qdrant_ranker import QdrantMemoryRanker
from dw_memory.contracts import MemoryType, WriteDecision
from dw_memory.policy import MemoryCandidate, MemoryWritePolicy
from dw_memory.service import MemoryService
from dw_platform.application.access_context import AccessContext

pytestmark = pytest.mark.integration

TENANT = uuid.UUID(int=0xCC00)
WORKSPACE = uuid.UUID(int=0xCC01)
SOURCE_TEXT = b"Anh An noi se gui hop dong truoc thu Sau."


OTHER_WORKSPACE = uuid.UUID(int=0xCC02)


@dataclass(frozen=True)
class Source:
    """One seeded document with one chunk, citable as evidence."""

    document_id: uuid.UUID
    chunk_id: uuid.UUID
    provenance_hash: str


@dataclass(frozen=True)
class Seeded:
    """Real source material and a real run, so a citation can be checked.

    The first document's fields sit on `Seeded` itself, as they always have;
    `others` are three more internal documents in the same workspace, because
    auto-write asks for two independent documents and recall orders by how many
    a fact rests on. The last three are the boundary cases.
    """

    run_id: uuid.UUID
    document_id: uuid.UUID
    chunk_id: uuid.UUID
    provenance_hash: str
    others: tuple[Source, ...]
    confidential: Source
    other_workspace: Source
    other_workspace_global: Source


async def _seed_document(
    conn: Any,
    *,
    workspace: uuid.UUID,
    content: bytes,
    classification: str = "internal",
    scope: str = "tenant",
    tenant: uuid.UUID = TENANT,
) -> Source:
    document_id, chunk_id = uuid.uuid4(), uuid.uuid4()
    digest = hashlib.sha256(content).hexdigest()
    await conn.execute(
        text(
            "INSERT INTO knowledge.documents"
            " (id, tenant_id, workspace_id, title, source_uri, created_by,"
            "  classification, scope)"
            " VALUES (:id, :t, :w, 'Bien ban hop', 'file://bien-ban', :actor, :c, :scope)"
        ),
        {
            "id": document_id,
            "t": tenant,
            "w": workspace,
            "actor": uuid.uuid4(),
            "c": classification,
            "scope": scope,
        },
    )
    await conn.execute(
        text(
            "INSERT INTO knowledge.chunks"
            " (id, tenant_id, workspace_id, document_id, seq, content,"
            "  start_offset, end_offset, provenance_hash)"
            " VALUES (:id, :t, :w, :doc, 0, :content, 0, :end, :hash)"
        ),
        {
            "id": chunk_id,
            "t": tenant,
            "w": workspace,
            "doc": document_id,
            "content": content.decode(),
            "end": len(content),
            "hash": digest,
        },
    )
    return Source(document_id, chunk_id, digest)


@pytest.fixture
async def seeded(urls: RuntimeUrls) -> AsyncIterator[Seeded]:
    """Written as the migrator: RLS is what the service is tested through, not what
    the fixture should have to satisfy to lay down a document."""
    engine = create_async_engine(urls.migrator, poolclass=NullPool)
    run_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO platform.worker_runs"
                " (id, thread_id, tenant_id, workspace_id, worker_id, worker_version,"
                "  graph_version, requested_by)"
                " VALUES (:id, :id, :t, :w, 'demo', '1.0.0', '1.0.0', :actor)"
            ),
            {"id": run_id, "t": TENANT, "w": WORKSPACE, "actor": uuid.uuid4()},
        )
        first = await _seed_document(conn, workspace=WORKSPACE, content=SOURCE_TEXT)
        others = tuple(
            [
                await _seed_document(
                    conn, workspace=WORKSPACE, content=SOURCE_TEXT + f" ({n})".encode()
                )
                for n in range(3)
            ]
        )
        confidential = await _seed_document(
            conn, workspace=WORKSPACE, content=b"Gia von 12.000", classification="confidential"
        )
        other_workspace = await _seed_document(
            conn, workspace=OTHER_WORKSPACE, content=b"Ban kia hop rieng."
        )
        other_workspace_global = await _seed_document(
            conn, workspace=OTHER_WORKSPACE, content=b"Thong tu 01/2026.", scope="global"
        )
    try:
        yield Seeded(
            run_id,
            first.document_id,
            first.chunk_id,
            first.provenance_hash,
            others,
            confidential,
            other_workspace,
            other_workspace_global,
        )
    finally:
        await engine.dispose()


@pytest.fixture
async def service(
    urls: RuntimeUrls,
) -> AsyncIterator[tuple[MemoryService, async_sessionmaker[AsyncSession]]]:
    engine = create_async_engine(urls.app, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield (
        MemoryService(
            session_factory=session_factory,
            policy=MemoryWritePolicy(),
            clock=SystemClock(),
            id_generator=Uuid4Generator(),
            evidence_store=SqlEvidenceStore(clock=SystemClock()),
        ),
        session_factory,
    )
    await engine.dispose()


def make_context() -> AccessContext:
    return AccessContext(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=frozenset({"demo.write"}),
        plan_id="professional",
    )


def evidence_for(seeded: Seeded, **overrides: object) -> EvidenceRef:
    fields: dict[str, object] = {
        "evidence_id": uuid.uuid4(),
        "source_document_id": seeded.document_id,
        "chunk_id": seeded.chunk_id,
        "source_version": "1",
        "relevance_score": 0.95,
        "classification": "internal",
        "provenance_hash": seeded.provenance_hash,
    }
    fields.update(overrides)
    return EvidenceRef(**fields)


def cite(source: Source, **overrides: object) -> EvidenceRef:
    fields: dict[str, object] = {
        "evidence_id": uuid.uuid4(),
        "source_document_id": source.document_id,
        "chunk_id": source.chunk_id,
        "source_version": "1",
        "relevance_score": 0.95,
        "classification": "internal",
        "provenance_hash": source.provenance_hash,
    }
    fields.update(overrides)
    return EvidenceRef(**fields)


def corroborated(seeded: Seeded, ref: EvidenceRef) -> tuple[EvidenceRef, ...]:
    """`ref` plus a sound citation of a second document, so the candidate is an
    AUTO_WRITE and its evidence is actually verified. A lone reference goes to
    review, where nothing is checked yet, and a refusal test built on one would
    pass by never reaching the check it names."""
    return (ref, cite(seeded.others[0]))


def candidate(seeded: Seeded, *, sources: int = 2, **overrides: object) -> MemoryCandidate:
    """`sources` distinct documents cited: two is what auto-write asks for."""
    refs = (evidence_for(seeded), *(cite(s) for s in seeded.others[: sources - 1]))
    fields: dict[str, object] = {
        "worker_id": "demo",
        "memory_type": MemoryType.COMMITMENT,
        "content": "Anh An cam kết gửi hợp đồng trước thứ Sáu.",
        "provenance_refs": refs[:sources],
    }
    fields.update(overrides)
    return MemoryCandidate(**fields)


async def _scalar(
    session_factory: async_sessionmaker[AsyncSession], sql: str, **params: object
) -> object:
    async with session_factory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(TENANT)}
        )
        return (await session.execute(text(sql), params)).scalar_one()


# ------------------------------------------------------------- the chain --


async def test_a_stored_memory_traces_back_to_the_document_it_quotes(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """The question Mốc 4 exists for, answered in SQL rather than by inference."""
    memory_service, session_factory = service

    result = await memory_service.propose(
        candidate(seeded), make_context(), created_by_run_id=seeded.run_id
    )

    assert result.outcome.decision is WriteDecision.AUTO_WRITE
    assert result.item is not None
    cited = await _scalar(
        session_factory,
        """
        SELECT array_agg(d.id)
        FROM memory.items i
        JOIN memory.item_evidence ie ON ie.memory_id = i.memory_id
        JOIN knowledge.evidence e ON e.evidence_id = ie.evidence_id
        JOIN knowledge.chunks c ON c.id = e.chunk_id
        JOIN knowledge.documents d ON d.id = c.document_id
        WHERE i.memory_id = :memory_id
        """,
        memory_id=result.item.memory_id,
    )
    assert isinstance(cited, list)
    assert set(cited) == {seeded.document_id, seeded.others[0].document_id}


async def test_the_run_that_wrote_a_memory_can_be_confirmed(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    memory_service, session_factory = service

    result = await memory_service.propose(
        candidate(seeded), make_context(), created_by_run_id=seeded.run_id
    )

    assert result.item is not None
    worker = await _scalar(
        session_factory,
        "SELECT r.worker_id FROM memory.items i"
        " JOIN platform.worker_runs r ON r.id = i.created_by_run_id"
        " WHERE i.memory_id = :memory_id",
        memory_id=result.item.memory_id,
    )
    assert worker == "demo"


async def test_a_memory_cannot_name_a_run_that_never_existed(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """`created_by_run_id` was NOT NULL with no foreign key: any UUID passed."""
    memory_service, _ = service

    with pytest.raises(sa.exc.IntegrityError, match="fk_items_created_by_run_id_worker_runs"):
        await memory_service.propose(
            candidate(seeded), make_context(), created_by_run_id=uuid.uuid4()
        )


# -------------------------------------------------- evidence must be real --


async def test_a_fabricated_hash_is_refused_and_nothing_is_written(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """A syntactically perfect citation to material that was never read. It used to
    be stored as the justification for a fact; now it is refused, and the candidate
    row rolls back with it — a refused write leaves no half-record behind."""
    memory_service, session_factory = service
    before = await _scalar(session_factory, "SELECT count(*) FROM memory.write_candidates")

    with pytest.raises(DomainError, match="hash does not match"):
        await memory_service.propose(
            candidate(
                seeded,
                provenance_refs=corroborated(
                    seeded, evidence_for(seeded, provenance_hash="b" * 64)
                ),
            ),
            make_context(),
            created_by_run_id=seeded.run_id,
        )

    assert await _scalar(session_factory, "SELECT count(*) FROM memory.write_candidates") == before


async def test_evidence_citing_a_chunk_nobody_stored_is_refused(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    memory_service, _ = service

    with pytest.raises(DomainError, match="does not have"):
        await memory_service.propose(
            candidate(
                seeded,
                provenance_refs=corroborated(seeded, evidence_for(seeded, chunk_id=uuid.uuid4())),
            ),
            make_context(),
            created_by_run_id=seeded.run_id,
        )


async def test_evidence_that_cites_one_document_and_quotes_another_is_refused(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """The hash and the chunk agree; the document named does not own that chunk."""
    memory_service, _ = service

    with pytest.raises(DomainError, match="quotes another"):
        await memory_service.propose(
            candidate(
                seeded,
                provenance_refs=corroborated(
                    seeded, evidence_for(seeded, source_document_id=uuid.uuid4())
                ),
            ),
            make_context(),
            created_by_run_id=seeded.run_id,
        )


async def test_evidence_with_no_chunk_is_refused_rather_than_stored_unverified(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """`EvidenceRef` allows it. Accepting it would be the loophole: name a real
    document, invent the hash, and nothing could check either."""
    memory_service, _ = service

    with pytest.raises(DomainError, match="must name the chunk"):
        await memory_service.propose(
            candidate(
                seeded, provenance_refs=corroborated(seeded, evidence_for(seeded, chunk_id=None))
            ),
            make_context(),
            created_by_run_id=seeded.run_id,
        )


# ------------------------------------- classification and workspace come --
# from the evidence, not from the candidate's word for them
#
# Recall filters by a memory's classification, so whatever a candidate claimed
# was what decided who could read the fact later. A candidate that said
# `internal` while quoting a confidential document was recalled for anyone.


async def _counts(session_factory: async_sessionmaker[AsyncSession]) -> tuple[object, object]:
    return (
        await _scalar(session_factory, "SELECT count(*) FROM memory.items"),
        await _scalar(session_factory, "SELECT count(*) FROM memory.write_candidates"),
    )


async def test_a_candidate_claiming_less_than_its_evidence_is_refused_and_nothing_is_written(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """Refused, not raised to `confidential`: the policy already decided on the
    claimed `internal`, and raising it afterwards would auto-write a fact the
    "restricted always needs review" rule never saw at its real level."""
    memory_service, session_factory = service
    before = await _counts(session_factory)

    with pytest.raises(DomainError, match="classification"):
        await memory_service.propose(
            candidate(
                seeded,
                classification="internal",
                provenance_refs=corroborated(seeded, cite(seeded.confidential)),
            ),
            make_context(),
            created_by_run_id=seeded.run_id,
        )

    assert await _counts(session_factory) == before, "no item and no candidate row"


async def test_evidence_records_the_documents_classification_not_the_references(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """The reference says `internal`; the document says `confidential`. The
    document is the owner of that fact, so the evidence row carries it."""
    memory_service, session_factory = service
    understated = cite(seeded.confidential, classification="internal")

    result = await memory_service.propose(
        candidate(
            seeded,
            classification="confidential",
            provenance_refs=corroborated(seeded, understated),
        ),
        make_context(),
        created_by_run_id=seeded.run_id,
    )

    assert result.item is not None
    assert result.item.classification == "confidential"
    recorded = await _scalar(
        session_factory,
        "SELECT classification FROM knowledge.evidence WHERE evidence_id = :e",
        e=understated.evidence_id,
    )
    assert recorded == "confidential"


async def test_a_chunk_from_another_workspace_is_refused_as_evidence(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """Same tenant, so RLS lets the chunk through; the other team's material is
    still not this team's evidence. Recall would hand the fact to this team."""
    memory_service, session_factory = service
    before = await _counts(session_factory)

    with pytest.raises(DomainError, match="workspace"):
        await memory_service.propose(
            candidate(seeded, provenance_refs=corroborated(seeded, cite(seeded.other_workspace))),
            make_context(),
            created_by_run_id=seeded.run_id,
        )

    assert await _counts(session_factory) == before


async def test_a_global_document_from_another_workspace_can_be_cited(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """The same rule search applies: own workspace OR a global document. A
    workspace check stricter than search would refuse evidence the run was
    legitimately shown."""
    memory_service, _ = service

    result = await memory_service.propose(
        candidate(
            seeded, provenance_refs=corroborated(seeded, cite(seeded.other_workspace_global))
        ),
        make_context(),
        created_by_run_id=seeded.run_id,
    )

    assert result.outcome.decision is WriteDecision.AUTO_WRITE
    assert result.item is not None


async def test_another_tenants_global_document_cannot_be_cited(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]],
    seeded: Seeded,
    urls: RuntimeUrls,
) -> None:
    """The evidence query now joins `knowledge.documents`, whose RLS lets every
    tenant read a global document. The chunk is what must stay fenced: chunks
    carry tenant-only RLS, so another tenant's global chunk is simply not there."""
    memory_service, session_factory = service
    engine = create_async_engine(urls.migrator, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            theirs = await _seed_document(
                conn,
                workspace=OTHER_WORKSPACE,
                content=b"Thong tu cua tenant khac.",
                scope="global",
                tenant=uuid.UUID(int=0xDEAD),
            )
    finally:
        await engine.dispose()
    before = await _counts(session_factory)

    with pytest.raises(DomainError, match="does not have"):
        await memory_service.propose(
            candidate(seeded, provenance_refs=corroborated(seeded, cite(theirs))),
            make_context(),
            created_by_run_id=seeded.run_id,
        )

    assert await _counts(session_factory) == before


async def test_a_document_labelled_off_the_ladder_is_refused_not_ranked(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]],
    seeded: Seeded,
    urls: RuntimeUrls,
) -> None:
    """`knowledge.documents.classification` has no CHECK yet. Ranking `public`
    would be a guess about who may recall the memory built on it."""
    memory_service, session_factory = service
    engine = create_async_engine(urls.migrator, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            odd = await _seed_document(
                conn, workspace=WORKSPACE, content=b"Nhan la.", classification="public"
            )
    finally:
        await engine.dispose()
    before = await _counts(session_factory)

    with pytest.raises(DomainError, match="unknown classification"):
        await memory_service.propose(
            candidate(seeded, provenance_refs=corroborated(seeded, cite(odd))),
            make_context(),
            created_by_run_id=seeded.run_id,
        )

    assert await _counts(session_factory) == before


# ------------------------------------------------------- policy, and audit --


async def test_a_single_source_goes_to_review_without_item(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    memory_service, session_factory = service
    before = await _scalar(session_factory, "SELECT count(*) FROM memory.items")

    result = await memory_service.propose(
        candidate(seeded, sources=1, memory_type=MemoryType.PREFERENCE),
        make_context(),
        created_by_run_id=seeded.run_id,
    )

    assert result.outcome.decision is WriteDecision.REVIEW
    assert result.item is None
    assert await _scalar(session_factory, "SELECT count(*) FROM memory.items") == before


async def test_no_provenance_rejected(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    memory_service, _ = service

    result = await memory_service.propose(
        candidate(seeded, provenance_refs=()),
        make_context(),
        created_by_run_id=seeded.run_id,
    )

    assert result.outcome.decision is WriteDecision.REJECT
    assert result.item is None


@pytest.mark.parametrize(
    ("sources", "action"),
    [
        (2, "memory.item_written"),
        (1, "memory.write_held_for_review"),
        (0, "memory.write_rejected"),
    ],
)
async def test_every_outcome_reaches_the_audit_trail(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]],
    seeded: Seeded,
    sources: int,
    action: str,
) -> None:
    """A fact appearing in a customer's system with nobody able to say when it was
    learned is what an audit trail is for — and so is one that was refused."""
    memory_service, session_factory = service

    result = await memory_service.propose(
        candidate(seeded, sources=sources), make_context(), created_by_run_id=seeded.run_id
    )

    resource = str(result.item.memory_id) if result.item else str(result.candidate_id)
    recorded = await _scalar(
        session_factory,
        "SELECT details->>'policy_version' FROM platform.audit_events"
        " WHERE action = :action AND resource_id = :resource",
        action=action,
        resource=resource,
    )
    assert recorded == memory_service.policy.policy_version


async def test_the_database_refuses_a_memory_with_empty_provenance(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """The service refuses it first. This is the constraint behind the service, for
    the second writer, the repair script and the bug that does not go through it."""
    _, session_factory = service

    with pytest.raises(sa.exc.IntegrityError, match="ck_items_provenance_refs"):
        async with session_factory() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(TENANT)}
            )
            await session.execute(
                sa.insert(tables.items).values(
                    memory_id=uuid.uuid4(),
                    tenant_id=TENANT,
                    workspace_id=WORKSPACE,
                    worker_id="demo",
                    memory_type="semantic",
                    subject_refs=[],
                    content="Không nguồn.",
                    structured_facts={},
                    provenance_refs=[],
                    confidence=0.99,
                    classification="internal",
                    valid_from=sa.func.now(),
                    retention_policy="default",
                    memory_schema_version="1.0.0",
                    created_by_run_id=seeded.run_id,
                    created_at=sa.func.now(),
                )
            )


# ------------------------------------------------------------------ recall --
#
# The read side. `propose` could write since the provenance work and nothing read
# it back, so every stored fact was write-only. These cover the four conditions
# recall narrows by, each with the negative case, because a recall that returns
# too much is a disclosure and looks exactly like one that works.


def a_subject() -> str:
    """A subject nobody else in this file uses.

    The database is created once for the whole module and every test writes into
    it as the same tenant, so a fixed subject string would make each test read
    its neighbours' memories — green alone, wrong together, and wrong in the
    direction that hides a leak. Isolating on the filter under test keeps each
    assertion about its own data.
    """
    return f"account:{uuid.uuid4()}"


async def _remember(
    # `Any`, not `object`: these are forwarded straight into `candidate`, whose
    # own signature types each one. `object` makes mypy reject the `sources`
    # int it declares, and narrowing here would mean restating that signature.
    service: MemoryService,
    seeded: Seeded,
    *,
    context: AccessContext,
    **overrides: Any,
) -> None:
    result = await service.propose(
        candidate(seeded, **overrides), context, created_by_run_id=seeded.run_id
    )
    assert result.outcome.decision is WriteDecision.AUTO_WRITE, result.outcome.reason


async def test_recall_returns_what_this_worker_learned_about_the_subject(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    svc, _ = service
    context = make_context()
    subject = a_subject()
    await _remember(svc, seeded, context=context, subject_refs=(subject,))

    found = await svc.recall(
        context, worker_id="demo", subject_refs=(subject,), now=datetime.now(UTC)
    )

    assert [item.content for item in found] == ["Anh An cam kết gửi hợp đồng trước thứ Sáu."]


async def test_recall_without_a_subject_returns_nothing_rather_than_everything(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """ "No subject" must not read as "every subject" — that is the shape of an
    empty filter that widens instead of narrowing."""
    svc, _ = service
    context = make_context()
    subject = a_subject()
    await _remember(svc, seeded, context=context, subject_refs=(subject,))

    assert await svc.recall(context, worker_id="demo", subject_refs=(), now=datetime.now(UTC)) == ()


async def test_recall_does_not_reach_another_tenants_memory(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    svc, _ = service
    subject = a_subject()
    await _remember(svc, seeded, context=make_context(), subject_refs=(subject,))

    intruder = make_context().model_copy(update={"tenant_id": uuid.UUID(int=0xDEAD)})
    found = await svc.recall(
        intruder, worker_id="demo", subject_refs=(subject,), now=datetime.now(UTC)
    )

    assert found == (), "another tenant's subject must return nothing at all"


async def test_recall_does_not_carry_one_workers_memory_into_another(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """A fact learned under a different prompt and toolset is not this worker's
    premise."""
    svc, _ = service
    context = make_context()
    subject = a_subject()
    await _remember(svc, seeded, context=context, subject_refs=(subject,))

    found = await svc.recall(
        context, worker_id="other", subject_refs=(subject,), now=datetime.now(UTC)
    )

    assert found == ()


async def test_recall_will_not_hand_a_run_material_above_its_clearance(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """A memory carries the classification of what it was learned from, so a run
    may only recall what it could have read directly."""
    svc, _ = service
    context = make_context()
    subject = a_subject()
    await _remember(
        svc,
        seeded,
        context=context,
        subject_refs=(subject,),
        classification="confidential",
    )

    at_internal = await svc.recall(
        context, worker_id="demo", subject_refs=(subject,), now=datetime.now(UTC)
    )
    assert at_internal == (), "internal clearance must not recall a confidential fact"

    cleared = context.model_copy(update={"clearance": "confidential"})
    assert (
        len(
            await svc.recall(
                cleared, worker_id="demo", subject_refs=(subject,), now=datetime.now(UTC)
            )
        )
        == 1
    )


async def test_recall_skips_a_fact_whose_validity_window_has_not_opened(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """`valid_from` is set to now at write, so asking about a moment before the
    write is how a closed window is exercised without waiting for one to close."""
    svc, _ = service
    context = make_context()
    subject = a_subject()
    await _remember(svc, seeded, context=context, subject_refs=(subject,))

    earlier = datetime.now(UTC) - timedelta(days=1)
    assert await svc.recall(context, worker_id="demo", subject_refs=(subject,), now=earlier) == ()


async def test_recall_does_not_cross_between_two_teams_of_one_tenant(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """Tenant isolation is not the whole boundary: one company's two sales teams
    share a tenant and must not share what they learned.

    Added because a mutation found it missing — deleting the workspace condition
    from `recall` left every test in this file green, since they all wrote and
    read as the same workspace. A passing cross-tenant test says nothing about
    workspace separation.
    """
    svc, _ = service
    context = make_context()
    subject = a_subject()
    await _remember(svc, seeded, context=context, subject_refs=(subject,))

    other_team = context.model_copy(update={"workspace_id": uuid.UUID(int=0xCC02)})
    found = await svc.recall(
        other_team, worker_id="demo", subject_refs=(subject,), now=datetime.now(UTC)
    )

    assert found == (), "a sibling workspace must not read this team's memory"


# ----------------------------------------------------------- supersession --
#
# Memory used to be append-only: nothing ever wrote `valid_until`, so two facts
# that disagreed both stayed live and recall returned both, ordered by
# confidence. A customer who moved their signing date twice left three live
# answers and the agent believed whichever it had been surest of.


async def test_a_newer_answer_closes_the_older_one_and_recall_returns_one(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    svc, _ = service
    context = make_context()
    subject = a_subject()
    await _remember(
        svc,
        seeded,
        context=context,
        subject_refs=(subject,),
        fact_key="contract_date",
        content="Ký ngày 10/10.",
        sources=3,
    )
    await _remember(
        svc,
        seeded,
        context=context,
        subject_refs=(subject,),
        fact_key="contract_date",
        content="Ký ngày 20/10.",
        sources=2,
    )

    live = await svc.recall(
        context, worker_id="demo", subject_refs=(subject,), now=datetime.now(UTC)
    )

    # The LATER one, not the more confident one — which is the whole point.
    assert [item.content for item in live] == ["Ký ngày 20/10."]


async def test_the_superseded_memory_is_closed_not_deleted(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """ "What did we believe last Tuesday" has to stay answerable, or the system
    cannot explain a decision it already made."""
    svc, session_factory = service
    context = make_context()
    subject = a_subject()
    await _remember(svc, seeded, context=context, subject_refs=(subject,), fact_key="contract_date")
    await _remember(
        svc,
        seeded,
        context=context,
        subject_refs=(subject,),
        fact_key="contract_date",
        content="Ký ngày 20/10.",
    )

    rows = await _scalar(
        session_factory,
        "SELECT count(*) FROM memory.items WHERE subject_refs ? :s",
        s=subject,
    )
    closed = await _scalar(
        session_factory,
        "SELECT count(*) FROM memory.items WHERE subject_refs ? :s AND valid_until IS NOT NULL",
        s=subject,
    )
    assert rows == 2, "both rows are still there"
    assert closed == 1, "exactly the older one is closed"


async def test_a_memory_with_no_fact_key_supersedes_nothing(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """An episode does not replace an episode: a meeting happened, and so did
    another. Only a fact that names the question it answers may close one."""
    svc, _ = service
    context = make_context()
    subject = a_subject()
    await _remember(svc, seeded, context=context, subject_refs=(subject,), content="Họp lần 1.")
    await _remember(svc, seeded, context=context, subject_refs=(subject,), content="Họp lần 2.")

    live = await svc.recall(
        context, worker_id="demo", subject_refs=(subject,), now=datetime.now(UTC)
    )

    assert len(live) == 2


async def test_the_same_question_about_another_customer_is_untouched(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """`fact_key` alone is not identity — every account has a contract date."""
    svc, _ = service
    context = make_context()
    theirs, ours = a_subject(), a_subject()
    await _remember(
        svc,
        seeded,
        context=context,
        subject_refs=(theirs,),
        fact_key="contract_date",
        content="Của khách kia.",
    )
    await _remember(svc, seeded, context=context, subject_refs=(ours,), fact_key="contract_date")

    live = await svc.recall(
        context, worker_id="demo", subject_refs=(theirs,), now=datetime.now(UTC)
    )

    assert [item.content for item in live] == ["Của khách kia."]


async def test_the_superseded_memory_loses_its_vector_and_the_new_one_keeps_it(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]],
    seeded: Seeded,
    ranker: QdrantMemoryRanker,
    indexed: Callable[..., Awaitable[uuid.UUID]],
) -> None:
    """Recall never reads a closed memory, so its point has no reader: it is an
    embedding of an answer the system no longer gives, kept for nobody."""
    svc, _ = service
    svc = replace(svc, vector_purge=ranker)
    context = make_context()
    subject = a_subject()
    older = await svc.propose(
        candidate(seeded, subject_refs=(subject,), fact_key="contract_date"),
        context,
        created_by_run_id=seeded.run_id,
    )
    assert older.item is not None
    await indexed("Ký 10/10.", tenant=TENANT, workspace=WORKSPACE, memory_id=older.item.memory_id)
    newer = await svc.propose(
        candidate(seeded, subject_refs=(subject,), fact_key="contract_date", content="Ký 20/10."),
        context,
        created_by_run_id=seeded.run_id,
    )
    assert newer.item is not None
    await indexed("Ký 20/10.", tenant=TENANT, workspace=WORKSPACE, memory_id=newer.item.memory_id)

    stored = await ranker.client.retrieve(
        ranker.collection, ids=[str(older.item.memory_id), str(newer.item.memory_id)]
    )

    assert {uuid.UUID(str(point.id)) for point in stored} == {newer.item.memory_id}


async def test_a_vector_store_that_is_down_does_not_fail_a_supersession(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """The memory is committed before the point is touched; a stray point costs
    space, a failed proposal would send a stored fact back round the retry loop."""
    svc, _ = service

    @dataclass
    class _Down:
        async def delete(self, memory_ids: Sequence[uuid.UUID]) -> None:
            raise RuntimeError("qdrant xuống")

        async def delete_by_tenant(self, tenant_id: uuid.UUID) -> None:
            raise RuntimeError("qdrant xuống")

    svc = replace(svc, vector_purge=_Down())
    context = make_context()
    subject = a_subject()
    await _remember(svc, seeded, context=context, subject_refs=(subject,), fact_key="contract_date")
    await _remember(
        svc, seeded, context=context, subject_refs=(subject,), fact_key="contract_date", content="2"
    )

    live = await svc.recall(
        context, worker_id="demo", subject_refs=(subject,), now=datetime.now(UTC)
    )
    assert [item.content for item in live] == ["2"]


async def test_supersession_names_what_it_closed_on_the_audit_trail(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """A fact that silently replaces another leaves a trail that records two
    writes and nothing connecting them."""
    svc, session_factory = service
    context = make_context()
    subject = a_subject()
    first = await svc.propose(
        candidate(seeded, subject_refs=(subject,), fact_key="contract_date"),
        context,
        created_by_run_id=seeded.run_id,
    )
    assert first.item is not None
    await _remember(
        svc,
        seeded,
        context=context,
        subject_refs=(subject,),
        fact_key="contract_date",
        content="Ký ngày 20/10.",
    )

    recorded = await _scalar(
        session_factory,
        """
        SELECT count(*) FROM platform.audit_events
        WHERE action = 'memory.item_written'
          AND details -> 'superseded' ? :closed
          AND details ->> 'fact_key' = 'contract_date'
        """,
        closed=str(first.item.memory_id),
    )
    assert recorded == 1


# ------------------------------------------------------------ idempotency --
#
# The outbox delivers at least once: the attempt is counted at claim time and
# the row marked afterwards, so a process that dies between them delivers again.
# Without a key that is a second identical memory, and with two retries a third.


async def test_the_same_delivery_twice_stores_one_memory(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    svc, session_factory = service
    context = make_context()
    subject = a_subject()
    event_id = uuid.uuid4()

    first = await svc.propose(
        candidate(seeded, subject_refs=(subject,)),
        context,
        created_by_run_id=seeded.run_id,
        idempotency_key=event_id,
    )
    second = await svc.propose(
        candidate(seeded, subject_refs=(subject,)),
        context,
        created_by_run_id=seeded.run_id,
        idempotency_key=event_id,
    )

    assert first.item is not None
    assert second.item is not None
    assert second.item.memory_id == first.item.memory_id, "the redelivery returns the first answer"
    stored = await _scalar(
        session_factory,
        "SELECT count(*) FROM memory.items WHERE subject_refs ? :s",
        s=subject,
    )
    assert stored == 1, "one delivery, one memory, however many times it arrives"


async def test_a_redelivery_does_not_write_a_second_audit_entry(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """An audit trail that records the same fact being learned three times
    because a worker restarted is a trail that has to be explained away."""
    svc, session_factory = service
    context = make_context()
    subject = a_subject()
    event_id = uuid.uuid4()
    for _ in range(3):
        await svc.propose(
            candidate(seeded, subject_refs=(subject,)),
            context,
            created_by_run_id=seeded.run_id,
            idempotency_key=event_id,
        )

    written = await _scalar(
        session_factory,
        """
        SELECT count(*) FROM platform.audit_events a
        JOIN memory.items i ON i.memory_id::text = a.resource_id
        WHERE a.action = 'memory.item_written' AND i.subject_refs ? :s
        """,
        s=subject,
    )
    assert written == 1


async def test_without_a_key_each_call_is_its_own_proposal(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """The unchanged behaviour, pinned: a caller that means each proposal
    separately must not start collapsing them."""
    svc, session_factory = service
    context = make_context()
    subject = a_subject()

    await _remember(svc, seeded, context=context, subject_refs=(subject,))
    await _remember(svc, seeded, context=context, subject_refs=(subject,))

    stored = await _scalar(
        session_factory,
        "SELECT count(*) FROM memory.items WHERE subject_refs ? :s",
        s=subject,
    )
    assert stored == 2


# ---------------------------------------------------------------- ranking --
#
# Similarity RANKS and never FILTERS. The rows are the rows the SQL already
# returned; the ranker only says what order to read them in. So an index that is
# empty, stale or poisoned cannot produce a wrong answer — only a worse order.


@dataclass
class _Ranker:
    """Returns whatever order it is told to, and records how it was asked."""

    order: tuple[uuid.UUID, ...] = ()
    raises: Exception | None = None
    asked: list[dict[str, Any]] = field(default_factory=list)

    async def nearest(
        self,
        query: str,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID,
        worker_id: str,
        candidate_ids: Sequence[uuid.UUID],
    ) -> tuple[uuid.UUID, ...]:
        self.asked.append(
            {
                "query": query,
                "tenant_id": tenant_id,
                "worker_id": worker_id,
                "candidate_ids": tuple(candidate_ids),
            }
        )
        if self.raises is not None:
            raise self.raises
        return self.order


async def _three(svc: MemoryService, seeded: Seeded, context: AccessContext, subject: str) -> None:
    for text_, sources in (("Nhất.", 4), ("Nhì.", 3), ("Ba.", 2)):
        await _remember(
            svc,
            seeded,
            context=context,
            subject_refs=(subject,),
            content=text_,
            sources=sources,
        )


async def test_similarity_decides_the_order_when_a_ranker_is_wired(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    svc, _ = service
    context = make_context()
    subject = a_subject()
    await _three(svc, seeded, context, subject)
    by_confidence = await svc.recall(
        context, worker_id="demo", subject_refs=(subject,), now=datetime.now(UTC)
    )
    assert [i.content for i in by_confidence] == ["Nhất.", "Nhì.", "Ba."]

    reversed_order = tuple(item.memory_id for item in reversed(by_confidence))
    ranked = replace(svc, ranker=_Ranker(order=reversed_order))
    found = await ranked.recall(
        context,
        worker_id="demo",
        subject_refs=(subject,),
        now=datetime.now(UTC),
        query="khi nào ký hợp đồng",
    )

    assert [i.content for i in found] == ["Ba.", "Nhì.", "Nhất."]


async def test_a_memory_the_ranker_never_saw_still_surfaces(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """The reason this ranks instead of filtering. Memories written before the
    index existed would vanish silently if ids from the store chose the rows."""
    svc, _ = service
    context = make_context()
    subject = a_subject()
    await _three(svc, seeded, context, subject)
    everything = await svc.recall(
        context, worker_id="demo", subject_refs=(subject,), now=datetime.now(UTC)
    )
    only_last = (everything[-1].memory_id,)

    found = await replace(svc, ranker=_Ranker(order=only_last)).recall(
        context, worker_id="demo", subject_refs=(subject,), now=datetime.now(UTC), query="x"
    )

    assert len(found) == 3, "the two it had no opinion about must not disappear"
    assert found[0].content == "Ba.", "the one it ranked leads"
    assert [i.content for i in found[1:]] == ["Nhất.", "Nhì."], "the rest keep their own order"


async def test_an_id_the_ranker_invents_cannot_add_a_row(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """A poisoned or stale index proposing a memory id — even a real one from
    another subject — changes nothing: the rows came from the query."""
    svc, _ = service
    context = make_context()
    mine, theirs = a_subject(), a_subject()
    await _remember(svc, seeded, context=context, subject_refs=(mine,), content="Của tôi.")
    await _remember(svc, seeded, context=context, subject_refs=(theirs,), content="Của khách kia.")
    stolen = await svc.recall(
        context, worker_id="demo", subject_refs=(theirs,), now=datetime.now(UTC)
    )

    found = await replace(svc, ranker=_Ranker(order=(stolen[0].memory_id,))).recall(
        context, worker_id="demo", subject_refs=(mine,), now=datetime.now(UTC), query="x"
    )

    assert [i.content for i in found] == ["Của tôi."]


async def test_a_ranker_that_fails_leaves_the_order_it_found(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """Degrades to what every run did before this existed — not to no memory,
    and certainly not to a failed run."""
    svc, _ = service
    context = make_context()
    subject = a_subject()
    await _three(svc, seeded, context, subject)

    broken = replace(svc, ranker=_Ranker(raises=RuntimeError("qdrant xuống")))
    found = await broken.recall(
        context, worker_id="demo", subject_refs=(subject,), now=datetime.now(UTC), query="x"
    )

    assert [i.content for i in found] == ["Nhất.", "Nhì.", "Ba."]


async def test_the_ranker_is_asked_under_the_runs_own_tenant_and_worker(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    svc, _ = service
    context = make_context()
    subject = a_subject()
    await _remember(svc, seeded, context=context, subject_refs=(subject,))
    ranker = _Ranker()

    await replace(svc, ranker=ranker).recall(
        context, worker_id="demo", subject_refs=(subject,), now=datetime.now(UTC), query="câu hỏi"
    )

    assert ranker.asked[0]["tenant_id"] == TENANT
    assert ranker.asked[0]["worker_id"] == "demo"
    assert ranker.asked[0]["query"] == "câu hỏi"


async def test_the_ranker_is_handed_exactly_the_rows_recall_found(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """The store orders these ids and no others; it never chooses its own."""
    svc, _ = service
    context = make_context()
    subject = a_subject()
    await _three(svc, seeded, context, subject)
    found = await svc.recall(
        context, worker_id="demo", subject_refs=(subject,), now=datetime.now(UTC)
    )
    ranker = _Ranker()

    await replace(svc, ranker=ranker).recall(
        context, worker_id="demo", subject_refs=(subject,), now=datetime.now(UTC), query="x"
    )

    assert set(ranker.asked[0]["candidate_ids"]) == {item.memory_id for item in found}


async def test_no_query_means_the_ranker_is_not_even_asked(
    service: tuple[MemoryService, async_sessionmaker[AsyncSession]], seeded: Seeded
) -> None:
    """A run with nothing to compare against should not pay for a vector search."""
    svc, _ = service
    context = make_context()
    subject = a_subject()
    await _remember(svc, seeded, context=context, subject_refs=(subject,))
    ranker = _Ranker()

    await replace(svc, ranker=ranker).recall(
        context, worker_id="demo", subject_refs=(subject,), now=datetime.now(UTC)
    )

    assert ranker.asked == []
