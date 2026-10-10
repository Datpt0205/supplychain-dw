"""SQL repository for step 12's sub-flow (`supply_chain.packaging_designs` and
`packaging_design_events`, migration 85659fd91943).

Read and written in a session bound to the caller's tenant AND workspace; RLS
narrows both tables by both, so another tenant's or workspace's row is absent,
never forbidden. Implements `PackagingDesignRepositoryPort`.

`save` is one transaction: it locks the PO case row (`FOR SHARE`, which waits
for a step being saved on the case, and makes a step on the case wait for this
one) and refuses unless the case is still in `pre_production`; then the design
row (inserted on its first step, else updated where `version` is the one read),
its pending history rows and the audit event.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_kernel.errors import ConflictError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_platform.adapters.persistence.repositories import SqlAuditRepository
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence import tables
from dw_supply_chain.domain.packaging_design import (
    PackagingAction,
    PackagingDesign,
    PackagingHistoryEntry,
    PreProductionTest,
    ReviewStatus,
)
from dw_supply_chain.domain.po_case import CaseState

_designs = tables.packaging_designs
_events = tables.packaging_design_events
_cases = tables.po_cases


@dataclass(frozen=True)
class SqlPackagingDesignRepository:
    session_factory: async_sessionmaker[AsyncSession]

    async def get(self, context: AccessContext, po_case_id: uuid.UUID) -> PackagingDesign | None:
        scope = TenantScope.from_access_context(context)
        async with tenant_session(self.session_factory, scope) as session:
            row = (
                await session.execute(
                    sa.select(_designs).where(_designs.c.po_case_id == po_case_id)
                )
            ).first()
        if row is None:
            return None
        return PackagingDesign(
            po_case_id=row.po_case_id,
            tenant_id=TenantId(row.tenant_id),
            workspace_id=WorkspaceId(row.workspace_id),
            colour_status=ReviewStatus(row.colour_status),
            design_status=ReviewStatus(row.design_status),
            pre_production_sample_received_at=row.pre_production_sample_received_at,
            pre_production_test=PreProductionTest(row.pre_production_test),
            mkt_pack_sent_at=row.mkt_pack_sent_at,
            packaging_content_submitted_at=row.packaging_content_submitted_at,
            version=row.version,
        )

    async def history(
        self, context: AccessContext, po_case_id: uuid.UUID
    ) -> list[PackagingHistoryEntry]:
        scope = TenantScope.from_access_context(context)
        async with tenant_session(self.session_factory, scope) as session:
            rows = (
                await session.execute(
                    sa.select(_events)
                    .where(_events.c.po_case_id == po_case_id)
                    .order_by(_events.c.occurred_at, _events.c.id)
                )
            ).all()
        return [
            PackagingHistoryEntry(
                action=PackagingAction(row.action),
                reason=row.reason,
                document_id=row.document_id,
                note=row.note,
                actor_id=row.actor_id,
                occurred_at=row.occurred_at,
            )
            for row in rows
        ]

    async def save(
        self,
        context: AccessContext,
        design: PackagingDesign,
        *,
        actor_id: uuid.UUID,
        audit: AuditEvent,
    ) -> None:
        events = design.pop_pending_events()
        scope = TenantScope.from_access_context(context)
        async with tenant_session(self.session_factory, scope) as session:
            await _require_pre_production(session, design.po_case_id)
            values = {
                "colour_status": design.colour_status.value,
                "design_status": design.design_status.value,
                "pre_production_sample_received_at": design.pre_production_sample_received_at,
                "pre_production_test": design.pre_production_test.value,
                "mkt_pack_sent_at": design.mkt_pack_sent_at,
                "packaging_content_submitted_at": design.packaging_content_submitted_at,
                "version": design.version,
            }
            if design.version == 1:
                inserted = (
                    await session.execute(
                        insert(_designs)
                        .values(
                            id=uuid.uuid4(),
                            tenant_id=design.tenant_id.value,
                            workspace_id=design.workspace_id.value,
                            po_case_id=design.po_case_id,
                            **values,
                        )
                        .on_conflict_do_nothing(
                            index_elements=["tenant_id", "workspace_id", "po_case_id"]
                        )
                        .returning(_designs.c.id)
                    )
                ).first()
                moved = inserted is not None
            else:
                result = await session.execute(
                    sa.update(_designs)
                    .where(
                        _designs.c.po_case_id == design.po_case_id,
                        _designs.c.version == design.version - 1,
                    )
                    .values(**values)
                )
                assert isinstance(result, CursorResult)
                moved = result.rowcount == 1
            if not moved:
                raise ConflictError(
                    "bước con của hồ sơ vừa được người khác cập nhật; tải lại rồi thử lại",
                    details={"case_id": str(design.po_case_id)},
                )
            if events:
                await session.execute(
                    sa.insert(_events),
                    [
                        {
                            "id": uuid.uuid4(),
                            "tenant_id": design.tenant_id.value,
                            "workspace_id": design.workspace_id.value,
                            "po_case_id": design.po_case_id,
                            "action": event.action.value,
                            "actor_id": actor_id,
                            "reason": event.reason,
                            "note": event.note,
                            "document_id": event.document_id,
                        }
                        for event in events
                    ],
                )
            await SqlAuditRepository(session).append(audit)


async def _require_pre_production(session: AsyncSession, po_case_id: uuid.UUID) -> None:
    state = (
        await session.execute(
            sa.select(_cases.c.state).where(_cases.c.id == po_case_id).with_for_update(read=True)
        )
    ).scalar_one_or_none()
    if state != CaseState.PRE_PRODUCTION.value:
        raise ConflictError(
            "thiết kế màu, bao bì và test trước SX chỉ làm khi Hồ sơ PO ở bước 12",
            details={"case_id": str(po_case_id)},
        )
