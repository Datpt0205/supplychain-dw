# Agent runtime and memory milestones (Mốc 0–6)

> **Inherited from `codebase`** (platform seed, `main` `bf553f4`, merged into this
> product repo as `platform/main`). It changes here only when work in this repo
> touches the area; after each `git merge platform/main` the upstream copy is the
> reference. Product work lives in `.claude/plans/supply-chain.md`.

The platform's own agent runtime, built before any bounded context existed.
The full narrative is at `git show 84e3f6a:.claude/PLAN.md`, sections "Mốc 3",
"Next after that", "Decisions still open" and "Done".

| Mốc | Commit    | What it changed                                                            |
| --- | --------- | -------------------------------------------------------------------------- |
| 0   | `4d45cf5` | `build_agent()`: one place a platform agent is assembled                   |
| 1a  | `1cda019` | Spend ceiling on the agent loop; the ledger no longer leaks                |
| 1b  | `b3b556f` | Context compaction that is recorded, bounded and fails open                |
| 2   | `eb25931` | Autonomy A0–A4 decides approval; tenant ceiling; policy stamped on the run |
| 3   | `d0883fe` | Recall: a run remembers what it learned about the record it is on          |
| 4   | `80d849f` | Provenance as a chain the database enforces                                |
| 5   | `ed441d8` | Sub-agents narrower than their parent, sharing one spend ceiling           |
| —   | `643236f` | Invariant checker + commit gate + the security-review skill                |

Mốc 6 (running many customers) is half done:

- **Done:**
    - retry on the agent path;
    - `cancel_thread` over HTTP, ownership checked under RLS;
    - provider fixtures, now tested.
- **Provider fallback:** deliberately not built, because it is LiteLLM proxy
  configuration.
- **Daily spend:** the per-tenant cap that read `platform.model_usage_ledger`
  was removed along with that table. Its successor is Ops hardening phase 3's
  spend guard, whose quotas are still unset.

## Memory, as it stands

- **Recall** matches on `subject_refs` overlap. Similarity only orders that
  set, never decides it, so a stale or poisoned index can give a worse order
  but never a wrong answer.
- **Supersession:** two live memories with one `fact_key` and one subject are
  two answers to one question, and the later one closes the earlier. Rows are
  closed, never deleted.
- **Writes are asynchronous:** the outbox handler for
  `memory.candidate_proposed` calls `propose` with the event id as the
  idempotency key, and tenancy comes from the envelope, not the payload.
- **What a write trusts** (2026-10-06, `memory-write-trust`): confidence is
  the policy's, from how many distinct documents are cited (two to auto-write;
  payload `1.1` cannot carry one). Evidence must come from the caller's
  workspace or a global document, and the item may not claim a lower
  classification than its documents.
- **Held for review** (2026-10-06, `memory-review-queue`): a REVIEW candidate
  has its evidence and label verified (savepoint, rolled back) and opens a
  `memory.review` approval, run-less, payload identifiers and label only.
  `decide` writes `memory.review.decided` to the outbox; the worker's handler
  calls `settle_review`, which writes the item through the same
  `_store_item` as an auto-write (evidence recorded and re-checked then) or
  records the refusal. `memory.` is strict, and a decider's clearance must
  cover the stamped label (`decision_guards`). Content is read at
  `GET /memory/candidates/{id}` under `memory.read` and clearance. The fixed-set columns have CHECKs
  pinned to the enums by a catalog test; memory retention has one class,
  `default`.
- **Compaction and checkpoints** (2026-10-06, `compaction`): what compaction
  removes is summarised whole, in chunks no larger than the summary route's
  `max_input_tokens` (a route used as summariser must declare it; build
  refuses otherwise), a tool call never split from its result, the previous
  summary (`dw_system_generated`) handed to the first call as the anchor it
  updates (`context_summary_update_prompt`, runtime copy 1.6.0) and each
  chunk's summary to the next. `_budget.check`/`record` per call; any chunk
  failing, or one message larger than the budget, compacts nothing. Run
  checkpoints are pruned by the worker's `checkpoint_retention` lane
  (`SqlCheckpointRetention`, `retention@1.6.0.yaml` `checkpoints`: 7 days for
  a thread's non-newest checkpoints, 730 for an idle thread), only on threads
  with a finished run visible and no unfinished one, so a missing
  `worker_drain_worker_runs` policy stops the sweep instead of emptying a
  paused run. A thread with no run row is left for offboarding.
- **Retrieval and the agent prompt** (2026-10-06, `retrieval-correctness`):
  `SearchQuery.document_ids` is a `MatchAny` on `source_document_id` in the
  same `must` as the trusted conditions, so it narrows before top-k (payload
  index added). One embedding builder,
  `dw_knowledge.adapters.embedding_factory`, used by API and worker;
  `ConfigError` lives in `dw_kernel.errors` (re-exported by the registry). The
  agent-loop prompt is a registry artifact pinned on `WorkerDefinition`
  (`agent_prompt_id`/`_version`), rendered per call for the run's tenant, and
  the loop's spend is billed under that pin (a plain graph under
  `graph:<worker_id>@<graph_version>`); `0.0.0` is gone. Open: the pin is not
  on the `worker_runs` row, and `AgentSpec` restates it rather than reading
  the definition.
- **Run-less `.decided` events with no handler (found 2026-10-06, not fixed):**
  every run-less approval now writes `<type>.decided`; the worker claims only
  the types it has handlers for, and nothing deletes outbox rows. A product
  whose run-less approval type registers no handler leaves one unclaimed row
  per decision, invisible to the backlog metric (which counts handled types).
- **Outbox (found on the way, not fixed):** `_deliver` treats
  `UndeliverableEventError` like any other error (retried to `max_attempts`),
  so the class records a reason but does not stop retries, which its
  docstring says it does.

## Open

- **Decided 2026-10-06 (Đạt delegated the calls):** run checkpoints keep 7 days
  of a thread's older checkpoints and 730 days for an idle thread; `memory.`
  approvals are strict (a second person and a comment); a memory auto-writes
  only on two distinct cited documents. All three are the shipped defaults.

- **CI: the three run-state announcement timeouts were a plugin race**
  (fixed 2026-10-02). pytest-asyncio (auto mode) and anyio's pytest plugin both
  wrap async fixtures; the one registered last wins, and registration follows
  the order of site-packages. On the runner image of 2026-09-20 anyio won for
  modules marked `pytest.mark.anyio`, so the LISTEN fixture ran on a loop that
  was not running and never heard the NOTIFY. Reproduced locally by forcing
  the order (3 failed), green after the fix in both orders (4 passed); the
  product code was never at fault. The anyio plugin is now off in `addopts`
  (`-p no:anyio`) and the marker is gone from four modules, so a new
  `pytest.mark.anyio` is a collection error under `--strict-markers`.
- **Tenant-schema guards read the catalog** (2026-10-02).
  `test_rls_coverage.py` and the USAGE test in `test_privileges.py` find every
  non-system schema with a `tenant_id` column instead of naming three, so a
  context's new schema is checked the day it exists (failure-modes #0). Still
  named by hand: `0001_platform_grants.sql` grants USAGE and default table
  privileges on `platform, knowledge, memory` only, so a new schema's migration
  ships both itself. The USAGE test catches a missing USAGE; missing table
  grants are caught only by the context's own tests running as `dw_app`.
- **Offboarding reads every workspace of a tenant** (2026-10-03,
  `platform-runtime/workspace-scope-offboarding/`, ticket 01 done). The lane sets
  `app.workspace_scope = 'tenant'` per transaction; `test_rls_coverage.py` fails a
  policy that reads it outside `tenant AND (workspace OR scope)` and a
  workspace-narrowed table that does not read it. Needed before the first
  context narrows its tables by workspace. Found on the way: offboarding loses
  in-app notifications unexported (`ops-hardening.md` Open).
- **Approval decisions audited; approvals, runs and audit read by workspace**
  (`platform-runtime/approval-audit-and-workspace/`): HITL-11 and the rest of
  TEN-04. Ticket 01 **resolved 2026-10-08** (`feat/security-debts`): every
  decision writes `approval.decided` (or, admitted by a channel code,
  `approval.channel_decided`) in its own transaction; `dw_app` lost UPDATE on
  `approval_decisions` (`ecb47f78702c`). Ticket 02
  **resolved 2026-10-06**, upstreamed from the first product's repo: the
  repository narrows approval, run and audit reads to the caller's workspace
  (RLS unchanged, still tenant-only), `decide` resumes in the run's own
  workspace, `GET /runs/{id}` checks `runs.read`, the audit route checks
  `audit.events`, cursors carry the workspace, keyset indexes carry
  `workspace_id` (`6d4aed20ccf2`; twin of this repo's `cbf765d02a12`, and
  `36dabf47619c` of `5d3965984679`, all four idempotent). Open: whether the
  UoW should own the workspace instead of a required read argument.
- **Tickets written for the first product, platform side** (2026-10-03, all
  `ready-for-agent`, product-neutral), each under `platform-runtime/<folder>/`:
  `scope-holder-check` (ask whether a user holds a scope without an
  `AccessContext`, for apply-time checks and worker lanes), `eval-grader-registry`
  (a context registers its own graders), `approval-inbox-link` (`/approvals`
  links a context's approval to that context's own inbox), `nav-any-scope` (a
  menu item shown for any of several scopes), `support-access` (support access
  the customer grants, ADR 0008), `tenant-members-and-invitations` (tenant-wide
  users, per-workspace roles, invitations).
  `eval-grader-registry` ticket 01 done here product-side on 2026-10-07 (Elmich
  S7): `run_dataset` takes the table, `grader_table()` in `scripts/run_evals.py`,
  `dw_evals` forbidden from importing a context. Upstream candidate.
- **A missing bearer token answers 403, not 401** (2026-10-05,
  `platform-runtime/unauthenticated-401/`, ticket 01 `needs-triage`):
  `bearer_token` raises `PermissionDeniedError` on every route. Found by Elmich
  Z1, whose route criterion was amended to the real behaviour.
- **Harness hardening, from the 2026-10-06 harness audit** (all
  `ready-for-agent`, product-neutral, in this order), each under
  `platform-runtime/<folder>/`: `memory-vectors` (Qdrant memory points deleted
  by retention, supersession and offboarding; the ranker reorders only the ids
  SQL recalled; **resolved 2026-10-06** in `55bd789`),
  `memory-write-trust` (classification from the cited documents,
  workspace-checked evidence, confidence computed by code, CHECKs, retention
  classes nothing can assign removed; **resolved 2026-10-06**), `memory-review-queue` (REVIEW becomes a
  `memory.review` approval; **resolved 2026-10-06**; the decision audit and
  the workspace narrowing of `decide` stay with `approval-audit-and-workspace`
  01–02; here 02 is done (above) and covers `memory.review`, 01 is open),
  `compaction` (long tool loops and multi-turn threads really compact, budget
  from the profile, checkpoint retention; **resolved 2026-10-06**),
  `retrieval-correctness`
  (`document_ids` inside the Qdrant filter, one embedding builder, a pinned
  agent-loop prompt; **resolved 2026-10-06**), `dev-harness` (commit gate for PowerShell and
  `git -C`/`-c`, per-session Stop hook, plugin wording, `ui-quality.md`;
  **resolved 2026-10-06**: `scripts/test_claude_hooks.sh` in `make ci` and the
  CI python job, green on Git Bash without jq and on Alpine with jq; a real
  PowerShell-tool commit was blocked in a nested session. Not done: no
  `permissions.deny` on the gate switches, because a shell `touch` gets past it,
  so it is gitignore plus a human-only note; the new CI step has not been
  watched on GitHub; the Stop hook cannot tell which session made a commit).
  Dropped as already true here: the rerank outage fallback (`4cb45dc`), and
  offboarding purging run checkpoints (catalog discovery already does; the
  `compaction` ticket only pins it with a test).
- **Upstreamed from the first product's repo** (2026-10-06, branch
  `feat/upstream-elmich-platform`): who may decide an approval is stamped on
  it as `required_scope` and enforced in `decide`; `platform_admin` does not
  pass a stamp (`docs/adr/0004`, migration `36dabf47619c`); the inbox reads
  `can_decide` from the same checks. The resume payload carries `decided_by`
  from the decider's verified context. A multipart route claims its
  idempotency key from parsed fields (`get_form_idempotent_operation`,
  `claim_fields`); `PAYLOAD_TOO_LARGE`→413, `UNSUPPORTED_MEDIA_TYPE`→415.
  Not taken: the product's contexts, migrations, channel code and web look.
- **Who sees a stamped approval** (2026-10-07, branch
  `feat/approval-visibility`, ADR 0004 amendment): a stamped request is listed
  and served only to who may decide it and to its requester; everyone else,
  `platform_admin` without the stamp included, gets 404 / absence, the
  decision included. `ApprovalAudience` owns the rule, the repository filters
  by it in SQL (`visible_to`, required on `get` and `list_pending`) and
  `SqlPendingApprovalQuery` now takes the composition root's
  `ScopeAuthorizationService`. Unstamped requests are unchanged.
- **`build_agent` has no production caller** (checked 2026-09-29): this
  checkout ships no bounded context.
- **Platform pieces waiting for their first context** (failure-modes #1).
  They were built with the Supply Chain context as their first caller, and
  that context was removed on 2026-09-29. Each keeps its own tests, and each
  is kept because the next product needs the same thing:
    - `SingleCallModelGateway`: a one-call model run that frees its
      spend-ledger entry;
    - `SqlPolicyOverrideRepository` / `platform.policy_overrides`: a
      tenant's own copy of a policy document;
    - `SqlPendingApprovalQuery`: a context counting its own pending
      approvals by type prefix;
    - `scope_holders.py`: `holding` and `holds` (who holds a scope, for
      routing work to people); only `scopes_of` has a caller (support grants);
    - `required_scope` on an approval: no platform node stamps one; a
      context's graph puts it in its interrupt payload;
    - `decided_by` in the resume payload: no platform graph reads it;
    - `get_form_idempotent_operation` / `claim_fields`, and
      `PayloadTooLargeError` / `UnsupportedMediaTypeError`: no platform
      route takes a form or raises them (a unit test mounts a probe route);
    - `platform.deliver_notification()`: the one way into a member's inbox.
      The bell, the API and retention are wired. First sender (2026-10-05):
      the Zalo link store tells a person when a chat is linked to, moved off,
      or unlinked from their account (`zalo_link_repo.py`, Elmich Z1).
      A context that adopts one wires it in `bootstrap/wiring.py` and names it
      here as taken.
- **Nothing emits `memory.candidate_proposed`.** A bounded context has to
  decide what is worth remembering; the platform ships only the consumer.
- **The GIN index on `subject_refs` is untuned.** The planner prefers
  `ix_items_page` on an empty table, and tuning it before real data exists
  would be a guess.
- **Vector-ranked recall is not wired**, although Qdrant and `EmbeddingPort`
  already serve knowledge.
- **The chat path reads the platform profile, not the tenant's.**
  `OpenAICompatibleChatModelFactory.resolve` calls `chat_route` with no
  tenant while the agent budget prices the tenant's route: the shape the
  structured gateway had until 2026-09-28. No agent loop runs in production
  yet, which is why it waits.
- **The release manifest does not pin model profiles.** They carry
  `routing_policy_version` but no run records which profile it used, except
  the usage recorders (now the effective id).
- **`make check-deepgram` and `make check-search` run scripts that do not
  exist**, left over from the product this was extracted from.
  `make check-model` was the third, and works since 2026-09-28.
- **Reranking is a hosted API, and self-hosted TEI is gone** (2026-10-06).
  `DW_API_RERANK_PROVIDER=cohere_compatible` calls `POST {base_url}/rerank`
  (FPT Cloud AI Marketplace, `bge-reranker-v2-m3`); `make check-rerank`
  probes it live. A reranker that fails costs ranking, not the search: the
  gateway keeps the vector order, logs `rerank_skipped`, leaves the
  `dw.knowledge.rerank` span in error and counts
  `dw_retrieval_rerank_skipped_total{error}`. A short or empty answer, a URL
  the client cannot use (no scheme, credentials, plain http when deployed)
  and a call past `DW_API_RERANK_TIMEOUT_SECONDS` as a whole are each refused
  rather than taken as a ranking. Open: a skipped rerank stores the vector
  score as `relevance_score`, a different scale from the reranker's; harmless
  while every caller asks `min_relevance=0`, not once one asks more.
  An unknown `embedding_provider` (the retired `tei` included) now stops
  startup instead of quietly becoming hash vectors. The key is only in local
  `.env`; uat/production have none yet.

## Security debts (2026-10-08, `feat/security-debts`)

Six debts, each its own commit and ticket under `platform-runtime/<folder>/`;
Đạt delegated the open calls, decided provisionally (fail closed) and
recorded in each ticket.

- **`prompt-containment/01`:** `PromptRegistry` wraps every interpolated
  value in an escaped `<input name>` block; `raw_variables` with a reason
  opts out (ADR 0010). Open: the compaction summary prompt is copy, not a
  registry artifact, and is not covered.
- **`approval-audit-and-workspace/01`:** above.
- **`system-actor/01`:** a lane audits as `system_actor(<lane>)`
  (`lane_audit_event`, `append_across_tenants`, ADR 0011); memory expiry and
  knowledge hard delete now audited, channel delivery no longer names the
  recipient as actor.
- **`sod-waiver-second-person/01`:** a waiver lifts nothing until a second
  admin confirms it; a role or permission set cannot gain a scope that puts a
  membership in breach (`f381f1694395`, ADR 0012). Open: a rule's own scopes
  changing under memberships.
- **`unauthenticated-401/01`:** missing or unverifiable bearer is 401 with
  `WWW-Authenticate`; the web bounces only a request that carried a token.
- **`channel-delivery-expiry/01`:** a delivery pending past
  `retention@1.7.0.yaml` `channel_deliveries.pending_expiry_days` (7) fails
  as `channel_unconfigured`, audited (`983b509c3f0f`, ADR 0006 amendment).

## Platform tickets (2026-10-08, `feat/platform-tickets`)

Đạt delegated the open calls; each is decided provisionally and recorded in
its ticket's Comments.

- **`scope-holder-check/01`, `/02`:** `SqlScopeHolders.holds(tenant, ws,
user, scope)` and `holding(tenant, ws, scopes)`, neither needing an
  `AccessContext`, both reading one membership query (`_members`) and
  `effective_scopes`. Integration-tested on `dw_app`, mutation-checked.
  Still no production caller (the "waiting for their first context" list
  above).

- **`support-access/01`:** customer-granted support grants (ADR 0024,
  `af8ee878b4ab`): `support_staff`, `support_grants` with the status machine
  in a trigger, a membership trigger refusing support staff on every path,
  `SupportScopeCatalog` (empty on `main`), `SupportGrantService`,
  `/support/*` and `/platform/support-*`. Refusals carry
  `details.reason_code`. `SqlScopeHolders` now has its first caller
  (`scopes_of`, the granter's current scopes). Open: the `/platform` console
  tables (web), conflict of interest at assignment.

- **`tenant-members-and-invitations/01`:** `GET /admin/members` (tenant-wide,
  `vi-VN-x-icu` order, measured present), `PUT /admin/members/{id}/memberships`
  (administrative roles kept, `plan_memberships`), `POST /admin/invitations`
  (user without a sign-in, `status=invited` until the first sign-in links by
  email), `status` on `/directory/members` from one SQL expression. No email
  is sent (P1). Open: email linking before customer SSO is brokered.

## Deliberately not taken

- **Vector or graph recall deciding the recalled set.**
- **LLM-decided memory merging:** the model proposes, code decides.
- **An `AGENTS.md`-style memory file** the model edits. Structured rows carry
  provenance, supersession, audit and RLS, and a file carries none of them.
- **`create_deep_agent`'s builtin file and shell tools.** `build_agent` uses
  `create_agent`, which installs none; `OfferedToolsOnlyMiddleware` guards a
  context that reaches for them anyway.
