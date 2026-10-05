"""supply_chain transitions gain a tenant time index

Revision ID: 165ce7ade5d7
Revises: 85d51fa0c98c
Create Date: 2026-09-28 02:10:06.114579+00:00

The daily brief asks "which cases moved in the last day" across a whole
tenant. `po_case_state_transitions` carried only
`(po_case_id, occurred_at)`, which serves one case's timeline and nothing
tenant-wide, so that question scanned the tenant's entire transition history
on every brief. The table is append-only and grows for ever (up to a dozen
rows per case, every case ever), so the scan would grow with it while the
answer — one day's activity — stays small.

`(tenant_id, occurred_at DESC)`: RLS supplies `tenant_id` and the query
never names it, which is why the index must lead with it; `occurred_at`
bounds the window. The query's own DISTINCT ON (po_case_id) then sorts only
the rows inside the window.
"""

from __future__ import annotations

from alembic import op

revision = '165ce7ade5d7'
down_revision = '85d51fa0c98c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_po_case_state_transitions_tenant_occurred_at"
        " ON supply_chain.po_case_state_transitions (tenant_id, occurred_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX supply_chain.ix_po_case_state_transitions_tenant_occurred_at")
