"""supply_chain.delay_impact_analyses — delay impact assumptions/mitigations, kept

Revision ID: 22b4b4a1a8ca
Revises: 325a40662260
Create Date: 2026-09-24 01:44:18.340175+00:00

No grants block, same reasoning as `325a40662260`: `fddd7579ba27`'s
`ALTER DEFAULT PRIVILEGES IN SCHEMA supply_chain` already covers a table
created later.

`impacted_milestones`/`assumptions`/`mitigation_options` are JSONB, not flat
columns — unlike `supplier_updates`, which has one fully-known shape per row,
each of these is itself a variable-length list of small structures nothing
here queries by individual element (matches `platform.approval_requests.
payload`'s and `platform.roles.scopes`'s own use of JSONB for the same
reason). Both `po_case_id` and `supplier_update_id` are `ON DELETE CASCADE`
— an analysis has no meaning without the case it is about or the update
that triggered it, same choice `supplier_updates.po_case_id` already made.
"""

from __future__ import annotations

from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401


revision = '22b4b4a1a8ca'
down_revision = '325a40662260'
branch_labels = None
depends_on = None

_TENANT_PREDICATE = (
    "(tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid)"
)

_UPGRADE = (
    """
    CREATE TABLE supply_chain.delay_impact_analyses (
        id uuid NOT NULL,
        tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        po_case_id uuid NOT NULL,
        supplier_update_id uuid NOT NULL,
        delay_days integer NOT NULL,
        impacted_milestones jsonb NOT NULL,
        assumptions jsonb NOT NULL,
        mitigation_options jsonb NOT NULL,
        created_at timestamp with time zone DEFAULT now() NOT NULL,
        CONSTRAINT pk_delay_impact_analyses PRIMARY KEY (id),
        CONSTRAINT fk_delay_impact_analyses_po_case_id_po_cases FOREIGN KEY (po_case_id)
            REFERENCES supply_chain.po_cases (id) ON DELETE CASCADE,
        CONSTRAINT fk_delay_impact_analyses_supplier_update_id_supplier_updates
            FOREIGN KEY (supplier_update_id)
            REFERENCES supply_chain.supplier_updates (id) ON DELETE CASCADE,
        CONSTRAINT ck_delay_impact_analyses_delay_days CHECK (delay_days >= 0)
    )
    """,
    "CREATE INDEX ix_delay_impact_analyses_po_case_id_created_at"
    " ON supply_chain.delay_impact_analyses (po_case_id, created_at)",
    # The second FK, indexed on its own side too (CLAUDE.md's rule) —
    # missed on the first pass of this migration, caught by the pre-commit
    # gate's own question rather than found later against a real table scan.
    "CREATE INDEX ix_delay_impact_analyses_supplier_update_id"
    " ON supply_chain.delay_impact_analyses (supplier_update_id)",
    "ALTER TABLE supply_chain.delay_impact_analyses ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE supply_chain.delay_impact_analyses FORCE ROW LEVEL SECURITY",
    f"CREATE POLICY tenant_isolation_delay_impact_analyses ON supply_chain.delay_impact_analyses"
    f" USING {_TENANT_PREDICATE} WITH CHECK {_TENANT_PREDICATE}",
)

_DOWNGRADE = ("DROP TABLE supply_chain.delay_impact_analyses",)


def upgrade() -> None:
    for statement in _UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in _DOWNGRADE:
        op.execute(statement)
