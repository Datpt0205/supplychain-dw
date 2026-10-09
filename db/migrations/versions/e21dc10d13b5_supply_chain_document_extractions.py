"""supply_chain document extractions

Revision ID: e21dc10d13b5
Revises: 82221a867e62
Create Date: 2026-10-09 10:05:00.000000+00:00

A case document read into cited fields by the worker's extraction lane (ADR
0021 amended 2026-10-09; ticket ai-automation/02).

- **`document_extractions`**: one row per (document, prompt version), append
  only (`dw_app` SELECT and INSERT): the prompt id and version, the model
  profile, the text the model read (already redacted), the kept `fields`
  (each value with its verbatim quote), the named `gaps`, how many identifiers
  were masked, and the status (`extracted | unreadable | refused | failed`, a
  CHECK). A new prompt version reads the document again into a new row.
- **The row describes exactly its document:** a composite FK `(tenant_id,
  workspace_id, document_id, doc_type, sha256)` to a new UNIQUE of the same
  shape on `case_documents`, so an extraction cannot name another workspace's
  document, nor carry a type or a hash its document does not have. `ON DELETE
  CASCADE`: the document's own cascade from its case (offboarding) is the
  only way either row leaves. Indexed on its own side.
- **Shape CHECKs:** `fields` an object, `gaps` an array, a non-extracted row
  holds no fields, an extracted row holds text, `sha256` hex, the version
  semver, the error bounded.
- **`supply_chain.documents_awaiting_extraction(targets, limit)`**: SECURITY
  DEFINER, ids only (`tenant_id, workspace_id, document_id`), oldest first: the
  documents of a type in `targets` (`{"doc_type": "prompt_id@version"}`) with no
  row for that prompt version. The lane then reads each document under its own
  tenant's and workspace's RLS; this function hands it nothing else.
- **`ck_case_documents_doc_type`** gains `supplier_quotation`; the integration
  test holds it equal to `DocumentType`.
- **Workspace RLS**, ENABLEd and FORCEd, the shape `CLAUDE.md` allows.

Reviewed by reading and parsed with libpg_query; not run here (no database on
the machine that wrote it).
"""

from __future__ import annotations

from alembic import op

revision = "e21dc10d13b5"
down_revision = "82221a867e62"
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
)
_NEW_DOC_TYPES = ("supplier_quotation",)
_STATUSES = ("extracted", "unreadable", "refused", "failed")


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
    _doc_types((*_DOC_TYPES, *_NEW_DOC_TYPES))
    op.execute(
        "ALTER TABLE supply_chain.case_documents"
        " ADD CONSTRAINT uq_case_documents_tenant_id_workspace_id_id_doc_type_sha256"
        " UNIQUE (tenant_id, workspace_id, id, doc_type, sha256)"
    )
    op.execute(
        f"""
        CREATE TABLE supply_chain.document_extractions (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            document_id uuid NOT NULL,
            doc_type text NOT NULL,
            sha256 text NOT NULL,
            prompt_id text NOT NULL,
            prompt_version text NOT NULL,
            model_profile text,
            status text NOT NULL,
            text text DEFAULT '' NOT NULL,
            fields jsonb DEFAULT '{{}}'::jsonb NOT NULL,
            gaps jsonb DEFAULT '[]'::jsonb NOT NULL,
            redactions integer DEFAULT 0 NOT NULL,
            error text,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_document_extractions PRIMARY KEY (id),
            CONSTRAINT uq_document_extractions_tenant_id_document_id_prompt
                UNIQUE (tenant_id, document_id, prompt_id, prompt_version),
            CONSTRAINT fk_document_extractions_tenant_id_case_documents
                FOREIGN KEY (tenant_id, workspace_id, document_id, doc_type, sha256)
                REFERENCES supply_chain.case_documents (tenant_id, workspace_id, id, doc_type, sha256)
                ON DELETE CASCADE,
            CONSTRAINT ck_document_extractions_status CHECK (status IN ({_in(_STATUSES)})),
            CONSTRAINT ck_document_extractions_sha256 CHECK (sha256 ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_document_extractions_prompt_version
                CHECK (prompt_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'),
            CONSTRAINT ck_document_extractions_fields CHECK (jsonb_typeof(fields) = 'object'),
            CONSTRAINT ck_document_extractions_gaps CHECK (jsonb_typeof(gaps) = 'array'),
            CONSTRAINT ck_document_extractions_fields_only_when_extracted
                CHECK (status = 'extracted' OR fields = '{{}}'::jsonb),
            CONSTRAINT ck_document_extractions_text_when_extracted
                CHECK (status <> 'extracted' OR btrim(text) <> ''),
            CONSTRAINT ck_document_extractions_redactions CHECK (redactions >= 0),
            CONSTRAINT ck_document_extractions_error
                CHECK (error IS NULL OR char_length(error) BETWEEN 1 AND 500)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_document_extractions_tenant_id_workspace_id_document_id"
        " ON supply_chain.document_extractions"
        " (tenant_id, workspace_id, document_id, doc_type, sha256)"
    )
    op.execute("ALTER TABLE supply_chain.document_extractions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE supply_chain.document_extractions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_document_extractions ON supply_chain.document_extractions"
        f" USING {_POLICY} WITH CHECK {_POLICY}"
    )
    op.execute(
        """
        CREATE FUNCTION supply_chain.documents_awaiting_extraction(targets jsonb, max_rows integer)
        RETURNS TABLE (tenant_id uuid, workspace_id uuid, document_id uuid)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, supply_chain
        AS $$
            SELECT d.tenant_id, d.workspace_id, d.id
            FROM supply_chain.case_documents d
            WHERE targets ? d.doc_type
              AND NOT EXISTS (
                  SELECT 1 FROM supply_chain.document_extractions e
                  WHERE e.tenant_id = d.tenant_id
                    AND e.document_id = d.id
                    AND e.prompt_id || '@' || e.prompt_version = targets ->> d.doc_type
              )
            ORDER BY d.uploaded_at, d.id
            LIMIT LEAST(GREATEST(max_rows, 0), 100)
        $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION supply_chain.documents_awaiting_extraction(jsonb, integer)"
        " FROM PUBLIC"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                REVOKE ALL ON supply_chain.document_extractions FROM dw_app;
                GRANT SELECT, INSERT ON supply_chain.document_extractions TO dw_app;
                GRANT EXECUTE ON FUNCTION
                    supply_chain.documents_awaiting_extraction(jsonb, integer) TO dw_app;
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
            IF EXISTS (
                SELECT 1 FROM supply_chain.case_documents
                WHERE doc_type IN ({_in(_NEW_DOC_TYPES)})
            ) THEN
                RAISE EXCEPTION
                    'cases hold supplier quotations; downgrading would refuse them'
                    USING ERRCODE = 'check_violation';
            END IF;
        END
        $$
        """
    )
    op.execute("DROP FUNCTION supply_chain.documents_awaiting_extraction(jsonb, integer)")
    op.execute("DROP TABLE supply_chain.document_extractions")
    op.execute(
        "ALTER TABLE supply_chain.case_documents"
        " DROP CONSTRAINT uq_case_documents_tenant_id_workspace_id_id_doc_type_sha256"
    )
    _doc_types(_DOC_TYPES)
