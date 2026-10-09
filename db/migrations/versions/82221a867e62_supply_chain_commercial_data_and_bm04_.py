"""supply_chain commercial data and bm04 fields

Revision ID: 82221a867e62
Revises: f38f027d8342
Create Date: 2026-10-09 08:27:50.907988+00:00

BM04 and the commercial data the later AI tickets compute and check with, as
typed fields behind a price scope (ADR 0026, E15; ticket ai-automation/01).

- **`product_profiles`**: one row per BM04 save, append-only (`dw_app` SELECT
  and INSERT only). Typed columns for what code computes with (unit price,
  currency, MOQ, lead time in days, Incoterm); `attributes jsonb` for the rest,
  checked against the tenant's schema by the handler and held to a JSON object
  here. `version` is the database's to keep unique per case (UNIQUE, computed
  in the INSERT, a 409 by constraint name on a race, as for `case_documents`).
  Composite FK `(tenant_id, workspace_id, product_dev_case_id)` to
  `product_dev_cases`, `ON DELETE CASCADE`: the only way a row leaves is with
  its case (offboarding), the reasoning of ADR 0021's amendment for documents.
  A price needs its currency (`ck_product_profiles_price_has_currency`).
- **`po_cases`** gains currency, Incoterm, payment terms, `deposit_percent`
  (CHECK 0-100) and the expected delivery date; **`po_case_lines`** gains
  `unit_price`, and `dw_app` may UPDATE that column (it already may UPDATE
  `quantity`, nothing else). No total is stored: code computes it.
- **`po_payments`**: a deposit or final payment per version, append-only,
  amount > 0, its paper a composite FK to a document of the SAME PO case
  (`case_documents (tenant_id, workspace_id, po_case_id, id)`, `ON DELETE NO
  ACTION` like the packaging designs' report: a cited paper cannot go on its
  own; the case's cascade takes both in one statement).
- **`supplier_contacts`**, **`supplier_bank_accounts`**: append-only versions
  per supplier, composite FK to the supplier (`suppliers` gains
  `uq_suppliers_tenant_id_workspace_id_id` for it) `ON DELETE CASCADE`, so the
  offboarding purge that may DELETE suppliers takes them along. An account
  number is stored normalised (`^[0-9A-Z]{6,34}$`); code compares it, a prompt
  never sees it.
- **Fixed sets are CHECKs**: currency (ISO 4217 price currencies, equal to
  `dw_supply_chain.domain.commercial.CURRENCY_CODES`), Incoterm (Incoterms
  2020, equal to `Incoterm`), payment kind. An integration test asserts each
  equals its domain set.
- **Workspace RLS** on the four new tables, ENABLEd and FORCEd, the one shape
  `CLAUDE.md` allows; indexes lead with `tenant_id` and carry each FK.
- **Scopes:** `supply_chain.commercial.read` for `sc_operator`, `sc_finance`,
  `sc_bod`; `.write` for `sc_operator`, `sc_finance`, and the write joins the
  operations side of `sod_sc_rules_vs_operations` like every other operating
  write. Which roles hold them is data; a later migration (or a tenant's own
  role) may change it.

Reviewed by reading; not run here (no database on the machine that wrote it).
"""

from __future__ import annotations

import json

from alembic import op

revision = "82221a867e62"
down_revision = "f38f027d8342"
branch_labels = None
depends_on = None

_TENANT = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
_WORKSPACE = "workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
_SCOPE = "current_setting('app.workspace_scope', true) = 'tenant'"
_POLICY = f"({_TENANT} AND ({_WORKSPACE} OR {_SCOPE}))"

_CURRENCIES = """
    AED AFN ALL AMD AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL
    BSD BTN BWP BYN BZD CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD
    EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR
    ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP
    LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD
    NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR
    SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP
    TRY TTD TWD TZS UAH UGX USD UYU UZS VED VES VND VUV WST XAF XCD XCG XOF XPF
    YER ZAR ZMW ZWG
""".split()
_INCOTERMS = ("EXW", "FCA", "CPT", "CIP", "DAP", "DPU", "DDP", "FAS", "FOB", "CFR", "CIF")

_READ = "supply_chain.commercial.read"
_WRITE = "supply_chain.commercial.write"
_READ_ROLES = ("sc_operator", "sc_finance", "sc_bod")
_WRITE_ROLES = ("sc_operator", "sc_finance")
_RULE = "sod_sc_rules_vs_operations"

_NEW_TABLES = ("product_profiles", "po_payments", "supplier_contacts", "supplier_bank_accounts")


def _in(values: tuple[str, ...] | list[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _currency(column: str) -> str:
    return f"{column} IS NULL OR {column} IN ({_in(_CURRENCIES)})"


def _incoterm(column: str) -> str:
    return f"{column} IS NULL OR {column} IN ({_in(_INCOTERMS)})"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE supply_chain.product_profiles (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            product_dev_case_id uuid NOT NULL,
            version integer NOT NULL,
            unit_price numeric(18, 4),
            currency text,
            moq integer,
            lead_time_days integer,
            incoterm text,
            attributes jsonb DEFAULT '{{}}'::jsonb NOT NULL,
            schema_version text NOT NULL,
            created_by uuid NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_product_profiles PRIMARY KEY (id),
            CONSTRAINT uq_product_profiles_tenant_id_product_dev_case_id_version
                UNIQUE (tenant_id, product_dev_case_id, version),
            CONSTRAINT fk_product_profiles_tenant_id_product_dev_cases
                FOREIGN KEY (tenant_id, workspace_id, product_dev_case_id)
                REFERENCES supply_chain.product_dev_cases (tenant_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT ck_product_profiles_version CHECK (version >= 1),
            CONSTRAINT ck_product_profiles_unit_price CHECK (unit_price IS NULL OR unit_price >= 0),
            CONSTRAINT ck_product_profiles_currency CHECK ({_currency("currency")}),
            CONSTRAINT ck_product_profiles_price_has_currency
                CHECK (unit_price IS NULL OR currency IS NOT NULL),
            CONSTRAINT ck_product_profiles_moq CHECK (moq IS NULL OR moq > 0),
            CONSTRAINT ck_product_profiles_lead_time_days
                CHECK (lead_time_days IS NULL OR lead_time_days BETWEEN 0 AND 3650),
            CONSTRAINT ck_product_profiles_incoterm CHECK ({_incoterm("incoterm")}),
            CONSTRAINT ck_product_profiles_attributes CHECK (jsonb_typeof(attributes) = 'object'),
            CONSTRAINT ck_product_profiles_schema_version
                CHECK (schema_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$')
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_product_profiles_tenant_id_workspace_id_product_dev_case_id"
        " ON supply_chain.product_profiles (tenant_id, workspace_id, product_dev_case_id)"
    )

    op.execute(
        f"""
        ALTER TABLE supply_chain.po_cases
            ADD COLUMN currency text,
            ADD COLUMN incoterm text,
            ADD COLUMN payment_terms text,
            ADD COLUMN deposit_percent numeric(5, 2),
            ADD COLUMN expected_delivery_date date,
            ADD CONSTRAINT ck_po_cases_currency CHECK ({_currency("currency")}),
            ADD CONSTRAINT ck_po_cases_incoterm CHECK ({_incoterm("incoterm")}),
            ADD CONSTRAINT ck_po_cases_payment_terms
                CHECK (payment_terms IS NULL OR char_length(payment_terms) BETWEEN 1 AND 500),
            ADD CONSTRAINT ck_po_cases_deposit_percent
                CHECK (deposit_percent IS NULL OR deposit_percent BETWEEN 0 AND 100)
        """
    )
    op.execute(
        """
        ALTER TABLE supply_chain.po_case_lines
            ADD COLUMN unit_price numeric(18, 4),
            ADD CONSTRAINT ck_po_case_lines_unit_price
                CHECK (unit_price IS NULL OR unit_price >= 0)
        """
    )

    op.execute(
        f"""
        CREATE TABLE supply_chain.po_payments (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            po_case_id uuid NOT NULL,
            kind text NOT NULL,
            version integer NOT NULL,
            amount numeric(18, 2) NOT NULL,
            currency text NOT NULL,
            due_date date,
            paid_on date,
            document_id uuid,
            recorded_by uuid NOT NULL,
            recorded_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_po_payments PRIMARY KEY (id),
            CONSTRAINT uq_po_payments_tenant_id_po_case_id_kind_version
                UNIQUE (tenant_id, po_case_id, kind, version),
            CONSTRAINT fk_po_payments_tenant_id_po_cases
                FOREIGN KEY (tenant_id, workspace_id, po_case_id)
                REFERENCES supply_chain.po_cases (tenant_id, workspace_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_po_payments_tenant_id_case_documents
                FOREIGN KEY (tenant_id, workspace_id, po_case_id, document_id)
                REFERENCES supply_chain.case_documents (tenant_id, workspace_id, po_case_id, id)
                ON DELETE NO ACTION,
            CONSTRAINT ck_po_payments_kind CHECK (kind IN ('deposit', 'final')),
            CONSTRAINT ck_po_payments_version CHECK (version >= 1),
            CONSTRAINT ck_po_payments_amount CHECK (amount > 0),
            CONSTRAINT ck_po_payments_currency
                CHECK (currency IS NOT NULL AND ({_currency("currency")}))
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_po_payments_tenant_id_workspace_id_po_case_id"
        " ON supply_chain.po_payments (tenant_id, workspace_id, po_case_id)"
    )
    op.execute(
        "CREATE INDEX ix_po_payments_tenant_id_workspace_id_po_case_id_document_id"
        " ON supply_chain.po_payments (tenant_id, workspace_id, po_case_id, document_id)"
        " WHERE document_id IS NOT NULL"
    )

    op.execute(
        "ALTER TABLE supply_chain.suppliers ADD CONSTRAINT uq_suppliers_tenant_id_workspace_id_id"
        " UNIQUE (tenant_id, workspace_id, id)"
    )
    op.execute(
        """
        CREATE TABLE supply_chain.supplier_contacts (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            supplier_id uuid NOT NULL,
            version integer NOT NULL,
            name text NOT NULL,
            email text,
            phone text,
            created_by uuid NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_supplier_contacts PRIMARY KEY (id),
            CONSTRAINT uq_supplier_contacts_tenant_id_supplier_id_version
                UNIQUE (tenant_id, supplier_id, version),
            CONSTRAINT fk_supplier_contacts_tenant_id_suppliers
                FOREIGN KEY (tenant_id, workspace_id, supplier_id)
                REFERENCES supply_chain.suppliers (tenant_id, workspace_id, id) ON DELETE CASCADE,
            CONSTRAINT ck_supplier_contacts_version CHECK (version >= 1),
            CONSTRAINT ck_supplier_contacts_name
                CHECK (btrim(name) <> '' AND char_length(name) <= 200),
            CONSTRAINT ck_supplier_contacts_email
                CHECK (email IS NULL OR (char_length(email) <= 254 AND email ~ '^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$')),
            CONSTRAINT ck_supplier_contacts_phone
                CHECK (phone IS NULL OR char_length(phone) BETWEEN 1 AND 40)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_supplier_contacts_tenant_id_workspace_id_supplier_id"
        " ON supply_chain.supplier_contacts (tenant_id, workspace_id, supplier_id)"
    )
    op.execute(
        """
        CREATE TABLE supply_chain.supplier_bank_accounts (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            supplier_id uuid NOT NULL,
            version integer NOT NULL,
            bank_name text NOT NULL,
            account_number text NOT NULL,
            account_holder text NOT NULL,
            created_by uuid NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_supplier_bank_accounts PRIMARY KEY (id),
            CONSTRAINT uq_supplier_bank_accounts_tenant_id_supplier_id_version
                UNIQUE (tenant_id, supplier_id, version),
            CONSTRAINT fk_supplier_bank_accounts_tenant_id_suppliers
                FOREIGN KEY (tenant_id, workspace_id, supplier_id)
                REFERENCES supply_chain.suppliers (tenant_id, workspace_id, id) ON DELETE CASCADE,
            CONSTRAINT ck_supplier_bank_accounts_version CHECK (version >= 1),
            CONSTRAINT ck_supplier_bank_accounts_account_number
                CHECK (account_number ~ '^[0-9A-Z]{6,34}$'),
            CONSTRAINT ck_supplier_bank_accounts_bank_name
                CHECK (btrim(bank_name) <> '' AND char_length(bank_name) <= 200),
            CONSTRAINT ck_supplier_bank_accounts_account_holder
                CHECK (btrim(account_holder) <> '' AND char_length(account_holder) <= 200)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_supplier_bank_accounts_tenant_id_workspace_id_supplier_id"
        " ON supply_chain.supplier_bank_accounts (tenant_id, workspace_id, supplier_id)"
    )

    # Spelled out per table: `verify_invariants.py` reads this text, and a loop
    # over names would hide the four statements from it.
    op.execute("ALTER TABLE supply_chain.product_profiles ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE supply_chain.product_profiles FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_product_profiles ON supply_chain.product_profiles"
        f" USING {_POLICY} WITH CHECK {_POLICY}"
    )
    op.execute("ALTER TABLE supply_chain.po_payments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE supply_chain.po_payments FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_po_payments ON supply_chain.po_payments"
        f" USING {_POLICY} WITH CHECK {_POLICY}"
    )
    op.execute("ALTER TABLE supply_chain.supplier_contacts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE supply_chain.supplier_contacts FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_supplier_contacts ON supply_chain.supplier_contacts"
        f" USING {_POLICY} WITH CHECK {_POLICY}"
    )
    op.execute("ALTER TABLE supply_chain.supplier_bank_accounts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE supply_chain.supplier_bank_accounts FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_supplier_bank_accounts ON supply_chain.supplier_bank_accounts"
        f" USING {_POLICY} WITH CHECK {_POLICY}"
    )
    revokes = "\n".join(
        f"REVOKE ALL ON supply_chain.{table} FROM dw_app;"
        f" GRANT SELECT, INSERT ON supply_chain.{table} TO dw_app;"
        for table in _NEW_TABLES
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                {revokes}
                GRANT UPDATE (unit_price) ON supply_chain.po_case_lines TO dw_app;
            END IF;
        END
        $$
        """
    )

    op.execute(
        f"""
        UPDATE platform.roles SET scopes = scopes || '{json.dumps([_READ])}'::jsonb
        WHERE key IN ({_in(_READ_ROLES)}) AND NOT scopes ? '{_READ}'
        """
    )
    op.execute(
        f"""
        UPDATE platform.roles SET scopes = scopes || '{json.dumps([_WRITE])}'::jsonb
        WHERE key IN ({_in(_WRITE_ROLES)}) AND NOT scopes ? '{_WRITE}'
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
    op.execute(f"UPDATE platform.roles SET scopes = scopes - '{_READ}' - '{_WRITE}'")
    for table in reversed(_NEW_TABLES):
        op.execute(f"DROP TABLE supply_chain.{table}")
    op.execute(
        "ALTER TABLE supply_chain.suppliers DROP CONSTRAINT uq_suppliers_tenant_id_workspace_id_id"
    )
    op.execute(
        "ALTER TABLE supply_chain.po_case_lines DROP CONSTRAINT ck_po_case_lines_unit_price,"
        " DROP COLUMN unit_price"
    )
    op.execute(
        """
        ALTER TABLE supply_chain.po_cases
            DROP CONSTRAINT ck_po_cases_currency,
            DROP CONSTRAINT ck_po_cases_incoterm,
            DROP CONSTRAINT ck_po_cases_payment_terms,
            DROP CONSTRAINT ck_po_cases_deposit_percent,
            DROP COLUMN currency,
            DROP COLUMN incoterm,
            DROP COLUMN payment_terms,
            DROP COLUMN deposit_percent,
            DROP COLUMN expected_delivery_date
        """
    )
