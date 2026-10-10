"""supply_chain paged reads carry their indexes

Revision ID: d9e136c14d83
Revises: cee9cf387387
Create Date: 2026-10-08 08:21:06.545723+00:00

Unbounded reads became paged (supply-chain ticket `unbounded-reads/01`), and
every new ORDER BY has an index that carries it, leading with what RLS
supplies (`tenant_id`, `workspace_id`) and which no query names:

- `ix_po_case_state_transitions_case_page`: a PO case's timeline, newest
  first, a page at a time.
- `ix_product_dev_case_transitions_case_page`: the same for a product case.
- `ix_product_dev_cases_page` gains `workspace_id` after `tenant_id`: the
  active product cases are now read a page at a time (the sweep, the brief),
  and the product case list already narrows by workspace. Rebuilt rather than
  kept beside a new one, as `6d4aed20ccf2` did for the approval inbox.

The active PO cases page on `ix_po_cases_page`, which `62cdcf3bf2d2` already
led with both.
"""

from __future__ import annotations

from alembic import op

revision = "d9e136c14d83"
down_revision = "cee9cf387387"
branch_labels = None
depends_on = None

_TRANSITION_PAGES = (
    ("ix_po_case_state_transitions_case_page", "po_case_state_transitions", "po_case_id"),
    (
        "ix_product_dev_case_transitions_case_page",
        "product_dev_case_state_transitions",
        "product_dev_case_id",
    ),
)


def upgrade() -> None:
    for name, table, case_column in _TRANSITION_PAGES:
        op.execute(
            f"CREATE INDEX {name} ON supply_chain.{table}"
            f" (tenant_id, workspace_id, {case_column}, occurred_at DESC, id DESC)"
        )
    op.execute("DROP INDEX supply_chain.ix_product_dev_cases_page")
    op.execute(
        "CREATE INDEX ix_product_dev_cases_page ON supply_chain.product_dev_cases"
        " (tenant_id, workspace_id, created_at DESC, id DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX supply_chain.ix_product_dev_cases_page")
    op.execute(
        "CREATE INDEX ix_product_dev_cases_page ON supply_chain.product_dev_cases"
        " (tenant_id, created_at DESC, id DESC)"
    )
    for name, _, _ in reversed(_TRANSITION_PAGES):
        op.execute(f"DROP INDEX supply_chain.{name}")
