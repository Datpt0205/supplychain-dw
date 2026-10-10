"""platform support staff and support grants

Revision ID: af8ee878b4ab
Revises: 983b509c3f0f
Create Date: 2026-10-08

Customer-granted support access (ADR 0024): the customer asks for or grants a
scoped, time-limited, revocable access; the operators pick who carries it;
the access is the staff member's own identity carrying only the scopes
stamped on the grant. This revision is the data and its lifecycle; building an
access context from a grant is a later revision's functions plus the API.

- **`platform.support_staff`**: who may ever be assigned a grant. Identity
  plane, no tenant (like `platform.platform_operators`). The provisioner
  writes it; `dw_app` only reads it (the membership guard below and, later,
  the support context).
- **`platform.support_grants`**: one row per grant, tenant-scoped, RLS
  forced. Stored statuses are `pending_approval`, `pending_assignment`,
  `active`, `rejected`, `revoked`; "expired" and "ineffective" are derived at
  read time by `grant_effective_state`, never stored. Each group of columns a
  step writes exists if and only if the status says that step happened, by
  CHECK on the step's timestamp: the `*_by` columns are `ON DELETE SET NULL`
  (the record outlives the person), so they cannot carry the "if and only
  if". `staff_user_id` is `ON DELETE RESTRICT`: a grant always names who
  holds it.
- **`platform.guard_support_grant()`**: every writer, migrator included, may
  only insert a grant as `pending_approval` or `pending_assignment`, and may
  only move it forward: approve (`pending_approval` → `pending_assignment`),
  reject, assign (`pending_assignment` → `active`), revoke (any of the first
  three → `revoked`). What a step decided never changes afterwards. So no
  path makes a request `active` without a grant first (SA1). The code
  (`SG-0001`, per tenant) is assigned here, under a per-tenant advisory lock,
  so concurrent requests never collide on `uq_support_grants_tenant_code`.
- **`platform.refuse_support_staff_membership()`**: a member of
  `support_staff` cannot be given, or have changed, a membership in any
  tenant (SA9, TM3), whichever path writes it — the org admin's grant, an
  invitation, the provisioner's org-admin assignment. A membership written
  before the person became staff stays (the support context never merges
  with it); changing its roles is refused.
- **Privileges.** `dw_app`: SELECT on both; INSERT on the customer's columns
  of a grant; UPDATE only on the columns the customer's own steps write
  (approve, reject, revoke); no DELETE (offboarding keeps grants with the
  audit log). `dw_provisioner`: SELECT, INSERT, DELETE on `support_staff`;
  SELECT on grants and UPDATE only of the assignment columns; INSERT on
  `platform.audit_events`, so the tenant's own audit trail records who
  assigned whom in the same transaction.
- **`org_admin` gains `support.request`.** `support.grant` is not given to
  any platform role: a context gives it to the role that owns the data.
- The `support_access` feature flag is in no plan; a tenant gets it through
  `platform.entitlements.feature_overrides`.
"""

from __future__ import annotations

from alembic import op

revision = "af8ee878b4ab"
down_revision = "983b509c3f0f"
branch_labels = None
depends_on = None

_TENANT_PREDICATE = (
    "(tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid)"
)

# The columns the customer's side writes, by step. The provisioner's step
# (assignment) writes the other group; nobody else writes anything.
_CUSTOMER_INSERT = (
    "id, tenant_id, workspace_id, resource_type, resource_id, resource_label,"
    " scope_set_key, scope_set_label, scopes, reason, duration_hours, status,"
    " requested_by, requested_at, granted_by, granted_at"
)
_CUSTOMER_UPDATE = (
    "status, granted_by, granted_at, rejected_by, rejected_at, reject_reason, revoked_by, revoked_at"
)
_ASSIGNMENT_UPDATE = "status, staff_user_id, assigned_by, activated_at, expires_at"

_STATUSES = "'pending_approval', 'pending_assignment', 'active', 'rejected', 'revoked'"


def _for_role(role: str, statement: str) -> str:
    return f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                {statement}
            END IF;
        END
        $$
        """


_GUARD = """
    CREATE OR REPLACE FUNCTION platform.guard_support_grant()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, platform
    AS $$
    DECLARE
        next_number integer;
    BEGIN
        IF TG_OP = 'INSERT' THEN
            IF NEW.status NOT IN ('pending_approval', 'pending_assignment') THEN
                RAISE EXCEPTION 'a support grant starts as a request or a grant, never %',
                    NEW.status
                    USING ERRCODE = 'check_violation',
                          CONSTRAINT = 'ck_support_grants_transition';
            END IF;
            -- One code sequence per tenant. The lock serialises concurrent
            -- requests of one tenant only; RLS (this runs as the caller)
            -- shows exactly that tenant's codes.
            PERFORM pg_advisory_xact_lock(hashtext('platform.support_grants'),
                                          hashtext(NEW.tenant_id::text));
            SELECT coalesce(max(substring(g.code FROM 4)::integer), 0) + 1
            INTO next_number
            FROM platform.support_grants g
            WHERE g.tenant_id = NEW.tenant_id;
            NEW.code := 'SG-' || CASE WHEN next_number < 10000
                THEN lpad(next_number::text, 4, '0') ELSE next_number::text END;
            RETURN NEW;
        END IF;

        -- What the request said never changes, whichever step this is.
        IF (NEW.id, NEW.code, NEW.tenant_id, NEW.workspace_id, NEW.resource_type,
            NEW.resource_id, NEW.resource_label, NEW.scope_set_key, NEW.scope_set_label,
            NEW.scopes, NEW.reason, NEW.duration_hours, NEW.requested_by, NEW.requested_at)
           IS DISTINCT FROM
           (OLD.id, OLD.code, OLD.tenant_id, OLD.workspace_id, OLD.resource_type,
            OLD.resource_id, OLD.resource_label, OLD.scope_set_key, OLD.scope_set_label,
            OLD.scopes, OLD.reason, OLD.duration_hours, OLD.requested_by, OLD.requested_at)
        THEN
            RAISE EXCEPTION 'what a support grant was requested for never changes'
                USING ERRCODE = 'check_violation',
                      CONSTRAINT = 'ck_support_grants_transition';
        END IF;

        IF (OLD.status, NEW.status) IN (
            ('pending_approval', 'pending_assignment'),
            ('pending_approval', 'rejected'),
            ('pending_approval', 'revoked'),
            ('pending_assignment', 'active'),
            ('pending_assignment', 'revoked'),
            ('active', 'revoked')
        ) THEN
            -- A step adds its own columns; the earlier steps' stay as they were.
            IF OLD.status <> 'pending_approval'
               AND (NEW.granted_by, NEW.granted_at)
                   IS DISTINCT FROM (OLD.granted_by, OLD.granted_at)
            THEN
                RAISE EXCEPTION 'who granted a support grant never changes'
                    USING ERRCODE = 'check_violation',
                          CONSTRAINT = 'ck_support_grants_transition';
            END IF;
            IF OLD.status = 'active'
               AND (NEW.staff_user_id, NEW.assigned_by, NEW.activated_at, NEW.expires_at)
                   IS DISTINCT FROM
                   (OLD.staff_user_id, OLD.assigned_by, OLD.activated_at, OLD.expires_at)
            THEN
                RAISE EXCEPTION 'who holds a support grant, and until when, never changes'
                    USING ERRCODE = 'check_violation',
                          CONSTRAINT = 'ck_support_grants_transition';
            END IF;
            RETURN NEW;
        END IF;

        RAISE EXCEPTION 'a support grant cannot go from % to %', OLD.status, NEW.status
            USING ERRCODE = 'check_violation',
                  CONSTRAINT = 'ck_support_grants_transition';
    END
    $$
"""

_STAFF_MEMBERSHIP = """
    CREATE OR REPLACE FUNCTION platform.refuse_support_staff_membership()
    RETURNS trigger
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, platform
    AS $$
    BEGIN
        IF EXISTS (SELECT 1 FROM platform.support_staff s WHERE s.user_id = NEW.user_id) THEN
            RAISE EXCEPTION 'a support staff member cannot be a member of a tenant'
                USING ERRCODE = 'check_violation',
                      CONSTRAINT = 'ck_memberships_not_support_staff';
        END IF;
        RETURN NEW;
    END
    $$
"""


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE platform.support_staff (
            user_id uuid NOT NULL,
            note text,
            added_by uuid,
            added_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_support_staff PRIMARY KEY (user_id),
            CONSTRAINT fk_support_staff_user_id_users FOREIGN KEY (user_id)
                REFERENCES platform.users (id) ON DELETE CASCADE,
            CONSTRAINT fk_support_staff_added_by_users FOREIGN KEY (added_by)
                REFERENCES platform.users (id) ON DELETE SET NULL,
            CONSTRAINT ck_support_staff_note CHECK (note IS NULL OR char_length(note) <= 200)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_support_staff_added_by ON platform.support_staff (added_by)"
        " WHERE added_by IS NOT NULL"
    )

    op.execute(
        f"""
        CREATE TABLE platform.support_grants (
            id uuid NOT NULL,
            code text NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            resource_type text NOT NULL,
            resource_id uuid,
            resource_label text NOT NULL,
            scope_set_key text NOT NULL,
            scope_set_label text NOT NULL,
            scopes text[] NOT NULL,
            reason text NOT NULL,
            duration_hours integer NOT NULL,
            status text NOT NULL,
            requested_by uuid,
            requested_at timestamp with time zone DEFAULT now() NOT NULL,
            granted_by uuid,
            granted_at timestamp with time zone,
            rejected_by uuid,
            rejected_at timestamp with time zone,
            reject_reason text,
            staff_user_id uuid,
            assigned_by uuid,
            activated_at timestamp with time zone,
            expires_at timestamp with time zone,
            revoked_by uuid,
            revoked_at timestamp with time zone,
            CONSTRAINT pk_support_grants PRIMARY KEY (id),
            CONSTRAINT uq_support_grants_tenant_code UNIQUE (tenant_id, code),
            CONSTRAINT fk_support_grants_tenant_id_tenants FOREIGN KEY (tenant_id)
                REFERENCES platform.tenants (id) ON DELETE CASCADE,
            CONSTRAINT fk_support_grants_workspace_id_workspaces FOREIGN KEY (workspace_id)
                REFERENCES platform.workspaces (id) ON DELETE CASCADE,
            CONSTRAINT fk_support_grants_requested_by_users FOREIGN KEY (requested_by)
                REFERENCES platform.users (id) ON DELETE SET NULL,
            CONSTRAINT fk_support_grants_granted_by_users FOREIGN KEY (granted_by)
                REFERENCES platform.users (id) ON DELETE SET NULL,
            CONSTRAINT fk_support_grants_rejected_by_users FOREIGN KEY (rejected_by)
                REFERENCES platform.users (id) ON DELETE SET NULL,
            CONSTRAINT fk_support_grants_staff_user_id_users FOREIGN KEY (staff_user_id)
                REFERENCES platform.users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_support_grants_assigned_by_users FOREIGN KEY (assigned_by)
                REFERENCES platform.users (id) ON DELETE SET NULL,
            CONSTRAINT fk_support_grants_revoked_by_users FOREIGN KEY (revoked_by)
                REFERENCES platform.users (id) ON DELETE SET NULL,
            CONSTRAINT ck_support_grants_status CHECK (status IN ({_STATUSES})),
            CONSTRAINT ck_support_grants_resource_type
                CHECK (resource_type ~ '^[a-z_]{{1,40}}$'),
            CONSTRAINT ck_support_grants_resource_id
                CHECK ((resource_id IS NULL) = (resource_type = 'workspace')),
            CONSTRAINT ck_support_grants_resource_label
                CHECK (char_length(btrim(resource_label)) BETWEEN 1 AND 200),
            CONSTRAINT ck_support_grants_scope_set CHECK (
                btrim(scope_set_key) <> '' AND btrim(scope_set_label) <> ''
            ),
            CONSTRAINT ck_support_grants_scopes CHECK (
                cardinality(scopes) > 0 AND array_position(scopes, NULL) IS NULL
            ),
            CONSTRAINT ck_support_grants_reason
                CHECK (char_length(btrim(reason)) BETWEEN 1 AND 300),
            CONSTRAINT ck_support_grants_duration CHECK (duration_hours BETWEEN 1 AND 336),
            -- Granted: always once past approval, never before; a revoked
            -- request may or may not have been granted.
            CONSTRAINT ck_support_grants_granted CHECK (
                CASE status
                    WHEN 'pending_assignment' THEN granted_at IS NOT NULL
                    WHEN 'active' THEN granted_at IS NOT NULL
                    WHEN 'revoked' THEN true
                    ELSE granted_at IS NULL AND granted_by IS NULL
                END
            ),
            CONSTRAINT ck_support_grants_rejected CHECK (
                (status = 'rejected') = (rejected_at IS NOT NULL)
                AND (status = 'rejected') = (reject_reason IS NOT NULL)
                AND (status = 'rejected' OR rejected_by IS NULL)
                AND (reject_reason IS NULL
                     OR char_length(btrim(reject_reason)) BETWEEN 1 AND 300)
            ),
            -- Assigned: always once active, never before; revoked either way.
            CONSTRAINT ck_support_grants_assigned CHECK (
                (activated_at IS NULL) = (expires_at IS NULL)
                AND (activated_at IS NULL) = (staff_user_id IS NULL)
                AND (activated_at IS NOT NULL OR assigned_by IS NULL)
                AND CASE status
                    WHEN 'active' THEN activated_at IS NOT NULL AND granted_at IS NOT NULL
                    WHEN 'revoked' THEN activated_at IS NULL OR granted_at IS NOT NULL
                    ELSE activated_at IS NULL
                END
                AND (expires_at IS NULL
                     OR expires_at = activated_at + make_interval(hours => duration_hours))
            ),
            CONSTRAINT ck_support_grants_revoked CHECK (
                (status = 'revoked') = (revoked_at IS NOT NULL)
                AND (status = 'revoked' OR revoked_by IS NULL)
            )
        )
        """
    )
    for statement in (
        # The customer's list, newest first; leads with what RLS supplies.
        "CREATE INDEX ix_support_grants_tenant_id_workspace_id_requested_at"
        " ON platform.support_grants (tenant_id, workspace_id, requested_at DESC, id)",
        "CREATE INDEX ix_support_grants_workspace_id ON platform.support_grants (workspace_id)",
        # The staff member's own grants, and the FK's index.
        "CREATE INDEX ix_support_grants_staff_user_id_status"
        " ON platform.support_grants (staff_user_id, status)",
        # The operators' queue, across tenants.
        "CREATE INDEX ix_support_grants_pending_assignment"
        " ON platform.support_grants (requested_at, id) WHERE status = 'pending_assignment'",
    ):
        op.execute(statement)
    for column in ("requested_by", "granted_by", "rejected_by", "assigned_by", "revoked_by"):
        op.execute(
            f"CREATE INDEX ix_support_grants_{column} ON platform.support_grants ({column})"
            f" WHERE {column} IS NOT NULL"
        )
    op.execute("ALTER TABLE platform.support_grants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE platform.support_grants FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_support_grants ON platform.support_grants"
        f" USING {_TENANT_PREDICATE} WITH CHECK {_TENANT_PREDICATE}"
    )

    op.execute(_GUARD)
    op.execute(
        """
        CREATE TRIGGER trg_support_grants_guard
        BEFORE INSERT OR UPDATE ON platform.support_grants
        FOR EACH ROW EXECUTE FUNCTION platform.guard_support_grant()
        """
    )
    op.execute(_STAFF_MEMBERSHIP)
    op.execute("REVOKE ALL ON FUNCTION platform.refuse_support_staff_membership() FROM PUBLIC")
    op.execute(
        """
        CREATE TRIGGER trg_memberships_not_support_staff
        BEFORE INSERT OR UPDATE OF user_id, role_keys, permission_set_keys
        ON platform.memberships
        FOR EACH ROW EXECUTE FUNCTION platform.refuse_support_staff_membership()
        """
    )

    # dw_app got SELECT, INSERT, UPDATE, DELETE on both from the default
    # privileges; narrow to what the customer's side does.
    op.execute(
        _for_role(
            "dw_app",
            "REVOKE ALL ON platform.support_staff FROM dw_app;"
            " GRANT SELECT ON platform.support_staff TO dw_app;"
            " REVOKE ALL ON platform.support_grants FROM dw_app;"
            " GRANT SELECT ON platform.support_grants TO dw_app;"
            f" GRANT INSERT ({_CUSTOMER_INSERT}) ON platform.support_grants TO dw_app;"
            f" GRANT UPDATE ({_CUSTOMER_UPDATE}) ON platform.support_grants TO dw_app;",
        )
    )
    op.execute(
        _for_role(
            "dw_provisioner",
            "GRANT SELECT, INSERT, DELETE ON platform.support_staff TO dw_provisioner;"
            " GRANT SELECT ON platform.support_grants TO dw_provisioner;"
            f" GRANT UPDATE ({_ASSIGNMENT_UPDATE}) ON platform.support_grants"
            " TO dw_provisioner;"
            " GRANT INSERT ON platform.audit_events TO dw_provisioner;",
        )
    )

    op.execute(
        "UPDATE platform.roles SET scopes = scopes || '[\"support.request\"]'::jsonb"
        " WHERE key = 'org_admin' AND NOT scopes ? 'support.request'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE platform.roles SET scopes = scopes - 'support.request' WHERE key = 'org_admin'"
    )
    op.execute(
        _for_role(
            "dw_provisioner",
            "REVOKE INSERT ON platform.audit_events FROM dw_provisioner;",
        )
    )
    op.execute("DROP TRIGGER IF EXISTS trg_memberships_not_support_staff ON platform.memberships")
    op.execute("DROP FUNCTION IF EXISTS platform.refuse_support_staff_membership()")
    op.execute("DROP TABLE IF EXISTS platform.support_grants")
    op.execute("DROP FUNCTION IF EXISTS platform.guard_support_grant()")
    op.execute("DROP TABLE IF EXISTS platform.support_staff")
