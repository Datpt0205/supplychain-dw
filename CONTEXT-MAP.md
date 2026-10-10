# Context map

This repository is a platform backbone plus bounded contexts that plug into it
(`CLAUDE.md`, "Adding a bounded context"). Each context owns its own language
and its own decisions; the platform's decisions apply to every context.

| Context               | What it covers                                                                                                                                                             | Glossary                                     | Decisions                                                                                |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Platform              | Tenancy and authorization, the agent runtime, model gateway, knowledge, memory, audit, notifications, ops                                                                  | `docs/platform/CONTEXT.md`                   | `docs/adr/`                                                                              |
| Supply Chain (Elmich) | Elmich's product-to-stock process, Part A, steps 1–17: product development case (steps 1–9), the ĐẶT HÀNG hand-off, PO case (steps 10–17), SLA, follow-ups, case documents | `packages/python/dw_supply_chain/CONTEXT.md` | `packages/python/dw_supply_chain/docs/adr/`, and the generic ones in `docs/adr/` (below) |

This checkout is the Elmich product repo and ships one business context,
Supply Chain (`packages/python/dw_supply_chain`). Its process spec is
`docs/products/elmich/process.md`. Another context is added with
`make new-context NAME=<name>` and a row here, with its glossary at
`packages/python/dw_<name>/CONTEXT.md` and its decisions at
`packages/python/dw_<name>/docs/adr/`.

Supply Chain's context-only decisions (E6–E9, E11: 0016–0019, 0021) are in
`packages/python/dw_supply_chain/docs/adr/`, keeping their numbers. Its generic
ones, upstream candidates for the platform, stay in `docs/adr/` (0011–0015, 0020,
0022, 0023; ADR 0011 E1); 0020 among them, because the stamped `required_scope`
is the platform's approval mechanism (its twin is platform ADR 0004).

A glossary or decision folder listed here may not exist yet: they are written
when a term or a decision is actually resolved (`/domain-modeling`), not
upfront. How the skills read these files, and how a decision's status is
treated, is in `docs/agents/domain.md`.

## How the contexts relate

- A context depends on the platform, never the other way round.
  `lint-imports` keeps the direction.
- Where a context needs a platform capability (approvals, policy overrides,
  the inbox, who holds a scope), it declares the Protocol it needs and the
  composition root satisfies it with a platform adapter.
- Where one context needs another's data, it goes through a Protocol the
  consumer declares, never by importing the other context.
- Supply Chain reaches platform capabilities this way: approvals (with a
  stamped `required_scope`, ADR 0020), policy overrides, the inbox and its
  channel deliveries (ADR 0013), scope holders, and object storage for case
  documents (ADR 0021). It owns schema `supply_chain`; the platform never reads
  it.
