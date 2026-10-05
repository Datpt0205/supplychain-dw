"""SQL persistence for case documents (`supply_chain.case_documents`).

Every statement runs under `tenant_session`, which binds the context's tenant
AND workspace: this table's policy narrows by both, so another workspace's
rows read as absent here exactly as another tenant's do. The statements also
name the tenant and workspace themselves, a second layer where RLS is the
first.

The version is computed inside the INSERT (one past the highest for the case
and type). Two concurrent uploads can compute the same one; the partial UNIQUE
`uq_case_documents_tenant_id_po_case_id_doc_type_version` refuses the second,
and this adapter turns that refusal, by the constraint's name, into a
`ConflictError` the client retries. Nothing here decides whether a version is
free; the database does.

The audit event commits with the row, in the same transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_kernel.errors import ConflictError
from dw_platform.adapters.persistence.repositories import SqlAuditRepository
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence import tables
from dw_supply_chain.application.ports import NewCaseDocument
from dw_supply_chain.domain.case_document import (
    CaseDocument,
    CaseDocumentId,
    CaseKind,
    DocumentType,
)

_d = tables.case_documents
VERSION_CONSTRAINT = "uq_case_documents_tenant_id_po_case_id_doc_type_version"


def _document(row: Row[tuple[object, ...]]) -> CaseDocument:
    m = row._mapping
    return CaseDocument(
        id=CaseDocumentId(m[_d.c.id]),
        tenant_id=m[_d.c.tenant_id],
        workspace_id=m[_d.c.workspace_id],
        case_kind=CaseKind.PO,
        case_id=m[_d.c.po_case_id],
        doc_type=DocumentType(m[_d.c.doc_type]),
        object_key=m[_d.c.object_key],
        filename=m[_d.c.filename],
        content_type=m[_d.c.content_type],
        size_bytes=m[_d.c.size_bytes],
        sha256=m[_d.c.sha256],
        version=m[_d.c.version],
        uploaded_by=m[_d.c.uploaded_by],
        uploaded_at=m[_d.c.uploaded_at],
    )


def _in_scope(context: AccessContext) -> tuple[sa.ColumnElement[bool], ...]:
    return (_d.c.tenant_id == context.tenant_id, _d.c.workspace_id == context.workspace_id)


@dataclass(frozen=True)
class SqlCaseDocumentRepository:
    """Implements `CaseDocumentRepositoryPort` and `CaseDocumentKeysPort`."""

    session_factory: async_sessionmaker[AsyncSession]

    async def add(
        self, context: AccessContext, document: NewCaseDocument, *, audit: AuditEvent
    ) -> CaseDocument:
        next_version = (
            sa.select(sa.func.coalesce(sa.func.max(_d.c.version), 0) + 1)
            .where(
                _d.c.tenant_id == context.tenant_id,
                _d.c.po_case_id == document.case_id,
                _d.c.doc_type == document.doc_type.value,
            )
            .scalar_subquery()
        )
        try:
            async with tenant_session(
                self.session_factory, TenantScope.from_access_context(context)
            ) as session:
                row = (
                    await session.execute(
                        sa.insert(_d)
                        .values(
                            id=document.id.value,
                            tenant_id=context.tenant_id,
                            workspace_id=context.workspace_id,
                            po_case_id=document.case_id,
                            doc_type=document.doc_type.value,
                            object_key=document.object_key,
                            filename=document.filename,
                            content_type=document.content_type,
                            size_bytes=document.size_bytes,
                            sha256=document.sha256,
                            version=next_version,
                            uploaded_by=context.principal_id,
                        )
                        .returning(_d)
                    )
                ).one()
                await SqlAuditRepository(session).append(audit)
        except IntegrityError as exc:
            if VERSION_CONSTRAINT in str(exc.orig):
                raise ConflictError(
                    "another upload of this document type took the same version; try again",
                    details={
                        "case_id": str(document.case_id),
                        "doc_type": document.doc_type.value,
                    },
                ) from exc
            raise
        return _document(row)

    async def list_for_case(self, context: AccessContext, case_id: uuid.UUID) -> list[CaseDocument]:
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            rows = (
                await session.execute(
                    sa.select(_d)
                    .where(*_in_scope(context), _d.c.po_case_id == case_id)
                    .order_by(_d.c.doc_type, _d.c.version.desc())
                )
            ).all()
        return [_document(row) for row in rows]

    async def get(self, context: AccessContext, document_id: CaseDocumentId) -> CaseDocument | None:
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            row = (
                await session.execute(
                    sa.select(_d).where(*_in_scope(context), _d.c.id == document_id.value)
                )
            ).first()
        return None if row is None else _document(row)

    async def existing_keys(self, context: AccessContext, keys: Sequence[str]) -> set[str]:
        if not keys:
            return set()
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            found = await session.scalars(
                sa.select(_d.c.object_key).where(*_in_scope(context), _d.c.object_key.in_(keys))
            )
            return set(found)
