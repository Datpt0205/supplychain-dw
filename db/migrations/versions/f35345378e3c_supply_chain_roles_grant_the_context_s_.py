"""supply_chain roles grant the context's scopes

Revision ID: f35345378e3c
Revises: 165ce7ade5d7
Create Date: 2026-09-28 03:20:10.126076+00:00

`0001_platform_reference.sql` says a bounded context adds its own roles, or
its own scopes to the platform's, in a migration of its own. Supply Chain
never did: every scope its handlers check was held by no role, so outside
tests only `platform_admin` could reach it. This is that migration. The
mapping is a first proposal, meant to be reviewed and revised by a later
migration.

It follows NIST RBAC (INCITS 359), and the choices are:

- **Functional roles of the context's own, not scopes added to the platform
  ladder.** Being a platform `member` must not make someone a Supply Chain
  operator. A person gets these roles next to their platform role, since a
  membership holds several (`memberships.role_keys`). That is least
  privilege by assignment rather than by default.
- **Hierarchy: each role is a superset of `sc_viewer`.** "An operator can
  read at least what a viewer can" is a property of the catalogue, the same
  way the platform ladder is built.
- **Separation of duties** (NIST SP 800-53 AC-5, ISO/IEC 27001:2022 A.5.3):
  - The people who set the process rules (SLA thresholds, which actions need
    approval, the brief order) are not, by the same role, the people who run
    cases under them. `sc_process_admin` holds no case write and
    `sc_operator` holds no policy write.
  - Nothing here grants `approvals.*`. Approval authority stays on the
    platform ladder (`approver`/`manager`/`director`), and the
    `supply_chain.case_action.` strict prefix already refuses
    self-approval at decide time.
- **Spending is operation.** A supplier-update submission and a delay
  analysis each call a model, so only `sc_operator` holds those writes; a
  viewer reads results without spending.

What this cannot express, recorded rather than guessed:
- **Per-step duties**, such as only Finance confirming a deposit or payment,
  or only QC passing QC. Every transition is one scope,
  `supply_chain.po_case.write`, and which department owns which step differs
  per company. That needs a tenant-configurable action-to-permission matrix,
  the same shape as the approval matrix.
- **Enforced static SoD.** Nothing stops an admin assigning both
  `sc_operator` and `sc_process_admin` to one membership (a small company may
  need to). Refusing that combination at grant time would be a platform
  feature.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "f35345378e3c"
down_revision = "165ce7ade5d7"
branch_labels = None
depends_on = None

_VIEWER = [
    "supply_chain.po_case.read",
    "supply_chain.supplier_update.read",
    "supply_chain.delay_impact.read",
    "supply_chain.sla_policy.read",
    "supply_chain.approval_matrix.read",
    "supply_chain.brief_policy.read",
]

_ROLES = {
    "sc_viewer": ("Supply Chain — Xem", _VIEWER),
    "sc_operator": (
        "Supply Chain — Điều phối đơn hàng",
        [
            *_VIEWER,
            "supply_chain.po_case.write",
            "supply_chain.supplier_update.write",
            "supply_chain.delay_impact.write",
        ],
    ),
    "sc_process_admin": (
        "Supply Chain — Quản trị quy trình",
        [
            *_VIEWER,
            "supply_chain.sla_policy.write",
            "supply_chain.approval_matrix.write",
            "supply_chain.brief_policy.write",
        ],
    ),
}


def upgrade() -> None:
    statement = sa.text(
        "INSERT INTO platform.roles (key, name, scopes)"
        " VALUES (:key, :name, CAST(:scopes AS jsonb))"
        " ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, scopes = EXCLUDED.scopes"
    )
    for key, (name, scopes) in _ROLES.items():
        op.execute(statement.bindparams(key=key, name=name, scopes=json.dumps(scopes)))


def downgrade() -> None:
    # A membership still naming one of these keys then grants nothing for it:
    # role keys resolve against this table, so the missing row fails closed.
    for key in _ROLES:
        op.execute(sa.text("DELETE FROM platform.roles WHERE key = :key").bindparams(key=key))
