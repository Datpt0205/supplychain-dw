"""supply chain follow ups

Revision ID: dc2285c629d4
Revises: 89e86dfabad6
Create Date: 2026-09-28

The work a reminder, an escalation or an SLA breach hands to a person. A
worker sweep opens one when its signal holds, notifies the people who must
act, and resolves it when the signal clears; a person may also mark it done.

- **`supply_chain.follow_ups`**, tenant-scoped like `po_cases` (RLS on
  `tenant_id`, enabled and forced). One row per case, kind and *episode*:
  `(po_case_id, kind, episode)` is unique, so a sweep that runs twice, or a
  retry, opens nothing twice, while a supplier who wrote and then went quiet
  again is a new episode and a new follow-up.
- **Stamped when it opens:** the recipient scopes (from the tenant's
  follow-up policy), the days and limit that made it due. A later policy
  edit does not re-route work already handed out.
- **History, not a queue:** a follow-up is closed (`done` by a person,
  `resolved` by the sweep), never deleted by the application; dw_app has no
  DELETE. It goes with its case (ON DELETE CASCADE), which is how
  offboarding's purge of cases removes it.
- **`supply_chain.tenants_with_cases()`**, SECURITY DEFINER: the one read
  that crosses tenants, so the sweep knows whom to visit. It returns tenant
  and workspace ids only; every read after it runs under that tenant's RLS.
- **Scopes:** `supply_chain.follow_up_policy.read` joins every Supply Chain
  role (each role contains the viewer's reads), `.write` joins
  `sc_process_admin`, and the write joins the rule-setting side of
  `sod_sc_rules_vs_operations` with the other policy writes.
"""

from __future__ import annotations

import json

from alembic import op

revision = "dc2285c629d4"
down_revision = "89e86dfabad6"
branch_labels = None
depends_on = None

_TENANT = "(tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid)"
_READ = "supply_chain.follow_up_policy.read"
_WRITE = "supply_chain.follow_up_policy.write"
_SC_ROLES = (
    "sc_viewer",
    "sc_operator",
    "sc_finance",
    "sc_qc",
    "sc_logistics",
    "sc_warehouse",
    "sc_process_admin",
)
_RULE = "sod_sc_rules_vs_operations"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE supply_chain.follow_ups (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            po_case_id uuid NOT NULL,
            kind text NOT NULL,
            episode text NOT NULL,
            milestone text,
            days integer NOT NULL,
            limit_days integer,
            recipient_scopes jsonb NOT NULL,
            status text DEFAULT 'open' NOT NULL,
            opened_at timestamp with time zone DEFAULT now() NOT NULL,
            notified_at timestamp with time zone,
            closed_at timestamp with time zone,
            closed_by uuid,
            close_note text,
            CONSTRAINT pk_follow_ups PRIMARY KEY (id),
            CONSTRAINT fk_follow_ups_po_case_id_po_cases FOREIGN KEY (po_case_id)
                REFERENCES supply_chain.po_cases (id) ON DELETE CASCADE,
            CONSTRAINT uq_follow_ups_po_case_id_kind_episode UNIQUE (po_case_id, kind, episode),
            CONSTRAINT ck_follow_ups_kind
                CHECK (kind IN ('update_reminder', 'update_escalation', 'sla_breach')),
            CONSTRAINT ck_follow_ups_status CHECK (status IN ('open', 'done', 'resolved')),
            CONSTRAINT ck_follow_ups_episode CHECK (btrim(episode) <> ''),
            CONSTRAINT ck_follow_ups_days
                CHECK (days >= 0 AND (limit_days IS NULL OR limit_days >= 0)),
            CONSTRAINT ck_follow_ups_recipient_scopes CHECK (
                jsonb_typeof(recipient_scopes) = 'array'
                AND jsonb_array_length(recipient_scopes) > 0
            ),
            CONSTRAINT ck_follow_ups_closed CHECK ((status = 'open') = (closed_at IS NULL)),
            CONSTRAINT ck_follow_ups_closed_by CHECK (closed_by IS NULL OR status = 'done'),
            CONSTRAINT ck_follow_ups_close_note
                CHECK (close_note IS NULL OR char_length(close_note) <= 2000)
        )
        """
    )
    # The list pages open follow-ups newest first; RLS supplies the tenant.
    op.execute(
        "CREATE INDEX ix_follow_ups_tenant_id_status_opened_at"
        " ON supply_chain.follow_ups (tenant_id, status, opened_at DESC)"
    )
    op.execute("ALTER TABLE supply_chain.follow_ups ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE supply_chain.follow_ups FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_follow_ups ON supply_chain.follow_ups"
        f" USING {_TENANT} WITH CHECK {_TENANT}"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                REVOKE DELETE, TRUNCATE ON supply_chain.follow_ups FROM dw_app;
            END IF;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION supply_chain.tenants_with_cases()
        RETURNS TABLE (tenant_id uuid, workspace_id uuid)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, supply_chain
        AS $$
            SELECT c.tenant_id, (array_agg(c.workspace_id ORDER BY c.workspace_id))[1]
            FROM supply_chain.po_cases c
            GROUP BY c.tenant_id
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION supply_chain.tenants_with_cases() FROM PUBLIC")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                GRANT EXECUTE ON FUNCTION supply_chain.tenants_with_cases() TO dw_app;
            END IF;
        END
        $$
        """
    )

    roles = ", ".join(f"'{key}'" for key in _SC_ROLES)
    op.execute(
        f"""
        UPDATE platform.roles SET scopes = scopes || '{json.dumps([_READ])}'::jsonb
        WHERE key IN ({roles}) AND NOT scopes ? '{_READ}'
        """
    )
    op.execute(
        f"""
        UPDATE platform.roles SET scopes = scopes || '{json.dumps([_WRITE])}'::jsonb
        WHERE key = 'sc_process_admin' AND NOT scopes ? '{_WRITE}'
        """
    )
    op.execute(
        f"""
        UPDATE platform.sod_rules SET left_scopes = left_scopes || '{json.dumps([_WRITE])}'::jsonb
        WHERE key = '{_RULE}' AND NOT left_scopes ? '{_WRITE}'
        """
    )


def downgrade() -> None:
    op.execute(
        f"UPDATE platform.sod_rules SET left_scopes = left_scopes - '{_WRITE}'"
        f" WHERE key = '{_RULE}'"
    )
    roles = ", ".join(f"'{key}'" for key in _SC_ROLES)
    op.execute(
        f"UPDATE platform.roles SET scopes = scopes - '{_READ}' - '{_WRITE}' WHERE key IN ({roles})"
    )
    op.execute("DROP FUNCTION supply_chain.tenants_with_cases()")
    op.execute("DROP TABLE supply_chain.follow_ups")
