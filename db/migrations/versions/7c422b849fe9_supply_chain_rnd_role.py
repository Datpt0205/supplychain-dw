"""supply chain rnd role

Revision ID: 7c422b849fe9
Revises: 59e69efdfa37
Create Date: 2026-10-05 15:44:22.000000+00:00

R&D joins the Supply Chain roles (stage-1 ticket 01, ADR 0016).

- `supply_chain.product_case.read` joins every `sc_*` role, as the document
  read did (f6a8142a6cd2): every holder of it sees every product case in its
  workspace; the PIC filter only narrows (QE-18).
- `supply_chain.product_case.write` (opening a product case, lead decision 9)
  joins every role that holds `supply_chain.po_case.write` (opening a PO
  case): read from the rows, so whoever opens one kind of case opens the other.
- `sc_rnd` is built the way `2a0ac1ac32e1` built the duty roles: everything
  `sc_viewer` holds (read from the row, so the reads later migrations added
  come with it), its own duty `supply_chain.duty.rnd`, and the document
  write: R&D uploads the Biên bản đánh giá mẫu and the Phiếu yêu cầu chỉnh
  sửa its steps need. Unlike the PO duty roles it does NOT hold
  `supply_chain.duty.exceptions` (lead decision 8): that duty is shared by both
  case kinds, so holding it would let R&D pause and resume PO cases. Under
  the platform policy the exception steps of a product case are taken by the
  PO operating roles; a tenant that wants R&D to take them maps them to `rnd`
  in its own `supply_chain_product_action_duties`, which touches no PO case.
- `supply_chain.duty.rnd` and `supply_chain.product_case.write` join the
  operations side of `sod_sc_rules_vs_operations`, so `sc_process_admin`
  cannot also test samples or open cases. No ordering-vs-R&D rule until
  Elmich answers QE-16.

No membership holds `sc_rnd` yet, and the product write goes only to roles
already holding the PO write, which is on the same side of the same rule, so
no existing membership can break a rule by this revision; the role-catalogue
integration test asks
`platform.sod_violation` which pairs conflict.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "7c422b849fe9"
down_revision = "59e69efdfa37"
branch_labels = None
depends_on = None

_READ = "supply_chain.product_case.read"
_WRITE = "supply_chain.product_case.write"
_PO_WRITE = "supply_chain.po_case.write"
_RND = "supply_chain.duty.rnd"
_SC_ROLES = (
    "sc_viewer",
    "sc_operator",
    "sc_finance",
    "sc_qc",
    "sc_logistics",
    "sc_warehouse",
    "sc_process_admin",
)
_RND_EXTRA = [_RND, "supply_chain.document.write"]
_RULE = "sod_sc_rules_vs_operations"


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE platform.roles SET scopes = scopes || '{json.dumps([_READ])}'::jsonb
        WHERE key IN ({_in(_SC_ROLES)}) AND NOT scopes ? '{_READ}'
        """
    )
    op.execute(
        f"""
        UPDATE platform.roles SET scopes = scopes || '{json.dumps([_WRITE])}'::jsonb
        WHERE key IN ({_in(_SC_ROLES)}) AND scopes ? '{_PO_WRITE}' AND NOT scopes ? '{_WRITE}'
        """
    )
    op.execute(
        sa.text(
            "INSERT INTO platform.roles (key, name, scopes)"
            " SELECT 'sc_rnd', 'Supply Chain — R&D', scopes || CAST(:extra AS jsonb)"
            " FROM platform.roles WHERE key = 'sc_viewer'"
            " ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, scopes = EXCLUDED.scopes"
        ).bindparams(extra=json.dumps(_RND_EXTRA))
    )
    for scope in (_RND, _WRITE):
        op.execute(
            f"""
            UPDATE platform.sod_rules
            SET right_scopes = right_scopes || '{json.dumps([scope])}'::jsonb
            WHERE key = '{_RULE}' AND NOT right_scopes ? '{scope}'
            """
        )


def downgrade() -> None:
    op.execute(
        f"UPDATE platform.sod_rules SET right_scopes = right_scopes - '{_RND}' - '{_WRITE}'"
        f" WHERE key = '{_RULE}'"
    )
    op.execute("DELETE FROM platform.roles WHERE key = 'sc_rnd'")
    op.execute(
        f"UPDATE platform.roles SET scopes = scopes - '{_READ}' - '{_WRITE}'"
        f" WHERE key IN ({_in(_SC_ROLES)})"
    )
