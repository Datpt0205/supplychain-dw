"""supply_chain duty roles and separation of duty rules

Revision ID: 2a0ac1ac32e1
Revises: f35345378e3c
Create Date: 2026-09-28 03:59:39.165859+00:00

Taking a step on a PO case now needs that step's duty
(`supply_chain.duty.<duty>`, see `dw_supply_chain.action_duties`), not the
general case write. This revises the roles of `f35345378e3c` to grant duties,
adds one role per duty, and records the context's separation-of-duty rules
in `platform.sod_rules`, which the membership trigger of `b9862fa13a80`
enforces.

Roles (every one contains `sc_viewer`):
- `sc_viewer`: reads, now including the step-to-duty mapping.
- `sc_operator`: opens cases, submits supplier updates, runs delay analyses;
  duties ordering and exceptions.
- `sc_finance`, `sc_qc`, `sc_logistics`, `sc_warehouse`: their own duty, plus
  exceptions. Anyone running cases may flag a blocked case or clear one.
- `sc_process_admin`: the process rules, now including which duty each step
  needs.

Rules. These are the classic procurement separations, and what an auditor
asks for first (NIST SP 800-53 AC-5, ISO/IEC 27001:2022 A.5.3):
- Whoever sets the process rules does not run cases under them.
- Ordering and paying are held apart, since the requester of a payment must
  not confirm it.
- Receiving and paying are held apart.
- Ordering and receiving are held apart.
- Ordering and QC are held apart: inspection stays independent of the buyer.

They are platform floors, the same for every tenant. A small company that
cannot staff them gets no waiver here; a per-tenant, audited exemption is a
decision recorded in PLAN, not a guess made in a migration.

The upgrade refuses to finish if any existing membership already breaks one
of the rules. The trigger judges only future writes, so a rule added over
data that already violates it would otherwise read as satisfied.

Ported onto the product repo (dw-elmichs), where this revision runs AFTER
`6b26771e549d` rather than before it: that revision replaced the two-argument
`sod_violation` with `sod_violation(tenant_id, role_keys, permission_set_keys)`,
so the check passes each membership's own tenant. The rules are inserted
non-waivable, so the answer is the same one the two-argument form gave.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "2a0ac1ac32e1"
down_revision = "f35345378e3c"
branch_labels = None
depends_on = None

_DUTY = "supply_chain.duty."
_POLICY_WRITES = [
    "supply_chain.sla_policy.write",
    "supply_chain.approval_matrix.write",
    "supply_chain.brief_policy.write",
    "supply_chain.action_duties.write",
]
_OPERATIONS = [
    "supply_chain.po_case.write",
    "supply_chain.supplier_update.write",
    "supply_chain.delay_impact.write",
    *(f"{_DUTY}{duty}" for duty in ("ordering", "finance", "qc", "logistics", "warehouse")),
    f"{_DUTY}exceptions",
]
_VIEWER = [
    "supply_chain.po_case.read",
    "supply_chain.supplier_update.read",
    "supply_chain.delay_impact.read",
    "supply_chain.sla_policy.read",
    "supply_chain.approval_matrix.read",
    "supply_chain.brief_policy.read",
    "supply_chain.action_duties.read",
]


def _duty_role(duty: str) -> list[str]:
    return [*_VIEWER, f"{_DUTY}{duty}", f"{_DUTY}exceptions"]


_ROLES = {
    "sc_viewer": ("Supply Chain — Xem", _VIEWER),
    "sc_operator": (
        "Supply Chain — Điều phối đơn hàng",
        [
            *_VIEWER,
            "supply_chain.po_case.write",
            "supply_chain.supplier_update.write",
            "supply_chain.delay_impact.write",
            f"{_DUTY}ordering",
            f"{_DUTY}exceptions",
        ],
    ),
    "sc_finance": ("Supply Chain — Kế toán", _duty_role("finance")),
    "sc_qc": ("Supply Chain — QC", _duty_role("qc")),
    "sc_logistics": ("Supply Chain — Logistics", _duty_role("logistics")),
    "sc_warehouse": ("Supply Chain — Kho", _duty_role("warehouse")),
    "sc_process_admin": ("Supply Chain — Quản trị quy trình", [*_VIEWER, *_POLICY_WRITES]),
}

_RULES = [
    (
        "sod_sc_rules_vs_operations",
        "Who sets the Supply Chain process rules does not run cases under them.",
        _POLICY_WRITES,
        _OPERATIONS,
    ),
    (
        "sod_sc_ordering_vs_payment",
        "Who orders or requests a payment does not confirm that it was paid.",
        [f"{_DUTY}ordering"],
        [f"{_DUTY}finance"],
    ),
    (
        "sod_sc_receiving_vs_payment",
        "Who receives the goods does not confirm their payment.",
        [f"{_DUTY}warehouse"],
        [f"{_DUTY}finance"],
    ),
    (
        "sod_sc_ordering_vs_receiving",
        "Who orders the goods does not receive them.",
        [f"{_DUTY}ordering"],
        [f"{_DUTY}warehouse"],
    ),
    (
        "sod_sc_ordering_vs_qc",
        "Who orders the goods does not pass them at QC.",
        [f"{_DUTY}ordering"],
        [f"{_DUTY}qc"],
    ),
]

# What `f35345378e3c` granted, for the downgrade.
_PREVIOUS = {
    "sc_viewer": _VIEWER[:-1],
    "sc_operator": [
        *_VIEWER[:-1],
        "supply_chain.po_case.write",
        "supply_chain.supplier_update.write",
        "supply_chain.delay_impact.write",
    ],
    "sc_process_admin": [*_VIEWER[:-1], *_POLICY_WRITES[:-1]],
}


def _upsert_role(key: str, name: str, scopes: list[str]) -> None:
    op.execute(
        sa.text(
            "INSERT INTO platform.roles (key, name, scopes)"
            " VALUES (:key, :name, CAST(:scopes AS jsonb))"
            " ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, scopes = EXCLUDED.scopes"
        ).bindparams(key=key, name=name, scopes=json.dumps(scopes))
    )


def upgrade() -> None:
    for key, (name, scopes) in _ROLES.items():
        _upsert_role(key, name, scopes)
    for key, description, left, right in _RULES:
        op.execute(
            sa.text(
                "INSERT INTO platform.sod_rules (key, description, left_scopes, right_scopes)"
                " VALUES (:key, :description, CAST(:left AS jsonb), CAST(:right AS jsonb))"
                " ON CONFLICT (key) DO UPDATE SET description = EXCLUDED.description,"
                " left_scopes = EXCLUDED.left_scopes, right_scopes = EXCLUDED.right_scopes"
            ).bindparams(
                key=key, description=description, left=json.dumps(left), right=json.dumps(right)
            )
        )
    op.execute(
        """
        DO $$
        DECLARE
            breaking integer;
        BEGIN
            SELECT count(*) INTO breaking
            FROM platform.memberships m
            WHERE EXISTS (
                SELECT 1 FROM platform.sod_violation(m.tenant_id, m.role_keys, m.permission_set_keys)
            );
            IF breaking > 0 THEN
                RAISE EXCEPTION
                    '% membership(s) already break a Supply Chain separation-of-duty rule',
                    breaking
                    USING HINT = 'Reassign their roles, then run this migration again.';
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    for key, *_ in _RULES:
        op.execute(sa.text("DELETE FROM platform.sod_rules WHERE key = :key").bindparams(key=key))
    for key in ("sc_finance", "sc_qc", "sc_logistics", "sc_warehouse"):
        op.execute(sa.text("DELETE FROM platform.roles WHERE key = :key").bindparams(key=key))
    for key, scopes in _PREVIOUS.items():
        _upsert_role(key, _ROLES[key][0], scopes)
