"""platform support grants readable by their staff member

Revision ID: f1576bf82a5a
Revises: af8ee878b4ab
Create Date: 2026-10-08

A support staff member is not a member of the customer's tenant (ADR 0024),
so RLS on `platform.support_grants` shows them nothing, which is right. They
still need to read the grants assigned to them: to build a support access
context from one, and to list "my grants". Two narrow `SECURITY DEFINER`
functions answer exactly that and nothing else:

- `platform.support_grant_for_staff(p_grant uuid)`: the one grant, only when
  its `staff_user_id` is the caller's `app.principal_id`.
- `platform.support_grants_for_staff()`: the caller's grants that are active,
  or ended (expired or revoked) in the last 30 days, with the company and
  workspace names.

Neither takes a user id: the person is the transaction's `app.principal_id`,
which the API binds from the verified token's own user, never from the
request. An unset principal matches nobody. `REVOKE ALL FROM PUBLIC`;
`dw_app` may execute both. Whether a grant is still in force (`expired`,
`ineffective`) is not decided here: the application reads these rows through
`grant_effective_state`, the one owner of that rule.
"""

from __future__ import annotations

from alembic import op

revision = "f1576bf82a5a"
down_revision = "af8ee878b4ab"
branch_labels = None
depends_on = None

_PRINCIPAL = "NULLIF(current_setting('app.principal_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION platform.support_grant_for_staff(p_grant uuid)
        RETURNS SETOF platform.support_grants
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
            SELECT g.*
            FROM platform.support_grants g
            WHERE g.id = p_grant
              AND g.staff_user_id = {_PRINCIPAL}
        $$
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION platform.support_grants_for_staff()
        RETURNS TABLE (
            id uuid,
            code text,
            tenant_id uuid,
            tenant_name text,
            workspace_id uuid,
            workspace_name text,
            resource_type text,
            resource_id uuid,
            resource_label text,
            scope_set_key text,
            scope_set_label text,
            scopes text[],
            status text,
            granted_by uuid,
            activated_at timestamp with time zone,
            expires_at timestamp with time zone,
            revoked_at timestamp with time zone
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
            SELECT g.id, g.code, g.tenant_id, t.name, g.workspace_id, w.name,
                   g.resource_type, g.resource_id, g.resource_label,
                   g.scope_set_key, g.scope_set_label, g.scopes, g.status,
                   g.granted_by, g.activated_at, g.expires_at, g.revoked_at
            FROM platform.support_grants g
            JOIN platform.tenants t ON t.id = g.tenant_id
            JOIN platform.workspaces w ON w.id = g.workspace_id
            WHERE g.staff_user_id = {_PRINCIPAL}
              AND g.activated_at IS NOT NULL
              AND least(g.expires_at, coalesce(g.revoked_at, g.expires_at))
                  > now() - interval '30 days'
            ORDER BY g.activated_at DESC, g.id
        $$
        """
    )
    for signature in ("support_grant_for_staff(uuid)", "support_grants_for_staff()"):
        op.execute(f"REVOKE ALL ON FUNCTION platform.{signature} FROM PUBLIC")
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                    GRANT EXECUTE ON FUNCTION platform.{signature} TO dw_app;
                END IF;
            END
            $$
            """
        )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS platform.support_grants_for_staff()")
    op.execute("DROP FUNCTION IF EXISTS platform.support_grant_for_staff(uuid)")
