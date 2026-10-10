"""supply_chain catalogue items and the import scope

Revision ID: 0f231b1bf02d
Revises: 1021f7fe88f0
Create Date: 2026-10-09 16:16:09.358532+00:00

The one-time import of a tenant's existing data (ADR 0027, E16; ticket
onboarding/01).

- **`catalogue_items`**: the item and SKU catalogue a tenant had before the
  application, one row per SKU (or per item code with none), which step 9's
  duplicate check reads beside `item_codes` and `skus` (ticket
  ai-automation/13). Codes are trimmed and non-blank; UNIQUE NULLS NOT
  DISTINCT `(tenant_id, workspace_id, item_code, sku_code)` is what makes a
  re-import add nothing, and UNIQUE `(tenant_id, workspace_id, sku_code)`
  keeps one SKU under one item code. `(tenant_id, workspace_id, item_code)`
  leads the first, so a lookup by item code has its index.
- **Workspace RLS** FORCEd, the shape `CLAUDE.md` allows, as `suppliers`
  (a catalogue is read where its workspace's cases are).
- **Grants:** `dw_app` SELECT and INSERT, and DELETE only so the offboarding
  purge (which empties what `dw_app` may delete) takes it; no code path
  deletes or edits a row.
- **`suppliers.code`**: `dw_app` gains UPDATE on that one column, so an
  import can give a supplier the application created by name (from a case)
  its code. The statement only sets a NULL code; the UNIQUE per workspace
  still decides a code is free. Nothing else of a supplier becomes writable.
- **`supply_chain.import`** for `org_admin`: who runs the import. Each part of
  a row still needs its own scope (`supply_chain.commercial.write` for a
  contact or bank account, `platform.members.write` for a user).

Reviewed by reading; not run here (no database on the machine that wrote it).
"""

from __future__ import annotations

import json

from alembic import op

revision = "0f231b1bf02d"
down_revision = "1021f7fe88f0"
branch_labels = None
depends_on = None

_TENANT = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
_WORKSPACE = "workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
_SCOPE = "current_setting('app.workspace_scope', true) = 'tenant'"
_POLICY = f"({_TENANT} AND ({_WORKSPACE} OR {_SCOPE}))"
_IMPORT = "supply_chain.import"
_IMPORT_ROLES = ("org_admin",)


def _code(column: str, nullable: bool) -> str:
    rule = f"btrim({column}) = {column} AND {column} <> '' AND char_length({column}) <= 64"
    return f"{column} IS NULL OR ({rule})" if nullable else rule


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE supply_chain.catalogue_items (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            item_code text NOT NULL,
            sku_code text,
            name text NOT NULL,
            category text,
            imported_by uuid NOT NULL,
            imported_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_catalogue_items PRIMARY KEY (id),
            CONSTRAINT uq_catalogue_items_tenant_id_workspace_id_item_code_sku_code
                UNIQUE NULLS NOT DISTINCT (tenant_id, workspace_id, item_code, sku_code),
            CONSTRAINT uq_catalogue_items_tenant_id_workspace_id_sku_code
                UNIQUE (tenant_id, workspace_id, sku_code),
            CONSTRAINT ck_catalogue_items_item_code CHECK ({_code("item_code", False)}),
            CONSTRAINT ck_catalogue_items_sku_code CHECK ({_code("sku_code", True)}),
            CONSTRAINT ck_catalogue_items_name
                CHECK (btrim(name) <> '' AND char_length(name) <= 300),
            CONSTRAINT ck_catalogue_items_category CHECK (
                category IS NULL OR (btrim(category) <> '' AND char_length(category) <= 100)
            )
        )
        """
    )
    op.execute("ALTER TABLE supply_chain.catalogue_items ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE supply_chain.catalogue_items FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_catalogue_items ON supply_chain.catalogue_items"
        f" USING {_POLICY} WITH CHECK {_POLICY}"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                REVOKE ALL ON supply_chain.catalogue_items FROM dw_app;
                GRANT SELECT, INSERT, DELETE ON supply_chain.catalogue_items TO dw_app;
                GRANT UPDATE (code) ON supply_chain.suppliers TO dw_app;
            END IF;
        END
        $$
        """
    )
    roles = ", ".join(f"'{r}'" for r in _IMPORT_ROLES)
    op.execute(
        f"""
        UPDATE platform.roles SET scopes = scopes || '{json.dumps([_IMPORT])}'::jsonb
        WHERE key IN ({roles}) AND NOT scopes ? '{_IMPORT}'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM supply_chain.catalogue_items) THEN
                RAISE EXCEPTION 'catalogue items were imported; this downgrade would lose them';
            END IF;
        END
        $$
        """
    )
    op.execute(f"UPDATE platform.roles SET scopes = scopes - '{_IMPORT}'")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                REVOKE UPDATE (code) ON supply_chain.suppliers FROM dw_app;
            END IF;
        END
        $$
        """
    )
    op.execute("DROP TABLE supply_chain.catalogue_items")
