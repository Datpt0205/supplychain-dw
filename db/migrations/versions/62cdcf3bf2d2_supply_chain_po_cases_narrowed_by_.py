"""supply chain po cases narrowed by workspace

Revision ID: 62cdcf3bf2d2
Revises: d56da3dd2146
Create Date: 2026-10-07 11:00:55.931749+00:00

A PO case is read only in its own workspace (port ticket 04, ADR 0017's open
item, decided 2026-10-07): a person who is only in W2 no longer sees W1's
cases of the same tenant, on the web or on Zalo. A department that needs
several workspaces is made a member of each.

- **RLS narrows by workspace** on `po_cases`, `po_case_state_transitions`,
  `supplier_updates` and `delay_impact_analyses`, the four supply-chain tables
  still tenant-only, in the one shape `CLAUDE.md` allows: tenant AND
  (workspace OR the offboarding scope), USING and WITH CHECK. `po_case_lines`,
  `case_documents` and `follow_ups` already had it; every `supply_chain`
  policy now does.
- **Children reference their case within the workspace.** Each child's FK to
  the case becomes `(tenant_id, workspace_id, po_case_id)` ->
  `po_cases (tenant_id, workspace_id, id)` (same names, `ON DELETE CASCADE`
  unchanged), and a delay analysis's FK to its supplier update
  `(tenant_id, workspace_id, supplier_update_id)` -> `supplier_updates
  (tenant_id, workspace_id, id)`, as `follow_ups`, `po_case_lines` and
  `case_documents` already do: no child can sit in a workspace its case is
  not in, whatever path wrote it. Every child is written with its case's
  workspace, so existing rows satisfy it; a row that did not would fail this
  revision loudly rather than be hidden by the new policy.
  `uq_supplier_updates_tenant_id_workspace_id_id` is the new FK target; the
  tenant-only `uq_po_cases_tenant_id_id` and `uq_supplier_updates_tenant_id_id`
  no FK names any more, and are dropped.
- **The page indexes carry the workspace** after `tenant_id`, the columns RLS
  now supplies (`ix_po_cases_page`, `_state_page`, `_supplier_page`, and the
  transitions' `ix_po_case_state_transitions_tenant_occurred_at`), same names.

The PO reference stays unique per tenant (`uq_po_cases_tenant_id_po_reference`):
a PO number is the company's, so a W2 member typing a number W1 already holds
is refused with "đã có trong công ty" and learns only that the number is taken.

No new SECURITY DEFINER function: the one lane that reads PO cases without a
person, the follow-up sweep, already iterates `workspaces_with_cases()` and
reads each workspace under its own RLS (`d56da3dd2146`). Offboarding reads
and purges through `app.workspace_scope = 'tenant'`, which the shape keeps.

Downgrade reverses each step: the four policies return to tenant-only, the
FKs and the page indexes to their tenant-only form, the two unique keys come
back. It loses no row.
"""

from __future__ import annotations

from alembic import op

revision = "62cdcf3bf2d2"
down_revision = "d56da3dd2146"
branch_labels = None
depends_on = None

_TENANT = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
_WORKSPACE = "workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
_SCOPE = "current_setting('app.workspace_scope', true) = 'tenant'"
_POLICY = f"({_TENANT} AND ({_WORKSPACE} OR {_SCOPE}))"
# The tenant-only shape the four tables were created with, for the downgrade.
_OLD_POLICY = f"({_TENANT})"

_NARROWED = (
    "po_cases",
    "po_case_state_transitions",
    "supplier_updates",
    "delay_impact_analyses",
)
_CHILDREN = ("supplier_updates", "delay_impact_analyses", "po_case_state_transitions")

# (name, table, the columns after the RLS ones)
_INDEXES = (
    ("ix_po_cases_page", "po_cases", "created_at DESC, id DESC"),
    ("ix_po_cases_state_page", "po_cases", "state, created_at DESC, id DESC"),
    ("ix_po_cases_supplier_page", "po_cases", "supplier_name, created_at DESC, id DESC"),
    (
        "ix_po_case_state_transitions_tenant_occurred_at",
        "po_case_state_transitions",
        "occurred_at DESC",
    ),
)


def _policies(policy: str) -> None:
    for table in _NARROWED:
        op.execute(f"DROP POLICY tenant_isolation_{table} ON supply_chain.{table}")
        op.execute(
            f"CREATE POLICY tenant_isolation_{table} ON supply_chain.{table}"
            f" USING {policy} WITH CHECK {policy}"
        )


def _indexes(leading: str) -> None:
    for name, table, tail in _INDEXES:
        op.execute(f"DROP INDEX supply_chain.{name}")
        op.execute(f"CREATE INDEX {name} ON supply_chain.{table} ({leading}, {tail})")


def upgrade() -> None:
    op.execute(
        "ALTER TABLE supply_chain.supplier_updates"
        " ADD CONSTRAINT uq_supplier_updates_tenant_id_workspace_id_id"
        " UNIQUE (tenant_id, workspace_id, id)"
    )
    for table in _CHILDREN:
        op.execute(
            f"ALTER TABLE supply_chain.{table}"
            f" DROP CONSTRAINT fk_{table}_tenant_id_po_cases,"
            f" ADD CONSTRAINT fk_{table}_tenant_id_po_cases"
            " FOREIGN KEY (tenant_id, workspace_id, po_case_id)"
            " REFERENCES supply_chain.po_cases (tenant_id, workspace_id, id) ON DELETE CASCADE"
        )
    op.execute(
        "ALTER TABLE supply_chain.delay_impact_analyses"
        " DROP CONSTRAINT fk_delay_impact_analyses_tenant_id_supplier_updates,"
        " ADD CONSTRAINT fk_delay_impact_analyses_tenant_id_supplier_updates"
        " FOREIGN KEY (tenant_id, workspace_id, supplier_update_id)"
        " REFERENCES supply_chain.supplier_updates (tenant_id, workspace_id, id)"
        " ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE supply_chain.supplier_updates DROP CONSTRAINT uq_supplier_updates_tenant_id_id"
    )
    op.execute("ALTER TABLE supply_chain.po_cases DROP CONSTRAINT uq_po_cases_tenant_id_id")
    _indexes("tenant_id, workspace_id")
    _policies(_POLICY)


def downgrade() -> None:
    _policies(_OLD_POLICY)
    _indexes("tenant_id")
    op.execute(
        "ALTER TABLE supply_chain.po_cases"
        " ADD CONSTRAINT uq_po_cases_tenant_id_id UNIQUE (tenant_id, id)"
    )
    op.execute(
        "ALTER TABLE supply_chain.supplier_updates"
        " ADD CONSTRAINT uq_supplier_updates_tenant_id_id UNIQUE (tenant_id, id)"
    )
    op.execute(
        "ALTER TABLE supply_chain.delay_impact_analyses"
        " DROP CONSTRAINT fk_delay_impact_analyses_tenant_id_supplier_updates,"
        " ADD CONSTRAINT fk_delay_impact_analyses_tenant_id_supplier_updates"
        " FOREIGN KEY (tenant_id, supplier_update_id)"
        " REFERENCES supply_chain.supplier_updates (tenant_id, id) ON DELETE CASCADE"
    )
    for table in _CHILDREN:
        op.execute(
            f"ALTER TABLE supply_chain.{table}"
            f" DROP CONSTRAINT fk_{table}_tenant_id_po_cases,"
            f" ADD CONSTRAINT fk_{table}_tenant_id_po_cases"
            " FOREIGN KEY (tenant_id, po_case_id)"
            " REFERENCES supply_chain.po_cases (tenant_id, id) ON DELETE CASCADE"
        )
    op.execute(
        "ALTER TABLE supply_chain.supplier_updates"
        " DROP CONSTRAINT uq_supplier_updates_tenant_id_workspace_id_id"
    )
