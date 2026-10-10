"""supply_chain sample measurements

Revision ID: 1021f7fe88f0
Revises: 381374b3cb35
Create Date: 2026-10-09 15:00:00.000000+00:00

What R&D measured on a sample round, criterion by criterion (ticket
ai-automation/09), and a fourth purpose of a message to a supplier.

- **`sample_measurements`**: one row per value R&D enters, append-only: the
  case, the round, the criterion's key, the value as code canonicalised it (a
  number, or `pass` / `fail`), an optional note, who and when. A correction is
  a newer row of the same (case, round, criterion); the newest one counts.
  The comparison with the criterion is code's, read each time, never stored:
  a threshold the tenant corrects re-judges the round. `(tenant_id,
  workspace_id, product_dev_case_id, sample_round, criterion, entered_at)`
  carries "the newest value of each criterion of this round". Bounded by the
  case's rounds and criteria.
- **`supplier_messages.purpose`** gains `sample_revision_request`: the
  approved revision request sent to the supplier (AI drafts, a person sends).
- **Workspace RLS** FORCEd, `ON DELETE CASCADE` from the case, indexed.

Reviewed by reading and parsed with libpg_query; not run here (no database on
the machine that wrote it).
"""

from __future__ import annotations

from alembic import op

revision = "1021f7fe88f0"
down_revision = "381374b3cb35"
branch_labels = None
depends_on = None

_TENANT = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
_WORKSPACE = "workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
_SCOPE = "current_setting('app.workspace_scope', true) = 'tenant'"
_POLICY = f"({_TENANT} AND ({_WORKSPACE} OR {_SCOPE}))"
_PURPOSES = ("sample_request", "supplier_reminder", "supplier_confirmation")
_NEW_PURPOSES = ("sample_revision_request",)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _purposes(values: tuple[str, ...]) -> None:
    op.execute(
        f"""
        ALTER TABLE supply_chain.supplier_messages
            DROP CONSTRAINT ck_supplier_messages_purpose,
            ADD CONSTRAINT ck_supplier_messages_purpose CHECK (purpose IN ({_in(values)}))
        """
    )


def upgrade() -> None:
    _purposes((*_PURPOSES, *_NEW_PURPOSES))
    op.execute(
        """
        CREATE TABLE supply_chain.sample_measurements (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            product_dev_case_id uuid NOT NULL,
            sample_round integer NOT NULL,
            criterion text NOT NULL,
            value text NOT NULL,
            note text,
            entered_by uuid NOT NULL,
            entered_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_sample_measurements PRIMARY KEY (id),
            CONSTRAINT fk_sample_measurements_tenant_id_product_dev_cases
                FOREIGN KEY (tenant_id, workspace_id, product_dev_case_id)
                REFERENCES supply_chain.product_dev_cases (tenant_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT ck_sample_measurements_sample_round CHECK (sample_round >= 1),
            CONSTRAINT ck_sample_measurements_criterion
                CHECK (criterion ~ '^[a-z][a-z0-9_]{0,63}$'),
            CONSTRAINT ck_sample_measurements_value
                CHECK (btrim(value) <> '' AND char_length(value) <= 40),
            CONSTRAINT ck_sample_measurements_note
                CHECK (note IS NULL OR (btrim(note) <> '' AND char_length(note) <= 500))
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_sample_measurements_tenant_id_workspace_id_product_dev_case_id"
        " ON supply_chain.sample_measurements"
        " (tenant_id, workspace_id, product_dev_case_id, sample_round, criterion, entered_at)"
    )
    op.execute("ALTER TABLE supply_chain.sample_measurements ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE supply_chain.sample_measurements FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_sample_measurements ON supply_chain.sample_measurements"
        f" USING {_POLICY} WITH CHECK {_POLICY}"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                REVOKE ALL ON supply_chain.sample_measurements FROM dw_app;
                GRANT SELECT, INSERT ON supply_chain.sample_measurements TO dw_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE supply_chain.sample_measurements")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM supply_chain.supplier_messages
                WHERE purpose IN ({_in(_NEW_PURPOSES)})
            ) THEN
                RAISE EXCEPTION 'supplier messages use a purpose this downgrade removes';
            END IF;
        END
        $$
        """
    )
    _purposes(_PURPOSES)
