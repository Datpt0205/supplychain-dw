"""Keeping the audit log in monthly partitions, on the worker's schedule.

`platform.audit_events` is partitioned by month. The baseline said an
operational job would create those partitions ahead of time; it never existed,
every row landed in the DEFAULT partition, and a default partition cannot be
dropped — so the retention answer for audit ("DROP PARTITION, instant, nothing
to vacuum") could not run at all.

`platform.model_usage_ledger` was the second table here until it was removed
entirely (migration `aefe7c1f5d9b`); one parent left is why the function takes
one cutoff.

This is that job, and it runs in the worker rather than in a cron entry someone
has to remember to install. Falling behind is not self-correcting: a month with
no partition sends its rows to the default, and a default holding that month's
rows then blocks the month's partition from ever being created.

**Why it calls functions instead of running DDL.** Creating a partition is DDL
on a table `dw_app` does not own, and `dw_app` deliberately has no BYPASSRLS.
Migration `06d9e1a67d40` installs two SECURITY DEFINER functions owned by the
migrator, with a fixed `search_path`, EXECUTE revoked from PUBLIC, and no
argument that names a table — the parents are a constant in the body. All this
module supplies is a bounded integer and two cutoffs.

**Creating and dropping are separate transactions.** Creating is safe and
always worth doing; dropping destroys a month. A drop that fails must not take
the creation with it, because the creation is what keeps next month's rows out
of the default.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_kernel.ports import UtcClock
from dw_platform.retention_policy import RetentionPolicy

logger = logging.getLogger("dw_platform.partitions")

__all__ = ["SqlPartitionMaintenance"]

_ENSURE = sa.text("SELECT platform.ensure_time_partitions(:months_ahead)")
_DROP = sa.text("SELECT platform.drop_expired_partitions(:audit_cutoff)")

# The one table this build partitions. A name the policy file carries that is
# not this one is not a table this build may be told to drop months from.
_AUDIT = "audit_events"


@dataclass(frozen=True)
class SqlPartitionMaintenance:
    """Implements `dw_worker.consumers.retention.RetentionPrunePort` for partitions."""

    session_factory: async_sessionmaker[AsyncSession]
    policy: RetentionPolicy
    clock: UtcClock

    async def prune(self) -> None:
        created = await self._create_ahead()
        if created:
            logger.info(
                "created time partitions",
                extra={"partitions": created, "policy_version": self.policy.policy_version},
            )
        dropped = await self._drop_expired()
        if dropped:
            logger.info(
                "dropped expired time partitions",
                extra={"partitions": dropped, "policy_version": self.policy.policy_version},
            )

    async def _create_ahead(self) -> list[str]:
        async with self.session_factory() as session, session.begin():
            made = await session.scalars(_ENSURE, {"months_ahead": self.policy.audit.months_ahead})
            return list(made.all())

    async def _drop_expired(self) -> list[str]:
        """Both cutoffs in one call, and `None` for a table with no term.

        `None` reaches the function as SQL NULL, which is how the table is told
        it is never dropped — the same meaning a memory class with no `days` carries,
        and what a decided-but-not-yet-enforced term resolves to. Passing a very
        old timestamp instead would be a term nobody wrote.

        No early return on NULL: the call is what makes the function the one
        place that decides, and short-circuiting here would put half the
        decision on this side of the connection where nothing tests it.
        """
        cutoff = self.policy.audit.cutoff_for(_AUDIT, now=self.clock.now())
        async with self.session_factory() as session, session.begin():
            gone = await session.scalars(_DROP, {"audit_cutoff": cutoff})
            return list(gone.all())
