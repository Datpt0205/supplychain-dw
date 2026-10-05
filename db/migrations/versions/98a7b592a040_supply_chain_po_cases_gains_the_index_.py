"""supply_chain.po_cases gains the index its keyset list needs

Revision ID: 98a7b592a040
Revises: 1c26d9c88738
Create Date: 2026-09-25 02:07:08.147928+00:00

The Case Workspace's own list page is the first caller that lists cases
tenant-wide rather than by id — `po_cases` carried no index beyond its
primary key and the `UNIQUE(tenant_id, po_reference)` constraint, neither
of which serves `ORDER BY created_at DESC, id DESC`. Same shape
`0003_keyset_indexes.py` already gives `platform.approval_requests`:
`(tenant_id, created_at DESC, id DESC)`, `tenant_id` leading because RLS
supplies that equality on every statement and the query itself never
names it.
"""

from __future__ import annotations

from alembic import op

revision = '98a7b592a040'
down_revision = '1c26d9c88738'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_po_cases_page ON supply_chain.po_cases"
        " (tenant_id, created_at DESC, id DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX supply_chain.ix_po_cases_page")
