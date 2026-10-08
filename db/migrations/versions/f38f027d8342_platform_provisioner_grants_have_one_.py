"""platform provisioner grants have one owner

Revision ID: f38f027d8342
Revises: a0035e9faf32
Create Date: 2026-10-08 12:30:00.000000+00:00

`dw_provisioner`'s privileges were written three times and the three
disagreed (supply-chain ticket `hardening/06`): the baseline
(`0001_platform_grants.sql`) gave it INSERT, UPDATE and DELETE on
`platform.users`, `platform.plans` and `platform.provisioning_audit`;
`scripts/create_provisioner_role.py`, for a cluster whose role came after the
migrations, gave SELECT only on users and plans and nothing on the tables
later migrations added (`tenant_offboarding_requests`, `support_staff`,
`support_grants`, `audit_events`); `test_provisioning.py`'s fixture had a
third list. Which one a cluster held depended on the order it was set up in.

`platform.grant_provisioner_privileges()` is now the one owner: it revokes
whatever the role holds on any table of any schema and grants exactly the list
below. This migration calls it; the script and the test fixture call it after
creating the role; a later migration that needs another grant replaces the
function with the longer list and calls it again. The list is what the
provisioning code does (`SqlProvisioningRepository`), the narrower of the old
copies where they disagreed:

- `tenants`, `workspaces`, `memberships`, `entitlements`: read and write.
- `platform_operators`, `support_staff`: read, add, remove (no update).
- `provisioning_audit`: read and append; `audit_events`: append.
- `tenant_offboarding_requests`: read, request, update its state.
- `support_grants`: read, and update only the assignment's columns.
- `roles`, `users`, `plans`: read only. Provisioning never writes a user or a
  plan; the baseline's write grants there were wider than any caller.

Nothing on a business schema, ever. Absent role: a notice, as the baseline did.
SECURITY INVOKER: whoever calls it must be able to grant (the migrator owns the
tables; the script runs as the cluster admin).
"""

from __future__ import annotations

from alembic import op

revision = "f38f027d8342"
down_revision = "a0035e9faf32"
branch_labels = None
depends_on = None

_FUNCTION = """
CREATE OR REPLACE FUNCTION platform.grant_provisioner_privileges() RETURNS void
    LANGUAGE plpgsql
    SET search_path = pg_catalog
AS $fn$
DECLARE
    held record;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_provisioner') THEN
        RAISE NOTICE 'role dw_provisioner absent; skipping its grants';
        RETURN;
    END IF;

    -- Whatever it holds now, on any table of any schema, goes first: the list
    -- below is the whole of it, not an addition to an older copy.
    FOR held IN
        SELECT n.nspname, c.relname
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg\\_%'
          AND (
              has_table_privilege(
                  'dw_provisioner', c.oid,
                  'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER'
              )
              OR has_any_column_privilege(
                  'dw_provisioner', c.oid, 'SELECT, INSERT, UPDATE, REFERENCES'
              )
          )
    LOOP
        EXECUTE format('REVOKE ALL ON %I.%I FROM dw_provisioner', held.nspname, held.relname);
    END LOOP;

    GRANT USAGE ON SCHEMA platform TO dw_provisioner;
    GRANT SELECT, INSERT, UPDATE, DELETE ON
        platform.tenants, platform.workspaces, platform.memberships, platform.entitlements
    TO dw_provisioner;
    GRANT SELECT, INSERT, DELETE ON platform.platform_operators, platform.support_staff
        TO dw_provisioner;
    GRANT SELECT, INSERT ON platform.provisioning_audit TO dw_provisioner;
    GRANT INSERT ON platform.audit_events TO dw_provisioner;
    GRANT SELECT, INSERT, UPDATE ON platform.tenant_offboarding_requests TO dw_provisioner;
    GRANT SELECT ON platform.support_grants TO dw_provisioner;
    GRANT UPDATE (status, staff_user_id, assigned_by, activated_at, expires_at)
        ON platform.support_grants TO dw_provisioner;
    GRANT SELECT ON platform.roles, platform.users, platform.plans TO dw_provisioner;
END
$fn$
"""


def upgrade() -> None:
    op.execute(_FUNCTION)
    # Not callable by the application roles: granting is the migrator's and
    # the cluster admin's business.
    op.execute("REVOKE ALL ON FUNCTION platform.grant_provisioner_privileges() FROM PUBLIC")
    op.execute("SELECT platform.grant_provisioner_privileges()")


def downgrade() -> None:
    # The grants it applied stay: they are narrower than what the earlier
    # revisions gave, and widening them again on a downgrade would be the
    # wrong direction to fail in.
    op.execute("DROP FUNCTION IF EXISTS platform.grant_provisioner_privileges()")
