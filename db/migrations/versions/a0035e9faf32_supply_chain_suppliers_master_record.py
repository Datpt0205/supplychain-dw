"""supply_chain suppliers master record

Revision ID: a0035e9faf32
Revises: d9e136c14d83
Create Date: 2026-10-08 09:40:00.000000+00:00

A supplier was free text on each case, so two spellings were two suppliers
(supply-chain ticket `hardening/04`). `supply_chain.suppliers` is the master
record, one per supplier per tenant AND workspace, and every case names one.

- **One owner of "the same supplier":** `supply_chain.normalize_supplier_name`
  (IMMUTABLE): NFKC (which also turns a no-break space into a space), runs of
  whitespace to one space, trimmed, lower-cased under the ICU root collation
  so the answer does not depend on the database's locale. `normalized_name` is
  a column GENERATED from it, UNIQUE per `(tenant_id, workspace_id)`: the
  database, not the application, decides two names are one supplier. Marks
  are kept: "Đông Á" and "Dong A" stay two suppliers (merging them is the merge
  UI's job, not a normalizer's guess).
- **`code`** optional, UNIQUE per workspace when set, CHECKed non-blank.
- **Cases reference it by `(tenant_id, workspace_id, supplier_id,
  supplier_name)`** -> `suppliers (tenant_id, workspace_id, id, name)`, `ON
  UPDATE CASCADE ON DELETE RESTRICT`. The case keeps `supplier_name` (every
  read, filter and index of it stays as it is), but it is now the supplier's
  stored name and cannot be anything else: the FK refuses another value and a
  rename reaches every case. Two copies that cannot disagree, rather than a
  join added to every read. `po_cases.supplier_id` is NOT NULL; a product
  case names its supplier from step 2, so its `supplier_id` is NULL until then
  (`ck_product_dev_cases_supplier`: both or neither).
- **Backfill:** one supplier per distinct normalized name per workspace, named
  by the spelling first used (the oldest case); every case then points at it
  and takes its name.
- **Workspace RLS**, ENABLEd and FORCEd, the shape `CLAUDE.md` allows.
- **Grants ship here:** `dw_app` SELECT, INSERT, and DELETE (only so the
  offboarding purge, which deletes what `dw_app` may delete, empties it; the
  RESTRICT from every case keeps a used supplier, and no code path deletes
  one). No UPDATE: nothing renames a supplier yet.
- `updated_at` by `platform.touch_updated_at()`.
"""

from __future__ import annotations

from alembic import op

revision = "a0035e9faf32"
down_revision = "d9e136c14d83"
branch_labels = None
depends_on = None

_TENANT = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
_WORKSPACE = "workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
_SCOPE = "current_setting('app.workspace_scope', true) = 'tenant'"
_POLICY = f"({_TENANT} AND ({_WORKSPACE} OR {_SCOPE}))"

_NORMALIZE = r"""
CREATE FUNCTION supply_chain.normalize_supplier_name(name text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    RETURN lower(
        btrim(regexp_replace(normalize(name, NFKC), '\s+', ' ', 'g')) COLLATE "und-x-icu"
    )
"""

# (table, nullable): each case table, and whether a case may have no supplier yet.
_CASES = (("po_cases", False), ("product_dev_cases", True))


def upgrade() -> None:
    op.execute(_NORMALIZE)
    op.execute(
        """
        CREATE TABLE supply_chain.suppliers (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            name text NOT NULL,
            normalized_name text GENERATED ALWAYS AS
                (supply_chain.normalize_supplier_name(name)) STORED,
            code text,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_suppliers PRIMARY KEY (id),
            CONSTRAINT uq_suppliers_tenant_id_workspace_id_normalized_name
                UNIQUE (tenant_id, workspace_id, normalized_name),
            CONSTRAINT uq_suppliers_tenant_id_workspace_id_code
                UNIQUE (tenant_id, workspace_id, code),
            CONSTRAINT uq_suppliers_tenant_id_workspace_id_id_name
                UNIQUE (tenant_id, workspace_id, id, name),
            CONSTRAINT ck_suppliers_name CHECK (
                normalized_name <> '' AND char_length(name) <= 200
            ),
            CONSTRAINT ck_suppliers_code CHECK (
                code IS NULL OR (btrim(code) <> '' AND char_length(code) <= 64)
            )
        )
        """
    )
    op.execute(
        "CREATE TRIGGER touch_updated_at BEFORE UPDATE ON supply_chain.suppliers"
        " FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at()"
    )
    op.execute("ALTER TABLE supply_chain.suppliers ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE supply_chain.suppliers FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_suppliers ON supply_chain.suppliers"
        f" USING {_POLICY} WITH CHECK {_POLICY}"
    )

    # Backfill: one supplier per normalized name per workspace, named as it
    # was first written.
    op.execute(
        """
        INSERT INTO supply_chain.suppliers (id, tenant_id, workspace_id, name)
        SELECT gen_random_uuid(), tenant_id, workspace_id,
               (array_agg(supplier_name ORDER BY first_used, supplier_name))[1]
        FROM (
            SELECT tenant_id, workspace_id, supplier_name, min(created_at) AS first_used
            FROM (
                SELECT tenant_id, workspace_id, supplier_name, created_at
                FROM supply_chain.po_cases
                UNION ALL
                SELECT tenant_id, workspace_id, supplier_name, created_at
                FROM supply_chain.product_dev_cases WHERE supplier_name IS NOT NULL
            ) used
            GROUP BY tenant_id, workspace_id, supplier_name
        ) spellings
        GROUP BY tenant_id, workspace_id, supply_chain.normalize_supplier_name(supplier_name)
        """
    )
    for table, nullable in _CASES:
        op.execute(f"ALTER TABLE supply_chain.{table} ADD COLUMN supplier_id uuid")
        op.execute(
            f"""
            UPDATE supply_chain.{table} c
            SET supplier_id = s.id, supplier_name = s.name
            FROM supply_chain.suppliers s
            WHERE s.tenant_id = c.tenant_id AND s.workspace_id = c.workspace_id
              AND s.normalized_name = supply_chain.normalize_supplier_name(c.supplier_name)
            """
        )
        if not nullable:
            op.execute(f"ALTER TABLE supply_chain.{table} ALTER COLUMN supplier_id SET NOT NULL")
        op.execute(
            f"ALTER TABLE supply_chain.{table} ADD CONSTRAINT fk_{table}_tenant_id_suppliers"
            " FOREIGN KEY (tenant_id, workspace_id, supplier_id, supplier_name)"
            " REFERENCES supply_chain.suppliers (tenant_id, workspace_id, id, name)"
            " ON UPDATE CASCADE ON DELETE RESTRICT"
        )
        op.execute(
            f"CREATE INDEX ix_{table}_tenant_id_workspace_id_supplier_id"
            f" ON supply_chain.{table} (tenant_id, workspace_id, supplier_id, supplier_name)"
        )
    op.execute(
        "ALTER TABLE supply_chain.product_dev_cases ADD CONSTRAINT ck_product_dev_cases_supplier"
        " CHECK ((supplier_id IS NULL) = (supplier_name IS NULL))"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                REVOKE ALL ON supply_chain.suppliers FROM dw_app;
                GRANT SELECT, INSERT, DELETE ON supply_chain.suppliers TO dw_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE supply_chain.product_dev_cases DROP CONSTRAINT ck_product_dev_cases_supplier"
    )
    for table, _ in reversed(_CASES):
        op.execute(f"ALTER TABLE supply_chain.{table} DROP COLUMN supplier_id")
    op.execute("DROP TABLE supply_chain.suppliers")
    op.execute("DROP FUNCTION supply_chain.normalize_supplier_name(text)")
