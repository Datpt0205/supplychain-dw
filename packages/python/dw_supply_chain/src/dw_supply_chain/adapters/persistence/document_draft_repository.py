"""SQL persistence for document drafts and the tenant's templates (cbebad572558).

Drafts and their decisions run under `tenant_session` (tenant AND workspace
bound; both tables are narrowed by both) and name the tenant and workspace in
every statement as a second layer. A draft's version and a decision's
uniqueness are the database's: a lineage's UNIQUE and the decisions' UNIQUE
turn a race into a `ConflictError` by constraint name. Audit events commit
with their row.

Template overrides are tenant-wide: the session binds the tenant, and the
table's policy reads nothing else.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

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
from dw_supply_chain.application.document_drafts import (
    NewDocumentDraft,
    NewDraftDecision,
    StoredTemplate,
)
from dw_supply_chain.domain.case_document import CaseKind, DocumentType
from dw_supply_chain.domain.document_draft import DocumentDraft, DraftDecision, DraftSource

_d = tables.document_drafts
_x = tables.document_draft_decisions
_t = tables.doc_template_overrides
_CASE_COLUMN = {CaseKind.PO: _d.c.po_case_id, CaseKind.PRODUCT: _d.c.product_dev_case_id}
VERSION_TAKEN = "uq_document_drafts_tenant_id_lineage_id_version"
ALREADY_DECIDED = "uq_document_draft_decisions_tenant_id_draft_id"
TEMPLATE_TAKEN = "uq_doc_template_overrides_tenant_id_template_id_version"


def _scope(context: AccessContext) -> TenantScope:
    return TenantScope.from_access_context(context)


def _mine(table: sa.Table, context: AccessContext) -> tuple[sa.ColumnElement[bool], ...]:
    return (table.c.tenant_id == context.tenant_id, table.c.workspace_id == context.workspace_id)


def _select_with_state(context: AccessContext) -> sa.Select[Any]:
    """A draft row with its decision and its lineage's newest version."""
    newest = sa.alias(_d, "newest")
    latest = (
        sa.select(sa.func.max(newest.c.version))
        .where(newest.c.tenant_id == _d.c.tenant_id, newest.c.lineage_id == _d.c.lineage_id)
        .scalar_subquery()
    )
    return (
        sa.select(_d, _x.c.decision, _x.c.reason.label("decision_reason"), latest.label("latest"))
        .select_from(
            _d.outerjoin(_x, sa.and_(_x.c.tenant_id == _d.c.tenant_id, _x.c.draft_id == _d.c.id))
        )
        .where(*_mine(_d, context))
    )


def _draft(row: Row[Any]) -> DocumentDraft:
    m = row._mapping
    kind = CaseKind.PO if m[_d.c.po_case_id] is not None else CaseKind.PRODUCT
    return DocumentDraft(
        id=m[_d.c.id],
        tenant_id=m[_d.c.tenant_id],
        workspace_id=m[_d.c.workspace_id],
        lineage_id=m[_d.c.lineage_id],
        version=m[_d.c.version],
        case_kind=kind,
        case_id=m[_CASE_COLUMN[kind]],
        doc_type=DocumentType(m[_d.c.doc_type]),
        template_id=m[_d.c.template_id],
        template_version=m[_d.c.template_version],
        prompt_id=m[_d.c.prompt_id],
        prompt_version=m[_d.c.prompt_version],
        fields=dict(m[_d.c.fields]),
        gaps=tuple(m[_d.c.gaps]),
        sources=tuple(
            DraftSource(
                document_id=uuid.UUID(s["document_id"]),
                sha256=s["sha256"],
                extraction_id=None
                if s.get("extraction_id") is None
                else uuid.UUID(s["extraction_id"]),
            )
            for s in m[_d.c.sources]
        ),
        content_sha256=m[_d.c.content_sha256],
        created_by=m[_d.c.created_by],
        created_at=m[_d.c.created_at],
        decision=None if m["decision"] is None else DraftDecision(m["decision"]),
        decision_reason=m["decision_reason"],
        latest_version=m["latest"],
    )


async def insert_draft(
    session: AsyncSession, context: AccessContext, draft: NewDocumentDraft
) -> None:
    """One draft version, in the caller's transaction."""
    await session.execute(
        sa.insert(_d).values(
            id=draft.id,
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            **{_CASE_COLUMN[draft.case_kind].name: draft.case_id},
            lineage_id=draft.lineage_id,
            version=draft.version,
            doc_type=draft.doc_type.value,
            template_id=draft.template_id,
            template_version=draft.template_version,
            prompt_id=draft.prompt_id,
            prompt_version=draft.prompt_version,
            fields=draft.fields,
            gaps=draft.gaps,
            sources=draft.sources,
            content_sha256=draft.content_sha256,
            created_by=context.principal_id,
        )
    )


async def insert_decision(
    session: AsyncSession, context: AccessContext, decision: NewDraftDecision
) -> None:
    """One decision on one draft version, in the caller's transaction."""
    await session.execute(
        sa.insert(_x).values(
            id=decision.id,
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            draft_id=decision.draft_id,
            decision=decision.decision.value,
            reason=decision.reason,
            decided_by=context.principal_id,
        )
    )


@dataclass(frozen=True)
class SqlDocumentDraftRepository:
    """Implements `DocumentDraftRepositoryPort`."""

    session_factory: async_sessionmaker[AsyncSession]

    async def add(
        self, context: AccessContext, draft: NewDocumentDraft, *, audit: AuditEvent
    ) -> DocumentDraft:
        try:
            async with tenant_session(self.session_factory, _scope(context)) as session:
                await insert_draft(session, context, draft)
                await SqlAuditRepository(session).append(audit)
                row = (
                    await session.execute(_select_with_state(context).where(_d.c.id == draft.id))
                ).one()
        except IntegrityError as exc:
            if VERSION_TAKEN in str(exc.orig):
                raise ConflictError(
                    "another edit of this draft took the same version; reload and try again",
                    details={"lineage_id": str(draft.lineage_id), "version": draft.version},
                ) from exc
            raise
        return _draft(row)

    async def get(self, context: AccessContext, draft_id: uuid.UUID) -> DocumentDraft | None:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            row = (
                await session.execute(_select_with_state(context).where(_d.c.id == draft_id))
            ).first()
        return None if row is None else _draft(row)

    async def latest_for_case(
        self, context: AccessContext, case_kind: CaseKind, case_id: uuid.UUID
    ) -> list[DocumentDraft]:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            rows = (
                await session.execute(
                    _select_with_state(context)
                    .where(_CASE_COLUMN[case_kind] == case_id)
                    .order_by(_d.c.created_at.desc(), _d.c.id)
                    .limit(200)
                )
            ).all()
        drafts = [_draft(r) for r in rows]
        return [d for d in drafts if d.version == d.latest_version]

    async def decide(
        self, context: AccessContext, decision: NewDraftDecision, *, audit: AuditEvent
    ) -> None:
        try:
            async with tenant_session(self.session_factory, _scope(context)) as session:
                await insert_decision(session, context, decision)
                await SqlAuditRepository(session).append(audit)
        except IntegrityError as exc:
            if ALREADY_DECIDED in str(exc.orig):
                raise ConflictError(
                    "this draft version already has a decision",
                    details={"draft_id": str(decision.draft_id)},
                ) from exc
            raise


@dataclass(frozen=True)
class SqlDocTemplateOverrides:
    """Implements `TemplateOverrideStorePort`."""

    session_factory: async_sessionmaker[AsyncSession]

    async def get(
        self, context: AccessContext, template_id: str, version: str
    ) -> StoredTemplate | None:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            row = (
                await session.execute(
                    sa.select(_t).where(
                        _t.c.tenant_id == context.tenant_id,
                        _t.c.template_id == template_id,
                        _t.c.version == version,
                    )
                )
            ).first()
        if row is None:
            return None
        return StoredTemplate(
            template_id=row.template_id,
            version=row.version,
            spec=row.spec,
            docx=bytes(row.docx),
            checksum=row.checksum,
        )

    async def list_refs(self, context: AccessContext) -> list[tuple[str, str]]:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            rows = (
                await session.execute(
                    sa.select(_t.c.template_id, _t.c.version).where(
                        _t.c.tenant_id == context.tenant_id
                    )
                )
            ).all()
        return [(r.template_id, r.version) for r in rows]

    async def add(
        self, context: AccessContext, template: StoredTemplate, *, audit: AuditEvent
    ) -> None:
        try:
            async with tenant_session(self.session_factory, _scope(context)) as session:
                await session.execute(
                    sa.insert(_t).values(
                        id=uuid.uuid4(),
                        tenant_id=context.tenant_id,
                        template_id=template.template_id,
                        version=template.version,
                        spec=template.spec,
                        docx=template.docx,
                        checksum=template.checksum,
                        uploaded_by=context.principal_id,
                    )
                )
                await SqlAuditRepository(session).append(audit)
        except IntegrityError as exc:
            if TEMPLATE_TAKEN in str(exc.orig):
                raise ConflictError(
                    "this template version already exists for the company; add a new version",
                    details={"template_id": template.template_id, "version": template.version},
                ) from exc
            raise
