# Failure modes this repository actually has

**Scope: every session, on purpose.** This file has no `paths:` frontmatter, so it
loads whatever part of the tree a session touches. That is deliberate: it applies to
every change, not to one directory (`CLAUDE.md` "Work style" item 3). A rule that
only applies to part of the tree carries `paths:`, as `ui-quality.md` does.

Not general advice. Every entry below is a shape that has bitten this codebase,
with the count of distinct times it was found. They are written as questions to
ask **while writing the line**, because each one was cheap to prevent and
expensive to find.

Two of the eight cannot be thought through — they are facts about the outside
world, and only measurement finds them. They are marked as such. Treating the
checklist as sufficient would miss exactly the dangerous half.

---

## 0. A check that cannot see the thing it checks (found 1×)

Numbered zero because it comes before the rest: a check you trust that is blind
to part of what it covers makes every finding below it unreliable.

`verify_invariants.py` reads migration TEXT and confirmed for months that every
tenant table had RLS. It was right about every table the text names. Postgres
creates a PARTITION from the partitioning DDL, so `platform.audit_events_default`
and `platform.model_usage_ledger_default` were never named — and neither had RLS,
while `dw_app` held SELECT on both. Every tenant's audit trail and every tenant's
spend were one table name away from any connection, with no `app.tenant_id` set.

Enabling RLS on a partitioned parent applies its policies to rows reached THROUGH
the parent. A partition addressed by its own name is an ordinary table using its
own settings.

**Ask:** what can this check NOT see? A check over source text cannot see what the
database creates on its own. Where the truth lives in a running system, ask the
running system — `test_rls_coverage.py` walks `pg_class`, partitions included.

## 1. Declared, and nobody reads it (found 10×)

`autonomy_level` was declared, validated and read by **nothing** — a worker at A4
paused exactly as often as one at A0. `approval_policy: conditional` was accepted
by the schema and matched by no branch, so it silently behaved as `never`.
`budgets` existed on every model profile and the agent loop never consulted it.
`evidence_id` was minted per retrieval and resolved to nothing at all. Also:
`retention_policy`, the memory REVIEW queue (read since 2026-10-06: a held
candidate opens a `memory.review` approval), `cancel_thread`, the
`prompt_bundle_version` binding, `@dw/agent-ui`.

**Ask:** who reads this? Name the file and line. If the answer is "nothing yet",
either wire it in the same change or do not add it.

A control that is configured and unenforced is worse than no control: it reads
like a safeguard in review, and the first incident discovers it was decoration.

The tenth was not in the code: the trivy step in CI pinned a release tag that
does not exist, and `curl` without `--fail` piped the 404 page into `tar`, so the
step failed as a corrupt archive while scanning nothing. It had been reported as
"trivy is wired into CI" on the strength of reading the workflow file. The same
question applies to a pipeline step as to a field — who runs this, and did you
watch it run?

## 2. One fact, two copies, and they drift (found 4×)

`DEFAULT_PLANS` in code beside the `platform.plans` rows. A comment listing
deepagents' builtin tools as `execute` when the library had since replaced it with
`delete`. The same column list written out three times in one repository file. A
cache serialiser naming every field by hand, so a field added to the object and
not to the serialiser comes back as its default — silently.

**Ask:** what already owns this fact? Read from the owner. If a second copy is
unavoidable, what makes them disagree loudly instead of quietly?

## 3. A test that cannot fail (found 4×)

An `ORDER BY` test that compared Postgres's collation against Python's
`sorted()` — it agreed only while every name happened to be capitalised. A
fork-bomb test that asserted "the next command works" when the budget was only
exhausted by the time the _following test_ ran. Two integration files fighting
over one tenant's row: green apart, red together. And a mutation script of mine
that silently did not mutate, so it reported a passing test as proof.

**Ask:** can this test fail? Break the thing it guards and watch it go red. When
a whole new suite passes on the first run, that is a reason to check it, not to
move on.

A new guard ships with a demonstration that removing the guard turns a test red.

## 4. Trusting a library instead of measuring it — NOT THINKABLE (found 5×)

MinIO withdrew both its images from Docker Hub; the error read as a credentials
problem. The move to quay.io held until MinIO withdrew them there too: twelve
unpushed commits later, CI failed on a pull that only worked on the dev machine
because its cache still held the image. The store is SeaweedFS now, behind a
contract test any replacement must pass first. deepagents changed which builtin tools it installs. Three of five
documented assumptions about `SummarizationMiddleware` were wrong when run. mypy
narrowed `sys.platform` differently per machine, so a typecheck passed locally and
failed in CI.

No amount of care catches these. The habit that does:

**Before depending on library behaviour, run it and look.** A comment describing
a library is a claim about a past version. Pin versions, and let CI run on the
real target platform.

## 5. Enforced in one place, bypassable from another (found 4×)

The spend ceiling ran on the structured path while the agent loop — the one that
can actually loop — had none. The approval gate in the executor could be reverted
to the old tool-only decision and **every agent-level test stayed green**, because
the tool wrapper asked first; the last line of defence was untested.

**Ask:** where does the effect actually happen? Enforce there. Then ask what
reaches that point without passing the check you just wrote — and test _that_
path directly, not through the layer that would have caught it anyway.

## 6. Created and never destroyed (found 3×)

`RunBudgetLedger` recorded per-run spend and `forget()` had no caller: one entry
per run, for the life of the process. Killed sandbox processes became zombies
because PID 1 was the app and reaped nothing — and a zombie still counts against
`ulimit -u`, so the corpses held the budget as firmly as the living.

**Ask:** what removes this, and when? A cache with a TTL, a row with a retention
rule, a dict keyed by something unbounded — each needs an answer.

## 7. Defaults that fail open (found 3×)

`provenance_refs jsonb DEFAULT '[]' NOT NULL` — an empty array satisfies NOT NULL,
so "provenance required" was enforced by nothing. A cache read as
`data.get("record_visibility", "open")`, so an entry written before the field
existed reads as the permissive value for as long as its TTL. Text columns holding
a fixed set of values with no CHECK constraint.

**Ask:** if this value is missing or unreadable, what happens? Refusing a
legitimate action is recoverable; allowing an illegitimate one is not. Narrow
defaults, and a CHECK constraint on anything with a fixed set of values.

---

## Where a decision goes when the session ends

Four kinds of thing, four homes. Putting them in one place is how they get lost.

| Kind                                       | Home                                                                                 |
| ------------------------------------------ | ------------------------------------------------------------------------------------ |
| A decision about the architecture, and why | `CLAUDE.md` — it travels with the code and is reviewed with it                       |
| A failure mode worth avoiding next time    | this file                                                                            |
| Where the work stands and what is next     | `.claude/plans/<area>.md`, indexed by `.claude/PLAN.md`, read whole at session start |
| A standing instruction from the user       | user-level memory, not the repo                                                      |

A session that ends without the plan matching reality — the area file it
worked in, and the index when an area's state, next step or owed decision
changed — has lost whatever it learned, however good the code was. The index
stays short on purpose: the session-start hook warns past 80 lines, because a
plan that grows until only its top is read hides exactly what was added last.
