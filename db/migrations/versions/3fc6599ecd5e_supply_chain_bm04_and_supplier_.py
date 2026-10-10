"""supply chain bm04 and supplier confirmation steps 7 8

Revision ID: 3fc6599ecd5e
Revises: d4048e50d4a3
Create Date: 2026-10-07 02:55:25.444044+00:00

R&D completes the BM04 profile and TP Cung ứng confirms the product with the
supplier (stage-1 ticket 03, steps 7 and 8, ADR 0016).

- **States and actions.** `supplier_confirmation` and `item_coding` join the
  case's states, `complete_profile` and `confirm_with_supplier` the history's
  actions. The five CHECKs that spell out a fixed set are rebuilt with the new
  members, as `5857ae25747a` did; `test_the_state_and_action_checks_are_
  exactly_the_domains` asserts each equals its enum.
- **The step's paper on its history row.** `product_dev_case_state_transitions`
  gains `document_id`, a composite FK `(tenant_id, workspace_id,
  product_dev_case_id, document_id)` to `case_documents`' UNIQUE of the same
  shape (`59e69efdfa37`), `ON DELETE NO ACTION` like the rounds' papers: a
  BM04 or a supplier's email can only be one of THIS case's documents, and
  cannot be deleted on its own once a step was taken on it (offboarding
  deletes the case, and the cascade takes both in one statement). Indexed on
  its own side. CHECK `ck_product_dev_case_state_transitions_document_steps`:
  the two steps carry a document and no other history row does (a round's
  paper stays on the round). Which TYPE and how recent is the domain's rule
  (`ProductDevelopmentCase._step_document`), as for the rounds.
- **`sc_supply_lead`** (TP Cung ứng) is built the way `7c422b849fe9` built
  `sc_rnd`: everything `sc_viewer` holds (read from the row), its own duty
  `supply_chain.duty.supply_lead`, and the document write, for the supplier's
  email its step needs. No `duty.exceptions`, for the reason `sc_rnd` has
  none (that duty is shared with PO cases).
- **`supply_chain.duty.supply_lead` joins the operations side of
  `sod_sc_rules_vs_operations`**, as every duty does, so the process admin
  cannot also confirm with suppliers. The rule count stays five; no R&D
  versus TP Cung ứng rule until Elmich answers QE-16.

The new history column is covered by the table's existing INSERT and SELECT
grants and RLS; nothing new crosses tenants. No membership holds
`sc_supply_lead` yet, so no existing membership can break a rule.

Downgrade REFUSES while any row needs what it would remove (a case in or
paused from `supplier_confirmation` or `item_coding`, a history row naming
the new actions or states, or a membership holding `sc_supply_lead`), rather
than rewriting history to fit the older CHECKs.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "3fc6599ecd5e"
down_revision = "d4048e50d4a3"
branch_labels = None
depends_on = None

# `ProductDevState` and `ProductAction` before this revision (5857ae25747a) and
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
    "profile_in_progress",
)
_NEW_STATES = (*_OLD_STATES, "supplier_confirmation", "item_coding")
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
    "bod_approve",
    "bod_reject",
)
_PAPER_STEPS = ("complete_profile", "confirm_with_supplier")
_NEW_ACTIONS = (*_OLD_ACTIONS, *_PAPER_STEPS)

_SUPPLY_LEAD = "supply_chain.duty.supply_lead"
_SUPPLY_LEAD_EXTRA = [_SUPPLY_LEAD, "supply_chain.document.write"]
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
        ALTER TABLE supply_chain.product_dev_case_state_transitions
            ADD COLUMN document_id uuid,
            ADD CONSTRAINT fk_product_dev_case_state_transitions_tenant_id_case_documents
                FOREIGN KEY (tenant_id, workspace_id, product_dev_case_id, document_id)
                REFERENCES supply_chain.case_documents
                    (tenant_id, workspace_id, product_dev_case_id, id)
                ON DELETE NO ACTION,
            ADD CONSTRAINT ck_product_dev_case_state_transitions_document_steps
                CHECK ((action IN ({_in(_PAPER_STEPS)})) = (document_id IS NOT NULL))
        """
    )
    # The FK's own side: a document's delete (only ever offboarding's cascade)
    # looks its history rows up by it.
    op.execute(
        "CREATE INDEX ix_product_dev_case_transitions_tenant_id_document_id"
        " ON supply_chain.product_dev_case_state_transitions"
        " (tenant_id, workspace_id, product_dev_case_id, document_id)"
        " WHERE document_id IS NOT NULL"
    )

    op.execute(
        sa.text(
            "INSERT INTO platform.roles (key, name, scopes)"
            " SELECT 'sc_supply_lead', 'Supply Chain — TP Cung ứng',"
            "        (SELECT jsonb_agg(DISTINCT scope ORDER BY scope)"
            "         FROM jsonb_array_elements_text(v.scopes || CAST(:extra AS jsonb)) AS scope)"
            " FROM platform.roles v WHERE v.key = 'sc_viewer'"
            " ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, scopes = EXCLUDED.scopes"
        ).bindparams(extra=json.dumps(_SUPPLY_LEAD_EXTRA))
    )
    op.execute(
        f"""
        UPDATE platform.sod_rules
        SET right_scopes = right_scopes || '{json.dumps([_SUPPLY_LEAD])}'::jsonb
        WHERE key = '{_RULE}' AND NOT right_scopes ? '{_SUPPLY_LEAD}'
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM supply_chain.product_dev_cases
                WHERE state IN ('supplier_confirmation', 'item_coding')
                   OR interrupted_state IN ('supplier_confirmation', 'item_coding')
            ) OR EXISTS (
                SELECT 1 FROM supply_chain.product_dev_case_state_transitions
                WHERE action IN ({_in(_PAPER_STEPS)})
                   OR from_state IN ('supplier_confirmation', 'item_coding')
                   OR to_state IN ('supplier_confirmation', 'item_coding')
            ) OR EXISTS (
                SELECT 1 FROM platform.memberships WHERE role_keys ? 'sc_supply_lead'
            ) THEN
                RAISE EXCEPTION
                    'product cases or memberships hold steps 7-8; downgrading would rewrite them'
                    USING ERRCODE = 'check_violation';
            END IF;
        END
        $$
        """
    )
    op.execute(
        f"UPDATE platform.sod_rules SET right_scopes = right_scopes - '{_SUPPLY_LEAD}'"
        f" WHERE key = '{_RULE}'"
    )
    op.execute("DELETE FROM platform.roles WHERE key = 'sc_supply_lead'")
    op.execute("DROP INDEX supply_chain.ix_product_dev_case_transitions_tenant_id_document_id")
    op.execute(
        """
        ALTER TABLE supply_chain.product_dev_case_state_transitions
            DROP CONSTRAINT ck_product_dev_case_state_transitions_document_steps,
            DROP CONSTRAINT fk_product_dev_case_state_transitions_tenant_id_case_documents,
            DROP COLUMN document_id
        """
    )
    _checks(_OLD_STATES, _OLD_ACTIONS)
