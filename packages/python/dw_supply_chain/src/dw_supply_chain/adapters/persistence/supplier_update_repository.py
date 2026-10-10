"""SQL repository mapping `supply_chain.supplier_updates` <-> `SupplierUpdate`.

Same shape as `po_case_repository.SqlPOCaseRepository`: `session_factory` at
construction, `context: AccessContext` per call, RLS is the one enforcement
mechanism so no query here names `tenant_id`. `po_case_id`'s FK to `po_cases`
guarantees the referenced row exists SOMEWHERE, across every tenant — it does
not guarantee it is this caller's case, which is why
`application.handlers.SubmitSupplierUpdate` reads the case through
`POCaseRepositoryPort` (RLS-scoped) before ever calling `add` here, rather
than trusting the FK to stand in for a tenant check it cannot make.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_kernel.ids import TenantId, WorkspaceId
from dw_platform.adapters.persistence.repositories import SqlAuditRepository
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence import tables
from dw_supply_chain.domain.po_case import POCaseId
from dw_supply_chain.domain.supplier_update import (
    SupplierEventType,
    SupplierUpdate,
    SupplierUpdateExtraction,
    SupplierUpdateId,
)


def _update_from_row(row: Row[tuple]) -> SupplierUpdate:  # type: ignore[type-arg]
    return SupplierUpdate(
        id=SupplierUpdateId(row.id),
        tenant_id=TenantId(row.tenant_id),
        workspace_id=WorkspaceId(row.workspace_id),
        po_case_id=POCaseId(row.po_case_id),
        raw_text=row.raw_text,
        extraction=SupplierUpdateExtraction(
            event_type=SupplierEventType(row.event_type),
            affected_po=row.affected_po,
            delay_days=row.delay_days,
            reason=row.reason,
            proposed_action=row.proposed_action,
            confidence=float(row.confidence),
            source_ref=row.source_ref,
        ),
        requires_confirmation=row.requires_confirmation,
        created_at=row.created_at,
    )


@dataclass(frozen=True)
class SqlSupplierUpdateRepository:
    """Implements `SupplierUpdateRepositoryPort`."""

    session_factory: async_sessionmaker[AsyncSession]

    async def add(
        self, context: AccessContext, update: SupplierUpdate, *, audit: AuditEvent | None = None
    ) -> None:
        """The record and `audit` in one transaction. `audit` is optional here
        only for fixtures; the port a command writes through requires it."""
        scope = TenantScope.from_access_context(context)
        async with tenant_session(self.session_factory, scope) as session:
            await session.execute(
                sa.insert(tables.supplier_updates).values(
                    id=update.id.value,
                    tenant_id=update.tenant_id.value,
                    workspace_id=update.workspace_id.value,
                    po_case_id=update.po_case_id.value,
                    raw_text=update.raw_text,
                    event_type=update.extraction.event_type.value,
                    affected_po=update.extraction.affected_po,
                    delay_days=update.extraction.delay_days,
                    reason=update.extraction.reason,
                    proposed_action=update.extraction.proposed_action,
                    confidence=Decimal(str(update.extraction.confidence)),
                    source_ref=update.extraction.source_ref,
                    requires_confirmation=update.requires_confirmation,
                )
            )
            if audit is not None:
                await SqlAuditRepository(session).append(audit)

    async def get(
        self, context: AccessContext, update_id: SupplierUpdateId
    ) -> SupplierUpdate | None:
        scope = TenantScope.from_access_context(context)
        async with tenant_session(self.session_factory, scope) as session:
            result = await session.execute(
                sa.select(tables.supplier_updates).where(
                    tables.supplier_updates.c.id == update_id.value
                )
            )
            row = result.first()
            return _update_from_row(row) if row else None

    async def list_for_case(
        self, context: AccessContext, po_case_id: POCaseId
    ) -> list[SupplierUpdate]:
        scope = TenantScope.from_access_context(context)
        async with tenant_session(self.session_factory, scope) as session:
            result = await session.execute(
                sa.select(tables.supplier_updates)
                .where(tables.supplier_updates.c.po_case_id == po_case_id.value)
                .order_by(tables.supplier_updates.c.created_at.desc())
            )
            return [_update_from_row(row) for row in result]

    async def bulk_latest(
        self, context: AccessContext, case_ids: list[POCaseId]
    ) -> dict[uuid.UUID, SupplierUpdate]:
        if not case_ids:
            return {}
        scope = TenantScope.from_access_context(context)
        ids = [case_id.value for case_id in case_ids]
        updates = tables.supplier_updates
        async with tenant_session(self.session_factory, scope) as session:
            # One row per case: DISTINCT ON keeps the first of each
            # po_case_id in the ORDER BY, newest first, id breaking a tie.
            result = await session.execute(
                sa.select(updates)
                .distinct(updates.c.po_case_id)
                .where(updates.c.po_case_id.in_(ids))
                .order_by(updates.c.po_case_id, updates.c.created_at.desc(), updates.c.id.desc())
            )
            return {row.po_case_id: _update_from_row(row) for row in result}
