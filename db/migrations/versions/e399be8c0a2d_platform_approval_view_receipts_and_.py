"""platform approval view receipts and decision codes

Revision ID: e399be8c0a2d
Revises: 5a25154e0296
Create Date: 2026-10-07 16:06:27.013816+00:00

A decision on Zalo after a view on the portal (channels Z5, ADR 0007). Three changes:

`platform.approval_view_receipts` — one row each time a person opens an
approval on the portal: who, which approval, the approval's `version` and the
subject's version (read through `ApprovalSubjectVersionPort`; NULL when no
context answers for the type) at that moment. A child of the approval, bounded
by it and gone with it (`ON DELETE CASCADE`); not partitioned.

- **Composite FK to the approval**, `(tenant_id, workspace_id, approval_id)`
  against the UNIQUE added to `approval_requests` here, so a receipt can only
  name an approval of its own tenant and workspace.
- **Workspace RLS**, ENABLEd and FORCEd, the one shape `CLAUDE.md` allows.
- `dw_app`: SELECT and INSERT only. A receipt is a fact, never edited.

`platform.approval_decision_codes` — the single-use code a view issued.

- **Only the HMAC is stored**, `code_hash` (HMAC-SHA256 under the server key
  `DW_APPROVAL_CODE_SECRET`, over approval id, user id and the six digits): a
  plain hash of six digits is reversed by a million tries.
- **Bound to the receipt** by a composite FK `(receipt_id, tenant_id,
  workspace_id, approval_id, user_id)`, so a code always names the view, the
  approval and the person it was issued to, and goes with them by cascade.
- **Bound to the comment** typed on the portal when it was issued : the decision records exactly that comment.
- **One open code per person per approval**: a partial UNIQUE over
  `(tenant_id, approval_id, user_id) WHERE used_at IS NULL AND revoked_at IS
  NULL`. Issuing a new one revokes the old one (`reissued`) in the same
  transaction.
- **Five wrong tries lock the code** (`failed_attempts`, CHECK 0..5; reaching 5
  sets `revoked_reason = 'locked'`, and a CHECK refuses a row at 5 that is not
  revoked).
- **Read by its owner across tenants** through `approval_decision_codes_self_select`
  (FOR SELECT, `app.principal_id`), the baseline's mechanism for "mine before a
  tenant is known" (`memberships_self_select`). The bot binds the principal from
  the chat's link row; every write — consuming, counting a wrong try, revoking —
  runs under the tenant and workspace policy, bound from the code's own row.
- `dw_app`: SELECT, INSERT, and UPDATE of `used_at`, `revoked_at`,
  `revoked_reason` and `failed_attempts` only. No DELETE: rows a day old go
  through `platform.prune_approval_decision_codes()` (SECURITY DEFINER, the
  worker's retention lane), or by cascade with their receipt.

`platform.approval_decisions.channel` — `web` or `zalo` (CHECK), default `web`:
every row before this revision was decided on the web.

**Twin.** This change came back from the first product, which had already
shipped it as its own revision `dbb8c3359981` (same names for every table,
constraint, index, policy and function, same grants). A product that merges
this platform carries both, on two branches alembic may run in either order,
so every statement here is idempotent: tables and indexes are created only if
missing, a policy only if none of that name exists on its table, functions and
triggers are created or replaced with the same body, and ENABLE/FORCE and the
REVOKE/GRANT pairs land on the same state however often they run. Downgrade is
a no-op while the twin is still applied, because the objects are then the
twin's too; whichever of the pair is downgraded last removes them. Where the
twin does not exist (this repository) the check never matches.
"""

from __future__ import annotations

from alembic import context, op
from alembic.script import ScriptDirectory

revision = "e399be8c0a2d"
down_revision = "5a25154e0296"
branch_labels = None
depends_on = None

# The product revision that shipped the same change first (see docstring).
_TWIN = "dbb8c3359981"

_TENANT = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
_WORKSPACE = "workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
_SCOPE = "current_setting('app.workspace_scope', true) = 'tenant'"
_POLICY = f"({_TENANT} AND ({_WORKSPACE} OR {_SCOPE}))"
_PRINCIPAL = "user_id = NULLIF(current_setting('app.principal_id', true), '')::uuid"


def _twin_applied() -> bool:
    script = ScriptDirectory.from_config(context.config)
    heads = op.get_context().get_current_heads()
    return any(
        rev.revision == _TWIN for head in heads for rev in script.iterate_revisions(head, "base")
    )


def _policy(table: str, name: str, ddl: str) -> str:
    """CREATE POLICY has no IF NOT EXISTS; the twin may have made it already."""
    return f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE schemaname = 'platform' AND tablename = '{table}' AND policyname = '{name}'
            ) THEN
                {ddl};
            END IF;
        END
        $$
        """


def _constraint(name: str, ddl: str) -> str:
    """ADD CONSTRAINT has no IF NOT EXISTS either."""
    return f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint c
                JOIN pg_namespace n ON n.oid = c.connamespace
                WHERE n.nspname = 'platform' AND c.conname = '{name}'
            ) THEN
                {ddl};
            END IF;
        END
        $$
        """


def upgrade() -> None:
    op.execute(
        _constraint(
            "uq_approval_requests_tenant_id_workspace_id_id",
            "ALTER TABLE platform.approval_requests ADD CONSTRAINT"
            " uq_approval_requests_tenant_id_workspace_id_id UNIQUE (tenant_id, workspace_id, id)",
        )
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.approval_view_receipts (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            approval_id uuid NOT NULL,
            user_id uuid NOT NULL,
            approval_version integer NOT NULL,
            subject_version text,
            viewed_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_approval_view_receipts PRIMARY KEY (id),
            CONSTRAINT uq_approval_view_receipts_binding
                UNIQUE (id, tenant_id, workspace_id, approval_id, user_id),
            CONSTRAINT fk_approval_view_receipts_tenant_id_approval_requests
                FOREIGN KEY (tenant_id, workspace_id, approval_id)
                REFERENCES platform.approval_requests (tenant_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT fk_approval_view_receipts_user_id_users FOREIGN KEY (user_id)
                REFERENCES platform.users (id) ON DELETE CASCADE,
            CONSTRAINT ck_approval_view_receipts_approval_version CHECK (approval_version >= 1),
            CONSTRAINT ck_approval_view_receipts_subject_version CHECK (
                subject_version IS NULL OR char_length(subject_version) BETWEEN 1 AND 64
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_approval_view_receipts_tenant_id_workspace_id_approval_id"
        " ON platform.approval_view_receipts (tenant_id, workspace_id, approval_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_approval_view_receipts_user_id ON platform.approval_view_receipts (user_id)"
    )
    op.execute("ALTER TABLE platform.approval_view_receipts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE platform.approval_view_receipts FORCE ROW LEVEL SECURITY")
    op.execute(
        _policy(
            "approval_view_receipts",
            "tenant_isolation_approval_view_receipts",
            "CREATE POLICY tenant_isolation_approval_view_receipts ON platform.approval_view_receipts"
            f" USING {_POLICY} WITH CHECK {_POLICY}",
        )
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.approval_decision_codes (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            approval_id uuid NOT NULL,
            user_id uuid NOT NULL,
            receipt_id uuid NOT NULL,
            code_hash bytea NOT NULL,
            comment text DEFAULT '' NOT NULL,
            failed_attempts integer DEFAULT 0 NOT NULL,
            expires_at timestamp with time zone NOT NULL,
            used_at timestamp with time zone,
            revoked_at timestamp with time zone,
            revoked_reason text,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_approval_decision_codes PRIMARY KEY (id),
            CONSTRAINT fk_approval_decision_codes_receipt_id_approval_view_receipts
                FOREIGN KEY (receipt_id, tenant_id, workspace_id, approval_id, user_id)
                REFERENCES platform.approval_view_receipts
                    (id, tenant_id, workspace_id, approval_id, user_id)
                ON DELETE CASCADE,
            CONSTRAINT ck_approval_decision_codes_code_hash CHECK (octet_length(code_hash) = 32),
            CONSTRAINT ck_approval_decision_codes_comment CHECK (char_length(comment) <= 2000),
            CONSTRAINT ck_approval_decision_codes_failed_attempts CHECK (
                failed_attempts BETWEEN 0 AND 5
            ),
            CONSTRAINT ck_approval_decision_codes_locked CHECK (
                failed_attempts < 5 OR revoked_reason IS NOT DISTINCT FROM 'locked'
            ),
            CONSTRAINT ck_approval_decision_codes_expires_at CHECK (expires_at > created_at),
            CONSTRAINT ck_approval_decision_codes_used_or_revoked CHECK (
                used_at IS NULL OR revoked_at IS NULL
            ),
            CONSTRAINT ck_approval_decision_codes_revoked_reason CHECK (
                (revoked_at IS NULL) = (revoked_reason IS NULL)
                AND (revoked_reason IS NULL OR revoked_reason IN ('reissued', 'locked'))
            )
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_approval_decision_codes_open"
        " ON platform.approval_decision_codes (tenant_id, approval_id, user_id)"
        " WHERE used_at IS NULL AND revoked_at IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_approval_decision_codes_receipt_id"
        " ON platform.approval_decision_codes"
        " (receipt_id, tenant_id, workspace_id, approval_id, user_id)"
    )
    # The bot's read: a person's codes across tenants, newest first.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_approval_decision_codes_user_id"
        " ON platform.approval_decision_codes (user_id, created_at)"
    )
    # The pruning sweep deletes by age.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_approval_decision_codes_created_at"
        " ON platform.approval_decision_codes (created_at)"
    )
    op.execute("ALTER TABLE platform.approval_decision_codes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE platform.approval_decision_codes FORCE ROW LEVEL SECURITY")
    op.execute(
        _policy(
            "approval_decision_codes",
            "tenant_isolation_approval_decision_codes",
            "CREATE POLICY tenant_isolation_approval_decision_codes ON platform.approval_decision_codes"
            f" USING {_POLICY} WITH CHECK {_POLICY}",
        )
    )
    op.execute(
        _policy(
            "approval_decision_codes",
            "approval_decision_codes_self_select",
            "CREATE POLICY approval_decision_codes_self_select ON platform.approval_decision_codes"
            f" FOR SELECT USING ({_PRINCIPAL})",
        )
    )

    op.execute(
        "ALTER TABLE platform.approval_decisions"
        " ADD COLUMN IF NOT EXISTS channel text DEFAULT 'web' NOT NULL"
    )
    op.execute(
        _constraint(
            "ck_approval_decisions_channel",
            "ALTER TABLE platform.approval_decisions"
            " ADD CONSTRAINT ck_approval_decisions_channel CHECK (channel IN ('web', 'zalo'))",
        )
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION platform.prune_approval_decision_codes()
        RETURNS integer
        LANGUAGE sql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
            WITH gone AS (
                DELETE FROM platform.approval_decision_codes
                WHERE created_at < now() - interval '1 day'
                RETURNING 1
            )
            SELECT count(*)::integer FROM gone
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION platform.prune_approval_decision_codes() FROM PUBLIC")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                REVOKE ALL ON platform.approval_view_receipts FROM dw_app;
                GRANT SELECT, INSERT ON platform.approval_view_receipts TO dw_app;
                REVOKE ALL ON platform.approval_decision_codes FROM dw_app;
                GRANT SELECT, INSERT ON platform.approval_decision_codes TO dw_app;
                GRANT UPDATE (used_at, revoked_at, revoked_reason, failed_attempts)
                    ON platform.approval_decision_codes TO dw_app;
                GRANT EXECUTE ON FUNCTION platform.prune_approval_decision_codes() TO dw_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    if _twin_applied():
        return
    op.execute("DROP FUNCTION platform.prune_approval_decision_codes()")
    op.execute(
        "ALTER TABLE platform.approval_decisions"
        " DROP CONSTRAINT ck_approval_decisions_channel, DROP COLUMN channel"
    )
    op.execute("DROP TABLE platform.approval_decision_codes")
    op.execute("DROP TABLE platform.approval_view_receipts")
    op.execute(
        "ALTER TABLE platform.approval_requests"
        " DROP CONSTRAINT uq_approval_requests_tenant_id_workspace_id_id"
    )
