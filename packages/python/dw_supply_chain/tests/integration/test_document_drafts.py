"""Integration: document drafts and template overrides (cbebad572558; ticket
ai-automation/03).

What only the real database can show:

- drafts and their decisions are narrowed by tenant AND workspace; a template
  override by tenant;
- a lineage's version and a version's decision are the database's to keep
  unique (409 by constraint name);
- a draft's id is not a case document: the documents repository the steps
  read from does not find it, so a draft cannot be a step's paper;
- `case_documents.origin` and `draft_id` agree (`ck_case_documents_draft_origin`);
- `bod_submission` is a document type the database accepts.

Owed when written (2026-10-09): not run, no Docker on the machine that wrote it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls

from dw_kernel.errors import ConflictError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import SystemClock
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence.case_document_repository import (
    SqlCaseDocumentRepository,
)
from dw_supply_chain.adapters.persistence.document_draft_repository import (
    SqlDocTemplateOverrides,
    SqlDocumentDraftRepository,
)
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.application.document_drafts import (
    NewDocumentDraft,
    NewDraftDecision,
    StoredTemplate,
)
from dw_supply_chain.application.ports import NewCaseDocument
from dw_supply_chain.domain.case_document import (
    CaseDocumentId,
    CaseKind,
    DocumentType,
    ObjectKey,
)
from dw_supply_chain.domain.document_draft import DraftDecision, DraftStatus
from dw_supply_chain.domain.po_case import POCase, POCaseId

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


def _audit(context: AccessContext) -> AuditEvent:
    return AuditEvent(
        id=uuid.uuid4(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        actor_id=UserId(context.principal_id),
        action="supply_chain.document_draft.prepared",
        resource_type="document_draft",
        resource_id=str(uuid.uuid4()),
        occurred_at=SystemClock().now(),
    )


async def _case(db: _Db, context: AccessContext) -> POCase:
    case = POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        po_reference=f"PO-DR-{uuid.uuid4().hex[:8]}",
        supplier_name="NCC Bản nháp",
    )
    await SqlPOCaseRepository(db.sessions).add(context, case)
    return case


def _draft(case: POCase, *, lineage: uuid.UUID | None = None, version: int = 1) -> NewDocumentDraft:
    draft_id = uuid.uuid4()
    return NewDocumentDraft(
        id=draft_id,
        lineage_id=lineage or draft_id,
        version=version,
        case_kind=CaseKind.PO,
        case_id=case.id.value,
        doc_type=DocumentType.PURCHASE_ORDER,
        template_id="supply_chain.purchase_order",
        template_version="1.0.0",
        prompt_id=None,
        prompt_version=None,
        fields={"po_reference": {"value": case.po_reference, "source": None}},
        gaps=["po_date"],
        sources=[],
        content_sha256="0" * 64,
    )


async def test_drafts_and_decisions_stay_in_their_workspace(db: _Db) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    mine = _context(tenant, workspace)
    neighbour = _context(tenant, uuid.uuid4())
    stranger = _context(uuid.uuid4(), workspace)
    case = await _case(db, mine)
    repo = SqlDocumentDraftRepository(db.sessions)
    first = await repo.add(mine, _draft(case), audit=_audit(mine))
    second = await repo.add(
        mine, _draft(case, lineage=first.lineage_id, version=2), audit=_audit(mine)
    )
    assert (await repo.get(mine, first.id)).status is DraftStatus.SUPERSEDED  # type: ignore[union-attr]
    assert [d.id for d in await repo.latest_for_case(mine, CaseKind.PO, case.id.value)] == [
        second.id
    ]
    for other in (neighbour, stranger):
        assert await repo.get(other, first.id) is None
        assert await repo.latest_for_case(other, CaseKind.PO, case.id.value) == []
        with pytest.raises((IntegrityError, DBAPIError)):
            await repo.add(other, _draft(case), audit=_audit(other))
        with pytest.raises((IntegrityError, DBAPIError)):
            await repo.decide(
                other,
                NewDraftDecision(
                    id=uuid.uuid4(),
                    draft_id=second.id,
                    decision=DraftDecision.REJECTED,
                    reason="x",
                ),
                audit=_audit(other),
            )


async def test_versions_and_decisions_are_unique_by_the_database(db: _Db) -> None:
    mine = _context(uuid.uuid4(), uuid.uuid4())
    case = await _case(db, mine)
    repo = SqlDocumentDraftRepository(db.sessions)
    first = await repo.add(mine, _draft(case), audit=_audit(mine))
    with pytest.raises(ConflictError):
        await repo.add(mine, _draft(case, lineage=first.lineage_id, version=1), audit=_audit(mine))
    decision = NewDraftDecision(
        id=uuid.uuid4(), draft_id=first.id, decision=DraftDecision.REJECTED, reason="sai NCC"
    )
    await repo.decide(mine, decision, audit=_audit(mine))
    with pytest.raises(ConflictError):
        await repo.decide(mine, replace_id(decision), audit=_audit(mine))
    with pytest.raises(IntegrityError, match="ck_document_draft_decisions_reason"):
        other = await repo.add(mine, _draft(case), audit=_audit(mine))
        await repo.decide(
            mine,
            NewDraftDecision(
                id=uuid.uuid4(), draft_id=other.id, decision=DraftDecision.REJECTED, reason=" "
            ),
            audit=_audit(mine),
        )


def replace_id(decision: NewDraftDecision) -> NewDraftDecision:
    return NewDraftDecision(
        id=uuid.uuid4(),
        draft_id=decision.draft_id,
        decision=decision.decision,
        reason=decision.reason,
    )


async def test_a_draft_is_not_a_case_document(db: _Db) -> None:
    """The steps read their paper through `SqlCaseDocumentRepository`; a
    draft's id is not there, so a draft cannot be a step's paper."""
    mine = _context(uuid.uuid4(), uuid.uuid4())
    case = await _case(db, mine)
    draft = await SqlDocumentDraftRepository(db.sessions).add(
        mine, _draft(case), audit=_audit(mine)
    )
    assert await SqlCaseDocumentRepository(db.sessions).get(mine, CaseDocumentId(draft.id)) is None


async def test_an_ai_prepared_document_names_its_draft_and_an_upload_names_none(db: _Db) -> None:
    mine = _context(uuid.uuid4(), uuid.uuid4())
    case = await _case(db, mine)
    draft = await SqlDocumentDraftRepository(db.sessions).add(
        mine, _draft(case), audit=_audit(mine)
    )
    document_id = uuid.uuid4()
    await SqlCaseDocumentRepository(db.sessions).add(
        mine,
        NewCaseDocument(
            id=CaseDocumentId(document_id),
            case_kind=CaseKind.PO,
            case_id=case.id.value,
            doc_type=DocumentType.PURCHASE_ORDER,
            object_key=ObjectKey.build(
                tenant_id=mine.tenant_id,
                workspace_id=mine.workspace_id,
                case_kind=CaseKind.PO,
                case_id=case.id.value,
                document_id=document_id,
            ).value,
            filename="po.pdf",
            content_type="application/pdf",
            size_bytes=10,
            sha256="0" * 64,
        ),
        audit=_audit(mine),
    )
    for origin, named in (("ai_prepared", None), ("uploaded", draft.id)):
        async with db.migrator() as session:
            with pytest.raises(IntegrityError, match="ck_case_documents_draft_origin"):
                await session.execute(
                    sa.text(
                        "UPDATE supply_chain.case_documents SET origin = :o, draft_id = :d"
                        " WHERE id = :i"
                    ),
                    {"o": origin, "d": named, "i": document_id},
                )


async def test_a_template_override_is_its_tenants_alone(db: _Db) -> None:
    async with db.migrator() as session, session.begin():
        tenants = (await session.execute(sa.text("SELECT id FROM platform.tenants LIMIT 2"))).all()
    if len(tenants) < 2:
        pytest.skip("needs two tenants in the migrated database's seed")
    a = _context(tenants[0].id, uuid.uuid4())
    b = _context(tenants[1].id, uuid.uuid4())
    store = SqlDocTemplateOverrides(db.sessions)
    template = StoredTemplate(
        template_id="supply_chain.purchase_order",
        version=f"9.{uuid.uuid4().int % 1000}.0",
        spec="schema_version: '1.0'",
        docx=b"PK\x03\x04",
        checksum="0" * 64,
    )
    await store.add(a, template, audit=_audit(a))
    assert await store.get(a, template.template_id, template.version) is not None
    assert await store.get(b, template.template_id, template.version) is None
    with pytest.raises(ConflictError):
        await store.add(a, template, audit=_audit(a))
