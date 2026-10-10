"""supply chain stage-1 brief and question indexes

Revision ID: fd285c0433c4
Revises: 62cdcf3bf2d2
Create Date: 2026-10-07 11:48:32.125574+00:00

Stage-1 ticket 08 reads product cases two new ways; each read gets the index
that carries it, leading with the columns RLS supplies (`CLAUDE.md`, data
model rules), since the queries never name them:

- **The product-case list narrowed by Category** (a question "hồ sơ Chảo
  đang test mẫu", and the list page the answer links to) pages newest first
  like the state and PIC filters, so it gets their shape:
  `(tenant_id, workspace_id, category, created_at DESC, id DESC)`.
- **Sample rounds closed since the start of the day** (the daily brief's
  "Mẫu đã đánh giá hôm nay" and the daily report to TP Cung ứng, every tick
  of the report lane for every workspace): `(tenant_id, workspace_id,
  closed_at)`, partial on closed rounds, the only ones the read wants.

No table, column, policy or grant changes: the tables already carry RLS and
their grants, and an index needs none.
"""

from __future__ import annotations

from alembic import op

revision = "fd285c0433c4"
down_revision = "62cdcf3bf2d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_product_dev_cases_category_page ON supply_chain.product_dev_cases"
        " (tenant_id, workspace_id, category, created_at DESC, id DESC)"
    )
    op.execute(
        "CREATE INDEX ix_product_sample_rounds_closed_at ON supply_chain.product_sample_rounds"
        " (tenant_id, workspace_id, closed_at) WHERE closed_at IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX supply_chain.ix_product_sample_rounds_closed_at")
    op.execute("DROP INDEX supply_chain.ix_product_dev_cases_category_page")
