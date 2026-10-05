"""supply chain case documents

Revision ID: f6a8142a6cd2
Revises: 5d3965984679
Create Date: 2026-10-05 12:58:14.163424+00:00

A file attached to a PO case, with a type and a version (ADR 0021, slice D).
The bytes live in object storage under a key the server builds; this table is
the record that says the file exists, whose it is, and which version it is.

- **The first table narrowed by workspace.** RLS is enabled, FORCEd, and has
  the one shape `CLAUDE.md` allows on both sides:
  `tenant AND (workspace OR app.workspace_scope = 'tenant')`. The offboarding
  lane is the only writer of `app.workspace_scope`; `test_rls_coverage.py`
  holds the shape. `po_cases` itself stays tenant-only.
- **A document's workspace is its case's workspace.** The FK is
  `(tenant_id, workspace_id, po_case_id)` to `po_cases (tenant_id,
  workspace_id, id)`, so the database refuses a row whose tenant or workspace
  differs from its case's, whatever path wrote it. That FK needs a unique key
  to point at: `uq_po_cases_tenant_id_workspace_id_id`, unique already
  because `id` is.
- **ON DELETE CASCADE, and no DELETE for the application** (ADR 0021 amended
  2026-10-05). A document is never edited or deleted by hand: `dw_app` holds
  SELECT and INSERT only. The cascade from `po_cases` is the one way a row
  leaves, and it is how offboarding's purge removes it (`SqlTenantOffboarding`
  deletes only what `dw_app` may DELETE, then `po_cases` cascades). RESTRICT,
  as the ADR first said, would make that purge fail on any tenant with a
  document, as follow_ups (dc2285c629d4) already found.
- **The version is the database's to keep unique:** a partial UNIQUE on
  `(tenant_id, po_case_id, doc_type, version) WHERE po_case_id IS NOT NULL`.
  The insert computes max + 1; two concurrent uploads of one type either get
  two versions or one meets this constraint, which the adapter turns into a
  409 by its name. Stage-1 ticket 01 adds the same for `product_dev_case_id`
  when it drops `po_case_id`'s NOT NULL.
- **Fixed sets are CHECKs:** `doc_type` (ADR 0021's fourteen, equal to
  `dw_supply_chain.domain.case_document.DocumentType`, asserted by an
  integration test), `content_type` (the seven the upload accepts), and the
  object key, which must be exactly the one built from the row's own ids.
- `uq_case_documents_tenant_id_workspace_id_id` is the target stage 1's sample
  rounds and revision requests point their composite FKs at.
- **Scopes:** `supply_chain.document.read` joins every Supply Chain role,
  `.write` the five operating roles, and the write joins the operations side
  of `sod_sc_rules_vs_operations`, so `sc_process_admin` stays out.
"""

from __future__ import annotations

import json

from alembic import op

revision = "f6a8142a6cd2"
down_revision = "5d3965984679"
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
)
_CONTENT_TYPES = (
    "application/pdf",
    "image/jpeg",
    "image/png",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "message/rfc822",
    "application/vnd.ms-outlook",
)

_READ = "supply_chain.document.read"
_WRITE = "supply_chain.document.write"
_OPERATING_ROLES = ("sc_operator", "sc_finance", "sc_qc", "sc_logistics", "sc_warehouse")
_SC_ROLES = ("sc_viewer", *_OPERATING_ROLES, "sc_process_admin")
_RULE = "sod_sc_rules_vs_operations"


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE supply_chain.po_cases ADD CONSTRAINT uq_po_cases_tenant_id_workspace_id_id"
        " UNIQUE (tenant_id, workspace_id, id)"
    )
    op.execute(
        f"""
        CREATE TABLE supply_chain.case_documents (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            po_case_id uuid NOT NULL,
            doc_type text NOT NULL,
            object_key text NOT NULL,
            filename text NOT NULL,
            content_type text NOT NULL,
            size_bytes bigint NOT NULL,
            sha256 text NOT NULL,
            version integer NOT NULL,
            uploaded_by uuid NOT NULL,
            uploaded_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_case_documents PRIMARY KEY (id),
            CONSTRAINT uq_case_documents_tenant_id_workspace_id_id
                UNIQUE (tenant_id, workspace_id, id),
            CONSTRAINT uq_case_documents_object_key UNIQUE (object_key),
            CONSTRAINT fk_case_documents_tenant_id_po_cases
                FOREIGN KEY (tenant_id, workspace_id, po_case_id)
                REFERENCES supply_chain.po_cases (tenant_id, workspace_id, id) ON DELETE CASCADE,
            CONSTRAINT ck_case_documents_doc_type CHECK (doc_type IN ({_in(_DOC_TYPES)})),
            CONSTRAINT ck_case_documents_content_type
                CHECK (content_type IN ({_in(_CONTENT_TYPES)})),
            CONSTRAINT ck_case_documents_object_key CHECK (
                object_key = 'supply_chain/' || tenant_id::text || '/' || workspace_id::text
                    || '/po/' || po_case_id::text || '/' || id::text
            ),
            CONSTRAINT ck_case_documents_filename
                CHECK (char_length(filename) BETWEEN 1 AND 255 AND filename !~ '[/\\\\[:cntrl:]]'),
            CONSTRAINT ck_case_documents_size_bytes CHECK (size_bytes > 0),
            CONSTRAINT ck_case_documents_sha256 CHECK (sha256 ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_case_documents_version CHECK (version >= 1)
        )
        """
    )
    # The version guard, and the index the per-case list reads (RLS supplies
    # the tenant, which leads).
    op.execute(
        "CREATE UNIQUE INDEX uq_case_documents_tenant_id_po_case_id_doc_type_version"
        " ON supply_chain.case_documents (tenant_id, po_case_id, doc_type, version)"
        " WHERE po_case_id IS NOT NULL"
    )
    # The FK's own side: what the cascade from po_cases looks rows up by.
    op.execute(
        "CREATE INDEX ix_case_documents_tenant_id_workspace_id_po_case_id"
        " ON supply_chain.case_documents (tenant_id, workspace_id, po_case_id)"
    )
    op.execute("ALTER TABLE supply_chain.case_documents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE supply_chain.case_documents FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_case_documents ON supply_chain.case_documents"
        f" USING {_POLICY} WITH CHECK {_POLICY}"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                REVOKE UPDATE, DELETE, TRUNCATE ON supply_chain.case_documents FROM dw_app;
            END IF;
        END
        $$
        """
    )

    op.execute(
        f"""
        UPDATE platform.roles SET scopes = scopes || '{json.dumps([_READ])}'::jsonb
        WHERE key IN ({_in(_SC_ROLES)}) AND NOT scopes ? '{_READ}'
        """
    )
    op.execute(
        f"""
        UPDATE platform.roles SET scopes = scopes || '{json.dumps([_WRITE])}'::jsonb
        WHERE key IN ({_in(_OPERATING_ROLES)}) AND NOT scopes ? '{_WRITE}'
        """
    )
    op.execute(
        f"""
        UPDATE platform.sod_rules SET right_scopes = right_scopes || '{json.dumps([_WRITE])}'::jsonb
        WHERE key = '{_RULE}' AND NOT right_scopes ? '{_WRITE}'
        """
    )


def downgrade() -> None:
    op.execute(
        f"UPDATE platform.sod_rules SET right_scopes = right_scopes - '{_WRITE}'"
        f" WHERE key = '{_RULE}'"
    )
    op.execute(
        f"UPDATE platform.roles SET scopes = scopes - '{_READ}' - '{_WRITE}'"
        f" WHERE key IN ({_in(_SC_ROLES)})"
    )
    op.execute("DROP TABLE supply_chain.case_documents")
    op.execute(
        "ALTER TABLE supply_chain.po_cases DROP CONSTRAINT uq_po_cases_tenant_id_workspace_id_id"
    )
