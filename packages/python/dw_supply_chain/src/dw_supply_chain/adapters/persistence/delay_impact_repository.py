"""SQL repository mapping `supply_chain.delay_impact_analyses` <->
`DelayImpactAnalysis`.

Same shape as `po_case_repository`/`supplier_update_repository`:
`session_factory` at construction, `context: AccessContext` per call, RLS is
the enforcement so no query names `tenant_id`. `impacted_milestones`/
`assumptions`/`mitigation_options` round-trip through JSONB, so the mapping
functions below are this module's one job the other two repositories do not
have to do.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_kernel.ids import TenantId, WorkspaceId
from dw_platform.adapters.persistence.repositories import SqlAuditRepository
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence import tables
from dw_supply_chain.domain.delay_impact import (
    DelayImpactAnalysis,
    DelayImpactAnalysisId,
    DelayImpactExtraction,
    ImpactedMilestoneEstimate,
    MitigationOption,
)
from dw_supply_chain.domain.po_case import CaseState, POCaseId
from dw_supply_chain.domain.supplier_update import SupplierUpdateId


def _analysis_from_row(row: Row[tuple]) -> DelayImpactAnalysis:  # type: ignore[type-arg]
    return DelayImpactAnalysis(
        id=DelayImpactAnalysisId(row.id),
        tenant_id=TenantId(row.tenant_id),
        workspace_id=WorkspaceId(row.workspace_id),
        po_case_id=POCaseId(row.po_case_id),
        supplier_update_id=SupplierUpdateId(row.supplier_update_id),
        delay_days=row.delay_days,
        impacted_milestones=tuple(
            ImpactedMilestoneEstimate(
                milestone=CaseState(entry["milestone"]),
                estimated_delay_days=entry["estimated_delay_days"],
            )
            for entry in row.impacted_milestones
        ),
        extraction=DelayImpactExtraction(
            assumptions=list(row.assumptions),
            mitigation_options=[MitigationOption(**entry) for entry in row.mitigation_options],
        ),
        created_at=row.created_at,
    )


@dataclass(frozen=True)
class SqlDelayImpactAnalysisRepository:
    """Implements `DelayImpactAnalysisRepositoryPort`."""

    session_factory: async_sessionmaker[AsyncSession]

    async def add(
        self,
        context: AccessContext,
        analysis: DelayImpactAnalysis,
        *,
        audit: AuditEvent | None = None,
    ) -> None:
        """The analysis and `audit` in one transaction. `audit` is optional
        here only for fixtures; the port a command writes through requires it."""
        scope = TenantScope.from_access_context(context)
        async with tenant_session(self.session_factory, scope) as session:
            await session.execute(
                sa.insert(tables.delay_impact_analyses).values(
                    id=analysis.id.value,
                    tenant_id=analysis.tenant_id.value,
                    workspace_id=analysis.workspace_id.value,
                    po_case_id=analysis.po_case_id.value,
                    supplier_update_id=analysis.supplier_update_id.value,
                    delay_days=analysis.delay_days,
                    impacted_milestones=[
                        {
                            "milestone": estimate.milestone.value,
                            "estimated_delay_days": estimate.estimated_delay_days,
                        }
                        for estimate in analysis.impacted_milestones
                    ],
                    assumptions=analysis.extraction.assumptions,
                    mitigation_options=[
                        {"description": option.description, "tradeoff": option.tradeoff}
                        for option in analysis.extraction.mitigation_options
                    ],
                )
            )
            if audit is not None:
                await SqlAuditRepository(session).append(audit)

    async def list_for_case(
        self, context: AccessContext, po_case_id: POCaseId
    ) -> list[DelayImpactAnalysis]:
        scope = TenantScope.from_access_context(context)
        async with tenant_session(self.session_factory, scope) as session:
            result = await session.execute(
                sa.select(tables.delay_impact_analyses)
                .where(tables.delay_impact_analyses.c.po_case_id == po_case_id.value)
                .order_by(tables.delay_impact_analyses.c.created_at.desc())
            )
            return [_analysis_from_row(row) for row in result]
