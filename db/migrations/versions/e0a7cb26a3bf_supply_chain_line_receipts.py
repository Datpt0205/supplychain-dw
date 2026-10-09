"""supply_chain line receipts

Revision ID: e0a7cb26a3bf
Revises: 0c3b3a73be30
Create Date: 2026-10-09 21:45:22.161400+00:00

Step 17 (ticket ai-automation/18).

- **`po_case_line_receipts`**: the warehouse's count of each PO line,
  append-only (a recount is the line's next `version`). `sku_code`,
  `ordered` and `shipped` are stamped beside `counted` when it is recorded, so
  the discrepancy report and the claim letter read what the person saw, never
  a PO or a packing list changed since. Composite FKs: to the PO line
  (`po_case_lines (tenant_id, workspace_id, po_case_id, sku_id)`, `ON DELETE
  CASCADE`, so the case's purge takes the counts along) and to the filed
  goods-received note (`case_documents (tenant_id, workspace_id, po_case_id,
  id)`, `ON DELETE NO ACTION` as `po_payments`: a cited paper cannot go on its
  own). Counts are whole numbers from 0 to 10,000,000 (`MAX_COUNT`).
- **Workspace RLS**, ENABLEd and FORCEd, the one shape `CLAUDE.md` allows;
  indexes lead with `tenant_id` and carry each FK and the lane's window read.
- **Grants:** `dw_app` SELECT and INSERT only (append-only).
- **Document types** `warehouse_receipt` and `discrepancy_report` join both
  doc-type CHECKs; **message purpose** `discrepancy_claim` joins
  `ck_supplier_messages_purpose`.

Reviewed by reading; not run here (no database on the machine that wrote it).
"""

from __future__ import annotations

from alembic import op

revision = "e0a7cb26a3bf"
down_revision = "0c3b3a73be30"
branch_labels = None
depends_on = None

_TENANT = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
_WORKSPACE = "workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
_SCOPE = "current_setting('app.workspace_scope', true) = 'tenant'"
_POLICY = f"({_TENANT} AND ({_WORKSPACE} OR {_SCOPE}))"

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
    "production_schedule",
    "qc_report",
    "packing_list",
    "bill_of_lading",
    "arrival_notice",
    "certificate_of_origin",
    "rework_request",
)
_NEW_DOC_TYPES = ("warehouse_receipt", "discrepancy_report")
_PURPOSES = (
    "sample_request",
    "supplier_reminder",
    "supplier_confirmation",
    "sample_revision_request",
    "production_progress",
)
_NEW_PURPOSES = ("discrepancy_claim",)


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
    _purposes((*_PURPOSES, *_NEW_PURPOSES))
    op.execute(
        """
        CREATE TABLE supply_chain.po_case_line_receipts (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            po_case_id uuid NOT NULL,
            sku_id uuid NOT NULL,
            version integer NOT NULL,
            sku_code text NOT NULL,
            ordered integer,
            shipped integer,
            counted integer NOT NULL,
            document_id uuid,
            recorded_by uuid NOT NULL,
            recorded_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_po_case_line_receipts PRIMARY KEY (id),
            CONSTRAINT uq_po_case_line_receipts_tenant_id_po_case_id_sku_id_version
                UNIQUE (tenant_id, po_case_id, sku_id, version),
            CONSTRAINT fk_po_case_line_receipts_tenant_id_po_case_lines
                FOREIGN KEY (tenant_id, workspace_id, po_case_id, sku_id)
                REFERENCES supply_chain.po_case_lines (tenant_id, workspace_id, po_case_id, sku_id)
                ON DELETE CASCADE,
            CONSTRAINT fk_po_case_line_receipts_tenant_id_case_documents
                FOREIGN KEY (tenant_id, workspace_id, po_case_id, document_id)
                REFERENCES supply_chain.case_documents (tenant_id, workspace_id, po_case_id, id)
                ON DELETE NO ACTION,
            CONSTRAINT ck_po_case_line_receipts_version CHECK (version >= 1),
            CONSTRAINT ck_po_case_line_receipts_sku_code CHECK (length(btrim(sku_code)) > 0),
            CONSTRAINT ck_po_case_line_receipts_ordered
                CHECK (ordered IS NULL OR ordered BETWEEN 0 AND 10000000),
            CONSTRAINT ck_po_case_line_receipts_shipped
                CHECK (shipped IS NULL OR shipped BETWEEN 0 AND 10000000),
            CONSTRAINT ck_po_case_line_receipts_counted CHECK (counted BETWEEN 0 AND 10000000)
        )
        """
    )
    # Every name under Postgres's 63 characters: a longer one is truncated
    # silently, and a later `DROP CONSTRAINT` by the convention's name misses.
    op.execute(
        "CREATE INDEX ix_po_case_line_receipts_tenant_id_workspace_id_po_case_id"
        " ON supply_chain.po_case_line_receipts (tenant_id, workspace_id, po_case_id)"
    )
    op.execute(
        "CREATE INDEX ix_po_case_line_receipts_tenant_id_document_id"
        " ON supply_chain.po_case_line_receipts (tenant_id, document_id)"
        " WHERE document_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_po_case_line_receipts_tenant_id_workspace_id_recorded_at"
        " ON supply_chain.po_case_line_receipts (tenant_id, workspace_id, recorded_at)"
    )
    # Spelled out: `verify_invariants.py` reads this text.
    op.execute("ALTER TABLE supply_chain.po_case_line_receipts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE supply_chain.po_case_line_receipts FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_po_case_line_receipts ON supply_chain.po_case_line_receipts"
        f" USING {_POLICY} WITH CHECK {_POLICY}"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                REVOKE ALL ON supply_chain.po_case_line_receipts FROM dw_app;
                GRANT SELECT, INSERT ON supply_chain.po_case_line_receipts TO dw_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM supply_chain.case_documents
                       WHERE doc_type IN ({_in(_NEW_DOC_TYPES)}))
               OR EXISTS (SELECT 1 FROM supply_chain.document_drafts
                          WHERE doc_type IN ({_in(_NEW_DOC_TYPES)}))
               OR EXISTS (SELECT 1 FROM supply_chain.supplier_messages
                          WHERE purpose IN ({_in(_NEW_PURPOSES)}))
               OR EXISTS (SELECT 1 FROM supply_chain.po_case_line_receipts) THEN
                RAISE EXCEPTION
                    'cases hold warehouse counts, papers or discrepancy claims'
                    USING ERRCODE = 'check_violation';
            END IF;
        END
        $$
        """
    )
    op.execute("DROP TABLE supply_chain.po_case_line_receipts")
    _purposes(_PURPOSES)
    _doc_types(_DOC_TYPES)
