# Plan — index

`.claude/hooks/session-start.sh` reads this file whole at session start and
warns past 80 lines, because an index that grows stops being read. It holds
four things: where each area stands, what is next, what is still owed, and how
a feature is checked. Detail lives in the area file; history lives in git.

## Areas

| Area                                       | File                                | State                     |
| ------------------------------------------ | ----------------------------------- | ------------------------- |
| Supply chain (Elmich), this product        | `.claude/plans/supply-chain.md`     | 1–17 run; AI layer next   |
| Ops hardening (inherited from `codebase`)  | `.claude/plans/ops-hardening.md`    | Done; numbers owed        |
| Agent runtime, memory (inherited)          | `.claude/plans/platform-runtime.md` | Done but for named gaps   |
| Web UI shell, Ant Design v6 (inherited)    | `.claude/plans/web-ui.md`           | antd everywhere, CSP done |
| Chat channels, Zalo (upstreamed from here) | `.claude/plans/channels.md`         | Upstreamed; live run owed |

Inherited areas came with the platform seed (`codebase` `main`, `bf553f4`) and
change here only when work in this repo touches them; their upstream copy is
the reference after each `git merge platform/main`.

## Now (2026-10-09)

- **This is the Elmich product repo** (`supplychain-dw`), built on the
  platform seed. Everything is built here; generic pieces stay in platform
  packages to upstream later (ADR 0011). Scope: Part A of Elmich's process,
  17 steps (`docs/products/elmich/process.md`).
- **Steps 10–17** come from the archived context (port, slice P, in its first
  build session). **Steps 1–9** are designed (ADR 0016–0021) and ticketed.
- **Zalo is two-way** (Đạt, 2026-10-05): chat proposal at step 1 (Z4),
  approvals at steps 6 and 9 by a one-time code seen on the portal (Z5, ADR
  0014 revised), read-only questions (Z6).
- **Done 2026-10-05/08** on `feat/elmich-a-d-s1`: P, ENV, U, Z1–Z6, A, D, S1–S8,
  W, P2–P4, PK, H, HR1–HR6; steps 1–17 run end to end (slice log in the area file).
- **AI layer (Đạt, 2026-10-09):** AI prepares each step (drafts, reads supplier
  files, checks) and raises "move to step X with documents Y"; a person approves
  (ADR 0025–0029, E14–E18). Tickets AI-01–AI-20, ON-01–02, UAT-1–2 in
  `supply-chain/ai-automation/`, `onboarding/`, `uat/`. AI-01–AI-11 resolved
  2026-10-09 (integration owed: run `make test-integration` first; `luna` gate ran
  three times, 8/8 tasks pass on 1.5.0, per case in AI-06; `qwen` owed). Next: AI-12 onward.
- **Run locally:** `make infra-up`, `make db-migrate`, seed
  (`DW_API_PROFILE=local uv run python scripts/seed_supply_chain_demo.py seed`,
  then `scripts/keycloak_dev_users.py`), `make dev`, open http://localhost:3200.
  Ports: this repo 2xxxx/8200/3200; codebase 1xxxx/8000/3000; dw-proterial
  3xxxx/8300/3300. One bot, one poller: never poll the same Zalo bot from
  sales_dw and here at once. Platform updates: `git merge platform/main`.

## Decisions owed

- **Elmich:** QE-01–QE-20 in `supply-chain.md` (SLA numbers and clocks,
  documents per step, revision loop, sign-off order, item code and SKU format,
  categories, PIC reassignment, Zalo events, what case data may pass through
  Zalo); QE-21–QE-24 (real templates, sample criteria, payment terms, import files).
- **Đạt, product:** none open; QO-1–QO-8 (2026-10-06) and QA-1–QA-12
  (2026-10-09, AI layer) decided, see `supply-chain.md`.
- **Đạt, inherited from the platform:**
    - a plan quota on direct model calls; the model profile, model key and
      rerank key for uat/production; whether CI runs the web vitest and
      Playwright suites; how many independent documents a memory needs to be
      written without review (`auto_write_sources`, 2 since 2026-10-06);
    - spend guard thresholds per plan; a retention term for offboarding
      bundles; how many superseded document versions to keep;
    - backfill ADRs: code cites ADR-001..003, which this repo never had.

## How a feature is checked here

1. `scripts/verify_invariants.py`: mechanical checks, in CI and the commit hook.
2. `.claude/hooks/pre-commit-gate.sh`: layer 1, then the questions this diff
   earns.
3. `.claude/skills/reviewing-feature-security/`: six trust boundaries, a
   negative test at each, a mutation check; run before calling a feature done.
4. `.claude/skills/reviewing-deployment-security/`: deployed-profile exposure,
   secrets, CORS, outbound URLs, and a scan of every new image.
5. `mattpocock-skills` (`/ask-matt`): grilling to `/implement` and
   `/code-review`; `CLAUDE.md` "Agent skills" places layers 1–4 inside it.
   Installed per checkout, not by the repo: check with `claude plugin list`.

`.claude/rules/failure-modes.md` holds the counts behind layers 1–3; UI work
also answers to `.claude/rules/ui-quality.md`. No layer replaces running the
thing.
