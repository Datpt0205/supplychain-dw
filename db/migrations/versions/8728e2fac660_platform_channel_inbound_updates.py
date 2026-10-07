"""platform channel inbound updates

Revision ID: 8728e2fac660
Revises: 992e7c6ae4fe
Create Date: 2026-10-07 13:47:47.839564+00:00

The Zalo webhook (zalo-channel ticket 03, ADR 0015 amendment Z3): the API
answers Zalo at once and leaves the work to the worker, which already hosts the
one ``ZaloInbound`` the poll lane runs (commands, model, the review runner a
decision resumes on). This table is the hand-over between them.

`platform.channel_inbound_updates` — one row per update the API accepted
(secret checked, at most 64 KB, a bot update by schema), as it was POSTed.

- **Identity plane, no RLS**, like `channel_inbound_messages`: the update is
  queued before anything resolves a chat to a person, let alone a tenant.
- **Taken by deleting.** The worker's `zalo_webhook_drain` lane deletes a batch
  and returns it in one `DELETE ... RETURNING`, so two drains never share an
  update (no `FOR UPDATE`: a row lock needs an UPDATE grant this table must
  not give). Taken means acknowledged, as `getUpdates` is on the poll path; the
  router's claim in `channel_inbound_messages` is what makes a redelivered
  message act once.
- **Grants ship here.** `dw_app` inserts (API), reads and deletes (worker);
  nothing updates a queued update. `test_privileges.py` asserts it.
- **Bounded.** A drained row is gone at once; one the worker never took (the
  lane was down) holds a person's text, so the `channel_inbound_messages_retention`
  lane deletes it after `INBOUND_UPDATE_RETENTION` (failure-modes #6).
"""

from __future__ import annotations

from alembic import op

revision = "8728e2fac660"
down_revision = "992e7c6ae4fe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE platform.channel_inbound_updates (
            id uuid NOT NULL,
            channel text NOT NULL,
            payload jsonb NOT NULL,
            received_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_channel_inbound_updates PRIMARY KEY (id),
            CONSTRAINT ck_channel_inbound_updates_channel CHECK (channel IN ('zalo')),
            CONSTRAINT ck_channel_inbound_updates_payload CHECK (
                jsonb_typeof(payload) = 'object'
            )
        )
        """
    )
    # The drain takes oldest first per channel; the retention sweep deletes by age.
    op.execute(
        "CREATE INDEX ix_channel_inbound_updates_channel_received_at"
        " ON platform.channel_inbound_updates (channel, received_at)"
    )
    op.execute(
        "CREATE INDEX ix_channel_inbound_updates_received_at"
        " ON platform.channel_inbound_updates (received_at)"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                REVOKE ALL ON platform.channel_inbound_updates FROM dw_app;
                GRANT SELECT, INSERT, DELETE ON platform.channel_inbound_updates TO dw_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.channel_inbound_updates")
