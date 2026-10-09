"""supply_chain proposal lists

Revision ID: 381374b3cb35
Revises: 1dc679326cb5
Create Date: 2026-10-09 13:30:00.000000+00:00

A list of proposed products a PIC drops in at step 1, read by AI into one row
per product, each proposed or dropped by the PIC (ticket ai-automation/08).

- **`proposal_lists`**: the file as uploaded (PDF, DOCX, XLSX, EML; at most
  10 MiB, CHECK), with its hash, append-only. Stored in PostgreSQL, like a
  tenant's template overrides: a list belongs to no case yet, so it has no
  place under a case's object key, and no orphan sweep has to learn a new
  prefix. It leaves with its workspace's offboarding like every row.
- **`proposal_list_readings`**: one reading per (list, prompt version),
  append-only: `extracted` (the rows: each field cited, a gap where the text
  does not prove it, code's findings: a code taken or repeated, a Category not
  in the tenant's list), `unreadable` (a scan, an image), `refused` or
  `failed`. UNIQUE per prompt version, so two ticks never both pay.
- **`proposal_list_decisions`**: per row, once (UNIQUE): `proposed` names the
  case the PIC's proposal made (`propose` is the one door that creates a case,
  so a code taken is still the database's refusal there), `dropped` may say
  why.
- **`supply_chain.proposal_lists_awaiting_reading(prompt, max)`**: the lane's
  one cross-tenant read, a definer function returning ids only, as the
  extraction queue's.
- Composite FKs `(tenant_id, workspace_id, …)` `ON DELETE CASCADE`, each
  indexed. **Workspace RLS** FORCEd on all three.

Reviewed by reading and parsed with libpg_query; not run here (no database on
the machine that wrote it).
"""

from __future__ import annotations

from alembic import op

revision = "381374b3cb35"
down_revision = "1dc679326cb5"
branch_labels = None
depends_on = None

_TENANT = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
_WORKSPACE = "workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
_SCOPE = "current_setting('app.workspace_scope', true) = 'tenant'"
_POLICY = f"({_TENANT} AND ({_WORKSPACE} OR {_SCOPE}))"
_SEMVER = "'^[0-9]+\\.[0-9]+\\.[0-9]+$'"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE supply_chain.proposal_lists (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            filename text NOT NULL,
            content_type text NOT NULL,
            size_bytes integer NOT NULL,
            sha256 text NOT NULL,
            content bytea NOT NULL,
            uploaded_by uuid NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_proposal_lists PRIMARY KEY (id),
            CONSTRAINT uq_proposal_lists_tenant_id_workspace_id_id
                UNIQUE (tenant_id, workspace_id, id),
            CONSTRAINT ck_proposal_lists_size
                CHECK (size_bytes = octet_length(content) AND size_bytes BETWEEN 1 AND 10485760),
            CONSTRAINT ck_proposal_lists_sha256 CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_proposal_lists_filename
                CHECK (btrim(filename) <> '' AND char_length(filename) <= 255)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_proposal_lists_tenant_id_workspace_id_created_at"
        " ON supply_chain.proposal_lists (tenant_id, workspace_id, created_at)"
    )
    op.execute("ALTER TABLE supply_chain.proposal_lists ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE supply_chain.proposal_lists FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_proposal_lists ON supply_chain.proposal_lists"
        f" USING {_POLICY} WITH CHECK {_POLICY}"
    )

    op.execute(
        f"""
        CREATE TABLE supply_chain.proposal_list_readings (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            list_id uuid NOT NULL,
            status text NOT NULL,
            prompt_id text NOT NULL,
            prompt_version text NOT NULL,
            model_profile text,
            items jsonb DEFAULT '[]'::jsonb NOT NULL,
            redactions integer DEFAULT 0 NOT NULL,
            error text,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_proposal_list_readings PRIMARY KEY (id),
            CONSTRAINT uq_proposal_list_readings_tenant_id_workspace_id_id
                UNIQUE (tenant_id, workspace_id, id),
            CONSTRAINT uq_proposal_list_readings_tenant_id_list_id_prompt
                UNIQUE (tenant_id, list_id, prompt_id, prompt_version),
            CONSTRAINT fk_proposal_list_readings_tenant_id_proposal_lists
                FOREIGN KEY (tenant_id, workspace_id, list_id)
                REFERENCES supply_chain.proposal_lists (tenant_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT ck_proposal_list_readings_status
                CHECK (status IN ('extracted', 'unreadable', 'refused', 'failed')),
            CONSTRAINT ck_proposal_list_readings_items CHECK (jsonb_typeof(items) = 'array'),
            CONSTRAINT ck_proposal_list_readings_prompt_version
                CHECK (prompt_version ~ {_SEMVER}),
            CONSTRAINT ck_proposal_list_readings_redactions CHECK (redactions >= 0),
            CONSTRAINT ck_proposal_list_readings_error
                CHECK (error IS NULL OR char_length(error) <= 500)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_proposal_list_readings_tenant_id_workspace_id_list_id"
        " ON supply_chain.proposal_list_readings (tenant_id, workspace_id, list_id)"
    )
    op.execute("ALTER TABLE supply_chain.proposal_list_readings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE supply_chain.proposal_list_readings FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_proposal_list_readings ON supply_chain.proposal_list_readings"  # noqa: E501
        f" USING {_POLICY} WITH CHECK {_POLICY}"
    )

    op.execute(
        """
        CREATE TABLE supply_chain.proposal_list_decisions (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            list_id uuid NOT NULL,
            row_index integer NOT NULL,
            decision text NOT NULL,
            product_dev_case_id uuid,
            reason text,
            decided_by uuid NOT NULL,
            decided_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_proposal_list_decisions PRIMARY KEY (id),
            CONSTRAINT uq_proposal_list_decisions_tenant_id_list_id_row_index
                UNIQUE (tenant_id, list_id, row_index),
            CONSTRAINT fk_proposal_list_decisions_tenant_id_proposal_lists
                FOREIGN KEY (tenant_id, workspace_id, list_id)
                REFERENCES supply_chain.proposal_lists (tenant_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT fk_proposal_list_decisions_tenant_id_product_dev_cases
                FOREIGN KEY (tenant_id, workspace_id, product_dev_case_id)
                REFERENCES supply_chain.product_dev_cases (tenant_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT ck_proposal_list_decisions_decision
                CHECK (decision IN ('proposed', 'dropped')),
            CONSTRAINT ck_proposal_list_decisions_case
                CHECK ((decision = 'proposed') = (product_dev_case_id IS NOT NULL)),
            CONSTRAINT ck_proposal_list_decisions_row_index CHECK (row_index >= 0),
            CONSTRAINT ck_proposal_list_decisions_reason
                CHECK (reason IS NULL OR (btrim(reason) <> '' AND char_length(reason) <= 1000))
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_proposal_list_decisions_tenant_id_workspace_id_list_id"
        " ON supply_chain.proposal_list_decisions (tenant_id, workspace_id, list_id)"
    )
    op.execute(
        "CREATE INDEX ix_proposal_list_decisions_tenant_id_workspace_id_product_dev_case_id"
        " ON supply_chain.proposal_list_decisions"
        " (tenant_id, workspace_id, product_dev_case_id)"
        " WHERE product_dev_case_id IS NOT NULL"
    )
    op.execute("ALTER TABLE supply_chain.proposal_list_decisions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE supply_chain.proposal_list_decisions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_proposal_list_decisions ON supply_chain.proposal_list_decisions"  # noqa: E501
        f" USING {_POLICY} WITH CHECK {_POLICY}"
    )

    op.execute(
        """
        CREATE FUNCTION supply_chain.proposal_lists_awaiting_reading(
            prompt text, max_rows integer
        )
        RETURNS TABLE (tenant_id uuid, workspace_id uuid, list_id uuid)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, supply_chain
        AS $$
            SELECT l.tenant_id, l.workspace_id, l.id
            FROM supply_chain.proposal_lists l
            WHERE NOT EXISTS (
                SELECT 1 FROM supply_chain.proposal_list_readings r
                WHERE r.tenant_id = l.tenant_id
                  AND r.list_id = l.id
                  AND r.prompt_id || '@' || r.prompt_version = prompt
            )
            ORDER BY l.created_at, l.id
            LIMIT LEAST(GREATEST(max_rows, 0), 50)
        $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION supply_chain.proposal_lists_awaiting_reading(text, integer)"
        " FROM PUBLIC"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                REVOKE ALL ON supply_chain.proposal_lists FROM dw_app;
                GRANT SELECT, INSERT ON supply_chain.proposal_lists TO dw_app;
                REVOKE ALL ON supply_chain.proposal_list_readings FROM dw_app;
                GRANT SELECT, INSERT ON supply_chain.proposal_list_readings TO dw_app;
                REVOKE ALL ON supply_chain.proposal_list_decisions FROM dw_app;
                GRANT SELECT, INSERT ON supply_chain.proposal_list_decisions TO dw_app;
                GRANT EXECUTE ON FUNCTION
                    supply_chain.proposal_lists_awaiting_reading(text, integer) TO dw_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION supply_chain.proposal_lists_awaiting_reading(text, integer)")
    op.execute("DROP TABLE supply_chain.proposal_list_decisions")
    op.execute("DROP TABLE supply_chain.proposal_list_readings")
    op.execute("DROP TABLE supply_chain.proposal_lists")
