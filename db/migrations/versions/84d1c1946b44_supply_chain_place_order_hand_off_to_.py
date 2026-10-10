"""supply chain place order hand-off to the po case

Revision ID: 84d1c1946b44
Revises: 76bd1b5fc546
Create Date: 2026-10-07 07:10:25.118910+00:00

ĐẶT HÀNG: the product case ends `ordered` and opens the PO case of steps
10-17 in `order_requested`, in one transaction; step 10's `create_po` gives it
its PO reference (stage-1 ticket 05, ADR 0017).

- **Product case.** `ordered` joins the states and `place_order` the history's
  actions; the CHECKs that spell out a fixed set are rebuilt, as
  `76bd1b5fc546` did.
- **`po_cases`.** `order_requested` joins the states (the case and its
  history). `po_reference` becomes nullable under
  `ck_po_cases_po_reference`: absent only in `order_requested`, or `cancelled`
  from there, and never blank. New columns:
    - `order_kind` (`new | reorder`, CHECK). Every row that exists was opened by
      `CreatePOCase` with no stage 1 behind it, which ADR 0017 calls a reorder,
      so they are backfilled `reorder`; the default is then dropped, so every
      insert names its kind.
    - `product_dev_case_id`, composite FK `(tenant_id, workspace_id,
      product_dev_case_id)` to the product case, `ON DELETE RESTRICT`: an
      ordered product keeps its PO. NULL for a case `CreatePOCase` opened.
      UNIQUE `(tenant_id, workspace_id, product_dev_case_id)`, which is also
      the FK's index: one PO case per product case (amendment to ADR 0017,
      QE-12 open). The conditional update of `PlaceOrder` is the guard; this
      is the database's second answer to the same question.
    - `pic_user_id`, `category`: stamped from the product case row read in the
      same transaction; NULL on rows from before this revision (S6 says who is
      told then). `ck_po_cases_product_stamps`: a case from a product case
      carries both.
- **`po_case_lines`**: one line per SKU of the order, `quantity` NULL or above
  zero (NULL while the SKU's planned quantity is open, QE-11; `create_po`
  refuses to move on until every line has one). FK to the PO case `(tenant_id,
  workspace_id, po_case_id)` `ON DELETE CASCADE`, to the SKU `(tenant_id,
  workspace_id, sku_id)` `ON DELETE RESTRICT`; both indexed on this side
  (the UNIQUE `(tenant_id, workspace_id, po_case_id, sku_id)` leads with the
  first). The SKU FK needs a target, so `skus` gains UNIQUE `(tenant_id,
  workspace_id, id)`; the line FK's target on `po_cases` exists since
  `f6a8142a6cd2`. Tenant AND workspace
  scoped, RLS ENABLEd and FORCEd in the one workspace shape `CLAUDE.md`
  allows. `dw_app` may SELECT, INSERT and UPDATE `quantity`; never DELETE: a
  line leaves with its PO case (the offboarding purge deletes `po_cases`, in
  name order before `product_dev_cases`, and the cascade carries the lines).
- **Stored `supply_chain_action_duties` overrides gain `create_po: ordering`**
  (as `89e86dfabad6` did for the SLA policy): the schema requires every
  `CaseAction`, so an override without it would refuse every step of that
  tenant from this deploy on.

Downgrade REFUSES while any row needs what it would remove (a PO case awaiting
its PO, without a reference, from a product case, or with lines; a product
case ordered, or a history row naming the new state or action), and removes
`create_po` from the overrides only where it is exactly what this added.
"""

from __future__ import annotations

from alembic import op

revision = "84d1c1946b44"
down_revision = "76bd1b5fc546"
branch_labels = None
depends_on = None

_TENANT = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
_WORKSPACE = "workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
_SCOPE = "current_setting('app.workspace_scope', true) = 'tenant'"
_POLICY = f"({_TENANT} AND ({_WORKSPACE} OR {_SCOPE}))"

# `ProductDevState` / `ProductAction` before this revision (76bd1b5fc546).
_OLD_PRODUCT_STATES = (
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
    "pending_signoff",
    "ready_to_order",
)
_NEW_PRODUCT_STATES = (*_OLD_PRODUCT_STATES, "ordered")
_PRODUCT_INTERRUPTS = ("waiting_external", "blocked", "manual_review")
_OLD_PRODUCT_ACTIONS = (
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
    "issue_item_code",
    "add_sku",
    "remove_sku",
    "submit_for_signoff",
    "signoff_approve",
    "signoff_reject",
)
_NEW_PRODUCT_ACTIONS = (*_OLD_PRODUCT_ACTIONS, "place_order")

# `CaseState` before this revision (fddd7579ba27) and after it.
_OLD_CASE_STATES = (
    "po_created",
    "waiting_deposit",
    "deposit_confirmed",
    "pre_production",
    "production",
    "qc",
    "in_transit",
    "arrived_port",
    "waiting_payment",
    "payment_completed",
    "warehouse_receiving",
    "completed",
    "waiting_external",
    "blocked",
    "rework",
    "manual_review",
    "cancelled",
)
_NEW_CASE_STATES = ("order_requested", *_OLD_CASE_STATES)


# Public to `test_place_order.py`, which runs it over an override stored the
# way a tenant stored one before this revision.
ADD_CREATE_PO_TO_OVERRIDES = """
    UPDATE platform.policy_overrides
    SET content = jsonb_set(content, '{action_duties,create_po}', '"ordering"'::jsonb)
    WHERE policy_id = 'supply_chain_action_duties'
      AND jsonb_typeof(content -> 'action_duties') = 'object'
      AND NOT content -> 'action_duties' ? 'create_po'
"""


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _product_checks(states: tuple[str, ...], actions: tuple[str, ...]) -> None:
    op.execute(
        f"""
        ALTER TABLE supply_chain.product_dev_cases
            DROP CONSTRAINT ck_product_dev_cases_state,
            DROP CONSTRAINT ck_product_dev_cases_interrupted_state,
            ADD CONSTRAINT ck_product_dev_cases_state CHECK (state IN ({_in(states)})),
            ADD CONSTRAINT ck_product_dev_cases_interrupted_state CHECK (
                (state IN ({_in(_PRODUCT_INTERRUPTS)})) = (interrupted_state IS NOT NULL)
                AND (interrupted_state IS NULL OR interrupted_state IN ({_in(states)}))
                AND (interrupted_state IS NULL
                     OR interrupted_state NOT IN ({_in(_PRODUCT_INTERRUPTS)}))
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


def _case_checks(states: tuple[str, ...]) -> None:
    op.execute(
        f"""
        ALTER TABLE supply_chain.po_cases
            DROP CONSTRAINT ck_po_cases_state,
            ADD CONSTRAINT ck_po_cases_state CHECK (state IN ({_in(states)}))
        """
    )
    op.execute(
        f"""
        ALTER TABLE supply_chain.po_case_state_transitions
            DROP CONSTRAINT ck_po_case_state_transitions_from_state,
            DROP CONSTRAINT ck_po_case_state_transitions_to_state,
            ADD CONSTRAINT ck_po_case_state_transitions_from_state
                CHECK (from_state IN ({_in(states)})),
            ADD CONSTRAINT ck_po_case_state_transitions_to_state
                CHECK (to_state IN ({_in(states)}))
        """
    )


def upgrade() -> None:
    _product_checks(_NEW_PRODUCT_STATES, _NEW_PRODUCT_ACTIONS)
    _case_checks(_NEW_CASE_STATES)

    op.execute(
        """
        ALTER TABLE supply_chain.po_cases
            ALTER COLUMN po_reference DROP NOT NULL,
            DROP CONSTRAINT ck_po_cases_po_reference,
            ADD CONSTRAINT ck_po_cases_po_reference CHECK (
                CASE
                    WHEN po_reference IS NULL THEN state IN ('order_requested', 'cancelled')
                    ELSE po_reference <> ''
                END
            ),
            ADD COLUMN order_kind text DEFAULT 'reorder' NOT NULL,
            ADD COLUMN product_dev_case_id uuid,
            ADD COLUMN pic_user_id uuid,
            ADD COLUMN category text,
            ADD CONSTRAINT ck_po_cases_order_kind CHECK (order_kind IN ('new', 'reorder')),
            ADD CONSTRAINT ck_po_cases_category CHECK (
                category IS NULL OR (btrim(category) <> '' AND char_length(category) <= 100)
            ),
            ADD CONSTRAINT ck_po_cases_product_stamps CHECK (
                product_dev_case_id IS NULL
                OR (pic_user_id IS NOT NULL AND category IS NOT NULL)
            ),
            ADD CONSTRAINT uq_po_cases_tenant_id_workspace_id_product_dev_case_id
                UNIQUE (tenant_id, workspace_id, product_dev_case_id),
            ADD CONSTRAINT fk_po_cases_tenant_id_product_dev_cases
                FOREIGN KEY (tenant_id, workspace_id, product_dev_case_id)
                REFERENCES supply_chain.product_dev_cases (tenant_id, workspace_id, id)
                ON DELETE RESTRICT
        """
    )
    # Every existing row is a `CreatePOCase` one: a reorder (module docstring).
    # From here on an insert names its kind.
    op.execute("ALTER TABLE supply_chain.po_cases ALTER COLUMN order_kind DROP DEFAULT")

    op.execute(
        "ALTER TABLE supply_chain.skus"
        " ADD CONSTRAINT uq_skus_tenant_id_workspace_id_id UNIQUE (tenant_id, workspace_id, id)"
    )
    op.execute(
        """
        CREATE TABLE supply_chain.po_case_lines (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            po_case_id uuid NOT NULL,
            sku_id uuid NOT NULL,
            quantity integer,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_po_case_lines PRIMARY KEY (id),
            CONSTRAINT uq_po_case_lines_tenant_id_workspace_id_po_case_id_sku_id
                UNIQUE (tenant_id, workspace_id, po_case_id, sku_id),
            CONSTRAINT fk_po_case_lines_tenant_id_po_cases
                FOREIGN KEY (tenant_id, workspace_id, po_case_id)
                REFERENCES supply_chain.po_cases (tenant_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT fk_po_case_lines_tenant_id_skus
                FOREIGN KEY (tenant_id, workspace_id, sku_id)
                REFERENCES supply_chain.skus (tenant_id, workspace_id, id)
                ON DELETE RESTRICT,
            CONSTRAINT ck_po_case_lines_quantity CHECK (quantity IS NULL OR quantity > 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_po_case_lines_tenant_id_workspace_id_sku_id"
        " ON supply_chain.po_case_lines (tenant_id, workspace_id, sku_id)"
    )
    for statement in (
        "ALTER TABLE supply_chain.po_case_lines ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE supply_chain.po_case_lines FORCE ROW LEVEL SECURITY",
        "CREATE POLICY tenant_isolation_po_case_lines ON supply_chain.po_case_lines"
        f" USING {_POLICY} WITH CHECK {_POLICY}",
    ):
        op.execute(statement)
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                REVOKE UPDATE, DELETE, TRUNCATE ON supply_chain.po_case_lines FROM dw_app;
                GRANT UPDATE (quantity) ON supply_chain.po_case_lines TO dw_app;
            END IF;
        END
        $$
        """
    )

    op.execute(ADD_CREATE_PO_TO_OVERRIDES)


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM supply_chain.po_cases
                WHERE state = 'order_requested' OR po_reference IS NULL
                   OR product_dev_case_id IS NOT NULL
            ) OR EXISTS (
                SELECT 1 FROM supply_chain.po_case_state_transitions
                WHERE from_state = 'order_requested' OR to_state = 'order_requested'
            ) OR EXISTS (SELECT 1 FROM supply_chain.po_case_lines)
              OR EXISTS (
                SELECT 1 FROM supply_chain.product_dev_cases
                WHERE state = 'ordered' OR interrupted_state = 'ordered'
            ) OR EXISTS (
                SELECT 1 FROM supply_chain.product_dev_case_state_transitions
                WHERE action = 'place_order' OR from_state = 'ordered' OR to_state = 'ordered'
            ) THEN
                RAISE EXCEPTION
                    'cases hold the ĐẶT HÀNG hand-off; downgrading would rewrite them'
                    USING ERRCODE = 'check_violation';
            END IF;
        END
        $$
        """
    )
    op.execute(
        """
        UPDATE platform.policy_overrides
        SET content = content #- '{action_duties,create_po}'
        WHERE policy_id = 'supply_chain_action_duties'
          AND content -> 'action_duties' -> 'create_po' = '"ordering"'::jsonb
        """
    )
    op.execute("DROP TABLE supply_chain.po_case_lines")
    op.execute("ALTER TABLE supply_chain.skus DROP CONSTRAINT uq_skus_tenant_id_workspace_id_id")
    op.execute(
        """
        ALTER TABLE supply_chain.po_cases
            DROP CONSTRAINT fk_po_cases_tenant_id_product_dev_cases,
            DROP CONSTRAINT uq_po_cases_tenant_id_workspace_id_product_dev_case_id,
            DROP CONSTRAINT ck_po_cases_product_stamps,
            DROP CONSTRAINT ck_po_cases_category,
            DROP CONSTRAINT ck_po_cases_order_kind,
            DROP COLUMN category,
            DROP COLUMN pic_user_id,
            DROP COLUMN product_dev_case_id,
            DROP COLUMN order_kind,
            DROP CONSTRAINT ck_po_cases_po_reference,
            ADD CONSTRAINT ck_po_cases_po_reference CHECK (po_reference <> ''),
            ALTER COLUMN po_reference SET NOT NULL
        """
    )
    _case_checks(_OLD_CASE_STATES)
    _product_checks(_OLD_PRODUCT_STATES, _OLD_PRODUCT_ACTIONS)
