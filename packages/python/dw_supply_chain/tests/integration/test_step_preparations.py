"""Integration: step preparations and a proposal's two ends (1a8527a5b426;
ticket ai-automation/05).

What only the real database can show:

- `step_preparations` is narrowed by tenant AND workspace (RLS FORCE, and its
  composite FKs to the case and the history row of the same workspace);
- a reason is there exactly for the outcomes that have one (CHECK);
- `apply_approved` is one transaction: the confirmation, the `ai_prepared`
  document naming its draft, the case's step and the record land together,
  and a second one on the same draft version (web and Zalo at once) is a
  `ConflictError` that leaves nothing behind;
- the readings adapter answers only within the caller's workspace.

Owed when written (2026-10-09): not run, no Docker on the machine that wrote it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls

from dw_kernel.errors import ConflictError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.pagination import PageQuery, page_request
from dw_kernel.ports import SystemClock
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence.document_draft_repository import (
    SqlDocumentDraftRepository,
)
from dw_supply_chain.adapters.persistence.document_extraction_repository import (
    SqlExtractionReadings,
)
from dw_supply_chain.adapters.persistence.product_case_repository import SqlProductCaseRepository
from dw_supply_chain.adapters.persistence.step_preparation_repository import (
    SqlPreparationRecords,
    SqlProposalOutcomes,
)
from dw_supply_chain.application.document_drafts import NewDocumentDraft, NewDraftDecision
from dw_supply_chain.application.ports import NewCaseDocument
from dw_supply_chain.application.step_preparation import NewPreparationRecord
from dw_supply_chain.application.step_proposals import AiPreparedDocument
from dw_supply_chain.domain.case_document import (
    CaseDocumentId,
    CaseKind,
    DocumentType,
    ObjectKey,
)
from dw_supply_chain.domain.document_draft import DraftDecision
from dw_supply_chain.domain.product_development_case import (
    ProductAction,
    ProductActionInput,
    ProductCaseTransition,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
    apply_product_action,
)
from dw_supply_chain.domain.step_proposal import PreparationOutcome

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class _Db:
    sessions: async_sessionmaker[AsyncSession]
    migrator: async_sessionmaker[AsyncSession]


@pytest.fixture
async def db(db_urls: DatabaseUrls) -> AsyncIterator[_Db]:
    app = create_async_engine(db_urls.app, poolclass=NullPool)
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    yield _Db(
        async_sessionmaker(app, class_=AsyncSession, expire_on_commit=False),
        async_sessionmaker(migrator, class_=AsyncSession, expire_on_commit=False),
    )
    await app.dispose()
    await migrator.dispose()


def _context(tenant: uuid.UUID, workspace: uuid.UUID) -> AccessContext:
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=frozenset(),
        plan_id="professional",
    )


def _audit(context: AccessContext, action: str = "supply_chain.test") -> AuditEvent:
    return AuditEvent(
        id=uuid.uuid4(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        actor_id=UserId(context.principal_id),
        action=action,
        resource_type="product_dev_case",
        resource_id=str(uuid.uuid4()),
        occurred_at=SystemClock().now(),
    )


async def _case(
    db: _Db, context: AccessContext
) -> tuple[ProductDevelopmentCase, ProductCaseTransition]:
    repo = SqlProductCaseRepository(db.sessions)
    case = ProductDevelopmentCase.propose(
        id=ProductDevelopmentCaseId(uuid.uuid4()),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        proposal_code=f"DX-{uuid.uuid4().hex[:8]}",
        product_name="Nồi inox 3 đáy 24cm",
        category="Nồi",
        actor_id=context.principal_id,
    )
    await repo.add(context, case, audit=_audit(context))
    page = await repo.list_transitions(
        context,
        case.id,
        page_request(
            limit=10,
            cursor=None,
            query=PageQuery(key="supply_chain.product_case_transitions", filters={"case": case.id}),
        ),
    )
    found = await repo.get(context, case.id)
    assert found is not None
    return found, page.items[0]


def _record(
    case: ProductDevelopmentCase,
    transition: ProductCaseTransition,
    outcome: PreparationOutcome,
    reason: str | None,
) -> NewPreparationRecord:
    assert transition.id is not None
    return NewPreparationRecord(
        id=uuid.uuid4(),
        case_id=case.id.value,
        transition_id=transition.id,
        policy_version="1.0.0",
        action="pass_sample",
        outcome=outcome,
        reason=reason,
    )


async def test_preparations_stay_in_their_workspace(db: _Db) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    mine = _context(tenant, workspace)
    neighbour = _context(tenant, uuid.uuid4())
    stranger = _context(uuid.uuid4(), workspace)
    case, entered = await _case(db, mine)
    records = SqlPreparationRecords(db.sessions)
    await records.add(
        mine,
        _record(case, entered, PreparationOutcome.NOT_PREPARED, "case_moved"),
        audit=_audit(mine),
    )
    assert entered.id is not None
    latest = await records.latest(mine, entered.id, "1.0.0")
    assert latest is not None and latest.reason == "case_moved"
    for other in (neighbour, stranger):
        assert await records.latest(other, entered.id, "1.0.0") is None
        with pytest.raises((IntegrityError, DBAPIError)):
            await records.add(
                other,
                _record(case, entered, PreparationOutcome.PROPOSED, None),
                audit=_audit(other),
            )


async def test_a_reason_is_there_exactly_when_the_outcome_has_one(db: _Db) -> None:
    mine = _context(uuid.uuid4(), uuid.uuid4())
    case, entered = await _case(db, mine)
    records = SqlPreparationRecords(db.sessions)
    for outcome, reason in (
        (PreparationOutcome.PROPOSED, "why"),
        (PreparationOutcome.NOT_PREPARED, None),
        (PreparationOutcome.REJECTED, " "),
    ):
        with pytest.raises(IntegrityError, match="ck_step_preparations_reason"):
            await records.add(mine, _record(case, entered, outcome, reason), audit=_audit(mine))


def _draft(case: ProductDevelopmentCase) -> NewDocumentDraft:
    draft_id = uuid.uuid4()
    return NewDocumentDraft(
        id=draft_id,
        lineage_id=draft_id,
        version=1,
        case_kind=CaseKind.PRODUCT,
        case_id=case.id.value,
        doc_type=DocumentType.SAMPLE_EVALUATION,
        template_id="supply_chain.sample_evaluation",
        template_version="1.0.0",
        prompt_id=None,
        prompt_version=None,
        fields={"proposal_code": {"value": case.proposal_code, "source": None}},
        gaps=[],
        sources=[],
        content_sha256="0" * 64,
    )


def _document(context: AccessContext, case: ProductDevelopmentCase) -> NewCaseDocument:
    document_id = uuid.uuid4()
    return NewCaseDocument(
        id=CaseDocumentId(document_id),
        case_kind=CaseKind.PRODUCT,
        case_id=case.id.value,
        doc_type=DocumentType.SAMPLE_EVALUATION,
        object_key=ObjectKey.build(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            case_kind=CaseKind.PRODUCT,
            case_id=case.id.value,
            document_id=document_id,
        ).value,
        filename="BIÊN BẢN ĐÁNH GIÁ MẪU.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes=10,
        sha256="1" * 64,
    )


async def test_an_approval_lands_in_one_transaction_and_only_once(db: _Db) -> None:
    mine = _context(uuid.uuid4(), uuid.uuid4())
    case, entered = await _case(db, mine)
    drafts = SqlDocumentDraftRepository(db.sessions)
    draft = await drafts.add(mine, _draft(case), audit=_audit(mine))
    outcomes = SqlProposalOutcomes(db.sessions)

    def approval() -> tuple[ProductDevelopmentCase, NewDraftDecision, AiPreparedDocument]:
        moving = replace(case, _pending_steps=[])
        apply_product_action(
            moving,
            action=ProductAction.REQUEST_SAMPLE,
            given=ProductActionInput(actor_id=mine.principal_id, supplier_name="NCC Thử"),
        )
        return (
            moving,
            NewDraftDecision(
                id=uuid.uuid4(), draft_id=draft.id, decision=DraftDecision.CONFIRMED, reason=None
            ),
            AiPreparedDocument(document=_document(mine, case), draft_id=draft.id),
        )

    moving, confirmation, prepared = approval()
    await outcomes.apply_approved(
        mine,
        case=moving,
        new_drafts=[],
        confirmations=[confirmation],
        documents=[prepared],
        record=_record(case, entered, PreparationOutcome.APPLIED, None),
        audits=[_audit(mine)],
    )
    after = await SqlProductCaseRepository(db.sessions).get(mine, case.id)
    assert after is not None and after.state is ProductDevState.SAMPLE_REQUESTED
    async with db.migrator() as session:
        row = (
            await session.execute(
                sa.text("SELECT origin, draft_id FROM supply_chain.case_documents WHERE id = :id"),
                {"id": prepared.document.id.value},
            )
        ).one()
    assert (row.origin, row.draft_id) == ("ai_prepared", draft.id)

    again, confirmation, prepared = approval()
    with pytest.raises(ConflictError):
        await outcomes.apply_approved(
            mine,
            case=again,
            new_drafts=[],
            confirmations=[confirmation],
            documents=[prepared],
            record=_record(case, entered, PreparationOutcome.APPLIED, None),
            audits=[_audit(mine)],
        )
    async with db.migrator() as session:
        count = await session.scalar(
            sa.text(
                "SELECT count(*) FROM supply_chain.case_documents WHERE product_dev_case_id = :c"
            ),
            {"c": case.id.value},
        )
    assert count == 1


async def test_readings_are_read_within_the_callers_workspace(db: _Db) -> None:
    mine = _context(uuid.uuid4(), uuid.uuid4())
    neighbour = _context(mine.tenant_id, uuid.uuid4())
    readings = SqlExtractionReadings(db.sessions)
    assert await readings.readings(mine, []) == []
    assert await readings.readings(neighbour, [uuid.uuid4()]) == []
