"""supply chain sla by category and follow ups for product cases

Revision ID: d56da3dd2146
Revises: 84d1c1946b44
Create Date: 2026-10-07 08:31:20.126606+00:00

SLA by Category and follow-ups that reach the case's PIC (stage-1 ticket 06,
ADR 0019).

- **Stored `supply_chain_sla` overrides become 2.0** (as `89e86dfabad6` and
  `84d1c1946b44` carried stored overrides across a schema change): `sla` is
  renamed `default`, every number kept as it was; `categories` is the list the
  2.0.0 platform file ships (`noi`, `chao`), since a 1.x document had none and
  the schema requires one to open a case; `by_category` is left out (empty).
  Nothing a tenant evaluated on changes: a Category with no entry uses
  `default`, which is the old `sla`.
- **`follow_ups` gains product cases.** `po_case_id` becomes nullable beside a
  new `product_dev_case_id`, and `ck_follow_ups_one_case` says exactly one is
  set. The product FK is `(tenant_id, workspace_id, product_dev_case_id)` to
  the product case, `ON DELETE CASCADE` like the PO one; UNIQUE
  `(product_dev_case_id, kind, episode)` is the episode key on that side and
  leads with the FK's own column, as the PO one does. The PO FK now carries
  the workspace too (same name), so no follow-up can sit in a workspace its
  case is not in.
- **`recipient_user_id`**: the PIC stamped on a follow-up when it opened, if
  the policy routes its kind to `pic` and the PIC was a member of the case's
  workspace then. NULL otherwise, and on every row from before this revision.
- **RLS narrows by workspace**, in the one shape `CLAUDE.md` allows (tenant
  AND (workspace OR the offboarding scope)). A product case is read only in its
  workspace, so its follow-up must be too; a PO case's follow-up is narrowed the
  same way, since its notice only ever went to that workspace's members. Every
  existing row already carries its case's workspace. The list index gains
  `workspace_id` after `tenant_id`, the columns RLS supplies.
- **`supply_chain.workspaces_with_cases()`** replaces `tenants_with_cases()`:
  SECURITY DEFINER, every `(tenant_id, workspace_id)` holding a PO case or a
  product case, ids only. The sweep now runs once per workspace, under that
  workspace's RLS, because product cases and follow-ups are read nowhere else.

Downgrade REFUSES while a follow-up names a product case, or any stored SLA
override uses `by_category`; otherwise it reverses each step, and turns 2.0
overrides back into 1.0 (`default` back to `sla`, `categories` dropped).
"""

from __future__ import annotations

import json

from alembic import op

revision = "d56da3dd2146"
down_revision = "84d1c1946b44"
branch_labels = None
depends_on = None

_TENANT = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
_WORKSPACE = "workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
_SCOPE = "current_setting('app.workspace_scope', true) = 'tenant'"
_POLICY = f"({_TENANT} AND ({_WORKSPACE} OR {_SCOPE}))"
# The tenant-only shape `dc2285c629d4` created, for the downgrade.
_OLD_POLICY = (
    "(tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid)"
)

# The 2.0.0 platform file's list, as shipped by this revision. A copy, on
# purpose: a migration is history, and the file may change after it.
_CATEGORIES = [{"key": "noi", "label": "Nồi"}, {"key": "chao", "label": "Chảo"}]

# Public to `test_sla_by_category.py`, which runs it over an override stored
# the way a tenant stored one before this revision.
UPGRADE_SLA_OVERRIDES = f"""
    UPDATE platform.policy_overrides
    SET content = (content - 'sla' - 'schema_version')
        || jsonb_build_object(
            'schema_version', '2.0',
            'default', content -> 'sla',
            'categories', '{json.dumps(_CATEGORIES, ensure_ascii=False)}'::jsonb
        )
    WHERE policy_id = 'supply_chain_sla'
      AND content ->> 'schema_version' = '1.0'
      AND jsonb_typeof(content -> 'sla') = 'object'
"""


def upgrade() -> None:
    op.execute(UPGRADE_SLA_OVERRIDES)

    op.execute(
        """
        ALTER TABLE supply_chain.follow_ups
            ALTER COLUMN po_case_id DROP NOT NULL,
            ADD COLUMN product_dev_case_id uuid,
            ADD COLUMN recipient_user_id uuid,
            ADD CONSTRAINT ck_follow_ups_one_case
                CHECK (num_nonnulls(po_case_id, product_dev_case_id) = 1),
            ADD CONSTRAINT uq_follow_ups_product_dev_case_id_kind_episode
                UNIQUE (product_dev_case_id, kind, episode),
            ADD CONSTRAINT fk_follow_ups_tenant_id_product_dev_cases
                FOREIGN KEY (tenant_id, workspace_id, product_dev_case_id)
                REFERENCES supply_chain.product_dev_cases (tenant_id, workspace_id, id)
                ON DELETE CASCADE,
            DROP CONSTRAINT fk_follow_ups_tenant_id_po_cases,
            ADD CONSTRAINT fk_follow_ups_tenant_id_po_cases
                FOREIGN KEY (tenant_id, workspace_id, po_case_id)
                REFERENCES supply_chain.po_cases (tenant_id, workspace_id, id)
                ON DELETE CASCADE
        """
    )
    op.execute("DROP INDEX supply_chain.ix_follow_ups_tenant_id_status_opened_at")
    op.execute(
        "CREATE INDEX ix_follow_ups_tenant_id_workspace_id_status_opened_at"
        " ON supply_chain.follow_ups (tenant_id, workspace_id, status, opened_at DESC)"
    )
    op.execute("DROP POLICY tenant_isolation_follow_ups ON supply_chain.follow_ups")
    op.execute(
        "CREATE POLICY tenant_isolation_follow_ups ON supply_chain.follow_ups"
        f" USING {_POLICY} WITH CHECK {_POLICY}"
    )

    op.execute(
        """
        CREATE FUNCTION supply_chain.workspaces_with_cases()
        RETURNS TABLE (tenant_id uuid, workspace_id uuid)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, supply_chain
        AS $$
            SELECT c.tenant_id, c.workspace_id FROM supply_chain.po_cases c
            UNION
            SELECT p.tenant_id, p.workspace_id FROM supply_chain.product_dev_cases p
            ORDER BY 1, 2
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION supply_chain.workspaces_with_cases() FROM PUBLIC")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                GRANT EXECUTE ON FUNCTION supply_chain.workspaces_with_cases() TO dw_app;
            END IF;
        END
        $$
        """
    )
    op.execute("DROP FUNCTION supply_chain.tenants_with_cases()")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM supply_chain.follow_ups WHERE product_dev_case_id IS NOT NULL
            ) OR EXISTS (
                SELECT 1 FROM platform.policy_overrides
                WHERE policy_id = 'supply_chain_sla'
                  AND jsonb_typeof(content -> 'by_category') = 'object'
                  AND content -> 'by_category' <> '{}'::jsonb
            ) THEN
                RAISE EXCEPTION
                    'follow-ups name product cases or SLA overrides use by_category;'
                    ' downgrading would drop them'
                    USING ERRCODE = 'check_violation';
            END IF;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION supply_chain.tenants_with_cases()
        RETURNS TABLE (tenant_id uuid, workspace_id uuid)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, supply_chain
        AS $$
            SELECT c.tenant_id, (array_agg(c.workspace_id ORDER BY c.workspace_id))[1]
            FROM supply_chain.po_cases c
            GROUP BY c.tenant_id
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION supply_chain.tenants_with_cases() FROM PUBLIC")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                GRANT EXECUTE ON FUNCTION supply_chain.tenants_with_cases() TO dw_app;
            END IF;
        END
        $$
        """
    )
    op.execute("DROP FUNCTION supply_chain.workspaces_with_cases()")

    op.execute("DROP POLICY tenant_isolation_follow_ups ON supply_chain.follow_ups")
    op.execute(
        "CREATE POLICY tenant_isolation_follow_ups ON supply_chain.follow_ups"
        f" USING {_OLD_POLICY} WITH CHECK {_OLD_POLICY}"
    )
    op.execute("DROP INDEX supply_chain.ix_follow_ups_tenant_id_workspace_id_status_opened_at")
    op.execute(
        "CREATE INDEX ix_follow_ups_tenant_id_status_opened_at"
        " ON supply_chain.follow_ups (tenant_id, status, opened_at DESC)"
    )
    op.execute(
        """
        ALTER TABLE supply_chain.follow_ups
            DROP CONSTRAINT fk_follow_ups_tenant_id_po_cases,
            ADD CONSTRAINT fk_follow_ups_tenant_id_po_cases
                FOREIGN KEY (tenant_id, po_case_id)
                REFERENCES supply_chain.po_cases (tenant_id, id) ON DELETE CASCADE,
            DROP CONSTRAINT fk_follow_ups_tenant_id_product_dev_cases,
            DROP CONSTRAINT uq_follow_ups_product_dev_case_id_kind_episode,
            DROP CONSTRAINT ck_follow_ups_one_case,
            DROP COLUMN recipient_user_id,
            DROP COLUMN product_dev_case_id,
            ALTER COLUMN po_case_id SET NOT NULL
        """
    )

    op.execute(
        """
        UPDATE platform.policy_overrides
        SET content = (content - 'default' - 'categories' - 'by_category' - 'schema_version')
            || jsonb_build_object('schema_version', '1.0', 'sla', content -> 'default')
        WHERE policy_id = 'supply_chain_sla' AND content ->> 'schema_version' = '2.0'
        """
    )
