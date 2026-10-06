"""platform approval and audit pages carry the workspace

Revision ID: cbf765d02a12
Revises: 7c422b849fe9
Create Date: 2026-10-06 02:10:00.830651+00:00

The approval inbox and the audit trail are now read within the caller's
workspace (platform-runtime/approval-audit-and-workspace/02): RLS on both
tables narrows by tenant only, so `SqlApprovalRepository.list_pending`,
`SqlPendingApprovalQuery` and `SqlAuditRepository.list_page` name
`workspace_id` themselves. The indexes their keyset orderings ran on led with
`tenant_id` and went straight to the sort, so a workspace with nothing pending
in a tenant whose other workspaces have thousands read all of those to fill an
empty page: page 50 costing what page 1 costs, which `0003_keyset_indexes`
was written to stop. Each index below is (RLS predicate, query predicate,
sort key), the shape `0003` set and `ix_items_page` already has.

- `ix_approval_requests_page` gains `workspace_id` after `tenant_id`. Rebuilt
  rather than kept beside a new one: every reader of the inbox now names a
  workspace, so the old one answers nothing the new one cannot.
- `ix_audit_events_page` is new, on the partitioned parent, so Postgres builds
  it on every partition and gives it to each one `ensure_time_partitions`
  creates later. `ix_audit_events_tenant_time` (baseline) stays: it is not a
  prefix of the new one, and a tenant-wide time range is still a question
  this table may be asked.

Locking, and why plain `CREATE INDEX` is acceptable here: each statement holds
a SHARE lock for the length of its build, on `approval_requests` and on the
`audit_events` parent and every partition, so approval writes and every
audited write wait until it finishes. On 2026-10-06 the plan records no
deployed environment with a populated audit trail (the uat/production model
profile is still a decision owed, `.claude/PLAN.md`), so the build is short;
whoever deploys this onto a large trail checks first. Once one exists,
the same change on a large table is done without blocking writes: `CREATE
INDEX ... ON ONLY platform.audit_events`, then `CREATE INDEX CONCURRENTLY` on
each partition outside alembic's transaction (`op.get_context().
autocommit_block()`), then `ALTER INDEX ... ATTACH PARTITION`; and for
`approval_requests`, `CREATE INDEX CONCURRENTLY` under a temporary name, drop
the old one, rename.
"""

from __future__ import annotations

from alembic import op

revision = "cbf765d02a12"
down_revision = "7c422b849fe9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX platform.ix_approval_requests_page")
    op.execute(
        "CREATE INDEX ix_approval_requests_page ON platform.approval_requests"
        " (tenant_id, workspace_id, status, created_at DESC, id DESC)"
    )
    op.execute(
        "CREATE INDEX ix_audit_events_page ON platform.audit_events"
        " (tenant_id, workspace_id, occurred_at DESC, id DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX platform.ix_audit_events_page")
    op.execute("DROP INDEX platform.ix_approval_requests_page")
    op.execute(
        "CREATE INDEX ix_approval_requests_page ON platform.approval_requests"
        " (tenant_id, status, created_at DESC, id DESC)"
    )
