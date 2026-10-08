---
status: Accepted
date: 2026-10-07
source:
    - ../../db/migrations/versions/5a25154e0296_platform_channel_deliveries.py
    - ../../packages/python/dw_platform/src/dw_platform/adapters/persistence/channel_deliveries.py # SqlChannelOutbox, SqlChannelDeliveryRetention
    - ../../apps/worker/src/dw_worker/consumers/channel_delivery.py # the lane, compose, backoff
    - ../../packages/python/dw_connectors/src/dw_connectors/ports.py # ChatSenderPort, ChatRecipientUnreachableError
---

# 0006. Notifications reach a chat channel through a provider-neutral outbox, `platform.channel_deliveries`

Accepted in the first product built on the platform (its ADR 0013, 2026-10-05,
amended 2026-10-07), and upstreamed from there.

## Context

A notification reached only the in-app inbox (`platform.deliver_notification`,
idempotent by `source_key`). Sending it to a chat is a side effect, so it needs
idempotency, retries and audit, and no context should send to a provider
itself.

## Decision

One platform table, one row per notification, recipient and channel:
`tenant_id`, `workspace_id`, `recipient_user_id`, `channel` (CHECK `'zalo'`),
`source_key`, `title`, `link`, `status` (`pending | sent | failed |
cancelled`), `attempts`, `next_attempt_at`, `external_message_id`,
`last_error`; UNIQUE `(tenant_id, channel, source_key, recipient_user_id)`.

- **One door in.** `platform.deliver_notification` queues a row in the same
  statement that inserts the notification, only for a recipient the notice was
  actually inserted for and who holds a channel link. A link is the opt-in.
  `dw_app` holds no INSERT and no DELETE on the table: the grant is the door.
- **The row carries `title` and `link`, never `body`.** The notification is
  never rewritten by the application, so the copy is a stamp taken at creation,
  not a second owner; the lane never reads another person's inbox. The message
  is the tenant and workspace names, the title and an absolute link to the
  portal: no decision code, no secret, nothing the portal would hide.
- **One transaction per send,** bound to the row's tenant and workspace (the
  table is narrowed by both), claiming one row `FOR UPDATE SKIP LOCKED`. The one
  cross-tenant read is `platform.channel_delivery_scopes_due(channel)`, SECURITY
  DEFINER, returning (tenant, workspace) pairs only.
- **Re-checked at send time:** the link, the membership in the workspace and an
  `active` tenant; any missing cancels the row with the reason.
- **Errors:** `ChatRecipientUnreachableError` (`dw_connectors.ports`) is
  permanent and fails the row after one attempt; for Zalo, HTTP or `error_code`
  400/403/404, inferred from the Telegram dialect and not yet measured. Anything
  else retries after 1, 2, 4, 8 minutes and fails at the fifth attempt. One
  audit row per outcome.
- **Ordinary table, not partitioned:** rows are updated in place, and
  `ensure_time_partitions` knows only the tenant policy shape. Workspace RLS,
  ENABLEd and FORCEd, in the shape `CLAUDE.md` requires.
- **Bounded:** `platform.prune_channel_deliveries()` deletes rows older than 90
  days and never a `pending` one, from its own `channel_deliveries_retention`
  lane.

## Alternatives considered

- **Send right after commit, store nothing.** No state, no retry, no audit.
- **Each context sends on its own.** One Zalo sender per context.

## Consequences

- **At least once, not exactly once.** Zalo takes no idempotency key; a worker
  that dies between the send and the commit sends that message again.
- Another channel (email) is one CHECK value and one adapter, not a new table.
- A context never sends to a chat; it sends a notification, and the
  notification goes out through the channel.

## Amendment 2026-10-08: nothing waits forever

A delivery is queued whether or not any host sends, and the prune never
removes a `pending` row, so with the bot token removed every queued row stayed
pending for good. The same `channel_deliveries_retention` lane now also fails
every row still pending past `channel_deliveries.pending_expiry_days` of the
retention policy (`retention@1.7.0.yaml`, 7 days) with `last_error =
'channel_unconfigured'`, writing one `channel_delivery.failed` audit row per
row in its own tenant, as the lane's system actor (ADR 0011), through
`platform.expire_pending_channel_deliveries` (migration `983b509c3f0f`,
SECURITY DEFINER, EXECUTE for `dw_app` only). Chosen over refusing to enqueue
when no sender is configured: the queue is written by the database function
inside the notification's own statement, which cannot know what a worker host
is configured to do, and a host may be configured later.
