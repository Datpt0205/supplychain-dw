"""platform channel deliveries pending expiry

Revision ID: 983b509c3f0f
Revises: f381f1694395
Create Date: 2026-10-08

`platform.deliver_notification` queues a delivery for every linked recipient
whether or not any host sends (ADR 0006), and the prune never removes a
`pending` row. A deployment whose bot token is removed therefore keeps every
queued row pending forever: no lane claims it, nothing fails it, and the
recipient's notice is neither sent nor said to be unsent.

`platform.expire_pending_channel_deliveries(p_older_than, p_actor, p_actor_label)`
fails every row still `pending` whose `created_at` is older than
`p_older_than`, with `last_error = 'channel_unconfigured'`, and writes one
`channel_delivery.failed` audit row per row in that row's own tenant, in the
same statement. The term is the retention policy's
(`channel_deliveries.pending_expiry_days`), passed by the worker's
`channel_deliveries_retention` lane together with its system actor (ADR 0011).
A sending lane settles a row within hours (five attempts, backoff from one
minute), so a row this old was never going to be sent.

SECURITY DEFINER because it crosses tenants, like `prune_channel_deliveries`;
EXECUTE is revoked from PUBLIC and granted to `dw_app` only. Idempotent:
`CREATE OR REPLACE` and REVOKE/GRANT land on the same state however often they
run.
"""

from __future__ import annotations

from alembic import op

revision = "983b509c3f0f"
down_revision = "f381f1694395"
branch_labels = None
depends_on = None

_SIGNATURE = "platform.expire_pending_channel_deliveries(interval, uuid, text)"


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION platform.expire_pending_channel_deliveries(
            p_older_than interval, p_actor uuid, p_actor_label text
        )
        RETURNS integer
        LANGUAGE sql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
            WITH expired AS (
                UPDATE platform.channel_deliveries
                SET status = 'failed', last_error = 'channel_unconfigured'
                WHERE status = 'pending'
                  AND created_at < now() - p_older_than
                RETURNING id, tenant_id, workspace_id, channel, attempts, recipient_user_id
            ),
            audited AS (
                INSERT INTO platform.audit_events
                    (id, tenant_id, workspace_id, actor_id, action, resource_type,
                     resource_id, details, occurred_at)
                SELECT gen_random_uuid(), e.tenant_id, e.workspace_id, p_actor,
                       'channel_delivery.failed', 'channel_delivery', e.id::text,
                       jsonb_build_object(
                           'channel', e.channel,
                           'attempts', e.attempts,
                           'recipient_user_id', e.recipient_user_id::text,
                           'reason', 'channel_unconfigured',
                           'actor', p_actor_label
                       ),
                       now()
                FROM expired e
                RETURNING 1
            )
            SELECT count(*)::integer FROM audited
        $$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION {_SIGNATURE} FROM PUBLIC")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                GRANT EXECUTE ON FUNCTION {_SIGNATURE} TO dw_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {_SIGNATURE}")
