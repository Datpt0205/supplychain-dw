"""platform approval and audit pages carry the workspace

Revision ID: 6d4aed20ccf2
Revises: 36dabf47619c
Create Date: 2026-10-06 16:33:05.528804+00:00

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

**Twin.** This change came back from the first product, which had already
shipped it as its own revision `cbf765d02a12`, with the same index names and
definitions. A product that merges this platform carries both, on two branches
alembic may run in either order, so upgrade is idempotent: the approval index
is rebuilt only when the one under that name is not already the workspace-led
definition, and the audit index is created only if missing. When it already
exists the twin built it on the partitioned parent, so every partition has it
and `ensure_time_partitions` gives it to later ones, as it would here.
Downgrade is a no-op while the twin is still applied, because the indexes are
then the twin's too; whichever of the pair is downgraded last restores them.
Where the twin does not exist (this repository) the check never matches.
"""

from __future__ import annotations

from alembic import context, op
from alembic.script import ScriptDirectory

revision = "6d4aed20ccf2"
down_revision = "36dabf47619c"
branch_labels = None
depends_on = None

# The product revision that shipped the same change first (see docstring).
_TWIN = "cbf765d02a12"

# How Postgres renders the workspace-led approval index (`pg_indexes.indexdef`).
# A rendering that ever differs only costs a rebuild to the same definition.
_APPROVAL_PAGE_DEF = (
    "CREATE INDEX ix_approval_requests_page ON platform.approval_requests"
    " USING btree (tenant_id, workspace_id, status, created_at DESC, id DESC)"
)


def _twin_applied() -> bool:
    script = ScriptDirectory.from_config(context.config)
    heads = op.get_context().get_current_heads()
    return any(
        rev.revision == _TWIN for head in heads for rev in script.iterate_revisions(head, "base")
    )


def upgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE schemaname = 'platform'
                  AND indexname = 'ix_approval_requests_page'
                  AND indexdef = '{_APPROVAL_PAGE_DEF}'
            ) THEN
                DROP INDEX IF EXISTS platform.ix_approval_requests_page;
                CREATE INDEX ix_approval_requests_page ON platform.approval_requests
                    (tenant_id, workspace_id, status, created_at DESC, id DESC);
            END IF;
        END
        $$
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_events_page ON platform.audit_events"
        " (tenant_id, workspace_id, occurred_at DESC, id DESC)"
    )


def downgrade() -> None:
    if _twin_applied():
        return
    op.execute("DROP INDEX platform.ix_audit_events_page")
    op.execute("DROP INDEX platform.ix_approval_requests_page")
    op.execute(
        "CREATE INDEX ix_approval_requests_page ON platform.approval_requests"
        " (tenant_id, status, created_at DESC, id DESC)"
    )
