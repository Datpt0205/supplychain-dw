<!-- Survey of 2026-10-05, kept in the repo so ticket citations outlive the session. In the session scratchpad this file was named `platform-auth-portal.md`; its name here matches its content. -->

## Channel integrations in old `dw`, compared with `codebase-main`

### 1. What the old platform has (C:\Users\phung\dw, HEAD 08f7902)

**Zalo: outbound adapters (dw_connectors)**

- `packages/python/dw_connectors/src/dw_connectors/adapters/zalo_bot.py:51-108`: `ZaloBotClient` for Bot Platform `https://bot-api.zaloplatforms.com/bot<token>`.
    - Methods: `get_updates` (lines 56-81), `send_chat_action` "typing" (83-86), `send_message` (88-108).
    - Zalo quirks it handles: an idle poll returns `ok=false` with `error_code 408` (67-70), and `result` is one object rather than a list (76-81).
    - `send_message` uses `_split_for_zalo` (18-48) to cut text into parts of at most 1900 characters on line breaks, because Zalo rejects messages over 2000. **Without that split, a long approval card is silently never delivered.**
- `.../adapters/zalo_approval_notifier.py`: approval cards as plain text with no buttons.
    - `render_text` (161-179) is pure. `_reply_hint` (132-158) gives the reply verb per event type ("duyệt cp1", "xác minh", ...).
    - `ZaloApprovalNotifier.send` (182-193) reuses `SlackApprovalMessage` / `SlackMessageRef`. It sends to `recipient_slack_user_id`, so the Slack names leak into Zalo.
    - Tested by `packages/python/dw_connectors/tests/unit/test_zalo_reply_hints.py:63-89`.
- `.../dw_connectors/ports.py`: only `TaskConnectorPort`. There is no chat port. The notifier protocol is declared ad hoc in the worker (below).

**Zalo: inbound "front office" (API process)**

- `apps/api/src/dw_api/channels/zalo.py`: `ZaloFrontOfficeService`.
    - Runs a long-poll loop with backoff (91-109), started as an asyncio task from `main.py:75-99` via `start_zalo_front_office` (252-264).
    - Identity (112-139): Zalo user id → demo subject, read from `configs/demo/channel_identities.yaml` or the env reverse map, then `demo_users.yaml` → `access_context_factory.build(VerifiedClaims(issuer="dw-zalo"))`. This is a static mapping, dev-only. There is no self-link.
    - When an unknown user writes, the bot replies with their Zalo ID and tells them to edit `.env` (150-159).
    - Decisions typed as words go through `DecisionEngine.try_text` (189-193). Ordinary chat goes through `ConversationIntakeService` (198-214). The case picker "chọn N" (168-177) keeps its state in memory (`_picker_options`), which is lost on restart and not shared across replicas.
- `apps/api/src/dw_api/channels/decisions.py:1-90, 147-237`: `DecisionEngine`, a channel-neutral parser for words or buttons that executes the same handlers the web uses. It is tightly coupled to `dw_tender` and `ApiContainer`.
- Siblings, all with the same demo-roster trust path:
    - `channels/telegram.py` (`TelegramBotService`, polling, lines 38-239)
    - `channels/slack.py` (895 lines)
    - `routes/v1/slack_channel.py:29-68`: an HTTPS webhook with signature verification that ACKs fast and processes async
    - `dw_connectors/adapters/slack_signature.py`

**Approval and notification delivery (worker)**

- `db/migrations/versions/0011_dw01_slack_notifications.py:21-104`: a durable outbox, `tender.approval_notification_jobs`.
    - Has status CHECK, attempts, `idempotency_key` UNIQUE, and RLS on the tenant plus a `app.worker_drain` policy.
    - Its columns are `slack_channel_id` / `slack_message_ts`, so the table is named for Slack.
    - It has hand-picked revision ids "0011", which the current CLAUDE.md forbids.
- `packages/python/dw_tender/src/dw_tender/domain/preparation/notifications.py:14-90`: event types (`intake.*`, `cp.approval_requested`, `run.progress`, `addendum.proposed`, `law.change_detected`, `rework.*`) and the job and delivery types.
- `apps/worker/src/dw_worker/consumers/slack_approvals.py`:
    - `ApprovalNotifierPort` (18-21)
    - a staleness cancel per case state (80-112)
    - a recipient map from subject to provider id (113-119)
    - permanent vs transient failure classification (26-65, 159-172)
    - `mark_sent` (172)
- `apps/worker/src/dw_worker/composition.py:133-158`: chooses Slack or Zalo with `if settings.approval_channel == "zalo"`.

**Email**

- `packages/python/dw_tender/src/dw_tender/adapters/preparation/smtp_publisher.py:18-50`: `SmtpEmailPublisher` (STARTTLS, blocking smtplib called through `to_thread`) and `MockEmailPublisher`, behind `EmailPublisherPort`. Wired at `apps/api/src/dw_api/bootstrap.py:482-491` by reading `os.environ` directly.
- `apps/api/src/dw_api/channels/mailroom.py:1-60`: an IMAP poller for bid replies tagged `[DW01:<case-id>]`. It acts under a fixed `DW_MAILROOM_SUBJECT` identity.

**Env var names (old dw)**

- Zalo: `ZALO_BOT_TOKEN`, `ZALO_USER_AN_ID`, `ZALO_USER_BINH_ID`, `ZALO_USER_CHI_ID`, `ZALO_USER_MAP_JSON`, `DW_ZALO_SHOW_THINKING`, `DW_APPROVAL_CHANNEL`
- Slack and approvals: `SLACK_BOT_TOKEN`, `SLACK_DEFAULT_CHANNEL`, `SLACK_APP_TOKEN`, `SLACK_SIGNING_SECRET`, `SLACK_USER_*_ID`, `SLACK_USER_MAP_JSON`, `DW_SLACK_APPROVALS_ENABLED`, `DW_APPROVAL_REMINDER_SECONDS`, `DW_PUBLIC_WEB_URL`, `DW_TASK_CONNECTOR`
- Telegram: `TELEGRAM_BOT_TOKEN`
- Email: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `IMAP_HOST`, `IMAP_USER`, `IMAP_PASSWORD`, `DW_EMAIL_SUBMISSIONS_ENABLED`, `DW_SUBMISSIONS_MIN_TO_CLOSE`, `DW_MAILROOM_SUBJECT`
- Where they are defined: `apps/api/src/dw_api/settings.py:184-279`, `apps/worker/src/dw_worker/settings.py:68-146`, `.env.example:144-198`.

**No user self-link UI exists in dw's web app.** A grep of `apps/web` for zalo finds nothing.

### 2. What `codebase-main` already has (bf553f4; `codebase`/bidding is the same apart from line endings)

- `packages/python/dw_connectors/src/dw_connectors/ports.py:27-41`: `ChatSenderPort.send_message(conversation_id, text) -> str`.
- `.../adapters/zalo_bot.py:18-79`: `ZaloBotClient`.
    - Has `get_updates`, `send_message`, `set_webhook`, `delete_webhook`, `get_webhook_info`.
    - **Missing compared with dw:** the 1900-character split and `send_chat_action`.
    - Tested in `tests/unit/test_zalo_bot.py`.
- `.../adapters/zalo_link.py`: the self-link flow, tested in `tests/unit/test_zalo_link.py`.
    - An HMAC-signed connect token with a 15-minute TTL (104-126).
    - The `ZaloLinkStore` protocol (129-136).
    - `parse_update`, which handles both poll and webhook shapes (139-149).
    - `handle_update`: `/start <token>` links, `/stop` unlinks (152-178).
- `packages/python/dw_platform/src/dw_platform/adapters/persistence/zalo_link_repo.py:27-86`: `SqlZaloLink` stores the link in `platform.external_identities` with `provider=issuer='zalo'`. Methods: `zalo_id_for`, `link`, `unlink_by_zalo`, `unlink_by_user`.
- `packages/typescript/api-client/src/client.ts:84-97, 829-842`: `getZaloStatus`, `connectZalo`, `disconnectZalo`, calling `/api/v1/zalo/{status,connect,disconnect}`.
- `infra/compose/docker-compose.yml:362-369`: `ZALO_BOT_TOKEN`, `ZALO_WEBHOOK_SECRET`, `ZALO_LINK_SECRET`, `ZALO_BOT_LINK`. The comment there says Zalo is "not wired into any composition root yet".
- Notifications:
    - In-app inbox only: `dw_platform/application/notifications.py:40-65`.
    - `SqlNotificationRepository.deliver` in `adapters/persistence/notifications.py:101-131` is idempotent per `source_key` and goes through `platform.deliver_notification`.
    - Migration `855ae928c3fa`.
    - **There is no outbound channel fan-out.**
- No Slack, Telegram, SMTP or IMAP code. `TELEGRAM_BOT_TOKEN` is in `.env.example:187` and compose, but nothing reads it.

### 3. Reuse verdict

| Piece                                                                                                                                                                                                                                                                                                                                                                          | Verdict                                                                                                                                                                                                                        |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| codebase-main `ZaloBotClient`, `ChatSenderPort`, `zalo_link.py`, `SqlZaloLink`                                                                                                                                                                                                                                                                                                 | **Reuse as is.** This is the platform-owned base for Elmich's Zalo channel.                                                                                                                                                    |
| dw `_split_for_zalo` + `send_chat_action`                                                                                                                                                                                                                                                                                                                                      | **Port into codebase-main's `ZaloBotClient`.** Splitting is a property of the channel and belongs in the adapter.                                                                                                              |
| dw `render_text` / `_reply_hint` pattern                                                                                                                                                                                                                                                                                                                                       | **Reuse the pattern, not the code.** The event types and wording are tender-specific; it should take a channel-neutral card DTO instead of `SlackApprovalMessage`.                                                             |
| dw `approval_notification_jobs` outbox + consumer (claim, staleness cancel, permanent vs transient, `mark_sent`)                                                                                                                                                                                                                                                               | **Reuse the design.** Rebuild it as a platform-level channel outbox: random-hex alembic revision, provider-neutral `external_message_id`, recipient resolved through `SqlZaloLink.zalo_id_for(user_id)` instead of an env map. |
| dw `DecisionEngine` (approve by typed word)                                                                                                                                                                                                                                                                                                                                    | **Concept only.** It is coupled to `dw_tender`. If Elmich needs approval by typing in Zalo, the model→typed-intent / code-authorizes split is right; re-implement it per context.                                              |
| dw Zalo front office identity (yaml/env roster, `VerifiedClaims(issuer="dw-zalo")`)                                                                                                                                                                                                                                                                                            | **Do not reuse.** It is dev-only static mapping; codebase-main's signed-token self-link replaces it.                                                                                                                           |
| dw `SmtpEmailPublisher` / `MockEmailPublisher`                                                                                                                                                                                                                                                                                                                                 | **Reuse almost verbatim** behind a port. Read settings through a pydantic Settings class, not `os.environ`.                                                                                                                    |
| dw mailroom IMAP                                                                                                                                                                                                                                                                                                                                                               | Product-specific (bids). Skip unless Elmich needs inbound email.                                                                                                                                                               |
| sales_dw (C:\Users\phung\sales_dw): `apps/api/src/dw_api/routes/v1/zalo.py` (status/connect/disconnect at 58-97, `/webhook/{secret}` with `compare_digest` at 99-114), `apps/worker/src/dw_worker/consumers/zalo_poll.py:22-36`, `apps/worker/src/dw_worker/zalo_notifier.py:19-29`, `apps/web/components/sales/zalo-connect-card.tsx`, `apps/web/app/sales/settings/page.tsx` | **This is the missing wiring for codebase-main.** Port the route, poll consumer and notifier. Rewrite the card in antd, because it uses lucide and `@dw/ui` Button, which breaks the antd-only rule.                           |

### 4. Gaps in codebase-main

1. **The API client calls endpoints that do not exist.** `/api/v1/zalo/*` has no route in `apps/api/src/dw_api/routes/v1/`, so `getZaloStatus`, `connectZalo` and `disconnectZalo` would 404 (failure-mode #1).
2. **Zalo is wired nowhere.**
    - No API webhook route.
    - No worker poll consumer in `apps/worker/src/dw_worker/main.py`.
    - No settings fields in the api or worker Settings.
    - `ZALO_*` vars appear only in compose, not in `.env.example`.
3. **Outbound notifications reach only the in-app inbox.** There is no channel fan-out, no `UserNotifier`/`ChatSenderPort` consumer and no delivery outbox. Approval cards cannot reach Zalo today.
4. **`zalo_link.py:175,178` hardcodes "Sale Intelligence"** in platform code. The product name should be injected (tenant-flex rule).
5. **A docstring claims a grant that does not exist.** `zalo_link_repo.py:7-8` says `dw_provisioner` holds the identity plane, but `db/migrations/sql/0001_platform_grants.sql:64-74` does not grant it `external_identities`; only `dw_app` has it. An API webhook bound to the provisioner session would fail with permission denied. Use `dw_app`, or add a grant plus a `test_privileges` case.
6. **Zalo links live in the login-identity table** (`external_identities`, which has no tenant column), keyed by `issuer='zalo'`. Login is safe only while `membership_lookup.py:77-80` matches on the verified JWT issuer, which can never be `zalo`. That needs a negative test. Also, one Zalo link per user spans all tenants, and there is one bot token per deployment; per-tenant bots would need a tenant-scoped table.
7. **`ZaloBotClient` is missing the 2000-character split** and the typing action; dw found by measurement that unsplit long messages are silently dropped.
8. **`handle_update`'s signature differs** between codebase-main (`sender=`) and sales_dw (`bot=`). Porting sales_dw's route and poll consumer needs the rename.
9. **No email adapter or port, and no SMTP env vars.**
10. `TELEGRAM_BOT_TOKEN` is declared and read by nothing.
11. Webhook vs polling: the webhook (`setWebhook`, a secret path segment) needs a public HTTPS domain. For local work, long-polling via the worker (sales_dw's `zalo_poll`) works without hosting. Both paths share `handle_update`, so the code can ship now and switch to the webhook once the domain exists.

### Recommendation

Build on codebase-main's Zalo pieces and close gaps 1-3 by porting sales_dw's route, poll consumer and notifier, with the four corrections in gaps 4, 5, 7 and 8. Add a provider-neutral platform outbox modelled on dw's 0011 consumer, so approval cards go through `ChatSenderPort`. Port SMTP from dw as a second channel adapter. Leave dw's `DecisionEngine` and its yaml roster identity behind.
