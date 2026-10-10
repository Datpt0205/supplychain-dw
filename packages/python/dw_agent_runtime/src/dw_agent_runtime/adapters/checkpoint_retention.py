"""The pass that keeps run checkpoints from growing without bound.

`SqlAlchemyCheckpointSaver` writes the thread's whole state on every super-step,
and nothing deleted those rows short of offboarding the tenant. A long thread
kept every earlier version of its conversation verbatim, for good — including
the text context compaction had removed from the live state. The terms are in
`dw_platform.retention_policy` (`checkpoints`), beside every other answer to
"how long do you keep our data"; this module is only the SQL.

Which rows go, in one predicate, oldest first and a bounded batch per pass:

- older than `superseded_days`, and
- on a thread with at least one FINISHED run and no run that is not finished,
  and
- either not the newest checkpoint of its thread and namespace (the newest is
  what the thread continues from), or on a thread nothing has been written to
  for `idle_thread_days`.

The run condition is written positively on purpose. "No unfinished run" alone
is also what an INVISIBLE `worker_runs` looks like — drop the drain policy that
lets this pass read runs across tenants and every thread would look settled,
paused approvals included. Requiring a finished run to be visible as well means
that failure deletes nothing. The cost is stated: a thread with checkpoints and
no run row at all is never pruned here; offboarding still removes it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.selectable import NamedFromClause

from dw_agent_runtime.adapters.runtime_tables import (
    run_checkpoint_writes,
    run_checkpoints,
    worker_runs,
)
from dw_kernel.ports import UtcClock
from dw_platform.retention_policy import RetentionPolicy

__all__ = ["FINISHED_RUN_STATUSES", "SqlCheckpointRetention"]

logger = logging.getLogger("dw_agent_runtime.checkpoint_retention")

# The statuses a run never leaves. The same set `uq_worker_runs_active_thread`
# excludes, so "this thread has no active run" means the same thing here as it
# does to the index that allows one active run per thread.
FINISHED_RUN_STATUSES = ("completed", "failed", "cancelled")

_SET_DRAIN = text("SELECT set_config('app.worker_drain', 'on', true)")


@dataclass(frozen=True)
class SqlCheckpointRetention:
    """Implements `dw_worker.consumers.retention.RetentionPrunePort` for checkpoints."""

    session_factory: async_sessionmaker[AsyncSession]
    policy: RetentionPolicy
    clock: UtcClock

    async def prune(self) -> None:
        now = self.clock.now()
        terms = self.policy.checkpoints
        superseded_cutoff = now - timedelta(days=terms.superseded_days)
        idle_cutoff = now - timedelta(days=terms.idle_thread_days)

        c = run_checkpoints.alias("c")
        newer = run_checkpoints.alias("newer")
        recent = run_checkpoints.alias("recent")
        finished = worker_runs.alias("finished")
        active = worker_runs.alias("active")

        def same_thread(runs: NamedFromClause) -> sa.ColumnElement[bool]:
            return sa.and_(runs.c.tenant_id == c.c.tenant_id, runs.c.thread_id == c.c.thread_id)

        doomed = (
            sa.select(c.c.thread_id, c.c.checkpoint_ns, c.c.checkpoint_id)
            .where(
                c.c.created_at < superseded_cutoff,
                sa.exists().where(
                    same_thread(finished), finished.c.status.in_(FINISHED_RUN_STATUSES)
                ),
                ~sa.exists().where(
                    same_thread(active), active.c.status.not_in(FINISHED_RUN_STATUSES)
                ),
                sa.or_(
                    sa.exists().where(
                        newer.c.thread_id == c.c.thread_id,
                        newer.c.checkpoint_ns == c.c.checkpoint_ns,
                        newer.c.checkpoint_id > c.c.checkpoint_id,
                    ),
                    ~sa.exists().where(
                        recent.c.thread_id == c.c.thread_id,
                        recent.c.created_at >= idle_cutoff,
                    ),
                ),
            )
            .order_by(c.c.created_at)
            .limit(self.policy.batch_limit)
        )
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_DRAIN)
            keys = [tuple(row) for row in (await session.execute(doomed)).all()]
            if not keys:
                return
            # Writes first: they belong to a checkpoint, and a write left behind
            # by its checkpoint is state nothing can read and nothing would
            # select again.
            for table in (run_checkpoint_writes, run_checkpoints):
                await session.execute(
                    sa.delete(table).where(
                        sa.tuple_(
                            table.c.thread_id, table.c.checkpoint_ns, table.c.checkpoint_id
                        ).in_(keys)
                    )
                )
        logger.info(
            "retention removed run checkpoints",
            extra={"deleted": len(keys), "policy_version": self.policy.policy_version},
        )
