"""The daily spend guard: `platform.tenant_daily_spend_guard`.

Not the ledger that was pulled (`platform.model_usage_ledger`, migration
`aefe7c1f5d9b`) — that one was read as "evidence for invoicing" it never was.
This is the mechanism only: one row per `(tenant_id, spend_date)`, a running
total, read by nothing but the runner's own gate
(`RunAllowancePort.spend_usd_per_day`, checked in `langgraph_runner.py`
alongside the existing `runs_per_day` gate). No admin route, no dashboard.

Every plan ships `spend_usd_per_day=None` today (see
`dw_platform.application.entitlement`) — this needs Đạt's actual dollar
thresholds before it protects anything. Recording still runs regardless, so
the numbers are already accumulating for whenever a threshold is set.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_agent_runtime.adapters.runtime_tables import tenant_daily_spend_guard
from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.model.gateway import ModelUsage
from dw_agent_runtime.ports import ModelRequest
from dw_kernel.ports import UtcClock

__all__ = ["SqlSpendGuardRecorder", "SqlSpendGuardRetention", "SqlSpendGuardStore"]

_SET_TENANT = text("SELECT set_config('app.tenant_id', :tenant_id, true)")
# Cross-tenant, like the memory/knowledge retention sweeps: aging out rows
# older than the guard's window is one DELETE across every tenant, and a
# sweep scoped by app.tenant_id would first need a list of tenants. See
# migration c3ec03bd6fd1.
_SET_DRAIN = text("SELECT set_config('app.worker_drain', 'on', true)")


@dataclass(frozen=True)
class SqlSpendGuardStore:
    """Reads today's running total for the runner's gate to check."""

    session_factory: async_sessionmaker[AsyncSession]

    async def spend_today(self, tenant_id: UUID, day: date) -> Decimal:
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_TENANT, {"tenant_id": str(tenant_id)})
            result = await session.execute(
                sa.select(tenant_daily_spend_guard.c.spend_usd).where(
                    tenant_daily_spend_guard.c.tenant_id == tenant_id,
                    tenant_daily_spend_guard.c.spend_date == day,
                )
            )
            spend = result.scalar_one_or_none()
            return spend if spend is not None else Decimal(0)


@dataclass(frozen=True)
class SqlSpendGuardRecorder:
    """Implements `UsageRecorderPort`: upserts one call's cost into today's total.

    Same fail-open contract every recorder gets from `CompositeUsageRecorder` -
    a failure here is logged, never takes the run down. The model answer the
    caller is holding has already been paid for; losing this one call's
    contribution to the running total is the smaller loss by a wide margin.
    """

    session_factory: async_sessionmaker[AsyncSession]
    clock: UtcClock

    async def record(
        self, run_context: RunContext, request: ModelRequest, usage: ModelUsage
    ) -> None:
        if usage.cost_usd <= 0:
            return
        today = self.clock.now().date()
        # str() first: Decimal(0.1) reproduces float's binary imprecision,
        # Decimal("0.1") does not.
        cost = Decimal(str(usage.cost_usd))
        upsert = pg_insert(tenant_daily_spend_guard).values(
            tenant_id=run_context.tenant_id,
            spend_date=today,
            spend_usd=cost,
            updated_at=self.clock.now(),
        )
        upsert = upsert.on_conflict_do_update(
            index_elements=[
                tenant_daily_spend_guard.c.tenant_id,
                tenant_daily_spend_guard.c.spend_date,
            ],
            set_={
                "spend_usd": tenant_daily_spend_guard.c.spend_usd + upsert.excluded.spend_usd,
                "updated_at": upsert.excluded.updated_at,
            },
        )
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_TENANT, {"tenant_id": str(run_context.tenant_id)})
            await session.execute(upsert)


@dataclass(frozen=True)
class SqlSpendGuardRetention:
    """Implements `dw_worker.consumers.retention.RetentionPrunePort`.

    Not a legal retention term the way audit/memory/knowledge are — nothing
    here answers a compliance question, so the window is a technical
    constant rather than a decision in `configs/policies/retention@1.7.0.yaml`.
    Rows are cold within a day or two: nothing ever reads a `spend_date` once
    the day it named has passed. `keep_days` is a buffer against clock skew
    and a slow sweep, not a term anyone had to choose.
    """

    session_factory: async_sessionmaker[AsyncSession]
    clock: UtcClock
    keep_days: int = 30

    async def prune(self) -> None:
        cutoff = self.clock.now().date() - timedelta(days=self.keep_days)
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_DRAIN)
            await session.execute(
                sa.delete(tenant_daily_spend_guard).where(
                    tenant_daily_spend_guard.c.spend_date < cutoff
                )
            )
