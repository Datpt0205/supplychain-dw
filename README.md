# Digital Worker Platform

A multi-tenant backbone for products whose work is done by agents: a versioned
agent runtime with human-in-command approvals, retrieval, memory, connectors,
tenancy with row-level security, an audit trail, and the evaluation harness that
keeps the safety gates honest.

It ships **no business domain**. A product plugs its own bounded context in at
three declared seams and inherits everything above.

---

## What is here

| Package            | Owns                                                                                                                                                                                                                      |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `dw_kernel`        | Pure primitives: errors, ids, clock/id ports, outbound-URL guard, circuit breaker, the database naming convention. Standard library only — enforced.                                                                      |
| `dw_platform`      | Tenants, workspaces, users, memberships, roles, permission sets, the manager hierarchy, plans/entitlements, approvals, audit, the transactional outbox, provisioning.                                                     |
| `dw_agent_runtime` | Worker/graph/tool registries, the tool executor (authorization → validation → execution → audit), model gateway and profiles, prompt bundles, checkpoints, run store, approval pause/resume, the document sandbox client. |
| `dw_knowledge`     | Documents, chunking, ingest jobs, embeddings, reranking, the retrieval gateway that injects the tenant filter.                                                                                                            |
| `dw_memory`        | Memory items and the write policy that decides what may be remembered.                                                                                                                                                    |
| `dw_connectors`    | Outbound channel ports and their adapters.                                                                                                                                                                                |
| `dw_observability` | OpenTelemetry wiring; Langfuse as an OTLP endpoint.                                                                                                                                                                       |
| `dw_evals`         | Dataset format, runner, and the four platform safety graders.                                                                                                                                                             |

| App           | Is                                                                                       |
| ------------- | ---------------------------------------------------------------------------------------- |
| `apps/api`    | FastAPI. Platform routes under `/api/v1`; a context mounts its router beside them.       |
| `apps/worker` | The async loop: transactional outbox, knowledge ingestion, stale-job reaping, retention. |
| `apps/docgen` | A network-isolated sandbox that runs model-written shell to produce documents.           |
| `apps/web`    | Next.js shell: Home, Approvals, Knowledge, Memory, Integrations, Audit, Admin.           |

---

## Getting started

```bash
make bootstrap        # Python (uv) + Node (pnpm) dependencies
cp .env.example .env  # then fill in the secrets it names
make infra-up         # Postgres, Qdrant, Valkey, S3 (SeaweedFS), Keycloak, docgen
make migrate          # create the schema
make dev              # api + worker + web, with reload
```

Everything is also runnable as containers: `make docker-up`.

---

## Adding a bounded context

```bash
make new-context NAME=sales_chat
```

That wires all fifteen places a context joins the platform and leaves a slice
that already passes lint, mypy, import-linter and its own test. CI generates a
throwaway context on every push, so the seams below are checked rather than
described.

A context is a package under `packages/python/dw_<name>` with the layers
`domain/ application/ workflows/ adapters/ presentation/`. Three of those places
are code, and they are marked in the code.

1. **`apps/api/src/dw_api/bootstrap/wiring.py`** — build the context's handlers
   from `container.runtime`, the published `RuntimeSeam`: session factory,
   clock, ids, telemetry, model gateway, chat-model factory, tool registry and
   executor, tool specs, toolsets, graph and worker registries, knowledge
   gateway, memory service.
2. **`apps/api/src/dw_api/main.py`** — mount its presentation router, guarded on
   the dependency it needs.
3. **`apps/worker/src/dw_worker/main.py`** — register its consumers, and a
   `ReapTarget` for each job queue it owns.

Then register it in `pyproject.toml` (uv sources, ruff first-party, mypy paths,
import-linter `root_packages` plus an independence contract), ship its
`configs/` artifacts, add an eval dataset with full security coverage, and add
its migrations after the baseline.

Nothing above those seams may import a business package, and import-linter
fails the build if it does.

---

## Environments

Four profiles, and two of them are deployed.

| Profile      | Meaning                                                                               |
| ------------ | ------------------------------------------------------------------------------------- |
| `local`      | A developer's machine. Mocks allowed, docs exposed, private outbound targets allowed. |
| `test`       | CI. Same permissiveness; no external services assumed.                                |
| `uat`        | A real deployment with real people and real data.                                     |
| `production` | The live deployment.                                                                  |

`uat` and `production` are held to **identical** rules, because the difference
between them is who a mistake reaches, not how strict the configuration should
be. In both, startup fails on: a mock model provider, dev-mode auth, the
meaningless hash embeddings, a missing vector store, CORS origins left
unlisted, or an issuer, CORS origin or public API URL that is not `https://`
— and the OpenAPI schema and its UIs are not served.

On a host with real hostnames, `infra/compose/docker-compose.host.yml` adds
Caddy (TLS, three hostnames from `.env`); the first-deployment runbook is
[`docs/deploy/host.md`](docs/deploy/host.md).

The profile is pinned by the compose overlay, not by an environment variable
somebody has to remember:

```bash
docker compose -f infra/compose/docker-compose.yml \
               -f infra/compose/docker-compose.uat.yml  --profile full up -d
docker compose -f infra/compose/docker-compose.yml \
               -f infra/compose/docker-compose.prod.yml --profile full up -d
```

One deploy path for all three environments, run by CI or by hand:

```bash
scripts/deploy.sh <branch> <dev|uat|production>
```

---

## Idempotent requests

Some mutations must not happen twice because a client's first attempt timed out.
Send an `Idempotency-Key` header — any string of your choosing, up to 255
characters, unique per request — and the API will act at most once on it.

It is honoured on the mutations where a duplicate does real damage, and it is
**optional** everywhere: a request without the header behaves exactly as before.

| Route                                    | A duplicate would                                    |
| ---------------------------------------- | ---------------------------------------------------- |
| `POST /api/v1/approvals/{id}/decisions`  | decide twice and resume the run's side effects twice |
| `POST /api/v1/admin/members`             | grant access twice, leaving two audit records        |
| `DELETE /api/v1/admin/members/{user_id}` | revoke twice, leaving two audit records              |

What happens:

- **First request with a key** — runs normally. The response status and body are
  stored against the key.
- **Retry with the same key and the same request** — the stored response comes
  back unchanged, with the same status code, and the handler does not run again.
  The reply echoes the `Idempotency-Key` header.
- **Same key, different request** — `409` with code `idempotency_conflict`. The
  key was already spent on something else, and returning the earlier response
  would hide the mistake. Use a new key for a new request.
- **Same key while the first request is still running** — `409` with code
  `conflict`. Retry in a few seconds; the server refuses rather than queue, so a
  retry storm cannot exhaust it.
- **The request fails with a 5xx** — the key is released, not spent. A failure is
  not a decision, and storing it would make the very retry the header exists to
  protect return the error forever.

A key is scoped to your tenant: two tenants may use the same string and will
never meet. Within a tenant, the same key sent against a different workspace is a
conflict, not a replay.

---

## Paging through a list

Every list endpoint returns the same envelope, never a bare array:

```json
{
    "items": [{ "...": "one row" }],
    "next_cursor": "eyJ2IjoxLCJrIjoiMjAyNi0wMy0wMVQwOTowMDowMCswMDowMCIsIn0"
}
```

Take `limit` (1–200, default 50) and `cursor` as query parameters. To read
everything: call once with no cursor, then keep calling with the `next_cursor`
you were last handed, until `next_cursor` is `null`.

```bash
curl "$API/api/v1/audit/events?limit=100"
curl "$API/api/v1/audit/events?limit=100&cursor=<next_cursor from the reply>"
```

**`next_cursor: null` is the only stop condition.** An empty `items` is not — a
filtered listing can hand back an empty page in the middle of a run and still
have rows behind it.

**The cursor is opaque.** It encodes the position of the last row you were given,
and nothing about the encoding is contract except that it round-trips. Do not
decode it, compare two of them, do arithmetic on one, or construct one. It also
carries a fingerprint of the query it came from, so a cursor replayed against
different filters — or against a different endpoint — is refused with `422` and
code `validation_failed` rather than answered from the wrong window. Change a
filter, start again without a cursor.

Paging is keyset, not `OFFSET`: the server seeks to your position through an
index instead of counting past the rows before it, so page 500 costs what page 1
costs. It also means a row written while you are paging cannot push an older row
across a boundary you have already read. Rows created _after_ you started appear
only if you restart the listing — that is the stability you are being given, not
a gap.

Paged today: `/audit/events`, `/knowledge/documents`, `/memory/items`,
`/approvals`, `/feedback`. Endpoints that return a bounded catalog rather than a
growing log — the workspace roster, the role catalog, the integration list, one
run's timeline — return a plain array and take no cursor.

---

## The data model

One baseline migration (`db/migrations/versions/0001_platform_baseline.py`)
creates three schemas — `platform`, `knowledge`, `memory` — and every later
change is an ordinary revision on top of it.

What the baseline guarantees:

- **Tenant isolation is enforced by the database.** Row-level security is
  enabled and FORCEd on every tenant-scoped table; the tenant comes from
  `app.tenant_id`, set per transaction from a verified access context. The
  runtime role does not hold `BYPASSRLS` — only the migrator does.
- **Every foreign key states what happens when its parent goes** — `CASCADE`,
  `RESTRICT` or `SET NULL`, never the implicit default.
- **Every foreign key is indexed on its own side**, so deleting a parent does
  not sequentially scan the child table while holding a lock on it.
- **`updated_at` is maintained by a trigger**, not by whichever code path
  remembered.
- **The two append-only tables are range-partitioned** — `audit_events` by
  `occurred_at`, `model_usage_ledger` by `created_at` — so retention is
  `DROP PARTITION` rather than a `DELETE` that has to be vacuumed. Both have a
  DEFAULT partition, so a row is never rejected for arriving outside every
  declared range.
- **Timestamps are `timestamptz`**, without exception.
- **Constraint names follow one convention** (`dw_kernel.naming`), so a later
  migration can name what it drops without querying the database first.
- **Privileges ship with the schema.** `dw_app` gets DML on the tables it
  serves, cannot UPDATE or DELETE `audit_events` (append-only is a grant, not a
  convention), and cannot read the provisioning record. Default privileges carry
  to tables added later, so a forgotten `GRANT` cannot ship a release that fails
  on its first query. Asserted in `test_privileges.py`.
- **Primary keys are time-ordered UUIDv7**, not random v4: a random key scatters
  every insert across the whole B-tree, and the rows already written keep the
  keys they were given.

Partition rolling is **not** an operational job: it runs in the worker, on the
`partitions` lane, because a partition created without row security is a
cross-tenant read addressable by name and a script on a cron is a place to
forget that. `scripts/roll_partitions.py` used to be that job and was removed —
it created partitions with no RLS and no `REVOKE UPDATE, DELETE` on the audit
log, so running it re-opened both holes every month.

One operational job is left, and it is still a cron:

```bash
scripts/backup_postgres.sh              # pg_dump, rotated
```

Roles are a precondition, not something a migration creates — it would have to
carry their passwords. Create `dw_app` (and `dw_provisioner` if the deployment
provisions tenants) before the first migration; it warns, with the fix in the
message, when one is missing.

---

## Checks

```bash
make lint typecheck test-unit test-architecture test-contract eval-smoke
make ci               # all of the above, as CI runs them
```

CI (GitHub Actions) covers
config/contract validation, Python lint + type check, frontend lint/type/build,
unit tests, import-boundary and dependency rules, integration tests against real
Postgres/Qdrant/Redis/S3, a dependency and secret scan, the eval smoke suite,
a container build and a compose smoke test.

Safety is tested, not asserted: the eval suite grades prompt containment, the
tool-approval gate, the cross-tenant retrieval filter and the memory write
policy against the real components.

---

## Conventions

- Clean/Hexagonal: `application → domain`, `presentation → application`,
  `adapters → application ports`; only a composition root imports a concrete
  adapter. Domain code imports no framework, ORM or provider SDK.
- Every worker, graph, prompt, tool, toolset, policy and eval dataset is
  versioned, and a release manifest pins the exact set a run used.
- Side effects go through the tool executor: policy evaluation, idempotency key,
  audit record, and approval when the tool's spec says so.
- Human-in-command. An approval pauses a checkpointed run and resumes it.

`CLAUDE.md` is the architecture of record and the file to read before changing
any of the above.
