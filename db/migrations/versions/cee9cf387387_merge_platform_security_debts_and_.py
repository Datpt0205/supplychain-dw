"""merge platform security debts and support access

Revision ID: cee9cf387387
Revises: c2d7fc0648be, f1576bf82a5a
Create Date: 2026-10-08 05:51:46.572946+00:00

Joins the platform's branch from `395acbcad324` (`ecb47f78702c` approval
decisions append-only, `f381f1694395` SoD waiver second person and role scope
recheck, `983b509c3f0f` channel deliveries pending expiry, `af8ee878b4ab` and
`f1576bf82a5a` support access) to this repo's head `c2d7fc0648be`. None of the
five has a twin here; each is idempotent on its own (objects added only if
missing, functions replaced, triggers dropped and recreated, grants
role-guarded), so a database that already carries this repo's branch runs them
once and a fresh one runs the two branches in either order.
"""

from __future__ import annotations

import sqlalchemy as sa  # noqa: F401
from alembic import op  # noqa: F401

revision = "cee9cf387387"
down_revision = ("c2d7fc0648be", "f1576bf82a5a")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
