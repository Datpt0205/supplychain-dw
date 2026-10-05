"""supply_chain.po_case_state_transitions gains its own reason column

Revision ID: 1c26d9c88738
Revises: 61d934921951
Create Date: 2026-09-25 01:43:08.681942+00:00

`61d934921951`'s own transition-history table validates a human's stated
reason (fail_qc, wait_for_external, flag_blocked, flag_manual_review,
cancel all refuse a blank one) and then discards it — `POCase._interrupt`/
`cancel`/`fail_qc` never carried it into `_pending_transitions`, so nothing
downstream of the guarded method ever saw it again. Named as a real,
live gap once the `/transitions` route became a real caller (`.claude/
PLAN.md`, ninth slice) rather than fixed then, to keep that slice's diff
reviewable.

Nullable: the twelve no-reason actions (the happy path, `resume_from_
rework`, `resume`) never had one to carry, and inventing one for them
would be worse than admitting there is none — same reasoning `bb430ecd4f84`
already gives for `worker_runs`'s four nullable version columns.
"""

from __future__ import annotations

from alembic import op

revision = '1c26d9c88738'
down_revision = '61d934921951'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE supply_chain.po_case_state_transitions ADD COLUMN reason text")


def downgrade() -> None:
    op.execute("ALTER TABLE supply_chain.po_case_state_transitions DROP COLUMN reason")
