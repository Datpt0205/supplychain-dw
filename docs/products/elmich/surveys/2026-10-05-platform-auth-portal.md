<!-- Survey of 2026-10-05, kept in the repo so ticket citations outlive the session. In the session scratchpad this file was named `pdf-gap.md`; its name here matches its content. -->

# Map of codebase-main (`C:\Users\phung\codebase-main`, HEAD `bf553f4`): login, per-user settings, channels and hosting

## 1. How login works

**API auth modes**

- Settings are in `apps/api/src/dw_api/settings.py`. `AuthMode = Literal["dev","oidc"]` is at :30, and `auth_mode` defaults to `"dev"` at :74.
- Related settings: `dev_secret` :75, `oidc_issuer_url` :76-78 (alias `OIDC_ISSUER_URL`), `oidc_audience="dw-api"` :79, `oidc_jwks_url` :84-86 (lets the API fetch keys inside Docker while the issuer stays the browser-facing URL).
- `apps/api/src/dw_api/bootstrap/identity.py:17-27` picks the token verifier by `auth_mode`:
    - oidc gives `KeycloakTokenVerifier`.
    - dev with a secret gives `DevTokenVerifier`.
    - Otherwise it returns `None` and auth is off.
- `packages/python/dw_platform/src/dw_platform/adapters/identity/keycloak.py:40-60` does RS256 checks against JWKS with `issuer` and `audience` required. It maps `sub`, `email`, `iss`, and `name` (or `preferred_username`) to `VerifiedClaims`.

**How a request becomes an AccessContext** (`apps/api/src/dw_api/dependencies/auth.py`)

- `get_verified_identity` (:27-41) checks the token only. `/auth/bootstrap` uses it.
- `get_access_context` (:47-64) takes the bearer token plus the `X-Tenant-Id` and `X-Workspace-Id` headers and calls `access_context_factory.build`.
- `packages/python/dw_platform/src/dw_platform/application/identity.py:61-101` (`DbAccessContextFactory`):
    - Both headers are required.
    - `MembershipLookupPort.find_access(subject, issuer, tenant, ws)` must confirm membership.
    - On failure it raises `PermissionDeniedError`, with the same response whether the tenant does not exist or the caller is not a member.
- The `AccessContext` model is in `application/access_context.py:17-61`. It carries tenant, workspace, principal, roles, groups, scopes, clearance, plan_id, feature_flags, record_visibility, visible_owners and max_autonomy_level (fails closed). Helpers: `has_scope`, `has_any_role`.
- Scopes come from `adapters/persistence/membership_lookup.py:29-51` (`effective_scopes`). It unions `platform.roles.scopes` and `permission_sets.scopes` over `memberships.role_keys` and `permission_set_keys`.
- `SqlMembershipLookup` (:55+) also resolves hierarchy visibility.
- Tables: `roles` and `permission_sets` are global catalogs (`tables.py:61-77`). `external_identities (issuer, subject) → user_id` is the identity plane and has no RLS (`tables.py:79-94`).

**First login (`/auth/bootstrap`)**

- Route: `apps/api/src/dw_api/routes/v1/auth.py:44-74`. It returns principal_id, subject, email, display_name, memberships (roles and scopes per workspace) and is_platform_operator.
- Implementation: `adapters/persistence/identity_provisioning.py:53-80` (`SqlIdentityBootstrap`). It creates the user and external_identity on first sight.
- By default a new user gets no membership. `auto_provision_default_membership=False` (`settings.py:100-105`, env `DW_API_AUTO_PROVISION_MEMBERSHIP`). The default tenant, workspace and role are at `settings.py:89-99`.

**Dev login**

- `apps/api/src/dw_api/routes/v1/dev.py:58-86`: `GET /dev/demo-users` and `POST /dev/session` (issues an 8-hour HS256 token for a roster subject from `configs/demo/demo_users.yaml`).
- Mounted only when `not settings.is_deployed` (`main.py:150-163`). The session endpoint exists only when `auth_mode=="dev"` and a secret is set.
- Web side: `apps/web/app/dev-login/page.tsx` shows only when `AUTH_MODE==="dev"` (:28, :40). The dev token is kept in localStorage (`apps/web/lib/session.ts:88-92`).

**Web auth**

- `apps/web/lib/auth/config.ts:11-23`. `NEXT_PUBLIC_AUTH_MODE` defaults to `"oidc"`. `KEYCLOAK_URL` defaults to `http://localhost:8080`, which disagrees with `.env.example`'s 8686.
- `lib/auth/keycloak.ts`: a single keycloak-js instance, tokens held in memory only.
- `lib/auth/auth-context.tsx`:
    - :184-187 uses the dev branch.
    - :196-200 runs `kc.init({onLoad:"login-required", pkceMethod:"S256"})`.
    - :208-215 refreshes the token, and falls back to `kc.login` if refresh fails.
    - :96-104 calls `/api/v1/auth/bootstrap`.
    - :134-163 picks the active workspace (remembered in localStorage `dw.active.workspaceId`).
    - The context exposes `hasScope`, `hasRole`, `login`, `register`, `logout` and `selectWorkspace`.
- Gate: `components/app-frame.tsx:70-120` (statuses loading, unauthenticated → `LoginScreen`, error, no-workspace, ready).

**Keycloak realm** (`infra/keycloak/dw-realm.json`)

- realm `dw`, `registrationAllowed:false`, `verifyEmail:true` but no `smtpServer`, `resetPasswordAllowed:false`, `sslRequired:"external"`.
- No identity providers, even though an auth-context comment mentions Google/Microsoft buttons.
- Client `dw-web`: public, standard flow, PKCE S256.
    - `redirectUris`: `localhost:3000/*`, `http://sales-dev.dxrank.vn:23000/*`, `127.0.0.1:3000/*`.
    - `webOrigins`: the same three.
    - `post.logout.redirect.uris`: localhost only.
    - An audience mapper `dw-api`.
- Seeded users: an.nguyen, binh.tran, chi.
- The `themes/sales/login` theme is mounted (compose :205-208). Compose says it is "activated per realm (loginTheme=sales)", but the realm JSON has no `loginTheme` key, so the theme is never activated.

## 2. Per-user settings page

There isn't one.

- `apps/web/app/` has: admin/{feedback,hierarchy,separation-of-duties,settings,workspaces}, approvals, audit, dev-login, integrations, knowledge, memory, platform.
- `app/admin/settings/page.tsx:28-40` is **tenant** settings, gated on scope `platform.tenant.settings.write`. It appears in the nav at `lib/nav/registry.ts:97-101`.
- `components/session-chip.tsx` (:29, :95, :105) offers only an audit link and logout.
- The API has no `/me/preferences` or similar. `GET /me` (`routes/v1/me.py:27-38`) is read-only.

## 3. Notifications and channels

**In-app inbox (wired)**

- Table `platform.notifications` (`tables.py:384-400`, migration `db/migrations/versions/855ae928c3fa_platform_in_app_notifications.py`). Columns: tenant_id, workspace_id, recipient_user_id, source_key (idempotent), title, body, link (app-relative only). RLS limits rows to the recipient.
- Service: `dw_platform/application/notifications.py:40-65` (`NotificationInboxPort`: latest, mark_read, mark_all_read).
- Routes: `apps/api/src/dw_api/routes/v1/notifications.py:45-79`. `GET /notifications`, `POST /{id}/read`, `POST /read-all`.
- Repository: `adapters/persistence/notifications.py:31`, with `deliver(...)` at :101-130 going through `platform.deliver_notification`. **No production code calls `deliver()`**; it is only wired as the inbox at `wiring.py:192`.
- Retention: `SqlNotificationRetention` :134.
- Web: `components/notification-bell.tsx`.

**Channel port**

- `packages/python/dw_connectors/src/dw_connectors/ports.py:27-41`: `ChatSenderPort.send_message(conversation_id, text) -> str`.
- `TaskConnectorPort` (:10-24) has only a mock.

**Zalo: built and tested, but wired into nothing**

- `dw_connectors/adapters/zalo_bot.py:18-79` (`ZaloBotClient`, base `https://bot-api.zaloplatforms.com`): get_updates long-poll (a 408 counts as idle), send_message, set_webhook, delete_webhook, get_webhook_info. It structurally satisfies `ChatSenderPort`.
- `dw_connectors/adapters/zalo_link.py`:
    - HMAC connect token with a 15-minute TTL (:25-47).
    - `ZaloLinkStore` Protocol (:50-57).
    - `parse_update` (:60-70).
    - `handle_update(update, *, link_secret, store, sender)` (:73-99): `/start <token>` links, `/stop` and `/hủy` unlink.
    - The reply text is hard-coded to "Sale Intelligence" (:96).
- `dw_platform/adapters/persistence/zalo_link_repo.py:27-86` (`SqlZaloLink`): zalo_id_for, link (one-to-one), unlink_by_zalo, unlink_by_user. It writes `external_identities` with issuer and provider `zalo`.
- Tests: `dw_connectors/tests/unit/test_zalo_bot.py`, `test_zalo_link.py`.
- The TypeScript client already has `getZaloStatus`, `connectZalo` and `disconnectZalo` → `/api/v1/zalo/{status,connect,disconnect}` (`packages/typescript/api-client/src/client.ts:84-97, 829-841`). **No such API route exists** in codebase-main.
- Compose passes `ZALO_BOT_TOKEN`, `ZALO_WEBHOOK_SECRET`, `ZALO_LINK_SECRET` and `ZALO_BOT_LINK` to api (`infra/compose/docker-compose.yml:362-369`) and worker (:541-545), with a comment saying no caller reads them yet. `ApiSettings` has no zalo fields, and `.env.example` has no ZALO_* variables.

**Grant bug that blocks porting**

- `db/migrations/sql/0001_platform_grants.sql:63-73` grants `dw_provisioner` access to tenants, workspaces, users, memberships, roles, plans, entitlements, platform_operators and provisioning_audit. It does **not** include `external_identities`.
- `zalo_link_repo.py:6-8` claims both `dw_provisioner` and `dw_app` have access to the identity plane. For the provisioner that is false.
- `dw_app` does have it, through the `ALL TABLES` grant at :31-32.
- So the link store must use the `dw_app` engine, or a new migration must add the grant.

**What to reuse from `C:\Users\phung\sales_dw`**

- `apps/api/src/dw_api/routes/v1/zalo.py` (status, connect, disconnect, and `POST /webhook/{secret}`).
- `apps/worker/src/dw_worker/consumers/zalo_poll.py` (36 lines) and `zalo_notifier.py` (29 lines), registered at `apps/worker/src/dw_worker/main.py:259-281`.
- `apps/web/components/sales/zalo-connect-card.tsx` (157 lines), used by `app/sales/settings/page.tsx`.
- Settings fields: `apps/api/src/dw_api/settings.py:263-284` and `apps/worker/src/dw_worker/settings.py:227-310`.

**What has to change when porting from sales_dw**

- It calls `handle_update(..., bot=bot)`; codebase-main's parameter is `sender=`.
- The route imports `SqlZaloLink` and runs raw SQL. That breaks the layer rule, so it belongs behind the container or wiring.
- It prefers `provisioner_engine`, which would hit the missing grant above.
- It has demo-only `ZALO_USER_*_ID` env maps to drop.

## 4. `.env.example` variables that matter (`C:\Users\phung\codebase-main\.env.example`)

- **Keycloak:** `KEYCLOAK_ADMIN`, `KEYCLOAK_ADMIN_PASSWORD`, `KEYCLOAK_DB_PASSWORD`, `OIDC_ISSUER_URL=http://localhost:8686/realms/dw`, `OIDC_CLIENT_ID=dw-web`, `OIDC_CLIENT_SECRET`. `KC_HOSTNAME` is not listed but compose reads it.
- **API:** `DW_API_PROFILE=local`, `DW_API_AUTH_MODE=oidc`, `DW_API_DEV_SECRET`, `DW_API_OIDC_ISSUER_URL`, `DW_API_OIDC_AUDIENCE=dw-api`, `DW_API_OIDC_JWKS_URL` (commented out).
    - `DW_API_CORS_ORIGINS` is **not listed** but is required when deployed.
    - `DW_API_AUTO_PROVISION_MEMBERSHIP` and `DW_API_DEFAULT_TENANT_ID` / `_WORKSPACE_ID` / `_ROLE` are not listed.
- **Web:** `NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_AUTH_MODE=oidc`, `NEXT_PUBLIC_KEYCLOAK_URL=http://localhost:8686`, `NEXT_PUBLIC_KEYCLOAK_REALM=dw`, `NEXT_PUBLIC_KEYCLOAK_CLIENT_ID=dw-web`, `NEXT_PUBLIC_CHAT_BASE_URL`.
- **Channels:** `DW_TASK_CONNECTOR=mock`, `TELEGRAM_BOT_TOKEN`, `DW_APPROVAL_REMINDER_SECONDS=5`, `DW_PUBLIC_WEB_URL=http://localhost:3000`. No ZALO_* variables.
- **Stale entries:** the template still describes `apps/chat`, `DW_CHAT_*` and a "signal analyst", none of which exist under `apps/` (api, docgen, web, worker only).

## 5. Compose (`infra/compose/docker-compose.yml`)

- **Services:** postgres :45, qdrant :77, valkey :94, s3 (SeaweedFS) :112, s3-setup :144, keycloak :172 (`start --import-realm`, `KC_HOSTNAME=${KC_HOSTNAME:-http://localhost:8686}` :221, `KC_PROXY_HEADERS=xforwarded`, port `127.0.0.1:8686→8080`), tei-embed and tei-rerank (models profile), migrate :323, api :342, docgen :420, docgen-gateway :482, worker :511, web :560, plus the observability set: clickhouse, langfuse-worker, langfuse-web, postgres-exporter, alertmanager, prometheus.
- **Profiles:** `infra` / `full` / `models` / `observability` / `migration`.
- Every port is bound to 127.0.0.1. The repo has no reverse proxy (nginx is only mentioned in comments at :177, :191).
- **web:** build args `NEXT_PUBLIC_*` (:563-570) are baked in at build time, so moving to a new domain means rebuilding the image. The runtime sets `NEXT_PUBLIC_API_BASE_URL: http://localhost:8000` (hard-coded, :575) and `API_INTERNAL_BASE_URL`, which nothing in `apps/web` reads.
- **Dockerfile:** `infra/docker/web.Dockerfile:30-36` defaults the Keycloak URL to localhost:8080.
- **Overlays:**
    - `docker-compose.prod.yml` and `docker-compose.uat.yml` only pin `DW_API_PROFILE` and set memory limits.
    - `docker-compose.dev.yml` (the shared server stack) sets `DW_API_CORS_ORIGINS: '["${DW_PUBLIC_WEB_URL}"]'` (:88), `KC_HOSTNAME: ${KC_PUBLIC_HOSTNAME:?}`, ports on 0.0.0.0 (23000 web, 28000 api, 28686 kc), and Qdrant and Redis passwords.
- `infra/helm` and `infra/terraform` are empty, and `docs/` holds only `agents/`. There is no deployment runbook.

## 6. What hosting a portal on a real domain needs

- **Startup checks** (`settings.py:256-299`, `validate_for_profile`). In uat or production the API refuses to start when:
    - auth is dev mode,
    - there is no OIDC issuer,
    - there is no `DW_API_DATABASE_URL`,
    - the model provider is mock,
    - the task connector is mock (`none` is accepted),
    - the embedding provider is hash,
    - there is no `qdrant_url`,
    - `cors_origins` is empty,
    - Langfuse is half-configured.
- **Compose gaps for prod and uat:**
    - The base and prod/uat api env never pass `DW_API_CORS_ORIGINS`, so prod or uat as they stand **refuse to start**. Only the dev overlay sets it.
    - The base defaults `DW_TASK_CONNECTOR` to mock, `DW_MODEL_PROVIDER` to mock and `DW_API_EMBEDDING_PROVIDER` to hash. The server `.env` must override all three: none, openai_compatible, and openai_compatible or tei.
- **CORS** (`apps/api/src/dw_api/main.py:93-107`): explicit origins, no credentials (bearer header). Allowed headers are Authorization, Content-Type, X-Tenant-Id, X-Workspace-Id, Idempotency-Key. Local falls back to localhost:3000. In deployed profiles the OpenAPI docs are hidden (:78-86) and dev routes are not mounted (:152).
- **OIDC:** add `https://<portal-domain>/*` to the dw-web `redirectUris`, `webOrigins` and `post.logout.redirect.uris` in `dw-realm.json`. The realm is only imported into an empty Keycloak DB, so an existing one has to be edited in the admin console.
    - Set `KC_HOSTNAME=https://<auth-domain>` so the token `iss` equals `DW_API_OIDC_ISSUER_URL` (`.../realms/dw`).
    - Keep `DW_API_OIDC_JWKS_URL=http://keycloak:8080/...` for in-network key fetches.
    - Add SMTP to the realm if `verifyEmail` or password reset is wanted.
- **Web build args:** `NEXT_PUBLIC_API_BASE_URL=https://<api-domain>`, `NEXT_PUBLIC_KEYCLOAK_URL=https://<auth-domain>`, `NEXT_PUBLIC_AUTH_MODE=oidc`. Also remove or override the hard-coded runtime `NEXT_PUBLIC_API_BASE_URL` at compose :575.
- **TLS and proxy:** an external reverse proxy (nginx or Caddy) has to terminate TLS for web, api and keycloak and forward X-Forwarded-*. Keycloak's `sslRequired:external` refuses plain http from non-local clients.
- **Zalo:** to receive updates by webhook, the API needs a public HTTPS URL for `POST /api/v1/zalo/webhook/{ZALO_WEBHOOK_SECRET}`. Without a public host, the worker long-poll path (`getUpdates`) works locally, as `zalo_bot.py` documents, so linking can be developed and run on localhost before the real domain exists.
- **Platform rule:** gate deployment behaviour on `settings.is_deployed`, never on `profile=="production"` (`settings.py:24-28, 240-243`).
