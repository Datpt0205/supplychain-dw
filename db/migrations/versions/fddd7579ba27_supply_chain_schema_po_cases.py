"""supply_chain schema + po_cases — the first Supply Chain table

Revision ID: fddd7579ba27
Revises: 855ae928c3fa
Create Date: 2026-09-23 09:03:57.375730+00:00

`dw_supply_chain` is the first bounded context that is actual business domain
rather than shared backbone (`platform`/`knowledge`/`memory`, all inherited by
every product built on this platform). A dedicated schema keeps its data
physically separable from the backbone's own tables — relevant the day a
narrower role, or a second-language service under the conditions
`CLAUDE.md`'s "A second language" section states, needs Supply Chain data and
nothing else.

Persists exactly what `dw_supply_chain.domain.po_case.POCase` already models
(commit `faa4016`) — no columns ahead of what the aggregate has a field for.
`updated_at` is the one addition beyond the dataclass: every other actively-
mutated table in this repo (`worker_runs`, `agent_store`, `ingest_jobs`) carries
it, trigger-maintained by the existing `platform.touch_updated_at()` (reused,
not redefined — it already yields to a value the statement states, migration
`e597bd453625`). Per-milestone SLA timestamps (when did this case enter
`waiting_deposit`, etc.) are deliberately NOT added here: that is the SLA
evaluator's own slice, informed by how it actually needs to query, not
guessed ahead of it.

A new schema needs its own grants — `0001_platform_grants.sql`'s
`ALTER DEFAULT PRIVILEGES IN SCHEMA platform, knowledge, memory` does not
reach a fourth schema. Mirrors that file's own shape: warn and skip if
`dw_app` does not exist yet, rather than fail the migration outright.

`test_rls_coverage.py` and `test_privileges.py` discover tenant schemas from
the catalog, so they cover `supply_chain` with no list to update. (In the
archive this revision came with a hand-kept `_TENANT_SCHEMAS` edit; on this
repo it re-chains onto `855ae928c3fa`, the platform head it was ported onto.)
"""

from __future__ import annotations

from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401


revision = 'fddd7579ba27'
down_revision = '855ae928c3fa'
branch_labels = None
depends_on = None

_TENANT_PREDICATE = (
    "(tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid)"
)

# Every `dw_supply_chain.domain.po_case.CaseState` value, exactly as the
# StrEnum spells it. Kept as a plain CHECK rather than a re-implementation of
# `POCase`'s transition guards in SQL — this constraint's job is "is this a
# name the application recognises at all", not "is this transition legal
# from here", which stays owned by the one place that already enforces it.
_CASE_STATES = (
    "'po_created', 'waiting_deposit', 'deposit_confirmed', 'pre_production',"
    " 'production', 'qc', 'in_transit', 'arrived_port', 'waiting_payment',"
    " 'payment_completed', 'warehouse_receiving', 'completed',"
    " 'waiting_external', 'blocked', 'rework', 'manual_review', 'cancelled'"
)

_UPGRADE = (
    "CREATE SCHEMA supply_chain",
    f"""
    CREATE TABLE supply_chain.po_cases (
        id uuid NOT NULL,
        tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        po_reference text NOT NULL,
        supplier_name text NOT NULL,
        state text NOT NULL DEFAULT 'po_created',
        interrupted_state text,
        created_at timestamp with time zone DEFAULT now() NOT NULL,
        updated_at timestamp with time zone DEFAULT now() NOT NULL,
        version integer DEFAULT 1 NOT NULL,
        CONSTRAINT pk_po_cases PRIMARY KEY (id),
        CONSTRAINT uq_po_cases_tenant_id_po_reference UNIQUE (tenant_id, po_reference),
        CONSTRAINT ck_po_cases_version CHECK (version >= 1),
        CONSTRAINT ck_po_cases_po_reference CHECK (po_reference <> ''),
        CONSTRAINT ck_po_cases_supplier_name CHECK (supplier_name <> ''),
        CONSTRAINT ck_po_cases_state CHECK (state IN ({_CASE_STATES})),
        CONSTRAINT ck_po_cases_interrupted_state
            CHECK (interrupted_state IS NULL OR interrupted_state IN ({_CASE_STATES}))
    )
    """,
    "ALTER TABLE supply_chain.po_cases ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE supply_chain.po_cases FORCE ROW LEVEL SECURITY",
    f"CREATE POLICY tenant_isolation_po_cases ON supply_chain.po_cases"
    f" USING {_TENANT_PREDICATE} WITH CHECK {_TENANT_PREDICATE}",
    "CREATE TRIGGER touch_updated_at BEFORE UPDATE ON supply_chain.po_cases"
    " FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at()",
    """
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
            RAISE WARNING USING
                MESSAGE = 'role dw_app does not exist; supply_chain grants skipped',
                HINT = 'CREATE ROLE dw_app LOGIN PASSWORD ''...''; then re-run this migration';
            RETURN;
        END IF;

        GRANT USAGE ON SCHEMA supply_chain TO dw_app;
        GRANT SELECT, INSERT, UPDATE, DELETE
            ON ALL TABLES IN SCHEMA supply_chain TO dw_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA supply_chain
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO dw_app;
    END
    $$
    """,
)

_DOWNGRADE = (
    "DROP SCHEMA supply_chain CASCADE",
)


def upgrade() -> None:
    for statement in _UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in _DOWNGRADE:
        op.execute(statement)
