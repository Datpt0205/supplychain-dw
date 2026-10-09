"""supply_chain document drafts and templates

Revision ID: cbebad572558
Revises: e21dc10d13b5
Create Date: 2026-10-09 13:10:00.000000+00:00

Drafts of a case's documents, and the tenant's own document templates (ADR
0025 point 4; ticket ai-automation/03).

- **`document_drafts`**: one row per draft version, append-only (`dw_app`
  SELECT and INSERT). A draft belongs to exactly one case
  (`ck_document_drafts_one_case`), by composite FKs `(tenant_id, workspace_id,
  case)` to `po_cases` / `product_dev_cases` `ON DELETE CASCADE` (it leaves
  with its case, as documents do), each indexed. Versions of one draft share a
  `lineage_id`; `(tenant_id, lineage_id, version)` is UNIQUE, so two edits of
  one version cannot both become the next (409 by name). Holds the fields
  (each value with its source), the gaps, the source documents, the template
  and prompt it came from, and `content_sha256`, the hash an approval binds to
  (ticket 05).
- **`document_draft_decisions`**: at most one per draft version (UNIQUE),
  `confirmed | rejected | superseded`, a reason required for a rejection, the
  person who decided. Append-only.
- **`case_documents.origin`** (`uploaded | ai_prepared`, default `uploaded`)
  and **`draft_id`**: an `ai_prepared` document names the draft it came from
  and no other document names one (`ck_case_documents_draft_origin`). FK to
  the draft `ON DELETE NO ACTION` (both leave with their case in one
  statement), indexed. A step's paper is still read from `case_documents`
  only: a draft never satisfies a step (ADR 0025 point 4).
- **`bod_submission`** (tờ trình BGĐ) joins `ck_case_documents_doc_type` and
  `ck_document_drafts_doc_type`; equal to `DocumentType` (integration test).
- **`doc_template_overrides`**: a tenant's own version of a platform template,
  uploaded at run time (declaration + DOCX, at most 5 MiB), append-only,
  UNIQUE per `(tenant_id, template_id, version)`. Tenant-wide like
  `platform.policy_overrides`, so tenant-only RLS (FORCEd) and no
  `workspace_id`: a company's forms are the company's, in every workspace.
  `ON DELETE CASCADE` from `platform.tenants`, as policy overrides.
- **Workspace RLS** on the two draft tables, the shape `CLAUDE.md` allows.

Reviewed by reading and parsed with libpg_query; not run here (no database on
the machine that wrote it).
"""

from __future__ import annotations

from alembic import op

revision = "cbebad572558"
down_revision = "e21dc10d13b5"
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
)
_NEW_DOC_TYPES = ("bod_submission",)
_ALL = (*_DOC_TYPES, *_NEW_DOC_TYPES)
_DECISIONS = ("confirmed", "rejected", "superseded")
_SEMVER = "'^[0-9]+\\.[0-9]+\\.[0-9]+$'"


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
    _doc_types(_ALL)
    op.execute(
        f"""
        CREATE TABLE supply_chain.document_drafts (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            po_case_id uuid,
            product_dev_case_id uuid,
            lineage_id uuid NOT NULL,
            version integer NOT NULL,
            doc_type text NOT NULL,
            template_id text NOT NULL,
            template_version text NOT NULL,
            prompt_id text,
            prompt_version text,
            fields jsonb NOT NULL,
            gaps jsonb DEFAULT '[]'::jsonb NOT NULL,
            sources jsonb DEFAULT '[]'::jsonb NOT NULL,
            content_sha256 text NOT NULL,
            created_by uuid NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_document_drafts PRIMARY KEY (id),
            CONSTRAINT uq_document_drafts_tenant_id_workspace_id_id
                UNIQUE (tenant_id, workspace_id, id),
            CONSTRAINT uq_document_drafts_tenant_id_lineage_id_version
                UNIQUE (tenant_id, lineage_id, version),
            CONSTRAINT fk_document_drafts_tenant_id_po_cases
                FOREIGN KEY (tenant_id, workspace_id, po_case_id)
                REFERENCES supply_chain.po_cases (tenant_id, workspace_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_document_drafts_tenant_id_product_dev_cases
                FOREIGN KEY (tenant_id, workspace_id, product_dev_case_id)
                REFERENCES supply_chain.product_dev_cases (tenant_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT ck_document_drafts_one_case
                CHECK ((po_case_id IS NULL) <> (product_dev_case_id IS NULL)),
            CONSTRAINT ck_document_drafts_version CHECK (version >= 1),
            CONSTRAINT ck_document_drafts_doc_type CHECK (doc_type IN ({_in(_ALL)})),
            CONSTRAINT ck_document_drafts_template_version CHECK (template_version ~ {_SEMVER}),
            CONSTRAINT ck_document_drafts_prompt
                CHECK ((prompt_id IS NULL) = (prompt_version IS NULL)
                       AND (prompt_version IS NULL OR prompt_version ~ {_SEMVER})),
            CONSTRAINT ck_document_drafts_fields CHECK (jsonb_typeof(fields) = 'object'),
            CONSTRAINT ck_document_drafts_gaps CHECK (jsonb_typeof(gaps) = 'array'),
            CONSTRAINT ck_document_drafts_sources CHECK (jsonb_typeof(sources) = 'array'),
            CONSTRAINT ck_document_drafts_content_sha256
                CHECK (content_sha256 ~ '^[0-9a-f]{{64}}$')
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_document_drafts_tenant_id_workspace_id_po_case_id"
        " ON supply_chain.document_drafts (tenant_id, workspace_id, po_case_id)"
        " WHERE po_case_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_document_drafts_tenant_id_workspace_id_product_dev_case_id"
        " ON supply_chain.document_drafts (tenant_id, workspace_id, product_dev_case_id)"
        " WHERE product_dev_case_id IS NOT NULL"
    )
    op.execute("ALTER TABLE supply_chain.document_drafts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE supply_chain.document_drafts FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_document_drafts ON supply_chain.document_drafts"
        f" USING {_POLICY} WITH CHECK {_POLICY}"
    )

    op.execute(
        f"""
        CREATE TABLE supply_chain.document_draft_decisions (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            draft_id uuid NOT NULL,
            decision text NOT NULL,
            reason text,
            decided_by uuid NOT NULL,
            decided_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_document_draft_decisions PRIMARY KEY (id),
            CONSTRAINT uq_document_draft_decisions_tenant_id_draft_id UNIQUE (tenant_id, draft_id),
            CONSTRAINT fk_document_draft_decisions_tenant_id_document_drafts
                FOREIGN KEY (tenant_id, workspace_id, draft_id)
                REFERENCES supply_chain.document_drafts (tenant_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT ck_document_draft_decisions_decision
                CHECK (decision IN ({_in(_DECISIONS)})),
            CONSTRAINT ck_document_draft_decisions_reason
                CHECK (
                    (decision <> 'rejected' OR (reason IS NOT NULL AND btrim(reason) <> ''))
                    AND (reason IS NULL OR char_length(reason) <= 1000)
                )
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_document_draft_decisions_tenant_id_workspace_id_draft_id"
        " ON supply_chain.document_draft_decisions (tenant_id, workspace_id, draft_id)"
    )
    op.execute("ALTER TABLE supply_chain.document_draft_decisions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE supply_chain.document_draft_decisions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_document_draft_decisions ON supply_chain.document_draft_decisions"  # noqa: E501
        f" USING {_POLICY} WITH CHECK {_POLICY}"
    )

    op.execute(
        """
        ALTER TABLE supply_chain.case_documents
            ADD COLUMN origin text DEFAULT 'uploaded' NOT NULL,
            ADD COLUMN draft_id uuid,
            ADD CONSTRAINT ck_case_documents_origin CHECK (origin IN ('uploaded', 'ai_prepared')),
            ADD CONSTRAINT ck_case_documents_draft_origin
                CHECK ((origin = 'ai_prepared') = (draft_id IS NOT NULL)),
            ADD CONSTRAINT fk_case_documents_tenant_id_document_drafts
                FOREIGN KEY (tenant_id, workspace_id, draft_id)
                REFERENCES supply_chain.document_drafts (tenant_id, workspace_id, id)
                ON DELETE NO ACTION
        """
    )
    op.execute(
        "CREATE INDEX ix_case_documents_tenant_id_workspace_id_draft_id"
        " ON supply_chain.case_documents (tenant_id, workspace_id, draft_id)"
        " WHERE draft_id IS NOT NULL"
    )

    op.execute(
        f"""
        CREATE TABLE supply_chain.doc_template_overrides (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            template_id text NOT NULL,
            version text NOT NULL,
            spec text NOT NULL,
            docx bytea NOT NULL,
            checksum text NOT NULL,
            uploaded_by uuid NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_doc_template_overrides PRIMARY KEY (id),
            CONSTRAINT uq_doc_template_overrides_tenant_id_template_id_version
                UNIQUE (tenant_id, template_id, version),
            CONSTRAINT fk_doc_template_overrides_tenant_id_tenants FOREIGN KEY (tenant_id)
                REFERENCES platform.tenants (id) ON DELETE CASCADE,
            CONSTRAINT ck_doc_template_overrides_version CHECK (version ~ {_SEMVER}),
            CONSTRAINT ck_doc_template_overrides_spec CHECK (char_length(spec) BETWEEN 1 AND 200000),
            CONSTRAINT ck_doc_template_overrides_docx
                CHECK (octet_length(docx) BETWEEN 1 AND 5242880),
            CONSTRAINT ck_doc_template_overrides_checksum CHECK (checksum ~ '^[0-9a-f]{{64}}$')
        )
        """
    )
    op.execute("ALTER TABLE supply_chain.doc_template_overrides ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE supply_chain.doc_template_overrides FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_doc_template_overrides ON supply_chain.doc_template_overrides"  # noqa: E501
        f" USING ({_TENANT}) WITH CHECK ({_TENANT})"
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                REVOKE ALL ON supply_chain.document_drafts FROM dw_app;
                GRANT SELECT, INSERT ON supply_chain.document_drafts TO dw_app;
                REVOKE ALL ON supply_chain.document_draft_decisions FROM dw_app;
                GRANT SELECT, INSERT ON supply_chain.document_draft_decisions TO dw_app;
                REVOKE ALL ON supply_chain.doc_template_overrides FROM dw_app;
                GRANT SELECT, INSERT ON supply_chain.doc_template_overrides TO dw_app;
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
                       WHERE origin <> 'uploaded' OR doc_type IN ({_in(_NEW_DOC_TYPES)})) THEN
                RAISE EXCEPTION
                    'cases hold AI-prepared documents or BOD submissions; downgrading would orphan them'
                    USING ERRCODE = 'check_violation';
            END IF;
        END
        $$
        """
    )
    op.execute("DROP TABLE supply_chain.doc_template_overrides")
    op.execute(
        """
        ALTER TABLE supply_chain.case_documents
            DROP CONSTRAINT fk_case_documents_tenant_id_document_drafts,
            DROP CONSTRAINT ck_case_documents_draft_origin,
            DROP CONSTRAINT ck_case_documents_origin,
            DROP COLUMN draft_id,
            DROP COLUMN origin
        """
    )
    op.execute("DROP TABLE supply_chain.document_draft_decisions")
    op.execute("DROP TABLE supply_chain.document_drafts")
    _doc_types(_DOC_TYPES)
