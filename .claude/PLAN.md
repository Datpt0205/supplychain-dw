# Plan — index

`.claude/hooks/session-start.sh` reads this file whole at session start and
warns past 80 lines, because an index that grows stops being read. It holds
four things: where each area stands, what is next, what is still owed, and how
a feature is checked. Detail lives in the area file; history lives in git.

## Areas

| Area                                      | File                                | State                    |
| ----------------------------------------- | ----------------------------------- | ------------------------ |
| Supply chain (Elmich), this product       | `.claude/plans/supply-chain.md`     | Port running; 25 tickets |
| Ops hardening (inherited from `codebase`) | `.claude/plans/ops-hardening.md`    | Done; numbers owed       |
| Agent runtime, memory (inherited)         | `.claude/plans/platform-runtime.md` | Done but for named gaps  |
| Web UI shell, Ant Design v6 (inherited)   | `.claude/plans/web-ui.md`           | Shell done; 03–09 next   |

Inherited areas came with the platform seed (`codebase` `main`, `bf553f4`) and
change here only when work in this repo touches them; their upstream copy is
the reference after each `git merge platform/main`.

## Now (2026-10-05)

- **This is the Elmich product repo** (`supplychain-dw`), built on the
  platform seed. Everything is built here; generic pieces stay in platform
  packages to upstream later (ADR 0011). Scope: Part A of Elmich's process,
  17 steps (`docs/products/elmich/process.md`).
- **Steps 10–17** come from the archived context (port, slice P, in its first
  build session). **Steps 1–9** are designed (ADR 0016–0021) and ticketed.
- **Zalo is two-way** (Đạt, 2026-10-05): chat proposal at step 1 (Z4),
  approvals at steps 6 and 9 by a one-time code seen on the portal (Z5, ADR
  0014 revised), read-only questions (Z6).
- **Done in the first build session (2026-10-05):** port P, ENV, login and
  `/settings` (U), Zalo link (Z1). `make ci` green.
- **Done on branch `feat/elmich-a-d-s1` (2026-10-06, not pushed, not merged):**
  A (`dabf5c4`), D (`51e3400`), S1. Steps 1–5 run end to end in the app.
- **2026-10-06:** platform-runtime 02 (`7b411df`) and S2 done. In progress:
  platform hardening and the FCI rerank in the platform repo, then merged here;
  then Z4 (Z4a, Z4b; photos split to 04b, waiting for a real photo update),
  S3 → S7, Z5, Z6.
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
  Zalo).
- **Đạt, product:** QO-1–QO-8 in `supply-chain.md` (sales-dev database and the
  re-pointed migrations; review ADR 0011–0023; realm SMTP; login branding;
  upstream timing; deferring step 14's container loading; the comment a strict
  approval needs in Zalo, code length and expiry; the provisional choices made
  in A, D, S1).
- **Đạt, inherited from the platform:**
    - a plan quota on direct model calls; the model profile and key for
      uat/production; whether CI runs the web vitest and Playwright suites;
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

`.claude/rules/failure-modes.md` holds the counts behind layers 1–3; UI work
also answers to `.claude/rules/ui-quality.md`. No layer replaces running the
thing.
