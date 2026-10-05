"""supply_chain po_cases gains filtered list indexes

Revision ID: 85d51fa0c98c
Revises: 98a7b592a040
Create Date: 2026-09-25 05:37:49.232176+00:00

`GET /po-cases` now narrows by `state` and by `supplier_name` (the Control
Tower's drill-down). `ix_po_cases_page` still carries the ORDER BY, but with
an equality filter in front of it a rare value — the three BLOCKED cases in
a tenant of fifty thousand — means walking the tenant's whole index to find
one page. Each filter gets the same `(created_at DESC, id DESC)` tail behind
its own column, `tenant_id` leading for the same reason `ix_po_cases_page`
leads with it: RLS supplies that equality and the query never names it.

No partial index for `active_only`: its predicate is `state NOT IN (the
terminal states)`, and writing that list into an immutable migration would
be a second copy of `domain.po_case.TERMINAL_STATES` that nothing keeps in
step. It would not even be used reliably: asyncpg sends the NOT IN values as
bind parameters, and a generic plan cannot prove a partial predicate from
parameters (measured — the custom plan used one, the generic plan did not).

What these indexes do NOT bound, measured on 500k synthetic rows (one
tenant of 200k, 175k of them completed), recorded rather than fixed:
- `active_only` and any second column are row filters, not index
  conditions. A supplier with 44 active cases in a history of 1 096 walks
  all 1 096 on the first page, because 44 < a page and the scan cannot stop
  early — cost follows the supplier's history, not the match count. Today
  that is proportionate (the Control Tower's own `list_active` is a
  sequential scan of the table); the remedy if it stops being so is a third
  index `(tenant_id, supplier_name, state, created_at DESC, id DESC)`.
- `state` + `supplier_name` together: asyncpg's named statements go to a
  generic plan after five runs, and that plan stays pinned to one index for
  every value pair (a rare state under a large supplier walked 27k rows).
- An index on `state` costs HOT updates: every transition now rewrites all
  five indexes instead of none (2 000 of 2 000 state updates were HOT
  without it, 4 of 2 000 with it) — acceptable at a dozen transitions per
  case.
- `active_only` with a terminal `state` is empty by definition and the
  planner cannot see it (it walked all 175k completed rows); the handler
  answers it without a query (`POCaseListFilter.matches_nothing`).
"""

from __future__ import annotations

from alembic import op

revision = '85d51fa0c98c'
down_revision = '98a7b592a040'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_po_cases_state_page ON supply_chain.po_cases"
        " (tenant_id, state, created_at DESC, id DESC)"
    )
    op.execute(
        "CREATE INDEX ix_po_cases_supplier_page ON supply_chain.po_cases"
        " (tenant_id, supplier_name, created_at DESC, id DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX supply_chain.ix_po_cases_supplier_page")
    op.execute("DROP INDEX supply_chain.ix_po_cases_state_page")
