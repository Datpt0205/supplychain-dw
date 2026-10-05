"""supply chain sla overrides keep their supplier update cadence

Revision ID: 89e86dfabad6
Revises: 564975794c7e
Create Date: 2026-09-28

SLA policy 1.2.0 adds a required `supplier_update` block: after how long
without a supplier update a reminder is due, and after how long an
escalation is. Until now those were constants in code, 5 and 10 days. The
platform default moves to 1 and 2 days.

A tenant that wrote its own SLA policy before 1.2.0 has a stored document
without the block, which the schema now refuses on read. This gives each such
document the 5 and 10 days that tenant has been running on, so no tenant's
reminders change because of a deploy. A tenant who wants the new cadence
writes it.

Downgrade removes the block only where it is exactly what this added.
"""

from __future__ import annotations

from alembic import op

revision = "89e86dfabad6"
down_revision = "564975794c7e"
branch_labels = None
depends_on = None

_CADENCE = """'{"reminder_after": "5d", "escalation_after": "10d"}'::jsonb"""


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE platform.policy_overrides
        SET content = content || jsonb_build_object('supplier_update', {_CADENCE})
        WHERE policy_id = 'supply_chain_sla' AND NOT content ? 'supplier_update'
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE platform.policy_overrides
        SET content = content - 'supplier_update'
        WHERE policy_id = 'supply_chain_sla' AND content -> 'supplier_update' = {_CADENCE}
        """
    )
