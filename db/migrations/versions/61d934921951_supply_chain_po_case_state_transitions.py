"""supply_chain.po_case_state_transitions — the SLA evaluator's own history

Revision ID: 61d934921951
Revises: 22b4b4a1a8ca
Create Date: 2026-09-24 03:36:23.564349+00:00

`fddd7579ba27`'s own docstring named this gap on purpose: "per-milestone SLA
timestamps... deliberately NOT added here: that is the SLA evaluator's own
slice, informed by how it actually needs to query, not guessed ahead of it."
This is that slice.

One row per state change, not one column per milestone on `po_cases` — a
column-per-milestone design breaks the moment a case revisits a state
(REWORK returns to PRODUCTION, an interrupt's `resume()` returns to whatever
it paused from), which is a real, modelled path here, not an edge case to
ignore. A history table has no such limit: each visit is its own row.
`from_state`/`to_state` reuse the exact CHECK list `fddd7579ba27` already
wrote for `po_cases.state` — same reasoning, this constraint only asks "is
this a name the application recognises", never "was this transition legal",
which stays owned by `POCase`'s own guarded methods.

No grants block, same reasoning as every `supply_chain` table after the
first: `fddd7579ba27`'s `ALTER DEFAULT PRIVILEGES IN SCHEMA supply_chain`
already covers a table created later. `po_case_id` is `ON DELETE CASCADE`
(a transition record has no meaning without the case it is about, same
choice `supplier_updates.po_case_id` already made). The index on
`(po_case_id, occurred_at)` is exactly the read the evaluator needs: the
most recent row for a case is "when did it enter its current state".
"""

from __future__ import annotations

from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401


revision = '61d934921951'
down_revision = '22b4b4a1a8ca'
branch_labels = None
depends_on = None

_TENANT_PREDICATE = (
    "(tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid)"
)

# Exactly `fddd7579ba27`'s own `_CASE_STATES` list — kept in sync by hand
# since a migration cannot import from a sibling module's constant.
_CASE_STATES = (
    "'po_created', 'waiting_deposit', 'deposit_confirmed', 'pre_production',"
    " 'production', 'qc', 'in_transit', 'arrived_port', 'waiting_payment',"
    " 'payment_completed', 'warehouse_receiving', 'completed',"
    " 'waiting_external', 'blocked', 'rework', 'manual_review', 'cancelled'"
)

_UPGRADE = (
    f"""
    CREATE TABLE supply_chain.po_case_state_transitions (
        id uuid NOT NULL,
        tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        po_case_id uuid NOT NULL,
        from_state text NOT NULL,
        to_state text NOT NULL,
        occurred_at timestamp with time zone DEFAULT now() NOT NULL,
        CONSTRAINT pk_po_case_state_transitions PRIMARY KEY (id),
        CONSTRAINT fk_po_case_state_transitions_po_case_id_po_cases FOREIGN KEY (po_case_id)
            REFERENCES supply_chain.po_cases (id) ON DELETE CASCADE,
        CONSTRAINT ck_po_case_state_transitions_from_state CHECK (from_state IN ({_CASE_STATES})),
        CONSTRAINT ck_po_case_state_transitions_to_state CHECK (to_state IN ({_CASE_STATES}))
    )
    """,
    "CREATE INDEX ix_po_case_state_transitions_po_case_id_occurred_at"
    " ON supply_chain.po_case_state_transitions (po_case_id, occurred_at)",
    "ALTER TABLE supply_chain.po_case_state_transitions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE supply_chain.po_case_state_transitions FORCE ROW LEVEL SECURITY",
    f"CREATE POLICY tenant_isolation_po_case_state_transitions ON supply_chain.po_case_state_transitions"
    f" USING {_TENANT_PREDICATE} WITH CHECK {_TENANT_PREDICATE}",
)

_DOWNGRADE = ("DROP TABLE supply_chain.po_case_state_transitions",)


def upgrade() -> None:
    for statement in _UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in _DOWNGRADE:
        op.execute(statement)
