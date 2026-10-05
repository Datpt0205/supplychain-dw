# Supply chain (Elmich)

This repo is the Elmich product: bounded context
`packages/python/dw_supply_chain`, built on the platform seed. Remotes:
`origin` = `supplychain-dw` (push here when Đạt says), `platform` = `codebase`
(merge from it, never push). How the two relate:
[ADR 0011](../../docs/adr/0011-e1-product-repo-builds-here-generic-pieces-stay-in-platform-packages.md).

- **Spec:** `docs/products/elmich/process.md`, Part A of Elmich's process (17
  steps) transcribed on 2026-10-05, with the step-to-code map and open points.
  Part B (Product MKT content) is out of scope (Đạt, 2026-10-05).
- **Glossary:** `packages/python/dw_supply_chain/CONTEXT.md`.
- **Decisions:** `docs/adr/0011`–`0023` (E1–E13), all Proposed.
- **History:** the context was built on the platform repo's `supply-chain`
  branch until 2026-09-28 and is kept at branch `archive-supply-chain` / tag
  `archive/supply-chain-2026-09-28` (`5d24c25`). Its slice log (slices 0–20)
  and narrative: `git show archive-supply-chain:.claude/plans/supply-chain.md`.

## Where it stands (2026-10-05)

- **Steps 10–17 (PO case)** are on `main` (slice P, `c2f04dc`).
- **Steps 1–9 have no code.** Stage 1 is designed (ADR 0016–0021) and
  ticketed (`supply-chain/stage-1/`), S1–S7.
- **Channels:** in-app inbox only. The Zalo pieces on `main` are built and
  tested but wired into nothing; the API client calls `/api/v1/zalo/*` routes
  that do not exist. Zalo is planned as a two-way work channel (Z4–Z6).
- **Owner decisions applied (Đạt, 2026-10-05):** build everything here, keep
  generic pieces in platform packages to upstream later; one Zalo bot token per
  deployment; port web pages as they are, rebuild in antd later; separate
  hostnames for web, api and auth from env; Elmich scope = Part A only.
- **Zalo is a two-way work channel (Đạt, 2026-10-05):** a PIC proposes a
  product at step 1 by chat (Z4); approvers decide steps 6 and 9 in Zalo only
  after opening the current version on the portal, by typing back a one-time
  code shown there, never sent in Zalo (Z5, ADR 0014 revised); anyone asks
  read-only questions about cases they may see (Z6). Inbound messages build an
  AccessContext from the linked user's membership, minimum scopes, server-side
  only (ADR 0012 revised).

## Slices

Specs and tickets under `.claude/plans/supply-chain/<feature>/`. "Generic"
means platform code, an upstream candidate (ADR 0011).

| Slice | Ticket                                                              | Generic | Status          | Blocked by                          |
| ----- | ------------------------------------------------------------------- | ------- | --------------- | ----------------------------------- |
| P     | `port/issues/01-port-dw-supply-chain.md`                            | no      | resolved        | —                                   |
| P2    | `port/issues/02-audit-and-spend-on-supply-chain-writes.md`          | partly  | ready-for-agent | P                                   |
| P3    | `port/issues/03-follow-ups-retention.md`                            | no      | ready-for-agent | P                                   |
| ENV   | `env/issues/01-env-example-and-init-env.md`                         | yes     | resolved        | P                                   |
| Z1    | `zalo-channel/issues/01-zalo-link.md`                               | yes     | resolved        | P, ENV                              |
| U     | `personal-settings/issues/01-settings-page-and-login.md`            | yes     | resolved        | Z1, ENV, web-ui antd-shell 03/05    |
| Z2    | `zalo-channel/issues/02-channel-delivery.md`                        | yes     | ready-for-agent | Z1                                  |
| Z3    | `zalo-channel/issues/03-zalo-webhook.md`                            | yes     | ready-for-agent | Z1                                  |
| Z4    | `zalo-channel/issues/04-chat-proposal.md`                           | partly  | ready-for-agent | Z1, U, S1, D                        |
| Z5    | `zalo-channel/issues/05-approve-via-zalo.md`                        | partly  | ready-for-agent | Z4, Z2, A, S2                       |
| Z6    | `zalo-channel/issues/06-read-only-qa.md`                            | partly  | ready-for-agent | Z4                                  |
| ZL    | `zalo-channel/issues/07-live-run.md`                                | —       | ready-for-human | Z1–Z6, H2                           |
| A     | `approval-decider-scope/issues/01-required-scope.md`                | yes     | resolved        | P                                   |
| D     | `case-documents/issues/01-case-documents.md`                        | no      | resolved        | P                                   |
| S1    | `stage-1/issues/01-product-case-steps-1-5.md`                       | no      | ready-for-agent | P, D                                |
| S2    | `stage-1/issues/02-bod-review-step-6.md`                            | no      | ready-for-agent | S1, A                               |
| S3    | `stage-1/issues/03-bm04-and-supplier-confirmation-steps-7-8.md`     | no      | ready-for-agent | S2                                  |
| S4    | `stage-1/issues/04-item-code-sku-signoff-step-9.md`                 | no      | ready-for-agent | S3                                  |
| S5    | `stage-1/issues/05-place-order-hand-off.md`                         | no      | ready-for-agent | S4                                  |
| S6    | `stage-1/issues/06-sla-by-category-and-pic-routing.md`              | no      | ready-for-agent | S5                                  |
| S7    | `stage-1/issues/07-stage-1-evals.md`                                | no      | ready-for-agent | S6                                  |
| PK    | `packaging-design/issues/01-colour-packaging-and-pre-production.md` | no      | ready-for-agent | P, D                                |
| W     | `antd-pages/issues/01-rebuild-supply-chain-pages.md`                | no      | ready-for-agent | P, web-ui antd-shell 03/05/07/08/09 |
| H     | `hosting/issues/01-caddy-overlay-and-runbook.md`                    | yes     | ready-for-agent | ENV, U                              |
| H2    | `hosting/issues/02-live-domain.md`                                  | —       | ready-for-human | H                                   |

**Next:** A, then D, then S1 (Đạt, 2026-10-05: in that order, without
stopping between them). Z4 starts once S1 is in; Z5 after Z4 and S2; Z6 after Z4.

Live runs that need a person, a phone or a domain are their own
`ready-for-human` tickets (ZL, H2), so agent tickets can close honestly. The
live-run ticket was renamed from Z4 (`04-live-run.md`) to ZL (`07-live-run.md`)
on 2026-10-05 when Z4–Z6 were added.

**Not ticketed yet:** the MKT half of step 12's sub-flow (waits for Part B);
container loading as its own step (step 14), deferred until QE-15 (QO-6);
email as a second channel.

**Surveys** behind the tickets' "Nguồn" sections: `docs/products/elmich/surveys/`
(2026-10-05), named by content.

## Slice log

| Slice  | Commit        | What                                                                                                       |
| ------ | ------------- | ---------------------------------------------------------------------------------------------------------- |
| P, ENV | `c2f04dc`     | Port of `dw_supply_chain` onto the current platform; Elmich environment.                                   |
| U, Z1  | `51c0ef7`     | Per-user login on 3200, `/settings`, Zalo link with a single-use token.                                    |
| A      | `dabf5c4`     | `required_scope` stamped on an approval, enforced in `decide`, read by `/approvals`.                       |
| D      | (this commit) | `case_documents` (first workspace-narrowed table), own bucket, upload/download, offboarding, orphan sweep. |

## Open — named, not fixed, still true after the port

Carried from the archive's area file; each was true at `5d24c25`. Re-check
after P before acting on one.

- **Follow-ups:** recipients are members of the case's own workspace; the
  sweep's own open and resolve are recorded on the row, not in
  `platform.audit_events`; each sweep re-assesses every active case of every
  tenant.
- **Waiving a separation-of-duty rule has no second pair of eyes;** the
  compensating control is the record. Clearing `waivable` later makes open
  waivers lift nothing.
- **A later change to a role's scopes is not re-checked** against existing
  memberships.
- **Unbounded reads:** `list_active` has no pagination and binds one parameter
  per active case (asyncpg's 32 767 limit fails closed with a 500); the brief
  reads 50 pending approvals; one day's transitions are unbounded.
- **Pending-approvals card filters on the client** from `listApprovals({limit: 200})`.
- **`delay_impact_analysis` renders supplier name and PO reference outside
  `<input>`;** `supplier_update_understanding`'s raw text has no route-level
  guard. Render-time neutralisation in `PromptRegistry` is the robust fix.
- **Model accuracy is unmeasured;** the mock cannot read.
  `delay_impact_analysis` has no eval case.
- **Provisional domain values:** `SupplierEventType`'s seven values; the
  supplier-update cadence 1d/2d; uniform delay propagation; SLA
  `not_applicable` while interrupted.
- **Suppliers have no master record;** two spellings are two suppliers.
- **Step 17's SLA** (`warehouse_receipt`) runs while the case sits in
  `payment_completed`, so it measures payment to start of receiving, not to
  goods in stock.
- **Context ADRs sit in `docs/adr/`** (0016–0019, 0021); `docs/agents/domain.md`
  puts them in `packages/python/dw_supply_chain/docs/adr/`. Move them once P
  is resolved, and fix the links.
- **The provisioner's grant list has three copies that disagree** (baseline,
  `create_provisioner_role.py`, `test_provisioning.py`) for users and plans.

## Decisions recorded, and why

From the archive, still held:

- **Own `supply_chain` schema;** a new schema ships its own USAGE and default
  privileges in its migration.
- **Stay Python;** revisit a second language only against a measured
  bottleneck (`CLAUDE.md`, "A second language").
- **Tenant-configurable, never single-customer:** SLA, approval matrix, duties,
  brief order, follow-up routing and (new) product approvals resolve through
  `PolicyOverridePort`. Elmich's numbers are Elmich's override.
- **Platform floors are not tenant-weakenable:** strict approval prefixes, a
  non-waivable SoD rule, retention, spend and run ceilings.
- **Append-only children bounded per case stay unpartitioned** (transitions,
  supplier updates, delay analyses; stage 1's transitions join them).
- **The model interprets, code decides; an answer is never broader than the
  question.**
- **SLA default is short test values;** a real customer sets its own override
  before go-live.

New on 2026-10-05: ADR 0011–0023, and the owner decisions listed above. ADR
0014 (E4) was revised the same day from "decisions only on the web" to
"decisions on Zalo after a portal view, with a one-time code"
(`docs/adr/0014-e4-decisions-on-zalo-after-a-portal-view.md`), and ADR 0012
(E2) from "the link never builds an AccessContext" to "inbound commands build
one from the linked user's membership, minimum scopes".

## Roles and duties

Current (archive migrations `f35345378e3c`, `2a0ac1ac32e1`): `sc_viewer`,
`sc_operator` (ordering, exceptions), `sc_finance`, `sc_qc`, `sc_logistics`,
`sc_warehouse`, `sc_process_admin`; five waivable SoD rules. Planned by the
stage-1 tickets: `sc_rnd` (duty `rnd`), `sc_supply_lead` (duty `supply_lead`,
TP Cung ứng), `sc_bod` (`supply_chain.approve.bod`); `supply_chain.approve.accounting`
granted to the existing `sc_finance` rather than a new `sc_accounting`, unless
QE-16 separates the two people. Elmich confirms the catalogue (QE-16).

## Decisions owed

**Elmich** (in Vietnamese, ready to send; QE-01–04 are Elmich's own
[Cần xác nhận], see `process.md` mục 6):

- **QE-01** Thời gian duyệt chứng từ các cấp và thanh toán đặt cọc; SLA cho từng
  bước duyệt: BGĐ duyệt mẫu (bước 6), TP Cung ứng xác nhận (bước 8), trình ký
  (bước 9).
- **QE-02** Bộ phận lập và bộ phận nhận của từng tài liệu ở mục 2; bước nào bắt
  buộc có tài liệu nào trước khi đi tiếp. Chứng từ thanh toán, đặt cọc phải lưu
  tối thiểu bao lâu theo luật (hôm nay một chứng từ chỉ bị xóa khi cả hồ sơ bị xóa
  lúc offboarding)?
- **QE-03** Sơ đồ chi tiết và sơ đồ con bước 12 có đúng không; vì sao dòng sơ đồ
  bỏ qua bước 6; ai làm và SLA của từng bước con bước 12.
- **QE-04** Các con số SLA đã áp dụng thực tế hay còn là đề xuất.
- **QE-05** BM04 là 2 ngày (bảng tổng quan) hay 4 ngày (bảng 17 bước)? "Tạo mã hàng
  0,5 ngày" còn áp dụng không?
- **QE-06** PO có được BGĐ duyệt riêng sau khi tạo (bảng tổng quan, bước 5), hay chỉ
  trình ký ở bước 9? Có ngưỡng giá trị không?
- **QE-07** Mẫu "Cần chỉnh sửa" quay lại bước 2 (Cung ứng liên hệ lại NCC) hay bước 4
  (R&D gửi phiếu)? Có giới hạn số vòng không?
- **QE-08** BGĐ "không duyệt" là hủy, hay trả về chỉnh sửa mẫu?
- **QE-09** Bước 8 xác nhận trong ứng dụng kèm file email có được không, hay phải đọc
  hộp thư?
- **QE-10** Trình ký bước 9: BGĐ và Kế toán ký tuần tự (ai trước) hay song song? Cả
  hai bắt buộc? Không duyệt thì về đâu?
- **QE-11** Định dạng mã hàng và mã SKU; không trùng trong cả công ty hay từng
  workspace; có đối chiếu với ERP hoặc danh mục hàng không, hệ thống nào cấp mã
  chính thức; một mã hàng có nhiều SKU (màu, cỡ) không; "chốt số lượng SKU" là số
  biến thể hay số lượng đặt mỗi SKU?
- **QE-12** Một sản phẩm sinh mấy PO? Một PO gộp SKU của nhiều sản phẩm được không?
  Hàng đặt lại có bỏ qua giai đoạn 1 không, Hàng mới thì sao?
- **QE-13** Danh sách Category; SLA từng bước theo Category; chọn Category ở bước 1;
  thang "mức độ ưu tiên" ở bước 1.
- **QE-14** Mỗi đồng hồ SLA bắt đầu từ đâu (đặt cọc từ lúc tạo PO hay lúc yêu cầu cọc;
  về cảng từ QC đạt, đóng cont hay ETD; thanh toán từ về cảng; nhập kho từ thanh toán
  hay về cảng)? SLA có dừng khi hồ sơ bị chặn hoặc chờ bên ngoài không?
- **QE-15** Đóng cont có phải bước riêng, người riêng, tách khỏi QC đạt không?
- **QE-16** Xác nhận danh mục vai (R&D, TP Cung ứng, BGĐ, Kế toán, Cung ứng, QC,
  Logistics, Kho) và các cặp tách nhiệm Elmich cần; đội nhỏ có cần miễn trừ không.
- **QE-17** Sự kiện nào gửi qua Zalo? Zalo chỉ cho nhân viên nội bộ (bot không nhắn
  được NCC chưa nhắn trước)?
- **QE-18** PIC vắng hoặc nghỉ thì ai đổi PIC? TP Cung ứng có thấy hồ sơ của mọi PIC?
- **QE-19** Báo cáo hằng ngày cho TP Cung ứng ở bước 3: mỗi lần đánh giá hay một bản
  mỗi ngày; trong ứng dụng, Zalo hay email?
- **QE-20** Tóm tắt trong tin duyệt, câu trả lời hỏi đáp và ảnh đề xuất đi qua máy chủ
  Zalo. Dữ liệu nào được đi qua (mã, tên SP, trạng thái, tên NCC, giá, chứng từ)? Có quy
  định nội bộ nào cấm không?

**Đạt:**

- **QO-1** Has the sales-dev database ever run the archive's migration chain? P
  re-points four revisions; running the new chain on such a database breaks it.
- **QO-2** Review ADR 0011–0023 (all Proposed).
- **QO-3** SMTP for the Keycloak realm (password reset, email verification):
  whose server?
- **QO-4** Login page branding: a neutral theme or Elmich's own.
- **QO-5** When and by which slices generic pieces go upstream (ADR 0011, open
  point 1).
- **QO-6** Confirm deferring container loading as its own step (step 14) until
  Elmich answers QE-15; until then it stays inside `qc` → `in_transit`.
- **QO-7** Strict approvals need a written comment, so a Zalo approval at steps
  6 and 9 is `DUYỆT 4821 <nhận xét>`, not bare `DUYỆT 4821` (Z5 Comments).
  Confirm, or pick one of the alternatives in ADR 0014 point 6: the page asks
  for a default comment when it issues the code, or strict types are decided
  on the web only; or drop the comment floor for these types (a platform floor
  change, ADR needed). Also confirm 4 digits, 15 minutes, 5 wrong tries
  per 15 minutes.

## Working notes

- **Local stack:** compose project `dw_elmichs`; postgres 25432, qdrant
  26333/26334, valkey 26379, object store 29000/29001, keycloak 28686, docgen
  28110; api 8200, web 3200 (`DW_API_PORT`, `DW_WEB_PORT` in `.env`, which
  `scripts/dev.sh` reads; the Keycloak realm's redirect URIs use 3200). `make infra-up` is safe here; it does not touch
  other stacks.
- **Setup:** `uv sync --all-packages` and `pnpm install` before checks;
  integration tests with `set -a && source .env && set +a`; backend URLs on
  `127.0.0.1`, not `localhost`.
- **Local browser run** (from the archive, re-check after P): `make db-migrate`,
  `scripts/seed_supply_chain_demo.py seed` (personas An, Bình, Diệu, Giang, Hà,
  Chi; PO-DEMO-001..004), `scripts/keycloak_dev_users.py`, `make dev`.
- **Test database:** supply chain uses `dw_test_supply_chain`, recreated per
  pytest session; an RLS mutation check goes into the migration file.
- **Windows:** `next build`'s standalone copy fails with EPERM here (CI builds
  on ubuntu); mutation scripts need `encoding="utf-8"` and a green control
  first.
