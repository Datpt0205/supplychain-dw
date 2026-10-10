"""merge platform channel twins

Revision ID: c2d7fc0648be
Revises: 85659fd91943, 395acbcad324
Create Date: 2026-10-07 17:09:04.545345+00:00

Joins the platform's channel branch (`02930a73bbdf`, `9f2becb1bf80`,
`5a25154e0296`, `e399be8c0a2d`, `395acbcad324`) to this repo's. Those five are
twins of this repo's `cf66605631d7`, `988592a8100f`, `4a865a1c97aa`,
`dbb8c3359981` and `8728e2fac660`; every one of the ten is idempotent, so an
existing database (twins applied) runs the platform branch as no-ops and a
fresh one runs whichever comes first for real.
"""

from __future__ import annotations

import sqlalchemy as sa  # noqa: F401
from alembic import op  # noqa: F401

revision = "c2d7fc0648be"
down_revision = ("85659fd91943", "395acbcad324")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
