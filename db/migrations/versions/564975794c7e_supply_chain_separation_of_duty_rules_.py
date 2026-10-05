"""supply chain separation of duty rules are waivable

Revision ID: 564975794c7e
Revises: 2a0ac1ac32e1
Create Date: 2026-09-28

The five Supply Chain rules (2a0ac1ac32e1) keep ordering, paying, receiving,
QC and rule-setting apart. They are business segregation, not platform
floors: a company too small to staff both sides of one may waive it for its
tenant, with a reason and an audit row (6b26771e549d). Đạt decided this on
2026-09-28.

The rules are this context's own, so marking them is this context's
migration, the same way it added them.
"""

from __future__ import annotations

from alembic import op

revision = "564975794c7e"
down_revision = "2a0ac1ac32e1"
branch_labels = None
depends_on = None

_RULES = (
    "sod_sc_rules_vs_operations",
    "sod_sc_ordering_vs_payment",
    "sod_sc_receiving_vs_payment",
    "sod_sc_ordering_vs_receiving",
    "sod_sc_ordering_vs_qc",
)


def upgrade() -> None:
    keys = ", ".join(f"'{key}'" for key in _RULES)
    op.execute(f"UPDATE platform.sod_rules SET waivable = true WHERE key IN ({keys})")


def downgrade() -> None:
    # Waivers of these rules stop lifting anything once they are floors again
    # (`sod_violation` reads `waivable`), so none may be open, and no
    # membership may rely on one.
    keys = ", ".join(f"'{key}'" for key in _RULES)
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM platform.sod_waivers
                WHERE rule_key IN ({keys}) AND revoked_at IS NULL
            ) THEN
                RAISE EXCEPTION 'open waivers of Supply Chain rules exist'
                    USING HINT = 'Revoke them first, once no membership relies on one.';
            END IF;
        END
        $$
        """
    )
    op.execute(f"UPDATE platform.sod_rules SET waivable = false WHERE key IN ({keys})")
