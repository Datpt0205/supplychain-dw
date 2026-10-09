"""supply_chain payment papers

Revision ID: 8e2d87208f42
Revises: 0f231b1bf02d
Create Date: 2026-10-10 02:10:00.000000+00:00

Steps 11 and 16 (ticket ai-automation/15): the supplier's proforma and
commercial invoices and the bank's transfer receipt (UNC) are case documents
the extraction lane reads; the deposit and final payment requests code drafts
are `deposit_docs` and `payment_docs` drafts, types both CHECKs already hold.

- **`proforma_invoice`, `commercial_invoice`, `bank_transfer_receipt`** join
  `ck_case_documents_doc_type` and `ck_document_drafts_doc_type`; equal to
  `DocumentType` (integration test).

No new table, no new grant: payments are recorded in `po_payments`
(82221a867e62), which a confirmed payment step writes through the same INSERT
the commercial card uses. Reviewed by reading; not run here (no database on
the machine that wrote it).
"""

from __future__ import annotations

from alembic import op

revision = "8e2d87208f42"
down_revision = "0f231b1bf02d"
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
)
_NEW_DOC_TYPES = ("proforma_invoice", "commercial_invoice", "bank_transfer_receipt")


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


def upgrade() -> None:
    _doc_types((*_DOC_TYPES, *_NEW_DOC_TYPES))


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM supply_chain.case_documents
                       WHERE doc_type IN ({_in(_NEW_DOC_TYPES)})) THEN
                RAISE EXCEPTION
                    'cases hold payment papers; downgrading would orphan them'
                    USING ERRCODE = 'check_violation';
            END IF;
        END
        $$
        """
    )
    _doc_types(_DOC_TYPES)
