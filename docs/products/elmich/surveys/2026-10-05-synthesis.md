<!-- Survey of 2026-10-05, kept in the repo so ticket citations outlive the session. In the session scratchpad this file was named `synthesis.md`; its name here matches its content. -->

**Plan: dw-elmichs (Elmich supply chain, steps 1-17, on platform main bf553f4)**

Facts I checked myself: `C:\Users\phung\dw-elmichs` has `origin` (supplychain-dw.git), `platform` (codebase.git) and branch `archive-supply-chain`. `platform.external_identities.user_id` already has `ON DELETE CASCADE` (baseline :720), so gap 8 in the survey is closed. The platform already has `OutboxRepositoryPort` and `FeedbackAttachmentStoragePort` (`dw_platform/application/ports.py:212,235`) that we can reuse.

**Repo rule (ADR-E1).** dw-elmichs is the product repo and takes platform updates with `git merge platform/main`. Anything that is not specific to Elmich (Zalo wiring, channel delivery, the personal settings page, the approval-decider rule) goes into `codebase` first and is merged across. Building it only in dw-elmichs would fork the platform. The owner needs to confirm this (Q-O1).

## 1. Porting dw_supply_chain (slice P, branch `elmich/port` off `main`)

1. **Package.** Run `git checkout archive-supply-chain -- packages/python/dw_supply_chain`, then its unit tests. Verify: 22 unit test files pass.
2. **Root registration.** Root `pyproject.toml` (uv sources, known-first-party, mypy_path, coverage, import-linter root plus the independence contract), `scripts/verify_architecture.py` `IMPORT_TO_DIST`, `infra/docker/{api,worker}.Dockerfile` COPY lines, and `dw-supply-chain` in `apps/{api,worker}/pyproject.toml` and `dw_evals/pyproject.toml`. Then `uv lock`.
3. **Migrations.** Copy all 13. Set `fddd7579ba27.down_revision = "855ae928c3fa"`. Re-chain the three links that pointed at platform revisions: `1c26d9c88738 → 61d934921951`, `2a0ac1ac32e1 → f35345378e3c`, `564975794c7e → 2a0ac1ac32e1`. `dc2285c629d4 → 89e86dfabad6` already holds. Keep `89e86dfabad6`: it does nothing on a fresh database, and dropping it means re-pointing anyway.
4. **Configs and evals.** `configs/{policies,prompts/supply_chain,workers}`, `evals/datasets/supply_chain@1.1.0.json` with its 22 fixtures and 22 expected results, the mock-model fixtures, and the grader block in `dw_evals/graders.py`.
5. **API wiring.** `bootstrap/paths.py`, `container.py` (28 fields), `wiring.py` at the "BOUNDED CONTEXTS PLUG IN HERE" seam (including `strict_approval_prefixes |= {"supply_chain.case_action."}`), the router mount in `main.py`, and the two API tests.
6. **Worker wiring.** `consumers/supply_chain.py`, the `supply_chain_follow_ups` lane, the `supply_chain_follow_up_interval_seconds` setting with a `.env.example` entry, and the `test_worker.py` additions.
7. **Web.** Port the contracts, the API client methods and the nav entry as they are, plus the pages under `apps/web/app/supply-chain/*`, so the slice goes green. Each page is rebuilt in antd by the ticket that next touches it (slice W).
8. **Regenerate** openapi.json, `generated/platform.d.ts` and the release manifest. Then `make ci`, and integration tests for dw_supply_chain, `test_rls_coverage.py` and `test_privileges.py`.

**Risks**

- `test_rls_coverage.py` on main reads the catalog, and the archive was tested against a hard-coded schema list. Any supply_chain policy that narrows by workspace without the `app.workspace_scope` shape will fail. Offboarding must also cover `supply_chain.*`.
- Re-pointing revisions breaks any database that already ran the archive chain, for example the sales-dev server. Ask before running against it.
- The package has not been run against main yet.
- The web pages use `@dw/ui` and lucide, which conflicts with the antd-only rule until slice W.

## 2. Zalo channel (platform slices Z1-Z3, merged into dw-elmichs)

**Reuse as is from codebase-main:** `dw_connectors/adapters/zalo_bot.py`, `zalo_link.py`, `ports.py::ChatSenderPort`, and `dw_platform/adapters/persistence/zalo_link_repo.py`.

**Port from old dw:** `_split_for_zalo` (1900 characters) and `send_chat_action`, into `ZaloBotClient`.

**Port from sales_dw:** `apps/api/src/dw_api/routes/v1/zalo.py`, `apps/worker/src/dw_worker/consumers/zalo_poll.py`, `zalo_notifier.py`, and the settings fields. Changes when porting:

- `bot=` becomes `sender=`.
- The route reaches `SqlZaloLink` through the container. No raw SQL.
- It uses the `dw_app` engine. The provisioner engine lacks the grant.
- The "Sale Intelligence" product name is injected.
- The `ZALO_USER_*` variables and `approval_channel` are dropped.

**Data model**

- The link stays in `external_identities` (`issuer=provider='zalo'`).
- New `platform.channel_link_nonces` (jti PK, user_id FK CASCADE, expires_at, used_at) makes the connect token single-use. The token gains a jti.
- New `platform.channel_deliveries`: tenant_id, workspace_id, recipient_user_id, channel CHECK('zalo'), source_key, status CHECK(pending|sent|failed|cancelled), attempts, external_message_id, last_error, timestamps. UNIQUE(tenant_id, channel, source_key), RLS FORCE with the standard workspace shape, range-partitioned with a DEFAULT partition, created through `platform.ensure_time_partitions`.
- All migrations use alembic random hex ids, with grants asserted in `test_privileges`.

**Link flow**

1. On Settings, the user presses "Kết nối Zalo". `POST /api/v1/zalo/connect` returns `/start <token>` (15 minutes) and the deep link.
2. The user sends it to the bot.
3. `handle_update` runs from either the poll lane or the webhook, consumes the nonce and links the user.
4. `/stop` in Zalo or "Ngắt kết nối" on Settings unlinks.
5. `GET /zalo/status` shows the state.

**Outbound**

- When `platform.deliver_notification` writes the in-app row, the service also enqueues a `channel_deliveries` row for each recipient who has a link. Having a link is the opt-in.
- Worker lane `channel_delivery` claims rows, resolves `zalo_id_for(user_id)`, sends through `ChatSenderPort`, classifies failures as permanent or transient, retries with backoff, records `mark_sent`, and writes an audit entry.
- The text is plain, with a link to the web case.
- **Approval decisions are not taken in Zalo** (ADR-E4). Zalo announces "cần duyệt" and links to `/approvals`.

**Local vs hosted.** `ZALO_UPDATES_MODE=poll|webhook`.

- `poll` (local) registers the `zalo_link_poll` lane, with outbound calls only.
- `webhook` mounts `POST /api/v1/zalo/webhook/{ZALO_WEBHOOK_SECRET}` (compare_digest, 404 when unset, 64 KB body limit, Pydantic schema) and needs public HTTPS.
- New `scripts/zalo_webhook.py set|delete|info` registers the webhook. The two modes are mutually exclusive.
- A CDN must let Zalo's "Java" user agent through.
- The bot token must be scrubbed from httpx errors and logs (SEC-20).

**Env vars:** `ZALO_BOT_TOKEN`, `ZALO_LINK_SECRET` (shared by API and worker), `ZALO_WEBHOOK_SECRET`, `ZALO_BOT_LINK`, `ZALO_UPDATES_MODE`, `DW_API_PUBLIC_BASE_URL`, `DW_WORKER_CHANNEL_DELIVERY_INTERVAL_SECONDS`.

## 3. Login and personal settings

**Login uses Keycloak OIDC, which already exists.**

- Add a realm/client for the portal in `infra/keycloak/dw-realm.json`: rename the realm or add client `elmich-portal`, with `loginTheme` set, redirect URIs taken from env, and registration off.
- Admins create users. Memberships and `sc_*` roles go through `/admin/workspaces`.
- Locally, `dev` mode uses `/dev-login` and the seed `scripts/seed_supply_chain_demo.py` with `keycloak_dev_users.py`.

**Settings page (slice U, platform)**

- New `apps/web/app/settings/page.tsx` in antd: profile read-only from `GET /me`, `ZaloConnectCard` rewritten in antd from sales_dw's `zalo-connect-card.tsx`, and a list of the user's memberships.
- Entry point in `components/session-chip.tsx` ("Cài đặt cá nhân").
- `client.ts` already has `getZaloStatus`, `connectZalo` and `disconnectZalo`.

**.env**

- Complete `.env.example`: add ZALO_*, `DW_API_CORS_ORIGINS`, `KC_HOSTNAME`, `DW_API_AUTO_PROVISION_MEMBERSHIP`/`DEFAULT_*`, and SMTP for the realm. Remove the stale `apps/chat` entries.
- Add `scripts/init_env.py`, which writes `.env` with random secrets and does not overwrite an existing file.
- Fix the web default Keycloak port (8080 vs 8686).

**Hosting (slice H, code now, run once a domain exists)**

- `infra/compose/docker-compose.host.yml` with Caddy (TLS for web, api and auth), `KC_HOSTNAME=https://auth.<domain>`, CORS, and web build arguments.
- Remove the hard-coded `NEXT_PUBLIC_API_BASE_URL` (compose :575).
- Write the runbook `docs/deploy/host.md`.
- Gates use `settings.is_deployed`.

## 4. Stage 1 (steps 1-9) and the hand-off to 10-17

Stage 1 lives inside `dw_supply_chain`.

**New tables** (tenant_id and workspace_id, RLS FORCE): `product_dev_cases`, `product_sample_rounds`, `sample_revision_requests`, `item_codes` UNIQUE(tenant_id, code), `skus` (FK item_code RESTRICT, UNIQUE(tenant_id, sku_code), CHECK quantity>0), and `case_documents` (case_kind, doc_type CHECK, object key under a tenant/workspace path, behind a storage port modelled on `FeedbackAttachmentStoragePort`).

**State machine** `ProductDevState`: PROPOSED → SAMPLE_REQUESTED → SAMPLE_TESTING → (REVISION_REQUESTED ↺ | CANCELLED | PENDING_BOD_REVIEW) → PROFILE_IN_PROGRESS (SLA bm04) → SUPPLIER_CONFIRMATION (SLA supplier_confirmation) → ITEM_CODING → PENDING_SIGNOFF → READY_TO_ORDER → ORDERED. Interrupts work as in POCase. Transitions go through one `apply_product_action` dispatch. Approvals use `supply_chain.product_action.{bod_review,signoff}`, added to the strict prefixes.

**Hand-off ("ĐẶT HÀNG")**

- `PlaceOrder` runs in one transaction, keyed idempotently on the product case id.
- It creates a `po_cases` row in the new initial state `ORDER_REQUESTED`. `po_reference` stays nullable, with a CHECK that it is set in any other state.
- Step 10's `create_po` sets `po_reference` and `order_kind` (new|reorder) and moves the case to PO_CREATED.
- New `po_cases` columns: `product_dev_case_id` (FK RESTRICT, indexed), `pic_user_id` (copied as a stamp), `category`, plus PO lines (sku_id, quantity).
- It emits the outbox event `supply_chain.product.ordered`.

**PIC.** Stamped from `principal_id` at step 1. Follow-up recipients gain the kind `pic`. Changing the PIC uses an audited `reassign_pic` action.

**SLA.** The policy becomes `default + by_category`. Map bm04 and supplier_confirmation to states. Elmich's 4/5/10/21/10/5 days go in as that tenant's override.

**Platform gap.** Restricting who may decide an approval type (BGĐ, Kế toán) needs a `required_scope` stamped on the approval at creation and enforced at decide time (slice A, platform).

## 5. ADRs, glossary, tickets

**ADRs** (`docs/adr/` for the platform; per-context folder following `docs/agents/domain.md`):

- **E1, product repo tracks platform by merge:** generic code goes upstream first.
- **E2, Zalo Bot Platform as the first outbound channel:** the link lives in `external_identities`, the token is single-use, and the link never builds an AccessContext.
- **E3, provider-neutral channel delivery outbox:** `channel_deliveries` is fanned out from `deliver_notification`, with at-least-once delivery and idempotency per source_key.
- **E4, decisions only in the web:** Zalo carries notifications plus a link, not approve-by-text.
- **E5, poll locally, webhook when hosted:** chosen by `ZALO_UPDATES_MODE`.
- **E6, stage 1 inside dw_supply_chain:** a separate ProductDevelopmentCase aggregate.
- **E7, hand-off via ORDER_REQUESTED with a nullable po_reference.**
- **E8, item code and SKU uniqueness owned by the database:** constraint violations map to ConflictError.
- **E9, SLA policy keyed by category:** tenant override for Elmich's numbers.
- **E10, approval decider stamped as required_scope.**
- **E11, case documents through an object-storage port.**

**Glossary (CONTEXT.md):** PO case, Product development case, Sample round, Phiếu yêu cầu chỉnh sửa, BM04 profile, Item code (Mã hàng), SKU, ĐẶT HÀNG/hand-off, PIC, Order kind (Hàng mới/Hàng đặt lại), Duty, Follow-up, Channel link, Channel delivery.

**Tickets, in dependency order** (under `.claude/plans/supply-chain/<feature>/`, and `.claude/plans/platform-channels/` upstream):

1. **P** port (steps 1-8 above)
2. **ENV** `.env.example` + `init_env.py`
3. **Z1** ZaloBotClient split + nonce migration + route + poll lane
4. **U** settings page + antd Zalo card
5. **Z2** channel_deliveries + delivery lane
6. **Z3** webhook + set-webhook script
7. **A** approval decider scope
8. **D** case_documents + storage
9. **S1** product case steps 1-5
10. **S2** BGĐ review step 6 (needs A)
11. **S3** BM04 and supplier confirmation steps 7-8 (needs D)
12. **S4** item code/SKU + signoff step 9
13. **S5** ORDER_REQUESTED/create_po hand-off steps 9→10
14. **S6** SLA by category + PIC routing
15. **S7** evals for stage 1 (injection, cross-tenant, missing evidence)
16. **W** antd rebuild of the supply-chain pages
17. **H** hosting overlay + runbook

## 6. Open questions

**For Elmich**

1. Does the revision loop go back to step 2 or step 4, and is there a maximum number of rounds?
2. Can BGĐ "không duyệt" send the sample back for revision, or does it cancel?
3. Is step 8 an in-app confirmation with the email attached?
4. Is the step 9 sign-off sequential or parallel, and is there a value threshold?
5. What are the item code and SKU formats, and is uniqueness company-wide or per workspace? Is there an ERP sync?
6. How many POs can one product case lead to, and can a reorder skip stage 1?
7. What is the category list, with the SLA for each step?
8. Where does each SLA clock start, and does it pause while blocked?
9. What are the SLAs for the approvals?
10. Who issues and who receives each document? Is a document mandatory at each step?
11. Is container loading its own step?
12. Who owns each part of step 12's sub-flow?
13. Confirm the role catalogue and the separation-of-duty waivers.
14. Which events go to Zalo? Is it internal staff only?

**For the owner**

1. Is upstream-first (E1) acceptable, or do you want everything in dw-elmichs for now?
2. Will it be one bot token per deployment or per tenant?
3. Has the sales-dev database ever run the archive migrations?
4. Port the web pages as they are first, or rebuild in antd immediately?
5. The domain layout: separate hostnames for web, api and auth?

The plugin legal MCP servers (atlassian, box, docusign, egnyte, slack) need authorization through `claude mcp` or `/mcp` in an interactive session. This plan did not need them.
