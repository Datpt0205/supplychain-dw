"""supply chain follow ups retention

Revision ID: 992e7c6ae4fe
Revises: fd285c0433c4
Create Date: 2026-10-07 13:16:09.915039+00:00

Ticket P3: `supply_chain.follow_ups` grew without bound — one row per case,
kind and episode, closed but never deleted (failure-modes #6).

- **`supply_chain.prune_follow_ups(older_than interval)`**, SECURITY DEFINER,
  the one way the application deletes a follow-up: `dw_app` keeps no DELETE
  or TRUNCATE on the table (`dc2285c629d4`), and gets EXECUTE on this. Running
  as its definer it is not narrowed by RLS, so it narrows itself: the tenant
  AND workspace the calling transaction bound (`app.tenant_id`,
  `app.workspace_id`); none bound, nothing matches and nothing is deleted. It deletes closed rows
  (`done`, `resolved`) whose `closed_at` is older than the term, never an open
  one. The term crosses a privilege boundary, so the function bounds it (at
  least one day) rather than trusting the policy model on the other side.
- The term itself is the follow-up policy's `closed_retention_days` (1.2.0,
  180 days, the platform's is the floor), resolved per tenant by the worker's
  `supply_chain_follow_ups_retention` lane: one owner of the number, and it is
  not this file.
- **`ix_follow_ups_tenant_id_workspace_id_closed_at`**, partial on closed
  rows, carries the pass's predicate so an hourly pass does not scan every
  open follow-up of the workspace.
"""

from __future__ import annotations

from alembic import op

revision = "992e7c6ae4fe"
down_revision = "fd285c0433c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_follow_ups_tenant_id_workspace_id_closed_at"
        " ON supply_chain.follow_ups (tenant_id, workspace_id, closed_at)"
        " WHERE status <> 'open'"
    )
    op.execute(
        """
        CREATE FUNCTION supply_chain.prune_follow_ups(older_than interval)
        RETURNS integer
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, supply_chain
        AS $$
        DECLARE
            v_tenant uuid := NULLIF(current_setting('app.tenant_id', true), '')::uuid;
            v_workspace uuid := NULLIF(current_setting('app.workspace_id', true), '')::uuid;
            v_gone integer;
        BEGIN
            IF older_than IS NULL OR older_than < interval '1 day' THEN
                RAISE EXCEPTION 'a follow-up retention term must be at least one day, got %',
                    older_than USING ERRCODE = '22023';
            END IF;
            -- An unbound setting is NULL, and `= NULL` matches no row: an
            -- unscoped call deletes nothing. `status <> 'open'` is also what
            -- lets the planner use the partial index below.
            DELETE FROM supply_chain.follow_ups
            WHERE tenant_id = v_tenant
              AND workspace_id = v_workspace
              AND status <> 'open'
              AND closed_at < now() - older_than;
            GET DIAGNOSTICS v_gone = ROW_COUNT;
            RETURN v_gone;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION supply_chain.prune_follow_ups(interval) FROM PUBLIC")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                GRANT EXECUTE ON FUNCTION supply_chain.prune_follow_ups(interval) TO dw_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION supply_chain.prune_follow_ups(interval)")
    op.execute("DROP INDEX supply_chain.ix_follow_ups_tenant_id_workspace_id_closed_at")
