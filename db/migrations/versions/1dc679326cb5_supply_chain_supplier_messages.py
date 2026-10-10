"""supply_chain supplier messages

Revision ID: 1dc679326cb5
Revises: 1a8527a5b426
Create Date: 2026-10-09 12:09:18.713098+00:00

Messages to a supplier that AI drafts and a person sends from their own mailbox
(ADR 0029, E18; ticket ai-automation/07).

- **`supplier_messages`**: one row per drafted message, append-only (`dw_app`
  SELECT and INSERT): the case it is about (a PO case or a product case,
  exactly one), the purpose (CHECK), the recipient as the supplier directory
  named them when drafted (a snapshot: contacts are versioned), the subject and
  body, the approved documents it attaches, the template and prompt it was
  written with, the citations of each kept paragraph and how many paragraphs
  code dropped, and its `content_sha256`. `status` is `drafted` (a body a
  person can copy), `refused` (the model's answer did not fit the schema) or
  `failed` (the call failed after the gateway's retries); only `drafted` has a
  body. `source_key` is what drafted it (a follow-up, a step entry), UNIQUE per
  workspace: the lane drafts once per trigger, and a second tick's insert is a
  conflict, never a second paid call's row.
- **`supplier_message_sends`**: "Đã gửi", one row per message (UNIQUE): who
  pressed it and when. Sending is an event, not an update of the message.
- Composite FKs `(tenant_id, workspace_id, …)` to the case and to the message,
  `ON DELETE CASCADE` (they leave with their case), each indexed;
  `(tenant_id, workspace_id, <case>, created_at)` carries the case page's list.
  Bounded by the case's activity: one row per trigger.
- **Workspace RLS** FORCEd on both, the shape `CLAUDE.md` allows.

Reviewed by reading and parsed with libpg_query; not run here (no database on
the machine that wrote it).
"""

from __future__ import annotations

from alembic import op

revision = "1dc679326cb5"
down_revision = "1a8527a5b426"
branch_labels = None
depends_on = None

_TENANT = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
_WORKSPACE = "workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
_SCOPE = "current_setting('app.workspace_scope', true) = 'tenant'"
_POLICY = f"({_TENANT} AND ({_WORKSPACE} OR {_SCOPE}))"
_SEMVER = "'^[0-9]+\\.[0-9]+\\.[0-9]+$'"
_PURPOSES = ("sample_request", "supplier_reminder", "supplier_confirmation")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _grant(table: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                REVOKE ALL ON supply_chain.{table} FROM dw_app;
                GRANT SELECT, INSERT ON supply_chain.{table} TO dw_app;
            END IF;
        END
        $$
        """
    )


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE supply_chain.supplier_messages (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            po_case_id uuid,
            product_dev_case_id uuid,
            purpose text NOT NULL,
            status text NOT NULL,
            source_key text NOT NULL,
            supplier_name text,
            recipient_name text,
            recipient_email text,
            subject text NOT NULL,
            body text NOT NULL,
            attachments jsonb DEFAULT '[]'::jsonb NOT NULL,
            citations jsonb DEFAULT '[]'::jsonb NOT NULL,
            dropped integer DEFAULT 0 NOT NULL,
            template_version text NOT NULL,
            prompt_id text NOT NULL,
            prompt_version text NOT NULL,
            model_profile text,
            content_sha256 text NOT NULL,
            created_by uuid NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_supplier_messages PRIMARY KEY (id),
            CONSTRAINT uq_supplier_messages_tenant_id_workspace_id_id
                UNIQUE (tenant_id, workspace_id, id),
            CONSTRAINT uq_supplier_messages_tenant_id_workspace_id_source_key
                UNIQUE (tenant_id, workspace_id, source_key),
            CONSTRAINT fk_supplier_messages_tenant_id_po_cases
                FOREIGN KEY (tenant_id, workspace_id, po_case_id)
                REFERENCES supply_chain.po_cases (tenant_id, workspace_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_supplier_messages_tenant_id_product_dev_cases
                FOREIGN KEY (tenant_id, workspace_id, product_dev_case_id)
                REFERENCES supply_chain.product_dev_cases (tenant_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT ck_supplier_messages_one_case
                CHECK ((po_case_id IS NULL) <> (product_dev_case_id IS NULL)),
            CONSTRAINT ck_supplier_messages_purpose CHECK (purpose IN ({_in(_PURPOSES)})),
            CONSTRAINT ck_supplier_messages_status
                CHECK (status IN ('drafted', 'refused', 'failed')),
            CONSTRAINT ck_supplier_messages_body CHECK (
                char_length(body) <= 10000
                AND ((status = 'drafted') = (btrim(body) <> ''))
            ),
            CONSTRAINT ck_supplier_messages_subject
                CHECK (btrim(subject) <> '' AND char_length(subject) <= 300),
            CONSTRAINT ck_supplier_messages_source_key
                CHECK (btrim(source_key) <> '' AND char_length(source_key) <= 200),
            CONSTRAINT ck_supplier_messages_recipient_email
                CHECK (recipient_email IS NULL OR char_length(recipient_email) <= 320),
            CONSTRAINT ck_supplier_messages_attachments
                CHECK (jsonb_typeof(attachments) = 'array'),
            CONSTRAINT ck_supplier_messages_citations CHECK (jsonb_typeof(citations) = 'array'),
            CONSTRAINT ck_supplier_messages_dropped CHECK (dropped >= 0),
            CONSTRAINT ck_supplier_messages_template_version
                CHECK (template_version ~ {_SEMVER}),
            CONSTRAINT ck_supplier_messages_prompt_version CHECK (prompt_version ~ {_SEMVER}),
            CONSTRAINT ck_supplier_messages_content_sha256
                CHECK (content_sha256 ~ '^[0-9a-f]{{64}}$')
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_supplier_messages_tenant_id_workspace_id_po_case_id"
        " ON supply_chain.supplier_messages (tenant_id, workspace_id, po_case_id, created_at)"
        " WHERE po_case_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_supplier_messages_tenant_id_workspace_id_product_dev_case_id"
        " ON supply_chain.supplier_messages"
        " (tenant_id, workspace_id, product_dev_case_id, created_at)"
        " WHERE product_dev_case_id IS NOT NULL"
    )
    op.execute("ALTER TABLE supply_chain.supplier_messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE supply_chain.supplier_messages FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_supplier_messages ON supply_chain.supplier_messages"
        f" USING {_POLICY} WITH CHECK {_POLICY}"
    )
    _grant("supplier_messages")

    op.execute(
        """
        CREATE TABLE supply_chain.supplier_message_sends (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            message_id uuid NOT NULL,
            content_sha256 text NOT NULL,
            sent_by uuid NOT NULL,
            sent_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_supplier_message_sends PRIMARY KEY (id),
            CONSTRAINT uq_supplier_message_sends_tenant_id_message_id
                UNIQUE (tenant_id, message_id),
            CONSTRAINT fk_supplier_message_sends_tenant_id_supplier_messages
                FOREIGN KEY (tenant_id, workspace_id, message_id)
                REFERENCES supply_chain.supplier_messages (tenant_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT ck_supplier_message_sends_content_sha256
                CHECK (content_sha256 ~ '^[0-9a-f]{64}$')
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_supplier_message_sends_tenant_id_workspace_id_message_id"
        " ON supply_chain.supplier_message_sends (tenant_id, workspace_id, message_id)"
    )
    op.execute("ALTER TABLE supply_chain.supplier_message_sends ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE supply_chain.supplier_message_sends FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_supplier_message_sends ON supply_chain.supplier_message_sends"  # noqa: E501
        f" USING {_POLICY} WITH CHECK {_POLICY}"
    )
    _grant("supplier_message_sends")


def downgrade() -> None:
    op.execute("DROP TABLE supply_chain.supplier_message_sends")
    op.execute("DROP TABLE supply_chain.supplier_messages")
