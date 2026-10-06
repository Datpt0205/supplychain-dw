"""platform approval requests required scope

Revision ID: 36dabf47619c
Revises: 7bbd071748ca
Create Date: 2026-10-06 16:20:08.763731+00:00

Who may decide an approval is stamped on it when it is raised (docs/adr/0004):
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
- Approval RLS is unchanged here: it narrows by tenant only. Reads are
  narrowed to the caller's workspace in the repository
  (platform-runtime/approval-audit-and-workspace ticket 02).

**Twin.** This change came back from the first product, which had already
shipped it as its own revision `5d3965984679` (same column, same CHECK, same
grant). A product that merges this platform carries both, on two branches
alembic may run in either order, so every statement here is idempotent: the
column is added if missing, the CHECK only if no constraint of that name exists,
and the REVOKE/GRANT pair lands on the same privileges however often it runs.
Downgrade is a no-op while the twin is still applied, because the objects are
then the twin's too; whichever of the pair is downgraded last removes them.
Where the twin does not exist (this repository) the check never matches.
"""

from __future__ import annotations

from alembic import context, op
from alembic.script import ScriptDirectory

revision = "36dabf47619c"
down_revision = "7bbd071748ca"
branch_labels = None
depends_on = None

# The product revision that shipped the same change first (see docstring).
_TWIN = "5d3965984679"

# What `SqlApprovalRepository.save` writes; nothing else updates the table.
_DECISION_COLUMNS = "status, decided_at, version"


def _twin_applied() -> bool:
    script = ScriptDirectory.from_config(context.config)
    heads = op.get_context().get_current_heads()
    return any(
        rev.revision == _TWIN for head in heads for rev in script.iterate_revisions(head, "base")
    )


def upgrade() -> None:
    op.execute(
        "ALTER TABLE platform.approval_requests ADD COLUMN IF NOT EXISTS required_scope text"
    )
    op.execute(
        r"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'platform.approval_requests'::regclass
                  AND conname = 'ck_approval_requests_required_scope'
            ) THEN
                ALTER TABLE platform.approval_requests
                    ADD CONSTRAINT ck_approval_requests_required_scope CHECK (
                        required_scope IS NULL
                        OR required_scope ~ '^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$');
            END IF;
        END
        $$
        """
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
    if _twin_applied():
        return
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
