<!-- Survey of 2026-10-05, kept in the repo so ticket citations outlive the session. In the session scratchpad this file was named `dw-channels.md`; its name here matches its content. -->

# sales_dw: how the Zalo connection works and whether we can reuse it

**Short answer:** sales_dw talks to the **Zalo Bot Platform** (`bot-api.zaloplatforms.com`), not Zalo OA or ZNS. It is plain text with no buttons. A user links their own Zalo: they press "Kết nối Zalo" in Settings, get a signed `/start <token>`, and send it to the bot. Their Zalo chat id is then stored in `platform.external_identities` with `provider='zalo'`. The three core files are already in `c:\Users\phung\codebase` (two identical, one improved). The API route, the worker poll lane, the notifier and the web card are not there yet; they need to be ported.

All paths below are under `C:\Users\phung\sales_dw\`.

## 1. Architecture and files

| Layer                      | File:lines                                                                                                                                            | Role                                                                                                                                                                                                                                                                                                                                                                        |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| HTTP client                | `packages/python/dw_connectors/src/dw_connectors/adapters/zalo_bot.py`                                                                                | `ZaloBotClient(bot_token, poll_timeout=25)`. Base URL `https://bot-api.zaloplatforms.com/bot{token}` (L15, L25). Methods: `get_updates` (L23-45), `send_message(chat_id, text) -> message_id` (L47-56), `set_webhook(url)` (L58-65), `delete_webhook` (L67-71), `get_webhook_info` (L73-79). Responses are Telegram-style `{ok, result, description}`.                      |
| Link logic, shared         | `packages/python/dw_connectors/src/dw_connectors/adapters/zalo_link.py`                                                                               | Token codec `make_connect_token` / `verify_connect_token` (L29-47). `ZaloLinkStore` Protocol with `link` and `unlink_by_zalo` (L50-57). `parse_update`, which handles both payload shapes (L60-70). `handle_update`: `/start <token>` links, stop words unlink, the bot replies in Vietnamese (L73-99). Stop words are `/stop`, `/huy`, `/hủy`, `stop`, `huỷ`, `hủy` (L26). |
| Persistence                | `packages/python/dw_platform/src/dw_platform/adapters/persistence/zalo_link_repo.py`                                                                  | `SqlZaloLink`: `zalo_id_for(user_id)` (L31-44), `link` (L46-68), `unlink_by_zalo` (L70-77), `unlink_by_user` (L79-86). `link` enforces one Zalo per user and one user per Zalo by deleting both sides, then inserting.                                                                                                                                                      |
| API route                  | `apps/api/src/dw_api/routes/v1/zalo.py`, mounted at `/api/v1` in `apps/api/src/dw_api/main.py:40,129`                                                 | `GET /zalo/status` (L58-73), `POST /zalo/connect` (L76-88), `POST /zalo/disconnect` (L91-96), `POST /zalo/webhook/{secret}` (L99-115).                                                                                                                                                                                                                                      |
| Worker poll fallback       | `apps/worker/src/dw_worker/consumers/zalo_poll.py`                                                                                                    | Calls `getUpdates`, then `handle_update` for each update. One bad update is logged and skipped. Registered as lane `zalo_link_poll` in `apps/worker/src/dw_worker/main.py:259-281`, only when both the bot token and the link secret are set.                                                                                                                               |
| Outbound notifier          | `apps/worker/src/dw_worker/zalo_notifier.py`                                                                                                          | `ZaloUserNotifier(resolver, bot).notify(user_id, text)`: look up the linked chat id (no link means silent no-op), send, swallow `RuntimeError`.                                                                                                                                                                                                                             |
| Port                       | `packages/python/dw_sales_crm/src/dw_sales_crm/application/ports.py:1598-1607`                                                                        | `UserNotifier.notify(user_id, text)`, best-effort by contract.                                                                                                                                                                                                                                                                                                              |
| Callers                    | `dw_sales_crm/application/reminder_scan.py` (step 4, after commit) and `report_subscription_fire.py:99,183-191` (gated by `subscription.notify_zalo`) | Wired in `apps/worker/src/dw_worker/main.py:319-328` and `apps/worker/src/dw_worker/consumers/report_subscriptions.py:110-116`.                                                                                                                                                                                                                                             |
| Approval rendering, unused | `dw_connectors/adapters/zalo_approval_notifier.py`                                                                                                    | Renders an approval card as plain text with a "reply in words" hint. Nothing in production imports it (confirmed in `docs/architecture/reminder-channels.md` §2.4).                                                                                                                                                                                                         |
| Design doc                 | `docs/architecture/reminder-channels.md`                                                                                                              | Audit of the notification channels and the plug-in points for a new one.                                                                                                                                                                                                                                                                                                    |

## 2. Data model

- **`platform.external_identities`** (sales_dw migration `db/migrations/versions/0006_external_identities.py`). Columns: `id` uuid PK, `user_id` FK to `platform.users`, `issuer` text, `subject` text, `provider` text (default `'oidc'`), `created_at` timestamptz. It has `UNIQUE(issuer, subject)` and an index `ix_external_identities_user`.
    - A Zalo link is a row with `issuer='zalo'`, `provider='zalo'`, `subject=<zalo chat id>`. No new migration was needed.
    - The table is in the identity plane and has **no RLS and no tenant_id**. The API writes it through the provisioner engine (`zalo.py:50-55`); the worker writes it as `dw_app`.
    - `codebase` already has the same table in `db/migrations/sql/0001_platform_baseline.sql:220` and in `dw_platform/adapters/persistence/tables.py:82-94`.
- **`sales_crm.notifications.channel`** (migration 0128, default `'inapp'`) was meant to hold one row per channel. Only `'inapp'` is ever written; Zalo sends leave no row and no delivery status.
- **`report_subscriptions.notify_zalo` / `notify_email`** (migration 0182, boolean, default true) is the per-user channel choice. Sending requires both the server to be configured and the user's flag to be on.

## 3. How a user links their Zalo

1. Settings page `apps/web/app/sales/settings/page.tsx:14,76` shows `<ZaloConnectCard />` from `apps/web/components/sales/zalo-connect-card.tsx` (157 lines, shadcn `Button` plus lucide icons).
2. The card calls `GET /zalo/status`. If linked, it shows "Đã kết nối Zalo" with "Đổi Zalo khác" and "Tắt thông báo Zalo". If not, it shows "Kết nối Zalo".
3. Pressing it calls `POST /zalo/connect`, which returns `{code, deep_link, instructions}`. The card displays `/start <code>` with a copy button and notes it expires in 15 minutes. If `ZALO_BOT_LINK` is set, it also shows a deep link `…?start=<token>`.
4. The user sends that line to the bot. The webhook or the poll loop calls `SqlZaloLink.link`, and the bot replies "✅ Đã kết nối!".
5. To disconnect, the user either presses the button (`POST /zalo/disconnect`, which calls `unlink_by_user`) or sends `/stop` in Zalo.
6. The API client methods `getZaloStatus`, `connectZalo` and `disconnectZalo` are in `packages/typescript/api-client/src/client.ts`; the OpenAPI snapshot was regenerated in commit 9db4e1f1.

## 4. Inbound: webhook and verification

- **Zalo sends no signature header**, and sales_dw does not use a `secret_token` with `setWebhook`. Authentication is a shared secret in the URL path: `POST /api/v1/zalo/webhook/{secret}`, compared with `hmac.compare_digest` against `ZALO_WEBHOOK_SECRET` (`zalo.py:104-107`). On mismatch, or when the secret is unset, it returns 404 (fails closed).
- **The real trust anchor is the connect token**, not the webhook. Format: 16-byte user UUID + 4-byte expiry + an 8-byte HMAC-SHA256 tag (truncated), base64url-encoded to 38 characters. The TTL is 900 seconds. Verification uses constant-time comparison and an expiry check (`zalo_link.py:30-47`). A forged update cannot link anyone without `ZALO_LINK_SECRET`.
- **Gaps:**
    - Tokens can be replayed within the 15 minutes. There is no single-use nonce, so whoever sees the token can tie their own Zalo to that user.
    - Nothing ever calls `set_webhook`; no script registers it. It has to be run by hand.
    - The webhook does `await request.json()` with no size limit or schema.
- **Payload shapes differ by path:** poll wraps the message as `result.message`; the webhook sends `message` at the top level. The chat id is in `message.chat.id`, or `message.from.id` as a fallback (`zalo_link.py:60-70`).

## 5. Outbound send

- `POST /bot{token}/sendMessage` with JSON `{chat_id, text}`. If `ok` is false, the client raises `RuntimeError`.
- It is always best-effort and always sent after the database commit. The result is at-least-once firing but at-most-once push (commit e12aa1f4).
- There is no retry, no backoff, no throttling and no idempotency key. Sends are sequential.
- Plain text only, and the code imposes no length limit.

## 6. Where secrets live

- Everything comes from environment variables read by pydantic settings: `apps/api/src/dw_api/settings.py:263-299` and `apps/worker/src/dw_worker/settings.py:226-269`.
- There is no vault. **The bot token travels in the request URL path**, so it can end up in proxy or access logs and in httpx error messages. `codebase` already records this as SEC-20 in `.claude/plans/bidding/ehsdt-decisions-owed.md:820`.
- The API and the worker must read the same `ZALO_LINK_SECRET`.

## 7. Environment variables (names only)

- **Core:** `ZALO_BOT_TOKEN` (aliases `DW_API_ZALO_BOT_TOKEN`, `DW_WORKER_ZALO_BOT_TOKEN`), `ZALO_WEBHOOK_SECRET`, `ZALO_LINK_SECRET` (alias `DW_WORKER_ZALO_LINK_SECRET`), `ZALO_BOT_LINK` (optional deep-link base).
- **Public base URL:** `DW_API_PUBLIC_BASE_URL` or `KC_HOSTNAME`, mapped to `zalo_public_base_url`.
- **Legacy hand-mapping, do not port:** `ZALO_USER_AN_ID`, `ZALO_USER_BINH_ID`, `ZALO_USER_CHI_ID`, `ZALO_USER_MAP_JSON`, and `DW_APPROVAL_CHANNEL` (`slack|zalo`).
- **Compose:** sales_dw passes the four core variables to api, chat and worker (`infra/compose/docker-compose.yml:468-471`, `541-544`, `717-720`). `codebase` passes the same four (`infra/compose/docker-compose.yml:362-369`, `542-545`).
- The sales_dw `.env.example` files list no ZALO variables; only the private `.env` has `ZALO_BOT_TOKEN` (line 70). I did not read any values.

## 8. Zalo API behaviour they hit (from code comments and commits)

1. An idle `getUpdates` returns `ok:false` with `error_code 408` "Request timeout". It is treated as "no updates", not an error (`zalo_bot.py:34-38`).
2. `getUpdates` returns `result` as **a single object, not an array**; the client accepts both (`zalo_bot.py:40-45`).
3. `getUpdates` acknowledges on read: each update comes back once and the offset has no effect, so none is tracked (`zalo_poll.py:4-8`). An update that fails to process is lost, and the user has to send `/start` again.
4. **Cloudflare in front of the API returned 403** to Zalo's webhook requests, which use the user agent "Java". That is why the poll fallback exists (commit b13b8f67).
5. There are **no buttons or interactive elements**, so approvals would have to be parsed from free text (`zalo_approval_notifier.py:1-7`).
6. A user who has never messaged the bot cannot be reached. Pushing to arbitrary users would need Zalo OA plus ZNS (verified business account, approved templates, fee per message; `codebase` `ehsdt-production-gaps.md:190,592`).
7. Rate limits and maximum message size were never measured. Treat them as unknown.

## 9. What runs locally vs what needs a public HTTPS host

**Works locally with no public host:**

- Linking through the worker's poll lane (outbound `getUpdates`).
- All outbound sends.
- The status, connect and disconnect endpoints.
- The token codec and its tests.

**Needs a public HTTPS URL:**

- The webhook path: you must call `setWebhook` with `https://<host>/api/v1/zalo/webhook/<secret>`.
- Webhook and poll are mutually exclusive on one bot. `getUpdates` only works after `deleteWebhook`.
- If you put a CDN in front, it must let Zalo's "Java" user agent through, or you stay on polling.
- The deep link in `ZALO_BOT_LINK` is a link to Zalo itself, not to our host, so it does not need one.

**Recommendation:** develop locally on the poll lane, and only enable the webhook once the real domain exists.

## 10. Reuse for FastAPI + PostgreSQL RLS (`codebase` and the separate Elmich portal repo)

**Already in `codebase`:**

- `zalo_bot.py` and `zalo_link_repo.py`, byte-identical to sales_dw.
- `zalo_link.py`, improved: `handle_update` takes `sender: ChatSenderPort` instead of the concrete `ZaloBotClient`.
- `ChatSenderPort` (`packages/python/dw_connectors/src/dw_connectors/ports.py:27-41`).
- Unit tests `dw_connectors/tests/unit/test_zalo_bot.py` and `test_zalo_link.py`.

None of this has a production caller yet; the compose comment at `docker-compose.yml:362-365` says so.

**To port from sales_dw:**

- `routes/v1/zalo.py`
- `consumers/zalo_poll.py` plus the lane registration
- `zalo_notifier.py`
- The `UserNotifier` port, moved to a platform-level home
- The settings fields
- The web card, rewritten in antd, because CLAUDE.md forbids new shadcn components

**Fixes needed before reuse:**

1. **Tenant.** Linking is keyed only by user and carries no tenant (HITL-15). That is fine for "notify this user". But the link must never be used to build an `AccessContext` or to take decisions in chat; the web stays the place where decisions are made (D38 option (a), ADR ctx 0013).
2. **Raw SQL in the status route.** `zalo.py:63-72` reads `external_identities` directly. It should go through `SqlZaloLink.zalo_id_for`.
3. **Token replay.** Make the token single-use (store a nonce or jti), or shorten the TTL.
4. **Token in the URL.** Scrub it from logs and error text, or proxy outbound calls.
5. **Delivery has no audit, idempotency or status.** CLAUDE.md requires audit and idempotency for side effects. Writing one notification row per channel with a send status would satisfy that, as migration 0128 intended.
6. **Legacy settings.** Drop the hand-mapping variables and `approval_channel`.
7. **Webhook registration.** Add a `set_webhook` script or command, plus `.env.example` entries for the four core variables and the public base URL.
8. **Cascade on user delete.** Check that `external_identities.user_id` has an `ON DELETE` rule in the codebase baseline. sales_dw's 0006 has none.

**Separate repo:** if the Elmich portal lives in its own repo, the minimum to copy is `zalo_bot.py`, `zalo_link.py`, the repo and the route. They depend only on httpx, SQLAlchemy and a `users` table, about 300 lines of Python. The identity-plane table (users plus external_identities) has to exist there, or the portal has to call the platform API.
