"""supply_chain mkt at step 12

Revision ID: 4527f22c2031
Revises: 8e2d87208f42
Create Date: 2026-10-10 06:00:00.000000+00:00

MKT is a minimal user at step 12 (ADR 0028, E17; ticket ai-automation/16).

- **`packaging_designs`** gains `mkt_pack_sent_at` and
  `packaging_content_submitted_at` (`timestamptz`, NULL until the step);
  `ck_packaging_designs_order` now also holds MKT's order in the row: no pack
  before the colour is approved, no content before the pack. `dw_app` may
  UPDATE the two new columns, as the other status columns.
- **`packaging_design_events`**: `ck_packaging_design_events_action` gains
  `send_mkt_pack` and `submit_packaging_content`; the content step carries its
  document (the `packaging_content` it submitted, by the existing FK to this
  PO case's documents) as the two test steps do, and no other step does.
- **Document types** `colour_revision_request`, `design_revision_request`
  (the revision requests code drafts) join both doc-type CHECKs.
- **`sc_mkt`**, built as `7c422b849fe9` built `sc_rnd`: everything `sc_viewer`
  holds (read from the row), the duty `supply_chain.duty.mkt`, and
  `supply_chain.packaging_document.write`, which uploads only MKT's four papers
  (packaging content, user manual, maquette, packaging design; the handler
  decides by type). No `supply_chain.commercial.read`: MKT sees no price (E15).
  No ordering duty: MKT takes no Cung ứng step. Both scopes join the
  operations side of `sod_sc_rules_vs_operations`.

The PO step-to-duty overrides stored before policy 1.3.0 are NOT rewritten:
`SupplyChainActionDuties.from_stored` gives them the platform's duty for MKT's
two steps when read. No membership holds `sc_mkt` yet. Reviewed by reading;
not run here (no database on the machine that wrote it).
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "4527f22c2031"
down_revision = "8e2d87208f42"
branch_labels = None
depends_on = None

_DOC_TYPES = (
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
    "colour_sample",
    "packaging_design",
    "pre_production_test_report",
    "supplier_quotation",
    "bod_submission",
    "proforma_invoice",
    "commercial_invoice",
    "bank_transfer_receipt",
)
_NEW_DOC_TYPES = ("colour_revision_request", "design_revision_request")
_ACTIONS = (
    "approve_colour",
    "request_colour_revision",
    "approve_design",
    "request_design_revision",
    "receive_pre_production_sample",
    "pass_pre_production_test",
    "fail_pre_production_test",
)
_NEW_ACTIONS = ("send_mkt_pack", "submit_packaging_content")
_TEST_STEPS = ("pass_pre_production_test", "fail_pre_production_test")
_DOCUMENT_STEPS = (*_TEST_STEPS, "submit_packaging_content")
_MKT = "supply_chain.duty.mkt"
_PACKAGING_WRITE = "supply_chain.packaging_document.write"
_RULE = "sod_sc_rules_vs_operations"
_ORDER = """
    (design_status = 'pending' OR colour_status = 'approved')
    AND (pre_production_sample_received_at IS NULL OR design_status = 'approved')
    AND (pre_production_test = 'pending' OR pre_production_sample_received_at IS NOT NULL)
"""
_MKT_ORDER = """
    AND (mkt_pack_sent_at IS NULL OR colour_status = 'approved')
    AND (packaging_content_submitted_at IS NULL OR mkt_pack_sent_at IS NOT NULL)
"""


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _doc_types(values: tuple[str, ...]) -> None:
    for table in ("case_documents", "document_drafts"):
        op.execute(
            f"""
            ALTER TABLE supply_chain.{table}
                DROP CONSTRAINT ck_{table}_doc_type,
                ADD CONSTRAINT ck_{table}_doc_type CHECK (doc_type IN ({_in(values)}))
            """
        )


def _events(actions: tuple[str, ...], document_steps: tuple[str, ...]) -> None:
    op.execute(
        f"""
        ALTER TABLE supply_chain.packaging_design_events
            DROP CONSTRAINT ck_packaging_design_events_action,
            ADD CONSTRAINT ck_packaging_design_events_action
                CHECK (action IN ({_in(actions)})),
            DROP CONSTRAINT ck_packaging_design_events_document_steps,
            ADD CONSTRAINT ck_packaging_design_events_document_steps
                CHECK ((action IN ({_in(document_steps)})) = (document_id IS NOT NULL))
        """
    )


def upgrade() -> None:
    _doc_types((*_DOC_TYPES, *_NEW_DOC_TYPES))
    op.execute(
        f"""
        ALTER TABLE supply_chain.packaging_designs
            ADD COLUMN mkt_pack_sent_at timestamp with time zone,
            ADD COLUMN packaging_content_submitted_at timestamp with time zone,
            DROP CONSTRAINT ck_packaging_designs_order,
            ADD CONSTRAINT ck_packaging_designs_order CHECK ({_ORDER}{_MKT_ORDER})
        """
    )
    _events((*_ACTIONS, *_NEW_ACTIONS), _DOCUMENT_STEPS)
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                GRANT UPDATE (mkt_pack_sent_at, packaging_content_submitted_at)
                    ON supply_chain.packaging_designs TO dw_app;
            END IF;
        END
        $$
        """
    )
    op.execute(
        sa.text(
            "INSERT INTO platform.roles (key, name, scopes)"
            " SELECT 'sc_mkt', 'Supply Chain — MKT', scopes || CAST(:extra AS jsonb)"
            " FROM platform.roles WHERE key = 'sc_viewer'"
            " ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, scopes = EXCLUDED.scopes"
        ).bindparams(extra=json.dumps([_MKT, _PACKAGING_WRITE]))
    )
    for scope in (_MKT, _PACKAGING_WRITE):
        op.execute(
            f"""
            UPDATE platform.sod_rules
            SET right_scopes = right_scopes || '{json.dumps([scope])}'::jsonb
            WHERE key = '{_RULE}' AND NOT right_scopes ? '{scope}'
            """
        )


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM supply_chain.packaging_design_events
                       WHERE action IN ({_in(_NEW_ACTIONS)}))
               OR EXISTS (SELECT 1 FROM supply_chain.case_documents
                          WHERE doc_type IN ({_in(_NEW_DOC_TYPES)}))
               OR EXISTS (SELECT 1 FROM platform.memberships
                          WHERE role_keys ? 'sc_mkt') THEN
                RAISE EXCEPTION
                    'cases hold MKT steps or revision requests, or members hold sc_mkt'
                    USING ERRCODE = 'check_violation';
            END IF;
        END
        $$
        """
    )
    op.execute(
        f"UPDATE platform.sod_rules SET right_scopes = right_scopes - '{_MKT}'"
        f" - '{_PACKAGING_WRITE}' WHERE key = '{_RULE}'"
    )
    op.execute("DELETE FROM platform.roles WHERE key = 'sc_mkt'")
    _events(_ACTIONS, _TEST_STEPS)
    op.execute(
        f"""
        ALTER TABLE supply_chain.packaging_designs
            DROP CONSTRAINT ck_packaging_designs_order,
            ADD CONSTRAINT ck_packaging_designs_order CHECK ({_ORDER}),
            DROP COLUMN packaging_content_submitted_at,
            DROP COLUMN mkt_pack_sent_at
        """
    )
    _doc_types(_DOC_TYPES)
