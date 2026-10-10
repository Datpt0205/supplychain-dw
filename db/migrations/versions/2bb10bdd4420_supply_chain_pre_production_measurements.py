"""supply_chain pre production measurements

Revision ID: 2bb10bdd4420
Revises: e0a7cb26a3bf
Create Date: 2026-10-09 23:34:48.975868+00:00

What R&D measured on a PO case's pre-production sample (step 12; ticket
ai-automation/17, item 2), built as `sample_measurements` (1021f7fe88f0) is
for a sample round.

- **`pre_production_measurements`**: one row per value R&D enters,
  append-only: the PO case, the test attempt (the first, plus one per failed
  test the case's history records; code computes it), the criterion's key,
  the value as code canonicalised it (a number, or `pass` / `fail`), an
  optional note, who and when. A correction is a newer row of the same
  (case, attempt, criterion); the newest one counts. The comparison with the
  criterion is code's, read each time, never stored. `(tenant_id,
  workspace_id, po_case_id, attempt, criterion, entered_at)` carries "the
  newest value of each criterion of this attempt". Bounded by the case's
  attempts and criteria.
- **Workspace RLS** FORCEd, `ON DELETE CASCADE` from the case, indexed on
  its own side; `dw_app` SELECT and INSERT only.

Reviewed by reading; not run here (no database on the machine that wrote it).
"""

from __future__ import annotations

from alembic import op

revision = "2bb10bdd4420"
down_revision = "e0a7cb26a3bf"
branch_labels = None
depends_on = None

_TENANT = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
_WORKSPACE = "workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
_SCOPE = "current_setting('app.workspace_scope', true) = 'tenant'"
_POLICY = f"({_TENANT} AND ({_WORKSPACE} OR {_SCOPE}))"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE supply_chain.pre_production_measurements (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            po_case_id uuid NOT NULL,
            attempt integer NOT NULL,
            criterion text NOT NULL,
            value text NOT NULL,
            note text,
            entered_by uuid NOT NULL,
            entered_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_pre_production_measurements PRIMARY KEY (id),
            CONSTRAINT fk_pre_production_measurements_tenant_id_po_cases
                FOREIGN KEY (tenant_id, workspace_id, po_case_id)
                REFERENCES supply_chain.po_cases (tenant_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT ck_pre_production_measurements_attempt CHECK (attempt >= 1),
            CONSTRAINT ck_pre_production_measurements_criterion
                CHECK (criterion ~ '^[a-z][a-z0-9_]{0,63}$'),
            CONSTRAINT ck_pre_production_measurements_value
                CHECK (btrim(value) <> '' AND char_length(value) <= 40),
            CONSTRAINT ck_pre_production_measurements_note
                CHECK (note IS NULL OR (btrim(note) <> '' AND char_length(note) <= 500))
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_pre_production_measurements_tenant_id_workspace_id"
        " ON supply_chain.pre_production_measurements"
        " (tenant_id, workspace_id, po_case_id, attempt, criterion, entered_at)"
    )
    op.execute("ALTER TABLE supply_chain.pre_production_measurements ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE supply_chain.pre_production_measurements FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation_ppm ON supply_chain.pre_production_measurements
            USING {_POLICY} WITH CHECK {_POLICY}
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                REVOKE ALL ON supply_chain.pre_production_measurements FROM dw_app;
                GRANT SELECT, INSERT ON supply_chain.pre_production_measurements TO dw_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE supply_chain.pre_production_measurements")
