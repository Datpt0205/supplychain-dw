"""supply chain packaging designs step 12

Revision ID: 85659fd91943
Revises: 8728e2fac660
Create Date: 2026-10-07 14:30:00.000000+00:00

Step 12's colour, packaging and pre-production sub-flow on a PO case (slice PK,
`packaging-design/issues/01`).

- **`supply_chain.packaging_designs`**, one row per PO case (UNIQUE
  `(tenant_id, workspace_id, po_case_id)`, which is also the FK's index): FK
  `(tenant_id, workspace_id, po_case_id)` -> `po_cases (tenant_id, workspace_id,
  id)` `ON DELETE CASCADE`, the workspace-narrowed shape `62cdcf3bf2d2` gave
  every child of a PO case (the ticket's tenant-only FK predates it), so no row
  can sit in a workspace its case is not in. `colour_status`, `design_status`
  (CHECK `pending | revision_requested | approved`),
  `pre_production_sample_received_at`, `pre_production_test` (CHECK
  `pending | passed | failed`), `version` (CHECK >= 1). CHECK
  `ck_packaging_designs_order`: the sub-flow's order holds in the row itself —
  no design approved before the colour, no sample before the design, no test
  result before the sample — whatever path wrote it.
  `updated_at` by `platform.touch_updated_at()`, which yields to a stated value.
- **`supply_chain.packaging_design_events`**, the history (as
  `po_case_state_transitions` is the case's): one row per step, `action` (CHECK,
  the seven `PackagingAction`s), `actor_id`, `reason`, `note`, `document_id`,
  `occurred_at`. FK to the design row `(tenant_id, workspace_id, po_case_id)`
  `ON DELETE CASCADE`; the test report a step was taken on, FK `(tenant_id,
  workspace_id, po_case_id, document_id)` -> `case_documents` (new UNIQUE
  `uq_case_documents_tenant_id_workspace_id_po_case_id_id`) `ON DELETE NO
  ACTION` as `3fc6599ecd5e` did for steps 7-8: the report is one of THIS case's
  papers, and is not deleted on its own once a test was recorded on it.
  CHECK: the two test steps carry a document and no other step does; the
  three sending-back steps carry a reason. Indexed on each FK's side.
- **RLS** ENABLE and FORCE on both, in the one workspace shape `CLAUDE.md`
  allows: tenant AND (workspace OR the offboarding scope), USING and WITH CHECK.
  Offboarding exports both by their `tenant_isolation_*` policy and purges them
  with the PO case, by cascade.
- **Grants.** `dw_app` may SELECT and INSERT both; UPDATE only the design's
  status columns and `version` (never its case, tenant or workspace); never
  DELETE or TRUNCATE either: a design and its history leave with their case.
  History rows are never updated.
- **`ck_case_documents_doc_type`** gains `colour_sample`, `packaging_design`,
  `pre_production_test_report`; `test_case_documents.py` asserts it equals
  `DocumentType`, the one list.
- **Stored `supply_chain_action_duties` overrides gain the seven steps** with
  the platform's duties (1.2.0: five `ordering`, the two test steps `rnd`), as
  `84d1c1946b44` added `create_po`: the schema requires every step a duty, so
  an override without them would refuse every step of that tenant from this
  deploy on.

Downgrade REFUSES while any design or history row, or any document of the new
types, exists; it removes the seven steps from overrides only where each is
exactly what this added.
"""

from __future__ import annotations

import json

from alembic import op

revision = "85659fd91943"
down_revision = "8728e2fac660"
branch_labels = None
depends_on = None

_TENANT = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
_WORKSPACE = "workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
_SCOPE = "current_setting('app.workspace_scope', true) = 'tenant'"
_POLICY = f"({_TENANT} AND ({_WORKSPACE} OR {_SCOPE}))"

_OLD_DOC_TYPES = (
    "proposal_list",
    "product_image",
    "sample_photo",
    "sample_evaluation",
    "sample_revision_request",
    "product_profile_bm04",
    "official_item_code",
    "supplier_confirmation_email",
    "purchase_order",
    "deposit_docs",
    "payment_docs",
    "packaging_content",
    "user_manual",
    "maquette",
)
_NEW_TYPES = ("colour_sample", "packaging_design", "pre_production_test_report")
_NEW_DOC_TYPES = (*_OLD_DOC_TYPES, *_NEW_TYPES)

_REVIEW = ("pending", "revision_requested", "approved")
_TEST = ("pending", "passed", "failed")
_TEST_STEPS = ("pass_pre_production_test", "fail_pre_production_test")
_REASON_STEPS = (
    "request_colour_revision",
    "request_design_revision",
    "fail_pre_production_test",
)
_ACTIONS = (
    "approve_colour",
    "request_colour_revision",
    "approve_design",
    "request_design_revision",
    "receive_pre_production_sample",
    *_TEST_STEPS,
)
# What 1.2.0 of `supply_chain_action_duties` gives the seven steps.
_DUTIES = {action: ("rnd" if action in _TEST_STEPS else "ordering") for action in _ACTIONS}


# The data step, its own constant so a test can replay it on an override it
# stored first. The platform's duties under the tenant's own: a step the tenant
# already mapped keeps its choice.
ADD_PACKAGING_STEPS_TO_OVERRIDES = f"""
    UPDATE platform.policy_overrides
    SET content = jsonb_set(
        content,
        '{{action_duties}}',
        '{json.dumps(_DUTIES)}'::jsonb || (content -> 'action_duties')
    )
    WHERE policy_id = 'supply_chain_action_duties'
      AND jsonb_typeof(content -> 'action_duties') = 'object'
"""


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _doc_types(values: tuple[str, ...]) -> None:
    op.execute(
        f"""
        ALTER TABLE supply_chain.case_documents
            DROP CONSTRAINT ck_case_documents_doc_type,
            ADD CONSTRAINT ck_case_documents_doc_type CHECK (doc_type IN ({_in(values)}))
        """
    )


def upgrade() -> None:
    _doc_types(_NEW_DOC_TYPES)
    op.execute(
        "ALTER TABLE supply_chain.case_documents"
        " ADD CONSTRAINT uq_case_documents_tenant_id_workspace_id_po_case_id_id"
        " UNIQUE (tenant_id, workspace_id, po_case_id, id)"
    )

    op.execute(
        f"""
        CREATE TABLE supply_chain.packaging_designs (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            po_case_id uuid NOT NULL,
            colour_status text DEFAULT 'pending' NOT NULL,
            design_status text DEFAULT 'pending' NOT NULL,
            pre_production_sample_received_at timestamp with time zone,
            pre_production_test text DEFAULT 'pending' NOT NULL,
            version integer NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_packaging_designs PRIMARY KEY (id),
            CONSTRAINT uq_packaging_designs_tenant_id_workspace_id_po_case_id
                UNIQUE (tenant_id, workspace_id, po_case_id),
            CONSTRAINT fk_packaging_designs_tenant_id_po_cases
                FOREIGN KEY (tenant_id, workspace_id, po_case_id)
                REFERENCES supply_chain.po_cases (tenant_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT ck_packaging_designs_colour_status
                CHECK (colour_status IN ({_in(_REVIEW)})),
            CONSTRAINT ck_packaging_designs_design_status
                CHECK (design_status IN ({_in(_REVIEW)})),
            CONSTRAINT ck_packaging_designs_pre_production_test
                CHECK (pre_production_test IN ({_in(_TEST)})),
            CONSTRAINT ck_packaging_designs_version CHECK (version >= 1),
            CONSTRAINT ck_packaging_designs_order CHECK (
                (design_status = 'pending' OR colour_status = 'approved')
                AND (pre_production_sample_received_at IS NULL OR design_status = 'approved')
                AND (pre_production_test = 'pending'
                     OR pre_production_sample_received_at IS NOT NULL)
            )
        )
        """
    )
    op.execute(
        "CREATE TRIGGER touch_updated_at BEFORE UPDATE ON supply_chain.packaging_designs"
        " FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at()"
    )

    op.execute(
        f"""
        CREATE TABLE supply_chain.packaging_design_events (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            po_case_id uuid NOT NULL,
            action text NOT NULL,
            actor_id uuid NOT NULL,
            reason text,
            note text,
            document_id uuid,
            occurred_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_packaging_design_events PRIMARY KEY (id),
            CONSTRAINT fk_packaging_design_events_tenant_id_packaging_designs
                FOREIGN KEY (tenant_id, workspace_id, po_case_id)
                REFERENCES supply_chain.packaging_designs (tenant_id, workspace_id, po_case_id)
                ON DELETE CASCADE,
            CONSTRAINT fk_packaging_design_events_tenant_id_case_documents
                FOREIGN KEY (tenant_id, workspace_id, po_case_id, document_id)
                REFERENCES supply_chain.case_documents (tenant_id, workspace_id, po_case_id, id)
                ON DELETE NO ACTION,
            CONSTRAINT ck_packaging_design_events_action CHECK (action IN ({_in(_ACTIONS)})),
            CONSTRAINT ck_packaging_design_events_document_steps
                CHECK ((action IN ({_in(_TEST_STEPS)})) = (document_id IS NOT NULL)),
            CONSTRAINT ck_packaging_design_events_reason CHECK (
                (action NOT IN ({_in(_REASON_STEPS)}))
                OR (reason IS NOT NULL AND btrim(reason) <> '')
            ),
            CONSTRAINT ck_packaging_design_events_text CHECK (
                (reason IS NULL OR char_length(reason) <= 2000)
                AND (note IS NULL OR char_length(note) <= 500)
            )
        )
        """
    )
    # The page reads one case's history oldest first, under the RLS columns.
    op.execute(
        "CREATE INDEX ix_packaging_design_events_tenant_id_po_case_id_occurred_at"
        " ON supply_chain.packaging_design_events"
        " (tenant_id, workspace_id, po_case_id, occurred_at)"
    )
    op.execute(
        "CREATE INDEX ix_packaging_design_events_tenant_id_document_id"
        " ON supply_chain.packaging_design_events"
        " (tenant_id, workspace_id, po_case_id, document_id)"
        " WHERE document_id IS NOT NULL"
    )

    # Spelled out per table: `verify_invariants.py` reads the statements' text.
    for statement in (
        "ALTER TABLE supply_chain.packaging_designs ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE supply_chain.packaging_designs FORCE ROW LEVEL SECURITY",
        "CREATE POLICY tenant_isolation_packaging_designs ON supply_chain.packaging_designs"
        f" USING {_POLICY} WITH CHECK {_POLICY}",
        "ALTER TABLE supply_chain.packaging_design_events ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE supply_chain.packaging_design_events FORCE ROW LEVEL SECURITY",
        # One literal line: the invariant check reads the statement whole.
        "CREATE POLICY tenant_isolation_packaging_design_events ON supply_chain.packaging_design_events"  # noqa: E501
        f" USING {_POLICY} WITH CHECK {_POLICY}",
    ):
        op.execute(statement)
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                REVOKE UPDATE, DELETE, TRUNCATE ON supply_chain.packaging_designs FROM dw_app;
                GRANT UPDATE (colour_status, design_status, pre_production_sample_received_at,
                              pre_production_test, version, updated_at)
                    ON supply_chain.packaging_designs TO dw_app;
                REVOKE UPDATE, DELETE, TRUNCATE
                    ON supply_chain.packaging_design_events FROM dw_app;
            END IF;
        END
        $$
        """
    )

    op.execute(ADD_PACKAGING_STEPS_TO_OVERRIDES)


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM supply_chain.packaging_designs)
               OR EXISTS (SELECT 1 FROM supply_chain.packaging_design_events)
               OR EXISTS (
                   SELECT 1 FROM supply_chain.case_documents
                   WHERE doc_type IN ({_in(_NEW_TYPES)})
               ) THEN
                RAISE EXCEPTION
                    'PO cases hold step 12 records or documents; downgrading would remove them'
                    USING ERRCODE = 'check_violation';
            END IF;
        END
        $$
        """
    )
    for action, duty in _DUTIES.items():
        op.execute(
            f"""
            UPDATE platform.policy_overrides
            SET content = content #- '{{action_duties,{action}}}'
            WHERE policy_id = 'supply_chain_action_duties'
              AND content -> 'action_duties' -> '{action}' = '{json.dumps(duty)}'::jsonb
            """
        )
    op.execute("DROP TABLE supply_chain.packaging_design_events")
    op.execute("DROP TABLE supply_chain.packaging_designs")
    op.execute(
        "ALTER TABLE supply_chain.case_documents"
        " DROP CONSTRAINT uq_case_documents_tenant_id_workspace_id_po_case_id_id"
    )
    _doc_types(_OLD_DOC_TYPES)
