"""supply_chain.supplier_updates — the supplier update extraction, kept

Revision ID: 325a40662260
Revises: fddd7579ba27
Create Date: 2026-09-23 10:24:51.708536+00:00

No grants block here, unlike the migration that created `supply_chain`
itself: `fddd7579ba27`'s `ALTER DEFAULT PRIVILEGES IN SCHEMA supply_chain
... TO dw_app` already covers a table the migrator creates later, same
reasoning `2b5ff2bceb06_tenant_daily_spend_guard.py` gives for `platform`.

`event_type`'s CHECK list is `dw_supply_chain.domain.supplier_update.
SupplierEventType`'s values exactly — provisional, not Elmich-confirmed, same
status as `sla_policy.py`'s reference numbers; see that enum's own docstring.
`po_case_id` is `ON DELETE CASCADE` (a supplier update has no meaning without
its case), same choice `approval_decisions.request_id -> approval_requests.id`
already made in the baseline for the same shape of relationship.
"""

from __future__ import annotations

from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401


revision = '325a40662260'
down_revision = 'fddd7579ba27'
branch_labels = None
depends_on = None

_TENANT_PREDICATE = (
    "(tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid)"
)

_EVENT_TYPES = (
    "'production_delay', 'qc_issue', 'shipment_update', 'deposit_confirmation',"
    " 'document_submitted', 'no_official_update', 'other'"
)

_UPGRADE = (
    f"""
    CREATE TABLE supply_chain.supplier_updates (
        id uuid NOT NULL,
        tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        po_case_id uuid NOT NULL,
        raw_text text NOT NULL,
        event_type text NOT NULL,
        affected_po text,
        delay_days integer,
        reason text NOT NULL,
        proposed_action text NOT NULL,
        confidence numeric(3, 2) NOT NULL,
        source_ref text NOT NULL,
        requires_confirmation boolean NOT NULL,
        created_at timestamp with time zone DEFAULT now() NOT NULL,
        CONSTRAINT pk_supplier_updates PRIMARY KEY (id),
        CONSTRAINT fk_supplier_updates_po_case_id_po_cases FOREIGN KEY (po_case_id)
            REFERENCES supply_chain.po_cases (id) ON DELETE CASCADE,
        CONSTRAINT ck_supplier_updates_event_type CHECK (event_type IN ({_EVENT_TYPES})),
        CONSTRAINT ck_supplier_updates_confidence CHECK (confidence >= 0 AND confidence <= 1),
        CONSTRAINT ck_supplier_updates_delay_days CHECK (delay_days IS NULL OR delay_days >= 0),
        CONSTRAINT ck_supplier_updates_raw_text CHECK (raw_text <> ''),
        CONSTRAINT ck_supplier_updates_reason CHECK (reason <> ''),
        CONSTRAINT ck_supplier_updates_proposed_action CHECK (proposed_action <> ''),
        CONSTRAINT ck_supplier_updates_source_ref CHECK (source_ref <> '')
    )
    """,
    # Indexed on its own side (CLAUDE.md's FK rule) and doubles as the index a
    # "list updates for this case, newest first" query needs.
    "CREATE INDEX ix_supplier_updates_po_case_id_created_at"
    " ON supply_chain.supplier_updates (po_case_id, created_at)",
    "ALTER TABLE supply_chain.supplier_updates ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE supply_chain.supplier_updates FORCE ROW LEVEL SECURITY",
    f"CREATE POLICY tenant_isolation_supplier_updates ON supply_chain.supplier_updates"
    f" USING {_TENANT_PREDICATE} WITH CHECK {_TENANT_PREDICATE}",
)

_DOWNGRADE = ("DROP TABLE supply_chain.supplier_updates",)


def upgrade() -> None:
    for statement in _UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in _DOWNGRADE:
        op.execute(statement)
