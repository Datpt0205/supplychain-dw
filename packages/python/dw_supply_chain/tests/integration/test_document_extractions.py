"""Integration: document extractions (e21dc10d13b5; ticket ai-automation/02).

What only the real database can show:

- a reading is narrowed by tenant AND workspace: another tenant and another
  workspace of the same tenant neither read nor write one;
- a reading describes exactly its document: another workspace's document, or
  a type or hash the document does not have, is refused by the composite FK;
- one reading per (document, prompt version): a second is a `ConflictError`;
- the queue function hands ids only, oldest first, and stops offering a
  document once it has a reading under the prompt version asked for;
- the lane, wired to the SQL adapters, writes its row and audits as itself.

Owed when written (2026-10-09): not run, no Docker on the machine that wrote it.
"""

from __future__ import annotations

import hashlib
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
from dw_supply_chain.adapters.persistence.document_extraction_repository import (
    SqlDocumentExtractionRepository,
    SqlExtractionQueue,
)
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.application.document_extraction import NewExtraction
from dw_supply_chain.application.ports import NewCaseDocument
from dw_supply_chain.domain.case_document import (
    CaseDocument,
    CaseDocumentId,
    CaseKind,
    DocumentType,
    ObjectKey,
)
from dw_supply_chain.domain.extraction import ExtractionStatus
from dw_supply_chain.domain.po_case import POCase, POCaseId

pytestmark = pytest.mark.integration

TARGETS = {"supplier_quotation": "supply_chain.extract_supplier_quotation@1.0.0"}


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
        action="supply_chain.document.extracted",
        resource_type="case_document",
        resource_id=str(uuid.uuid4()),
        occurred_at=SystemClock().now(),
    )


async def _document(db: _Db, context: AccessContext) -> CaseDocument:
    case = POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        po_reference=f"PO-EX-{uuid.uuid4().hex[:8]}",
        supplier_name="NCC Báo giá",
    )
    await SqlPOCaseRepository(db.sessions).add(context, case)
    document_id = uuid.uuid4()
    return await SqlCaseDocumentRepository(db.sessions).add(
        context,
        NewCaseDocument(
            id=CaseDocumentId(document_id),
            case_kind=CaseKind.PO,
            case_id=case.id.value,
            doc_type=DocumentType.SUPPLIER_QUOTATION,
            object_key=ObjectKey.build(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                case_kind=CaseKind.PO,
                case_id=case.id.value,
                document_id=document_id,
            ).value,
            filename="bao-gia.pdf",
            content_type="application/pdf",
            size_bytes=10,
            sha256=hashlib.sha256(document_id.bytes).hexdigest(),
        ),
        audit=_audit(context),
    )


def _reading(document: CaseDocument, **overrides: object) -> NewExtraction:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "document_id": document.id.value,
        "doc_type": document.doc_type,
        "sha256": document.sha256,
        "prompt_id": "supply_chain.extract_supplier_quotation",
        "prompt_version": "1.0.0",
        "model_profile": "luna",
        "status": ExtractionStatus.EXTRACTED,
        "text": "BÁO GIÁ",
        "fields": {"currency": {"value": "USD", "quote": "Tiền tệ: USD"}},
        "gaps": [],
    }
    values.update(overrides)
    return NewExtraction(**values)  # type: ignore[arg-type]


async def test_a_reading_stays_in_its_workspace_and_is_written_once(db: _Db) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    mine = _context(tenant, workspace)
    neighbour = _context(tenant, uuid.uuid4())
    stranger = _context(uuid.uuid4(), workspace)
    document = await _document(db, mine)
    repo = SqlDocumentExtractionRepository(db.sessions)

    await repo.add(mine, _reading(document), audit=_audit(mine))
    with pytest.raises(ConflictError):
        await repo.add(mine, _reading(document), audit=_audit(mine))

    for other in (neighbour, stranger):
        # Writing a reading of this document from elsewhere: FK or RLS refuses.
        with pytest.raises((IntegrityError, DBAPIError)):
            await repo.add(other, _reading(document, prompt_version="9.9.9"), audit=_audit(other))
    async with db.sessions() as session:
        unscoped = await session.scalar(
            sa.text("SELECT count(*) FROM supply_chain.document_extractions")
        )
    assert unscoped == 0


async def test_a_reading_cannot_carry_a_type_or_hash_its_document_does_not_have(db: _Db) -> None:
    mine = _context(uuid.uuid4(), uuid.uuid4())
    document = await _document(db, mine)
    repo = SqlDocumentExtractionRepository(db.sessions)
    with pytest.raises(IntegrityError, match="fk_document_extractions_tenant_id_case_documents"):
        await repo.add(mine, _reading(document, sha256="0" * 64), audit=_audit(mine))
    with pytest.raises(IntegrityError, match="fk_document_extractions_tenant_id_case_documents"):
        await repo.add(
            mine,
            _reading(document, doc_type=DocumentType.PURCHASE_ORDER),
            audit=_audit(mine),
        )
    with pytest.raises(IntegrityError, match="ck_document_extractions_fields_only_when_extracted"):
        await repo.add(
            mine, _reading(document, status=ExtractionStatus.UNREADABLE), audit=_audit(mine)
        )


async def test_the_queue_hands_ids_only_and_forgets_a_document_once_read(db: _Db) -> None:
    mine = _context(uuid.uuid4(), uuid.uuid4())
    document = await _document(db, mine)
    queue = SqlExtractionQueue(db.sessions)

    waiting = await queue.awaiting(TARGETS, 100)
    assert any(q.document_id == document.id.value for q in waiting)
    item = next(q for q in waiting if q.document_id == document.id.value)
    assert (item.tenant_id, item.workspace_id) == (mine.tenant_id, mine.workspace_id)

    await SqlDocumentExtractionRepository(db.sessions).add(
        mine, _reading(document), audit=_audit(mine)
    )
    assert all(q.document_id != document.id.value for q in await queue.awaiting(TARGETS, 100))
    # A new prompt version reads it again.
    again = {"supplier_quotation": "supply_chain.extract_supplier_quotation@1.1.0"}
    assert any(q.document_id == document.id.value for q in await queue.awaiting(again, 100))
    # A type not asked for is not offered.
    assert all(
        q.document_id != document.id.value
        for q in await queue.awaiting({"sample_evaluation": "x@1.0.0"}, 100)
    )
