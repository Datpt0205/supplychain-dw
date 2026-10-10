"""merge platform twins required_scope and workspace keyset

Revision ID: 57ca5f1df964
Revises: 2c3c2f9ae9e1, 6d4aed20ccf2
Create Date: 2026-10-06 17:31:34.819140+00:00

Joins the platform branch (`36dabf47619c`, `6d4aed20ccf2`) to this repo's.
Those two are twins of this repo's `5d3965984679` and `cbf765d02a12`; every
one of the four is idempotent, so an existing database (twins applied) runs the
platform pair as no-ops and a fresh one runs whichever comes first for real.
"""

from __future__ import annotations

import sqlalchemy as sa  # noqa: F401
from alembic import op  # noqa: F401

revision = "57ca5f1df964"
down_revision = ("2c3c2f9ae9e1", "6d4aed20ccf2")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
