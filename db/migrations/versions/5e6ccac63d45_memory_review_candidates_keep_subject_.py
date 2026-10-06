"""memory review candidates keep subject and fact key

Revision ID: 5e6ccac63d45
Revises: a4104b95722b
Create Date: 2026-10-06

A candidate the policy holds for review is now written later, by the approval
that a person decides (`platform-runtime/memory-review-queue`). The item is
built from the candidate row, and the row lacked two of the item's fields:

- `subject_refs`, which recall matches on. A memory promoted without it is
  stored and never recalled: `?|` against an empty list matches nothing.
- `fact_key`, which supersession closes the older answer by. Without it a
  reviewed fact would accumulate beside the one it was meant to replace.

`subject_refs` defaults to `'[]'` like the column on `memory.items`; an empty
list narrows recall rather than widening it, so the default fails closed. Rows
written before this revision have no approval and are never promoted, so
nothing reads their empty value as a claim.

Reversible: both columns drop.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "5e6ccac63d45"
down_revision = "a4104b95722b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "write_candidates",
        sa.Column("subject_refs", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        schema="memory",
    )
    op.add_column(
        "write_candidates",
        sa.Column("fact_key", sa.Text, nullable=True),
        schema="memory",
    )


def downgrade() -> None:
    op.drop_column("write_candidates", "fact_key", schema="memory")
    op.drop_column("write_candidates", "subject_refs", schema="memory")
