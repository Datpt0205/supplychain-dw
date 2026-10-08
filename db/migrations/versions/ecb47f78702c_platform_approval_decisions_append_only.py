"""platform approval decisions append only

Revision ID: ecb47f78702c
Revises: 395acbcad324
Create Date: 2026-10-08 02:07:10.324631+00:00

A decision is written once (`ApproveAndResumeService.decide`) and no code
rewrites it, yet the default privileges handed `dw_app` UPDATE on
`platform.approval_decisions`: who decided, and how, could be changed after the
fact by any holder of the application's credentials. Since this change the
decision is also on `platform.audit_events` (`approval.decided`, written in the
same transaction), which `dw_app` may only append to; this closes the other copy.

- `dw_app` loses UPDATE on `platform.approval_decisions`.
- DELETE stays: offboarding purges every table `dw_app` may delete from
  (`offboarding.py`, `_CATALOG_PURGEABLE`), and the durable record of a
  decision is the audit row, which offboarding exports and never purges.
- Idempotent and guarded on the role existing: a product that already revoked
  it lands on the same state. Downgrade gives back what the default privileges
  granted.

`dw_platform/tests/integration/test_privileges.py` asserts it.
"""

from __future__ import annotations

from alembic import op

revision = "ecb47f78702c"
down_revision = "395acbcad324"
branch_labels = None
depends_on = None


def _for_app(statement: str) -> str:
    return f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                {statement}
            END IF;
        END
        $$
        """


def upgrade() -> None:
    op.execute(_for_app("REVOKE UPDATE ON platform.approval_decisions FROM dw_app;"))


def downgrade() -> None:
    op.execute(_for_app("GRANT UPDATE ON platform.approval_decisions TO dw_app;"))
