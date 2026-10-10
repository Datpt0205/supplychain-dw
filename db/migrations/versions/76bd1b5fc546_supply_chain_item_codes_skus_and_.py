"""supply chain item codes skus and signoff step 9

Revision ID: 76bd1b5fc546
Revises: dbb8c3359981
Create Date: 2026-10-07 05:34:51.770332+00:00

Step 9: the official item code, its SKUs, and the sign-off by BGĐ and Kế toán
(stage-1 ticket 04, ADR 0016, ADR 0018, ADR 0020).

- **States and actions.** `pending_signoff` and `ready_to_order` join the
  case's states; `issue_item_code`, `add_sku`, `remove_sku`,
  `submit_for_signoff`, `signoff_approve` and `signoff_reject` the history's
  actions. The five CHECKs that spell out a fixed set are rebuilt with the new
  members, as `3fc6599ecd5e` did; `test_the_state_and_action_checks_are_
  exactly_the_domains` asserts each equals its enum. `product_dev_cases` gains
  `signoff_round` (how many times the case was submitted; it names the
  sign-off a decision belongs to).
- **`supply_chain.item_codes`**: one per case (UNIQUE `(tenant_id,
  product_dev_case_id)`), and a code is taken once per TENANT (UNIQUE
  `(tenant_id, code)`, ADR 0018): two people issuing the same code at once,
  one is refused by the database. The code is stored trimmed and non-blank
  (CHECK); its format is not checked until Elmich sends its rule (QE-11).
  `dw_app` may INSERT, and UPDATE only `code` (a correction before sign-off
  keeps the row, so its SKUs stay under it); never DELETE.
- **`supply_chain.skus`**: UNIQUE `(tenant_id, sku_code)`; `item_code_id` NOT
  NULL with a composite FK to its item code of the same case, workspace and
  tenant, so a SKU cannot exist without one nor sit under another case's;
  `variant_label` non-blank; `planned_quantity` NULL or above zero (QE-11).
  `dw_app` may INSERT and DELETE (`remove_sku`), never UPDATE.
- **The case FKs are CASCADE, the SKU's FK to its item code NO ACTION**, not
  RESTRICT as the ticket says: the offboarding purge deletes only tables
  `dw_app` may DELETE, in name order, and relies on the case's delete
  carrying its children away (as `59e69efdfa37` explains). `item_codes` is not
  purgeable (no DELETE), so the case's cascade is its only way out, and
  RESTRICT would refuse that cascade the moment it reached an item code
  before its SKUs. NO ACTION is checked at the end of the statement, by which
  time both are gone; any other delete of an item code with SKUs is refused
  exactly as RESTRICT would (`test_an_item_code_with_skus_cannot_be_deleted`).
- Both tables are tenant AND workspace scoped, RLS ENABLEd and FORCEd with the
  one workspace shape `CLAUDE.md` allows, written out per table for
  `verify_invariants.py`. Every FK has an index on its own side.
- **Kế toán signs through `sc_finance`** (QE-16 open): the role gains
  `supply_chain.approve.accounting`, the `required_scope` the platform policy
  `supply_chain_product_approvals@1.1.0` stamps on the accounting sign-off
  step. The scope joins the operations side of `sod_sc_rules_vs_operations`,
  as `supply_chain.approve.bod` did; every `sc_finance` holder already holds an
  operations scope (`duty.finance`), so no existing membership starts breaking
  a rule.
- **`supply_chain.workspaces_awaiting_product_approval()`** replaces
  `workspaces_awaiting_bod_review()`: the reconcile lane now raises the
  sign-off too, so it visits workspaces holding a case in `pending_bod_review`
  OR `pending_signoff`. SECURITY DEFINER, ids only, served by a partial index
  on exactly those rows.

Downgrade REFUSES while any row needs what it would remove (a case in or
paused from the new states, a history row naming the new actions or states,
an item code or a SKU, or a case submitted for sign-off), rather than
rewriting history to fit the older CHECKs.
"""

from __future__ import annotations

import json

from alembic import op

revision = "76bd1b5fc546"
down_revision = "dbb8c3359981"
branch_labels = None
depends_on = None

_TENANT = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
_WORKSPACE = "workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
_SCOPE = "current_setting('app.workspace_scope', true) = 'tenant'"
_POLICY = f"({_TENANT} AND ({_WORKSPACE} OR {_SCOPE}))"

# `ProductDevState` and `ProductAction` before this revision (3fc6599ecd5e) and
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
    "supplier_confirmation",
    "item_coding",
)
_ADDED_STATES = ("pending_signoff", "ready_to_order")
_NEW_STATES = (*_OLD_STATES, *_ADDED_STATES)
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
    "complete_profile",
    "confirm_with_supplier",
)
_ADDED_ACTIONS = (
    "issue_item_code",
    "add_sku",
    "remove_sku",
    "submit_for_signoff",
    "signoff_approve",
    "signoff_reject",
)
_NEW_ACTIONS = (*_OLD_ACTIONS, *_ADDED_ACTIONS)
_AWAITING = ("pending_bod_review", "pending_signoff")

_APPROVE_ACCOUNTING = "supply_chain.approve.accounting"
_RULE = "sod_sc_rules_vs_operations"

_CASE_FK = (
    "FOREIGN KEY (tenant_id, workspace_id, product_dev_case_id)"
    " REFERENCES supply_chain.product_dev_cases (tenant_id, workspace_id, id) ON DELETE CASCADE"
)


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


def _awaiting_function(name: str, states: tuple[str, ...]) -> None:
    op.execute(
        f"""
        CREATE FUNCTION supply_chain.{name}()
        RETURNS TABLE (tenant_id uuid, workspace_id uuid)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, supply_chain
        AS $$
            SELECT DISTINCT c.tenant_id, c.workspace_id
            FROM supply_chain.product_dev_cases c
            WHERE c.state IN ({_in(states)})
            ORDER BY c.tenant_id, c.workspace_id
        $$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION supply_chain.{name}() FROM PUBLIC")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                GRANT EXECUTE ON FUNCTION supply_chain.{name}() TO dw_app;
            END IF;
        END
        $$
        """
    )


def upgrade() -> None:
    _checks(_NEW_STATES, _NEW_ACTIONS)
    op.execute(
        """
        ALTER TABLE supply_chain.product_dev_cases
            ADD COLUMN signoff_round integer DEFAULT 0 NOT NULL,
            ADD CONSTRAINT ck_product_dev_cases_signoff_round CHECK (signoff_round >= 0)
        """
    )

    op.execute(
        f"""
        CREATE TABLE supply_chain.item_codes (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            product_dev_case_id uuid NOT NULL,
            code text NOT NULL,
            issued_by uuid NOT NULL,
            issued_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_item_codes PRIMARY KEY (id),
            CONSTRAINT uq_item_codes_tenant_id_code UNIQUE (tenant_id, code),
            CONSTRAINT uq_item_codes_tenant_id_product_dev_case_id
                UNIQUE (tenant_id, product_dev_case_id),
            CONSTRAINT uq_item_codes_tenant_id_workspace_id_product_dev_case_id_id
                UNIQUE (tenant_id, workspace_id, product_dev_case_id, id),
            CONSTRAINT fk_item_codes_tenant_id_product_dev_cases {_CASE_FK},
            CONSTRAINT ck_item_codes_code
                CHECK (code = btrim(code) AND code <> '' AND char_length(code) <= 100)
        )
        """
    )
    # The case FK's own side is `uq_item_codes_tenant_id_product_dev_case_id`
    # (tenant, then the case, whose id is unique), as for the S1 children.
    op.execute(
        "CREATE TRIGGER touch_updated_at BEFORE UPDATE ON supply_chain.item_codes"
        " FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at()"
    )

    op.execute(
        f"""
        CREATE TABLE supply_chain.skus (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            product_dev_case_id uuid NOT NULL,
            item_code_id uuid NOT NULL,
            sku_code text NOT NULL,
            variant_label text NOT NULL,
            planned_quantity integer,
            added_by uuid NOT NULL,
            added_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_skus PRIMARY KEY (id),
            CONSTRAINT uq_skus_tenant_id_sku_code UNIQUE (tenant_id, sku_code),
            CONSTRAINT fk_skus_tenant_id_product_dev_cases {_CASE_FK},
            CONSTRAINT fk_skus_tenant_id_item_codes
                FOREIGN KEY (tenant_id, workspace_id, product_dev_case_id, item_code_id)
                REFERENCES supply_chain.item_codes
                    (tenant_id, workspace_id, product_dev_case_id, id)
                ON DELETE NO ACTION,
            CONSTRAINT ck_skus_sku_code
                CHECK (sku_code = btrim(sku_code) AND sku_code <> ''
                       AND char_length(sku_code) <= 100),
            CONSTRAINT ck_skus_variant_label
                CHECK (variant_label = btrim(variant_label) AND variant_label <> ''
                       AND char_length(variant_label) <= 200),
            CONSTRAINT ck_skus_planned_quantity
                CHECK (planned_quantity IS NULL OR planned_quantity > 0)
        )
        """
    )
    # Both FKs' own side, and the case page's list (tenant, workspace, case).
    op.execute(
        "CREATE INDEX ix_skus_tenant_id_workspace_id_case_item_code_id ON supply_chain.skus"
        " (tenant_id, workspace_id, product_dev_case_id, item_code_id)"
    )

    for statement in (
        "ALTER TABLE supply_chain.item_codes ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE supply_chain.item_codes FORCE ROW LEVEL SECURITY",
        "CREATE POLICY tenant_isolation_item_codes ON supply_chain.item_codes",
        "ALTER TABLE supply_chain.skus ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE supply_chain.skus FORCE ROW LEVEL SECURITY",
        "CREATE POLICY tenant_isolation_skus ON supply_chain.skus",
    ):
        if statement.startswith("CREATE POLICY"):
            statement += f" USING {_POLICY} WITH CHECK {_POLICY}"
        op.execute(statement)
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                REVOKE UPDATE, DELETE, TRUNCATE ON supply_chain.item_codes FROM dw_app;
                GRANT UPDATE (code) ON supply_chain.item_codes TO dw_app;
                REVOKE UPDATE, TRUNCATE ON supply_chain.skus FROM dw_app;
            END IF;
        END
        $$
        """
    )

    op.execute(
        f"""
        UPDATE platform.roles
        SET scopes = (
            SELECT jsonb_agg(DISTINCT scope ORDER BY scope)
            FROM jsonb_array_elements_text(scopes || '{json.dumps([_APPROVE_ACCOUNTING])}'::jsonb)
                AS scope
        )
        WHERE key = 'sc_finance'
        """
    )
    op.execute(
        f"""
        UPDATE platform.sod_rules
        SET right_scopes = right_scopes || '{json.dumps([_APPROVE_ACCOUNTING])}'::jsonb
        WHERE key = '{_RULE}' AND NOT right_scopes ? '{_APPROVE_ACCOUNTING}'
        """
    )

    op.execute("DROP FUNCTION supply_chain.workspaces_awaiting_bod_review()")
    op.execute("DROP INDEX supply_chain.ix_product_dev_cases_awaiting_bod_review")
    op.execute(
        "CREATE INDEX ix_product_dev_cases_awaiting_approval"
        " ON supply_chain.product_dev_cases (tenant_id, workspace_id)"
        f" WHERE state IN ({_in(_AWAITING)})"
    )
    _awaiting_function("workspaces_awaiting_product_approval", _AWAITING)


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM supply_chain.product_dev_cases
                WHERE state IN ({_in(_ADDED_STATES)})
                   OR interrupted_state IN ({_in(_ADDED_STATES)})
                   OR signoff_round > 0
            ) OR EXISTS (
                SELECT 1 FROM supply_chain.product_dev_case_state_transitions
                WHERE action IN ({_in(_ADDED_ACTIONS)})
                   OR from_state IN ({_in(_ADDED_STATES)})
                   OR to_state IN ({_in(_ADDED_STATES)})
            ) OR EXISTS (SELECT 1 FROM supply_chain.item_codes)
              OR EXISTS (SELECT 1 FROM supply_chain.skus) THEN
                RAISE EXCEPTION
                    'product cases hold step 9; downgrading would rewrite them'
                    USING ERRCODE = 'check_violation';
            END IF;
        END
        $$
        """
    )
    op.execute("DROP FUNCTION supply_chain.workspaces_awaiting_product_approval()")
    op.execute("DROP INDEX supply_chain.ix_product_dev_cases_awaiting_approval")
    op.execute(
        "CREATE INDEX ix_product_dev_cases_awaiting_bod_review"
        " ON supply_chain.product_dev_cases (tenant_id, workspace_id)"
        " WHERE state = 'pending_bod_review'"
    )
    _awaiting_function("workspaces_awaiting_bod_review", ("pending_bod_review",))
    op.execute(
        f"UPDATE platform.sod_rules SET right_scopes = right_scopes - '{_APPROVE_ACCOUNTING}'"
        f" WHERE key = '{_RULE}'"
    )
    op.execute(
        f"UPDATE platform.roles SET scopes = scopes - '{_APPROVE_ACCOUNTING}'"
        " WHERE key = 'sc_finance'"
    )
    op.execute("DROP TABLE supply_chain.skus")
    op.execute("DROP TABLE supply_chain.item_codes")
    op.execute(
        """
        ALTER TABLE supply_chain.product_dev_cases
            DROP CONSTRAINT ck_product_dev_cases_signoff_round,
            DROP COLUMN signoff_round
        """
    )
    _checks(_OLD_STATES, _OLD_ACTIONS)
