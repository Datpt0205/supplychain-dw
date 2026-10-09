"""supply_chain shipping papers

Revision ID: 0c3b3a73be30
Revises: 4527f22c2031
Create Date: 2026-10-10 10:00:00.000000+00:00

Steps 13-15 (ticket ai-automation/17).

- **Document types** `production_schedule`, `qc_report`, `packing_list`,
  `bill_of_lading`, `arrival_notice`, `certificate_of_origin` (the supplier's
  and the carrier's papers the extraction lane reads) and `rework_request`
  (drafted by code when QC's numbers fail) join both doc-type CHECKs.
- **`po_cases`** gains `etd`, `eta` (`date`, NULL until the step that learns
  it) and `container_number` (`text`, NULL or an ISO 6346 number: four
  letters and seven digits, `ck_po_cases_container_number`). Written by an
  approved PO step only; `dw_app` already holds UPDATE on `po_cases`.
- **`supplier_messages.purpose`** gains `production_progress`: the weekly chase
  of a PO case in production, drafted by AI and sent by a person.

No new table. Reviewed by reading; not run here (no database on the machine
that wrote it).
"""

from __future__ import annotations

from alembic import op

revision = "0c3b3a73be30"
down_revision = "4527f22c2031"
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
    "colour_revision_request",
    "design_revision_request",
)
_NEW_DOC_TYPES = (
    "production_schedule",
    "qc_report",
    "packing_list",
    "bill_of_lading",
    "arrival_notice",
    "certificate_of_origin",
    "rework_request",
)
_PURPOSES = (
    "sample_request",
    "supplier_reminder",
    "supplier_confirmation",
    "sample_revision_request",
)
_NEW_PURPOSES = ("production_progress",)


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


def _purposes(values: tuple[str, ...]) -> None:
    op.execute(
        f"""
        ALTER TABLE supply_chain.supplier_messages
            DROP CONSTRAINT ck_supplier_messages_purpose,
            ADD CONSTRAINT ck_supplier_messages_purpose CHECK (purpose IN ({_in(values)}))
        """
    )


def upgrade() -> None:
    _doc_types((*_DOC_TYPES, *_NEW_DOC_TYPES))
    op.execute(
        """
        ALTER TABLE supply_chain.po_cases
            ADD COLUMN etd date,
            ADD COLUMN eta date,
            ADD COLUMN container_number text,
            ADD CONSTRAINT ck_po_cases_container_number
                CHECK (container_number IS NULL OR container_number ~ '^[A-Z]{4}[0-9]{7}$')
        """
    )
    _purposes((*_PURPOSES, *_NEW_PURPOSES))


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM supply_chain.case_documents
                       WHERE doc_type IN ({_in(_NEW_DOC_TYPES)}))
               OR EXISTS (SELECT 1 FROM supply_chain.supplier_messages
                          WHERE purpose IN ({_in(_NEW_PURPOSES)}))
               OR EXISTS (SELECT 1 FROM supply_chain.po_cases
                          WHERE etd IS NOT NULL OR eta IS NOT NULL
                             OR container_number IS NOT NULL) THEN
                RAISE EXCEPTION
                    'cases hold shipping papers, dates or progress messages'
                    USING ERRCODE = 'check_violation';
            END IF;
        END
        $$
        """
    )
    _purposes(_PURPOSES)
    op.execute(
        """
        ALTER TABLE supply_chain.po_cases
            DROP CONSTRAINT ck_po_cases_container_number,
            DROP COLUMN container_number,
            DROP COLUMN eta,
            DROP COLUMN etd
        """
    )
    _doc_types(_DOC_TYPES)
