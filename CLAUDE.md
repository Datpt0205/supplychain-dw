# Architecture of record — Digital Worker Platform

This repository is a **platform backbone**, not a product. It carries everything
that is not a business domain: the agent runtime, tenancy and authorization,
knowledge, memory, connectors, observability, evaluations, and the api/worker/
sandbox/web shells. A product adds one or more bounded contexts as packages
under `packages/python/dw_<name>` and inherits the rest.

This file is the architecture of record. Do not silently deviate from it —
record a decision instead.

## Adding a bounded context (the plug-in points)

Run `make new-context NAME=<name>` rather than working the list by hand:
`scripts/new_context.py` owns all fifteen places, and the `scaffold-smoke` CI
job generates a throwaway context on every push so a seam that moves fails there
instead of in someone's first week. The list below is what it does, kept because
a generator whose steps nobody can read is a different kind of unchecked claim.

1. Package `packages/python/dw_<name>` with layers
   `domain/ application/ workflows/ adapters/ presentation/`.
2. Register in the root `pyproject.toml`: `tool.uv.sources`, ruff
   `known-first-party`, mypy `files`/`mypy_path`, coverage `source`, and
   import-linter `root_packages` — plus an `independence` contract the moment a
   second context exists.
3. Wire it at the marked seams: `apps/api/src/dw_api/bootstrap/wiring.py`
   (build from `container.runtime`, the published `RuntimeSeam`),
   `apps/api/src/dw_api/main.py` (mount the router), and
   `apps/worker/src/dw_worker/main.py` (register consumers and reap targets).
4. Ship `configs/workers/<name>.yaml`, prompts under
   `configs/prompts/<name>/<id>@<semver>.yaml`, tool specs under `configs/tools`,
   a toolset under `configs/toolsets`, and policies under `configs/policies`.
5. Add an eval dataset under `evals/datasets/` with FULL security coverage
   (prompt injection, cross-tenant attack, missing evidence) and graders keyed
   `<name>.<gate>` in the context's own `dw_<name>.testing` package, registered
   at the marked seam in `scripts/run_evals.py` (the eval composition root).
   `dw_evals` never imports a context; import-linter forbids it.
6. Add Alembic migrations continuing from the baseline (`0001`).
7. Update `scripts/verify_architecture.py` (`IMPORT_TO_DIST`) and the Dockerfile
   COPY lists.
8. Declare the context in `apps/api/pyproject.toml`. Mounting its router makes
   the API an importer of it, and an undeclared import builds locally and fails
   in the container — this step was missing from the list until the generator
   walked it and `verify_architecture.py` said so.

## Non-negotiable architecture

- One monorepo and one shared UI shell.
- Modular monolith plus a separate async worker process.
- Independent bounded contexts; never a super-agent. Where one context needs
  another's data, the dependency goes through a Protocol the **consumer**
  declares and the composition root satisfies — never a direct import.
- Clean/Hexagonal dependency direction.
- Domain code must not import FastAPI, SQLAlchemy, LangGraph, Qdrant or a
  provider SDK.
- All external systems sit behind ports and adapters.
- PostgreSQL is the system of record.
- Qdrant retrieval always receives trusted tenant/workspace/ACL filters from
  backend context; filter injection happens only inside the knowledge gateway.
- Redis/Valkey is never a source of truth.
- Side effects require policy evaluation, idempotency and audit; critical
  effects require approval.
- Every worker, graph, prompt, tool, policy, event schema and evaluation dataset
  is versioned, and a release manifest pins the set a run used.
- Human-in-command.

## A second language, and where its boundary has to be

This repository is an **AI platform**, not a general application framework. What
belongs here is the agent runtime, memory, knowledge, the trust boundary around
them, and the seams a business context plugs into. A CRUD-heavy application
layer does not have to be Python, and saying so is cheaper than pretending
Python is the right tool for every tier.

**A service in another language (Go was the case discussed) is allowed, under
one condition that is not negotiable: it must not re-implement tenant
isolation.** That fact has exactly one owner, and it is not an application —
it is PostgreSQL.

Concretely, a non-Python service may connect to the database when all of the
following hold, and a reviewer can check every one:

1. It connects as a role that does **not** bypass RLS. `dw_app` and
   `dw_agent_ro` qualify; `dw_migrator`, `dw_provisioner` and `dw_admin` do not
   and must never be handed to an application.
2. It sets `app.tenant_id` **per transaction**, from a credential it verified
   itself, using `set_config(..., true)` so a pooled connection carries no
   residue into the next caller.
3. It owns no migrations. The schema has one owner and it is `db/migrations`.
4. It re-derives no authorization. Scopes, roles and plan entitlements are
   resolved by the platform and reach it as a verified context or through the
   platform's API — never by reading `platform.roles` and deciding for itself.
   Two implementations of who-may-do-what is the drift that ends as a
   disclosure.

What makes this safe is not the list; it is that the list is TESTED.
`test_rls_coverage.py` asks the catalog, not the source: every tenant table has
RLS enabled and FORCEd including partitions, every policy narrows by a setting
the backend controls, and a connection that never scopes itself reads nothing.
Those properties hold for any language, which is the whole point — they are
what allows a second one at all.

**What a second language must NOT take over:** anything that decides. Approval,
autonomy, the spend ceiling, evidence verification, memory writes, retrieval
filters. Those are the platform's reason to exist, they are where the tests are,
and a second implementation of any of them is a second answer to a question that
must have one.

## Required stack

Python 3.12 with a uv workspace. FastAPI, Pydantic v2, SQLAlchemy 2 async,
Alembic. LangGraph for orchestration/checkpoint/HITL. PostgreSQL, Qdrant,
Redis/Valkey. S3-compatible object storage (SeaweedFS in compose). Next.js, TypeScript strict, Ant Design v6 (below),
Tailwind for layout only. OpenTelemetry, optional Langfuse. Ruff, mypy, pytest,
import-linter, pre-commit. pnpm workspace and lockfile. Pin versions; commit
lockfiles; never `latest`.

## Web UI

Ant Design v6 (`antd`) is the component library, decided 2026-09-28 in place
of shadcn/ui; the measurements behind it are in `.claude/plans/web-ui.md`.

- **One component system.** New UI uses `antd`, `@ant-design/icons`, and
  `@ant-design/nextjs-registry` for server-rendered styles. No new shadcn/ui
  component is added; an existing one is replaced when its page is next
  touched, not in one sweep. ProComponents waits for a stable release that
  supports v6 (its v6 line was still beta on 2026-09-27).
- **Tailwind is for layout only** (flex, grid, spacing). Colour, type and
  radius come from the antd theme.
- **One owner of the tokens:** the antd theme (`ConfigProvider`, `vi_VN`
  locale) in `@dw/ui`, emitted as CSS variables. Tailwind's colours map to
  those variables and are never defined a second time.
- **Layer order:** `@layer theme, base, antd, components, utilities;` before
  `@import "tailwindcss"`, with `AntdRegistry` given the `antd` layer. A wrong
  order fails silently (Tailwind classes on antd components are ignored), so
  the switch ships with a test that fails without it.
- **Bundle:** about +250 kB first-load JS on a page using Table, Form, Select
  and DatePicker is accepted.
- **Dates:** dayjs with the `vi` locale, behind `lib/dates.ts` — one
  formatter, not two.
- **One shared shell:** `@dw/ui` owns the theme, the top-navbar layout (a
  horizontal menu, a drawer below the `lg` breakpoint) and the domain-neutral
  pieces a context reuses (page header, status tags, the case workspace). A
  context imports antd primitives directly; `@dw/ui` does not re-wrap them.

## Layer rules

Per context: `domain` (entities, value objects, domain events, rules),
`application` (commands, queries, handlers, ports, DTO mapping), `workflows`
(versioned graph state/nodes/routing), `adapters` (persistence, external
systems), `presentation` (API routes, event handlers).

Direction: `application → domain`, `presentation → application`,
`adapters → application ports`. Only a composition root imports a concrete
adapter. Constructor injection — no service locator, no mutable global client.

## Tenancy and authorization

- Every tenant-scoped table has `tenant_id` and `workspace_id`.
- PostgreSQL RLS is enabled, FORCEd, and tested. The application role must not
  hold `BYPASSRLS`; only the migrator does.
- Tenant context is set per transaction from a verified server-side access
  context — never from anything the client sent.
- Cache keys and object paths include tenant/workspace.
- Entitlement checks and authorization checks are separate concerns.
- Negative tests for cross-tenant reads and writes are mandatory.
- Hiding a control is not authorization. Enforce where the mutation happens.
- A table narrowed by workspace as well uses one policy shape on both sides:
  `tenant AND (workspace OR current_setting('app.workspace_scope') = 'tenant')`.
  Only the offboarding lane sets `app.workspace_scope`, per transaction, to
  export and purge every workspace of one tenant; `test_rls_coverage.py` fails
  a policy that reads it outside that shape and a workspace-narrowed table that
  does not read it.

## Per-tenant artifacts

One deployment serves many customers whose processes differ, and it does that
without a branch per customer. Every versioned artifact — prompt, tool spec,
toolset, model profile — resolves through `dw_kernel.overlay.TenantOverlay`:
the tenant's own version if it has one, the platform's otherwise.

- `tenant_id=None` is the platform layer: what ships in `configs/`.
- A concrete tenant id is an override, loaded at runtime from storage, never
  from the checkout — an override that needs a deploy is a fork with extra steps.
- **The tenant comes from the run or the access context, never from the
  request.** A caller who could name the tenant whose prompt to render could
  read another customer's wording.
- Resolution falls back to the platform, never sideways to another tenant.
  `test_tenant_overlay.py` asserts this; it is the breach this mechanism must
  not become.
- Inventory answers — the release manifest, `/v1/integrations` — read the
  platform layer only. What a deployment can do must not depend on who asked.

New registries take `tenant_id` from the start. Adding the argument later means
editing every call site in every product built on this platform, which is the
kind of change nobody makes and everybody works around.

## Agent and tool rules

- Graph state is typed and versioned; LLM output is always validated into a
  Pydantic schema.
- Workflow nodes contain no provider SDK and no SQL, and never import a concrete
  adapter — they take what they need by injection.
- A tool definition carries version, schemas, scopes, side-effect level,
  approval policy, timeout and idempotency. The executor authorizes, validates,
  executes, validates the output and audits.
- All side effects use idempotency keys.
- A tenant's plan quota is enforced where a run begins — in the runner, not in
  an API dependency. The API is not the only door: a worker reacting to an
  inbound event starts runs no request ever touched. The limit comes from the
  plan through `RunAllowancePort`, which the runtime declares and the platform
  satisfies, so neither package reads the other's table.
- Channels are reached through a port, never a provider's client. `ChatSenderPort`
  is the narrow intersection every channel can satisfy; code needing one
  provider's own features takes that provider's client and says so in its type.
  A provider adapter that does not fit gets an anti-corruption layer, not a
  widened port.
- Approval pauses and resumes a durable, checkpointed run. An approval with no
  run announces its decision instead: `ApproveAndResumeService.decide` writes one
  outbox event `<approval_type>.decided` in the decision's transaction, and the
  context that opened it registers the handler. A condition one type puts on
  who may decide it is a `decision_guards` entry at the composition root, like
  `strict_approval_prefixes`, never a branch in `decide`.

## Data model rules

- Timestamps are `timestamptz`.
- Every foreign key declares `ON DELETE` explicitly and is indexed on its own
  side.
- `updated_at` is maintained by a database trigger, and the trigger yields to a
  value the statement states. Unconditional `NEW.updated_at := now()` also
  discards a deliberate one, which makes a repair, a backfill that preserves
  original times, and any test that has to age a row impossible from every role.
- Every ORDER BY a list endpoint pages on has an index that carries it, leading
  with the columns RLS supplies (`tenant_id`) — a query never names those, which
  is exactly why the index must. Keyset pagination without such an index still
  sorts the whole match set, so page 50 costs what page 1 costs.
- Append-only, unbounded tables are range-partitioned, with a DEFAULT partition.
- Constraint names come from `dw_kernel.naming.NAMING_CONVENTION`; a new
  `MetaData` passes it.
- Migration `0001` is the immutable baseline. Corrections are new revisions.
- **A revision id is alembic's random hex, never a hand-picked number.** Two
  people working at once both guess the same "next number", git reports no
  conflict because the filenames differ, and alembic then refuses the merged
  tree with "revision is present more than once" — measured three times in one
  day on the product this was extracted from. Generate migrations with
  `alembic revision -m "..."` and keep the sequence in the _filename_ only,
  where it is a reading aid and carries no meaning.
- Privileges are part of the schema. A role that cannot read a table is an
  application that fails on its first real query while every health check still
  passes, so grants ship with the migration and are asserted by
  `dw_platform/tests/integration/test_privileges.py`.
- Partitioned tables need next month's partition before rows need it, and
  creating one is not just `CREATE TABLE ... PARTITION OF`: row security is NOT
  inherited by a partition made later, and `ALTER DEFAULT PRIVILEGES` hands it
  the write grants the audit log must not have. So creation, `ENABLE`/`FORCE`
  RLS, the tenant policy and the audit revoke are one function
  (`platform.ensure_time_partitions`), called from the worker's `partitions`
  lane — never a script, which is a place to forget one of the four.
  The DEFAULT partition is a safety net, not the plan: rows that land there
  block that month's partition from ever being created, which the function
  recovers from by relocating them.

## Environments

`local | test | uat | production`. `uat` and `production` are both deployed and
obey identical rules — the difference is blast radius, not strictness. Gate
anything that must not reach a deployed environment on `settings.is_deployed`,
never on `profile == "production"`, so a fifth environment cannot silently
reopen a hole. The compose overlay pins the profile.

## Observability and evaluation

Emit trace metadata for tenant/workspace (safe identifiers only), worker and
artifact versions, run/node/model/retrieval/tool/approval, latency, token use
and cost, and an error taxonomy. Ship a golden dataset and smoke evals per
context, including prompt injection, missing evidence and tenant leakage.

## Testing and CI

Unit, architecture/import-boundary, PostgreSQL repository/RLS integration,
Qdrant tenant-filter integration, LangGraph checkpoint/resume, outbox/idempotency,
API contract, generated frontend client compile, end-to-end vertical slice, and
eval smoke. CI runs config/contract validation, Python lint/typecheck, frontend
lint/typecheck/build, unit tests, architecture tests, integration tests,
security/dependency scan, eval smoke, container build and a compose smoke test.

## Work style

1. Inspect before changing.
2. Plan, and state assumptions.
3. Size is not a reason to go faster. A one-line fix earns the same questions
   as a new bounded context: is this the simplest approach that solves the
   problem, how does it behave once a second tenant/case/scale hits it, what
   will it interact with once more is built on top, and what could it
   silently break. Where the answer is "not sure," that is what
   `.claude/rules/failure-modes.md` and `reviewing-feature-security`'s
   mutation check are for — run them rather than re-deriving the checklist
   from memory, and slow down rather than guess.
4. Work in small phases; run tests after each.
5. Prefer a thin end-to-end slice over empty abstractions.
6. Never leave a placeholder-only module. A deferred component ships a working
   mock adapter and a documented port.
7. Keep diffs focused; update this file when the architecture changes.
8. Record a decision rather than deviating silently.
9. Write SOLID, cleanly factored, reusable, properly-composed code as a
   matter of course — `.claude/rules/code-quality.md` shows what that looks
   like in this codebase's own idiom (ports over branching, one dispatch
   table instead of two copies, composition over inheritance), not as a
   textbook checklist. It answers to the same "senior engineer" test as
   everything else here: appropriately factored, not over-abstracted for
   what the task actually asked.

## Agent skills

Matt Pocock's engineering skills are installed as a project plugin
(`mattpocock-skills@claude-plugins-official`, pinned by the marketplace to one
commit). `/ask-matt` routes to the right one.
The install is per checkout: `.claude/settings.json` only enables the plugin, so
a fresh clone, or a product merged from this platform, has none of its commands
until someone runs
`claude plugin install mattpocock-skills@claude-plugins-official --scope project`
there. `claude plugin list` says which: "✔ enabled" is installed, "✘ failed to
load" is enabled but not installed, and then `/ask-matt`, `/implement` and the
plugin's `code-review` do not exist while the repository's own hooks and skills
still run.

### Issue tracker

Local markdown in `.claude/plans/`, the plan the session hooks already enforce:
specs and tickets under `.claude/plans/<area>/<feature>/`, the area file stays
the summary. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default roles (`needs-triage`, `needs-info`, `ready-for-agent`,
`ready-for-human`, `wontfix`), written as a ticket's `Status:` line. See
`docs/agents/triage-labels.md`.

### Domain docs

Multi-context: `CONTEXT-MAP.md` at the root, a `CONTEXT.md` and `docs/adr/`
per context. ADRs carry a status, and not every decision recorded in this
repo is settled. See `docs/agents/domain.md`.

### Where the skills meet this repo's own gates

- `/implement` ends in `/code-review` and a commit. Before `/code-review`, run
  `reviewing-feature-security` and the scoped mutation check; the pre-commit
  gate still asks its questions.
- `/code-review`'s Standards axis reads this file and `.claude/rules/`
  (`code-quality.md`, `failure-modes.md`). The plugin's skill is
  `mattpocock-skills:code-review`, not Claude Code's built-in `/code-review`.
- A slice is finished when its area file in `.claude/plans/` says so.
