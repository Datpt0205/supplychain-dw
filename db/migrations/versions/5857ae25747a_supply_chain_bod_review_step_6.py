"""supply chain bod review step 6

Revision ID: 5857ae25747a
Revises: cbf765d02a12
Create Date: 2026-10-06 05:38:40.030929+00:00

BGĐ reviews a passed sample (stage-1 ticket 02, ADR 0016, ADR 0020).

- **States and actions.** `profile_in_progress` (BGĐ approved; R&D completes
  the BM04 profile, step 7 in S3) joins the case's states, and `bod_approve`,
  `bod_reject` join the history's actions. The five CHECKs that spell out a
  fixed set are rebuilt with the new members: `ck_product_dev_cases_state`,
  `ck_product_dev_cases_interrupted_state`, and the history's `action`,
  `from_state` and `to_state`. `test_the_state_and_action_checks_are_exactly_
  the_domains` asserts each equals its enum.
- **`sc_bod`** is built the way `7c422b849fe9` built `sc_rnd`: everything
  `sc_viewer` holds (read from the row; it carries
  `supply_chain.product_case.read` since `7c422b849fe9`), plus
  `supply_chain.product_case.read` named again for the record, plus
  `supply_chain.approve.bod`, the `required_scope` the platform policy
  `supply_chain_product_approvals@1.0.0` stamps on a BGĐ review. Deciding also
  needs `approvals.decide`, which no Supply Chain role grants: a BGĐ member
  holds a platform approval role beside it (`approver`, `manager`,
  `director`) or the `approver_boost` permission set. Duplicates are folded,
  so the role holds each scope once.
- **`supply_chain.approve.bod` joins the operations side of
  `sod_sc_rules_vs_operations`**: whoever sets the process rules does not also
  decide BGĐ's reviews. The rule count stays five.
- **`supply_chain.workspaces_awaiting_bod_review()`**, SECURITY DEFINER, the
  one read that crosses tenants for the worker's reconcile lane: (tenant,
  workspace) pairs holding a case in `pending_bod_review`, ids only, as
  `tenants_with_cases()` is for the follow-up sweep. Served by a partial
  index on exactly those rows.

Downgrade REFUSES while any row needs what it would remove (a case in
`profile_in_progress`, or paused from it, or a history row naming
`bod_approve`, `bod_reject` or `profile_in_progress`), rather than rewriting
history or a case's state to fit the older CHECKs. Cancelled-by-BGĐ cases are
`cancelled` like any other, but their `bod_reject` row still blocks it.
"""

from __future__ import annotations

import json

from alembic import op

revision = "5857ae25747a"
down_revision = "cbf765d02a12"
branch_labels = None
depends_on = None

# `ProductDevState` and `ProductAction` before this revision (59e69efdfa37) and
# after it.
_OLD_STATES = (
    "proposed",
    "sample_requested",
    "sample_testing",
    "revision_requested",
    "pending_bod_review",
    "waiting_external",
    "blocked",
    "manual_review",
    "cancelled",
)
_NEW_STATES = (*_OLD_STATES, "profile_in_progress")
_INTERRUPTS = ("waiting_external", "blocked", "manual_review")
_OLD_ACTIONS = (
    "propose",
    "request_sample",
    "receive_sample",
    "pass_sample",
    "request_revision",
    "receive_revised_sample",
    "reject_sample",
    "wait_for_external",
    "flag_blocked",
    "flag_manual_review",
    "resume",
    "cancel",
)
_NEW_ACTIONS = (*_OLD_ACTIONS, "bod_approve", "bod_reject")

_READ = "supply_chain.product_case.read"
_APPROVE_BOD = "supply_chain.approve.bod"
_RULE = "sod_sc_rules_vs_operations"


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _checks(states: tuple[str, ...], actions: tuple[str, ...]) -> None:
    op.execute(
        f"""
        ALTER TABLE supply_chain.product_dev_cases
            DROP CONSTRAINT ck_product_dev_cases_state,
            DROP CONSTRAINT ck_product_dev_cases_interrupted_state,
            ADD CONSTRAINT ck_product_dev_cases_state CHECK (state IN ({_in(states)})),
            ADD CONSTRAINT ck_product_dev_cases_interrupted_state CHECK (
                (state IN ({_in(_INTERRUPTS)})) = (interrupted_state IS NOT NULL)
                AND (interrupted_state IS NULL OR interrupted_state IN ({_in(states)}))
                AND (interrupted_state IS NULL OR interrupted_state NOT IN ({_in(_INTERRUPTS)}))
            )
        """
    )
    op.execute(
        f"""
        ALTER TABLE supply_chain.product_dev_case_state_transitions
            DROP CONSTRAINT ck_product_dev_case_state_transitions_action,
            DROP CONSTRAINT ck_product_dev_case_state_transitions_from_state,
            DROP CONSTRAINT ck_product_dev_case_state_transitions_to_state,
            ADD CONSTRAINT ck_product_dev_case_state_transitions_action
                CHECK (action IN ({_in(actions)})),
            ADD CONSTRAINT ck_product_dev_case_state_transitions_from_state
                CHECK (from_state IS NULL OR from_state IN ({_in(states)})),
            ADD CONSTRAINT ck_product_dev_case_state_transitions_to_state
                CHECK (to_state IN ({_in(states)}))
        """
    )


def upgrade() -> None:
    _checks(_NEW_STATES, _NEW_ACTIONS)

    op.execute(
        f"""
        INSERT INTO platform.roles (key, name, scopes)
        SELECT 'sc_bod', 'Supply Chain — BGĐ',
               (SELECT jsonb_agg(DISTINCT scope ORDER BY scope)
                FROM jsonb_array_elements_text(
                    v.scopes || '{json.dumps([_READ, _APPROVE_BOD])}'::jsonb
                ) AS scope)
        FROM platform.roles v WHERE v.key = 'sc_viewer'
        ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, scopes = EXCLUDED.scopes
        """
    )
    op.execute(
        f"""
        UPDATE platform.sod_rules
        SET right_scopes = right_scopes || '{json.dumps([_APPROVE_BOD])}'::jsonb
        WHERE key = '{_RULE}' AND NOT right_scopes ? '{_APPROVE_BOD}'
        """
    )

    op.execute(
        "CREATE INDEX ix_product_dev_cases_awaiting_bod_review"
        " ON supply_chain.product_dev_cases (tenant_id, workspace_id)"
        " WHERE state = 'pending_bod_review'"
    )
    op.execute(
        """
        CREATE FUNCTION supply_chain.workspaces_awaiting_bod_review()
        RETURNS TABLE (tenant_id uuid, workspace_id uuid)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, supply_chain
        AS $$
            SELECT DISTINCT c.tenant_id, c.workspace_id
            FROM supply_chain.product_dev_cases c
            WHERE c.state = 'pending_bod_review'
            ORDER BY c.tenant_id, c.workspace_id
        $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION supply_chain.workspaces_awaiting_bod_review() FROM PUBLIC"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                GRANT EXECUTE ON FUNCTION supply_chain.workspaces_awaiting_bod_review()
                    TO dw_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM supply_chain.product_dev_cases
                WHERE state = 'profile_in_progress'
                   OR interrupted_state = 'profile_in_progress'
            ) OR EXISTS (
                SELECT 1 FROM supply_chain.product_dev_case_state_transitions
                WHERE action IN ('bod_approve', 'bod_reject')
                   OR from_state = 'profile_in_progress'
                   OR to_state = 'profile_in_progress'
            ) THEN
                RAISE EXCEPTION
                    'product cases hold BGĐ review steps; downgrading would rewrite their history'
                    USING ERRCODE = 'check_violation';
            END IF;
        END
        $$
        """
    )
    op.execute("DROP FUNCTION supply_chain.workspaces_awaiting_bod_review()")
    op.execute("DROP INDEX supply_chain.ix_product_dev_cases_awaiting_bod_review")
    op.execute(
        f"UPDATE platform.sod_rules SET right_scopes = right_scopes - '{_APPROVE_BOD}'"
        f" WHERE key = '{_RULE}'"
    )
    op.execute("DELETE FROM platform.roles WHERE key = 'sc_bod'")
    _checks(_OLD_STATES, _OLD_ACTIONS)
