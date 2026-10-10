"""supply_chain imported open cases

Revision ID: e623edd08e76
Revises: 2bb10bdd4420
Create Date: 2026-10-10 00:30:00.000000+00:00

Open cases imported at their current state (ticket onboarding/02, ADR 0027
decision 3): a product case or a PO case starts in the state the sheet gives,
with ONE history row saying so, dated when the sheet says the case entered
that state (declared, not observed), and no row invented for the steps before.

- **Product case history.** `import` joins the actions (`ck_..._action`, which
  stays equal to `ProductAction`). `ck_..._only_propose_starts` becomes
  `ck_..._starts`: a row has no `from_state` exactly when it is `propose` or
  `import`.
- **PO case history.** `po_case_state_transitions` gains `action` (NULL for
  every step a person or a workflow takes, as before; `import` for the start
  row, `ck_..._action`), `from_state` becomes nullable, and
  `ck_..._import_starts`: a row has no `from_state` exactly when it is the
  import.
- **Only the first row.** On both tables, a partial UNIQUE index allows one
  start row per case (`uq_..._one_start`), and a trigger refuses a start row
  for a case that already has history (`refuse_late_start`): an import is the
  case's first row or nothing.
- Backdated times need no change: `occurred_at`, `created_at` and a round's
  `opened_at` default to `now()` and take a value the statement states; the
  `updated_at` triggers already yield to one.

Downgrade refuses while an imported row exists. Reviewed by reading; not run
here (no database on the machine that wrote it).
"""

from __future__ import annotations

from alembic import op

revision = "e623edd08e76"
down_revision = "2bb10bdd4420"
branch_labels = None
depends_on = None

_OLD_ACTIONS = (
    "propose",
    "request_sample",
    "receive_sample",
    "pass_sample",
    "request_revision",
    "receive_revised_sample",
    "reject_sample",
    "complete_profile",
    "confirm_with_supplier",
    "issue_item_code",
    "add_sku",
    "remove_sku",
    "submit_for_signoff",
    "place_order",
    "wait_for_external",
    "flag_blocked",
    "flag_manual_review",
    "resume",
    "cancel",
    "bod_approve",
    "bod_reject",
    "signoff_approve",
    "signoff_reject",
)
_NEW_ACTIONS = (*_OLD_ACTIONS, "import")
_PRODUCT = "supply_chain.product_dev_case_state_transitions"
_PO = "supply_chain.po_case_state_transitions"


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _actions(values: tuple[str, ...]) -> None:
    op.execute(
        f"""
        ALTER TABLE {_PRODUCT}
            DROP CONSTRAINT ck_product_dev_case_state_transitions_action,
            ADD CONSTRAINT ck_product_dev_case_state_transitions_action
                CHECK (action IN ({_in(values)}))
        """
    )


def upgrade() -> None:
    _actions(_NEW_ACTIONS)
    op.execute(
        f"""
        ALTER TABLE {_PRODUCT}
            DROP CONSTRAINT ck_product_dev_case_state_transitions_only_propose_starts,
            ADD CONSTRAINT ck_product_dev_case_state_transitions_starts
                CHECK ((from_state IS NULL) = (action IN ('propose', 'import')))
        """
    )
    op.execute(
        f"""
        ALTER TABLE {_PO}
            ADD COLUMN action text,
            ALTER COLUMN from_state DROP NOT NULL,
            ADD CONSTRAINT ck_po_case_state_transitions_action
                CHECK (action IS NULL OR action = 'import'),
            ADD CONSTRAINT ck_po_case_state_transitions_import_starts
                CHECK ((from_state IS NULL) = (action IS NOT NULL))
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_product_dev_case_state_transitions_one_start"
        f" ON {_PRODUCT} (tenant_id, workspace_id, product_dev_case_id)"
        " WHERE from_state IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_po_case_state_transitions_one_start"
        f" ON {_PO} (tenant_id, workspace_id, po_case_id) WHERE from_state IS NULL"
    )
    # A start row is the case's first row. Read under the inserting session's
    # own RLS, which is the case's tenant and workspace (the row's WITH CHECK).
    op.execute(
        """
        CREATE FUNCTION supply_chain.refuse_late_start() RETURNS trigger
            LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.from_state IS NOT NULL THEN
                RETURN NEW;
            END IF;
            IF TG_TABLE_NAME = 'po_case_state_transitions' THEN
                IF EXISTS (SELECT 1 FROM supply_chain.po_case_state_transitions
                           WHERE po_case_id = NEW.po_case_id) THEN
                    RAISE EXCEPTION 'a start row must be the first row of the case'
                        USING ERRCODE = 'check_violation';
                END IF;
            ELSIF EXISTS (SELECT 1 FROM supply_chain.product_dev_case_state_transitions
                          WHERE product_dev_case_id = NEW.product_dev_case_id) THEN
                RAISE EXCEPTION 'a start row must be the first row of the case'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    for table, name in ((_PRODUCT, "product"), (_PO, "po")):
        op.execute(
            f"CREATE TRIGGER trg_{name}_case_transitions_start_first BEFORE INSERT ON {table}"
            " FOR EACH ROW EXECUTE FUNCTION supply_chain.refuse_late_start()"
        )


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM {_PRODUCT} WHERE action = 'import')
               OR EXISTS (SELECT 1 FROM {_PO} WHERE action IS NOT NULL) THEN
                RAISE EXCEPTION 'imported cases exist; this downgrade would rewrite their history';
            END IF;
        END
        $$
        """
    )
    op.execute(f"DROP TRIGGER trg_product_case_transitions_start_first ON {_PRODUCT}")
    op.execute(f"DROP TRIGGER trg_po_case_transitions_start_first ON {_PO}")
    op.execute("DROP FUNCTION supply_chain.refuse_late_start()")
    op.execute("DROP INDEX supply_chain.uq_po_case_state_transitions_one_start")
    op.execute("DROP INDEX supply_chain.uq_product_dev_case_state_transitions_one_start")
    op.execute(
        f"""
        ALTER TABLE {_PO}
            DROP CONSTRAINT ck_po_case_state_transitions_import_starts,
            DROP CONSTRAINT ck_po_case_state_transitions_action,
            ALTER COLUMN from_state SET NOT NULL,
            DROP COLUMN action
        """
    )
    op.execute(
        f"""
        ALTER TABLE {_PRODUCT}
            DROP CONSTRAINT ck_product_dev_case_state_transitions_starts,
            ADD CONSTRAINT ck_product_dev_case_state_transitions_only_propose_starts
                CHECK ((from_state IS NULL) = (action = 'propose'))
        """
    )
    _actions(_OLD_ACTIONS)
