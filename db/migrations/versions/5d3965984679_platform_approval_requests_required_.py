"""platform approval requests required scope

Revision ID: 5d3965984679
Revises: cf66605631d7
Create Date: 2026-10-05 11:07:36.093329+00:00

Who may decide an approval is stamped on it when it is raised (ADR 0020):
`required_scope`, the scope a decider must hold besides `approvals.decide`.
NULL keeps today's rule, so every request already in the table is unchanged.

- **One owner for the shape of a scope name.** The CHECK below is the only place
  the pattern is written. `_create_approval` passes the node's value through
  untouched, so a malformed one fails this INSERT and the run ends failed with
  no approval row; nothing upstream coerces or drops it (failure-modes #7).
  An empty string is malformed, not "no scope": it would otherwise be a stamp
  nobody can satisfy, or one some later `or None` quietly turns into none.
- **Written once.** A decision changes `status`, `decided_at` and `version`, and
  nothing else; the stamp is the past decision of who may decide, so no later
  write may move it. Postgres cannot revoke one column from a table-wide
  UPDATE, so the table-wide grant default privileges gave `dw_app` becomes a
  column grant on exactly what `SqlApprovalRepository.save` writes. That also
  freezes `approval_type`, `requested_by` and `payload`, which nothing updates
  either. `test_privileges.py` asserts it from the catalog.
- Approval RLS is unchanged here: it narrows by tenant only. Narrowing reads
  by workspace is platform-runtime/approval-audit-and-workspace ticket 02.
"""

from __future__ import annotations

from alembic import op

revision = "5d3965984679"
down_revision = "cf66605631d7"
branch_labels = None
depends_on = None

# What `SqlApprovalRepository.save` writes; nothing else updates the table.
_DECISION_COLUMNS = "status, decided_at, version"


def upgrade() -> None:
    op.execute("ALTER TABLE platform.approval_requests ADD COLUMN required_scope text")
    op.execute(
        "ALTER TABLE platform.approval_requests"
        " ADD CONSTRAINT ck_approval_requests_required_scope CHECK ("
        " required_scope IS NULL"
        r" OR required_scope ~ '^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$')"
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                REVOKE UPDATE ON platform.approval_requests FROM dw_app;
                GRANT UPDATE ({_DECISION_COLUMNS}) ON platform.approval_requests TO dw_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                REVOKE UPDATE ({_DECISION_COLUMNS}) ON platform.approval_requests FROM dw_app;
                GRANT UPDATE ON platform.approval_requests TO dw_app;
            END IF;
        END
        $$
        """
    )
    op.execute(
        "ALTER TABLE platform.approval_requests"
        " DROP CONSTRAINT IF EXISTS ck_approval_requests_required_scope"
    )
    op.execute("ALTER TABLE platform.approval_requests DROP COLUMN IF EXISTS required_scope")
