"""platform channel deliveries

Revision ID: 5a25154e0296
Revises: 9f2becb1bf80
Create Date: 2026-10-07 15:58:10.489888+00:00

An in-app notification to a person who linked a chat also goes out through that
chat (channels Z2, ADR 0006). `platform.channel_deliveries` holds one
row per notification, recipient and channel: its status, its attempts, when it
may be tried next, and what the provider called the message it sent.

- **One door in.** `platform.deliver_notification` (855ae928c3fa) is replaced
  here so that, in the same statement that addresses the notification, it
  queues one delivery for each recipient the notification was actually
  inserted for (a repeat of a `source_key` inserts nothing, so it queues
  nothing) and who holds a channel link in `platform.external_identities`
  (provider `zalo`, the column `SqlZaloLink.zalo_id_for` reads). A link is the
  opt-in. `dw_app` holds no INSERT on the table, so there is no second way to
  create a row.
- **What leaves through the chat** is copied onto the row at that moment: the
  notification's `title` and `link`, never its `body` (a
  short title and a link back to the portal, which shows the rest after
  sign-in). `platform.notifications` is never rewritten by the application
  (`dw_app` updates `read_at` alone), so the copy is a stamp, not a second
  owner that could drift; it also spares the lane reading another person's
  inbox, which that table's RLS narrows to `app.user_id`.
- **Idempotent.** UNIQUE `(tenant_id, channel, source_key,
  recipient_user_id)`, inserted `ON CONFLICT DO NOTHING`.
- **Updated in place**, so an ordinary table and not partitioned (ADR 0006).
  `dw_app` may update the delivery's own state (`status`, `attempts`,
  `next_attempt_at`, `external_message_id`, `last_error`) and nothing else, and
  may not delete: rows go through `platform.prune_channel_deliveries()` (90
  days, never a `pending` one), by cascade with their workspace, tenant or
  user, or through offboarding's purge of the workspace.
- **Workspace RLS**, ENABLEd and FORCEd, the one shape `CLAUDE.md` allows:
  `tenant AND (workspace OR app.workspace_scope = 'tenant')`.
- **`platform.channel_delivery_scopes_due(channel)`**, SECURITY DEFINER: the
  one read that crosses tenants, so the lane knows whom to visit. It returns
  tenant and workspace ids only (the workspace because the table is narrowed
  by it); every read after it runs under that scope's RLS.
- **`updated_at`** by `platform.touch_updated_at()`.

**Twin.** This change came back from the first product, which had already
shipped it as its own revision `4a865a1c97aa` (same names for every table,
constraint, index, policy and function, same grants). A product that merges
this platform carries both, on two branches alembic may run in either order,
so every statement here is idempotent: tables and indexes are created only if
missing, a policy only if none of that name exists on its table, functions and
triggers are created or replaced with the same body, and ENABLE/FORCE and the
REVOKE/GRANT pairs land on the same state however often they run. Downgrade is
a no-op while the twin is still applied, because the objects are then the
twin's too; whichever of the pair is downgraded last removes them. Where the
twin does not exist (this repository) the check never matches.
"""

from __future__ import annotations

from alembic import context, op
from alembic.script import ScriptDirectory

revision = "5a25154e0296"
down_revision = "9f2becb1bf80"
branch_labels = None
depends_on = None

# The product revision that shipped the same change first (see docstring).
_TWIN = "4a865a1c97aa"

_TENANT = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
_WORKSPACE = "workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
_SCOPE = "current_setting('app.workspace_scope', true) = 'tenant'"
_POLICY = f"({_TENANT} AND ({_WORKSPACE} OR {_SCOPE}))"

# The guard both bodies share: delivered inside the bound tenant, to a
# workspace of it, only to its members (unchanged from 855ae928c3fa).
_DELIVER_HEAD = """
        CREATE OR REPLACE FUNCTION platform.deliver_notification(
            p_workspace_id uuid,
            p_recipients uuid[],
            p_source_key text,
            p_title text,
            p_body text,
            p_link text
        )
        RETURNS integer
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
        DECLARE
            v_tenant uuid := NULLIF(current_setting('app.tenant_id', true), '')::uuid;
            v_delivered integer;
        BEGIN
            IF v_tenant IS NULL OR NOT EXISTS (
                SELECT 1 FROM platform.workspaces
                WHERE id = p_workspace_id AND tenant_id = v_tenant
            ) THEN
                RAISE EXCEPTION 'notifications are delivered inside the bound tenant'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
"""

_DELIVER_WITH_CHANNELS = (
    _DELIVER_HEAD
    + """
            WITH delivered AS (
                INSERT INTO platform.notifications (
                    id, tenant_id, workspace_id, recipient_user_id, source_key, title, body, link
                )
                SELECT gen_random_uuid(), v_tenant, p_workspace_id, r.user_id, p_source_key,
                       p_title, p_body, p_link
                FROM (SELECT DISTINCT unnest(p_recipients) AS user_id) r
                WHERE EXISTS (
                    SELECT 1 FROM platform.memberships m
                    WHERE m.tenant_id = v_tenant
                      AND m.workspace_id = p_workspace_id
                      AND m.user_id = r.user_id
                )
                ON CONFLICT ON CONSTRAINT uq_notifications_recipient_user_id_source_key
                    DO NOTHING
                RETURNING recipient_user_id
            ), queued AS (
                INSERT INTO platform.channel_deliveries (
                    id, tenant_id, workspace_id, recipient_user_id, channel, source_key,
                    title, link
                )
                SELECT gen_random_uuid(), v_tenant, p_workspace_id, d.recipient_user_id,
                       e.provider, p_source_key, p_title, p_link
                FROM delivered d
                JOIN platform.external_identities e
                  ON e.user_id = d.recipient_user_id AND e.provider IN ('zalo')
                ON CONFLICT ON CONSTRAINT
                    uq_channel_deliveries_tenant_id_channel_source_key_recipient
                    DO NOTHING
            )
            SELECT count(*) INTO v_delivered FROM delivered;
            RETURN v_delivered;
        END
        $$
        """
)

_DELIVER_INBOX_ONLY = (
    _DELIVER_HEAD
    + """
            INSERT INTO platform.notifications (
                id, tenant_id, workspace_id, recipient_user_id, source_key, title, body, link
            )
            SELECT gen_random_uuid(), v_tenant, p_workspace_id, r.user_id, p_source_key,
                   p_title, p_body, p_link
            FROM (SELECT DISTINCT unnest(p_recipients) AS user_id) r
            WHERE EXISTS (
                SELECT 1 FROM platform.memberships m
                WHERE m.tenant_id = v_tenant
                  AND m.workspace_id = p_workspace_id
                  AND m.user_id = r.user_id
            )
            ON CONFLICT ON CONSTRAINT uq_notifications_recipient_user_id_source_key DO NOTHING;
            GET DIAGNOSTICS v_delivered = ROW_COUNT;
            RETURN v_delivered;
        END
        $$
        """
)


def _twin_applied() -> bool:
    script = ScriptDirectory.from_config(context.config)
    heads = op.get_context().get_current_heads()
    return any(
        rev.revision == _TWIN for head in heads for rev in script.iterate_revisions(head, "base")
    )


def _policy(table: str, name: str, ddl: str) -> str:
    """CREATE POLICY has no IF NOT EXISTS; the twin may have made it already."""
    return f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE schemaname = 'platform' AND tablename = '{table}' AND policyname = '{name}'
            ) THEN
                {ddl};
            END IF;
        END
        $$
        """


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.channel_deliveries (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            recipient_user_id uuid NOT NULL,
            channel text NOT NULL,
            source_key text NOT NULL,
            title text NOT NULL,
            link text,
            status text DEFAULT 'pending' NOT NULL,
            attempts integer DEFAULT 0 NOT NULL,
            next_attempt_at timestamp with time zone DEFAULT now() NOT NULL,
            external_message_id text,
            last_error text,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_channel_deliveries PRIMARY KEY (id),
            CONSTRAINT uq_channel_deliveries_tenant_id_channel_source_key_recipient
                UNIQUE (tenant_id, channel, source_key, recipient_user_id),
            CONSTRAINT fk_channel_deliveries_tenant_id_tenants FOREIGN KEY (tenant_id)
                REFERENCES platform.tenants (id) ON DELETE CASCADE,
            CONSTRAINT fk_channel_deliveries_workspace_id_workspaces FOREIGN KEY (workspace_id)
                REFERENCES platform.workspaces (id) ON DELETE CASCADE,
            CONSTRAINT fk_channel_deliveries_recipient_user_id_users
                FOREIGN KEY (recipient_user_id)
                REFERENCES platform.users (id) ON DELETE CASCADE,
            CONSTRAINT ck_channel_deliveries_channel CHECK (channel IN ('zalo')),
            CONSTRAINT ck_channel_deliveries_status CHECK (
                status IN ('pending', 'sent', 'failed', 'cancelled')
            ),
            CONSTRAINT ck_channel_deliveries_attempts CHECK (attempts >= 0),
            CONSTRAINT ck_channel_deliveries_source_key CHECK (btrim(source_key) <> ''),
            CONSTRAINT ck_channel_deliveries_title CHECK (
                btrim(title) <> '' AND char_length(title) <= 200
            ),
            CONSTRAINT ck_channel_deliveries_link CHECK (
                link IS NULL OR link ~ '^/([A-Za-z0-9_-][A-Za-z0-9/_.?=&-]*)?$'
            ),
            CONSTRAINT ck_channel_deliveries_last_error CHECK (char_length(last_error) <= 500),
            CONSTRAINT ck_channel_deliveries_sent CHECK (
                status <> 'sent' OR external_message_id IS NOT NULL
            )
        )
        """
    )
    # The lane's claim: due rows of one scope, oldest first. Leads with the
    # columns RLS supplies; partial, so finished rows cost the claim nothing.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_channel_deliveries_due"
        " ON platform.channel_deliveries (tenant_id, workspace_id, channel, next_attempt_at)"
        " WHERE status = 'pending'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_channel_deliveries_workspace_id"
        " ON platform.channel_deliveries (workspace_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_channel_deliveries_recipient_user_id"
        " ON platform.channel_deliveries (recipient_user_id)"
    )
    # The pruning sweep deletes by age.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_channel_deliveries_created_at"
        " ON platform.channel_deliveries (created_at)"
    )
    op.execute(
        "CREATE OR REPLACE TRIGGER touch_updated_at BEFORE UPDATE ON platform.channel_deliveries"
        " FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at()"
    )
    op.execute("ALTER TABLE platform.channel_deliveries ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE platform.channel_deliveries FORCE ROW LEVEL SECURITY")
    op.execute(
        _policy(
            "channel_deliveries",
            "tenant_isolation_channel_deliveries",
            "CREATE POLICY tenant_isolation_channel_deliveries ON platform.channel_deliveries"
            f" USING {_POLICY} WITH CHECK {_POLICY}",
        )
    )

    op.execute(_DELIVER_WITH_CHANNELS)

    op.execute(
        """
        CREATE OR REPLACE FUNCTION platform.channel_delivery_scopes_due(p_channel text)
        RETURNS TABLE (tenant_id uuid, workspace_id uuid)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
            SELECT DISTINCT d.tenant_id, d.workspace_id
            FROM platform.channel_deliveries d
            WHERE d.status = 'pending'
              AND d.channel = p_channel
              AND d.next_attempt_at <= now()
            ORDER BY d.tenant_id, d.workspace_id
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION platform.prune_channel_deliveries()
        RETURNS integer
        LANGUAGE sql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
            WITH gone AS (
                DELETE FROM platform.channel_deliveries
                WHERE created_at < now() - interval '90 days'
                  AND status <> 'pending'
                RETURNING 1
            )
            SELECT count(*)::integer FROM gone
        $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION platform.channel_delivery_scopes_due(text) FROM PUBLIC"
    )
    op.execute("REVOKE ALL ON FUNCTION platform.prune_channel_deliveries() FROM PUBLIC")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                REVOKE ALL ON platform.channel_deliveries FROM dw_app;
                GRANT SELECT ON platform.channel_deliveries TO dw_app;
                GRANT UPDATE (status, attempts, next_attempt_at, external_message_id, last_error)
                    ON platform.channel_deliveries TO dw_app;
                GRANT EXECUTE ON FUNCTION platform.channel_delivery_scopes_due(text) TO dw_app;
                GRANT EXECUTE ON FUNCTION platform.prune_channel_deliveries() TO dw_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    if _twin_applied():
        return
    op.execute(_DELIVER_INBOX_ONLY)
    op.execute("DROP FUNCTION platform.prune_channel_deliveries()")
    op.execute("DROP FUNCTION platform.channel_delivery_scopes_due(text)")
    op.execute("DROP TABLE platform.channel_deliveries")
