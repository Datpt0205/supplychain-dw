"""supply_chain step preparations

Revision ID: 1a8527a5b426
Revises: cbebad572558
Create Date: 2026-10-09 11:00:38.048544+00:00

What each preparation of a step came to (ADR 0025; ticket ai-automation/05).

- **`step_preparations`**: one row per change in a step's preparation,
  append-only (`dw_app` SELECT and INSERT): `proposed` (the approval was
  raised; the drafts' lineages it named, so a later attempt keeps a person's
  edits), `not_prepared` (and why: the case moved, its paper is not on the
  case, a source is not read yet, the run was refused or failed), `superseded`,
  `rejected` (the decider's reason), `applied`. Keyed by the history row that
  brought the case into the step and the policy version: what a preparation is
  idempotent on. The decision itself stays on `platform.approval_requests`;
  this row is what it led to, for the case page and the lane. A reason is
  required exactly for the outcomes that have one.
- Composite FKs `(tenant_id, workspace_id, …)` to the case and to its history
  row, `ON DELETE CASCADE` (it leaves with its case), each indexed; the history
  table gains the UNIQUE `(tenant_id, workspace_id, id)` the second one needs.
  `(tenant_id, workspace_id, transition_id, created_at)` carries "the latest
  row of this step entry". Bounded by the case's activity: the lane writes a
  `not_prepared` row only when its reason changes, never once per tick.
- **Workspace RLS** FORCEd, the shape `CLAUDE.md` allows.

Reviewed by reading and parsed with libpg_query; not run here (no database on
the machine that wrote it).
"""

from __future__ import annotations

from alembic import op

revision = "1a8527a5b426"
down_revision = "cbebad572558"
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
        ALTER TABLE supply_chain.product_dev_case_state_transitions
            ADD CONSTRAINT uq_product_dev_case_state_transitions_tenant_id_workspace_id_id
                UNIQUE (tenant_id, workspace_id, id)
        """
    )
    op.execute(
        f"""
        CREATE TABLE supply_chain.step_preparations (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            product_dev_case_id uuid NOT NULL,
            transition_id uuid NOT NULL,
            policy_version text NOT NULL,
            action text NOT NULL,
            outcome text NOT NULL,
            reason text,
            run_id uuid,
            draft_lineages jsonb DEFAULT '[]'::jsonb NOT NULL,
            recorded_by uuid NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_step_preparations PRIMARY KEY (id),
            CONSTRAINT fk_step_preparations_tenant_id_product_dev_cases
                FOREIGN KEY (tenant_id, workspace_id, product_dev_case_id)
                REFERENCES supply_chain.product_dev_cases (tenant_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT fk_step_preparations_tenant_id_product_dev_case_transitions
                FOREIGN KEY (tenant_id, workspace_id, transition_id)
                REFERENCES supply_chain.product_dev_case_state_transitions
                    (tenant_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT ck_step_preparations_outcome CHECK (
                outcome IN ('proposed', 'not_prepared', 'superseded', 'rejected', 'applied')
            ),
            CONSTRAINT ck_step_preparations_reason CHECK (
                (outcome IN ('proposed', 'applied')) = (reason IS NULL)
                AND (reason IS NULL OR (btrim(reason) <> '' AND char_length(reason) <= 1000))
            ),
            CONSTRAINT ck_step_preparations_policy_version CHECK (policy_version ~ {_SEMVER}),
            CONSTRAINT ck_step_preparations_action CHECK (action ~ '^[a-z][a-z_]{{0,59}}$'),
            CONSTRAINT ck_step_preparations_draft_lineages
                CHECK (jsonb_typeof(draft_lineages) = 'array')
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_step_preparations_tenant_id_workspace_id_product_dev_case_id"
        " ON supply_chain.step_preparations (tenant_id, workspace_id, product_dev_case_id)"
    )
    op.execute(
        "CREATE INDEX ix_step_preparations_tenant_id_workspace_id_transition_id"
        " ON supply_chain.step_preparations"
        " (tenant_id, workspace_id, transition_id, created_at)"
    )
    op.execute("ALTER TABLE supply_chain.step_preparations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE supply_chain.step_preparations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_step_preparations ON supply_chain.step_preparations"
        f" USING {_POLICY} WITH CHECK {_POLICY}"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                REVOKE ALL ON supply_chain.step_preparations FROM dw_app;
                GRANT SELECT, INSERT ON supply_chain.step_preparations TO dw_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE supply_chain.step_preparations")
    op.execute(
        """
        ALTER TABLE supply_chain.product_dev_case_state_transitions
            DROP CONSTRAINT uq_product_dev_case_state_transitions_tenant_id_workspace_id_id
        """
    )
