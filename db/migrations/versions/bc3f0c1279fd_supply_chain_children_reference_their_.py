"""supply chain children reference their case within the tenant

Revision ID: bc3f0c1279fd
Revises: dc2285c629d4
Create Date: 2026-10-05 07:00:46.299044+00:00

Every child of a PO case referenced `po_cases (id)` alone. Postgres checks a
foreign key without row security, so that FK only proved the case exists in
SOME tenant: the database would have accepted a tenant-B supplier update, delay
analysis, transition or follow-up pointing at a tenant-A case. Only the
handlers' RLS-scoped `po_case_repo.get` stood in the way, which is one layer
where the database should be a second.

Each FK now carries the tenant: `(tenant_id, po_case_id)` references
`po_cases (tenant_id, id)`, so a child row whose tenant differs from its
case's is refused by the database whatever path wrote it. The same for
`delay_impact_analyses.supplier_update_id`, which pointed across tenants the
same way. A composite FK needs a unique key to point at; `(tenant_id, id)` is
unique already because `id` is, and the constraint only makes that a target.

`ON DELETE CASCADE` is unchanged. The existing indexes on each child lead with
`po_case_id` (and `supplier_update_id`), which is what the cascade's lookup
filters on, so no new index is needed for the FK's own side.
"""

from __future__ import annotations

from alembic import op

revision = "bc3f0c1279fd"
down_revision = "dc2285c629d4"
branch_labels = None
depends_on = None

_CHILDREN = (
    "supplier_updates",
    "delay_impact_analyses",
    "po_case_state_transitions",
    "follow_ups",
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE supply_chain.po_cases"
        " ADD CONSTRAINT uq_po_cases_tenant_id_id UNIQUE (tenant_id, id)"
    )
    op.execute(
        "ALTER TABLE supply_chain.supplier_updates"
        " ADD CONSTRAINT uq_supplier_updates_tenant_id_id UNIQUE (tenant_id, id)"
    )
    for table in _CHILDREN:
        op.execute(
            f"ALTER TABLE supply_chain.{table}"
            f" DROP CONSTRAINT fk_{table}_po_case_id_po_cases,"
            f" ADD CONSTRAINT fk_{table}_tenant_id_po_cases"
            " FOREIGN KEY (tenant_id, po_case_id)"
            " REFERENCES supply_chain.po_cases (tenant_id, id) ON DELETE CASCADE"
        )
    op.execute(
        "ALTER TABLE supply_chain.delay_impact_analyses"
        " DROP CONSTRAINT fk_delay_impact_analyses_supplier_update_id_supplier_updates,"
        " ADD CONSTRAINT fk_delay_impact_analyses_tenant_id_supplier_updates"
        " FOREIGN KEY (tenant_id, supplier_update_id)"
        " REFERENCES supply_chain.supplier_updates (tenant_id, id) ON DELETE CASCADE"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE supply_chain.delay_impact_analyses"
        " DROP CONSTRAINT fk_delay_impact_analyses_tenant_id_supplier_updates,"
        " ADD CONSTRAINT fk_delay_impact_analyses_supplier_update_id_supplier_updates"
        " FOREIGN KEY (supplier_update_id)"
        " REFERENCES supply_chain.supplier_updates (id) ON DELETE CASCADE"
    )
    for table in _CHILDREN:
        op.execute(
            f"ALTER TABLE supply_chain.{table}"
            f" DROP CONSTRAINT fk_{table}_tenant_id_po_cases,"
            f" ADD CONSTRAINT fk_{table}_po_case_id_po_cases"
            " FOREIGN KEY (po_case_id)"
            " REFERENCES supply_chain.po_cases (id) ON DELETE CASCADE"
        )
    op.execute(
        "ALTER TABLE supply_chain.supplier_updates DROP CONSTRAINT uq_supplier_updates_tenant_id_id"
    )
    op.execute("ALTER TABLE supply_chain.po_cases DROP CONSTRAINT uq_po_cases_tenant_id_id")
