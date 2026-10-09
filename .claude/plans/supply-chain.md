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
- **Decisions:** E1–E18, Accepted with dated amendments (QO-2). The generic ones in
  `docs/adr/` (0011–0015, 0020, 0022, 0023); the context-only ones (E6–E9, E11,
  E14–E18: 0016–0019, 0021, 0025–0029) in `packages/python/dw_supply_chain/docs/adr/`
  since HR6. E14–E18 (2026-10-09): AI prepares a step and a person approves the move,
  ADR 0021 amended so file content reaches the model as untrusted data, BM04 and
  commercial data as fields, one-time import, MKT a minimal user, supplier messages
  drafted by AI and sent by a person.
- **History:** the context was built on the platform repo's `supply-chain`
  branch until 2026-09-28 and is kept at branch `archive-supply-chain` / tag
  `archive/supply-chain-2026-09-28` (`5d24c25`). Its slice log (slices 0–20)
  and narrative: `git show archive-supply-chain:.claude/plans/supply-chain.md`.

## Where it stands (2026-10-05)

- **Steps 10–17 (PO case)** are on `main` (slice P, `c2f04dc`).
- **Steps 1–17 run as one flow** (S1 steps 1–5, S2 step 6, S3 steps 7–8, S4 step 9,
  S5 ĐẶT HÀNG and step 10, S6 SLA by Category and PIC routing, S7 stage-1 evals,
  S8 stage 1 in the brief, the command bar, Zalo questions and the daily report). Approvals carry `required_scope` (A); cases carry
  documents (D).
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

| Slice | Ticket                                                                    | Generic | Status          | Blocked by                          |
| ----- | ------------------------------------------------------------------------- | ------- | --------------- | ----------------------------------- |
| P     | `port/issues/01-port-dw-supply-chain.md`                                  | no      | resolved        | —                                   |
| P2    | `port/issues/02-audit-and-spend-on-supply-chain-writes.md`                | partly  | resolved        | P                                   |
| P3    | `port/issues/03-follow-ups-retention.md`                                  | no      | resolved        | P                                   |
| P4    | `port/issues/04-po-cases-narrowed-by-workspace.md`                        | no      | resolved        | P                                   |
| ENV   | `env/issues/01-env-example-and-init-env.md`                               | yes     | resolved        | P                                   |
| Z1    | `zalo-channel/issues/01-zalo-link.md`                                     | yes     | resolved        | P, ENV                              |
| U     | `personal-settings/issues/01-settings-page-and-login.md`                  | yes     | resolved        | Z1, ENV, web-ui antd-shell 03/05    |
| Z2    | `zalo-channel/issues/02-channel-delivery.md`                              | yes     | resolved        | Z1                                  |
| Z3    | `zalo-channel/issues/03-zalo-webhook.md`                                  | yes     | resolved        | Z1                                  |
| Z4a   | `zalo-channel/issues/04-chat-proposal.md` (steps 1–3, `/settings` select) | yes     | resolved        | Z1, U                               |
| Z4b   | `zalo-channel/issues/04-chat-proposal.md` (steps 4–7, 9, 10)              | no      | resolved        | Z4a, S1, D                          |
| Z4p   | `zalo-channel/issues/04b-photos.md` (photos, old step 8)                  | no      | ready-for-human | Z4b; a real photo update as fixture |
| Z5    | `zalo-channel/issues/05-approve-via-zalo.md`                              | partly  | resolved        | Z4, Z2, A, S2                       |
| Z6    | `zalo-channel/issues/06-read-only-qa.md`                                  | partly  | resolved        | Z4                                  |
| ZL    | `zalo-channel/issues/07-live-run.md`                                      | —       | ready-for-human | Z1–Z6, H2                           |
| A     | `approval-decider-scope/issues/01-required-scope.md`                      | yes     | resolved        | P                                   |
| A2    | `approval-decider-scope/issues/02-admin-does-not-pass-stamped-scope.md`   | yes     | resolved        | A                                   |
| D     | `case-documents/issues/01-case-documents.md`                              | no      | resolved        | P                                   |
| S1    | `stage-1/issues/01-product-case-steps-1-5.md`                             | no      | resolved        | P, D                                |
| S2    | `stage-1/issues/02-bod-review-step-6.md`                                  | no      | resolved        | S1, A                               |
| S3    | `stage-1/issues/03-bm04-and-supplier-confirmation-steps-7-8.md`           | no      | resolved        | S2                                  |
| S4    | `stage-1/issues/04-item-code-sku-signoff-step-9.md`                       | no      | resolved        | S3                                  |
| S5    | `stage-1/issues/05-place-order-hand-off.md`                               | no      | resolved        | S4                                  |
| S6    | `stage-1/issues/06-sla-by-category-and-pic-routing.md`                    | no      | resolved        | S5                                  |
| S7    | `stage-1/issues/07-stage-1-evals.md`                                      | partly  | resolved        | S6                                  |
| S8    | `stage-1/issues/08-stage-1-brief-and-command-bar.md`                      | no      | resolved        | S7; QE-19 provisional               |
| PK    | `packaging-design/issues/01-colour-packaging-and-pre-production.md`       | no      | resolved        | P, D                                |
| W     | `antd-pages/issues/01-rebuild-supply-chain-pages.md` (E-HSDT v3 look)     | no      | resolved        | P                                   |
| H     | `hosting/issues/01-caddy-overlay-and-runbook.md`                          | yes     | resolved        | ENV, U                              |
| H2    | `hosting/issues/02-live-domain.md`                                        | —       | ready-for-human | H                                   |
| HR1   | `hardening/issues/01-unbounded-reads.md`                                  | partly  | resolved        | —                                   |
| HR2   | `hardening/issues/02-follow-up-sweep-audits-as-its-lane.md`               | no      | resolved        | platform ADR 0011 (M3)              |
| HR3   | `hardening/issues/03-step-17-sla-clock.md`                                | no      | resolved        | QE-14 provisional                   |
| HR4   | `hardening/issues/04-supplier-master-record.md`                           | no      | resolved        | —                                   |
| HR5   | `hardening/issues/05-ops-and-settings-screen.md`                          | partly  | resolved        | H                                   |
| HR6   | `hardening/issues/06-cleanup.md`                                          | partly  | resolved        | —                                   |
| AI-01 | `ai-automation/issues/01-commercial-data-and-bm04-fields.md` (L)          | no      | resolved        | —                                   |
| AI-02 | `ai-automation/issues/02-document-extraction-lane.md` (L)                 | partly  | resolved        | —                                   |
| AI-03 | `ai-automation/issues/03-drafts-and-templates.md` (L)                     | partly  | resolved        | —                                   |
| AI-04 | `ai-automation/issues/04-skills-registry.md` (M)                          | yes     | resolved        | —                                   |
| AI-05 | `ai-automation/issues/05-step-proposals.md` (L)                           | partly  | resolved        | —                                   |
| AI-06 | `ai-automation/issues/06-preparation-evals-and-qwen-gate.md` (M)          | partly  | resolved        | —                                   |
| AI-07 | `ai-automation/issues/07-supplier-messages.md` (M)                        | partly  | resolved        | —                                   |
| AI-08 | `ai-automation/issues/08-step-1-proposal-list.md` (M)                     | partly  | resolved        | —                                   |
| AI-09 | `ai-automation/issues/09-steps-3-5-evaluation-and-revision.md` (L)        | partly  | resolved        | —                                   |
| AI-10 | `ai-automation/issues/10-step-6-bod-submission.md` (M)                    | partly  | resolved        | —                                   |
| AI-11 | `ai-automation/issues/11-step-7-bm04-prefill.md` (L)                      | no      | resolved        | —                                   |
| AI-12 | `ai-automation/issues/12-step-8-supplier-confirmation.md` (M)             | no      | resolved        | —                                   |
| ON-01 | `onboarding/issues/01-import-suppliers-catalogue-users.md` (M)            | no      | resolved        | —                                   |
| AI-13 | `ai-automation/issues/13-step-9-item-code-proposal.md` (M)                | no      | resolved        | —                                   |
| AI-14 | `ai-automation/issues/14-step-10-purchase-order.md` (M)                   | no      | resolved        | —                                   |
| AI-15 | `ai-automation/issues/15-steps-11-16-deposit-payment.md` (L)              | no      | resolved        | —                                   |
| AI-16 | `ai-automation/issues/16-step-12-mkt-and-proof-check.md` (L)              | no      | resolved        | —                                   |
| AI-17 | `ai-automation/issues/17-steps-13-15-supplier-files.md` (L)               | no      | ready-for-agent | AI-05, AI-14                        |
| AI-18 | `ai-automation/issues/18-step-17-warehouse.md` (M)                        | no      | ready-for-agent | AI-17                               |
| ON-02 | `onboarding/issues/02-import-open-cases.md` (M)                           | no      | ready-for-agent | ON-01                               |
| AI-19 | `ai-automation/issues/19-case-assistant.md` (L)                           | partly  | ready-for-agent | AI-02, AI-04                        |
| AI-20 | `ai-automation/issues/20-reports-and-acceptance.md` (M)                   | no      | ready-for-agent | AI-05                               |
| UAT-1 | `uat/issues/01-uat-plan-and-training.md` (M)                              | no      | ready-for-agent | AI-05                               |
| UAT-2 | `uat/issues/02-uat-with-elmich.md` (S)                                    | —       | ready-for-human | UAT-1, ON-02, H2, ZL                |

**Next:** the AI layer (Đạt, 2026-10-09: "AI does the work, people only
approve"; spec `supply-chain/ai-automation/spec.md`): AI-01–AI-16 and ON-01
resolved 2026-10-09/10 with their integration tests owed (written, not run: no Docker on that
machine; run them first with `make infra-up && make test-integration`), and the
live model gate: `luna` read case by case 2026-10-09 (three live runs; 8/8 tasks
pass on dataset 1.5.0, before/after in AI-06's Comments), then once more live
2026-10-10 on dataset 1.9.0: 9/9 tasks pass (`evals/gates/luna.json`; steps 9 and
10 ask no model, so their cases are not gate tasks), `qwen` owed; the per-step
tickets (AI-15 onward) are unblocked. H2 (the domain the webhook needs; runbook `docs/deploy/host.md`); Z4p
when a real photo update exists; ZL measures the webhook header (Z3).
Platform hardening and the FCI rerank land from `platform/main` once the
platform repo merges them (2026-10-06).

Live runs that need a person, a phone or a domain are their own
`ready-for-human` tickets (ZL, H2), so agent tickets can close honestly. The
live-run ticket was renamed from Z4 (`04-live-run.md`) to ZL (`07-live-run.md`)
on 2026-10-05 when Z4–Z6 were added.

**Not ticketed yet:** container loading as its own state (step 14), deferred
until QE-15 (QO-6; AI-17 records container number and photos on `pass_qc` until
then). The MKT half of step 12 is AI-16 (E17); email as a channel was decided
against (E18: AI drafts, a person sends).

**Surveys** behind the tickets' "Nguồn" sections: `docs/products/elmich/surveys/`
(2026-10-05), named by content.

## Slice log

| Slice  | Commit    | What                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| ------ | --------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| P, ENV | `c2f04dc` | Port of `dw_supply_chain` onto the current platform; Elmich environment.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| U, Z1  | `51c0ef7` | Per-user login on 3200, `/settings`, Zalo link with a single-use token.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| A      | `dabf5c4` | `required_scope` stamped on an approval, enforced in `decide`, read by `/approvals`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| D      | `51e3400` | `case_documents` (first workspace-narrowed table), own bucket, upload/download, offboarding, orphan sweep.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| S1     | `6dfb1ef` | Product development case, steps 1–5: aggregate, four workspace-narrowed tables, rounds with set-once results, `sc_rnd`, product duty policy, antd list and detail pages.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| PR-02  | `7b411df` | Platform: approvals, runs and audit read only the caller's workspace.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| S2     | `446083e` | BGĐ review at step 6: graph-only approve/reject, decider as actor, idempotent start plus a reconcile lane, `sc_bod`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| A2     | `ae4202b` | A stamped `required_scope` is not satisfied by `platform_admin`; the server computes `can_decide` for `/approvals`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| W      | `82b5c13` | Supply Chain pages on antd in the E-HSDT v3 look: theme, `StatusTag`, `PageHeader`, `RegionState`, context navbar; Playwright viewports owed.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| W-e2e  | e2e       | Playwright for W: `sc-320`/`sc-991`/`sc-992` projects (Tokyo zone on 992), Keycloak sign-in, one product case per width to BGĐ's Zalo code; seven UI defects fixed (labels, clipped titles, lock reasons, nav at 992).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| PM-1   | `6d6459a` | Merge `platform/main` (`c16857c`): hosted rerank (TEI gone), memory/compaction/retrieval hardening, run-less decided event, dev-harness gate.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| PM-2   | `c596a4b` | Merge `platform/main` (`9cc47cf`), upstreamed approval work back as one copy; `96c57d5`: twin migrations idempotent, head `57ca5f1df964`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| PM-3   | merge     | Merge `platform/main` (`96f95ad`): a stamped approval is seen only by its deciders and requester (ADR 0020 sửa đổi 2026-10-07); `raised_by_payload` for the BGĐ dedupe; head unchanged.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Z4a    | `6f491fb` | Inbound Zalo foundation: router and `ChannelCommandRegistry` (empty), message-id dedupe, linked-user AccessContext cut to a ceiling with no role, `channel_preferences` + `/settings` select.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Z4b    | `9852e52` | Chat proposal: `ProductProposalIntent` grounded per message, `proposal_drafts` (workspace RLS, guarded consume in the case's transaction), "Đồng ý" only at the summarised version, ceiling = `propose_scopes`; model in the worker via shared `model_stack` + `DailyAllowance`; eval `supply_chain@1.2.0`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| S3     | `689f841` | BM04 and the supplier's confirmation, steps 7–8: paper on the history row (composite FK, CHECK), bound = when the case reached the step, `sc_supply_lead`, duty policy 1.1.0 with 1.0.0 overrides kept, upload inside the step form.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Z2     | `a57bcba` | Channel outbox: `platform.channel_deliveries` queued by `deliver_notification` for linked recipients only, `channel_delivery` lane (one tx per send, SKIP LOCKED, link/membership re-checked, 1/2/4/8 min backoff, fail at 5, audit per outcome), title+link only; ADR 0013 amended.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Z5     | `9d00e24` | Approve on Zalo after a portal view: receipts + single-use HMAC codes (workspace RLS, principal self-select, lock at 5 wrong tries), `DUYỆT <mã>`/`KHÔNG <mã> <lý do>` first in the router, `decide(channel=, admission=)`, case-version port, `/approvals/[id]`; ADR 0014 amended.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| S4     | `4500ee0` | Item code, SKUs and sign-off, step 9: UNIQUE per tenant by the database (409 by constraint, race-tested), SKU under its case's item code, sign-off graph (own worker) with ordered stamped steps BGĐ → Kế toán from policy 1.1.0, one ensure + extended lane, `sc_finance` signs, coding card on the case page.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| S5     | `3deec0e` | ĐẶT HÀNG and step 10: conditional UPDATE opens one PO case in `order_requested` (race-tested; UNIQUE per product case while QE-12 is open), PIC/Category stamped, `po_case_lines` (workspace RLS), `create_po` command, duty policies 1.3.0/1.1.0 with override migration, end-to-end step 1 → `completed`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| S6     | `ff65682` | SLA policy 2.0 (`categories`, `default`, `by_category`; overrides migrated), Category checked at propose/CreatePOCase and resolved in chat, stage-1 milestones, `follow_ups` for product cases with workspace RLS and per-workspace sweep, `pic` recipient (follow-up policy 1.1.0) re-checked at delivery, `reassign_pic` on both cases, Elmich override script.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| S7     | `024a287` | Stage-1 evals: `supply_chain@1.4.0` (43 cases, +11: proposal prompt containment, propose/step authority through route and handler, approval stamp), each security case red without its guard; graders moved to `dw_supply_chain.testing`, registered in `scripts/run_evals.py`, `dw_evals` forbidden from importing a context; brief/command bar split to S8.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Z6     | `fb56351` | Read-only questions on Zalo: last command, ceiling `po_case.read`, the web's `AnswerCaseQuery` + `CaseQueryRequest`, reply by code (≤10 cases, links, citations, no prices); proposal prompt 1.1.0 `question` hands on; `supply_chain@1.5.0` (50); W2-sees-W1 is P4 (strict xfail).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| P4     | `f670308` | PO cases read only in their own workspace: `62cdcf3bf2d2` narrows `po_cases` and its three tenant-only children, composite FKs with workspace, page indexes with workspace; sweep already per workspace; Z6 xfail gone; `supply_chain@1.6.0` (51); ADR 0017 amended.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| S8     | `86d5393` | Stage 1 in the brief (4 groups, policy 1.1.0 with 1.0.0 overrides kept), summary checks codes and product names, no sample note to the model; command bar and Zalo answer product cases (state, Category, PIC, code; product_case.read); daily report to TP Cung ứng (QE-19 provisional); `supply_chain@1.7.0` (65).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| P2     | `16ee784` | PO-case writes audited in their transaction: create, steps 11–17 (direct and the approval graph's apply), supplier update, delay analysis; `po_case_audit` one format; ports require the event; plan-day refusal pinned per model-calling handler.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| P3     | `42239c2` | Follow-up retention: `992e7c6ae4fe` `prune_follow_ups` (definer, bound tenant+workspace, closed only, ≥1 day), policy 1.2.0 `closed_retention_days` 180 = floor, `supply_chain_follow_ups_retention` lane.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Z3     | `0f60e80` | Zalo webhook: `POST /zalo/webhook` only in webhook mode with a secret, `X-Bot-Api-Secret-Token` constant-time before the body (403), 64 KB by bytes (413), envelope 422 without echo; queued in `channel_inbound_updates` (identity plane), `zalo_webhook_drain` hands it to the same `ZaloInbound.handle`; one mode var for api and worker; `zalo_webhook.py`; ADR 0015 amended.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| PK     | `435bd63` | Step 12 sub-flow on the PO case: `packaging_designs` + history (workspace RLS, order CHECK, report FK), 3 doc types, duties 1.2.0 + override data step, `supply_chain_packaging@1.0.0` gate asked by `AdvancePOCase` and the graph's apply node, Elmich override on, step-12 card; ADR 0021 amended.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| H      | `9b068dc` | Hosted overlay: Caddy 2.11.7 (digest, trivy 0) on `DW_WEB_HOST`/`DW_API_HOST`/`DW_AUTH_HOST`, `/api/*` and Keycloak public paths only, X-Forwarded-* trusted from Caddy's fixed IP (real client IP for rate limits); deployed URLs must be https; realm URLs from `DW_PUBLIC_WEB_URL`; `docs/deploy/host.md`; ADR 0023 amended.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| M2     | `7502a96` | Platform `2ebd50f` merged: one copy of the channel work (platform wording, ADR 0005–0009 twins of 0012–0015/0023); decide command takes subjects + strict prefixes from the product; five twin migrations idempotent, head `c2d7fc0648be`; dev DB migrated in place, each object once.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| HR1    | `2e6e210` | Paged reads: active cases a page at a time (bulk reads bind one page of ids), transitions `Page` newest first, brief reads only the 10 it shows (`entries_shown`), `GET /po-cases/{id}/approvals` filtered on the server (platform `payload_match`); `d9e136c14d83` indexes.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| HR2    | `efef577` | Follow-up sweep audits its own open/resolve as `system:supply_chain_follow_ups` (`lane_audit_event`), in the row's transaction, only for rows it changed; lane registered by `FOLLOW_UP_SWEEP_LANE`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| HR3    | `04bd815` | Step 17's `warehouse_receipt` runs from payment to goods in stock: mapped on `warehouse_receiving` too, its clock starts at the latest entry into `payment_completed` (`sla_clock_start_state`, SQL built from it); ports renamed `*_sla_clock_started_at`; ADR 0019 amended.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| HR4    | `f5ce02a` | Supplier master record `a0035e9faf32`: `suppliers` (workspace RLS, DB-owned `normalize_supplier_name`, generated UNIQUE `normalized_name`, optional `code`), cases reference it by `(supplier_id, supplier_name)` with ON UPDATE CASCADE so the name cannot drift; resolve-or-create in the case write's transaction (audited); backfill; case questions resolve against it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| HR5    | `b88d322` | Backup dumps `dw` and `keycloak` (restore `--latest` per database); `deploy.sh … hosted` adds the host overlay and its health gate asks by service; chat URL build arg gone; approval code secret documented; `/supply-chain/settings` reads and sets SLA and the pre-production rule, locked with reasons without the write scopes.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| HR6    | `935e0f8` | `dw_provisioner` grants have one owner (`platform.grant_provisioner_privileges()`, `f38f027d8342`; users/plans read-only); ADRs 0016–0019, 0021 moved into the package; Playwright removes its `E2E-…` case (`e2e-cleanup`); mypy covers the apps' integration tests; the slow vitest query replaced (11 s → 0.7 s).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| M3     | `4890979` | Platform `ab33703` merged: prompts through the containing registry (5 new versions, delay analysis raw only for days and milestones, hostile-value tests; `supply_chain@1.8.0`); decision audit with channel decisions; SoD second person and role recheck (two Open items closed); `sc_*` role labels; one `@dw/ui` PageHeader/RegionState in this theme; merge head `cee9cf387387`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| AI-01  | `4c71f77` | BM04 and commercial data as fields (`82221a867e62`, not run): `product_profiles`, `po_payments`, supplier contacts and bank accounts (workspace RLS, append-only), PO terms and line prices, `commercial.read                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | write`(redacted in the handler,`{value: null, redacted: true}`), BM04 schema policy 1.0.0 with tenant override, Zalo views disjoint from `PRICE_FIELDS`, `Bm04ProfileCard`/`POCommercialCard`, `MaskedValue`; ADR 0026 amended; integration owed (no Docker). |
| AI-02  | `06fe7bc` | Document extraction lane (`e21dc10d13b5`, not run): `document_extractions` (workspace RLS, FK with type + hash), ids-only definer queue, lane reads under the row's own scope and re-checks it, in-process PDF/DOCX/XLSX/EML text (`dw_knowledge.office_parsers`, + `pypdf`), account numbers masked before the one-call gateway, fields kept only when quote and value are in the text, quotation arithmetic by code; 4 prompts; `supplier_quotation`; eval `supply_chain@1.9.0` (82); ADR 0021 amended (images/MSG unreadable); integration owed.                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| AI-03  | `1e6521f` | Drafts and templates (`cbebad572558`, not run): `document_drafts` (versions per lineage, `content_sha256`) + decisions (workspace RLS), `case_documents.origin`/`draft_id`, tenant template overrides in PostgreSQL; `dw_agent_runtime.doc_templates` (registry, `TenantOverlay`, checked at load) + python-docx renderer behind `DocumentRendererPort` (one pass, no field codes, macros refused); 5 neutral templates pinned in the manifest; `bod_submission`; `DraftsCard`; a draft never satisfies a step; ADR 0025 amended; integration owed.                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| AI-04  | `adbc032` | Skill registry (platform, upstream candidate; ADR 0030): `model.skills` (`TenantOverlay`, `id@^x.y.z`), prompts declare skills and the registry appends them to the system part, `load_shipped_prompts` checks both ways at start (API, worker, evals), skills pinned in the release manifest; 8 supply-chain skills, extraction prompts 1.1.0 declare 4; no Docker-owed test.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| AI-05  | `31237cc` | Step proposals (`1a8527a5b426`, not run): platform `subject_version` (409 on a moved subject) and `required_input` (typed result, web only) in `decide`, `supersede_stale`; policy `supply_chain_step_preparation@1.0.0` (platform empty, Elmich: steps 3–5 physical, step 8); graph + lane `supply_chain_step_preparation` (one stamped approval per step entry, duty scope, drafts reused, `step_preparations` workspace RLS); approval applies drafts as `ai_prepared` documents and the step in one transaction; `StepProposalCard`; ADR 0025 amended; integration owed.                                                                                                                                                                                                                                                                                                                                                                                                                    |
| AI-06  | `6db98eb` | Preparation evals and the model gate: dataset `supply_chain_preparation@1.0.0` (49 cases, 4 extractors + 2 drafters, injection/cross-tenant/cross-workspace/missing evidence/fabricated number/contradiction/unreadable, with and without diacritics), grader `supply_chain.step_preparation`; `model.gates` + `dw_evals.gate` + `scripts/model_gate.py` (live writes `evals/gates/<profile>.json`, mock is never evidence); policy `supply_chain_model_routes@1.0.0` refuses a route to an ungated profile without a live pass, the extraction lane reads routes; placeholder `qwen` profile; live gate owed.                                                                                                                                                                                                                                                                                                                                                                                  |
| AI-07  | `a45d5c7` | Supplier messages (`1dc679326cb5`, not run): `supplier_messages` + `supplier_message_sends` (workspace RLS, append-only, UNIQUE per trigger and per send); lane `supply_chain_supplier_messages` drafts a reminder per open supplier-side follow-up and a sample request / confirmation per step entry for purposes the tenant's step preparation policy lists (Elmich: all three); the model writes cited paragraphs, `domain.grounded_writing` keeps those whose citations and numbers check out, code writes subject, greeting, reply-by and closing from `supply_chain_supplier_messages@1.0.0`; no price, no account number, nothing sent (architecture test); "Đã gửi" records who and which text; `SupplierMessagesCard`; task `draft.supplier_message` in the routes policy 1.1.0 and dataset `supply_chain_preparation@1.1.0` (57); ADR 0029 amended; integration owed.                                                                                                                |
| AI-08  | `1650e40` | Step 1 from a list (`381374b3cb35`, not run): `proposal_lists` (file in PostgreSQL, ≤ 10 MiB), `proposal_list_readings`, `proposal_list_decisions` (workspace RLS, append-only, one reading per prompt, one decision per row), definer queue; lane `supply_chain_proposal_lists` reads a list into cited rows, a Category kept only as a tenant key, priority as a suggestion, findings for codes taken / repeated and names seen; each row becomes a case only through `ProposeProductCase` when the PIC presses; pages "Danh sách đề xuất"; task `extract.proposal_list`, routes 1.2.0, dataset 1.2.0 (67); ADR 0025 amended; integration owed.                                                                                                                                                                                                                                                                                                                                               |
| AI-09  | `1dcfdea` | Steps 3-5 measured (`1021f7fe88f0`, not run): `sample_measurements` (workspace RLS, append-only) and policy `supply_chain_sample_criteria@1.0.0` (Elmich override, provisional thresholds); checklist card and routes; the record's table and the request's items are code's comparison, the model's notes and requirements kept only when grounded (`draft_sample_evaluation@1.0.0`); the next round checks each item of the last request; one approval, three outcomes chosen in the empty conclusion (the comment is the reason), the unchosen request closed; measurements in the proposal's subject; a round's sources bounded to the round (AI-05 fix); the approved request gets a supplier message; Elmich preparation override 1.2.0; routes and dataset 1.3.0 (78); ADR 0025 amended; integration owed.                                                                                                                                                                               |
| AI-10  | `47459f6` | Tờ trình BGĐ (no table): `PrepareBodSubmission` drafts it once per round in the reconcile lane BEFORE BGĐ's review is raised (`EnsureProductApproval.submissions`, payload `bod_submission` or null, a failed drafter blocks nothing); code fills code, name, Category, supplier, the approved record's result and the quotation's price/currency/MOQ with quotes; the model, shown no price, writes summary, risks, recommendation kept only when grounded (`draft_bod_submission@1.0.0`); the approval page shows the draft as the viewer may read it; opt-in `bod_submission` (Elmich override 1.3.0); routes and dataset 1.4.0 (87); ADR 0025 amended; a real runner round owed.                                                                                                                                                                                                                                                                                                            |
| AI-11  | `77900ed` | BM04 bước 7 (no table): `DraftRecipe.BM04` reconciles the case, the quotation and a supplier BM04 (two values disagreeing are a conflict and an empty field), code fills price/currency/MOQ/lead time/Incoterm with quotes, one `draft_bm04@1.0.0` call fills what is left from price-free evidence and is kept only when grounded; `bm04_sources` check; approval writes a `product_profiles` version in the step's transaction (price only for `commercial.write`); Elmich override 1.4.0; routes and dataset 1.6.0 (98); ADR 0026 amended; a real runner round owed.                                                                                                                                                                                                                                                                                                                                                                                                                         |
| AI-12  | `93bdfb6` | Bước 8 (no table): the confirmation email is drafted from the latest `product_profiles` version (no price) with the BM04 attached; `extract_supplier_confirmation_email@1.3.0` reads MOQ, lead time, specification, packaging; `TERMS_MATCH_BM04` compares each term (match / differ / not stated / not in BM04), payload `comparison` carries no price value and masks price numbers elsewhere; the subject binds the BM04 version; Elmich override 1.5.0; routes and dataset 1.7.0 (109); ADR 0026 amended; a real runner round owed.                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ON-01  | `f192aa8` | One-time import (`0f231b1bf02d`, not run): admin screen "Nạp dữ liệu" (dry run, then apply the same file), template from `SHEETS` (`docs/products/elmich/import-template.xlsx`); suppliers by code (a case-made supplier gets its code), contact and bank account through the commercial handlers (account compared by `same_account`, a different one refused, never printed), `catalogue_items` (workspace RLS, UNIQUE NULLS NOT DISTINCT), users through the platform member service (no admin role); `supply_chain.import` for `org_admin`; zip bomb refused; ADR 0027 amended; integration owed.                                                                                                                                                                                                                                                                                                                                                                                           |
| AI-13  | `9b58efb` | Bước 9 by code (no table, no model): policy `supply_chain_item_code_rule@1.0.0` (platform none; Elmich provisional `EL-00001` until QE-11, GET and PUT `/item-code-rule`); `DraftRecipe.ITEM_CODING` fills `official_item_code` (code from the rule after the highest used in the app and the catalogue, one SKU per BM04 variant, quantity only when the line ends with one); `codes_free` names codes another case or the catalogue holds, the subject binds them; approval refuses a taken code (409), then issues the code, adds the SKUs and submits in one save (`version - max(1, steps)`); Elmich override 1.6.0; routes and dataset 1.8.0 (118); ADR 0018, 0025 amended; integration owed.                                                                                                                                                                                                                                                                                             |
| AI-14  | `e11628f` | Bước 10 by code (no table, no model): lane `supply_chain_purchase_orders` drafts one `purchase_order` per PO case awaiting its PO when the tenant's policy says `purchase_order` (Elmich override 1.7.0): the case's own terms and prices first, then the latest BM04 unless step 8's confirmation differs (a conflict, empty); totals and deposit by code (template 1.1.0); Cung ứng and Kế toán told, no price; the PO case page shows code's findings and takes Cung ứng's approval (create_po duty and `commercial.write`, the draft seen, totals code's), one transaction for the draft version, the `purchase_order` document, `create_po`, terms and prices; the PO file needs `commercial.read`; routes and dataset 1.9.0 (126); ADR 0017, 0025, 0026 amended; integration owed.                                                                                                                                                                                                        |
| AI-15  | `7f17d78` | Bước 11, 16 by code (no table; migration `8e2d87208f42` adds three document types): `domain.po_step` generalises AI-14's PO-page pattern (steps turned on by name, `po_steps`; Elmich override 1.8.0), lane `supply_chain_po_steps` drafts the deposit and final payment requests (deposit = total × %, balance = total − deposit recorded; templates `deposit_request`, `payment_request`; account from the supplier master); PI, commercial invoice and UNC read (prompts 1.0.0), the beneficiary account read by code before redaction and kept as a digest; code checks totals, deposit, transfer, currency, account (a changed account is a red finding, never a block) and invoice lines by SKU; Kế toán types the amount paid beside AI's reading, `po_payments` written in the step's transaction; QE-02 policy `supply_chain_po_documents` at all three doors; payment papers need `commercial.read`; routes and dataset 1.10.0 (163); ADR 0021, 0025, 0026 amended; integration owed. |
| AI-16  | (pending) | Bước 12, MKT a minimal user (migration `4527f22c2031`, no table): role `sc_mkt` (viewer, `duty.mkt`, `packaging_document.write` for MKT's four papers only; no price, no Cung ứng step), steps `send_mkt_pack` and `submit_packaging_content` where the tenant's packaging policy 1.1.0 requires them (Elmich), PO duties 1.3.0 with older overrides loaded by `from_stored`; the proof read (`extract_packaging_design@1.0.0`, skill `label_rules@1.1.0`, barcode read by code) and checked against the label rules, the BM04 and the PO's SKUs; lane `supply_chain_packaging_papers` drafts MKT's skeletons from the BM04, colour and design revision requests (Elmich override 1.9.0); routes and dataset 1.11.0 (182); ADR 0028 amended; integration owed.                                                                                                                                                                                                                                  |

## Open — named, not fixed, still true after the port

Carried from the archive's area file; each was true at `5d24c25`. Re-check
after P before acting on one.

- **Follow-ups:** recipients are members of the case's own workspace; each
  sweep re-assesses every active case of every tenant.
- **Vitest prints 6 unhandled "message.error is not a function"** (pages
  rendered outside `<App>` in `po-case-pages`, `signal-pages`, `follow-ups`,
  `approvals-page` tests); 439/439 pass. Seen in HR6. HR4's worker fixture fix
  is `303c720`.
- **Model accuracy is unmeasured;** the mock cannot read.
  `delay_impact_analysis` has no eval case.
- **Provisional domain values:** `SupplierEventType`'s seven values; the
  supplier-update cadence 1d/2d; uniform delay propagation; SLA
  `not_applicable` while interrupted.
- **Suppliers:** a master record exists (HR4) but nothing renames, merges or codes
  one yet; marks are significant ("Đông Á" ≠ "Dong A").

## Decisions recorded, and why

- **Đạt, 2026-10-06:** a review or sign-off notice goes only to the people who
  can actually decide it (they hold both the stamped scope and `approvals.decide`),
  never to someone `decide` would refuse. An approval-only run (no model call)
  counts against the plan's run quota like any run; a tenant out of runs gets its
  review raised by the reconcile lane once runs are available.

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
  (Tạm ở S4: tuần tự BGĐ rồi Kế toán, cả hai bắt buộc, không duyệt thì về Đang tạo mã
  hàng giữ mã; thứ tự là policy `supply_chain_product_approvals` của tenant.)
- **QE-11** Định dạng mã hàng và mã SKU; không trùng trong cả công ty hay từng
  workspace; có đối chiếu với ERP hoặc danh mục hàng không, hệ thống nào cấp mã
  chính thức; một mã hàng có nhiều SKU (màu, cỡ) không; "chốt số lượng SKU" là số
  biến thể hay số lượng đặt mỗi SKU?
  (Tạm ở S4: không kiểm định dạng; không trùng theo tenant, phân biệt hoa thường; SKU là
  biến thể dưới mã hàng; `planned_quantity` tùy chọn; không ERP.)
- **QE-12** Một sản phẩm sinh mấy PO? Một PO gộp SKU của nhiều sản phẩm được không?
  Hàng đặt lại có bỏ qua giai đoạn 1 không, Hàng mới thì sao?
  (Tạm ở S5: một hồ sơ phát triển một Hồ sơ PO, câu cập nhật có điều kiện và UNIQUE
  của database; Hàng đặt lại mở bằng `CreatePOCase`, không qua giai đoạn 1; không gộp
  SKU nhiều sản phẩm. Trả lời "nhiều" thì bỏ UNIQUE bằng migration mới, đổi máy trạng
  thái; ADR 0017 sửa đổi S5.)
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
  mỗi ngày; trong ứng dụng, Zalo hay email? (Tạm ở S8: một thông báo mỗi ngày từ 17:00,
  trong app và Zalo qua Z2, nội dung trên trang brief.)
- **QE-20** Tóm tắt trong tin duyệt, câu trả lời hỏi đáp và ảnh đề xuất đi qua máy chủ
  Zalo. Dữ liệu nào được đi qua (mã, tên SP, trạng thái, tên NCC, giá, chứng từ)? Có quy
  định nội bộ nào cấm không?

- **QE-21** Mẫu thật và 2–3 bản đã điền của: BM04, biên bản đánh giá mẫu, phiếu yêu cầu
  chỉnh sửa, tờ trình BGĐ, PO, đề nghị đặt cọc/thanh toán, phiếu nhập kho (hôm nay dùng
  mẫu trung tính; mẫu Elmich là override của tenant).
- **QE-22** Tiêu chí test mẫu theo Category (chỉ tiêu, ngưỡng) mà R&D đang dùng.
- **QE-23** Điều khoản thanh toán chuẩn với NCC (% cọc, hạn thanh toán, Incoterm, tiền tệ).
- **QE-24** File xuất (Excel) của NCC, danh mục mã hàng/SKU và PO đang chạy cho lần nạp
  đầu; ai xuất.

**Đạt:** none open. QO-1 to QO-8 were decided on 2026-10-06 (Đạt delegated
them: "tự quyết định cho hướng tốt nhất"); see "Decided on 2026-10-06" below.
QA-1 to QA-12 (the AI layer) were decided on 2026-10-09; see below.

### Decided on 2026-10-09 (Đạt: "AI does the work, people only approve")

Asked in the automation audit of 2026-10-09; QA-7 to QA-12 are the lead's
defaults, accepted with the rest.

- **QA-6 What may happen without a person:** AI prepares (drafts, reads supplier
  files, runs checks) and raises an approval "move to step X with documents Y"; the
  step changes only when a person with the action's duty approves (web, or Zalo with
  the portal code). Physical work (testing, paying, counting) stays human: AI
  pre-fills the record with a suggestion beside an empty result field; the person
  enters the result and approves (ADR 0025, E14).
- **QA-2/3 Suppliers:** AI drafts every supplier message; a person copies and sends
  it; replies are dragged into the case and AI reads them. No mailbox, no outbound
  email (E18).
- **QA-4/5 Model:** hosted `luna` profile for drafting and reading files; document
  contents may go to the provider; bank account numbers are compared by code and
  never sent to a model (ADR 0021 amended 2026-10-09). Qwen only for a task that
  passes its eval gate (AI-06).
- **QA-1 Templates:** neutral templates first; Elmich's real ones later as a tenant
  override (QE-21).
- **QA-7 Onboarding:** one-time Excel import of suppliers, catalogue, users and open
  cases at their current state with one `import` history row (E16).
- **QA-8 MKT:** a minimal user at step 12: receives the pack, uploads content,
  confirms (E17).
- **QA-9 BM04:** a form in the app rendered to a template; upload kept as fallback
  (E15).
- **QA-10 Commercial data:** prices, amounts and terms stored behind
  `supply_chain.commercial.read|write`, never in Zalo (E15).
- **QA-11 Results:** AI suggests beside an empty result field; the person chooses
  and approves.
- **QA-12 Scope:** the whole of steps 1–17, not a PoC subset.

### Decided on 2026-10-06 (Đạt delegated QO-1 to QO-8)

- **QO-1 Database.** Elmich runs on its own database (`dw_elmichs` locally, its
  own instance when hosted). This chain is never run against the sales-dev
  database, whatever that database has run; nothing here needs its data.
- **QO-2 ADRs 0011–0023:** Accepted, with their dated amendments.
- **QO-8, the provisional choices of A, D, S1, S2:** kept, with one reversal:
  a **stamped `required_scope` is not satisfied by `platform_admin`**. A
  platform operator is not Elmich's BGĐ; a business approval needs the scope
  itself (human-in-command). Ticket A2 (`approval-decider-scope/issues/02`).
  Everything else stands: documents and product cases are workspace-narrowed
  while `po_cases` stays tenant-only (narrowed too by P4, 2026-10-07); `proposal_code` unique per tenant;
  supplier from step 2; Category free text until S6; `sc_rnd` without the
  exceptions duty; product duties in their own policy.
- **QO-3 SMTP:** the customer's own mail domain, set per deployment from env
  (realm SMTP is configuration, never committed). Until it is set, password
  resets go through an administrator; local development sends no mail.
- **QO-4 Login page:** Elmich's own branding (logo, name, the v3 colours this
  repo's web uses), as a Keycloak theme in this repo. A product repo per
  customer brands per customer.
- **QO-5 Upstream:** continuous, not batched. A fix to a platform package lands
  in `codebase` first and comes back here with `git merge platform/main`; a
  product's design (theme, page layouts) stays in the product (Đạt, 2026-10-06:
  "design này theo từng dự án").
- **QO-6 Step 14:** deferred as its own step until Elmich answers QE-15;
  container loading stays inside `qc` → `in_transit`.
- **QO-7 Zalo approvals at steps 6 and 9:** the approver writes the comment on
  the portal when the page issues the code (they must open the portal to see
  the code anyway); Zalo carries `DUYỆT <mã>` or `KHÔNG <mã> <lý do>`. The code
  is **6 digits**, valid **10 minutes**, single use; **5 wrong tries** lock that
  code. ADR 0014 amended.
- **Accounting documents (QE-02 addition):** `deposit_docs`, `payment_docs`
  and `purchase_order` are accounting records, which Vietnamese law (Luật Kế
  toán 2015, Nghị định 174/2016) keeps at least 10 years; the platform never
  deletes a case document on its own (documents leave only when a tenant is
  offboarded, and the offboarding bundle hands them back first). The duty to
  keep them rests with Elmich as owner of the records; ADR 0021 amended.
- **Monthly volume** (how many new products and POs) is not a process question
  and is not asked of Elmich for the build.

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
  Chi; PO-DEMO-001..004), `scripts/keycloak_dev_users.py`, `make dev`. The
  Playwright spec removes its own `E2E-…` case; `seed_supply_chain_demo.py
e2e-cleanup` removes what a crashed run left.
- **Test database:** supply chain uses `dw_test_supply_chain`, recreated per
  pytest session; an RLS mutation check goes into the migration file.
- **Windows:** `next build`'s standalone copy fails with EPERM here (CI builds
  on ubuntu); mutation scripts need `encoding="utf-8"` and a green control
  first.
