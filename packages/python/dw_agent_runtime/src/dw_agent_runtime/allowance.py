"""A tenant's daily allowance — runs per day and spend per day — checked before
anything spends.

Two doors reach a model: a run the runner starts, and a single structured call a
handler makes with no run around it (a command bar question, a chat message the
worker reads). Both are checked here, by one object, so the plan's limits are one
answer whichever door was used (failure-modes #5: a quota enforced on one door is
a quota the other walks around).

The plan comes from ``run_context``, which carries what the requester was
entitled to when the turn started. Neither check is transactional, deliberately:
counting is a read, and holding a lock across a whole start to make the count
exact would serialise everything a tenant does. The slack is bounded by how much
one tenant starts in the same instant — the difference between 200 and 203 a
day, not between 200 and unlimited.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.ports import RunAllowancePort
from dw_kernel.errors import QuotaExceededError
from dw_kernel.ports import UtcClock


class RunsStartedPort(Protocol):
    """How many runs a tenant has started since a moment (``SqlWorkerRunStore``)."""

    async def started_since(self, tenant_id: uuid.UUID, since: datetime) -> int: ...


class SpendTodayPort(Protocol):
    """What a tenant has spent on one UTC day (``SqlSpendGuardStore``)."""

    async def spend_today(self, tenant_id: uuid.UUID, day: date) -> Decimal: ...


@dataclass(frozen=True)
class DailyAllowance:
    """Refuses with ``QuotaExceededError`` once the tenant's plan has nothing
    left for today; returns otherwise."""

    allowance: RunAllowancePort
    runs: RunsStartedPort
    # None for a wiring that predates the spend guard; every plan ships
    # `spend_usd_per_day=None` today as well. Either means the spend check
    # returns at once. Recording spend happens elsewhere either way.
    spend: SpendTodayPort | None
    clock: UtcClock

    async def require(self, run_context: RunContext) -> None:
        await self._require_runs(run_context)
        await self._require_spend(run_context)

    async def _require_runs(self, run_context: RunContext) -> None:
        day_start = (
            self.clock.now().astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        )
        limit = self.allowance.runs_per_day(run_context.plan_id)
        if limit is None:
            return
        used = await self.runs.started_since(run_context.tenant_id, day_start)
        if used < limit:
            return
        raise QuotaExceededError(
            "hôm nay đã dùng hết số lượt chạy của gói; thử lại sau 00:00 UTC"
            " hoặc nâng gói để có thêm lượt",
            details={
                "quota": "runs_per_day",
                "limit": str(limit),
                "used": str(used),
                "plan_id": run_context.plan_id,
                "resets_at": (day_start + timedelta(days=1)).isoformat(),
            },
        )

    async def _require_spend(self, run_context: RunContext) -> None:
        if self.spend is None:
            return
        limit = self.allowance.spend_usd_per_day(run_context.plan_id)
        if limit is None:
            return
        today = self.clock.now().astimezone(UTC).date()
        spent = await self.spend.spend_today(run_context.tenant_id, today)
        if spent < limit:
            return
        raise QuotaExceededError(
            "hôm nay đã dùng hết trần chi tiêu của gói; thử lại sau 00:00 UTC"
            " hoặc nâng gói để có thêm trần",
            details={
                "quota": "spend_usd_per_day",
                "limit": str(limit),
                "used": str(spent),
                "plan_id": run_context.plan_id,
            },
        )
