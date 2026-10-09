"""SQL persistence for sample measurements (1021f7fe88f0; ticket
ai-automation/09).

Runs under `tenant_session` (tenant AND workspace bound) and names both in
every statement as a second layer. Append-only: a correction is a newer row,
and readers take the newest value of each criterion of the round. The audit
event commits with its row.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_platform.adapters.persistence.repositories import SqlAuditRepository
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence import tables
from dw_supply_chain.application.sample_checklist import NewMeasurement
from dw_supply_chain.domain.sample_evaluation import Measurement

_m = tables.sample_measurements


@dataclass(frozen=True)
class SqlSampleMeasurements:
    """Implements `MeasurementStorePort`."""

    session_factory: async_sessionmaker[AsyncSession]

    async def for_round(
        self, context: AccessContext, case_id: uuid.UUID, sample_round: int
    ) -> list[Measurement]:
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            rows = (
                await session.execute(
                    sa.select(_m)
                    .where(
                        _m.c.tenant_id == context.tenant_id,
                        _m.c.workspace_id == context.workspace_id,
                        _m.c.product_dev_case_id == case_id,
                        _m.c.sample_round == sample_round,
                    )
                    .order_by(_m.c.criterion, _m.c.entered_at)
                    .limit(2000)
                )
            ).all()
        return [
            Measurement(
                id=r.id,
                sample_round=r.sample_round,
                criterion=r.criterion,
                value=r.value,
                note=r.note,
                entered_by=r.entered_by,
                entered_at=r.entered_at,
            )
            for r in rows
        ]

    async def add(
        self, context: AccessContext, measurement: NewMeasurement, *, audit: AuditEvent
    ) -> None:
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            await session.execute(
                sa.insert(_m).values(
                    id=measurement.id,
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    product_dev_case_id=measurement.case_id,
                    sample_round=measurement.sample_round,
                    criterion=measurement.criterion,
                    value=measurement.value,
                    note=measurement.note,
                    entered_by=context.principal_id,
                )
            )
            await SqlAuditRepository(session).append(audit)
