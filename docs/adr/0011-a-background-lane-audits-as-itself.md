---
status: Accepted (provisional, decided under delegation 2026-10-08)
date: 2026-10-08
source:
    - ../../packages/python/dw_platform/src/dw_platform/domain/audit.py # system_actor, lane_audit_event
    - ../../packages/python/dw_platform/src/dw_platform/adapters/persistence/lane_audit.py # append_across_tenants
---

# 0011. A background lane audits as itself: `system:<lane>`, a fixed id per lane

## Context

`platform.audit_events.actor_id` is `uuid NOT NULL`. Lanes in the worker change
tenant state with no person behind them. The channel delivery lane wrote the
recipient's user id as the actor (with `details.actor = channel_delivery_lane`),
so the log said the recipient sent their own message. The retention sweeps
hard-deleted memories and documents and wrote no audit row at all. A product's
sweeps (a follow-up sweep, for one) needed a convention before they copied either.

## Decision

- A lane's actor is `system_actor(<lane>)`, the name-based UUID of
  `system:<lane>` under `SYSTEM_ACTOR_NAMESPACE`, where `<lane>` is the name the lane is registered under in
  the worker (`retention`, `retention_knowledge`, `channel_delivery`). Same id on
  every host and release; never equal to a user id, which is a random uuid4.
  The namespace is fixed forever.
- `details.actor` reads `system:<lane>`, set last so a caller cannot relabel it.
- `lane_audit_event(...)` is the one way a lane builds its event. A person the
  action concerns (a delivery's recipient) goes in `details`, never `actor_id`.
- A lane writing from a cross-tenant drain uses `append_across_tenants`, which
  binds each event's own tenant per transaction before the insert, in the same
  transaction as the change it records. If the audit row cannot be written,
  the change does not happen.
- Applied 2026-10-08: memory expiry (`memory.item.expired`), knowledge hard
  delete (`knowledge.document.purged`), channel delivery settlements (actor
  changed from the recipient to the lane).

## Not applied, on purpose

- Retention of operational rows (checkpoints, notifications, link nonces,
  inbound messages, delivery rows, approval codes, spend-guard counters) and
  partition maintenance: housekeeping of rows that are not records a person
  made; they log counts. Orphaned evidence deletion likewise.
- Offboarding's purge keeps the operator who requested it as the actor: a
  person decided it and the lane carried it out.
- Lanes that act on a person's decision (memory review settlement) already
  audit that person.

## Consequences

- An auditor finds every lane's actions by one fixed id per lane.
- A new lane picks its registry name once; renaming it changes its actor id,
  so a rename is a decision, recorded like any other.
