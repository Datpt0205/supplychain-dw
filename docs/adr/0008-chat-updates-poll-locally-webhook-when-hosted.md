---
status: Accepted
date: 2026-10-07
source:
    - ../../apps/api/src/dw_api/routes/v1/zalo.py # the webhook route
    - ../../apps/api/src/dw_api/settings.py # zalo_updates_mode, validate_for_profile
    - ../../apps/worker/src/dw_worker/consumers/zalo_poll.py
    - ../../apps/worker/src/dw_worker/consumers/zalo_webhook.py
    - ../../db/migrations/versions/395acbcad324_platform_channel_inbound_updates.py
    - ../../scripts/zalo_webhook.py
---

# 0008. Chat updates arrive by polling on a developer machine and by webhook when hosted; one bot has one reader

Accepted in the first product built on the platform (its ADR 0015, 2026-10-05,
amended 2026-10-07), and upstreamed from there.

## Decision

`ZALO_UPDATES_MODE=poll|webhook`, read by both the API and the worker, so one
value decides both processes. The two are exclusive on one bot (`getUpdates`
works only after `deleteWebhook`), and both feed the same `ZaloInbound.handle`
that `build_zalo_inbound` builds in the worker.

- **`poll`** (the default): the worker's `zalo_link_poll` lane long-polls; no
  public host is needed. `getUpdates` acknowledges on read, so an update whose
  handling fails is lost and the person sends it again. The API mounts no
  webhook.
- **`webhook`**: the API mounts `POST /api/v1/zalo/webhook` only with
  `ZALO_WEBHOOK_SECRET` set.
    - **The secret travels in a header, never the path:** Zalo returns the
      `secret_token` given to `setWebhook` in `X-Bot-Api-Secret-Token`, compared
      with `hmac.compare_digest` before the body is read. A secret in the path
      would sit in every access log on the way. Missing or wrong: 403, nothing
      queued. Not yet measured against Zalo; if it never sends the header,
      every call is refused and the deployment stays on poll, the safe way to
      be wrong.
    - **The API only queues:** at most 64 KB by bytes read (413), the bot-update
      envelope by schema (422, no echo), stored unchanged in
      `platform.channel_inbound_updates` (identity plane, no RLS, `dw_app`
      SELECT/INSERT/DELETE) and answered 200. The worker's `zalo_webhook_drain`
      lane takes a batch with one `DELETE ... RETURNING` and hands each update
      to the same entry the poll lane uses. Commands live at the worker's
      composition root; building them again in the API would be a second
      composition.
    - **Deduplication is the router's**, not the door's: the message-id claim in
      `channel_inbound_messages` makes a redelivered update act once.
    - An update never taken is deleted by the retention lane after a day.
- `scripts/zalo_webhook.py set|delete|info` registers the URL built from
  `DW_API_PUBLIC_BASE_URL` with the secret as `secret_token`, prints neither,
  and refuses `set` outside webhook mode.

## Deployed constraints

`ApiSettings.validate_for_profile` refuses to start a deployed profile in
webhook mode with a secret shorter than 32 characters or a
`DW_API_PUBLIC_BASE_URL` that is not `https://`. A CDN in front of the API must
let Zalo's "Java" user agent through; if it cannot, stay on poll.

## Alternatives considered

- **Webhook only.** Cannot run on a developer machine before a domain exists.
- **Poll only, even when hosted.** Works, but an update is lost when the worker
  fails at that moment, and several workers polling one bot steal updates from
  each other. Kept as the fallback.

## Consequences

- Exactly one worker may run the poll lane; with more replicas, turn it off on
  the others.
