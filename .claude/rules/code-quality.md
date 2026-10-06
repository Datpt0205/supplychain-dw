# SOLID, clean code, and reuse — how this codebase already does it

**Scope: every session, on purpose.** This file has no `paths:` frontmatter, so it
loads whatever part of the tree a session touches. That is deliberate: it applies to
every change, not to one directory (`CLAUDE.md` "Work style" item 9). A rule that
only applies to part of the tree carries `paths:`, as `ui-quality.md` does.

Not textbook definitions. Every principle below is shown against a real
pattern already in this repo, because "write SOLID code" is not
actionable on its own — knowing what it looks like _here_, in this
codebase's own idiom, is. Written as a check to run **while writing the
line**, the same way `.claude/rules/failure-modes.md` is.

This file does not add a mandate on top of `CLAUDE.md` (this repo's own
architecture of record — Clean/Hexagonal dependency direction, ports and
adapters, one context never importing another's concrete adapter) or the
operator's own standing global guidelines (Simplicity First, One Owner
Per Fact). It is the same mandate, read from the angle of a single class
or function instead of the whole architecture. Where the two ever seem to
disagree, `CLAUDE.md` wins; say which line of this file you set aside and
why.

---

## Single responsibility: one decision per method, each fact in one place

`SeparationOfDutiesService` has three methods, `list_rules`, `waive` and
`revoke`, and each one does exactly two things: ask `authz.require` whether
the caller may, then hand the repository one write with its audit event.
Whether a waiver may exist at all (the rule is waivable, no waiver is
already open, no membership still holds both sides) is not decided there:
the database refuses it by constraint, and the adapter turns that refusal
into a `ConflictError` by constraint name. Authorize, decide, translate —
three responsibilities, three places. A method that both validates AND
persists AND notifies is three responsibilities wearing one name — split
it before adding a fourth.

**Ask:** if this method's name needs "and" to describe honestly, it is
two methods.

## Open/closed: extend via a new port implementation, never by branching a shared one

`ModelGateway` and `WorkflowRunnerPort` are Protocols;
`LangGraphWorkflowRunner` is one implementation of the second, not the
contract itself. A tenant needing different behaviour gets it through
`PolicyOverridePort`/`TenantOverlay` — a new row, a new resolved value —
never through `if tenant_id == X:` grown inside a shared function. A new
worker lane is a `ConsumerRegistry.register(name, consumer,
interval_seconds=…)` call at the composition root, not a branch added to
the loop that runs lanes. A context whose approvals need separation of
duties adds its prefix to `ApproveAndResumeService`'s
`strict_approval_prefixes` with `|=` where it is wired, not an
`if approval_type.startswith(…)` inside the service.

**Ask:** does adding this case require editing existing branches, or only
adding a new entry/implementation? If the former, the seam is in the
wrong place.

## Liskov / interface honesty: a fake honours the contract it stands in for

`apps/api/tests/unit/test_admin_separation_of_duties_endpoint.py`'s
`FakeRepo.revoke` refuses the way `SqlSeparationOfDutiesRepository.revoke`
does: a `ConflictError` naming the memberships while any still hold both
sides of the rule, and `False` when there is no open waiver to revoke —
not `True` because the one assertion at hand only needed the happy path.
A fake that cannot meaningfully implement a Protocol method raises
`NotImplementedError("not exercised by …")` from it, rather than returning
`None`/`[]`/`True` and hoping nothing checks.

**Ask:** if a caller swapped this implementation in without reading its
source, would it behave the way the Protocol promises — or only the way
this one caller happens to use it today?

## Interface segregation: the narrowest Protocol the consumer actually calls

`NotificationService` takes a `NotificationInboxPort` with `latest`,
`mark_read` and `mark_all_read` — what a reader of their own inbox does.
`SqlNotificationRepository` also has `deliver`, but the inbox never sends,
so the port does not carry it; a sender declares its own narrow Protocol
for delivery. `RunAllowancePort` is the same shape across packages: the
runtime declares the one question it asks ("may this tenant start a run?")
and the platform answers it, so neither reads the other's tables.

**Ask:** does this Protocol have a method nothing outside this file
calls? If so, it is either dead or belongs on a narrower Protocol.

## Dependency inversion: application owns the Protocol; only the composition root imports the adapter

Constructor injection everywhere — `SeparationOfDutiesService(repo, authz,
clock, id_generator)` — never a service locator or a module-level client.
`dw_platform/application/separation_of_duties.py` imports no adapter; it
depends on `SeparationOfDutiesRepositoryPort`, declared in the same file.
Outside the adapter package itself, only
`apps/api/src/dw_api/bootstrap/wiring.py` names
`SqlSeparationOfDutiesRepository` or `SqlNotificationRepository`. This is
`CLAUDE.md`'s own "Clean/Hexagonal dependency direction" — this section is
that rule read at function-signature scope.

**Ask:** if I `grep` this file for a concrete adapter class name outside
`bootstrap/`, do I find one? If yes, the dependency points the wrong way.

## DRY / reuse: one function answers one question

`effective_scopes()` in `membership_lookup.py` decides which scopes a
membership holds, and both of its callers use it: `SqlMembershipLookup`,
which answers "may this person act?", and `scope_holders.py`, which answers
"who should be handed this work?". It was extracted when the second caller
arrived. Two copies would be two answers to one question, and the day they
disagree a person is handed work they are then refused permission to do —
`failure-modes.md` #2, which counts four silent drifts of one fact here.
The same discipline applies to behaviour, not only to data.

**Ask:** does another file already answer this exact question? A second
function deciding what a first one already owns is the failure-modes.md
#2 shape, just in code instead of config.

## Composition over inheritance: wrap and inject, not base classes for one subclass

`SingleCallModelGateway(inner=…, ledger=…)` wraps any `ModelGateway` to
free a one-call run's spend-ledger entry; it does not subclass the gateway
it wraps. `RunnerStack` in `dw_agent_runtime`'s integration tests composes
a session factory, registries and a runner as plain attributes, not
through a base test-case class every stack must extend. The inheritance
the platform does use — `TenantId`, `WorkspaceId`, `UserId`, `RunId`, each
`(EntityId)` — earns it honestly: many real subclasses sharing one genuine
behaviour (wrap a UUID, parse one back from a string). An abstract base
class with exactly one concrete subclass and no second one on the horizon
is the operator's own "no abstractions for single-use code" rule, worn as
an inheritance hierarchy instead of a config flag.

**Ask:** if I deleted this base class and inlined its one subclass, would
anything outside this file notice? If not, the hierarchy is decoration.

---

## Where this stops being clean code and starts being over-engineering

SOLID done badly is speculative interfaces, needless indirection, and
inheritance for code that will only ever have one shape. The operator's
own global guidelines (Simplicity First, One Owner Per Fact) already
govern that trade-off — a Protocol with one implementation and no second
consumer named anywhere is premature the same way a config flag nobody
reads is (`failure-modes.md` #1). Apply this file's checks where a real
second caller, a real second implementation, or a real composition-root
seam already exists — not ahead of one, on the guess that it might.

**The test, same as those guidelines' own:** would a senior engineer
reading this diff call it appropriately factored, or over-abstracted for
what the task actually asked? If the second, simplify — clean code that
nobody
can read quickly is not clean.
