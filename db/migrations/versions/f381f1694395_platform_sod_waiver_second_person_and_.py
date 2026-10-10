"""platform sod waiver second person and role scope recheck

Revision ID: f381f1694395
Revises: ecb47f78702c
Create Date: 2026-10-08

Two holes in separation of duties (6b26771e549d, b9862fa13a80), both closed by
the database rather than by a service, the way the rest of SoD is.

**A waiver needs a second person.** One org admin could waive a rule and then
hold both sides of it: the control that exists to need two people was lifted by
one. A waiver is now a proposal until a different holder of
`platform.sod_waivers.write` confirms it:

- `confirmed_by`, `confirmed_at`, `confirm_reason` on `platform.sod_waivers`,
  all three set together, `confirmed_by <> granted_by`
  (`ck_sod_waivers_second_person`), a non-blank reason.
- `platform.sod_violation` honours only a CONFIRMED open waiver.
- The guard trigger allows exactly two updates of an open waiver: confirming
  it once, and revoking it once (a proposal may be withdrawn by revoking). An
  insert may not arrive confirmed.
- Open waivers that exist when this runs were granted by one person. They stay
  open and unconfirmed: from now on they lift nothing for a NEW membership
  write until a second admin confirms them (fail closed). Memberships already
  holding both sides are left as they are; the in-use guard on revoking still
  counts them.

**A role's scopes cannot change under a membership.** SoD was checked when a
membership was written, never when a role or permission set it holds gained a
scope. `platform.roles` and `platform.permission_sets` change only in
migrations (648e2f7c3edb), so a migration could widen a role past a rule and
every membership holding it would break the rule silently. An AFTER UPDATE OF
scopes trigger on both tables now refuses the change while any membership
holding that role or set would break a rule its tenant has not waived (and
confirmed). It names the rule and lists up to 20 membership ids in DETAIL
(`ck_roles_sod_memberships`): the migration fails, and its author fixes the
memberships or the change first. No HTTP path writes either table today; one
that does translates the constraint to a 409, as the waiver path does.

Idempotent: columns and constraints are added only if missing, functions are
replaced, triggers dropped if present and recreated, so a product carrying the
same change lands on the same state.
"""

from __future__ import annotations

from alembic import op

revision = "f381f1694395"
down_revision = "ecb47f78702c"
branch_labels = None
depends_on = None

_VIOLATION = """
    CREATE OR REPLACE FUNCTION platform.sod_violation(
        p_tenant_id uuid, p_role_keys jsonb, p_permission_set_keys jsonb
    )
    RETURNS TABLE (rule_key text, rule_description text)
    LANGUAGE plpgsql
    VOLATILE
    SECURITY DEFINER
    SET search_path = pg_catalog, platform
    AS $$
    #variable_conflict use_column
    DECLARE
        candidate record;
    BEGIN
        FOR candidate IN
            SELECT s.key, s.description, s.waivable FROM platform.sod_rules s ORDER BY s.key
        LOOP
            CONTINUE WHEN NOT platform.sod_rule_broken(
                candidate.key, p_role_keys, p_permission_set_keys
            );
            IF candidate.waivable AND p_tenant_id IS NOT NULL THEN
                -- FOR SHARE: a concurrent revoke of this waiver waits for
                -- the write relying on it, and then sees that write.
                PERFORM 1
                FROM platform.sod_waivers w
                WHERE w.tenant_id = p_tenant_id
                  AND w.rule_key = candidate.key
                  AND w.revoked_at IS NULL
                  {confirmed}
                FOR SHARE;
                CONTINUE WHEN FOUND;
            END IF;
            rule_key := candidate.key;
            rule_description := candidate.description;
            RETURN NEXT;
            RETURN;
        END LOOP;
    END
    $$
"""

_GUARD = """
    CREATE OR REPLACE FUNCTION platform.guard_sod_waiver()
    RETURNS trigger
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, platform
    AS $$
    BEGIN
        IF TG_OP = 'INSERT' THEN
            IF NOT EXISTS (
                SELECT 1 FROM platform.sod_rules WHERE key = NEW.rule_key AND waivable
            ) THEN
                RAISE EXCEPTION 'rule % cannot be waived', NEW.rule_key
                    USING ERRCODE = 'check_violation',
                          CONSTRAINT = 'ck_sod_waivers_rule_waivable';
            END IF;
            IF NEW.confirmed_at IS NOT NULL THEN
                RAISE EXCEPTION 'a waiver is confirmed by a second person, never on creation'
                    USING ERRCODE = 'check_violation',
                          CONSTRAINT = 'ck_sod_waivers_second_person';
            END IF;
            RETURN NEW;
        END IF;
        -- What was decided never changes, whichever update this is.
        IF OLD.revoked_at IS NOT NULL
           OR (NEW.id, NEW.tenant_id, NEW.rule_key, NEW.reason, NEW.granted_by,
               NEW.granted_at)
              IS DISTINCT FROM
              (OLD.id, OLD.tenant_id, OLD.rule_key, OLD.reason, OLD.granted_by,
               OLD.granted_at)
        THEN
            RAISE EXCEPTION 'a waiver can only be confirmed, once, and revoked, once'
                USING ERRCODE = 'check_violation',
                      CONSTRAINT = 'ck_sod_waivers_revoke_only';
        END IF;
        -- Confirming: the confirmation appears, nothing else moves.
        IF OLD.confirmed_at IS NULL AND NEW.confirmed_at IS NOT NULL
           AND NEW.revoked_at IS NULL
        THEN
            RETURN NEW;
        END IF;
        -- Revoking: the revocation appears, the confirmation (or its absence) stays.
        IF NEW.revoked_at IS NOT NULL
           AND (NEW.confirmed_at, NEW.confirmed_by, NEW.confirm_reason)
               IS NOT DISTINCT FROM (OLD.confirmed_at, OLD.confirmed_by, OLD.confirm_reason)
        THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'a waiver can only be confirmed, once, and revoked, once'
            USING ERRCODE = 'check_violation',
                  CONSTRAINT = 'ck_sod_waivers_revoke_only';
    END
    $$
"""

_SCOPE_CHANGE = """
    CREATE OR REPLACE FUNCTION platform.refuse_scope_change_breaking_memberships()
    RETURNS trigger
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, platform
    AS $$
    DECLARE
        holder_column text := CASE TG_TABLE_NAME
            WHEN 'roles' THEN 'role_keys' ELSE 'permission_set_keys' END;
        broken_rule text;
        broken integer;
        listed text;
    BEGIN
        -- The row already carries its new scopes (AFTER), so `sod_violation`
        -- answers for the catalogue as it will be.
        WITH holders AS (
            SELECT m.id, v.rule_key
            FROM platform.memberships m
            CROSS JOIN LATERAL platform.sod_violation(
                m.tenant_id, m.role_keys, m.permission_set_keys
            ) AS v
            WHERE CASE holder_column
                WHEN 'role_keys' THEN m.role_keys ? NEW.key
                ELSE m.permission_set_keys ? NEW.key
            END
        )
        SELECT min(rule_key), count(*),
               string_agg(id::text, ', ' ORDER BY id) FILTER (WHERE rn <= 20)
        INTO broken_rule, broken, listed
        FROM (SELECT h.*, row_number() OVER (ORDER BY h.id) AS rn FROM holders h) ranked;
        IF broken > 0 THEN
            RAISE EXCEPTION
                'changing the scopes of % would break % for % membership(s)',
                NEW.key, broken_rule, broken
                USING ERRCODE = 'check_violation',
                      CONSTRAINT = 'ck_roles_sod_memberships',
                      DETAIL = listed;
        END IF;
        RETURN NULL;
    END
    $$
"""


def _add_constraint_once(name: str, definition: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = '{name}'
                  AND conrelid = 'platform.sod_waivers'::regclass
            ) THEN
                ALTER TABLE platform.sod_waivers ADD CONSTRAINT {name} {definition};
            END IF;
        END
        $$
        """
    )


def upgrade() -> None:
    op.execute(
        "ALTER TABLE platform.sod_waivers"
        " ADD COLUMN IF NOT EXISTS confirmed_by uuid,"
        " ADD COLUMN IF NOT EXISTS confirmed_at timestamp with time zone,"
        " ADD COLUMN IF NOT EXISTS confirm_reason text"
    )
    _add_constraint_once(
        "ck_sod_waivers_second_person",
        "CHECK ("
        " (confirmed_at IS NULL) = (confirmed_by IS NULL)"
        " AND (confirmed_at IS NULL) = (confirm_reason IS NULL)"
        " AND (confirm_reason IS NULL OR btrim(confirm_reason) <> '')"
        " AND (confirmed_by IS NULL OR confirmed_by <> granted_by))",
    )
    op.execute(_VIOLATION.replace("{confirmed}", "AND w.confirmed_at IS NOT NULL"))
    op.execute(_GUARD)
    op.execute(_SCOPE_CHANGE)
    for table in ("roles", "permission_sets"):
        trigger = f"trg_{table}_sod_memberships"
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON platform.{table}")
        op.execute(
            f"""
            CREATE TRIGGER {trigger}
            AFTER UPDATE OF scopes ON platform.{table}
            FOR EACH ROW
            WHEN (OLD.scopes IS DISTINCT FROM NEW.scopes)
            EXECUTE FUNCTION platform.refuse_scope_change_breaking_memberships()
            """
        )


def downgrade() -> None:
    for table in ("roles", "permission_sets"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_sod_memberships ON platform.{table}")
    op.execute("DROP FUNCTION IF EXISTS platform.refuse_scope_change_breaking_memberships()")
    op.execute(_VIOLATION.replace("{confirmed}", ""))
    # The guard of 6b26771e549d, which never heard of confirmation.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION platform.guard_sod_waiver()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM platform.sod_rules WHERE key = NEW.rule_key AND waivable
                ) THEN
                    RAISE EXCEPTION 'rule % cannot be waived', NEW.rule_key
                        USING ERRCODE = 'check_violation',
                              CONSTRAINT = 'ck_sod_waivers_rule_waivable';
                END IF;
                RETURN NEW;
            END IF;
            IF OLD.revoked_at IS NOT NULL
               OR NEW.revoked_at IS NULL
               OR (NEW.id, NEW.tenant_id, NEW.rule_key, NEW.reason, NEW.granted_by,
                   NEW.granted_at)
                  IS DISTINCT FROM
                  (OLD.id, OLD.tenant_id, OLD.rule_key, OLD.reason, OLD.granted_by,
                   OLD.granted_at)
            THEN
                RAISE EXCEPTION 'a waiver can only be revoked, once'
                    USING ERRCODE = 'check_violation',
                          CONSTRAINT = 'ck_sod_waivers_revoke_only';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "ALTER TABLE platform.sod_waivers DROP CONSTRAINT IF EXISTS ck_sod_waivers_second_person"
    )
    op.execute(
        "ALTER TABLE platform.sod_waivers"
        " DROP COLUMN IF EXISTS confirm_reason,"
        " DROP COLUMN IF EXISTS confirmed_at,"
        " DROP COLUMN IF EXISTS confirmed_by"
    )
