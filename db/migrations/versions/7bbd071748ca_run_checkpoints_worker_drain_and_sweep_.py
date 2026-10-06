"""run checkpoints worker drain and sweep index

Revision ID: 7bbd071748ca
Revises: 5e6ccac63d45
Create Date: 2026-10-06

Run checkpoints were written on every super-step and deleted by nothing but
offboarding: `SqlAlchemyCheckpointSaver` keeps the whole message list per
checkpoint, so a long thread held every earlier version of its conversation
verbatim, for good (platform-runtime/compaction). The retention lane now prunes
them on the term in `configs/policies/retention@1.6.0.yaml`, and this revision
is what lets it.

Same escape hatch `0010_memory_retention_drain` and the others use, for the same
reason: the sweep deletes across every tenant, and a sweep scoped by
`app.tenant_id` would first need a list of tenants — itself the cross-tenant
read the scoping exists to prevent. The GUC is set per transaction by the sweep
and never by anything reachable from a request.

- `run_checkpoints`, `run_checkpoint_writes`: read and delete under the drain.
- `worker_runs`: READ only under the drain (`FOR SELECT`). The sweep keeps every
  checkpoint of a thread that has a run still pending, running or waiting for a
  person, and it can only see that run if this policy lets it. The sweep's
  predicate is written to fail closed without it — a thread is pruned only when
  a FINISHED run is visible too — so dropping this policy stops the sweep rather
  than letting it delete a paused run's state.
- `ix_run_checkpoints_created_at`: the sweep's filter is age, across tenants,
  so the index leads with `created_at` and not `tenant_id` (the drain names no
  tenant; an index leading with one would not serve it).

No grants: `dw_app` already holds SELECT/DELETE on all three tables
(`0001_platform_grants.sql`), which `test_privileges.py` asserts.

Reversible: the policies and the index drop, and the sweep then deletes nothing.
"""

from __future__ import annotations

from alembic import op

revision = "7bbd071748ca"
down_revision = "5e6ccac63d45"
branch_labels = None
depends_on = None

_DRAIN = "current_setting('app.worker_drain', true) = 'on'"


def upgrade() -> None:
    for table in ("run_checkpoints", "run_checkpoint_writes"):
        op.execute(
            f"CREATE POLICY worker_drain_{table} ON platform.{table}"
            f" USING ({_DRAIN}) WITH CHECK ({_DRAIN})"
        )
    op.execute(
        f"CREATE POLICY worker_drain_worker_runs ON platform.worker_runs"
        f" FOR SELECT USING ({_DRAIN})"
    )
    op.execute(
        "CREATE INDEX ix_run_checkpoints_created_at ON platform.run_checkpoints (created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS platform.ix_run_checkpoints_created_at")
    op.execute("DROP POLICY IF EXISTS worker_drain_worker_runs ON platform.worker_runs")
    for table in ("run_checkpoint_writes", "run_checkpoints"):
        op.execute(f"DROP POLICY IF EXISTS worker_drain_{table} ON platform.{table}")
