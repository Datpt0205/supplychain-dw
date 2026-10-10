"""platform channel link nonces

Revision ID: cf66605631d7
Revises: bc3f0c1279fd
Create Date: 2026-10-05 08:14:15.738563+00:00

A person links their own chat (Zalo first) by sending the bot a signed
`/start <token>`. The signature proves the app minted the token for that user;
it cannot prove the token was not used already, and a token is easy to see over
someone's shoulder. So each token carries a `jti`, written here when the token
is issued and consumed by ONE conditional UPDATE in the transaction that writes
the link:

    UPDATE platform.channel_link_nonces SET used_at = now()
     WHERE jti = :jti AND user_id = :uid AND used_at IS NULL AND expires_at > now()
    RETURNING jti

Zero rows means refused: used, expired, or never issued. Not read-then-update,
under which two `/start` with the same token in flight would both pass.

- **Identity plane, no RLS.** Like `users` and `external_identities`: a link
  belongs to the person, not to one of their workspaces (ADR 0012), so the row
  has no tenant to narrow by. What stops one user touching another's nonce is
  that every statement names the `jti` — 64 random bits behind an HMAC only the
  server can produce — together with the user it was minted for.
- **Grants ship here.** `dw_app` issues (INSERT), consumes (UPDATE of `used_at`
  only, plus the SELECT its WHERE and RETURNING need) and prunes (DELETE). It cannot move a nonce to another user or extend
  its expiry; no other role is granted anything. `test_privileges.py` asserts it.
- **Bounded.** The worker's `channel_link_nonces_retention` lane deletes rows
  expired for more than a day (failure-modes #6).
- **One chat per user, per channel.** `external_identities` already refuses one
  chat for two users (`UNIQUE (issuer, subject)`); the partial unique index below
  refuses two chats for one user, so two links racing for one person cannot both
  land. The link store clears both sides first, so a relink never trips either.

**Twin.** The platform took this change back as its own revision `02930a73bbdf`
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

revision = "cf66605631d7"
down_revision = "bc3f0c1279fd"
branch_labels = None
depends_on = None

# The platform revision that carries the same change (see docstring).
_TWIN = "02930a73bbdf"


def _twin_applied() -> bool:
    script = ScriptDirectory.from_config(context.config)
    heads = op.get_context().get_current_heads()
    return any(
        rev.revision == _TWIN for head in heads for rev in script.iterate_revisions(head, "base")
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.channel_link_nonces (
            jti text NOT NULL,
            channel text NOT NULL,
            user_id uuid NOT NULL,
            expires_at timestamp with time zone NOT NULL,
            used_at timestamp with time zone,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_channel_link_nonces PRIMARY KEY (jti),
            CONSTRAINT fk_channel_link_nonces_user_id_users FOREIGN KEY (user_id)
                REFERENCES platform.users (id) ON DELETE CASCADE,
            CONSTRAINT ck_channel_link_nonces_channel CHECK (channel IN ('zalo')),
            CONSTRAINT ck_channel_link_nonces_jti CHECK (jti ~ '^[0-9a-f]{16}$'),
            CONSTRAINT ck_channel_link_nonces_used_after_created CHECK (
                used_at IS NULL OR used_at >= created_at
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_channel_link_nonces_user_id"
        " ON platform.channel_link_nonces (user_id)"
    )
    # The retention sweep deletes by expiry.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_channel_link_nonces_expires_at"
        " ON platform.channel_link_nonces (expires_at)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_external_identities_user_id_provider"
        " ON platform.external_identities (user_id, provider) WHERE provider IN ('zalo')"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                -- Default privileges handed dw_app UPDATE on every column; a
                -- nonce may only ever be marked used.
                REVOKE ALL ON platform.channel_link_nonces FROM dw_app;
                GRANT SELECT, INSERT, DELETE ON platform.channel_link_nonces TO dw_app;
                GRANT UPDATE (used_at) ON platform.channel_link_nonces TO dw_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    if _twin_applied():
        return
    op.execute("DROP INDEX IF EXISTS platform.uq_external_identities_user_id_provider")
    op.execute("DROP TABLE IF EXISTS platform.channel_link_nonces")
