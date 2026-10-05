"""supply chain product development cases

Revision ID: 59e69efdfa37
Revises: f6a8142a6cd2
Create Date: 2026-10-05 15:44:10.607101+00:00

Stage 1, steps 1-5 (stage-1 ticket 01, ADR 0016): a product proposed by Cung
ứng, sampled, tested by R&D, revised or rejected, until it waits for BGĐ.

Four tables, each tenant AND workspace scoped, RLS ENABLEd and FORCEd with the
one workspace shape `CLAUDE.md` allows, as `case_documents` (f6a8142a6cd2) was:
`tenant AND (workspace OR app.workspace_scope = 'tenant')`.

- **`product_dev_cases`**: the case. `proposal_code` is unique per TENANT
  (`CONTEXT.md`, Mã đề xuất), so a code taken in another workspace of the same
  tenant is a 409 there: it confirms only that the code exists, accepted and
  recorded in the ticket. `pic_user_id` is NOT NULL and stamped at propose.
  `category` is free, trimmed, non-blank text until S6 puts the list in the
  SLA policy (ADR 0019). `supplier_name` is NULL until `request_sample` names
  it. Keyset indexes lead with `tenant_id` (RLS supplies it), one per filter.
- **Children** (`product_dev_case_state_transitions`, `product_sample_rounds`,
  `sample_revision_requests`) reference the case by
  `(tenant_id, workspace_id, product_dev_case_id)`, so a child cannot sit in
  another workspace than its case, and `ON DELETE CASCADE`: the case's delete
  is the only way a child row leaves.
- **Grants.** Transitions and revision requests are append-only for `dw_app`
  (SELECT, INSERT). A round is opened by INSERT and closed once: `dw_app` may
  UPDATE only the four columns closing writes, and `closes_once` refuses any
  update of a round already closed, so a result is set once whatever the
  statement says. `product_dev_cases` keeps the schema's default DELETE, as
  `po_cases` does: the offboarding purge deletes only tables `dw_app` may
  DELETE (`SqlTenantOffboarding`), and the case is the table whose cascade
  carries the rest away. Without it a tenant's product cases would outlive
  its offboarding. No code path deletes a case.
- **Papers are tied by the database to their case.** A round's evaluation and
  a revision request's document reference
  `case_documents (tenant_id, workspace_id, product_dev_case_id, id)`, so a
  document of another case, workspace or tenant is refused whatever wrote
  the row; each document can close one round only (UNIQUE). Which TYPE it is,
  and that it came after the round opened, the domain decides
  (`ProductDevelopmentCase._round_document`). Those FKs are `NO ACTION`: a
  document and its round leave together by the case's cascade, and NO ACTION
  is checked at the end of that statement, by which time both are gone.
- **`case_documents`** now belongs to exactly one of two cases: `po_case_id`
  loses NOT NULL, `product_dev_case_id` joins with its composite FK (CASCADE)
  and its own version guard, and the object-key CHECK covers both kinds.

Four names follow `NAMING_CONVENTION` with `product_dev_case_id` shortened to
`case`: written out they pass Postgres's 63-byte identifier limit, which
truncates silently, and the adapter maps a refusal by its exact name.
"""

from __future__ import annotations

from alembic import op

revision = "59e69efdfa37"
down_revision = "f6a8142a6cd2"
branch_labels = None
depends_on = None

_TENANT = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
_WORKSPACE = "workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
_SCOPE = "current_setting('app.workspace_scope', true) = 'tenant'"
_POLICY = f"({_TENANT} AND ({_WORKSPACE} OR {_SCOPE}))"

# `dw_supply_chain.domain.product_development_case.ProductDevState` and
# `ProductAction`, exactly; an integration test asserts each CHECK equals its enum.
_STATES = (
    "proposed",
    "sample_requested",
    "sample_testing",
    "revision_requested",
    "pending_bod_review",
    "waiting_external",
    "blocked",
    "manual_review",
    "cancelled",
)
_INTERRUPTS = ("waiting_external", "blocked", "manual_review")
_ACTIONS = (
    "propose",
    "request_sample",
    "receive_sample",
    "pass_sample",
    "request_revision",
    "receive_revised_sample",
    "reject_sample",
    "wait_for_external",
    "flag_blocked",
    "flag_manual_review",
    "resume",
    "cancel",
)
_RESULTS = ("passed", "needs_revision", "rejected")

_CASE_FK = (
    "FOREIGN KEY (tenant_id, workspace_id, product_dev_case_id)"
    " REFERENCES supply_chain.product_dev_cases (tenant_id, workspace_id, id) ON DELETE CASCADE"
)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _doc_fk(column: str) -> str:
    return (
        f"FOREIGN KEY (tenant_id, workspace_id, product_dev_case_id, {column})"
        " REFERENCES supply_chain.case_documents (tenant_id, workspace_id, product_dev_case_id, id)"
        " ON DELETE NO ACTION"
    )


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE supply_chain.product_dev_cases (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            proposal_code text NOT NULL,
            product_name text NOT NULL,
            category text NOT NULL,
            supplier_name text,
            pic_user_id uuid NOT NULL,
            state text DEFAULT 'proposed' NOT NULL,
            interrupted_state text,
            sample_round integer DEFAULT 0 NOT NULL,
            version integer DEFAULT 1 NOT NULL,
            created_by uuid NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_product_dev_cases PRIMARY KEY (id),
            CONSTRAINT uq_product_dev_cases_tenant_id_workspace_id_id
                UNIQUE (tenant_id, workspace_id, id),
            CONSTRAINT uq_product_dev_cases_tenant_id_proposal_code
                UNIQUE (tenant_id, proposal_code),
            CONSTRAINT ck_product_dev_cases_proposal_code
                CHECK (btrim(proposal_code) <> '' AND char_length(proposal_code) <= 100),
            CONSTRAINT ck_product_dev_cases_product_name
                CHECK (btrim(product_name) <> '' AND char_length(product_name) <= 300),
            CONSTRAINT ck_product_dev_cases_category
                CHECK (btrim(category) <> '' AND char_length(category) <= 100),
            CONSTRAINT ck_product_dev_cases_supplier_name
                CHECK (supplier_name IS NULL OR btrim(supplier_name) <> ''),
            CONSTRAINT ck_product_dev_cases_state CHECK (state IN ({_in(_STATES)})),
            CONSTRAINT ck_product_dev_cases_interrupted_state CHECK (
                (state IN ({_in(_INTERRUPTS)})) = (interrupted_state IS NOT NULL)
                AND (interrupted_state IS NULL OR interrupted_state IN ({_in(_STATES)}))
                AND (interrupted_state IS NULL OR interrupted_state NOT IN ({_in(_INTERRUPTS)}))
            ),
            CONSTRAINT ck_product_dev_cases_sample_round CHECK (sample_round >= 0),
            CONSTRAINT ck_product_dev_cases_version CHECK (version >= 1)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_product_dev_cases_page ON supply_chain.product_dev_cases"
        " (tenant_id, created_at DESC, id DESC)"
    )
    op.execute(
        "CREATE INDEX ix_product_dev_cases_state_page ON supply_chain.product_dev_cases"
        " (tenant_id, state, created_at DESC, id DESC)"
    )
    op.execute(
        "CREATE INDEX ix_product_dev_cases_pic_page ON supply_chain.product_dev_cases"
        " (tenant_id, pic_user_id, created_at DESC, id DESC)"
    )
    op.execute(
        "CREATE TRIGGER touch_updated_at BEFORE UPDATE ON supply_chain.product_dev_cases"
        " FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at()"
    )

    op.execute(
        f"""
        CREATE TABLE supply_chain.product_dev_case_state_transitions (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            product_dev_case_id uuid NOT NULL,
            action text NOT NULL,
            from_state text,
            to_state text NOT NULL,
            reason text,
            actor_id uuid NOT NULL,
            occurred_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_product_dev_case_state_transitions PRIMARY KEY (id),
            CONSTRAINT fk_product_dev_case_state_transitions_case
                {_CASE_FK},
            CONSTRAINT ck_product_dev_case_state_transitions_action
                CHECK (action IN ({_in(_ACTIONS)})),
            CONSTRAINT ck_product_dev_case_state_transitions_from_state
                CHECK (from_state IS NULL OR from_state IN ({_in(_STATES)})),
            CONSTRAINT ck_product_dev_case_state_transitions_to_state
                CHECK (to_state IN ({_in(_STATES)})),
            CONSTRAINT ck_product_dev_case_state_transitions_only_propose_starts
                CHECK ((from_state IS NULL) = (action = 'propose'))
        )
        """
    )
    # The timeline read, and the cascade's lookup (case ids are unique, so
    # tenant then case pins the rows).
    op.execute(
        "CREATE INDEX ix_product_dev_case_transitions_tenant_id_case_id_occurred_at"
        " ON supply_chain.product_dev_case_state_transitions"
        " (tenant_id, product_dev_case_id, occurred_at)"
    )

    # The papers point at a case's own documents, so `case_documents` changes
    # before the tables that reference it.
    op.execute(
        f"""
        ALTER TABLE supply_chain.case_documents
            ALTER COLUMN po_case_id DROP NOT NULL,
            ADD COLUMN product_dev_case_id uuid,
            ADD CONSTRAINT fk_case_documents_tenant_id_product_dev_cases
                {_CASE_FK},
            ADD CONSTRAINT ck_case_documents_one_case
                CHECK (num_nonnulls(po_case_id, product_dev_case_id) = 1),
            ADD CONSTRAINT uq_case_documents_tenant_id_workspace_id_product_dev_case_id_id
                UNIQUE (tenant_id, workspace_id, product_dev_case_id, id),
            DROP CONSTRAINT ck_case_documents_object_key,
            ADD CONSTRAINT ck_case_documents_object_key CHECK (
                object_key = 'supply_chain/' || tenant_id::text || '/' || workspace_id::text
                    || CASE
                        WHEN po_case_id IS NOT NULL THEN '/po/' || po_case_id::text
                        ELSE '/product/' || product_dev_case_id::text
                    END
                    || '/' || id::text
            )
        """
    )
    # The version guard for a product case's documents; the per-case list and
    # the FK's own side are served by the UNIQUE above, which leads with them.
    op.execute(
        "CREATE UNIQUE INDEX uq_case_documents_tenant_id_product_case_doc_type_version"
        " ON supply_chain.case_documents (tenant_id, product_dev_case_id, doc_type, version)"
        " WHERE product_dev_case_id IS NOT NULL"
    )

    op.execute(
        f"""
        CREATE TABLE supply_chain.product_sample_rounds (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            product_dev_case_id uuid NOT NULL,
            round_no integer NOT NULL,
            opened_at timestamp with time zone DEFAULT now() NOT NULL,
            opened_by uuid NOT NULL,
            result text,
            evaluation_document_id uuid,
            closed_at timestamp with time zone,
            closed_by uuid,
            CONSTRAINT pk_product_sample_rounds PRIMARY KEY (id),
            CONSTRAINT uq_product_sample_rounds_tenant_id_product_dev_case_id_round_no
                UNIQUE (tenant_id, product_dev_case_id, round_no),
            CONSTRAINT uq_product_sample_rounds_evaluation_document_id
                UNIQUE (evaluation_document_id),
            CONSTRAINT fk_product_sample_rounds_tenant_id_product_dev_cases {_CASE_FK},
            CONSTRAINT fk_product_sample_rounds_tenant_id_case_documents
                {_doc_fk("evaluation_document_id")},
            CONSTRAINT ck_product_sample_rounds_round_no CHECK (round_no >= 1),
            CONSTRAINT ck_product_sample_rounds_result
                CHECK (result IS NULL OR result IN ({_in(_RESULTS)})),
            CONSTRAINT ck_product_sample_rounds_closed CHECK (
                (result IS NULL) = (closed_at IS NULL) AND (result IS NULL) = (closed_by IS NULL)
            ),
            CONSTRAINT ck_product_sample_rounds_passed_on_an_evaluation
                CHECK (result IS DISTINCT FROM 'passed' OR evaluation_document_id IS NOT NULL),
            CONSTRAINT ck_product_sample_rounds_evaluation_closes
                CHECK (evaluation_document_id IS NULL OR result IN ('passed', 'rejected'))
        )
        """
    )
    # At most one open round per case.
    op.execute(
        "CREATE UNIQUE INDEX uq_product_sample_rounds_tenant_id_product_dev_case_id"
        " ON supply_chain.product_sample_rounds (tenant_id, product_dev_case_id)"
        " WHERE result IS NULL"
    )
    op.execute(
        """
        CREATE FUNCTION supply_chain.sample_round_closes_once() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.result IS NOT NULL THEN
                RAISE EXCEPTION 'sample round % of case % is already closed',
                    OLD.round_no, OLD.product_dev_case_id
                    USING ERRCODE = 'check_violation',
                          CONSTRAINT = 'ck_product_sample_rounds_closes_once';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER closes_once BEFORE UPDATE ON supply_chain.product_sample_rounds"
        " FOR EACH ROW EXECUTE FUNCTION supply_chain.sample_round_closes_once()"
    )

    op.execute(
        f"""
        CREATE TABLE supply_chain.sample_revision_requests (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            product_dev_case_id uuid NOT NULL,
            round_no integer NOT NULL,
            revision_document_id uuid NOT NULL,
            requested_changes text NOT NULL,
            sent_by uuid NOT NULL,
            sent_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_sample_revision_requests PRIMARY KEY (id),
            CONSTRAINT uq_sample_revision_requests_tenant_id_case_round_no
                UNIQUE (tenant_id, product_dev_case_id, round_no),
            CONSTRAINT uq_sample_revision_requests_revision_document_id
                UNIQUE (revision_document_id),
            CONSTRAINT fk_sample_revision_requests_tenant_id_product_dev_cases {_CASE_FK},
            CONSTRAINT fk_sample_revision_requests_tenant_id_product_sample_rounds
                FOREIGN KEY (tenant_id, product_dev_case_id, round_no)
                REFERENCES supply_chain.product_sample_rounds
                    (tenant_id, product_dev_case_id, round_no) ON DELETE CASCADE,
            CONSTRAINT fk_sample_revision_requests_tenant_id_case_documents
                {_doc_fk("revision_document_id")},
            CONSTRAINT ck_sample_revision_requests_requested_changes
                CHECK (btrim(requested_changes) <> '')
        )
        """
    )

    # Written out per table, not looped: `scripts/verify_invariants.py` reads
    # this text, and a statement assembled in a loop is one it cannot see.
    # `test_rls_coverage.py` asks the catalog either way.
    for statement in (
        "ALTER TABLE supply_chain.product_dev_cases ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE supply_chain.product_dev_cases FORCE ROW LEVEL SECURITY",
        "CREATE POLICY tenant_isolation_product_dev_cases ON supply_chain.product_dev_cases",
        "ALTER TABLE supply_chain.product_dev_case_state_transitions ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE supply_chain.product_dev_case_state_transitions FORCE ROW LEVEL SECURITY",
        "CREATE POLICY tenant_isolation_product_dev_case_state_transitions ON supply_chain.product_dev_case_state_transitions",  # noqa: E501
        "ALTER TABLE supply_chain.product_sample_rounds ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE supply_chain.product_sample_rounds FORCE ROW LEVEL SECURITY",
        "CREATE POLICY tenant_isolation_product_sample_rounds ON supply_chain.product_sample_rounds",
        "ALTER TABLE supply_chain.sample_revision_requests ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE supply_chain.sample_revision_requests FORCE ROW LEVEL SECURITY",
        "CREATE POLICY tenant_isolation_sample_revision_requests ON supply_chain.sample_revision_requests",  # noqa: E501
    ):
        if statement.startswith("CREATE POLICY"):
            statement += f" USING {_POLICY} WITH CHECK {_POLICY}"
        op.execute(statement)
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                REVOKE TRUNCATE ON supply_chain.product_dev_cases FROM dw_app;
                REVOKE UPDATE, DELETE, TRUNCATE
                    ON supply_chain.product_dev_case_state_transitions,
                       supply_chain.sample_revision_requests,
                       supply_chain.product_sample_rounds
                    FROM dw_app;
                GRANT UPDATE (result, evaluation_document_id, closed_at, closed_by)
                    ON supply_chain.product_sample_rounds TO dw_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE supply_chain.sample_revision_requests")
    op.execute("DROP TABLE supply_chain.product_sample_rounds")
    op.execute("DROP FUNCTION supply_chain.sample_round_closes_once()")
    # Product documents cannot survive without the column that names their case.
    op.execute("DELETE FROM supply_chain.case_documents WHERE product_dev_case_id IS NOT NULL")
    op.execute("DROP INDEX supply_chain.uq_case_documents_tenant_id_product_case_doc_type_version")
    op.execute(
        """
        ALTER TABLE supply_chain.case_documents
            DROP CONSTRAINT ck_case_documents_object_key,
            DROP CONSTRAINT uq_case_documents_tenant_id_workspace_id_product_dev_case_id_id,
            DROP CONSTRAINT ck_case_documents_one_case,
            DROP CONSTRAINT fk_case_documents_tenant_id_product_dev_cases,
            DROP COLUMN product_dev_case_id,
            ALTER COLUMN po_case_id SET NOT NULL,
            ADD CONSTRAINT ck_case_documents_object_key CHECK (
                object_key = 'supply_chain/' || tenant_id::text || '/' || workspace_id::text
                    || '/po/' || po_case_id::text || '/' || id::text
            )
        """
    )
    op.execute("DROP TABLE supply_chain.product_dev_case_state_transitions")
    op.execute("DROP TABLE supply_chain.product_dev_cases")
