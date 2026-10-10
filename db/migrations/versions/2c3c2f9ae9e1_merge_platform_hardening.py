"""merge platform hardening

Revision ID: 2c3c2f9ae9e1
Revises: 5857ae25747a, 7bbd071748ca
Create Date: 2026-10-06 14:04:23.833501+00:00
"""

from __future__ import annotations

import sqlalchemy as sa  # noqa: F401
from alembic import op  # noqa: F401

revision = "2c3c2f9ae9e1"
down_revision = ("5857ae25747a", "7bbd071748ca")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
