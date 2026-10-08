---
status: Accepted (provisional, decided under delegation 2026-10-08)
date: 2026-10-08
source:
    - ../../db/migrations/versions/f381f1694395_platform_sod_waiver_second_person_and_.py
    - ../../packages/python/dw_platform/src/dw_platform/application/separation_of_duties.py
---

# 0012. A separation-of-duties waiver takes two people, and a role cannot change under a membership

Amends the waiver design of migration `6b26771e549d` (per-tenant waivers, on the
record) and the membership trigger of `b9862fa13a80`.

## Context

- One org admin could waive a rule and then hold both sides of it: the control
  whose purpose is to need two people was lifted by one.
- SoD was checked when a membership was written. A migration widening a role
  or permission set past a rule left every membership holding it in breach, and
  nothing said so.

## Decision

- **Two people.** A waiver is a proposal until a different holder of
  `platform.sod_waivers.write` confirms it with a reason
  (`POST /admin/separation-of-duties/{rule}/waiver/confirm`). Only a confirmed
  open waiver lifts a rule. The database enforces it for every writer:
  `ck_sod_waivers_second_person` (`confirmed_by <> granted_by`, all confirmation
  fields set together, non-blank reason), an insert may not arrive confirmed,
  and an open waiver may be confirmed once and revoked once (revoking a proposal
  withdraws it). Chosen over making the waiver a strict approval: the waiver
  table, its guard trigger and the service already own this decision, and an
  approval would have added a run-less approval type, a strict prefix, an
  outbox handler in the worker and a second place that decides.
- **Existing open waivers** were granted by one person; they stay open and
  unconfirmed and lift nothing for a new membership write until confirmed
  (fail closed). Memberships already holding both sides are not touched.
- **Role scope changes.** An AFTER UPDATE OF scopes trigger on `platform.roles`
  and `platform.permission_sets` refuses a change that would make a membership
  holding that role or set break a rule its tenant has not waived and
  confirmed: SQLSTATE 23514, constraint `ck_roles_sod_memberships`, the rule in
  the message, up to 20 membership ids in DETAIL. Refuse, not flag: both tables
  change only in migrations, so the refusal stops the migration and its author
  fixes the memberships or the change. An HTTP path that ever writes these
  tables translates the constraint to a 409 with the list, as the revoke path
  does with `ck_sod_waivers_not_in_use`.

## Consequences

- A tenant with a single admin cannot waive a rule at all; that is the point.
- Adding a scope to a role in a product migration can now fail on that
  product's data. The failure names the memberships to fix first.
- Not covered: changing a RULE's scopes (`platform.sod_rules`) under existing
  memberships; rules are added with their own check in the migration that adds
  them.
