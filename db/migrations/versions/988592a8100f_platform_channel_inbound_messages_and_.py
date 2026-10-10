"""platform channel inbound messages and channel preferences

Revision ID: 988592a8100f
Revises: 57ca5f1df964
Create Date: 2026-10-06 20:09:21.649647+00:00

The foundation for commands a linked person sends through a chat (zalo-channel
ticket 04, part Z4a). Two tables, both keyed by the person rather than by one of
their workspaces, because the bot reads both before it knows a tenant.

`platform.channel_inbound_messages` — one row per inbound message id, written
with `INSERT ... ON CONFLICT DO NOTHING` and committed as `processing` BEFORE the
message is acted on; zero rows inserted means the id was seen already and the
message is skipped. The poll lane's `getUpdates` acknowledges on read, and the
webhook (Z3) may deliver the same update again, so the id is the only thing that
makes "act once" hold across both. After acting, `outcome` becomes `done`,
`ignored` or `failed`; an id is never processed a second time, whatever its
outcome.

- **Identity plane, no RLS**, like `external_identities` and
  `channel_link_nonces`: no tenant column, because the dedupe happens before a
  tenant is resolved. Every statement names `(channel, external_message_id)`.
- **Grants ship here.** `dw_app` inserts, reads, updates `outcome` alone and
  deletes (the retention lane); `test_privileges.py` asserts it.
- **Bounded.** The worker's `channel_inbound_messages_retention` lane deletes
  rows older than seven days (failure-modes #6).

`platform.channel_preferences` — the workspace a person chose on `/settings`
for their chat commands ("Workspace dùng cho Zalo"), one row per person.

- **FK to the membership**, `(tenant_id, workspace_id, user_id)` against
  `uq_memberships_scope_user`, `ON DELETE CASCADE`: the choice can only name a
  workspace the person belongs to, and it disappears with the membership
  (offboarding included; an FK action is not subject to RLS).
- **RLS by principal, not by tenant.** ENABLE and FORCE, one policy for every
  command reading `app.principal_id` on USING and WITH CHECK: the row belongs to
  the person, and the bot reads it before any tenant is known — the same
  setting, set per transaction, that the baseline's `memberships_self_select`
  uses for "my memberships". No SECURITY DEFINER function. The policy is not
  named `tenant_isolation_*`, so the offboarding export does not pick the row
  up; the purge removes it through the membership's cascade.

**Twin.** The platform took this change back as its own revision `9f2becb1bf80`
(same names, same grants), which merging `platform/main` brings here on a
second branch alembic may run before or after this one. So every statement is
idempotent, written as the platform's copy writes it (objects created only if
missing, functions and triggers `CREATE OR REPLACE`, the REVOKE/GRANT pair
landing on the same state however often it runs), and downgrade is a no-op
while the twin is still applied: whichever of the pair is downgraded last
removes the objects. The platform revision does the same, mirrored.
"""

from __future__ import annotations

from alembic import context, op
from alembic.script import ScriptDirectory

revision = "988592a8100f"
down_revision = "57ca5f1df964"
branch_labels = None
depends_on = None

# The platform revision that carries the same change (see docstring).
_TWIN = "9f2becb1bf80"

_PRINCIPAL = "user_id = NULLIF(current_setting('app.principal_id', true), '')::uuid"


def _twin_applied() -> bool:
    script = ScriptDirectory.from_config(context.config)
    heads = op.get_context().get_current_heads()
    return any(
        rev.revision == _TWIN for head in heads for rev in script.iterate_revisions(head, "base")
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.channel_inbound_messages (
            channel text NOT NULL,
            external_message_id text NOT NULL,
            user_id uuid NOT NULL,
            received_at timestamp with time zone DEFAULT now() NOT NULL,
            outcome text DEFAULT 'processing' NOT NULL,
            CONSTRAINT pk_channel_inbound_messages PRIMARY KEY (channel, external_message_id),
            CONSTRAINT fk_channel_inbound_messages_user_id_users FOREIGN KEY (user_id)
                REFERENCES platform.users (id) ON DELETE CASCADE,
            CONSTRAINT ck_channel_inbound_messages_channel CHECK (channel IN ('zalo')),
            CONSTRAINT ck_channel_inbound_messages_outcome CHECK (
                outcome IN ('processing', 'done', 'failed', 'ignored')
            ),
            CONSTRAINT ck_channel_inbound_messages_external_message_id CHECK (
                length(external_message_id) BETWEEN 1 AND 128
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_channel_inbound_messages_user_id"
        " ON platform.channel_inbound_messages (user_id)"
    )
    # The retention sweep deletes by age.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_channel_inbound_messages_received_at"
        " ON platform.channel_inbound_messages (received_at)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.channel_preferences (
            user_id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            CONSTRAINT pk_channel_preferences PRIMARY KEY (user_id),
            CONSTRAINT fk_channel_preferences_tenant_id_memberships
                FOREIGN KEY (tenant_id, workspace_id, user_id)
                REFERENCES platform.memberships (tenant_id, workspace_id, user_id)
                ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_channel_preferences_tenant_id_workspace_id_user_id"
        " ON platform.channel_preferences (tenant_id, workspace_id, user_id)"
    )
    op.execute("ALTER TABLE platform.channel_preferences ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE platform.channel_preferences FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE schemaname = 'platform'
                  AND tablename = 'channel_preferences'
                  AND policyname = 'channel_preferences_self'
            ) THEN
                CREATE POLICY channel_preferences_self ON platform.channel_preferences
                    USING ({_PRINCIPAL})
                    WITH CHECK ({_PRINCIPAL});
            END IF;
        END
        $$
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                -- Default privileges handed dw_app UPDATE on every column; a
                -- message's only later change is its outcome.
                REVOKE ALL ON platform.channel_inbound_messages FROM dw_app;
                GRANT SELECT, INSERT, DELETE ON platform.channel_inbound_messages TO dw_app;
                GRANT UPDATE (outcome) ON platform.channel_inbound_messages TO dw_app;
                -- A choice is made and changed, never deleted by the
                -- application: it goes with the membership, by cascade.
                REVOKE ALL ON platform.channel_preferences FROM dw_app;
                GRANT SELECT, INSERT ON platform.channel_preferences TO dw_app;
                GRANT UPDATE (tenant_id, workspace_id) ON platform.channel_preferences TO dw_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    if _twin_applied():
        return
    op.execute("DROP TABLE IF EXISTS platform.channel_preferences")
    op.execute("DROP TABLE IF EXISTS platform.channel_inbound_messages")
