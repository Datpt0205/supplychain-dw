# Chat channels (Zalo first)

A two-way chat channel for staff: link a chat, receive notifications in it,
send a few commands, decide an approval after viewing it on the portal. Built
in the first product (Elmich supply chain, `feat/elmich-a-d-s1`) and
upstreamed here without its product commands, on 2026-10-07. The slice ids
are the product's, so its commit messages and this file name the same work.

Decisions: ADR 0005 (the channel, the link, inbound commands), and the ADRs
each slice below names.

| Slice | What it is                                                                                                                                                                                                                                                                               | Platform commit |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------- |
| Z1    | Link with a single-use `/start` token (`channel_link_nonces`), `/zalo/status`, `/connect`, `/disconnect`, the `zalo_link_poll` lane, link changes audited and announced in the app                                                                                                       | `dfa0f39`       |
| Z4a   | Inbound foundation: `InboundRouter`, `ChannelCommandRegistry` (empty), message-id dedupe and its retention lane, `find_linked_access` (no role, ceiling), `/zalo/workspace`                                                                                                              | `dfa0f39`       |
| Z2    | Notifications out through a linked chat: `channel_deliveries` queued by `deliver_notification`, the `channel_delivery` lane (SKIP LOCKED, link and membership re-checked, 1/2/4/8 min backoff, fail at 5), prune lane; ADR 0006                                                          | `8f1928f`       |
| Z5    | Decide in a chat after a portal view: view receipts, single-use 6-digit HMAC codes (10 min, lock at 5), `decide(channel=, admission=)`, the `DUYỆT`/`KHÔNG` command builder for a context's runner, `/approvals/[id]` with "Lấy mã"; ADR 0007                                            | `3da0138`       |
| Z3    | Webhook for hosted deployments: `POST /zalo/webhook` under `ZALO_UPDATES_MODE=webhook` (header secret, 64 KB, schema), `channel_inbound_updates` queue, `zalo_webhook_drain` lane, mode guard, https and 32-character secret required when deployed, `scripts/zalo_webhook.py`; ADR 0008 | `0231c1a`       |

Upstreamed in the same pass, outside this area: the shared model-stack
builder and `DailyAllowance` (`dc40a33`, ADR 0005 amendment), the Caddy host
overlay and https-when-deployed (`f1a260c`, ADR 0009), the eval grader seam
(`5643634`), the RLS catalog-query fence (`ca369f2`). Migrations
`02930a73bbdf`, `9f2becb1bf80`, `5a25154e0296`, `e399be8c0a2d` and
`395acbcad324` are idempotent twins of the product's `cf66605631d7`,
`988592a8100f`, `4a865a1c97aa`, `dbb8c3359981` and `8728e2fac660`: run on a
scratch database after the product's DDL they apply cleanly and leave the
schema, policies and grants identical to the platform chain alone.

## Not here, and why

- **The settings page** ("Kết nối Zalo", the workspace select): the platform
  web has no `/settings` page, so the link and the workspace choice are API
  only (`/api/v1/zalo/*`, typed in `@dw/api-client`). A product with a
  settings page renders them; the first product's page is its own design.
  The bot still answers a person with several workspaces and no choice with
  `<DW_PUBLIC_WEB_URL>/settings`, a page this web does not have: a product
  provides it, or that sentence changes when the platform gets one.
- **The decide command in this worker:** the platform worker hosts no graph,
  so it has no runner to resume a decided run on. A context that hosts its
  graph in the worker builds the command with `build_channel_decision_command`
  over its runner and passes it to `build_channel_commands`.
- **The first product's runbook** (`docs/deploy/host.md`): it names that
  product's overrides; ADR 0009 carries the platform's short version.
- **Chat commands** (a proposal, read-only questions): product commands,
  registered at the product's composition root.

## Owed

- A live run against a real bot: the message id field, the webhook's secret
  header and the 400/403/404 "unreachable" classification are read as Zalo
  documents them (Telegram's dialect) and have not been measured.
- A hosted run of the Caddy overlay on this repo's stack (verified here with
  `docker compose config` and `caddy validate` only).
