"""Importable test-seed helper: two tenants with workspaces, entitlements,
users, memberships, a reporting tree and the demo OIDC identities.

Ported from the former ``scripts/seed_demo.py`` so integration tests across the
workspace can populate a migrated test database in-process
(``await seed_test_env(db_urls.migrator)``) instead of shelling out to a script.

Platform rows only. Every suite in the workspace imports this, so a business
table named here would make all of them depend on one bounded context; a context
that needs demo records of its own seeds them from its own package.

What it deliberately does NOT seed: roles, permission sets and plans. That
config is owned by Alembic migration ``0143_platform_role_catalog`` — a migrated
database already has it, and a second source of truth here would drift.

Runs as the migrator role (BYPASSRLS maintenance role) via the database URL.
Deterministic UUIDs (uuid5) + ON CONFLICT upserts → re-running never creates
duplicates.
"""

from __future__ import annotations

import os
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import create_async_engine

from dw_platform.adapters.persistence import tables

SEED_NAMESPACE = uuid.UUID("6f0f6f1e-9f6a-4f65-9a1c-000000000d10")


def sid(kind: str, key: str) -> uuid.UUID:
    """Stable deterministic id for a seed entity."""
    return uuid.uuid5(SEED_NAMESPACE, f"{kind}:{key}")


# Demo assignment: Diệu (AM) gets approval authority without becoming a manager —
# the exact case Permission Sets exist for. The set itself (``approver_boost``)
# is defined by migration 0143; this only attaches it. (subject, [set keys],
# tenant_slug).
PERMISSION_SET_ASSIGNMENTS = [
    ("dev|dieu.hoang", ["approver_boost"], "tenant-alpha"),
]

# tenant-alpha is the first real tenant (FDX). Its slug/id stay "tenant-alpha"
# so DW_API_DEFAULT_TENANT_ID and every seeded reference keep resolving; only the
# display name changes. It carries the demo roster plus the real admin. The
# ``plan_id`` points the tenant's entitlement at a plan row migration 0143 owns.
TENANTS = [
    {"slug": "tenant-alpha", "name": "FDX", "plan_id": "professional"},
    {"slug": "tenant-beta", "name": "Công ty Beta", "plan_id": "basic"},
]

# Tenant Alpha carries one user per role so a tester can log in six times and see
# six different workbenches; Beta stays at two, which is all the cross-tenant
# isolation tests need.
USERS = [
    # (subject, email, display_name, tenant_slug, roles, department)
    (
        "dev|an.nguyen",
        "an.nguyen@alpha.local",
        "Nguyễn Văn An",
        "tenant-alpha",
        ["member"],
        "kinh-doanh",
    ),
    (
        "dev|binh.tran",
        "binh.tran@alpha.local",
        "Trần Thị Bình",
        "tenant-alpha",
        # Manager ONLY — separation of duties: the person who prepares/runs a
        # case must not also be the one who approves it.
        ["manager"],
        "mua-hang",
    ),
    (
        "dev|chi.le",
        "chi.le@alpha.local",
        "Lê Thị Chi",
        "tenant-alpha",
        ["platform_admin", "member"],
        "dieu-hanh",
    ),
    (
        "dev|dieu.hoang",
        "dieu.hoang@alpha.local",
        "Hoàng Thị Diệu",
        "tenant-alpha",
        ["member"],
        "kinh-doanh",
    ),
    (
        "dev|giang.do",
        "giang.do@alpha.local",
        "Đỗ Trường Giang",
        "tenant-alpha",
        ["director"],
        "kinh-doanh",
    ),
    (
        "dev|ha.vu",
        "ha.vu@alpha.local",
        "Vũ Thanh Hà",
        "tenant-alpha",
        ["executive"],
        "dieu-hanh",
    ),
    (
        "dev|bao.pham",
        "bao.pham@beta.local",
        "Phạm Quốc Bảo",
        "tenant-beta",
        ["member"],
        "kinh-doanh",
    ),
    (
        "dev|dung.vo",
        "dung.vo@beta.local",
        "Võ Thị Dung",
        "tenant-beta",
        ["manager"],
        "dieu-hanh",
    ),
]

# Demo reporting lines (ADR-003): a real three-level tree in Alpha so the org
# chart and the roll-up have something to show — Giang (director) over Bình
# (manager) over An and Diệu. (subject, manager_subject, tenant_slug).
HIERARCHY = [
    ("dev|an.nguyen", "dev|binh.tran", "tenant-alpha"),
    ("dev|dieu.hoang", "dev|binh.tran", "tenant-alpha"),
    ("dev|binh.tran", "dev|giang.do", "tenant-alpha"),
]

# Keycloak seeds the same Alpha users with fixed ids (infra/keycloak/dw-realm.json).
# The token 'sub' is that id and the 'iss' is the browser-facing issuer, so we map
# (issuer, keycloak_id) -> the existing platform user. Without this, an OIDC login
# would be treated as a brand-new identity and lose the seeded role.
# The issuer has one owner, DW_API_OIDC_ISSUER_URL — the value the API checks
# `iss` against. DW_KEYCLOAK_ISSUER stays only as an explicit override (CI sets
# it); a separate default here would stamp identities with an issuer no token
# carries once Keycloak listens on another port.
KEYCLOAK_ISSUER = (
    os.environ.get("DW_KEYCLOAK_ISSUER")
    or os.environ.get("DW_API_OIDC_ISSUER_URL")
    or "http://localhost:8686/realms/dw"
)
KEYCLOAK_IDENTITIES = [
    # (keycloak_user_id, platform_subject)
    ("a0000000-0000-4000-8000-0000000000a1", "dev|an.nguyen"),
    ("b0000000-0000-4000-8000-0000000000b2", "dev|binh.tran"),
    ("c0000000-0000-4000-8000-0000000000c3", "dev|chi.le"),
]


async def seed_test_env(database_url: str, *, restore_deleted: bool = True) -> dict[str, int]:
    """Populate a migrated database with tenants, users and memberships.

    ``restore_deleted`` is accepted and does nothing here. It cleared a
    soft-deleted demo account's tombstone, and accounts are a business table no
    platform seed owns; the parameter stays because callers pass it and because
    a context seeding its own records wants the same switch.
    """
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            for tenant in TENANTS:
                tenant_id = sid("tenant", tenant["slug"])
                stmt = pg_insert(tables.tenants).values(
                    id=tenant_id, slug=tenant["slug"], name=tenant["name"]
                )
                await conn.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["slug"], set_={"name": stmt.excluded.name}
                    )
                )

                workspace_id = sid("workspace", f"{tenant['slug']}:main")
                ws_stmt = pg_insert(tables.workspaces).values(
                    id=workspace_id, tenant_id=tenant_id, slug="main", name="Main workspace"
                )
                await conn.execute(
                    ws_stmt.on_conflict_do_update(
                        constraint="uq_workspaces_tenant_slug",
                        set_={"name": ws_stmt.excluded.name},
                    )
                )

                ent_stmt = pg_insert(tables.entitlements).values(
                    id=sid("entitlement", tenant["slug"]),
                    tenant_id=tenant_id,
                    plan_id=tenant["plan_id"],
                    feature_overrides=[],
                )
                await conn.execute(
                    ent_stmt.on_conflict_do_update(
                        index_elements=["tenant_id"],
                        set_={"plan_id": ent_stmt.excluded.plan_id},
                    )
                )

            for subject, email, display_name, tenant_slug, role_keys, department in USERS:
                user_stmt = pg_insert(tables.users).values(
                    id=sid("user", subject),
                    subject=subject,
                    email=email,
                    display_name=display_name,
                )
                await conn.execute(
                    user_stmt.on_conflict_do_update(
                        index_elements=["subject"],
                        set_={
                            "email": user_stmt.excluded.email,
                            "display_name": user_stmt.excluded.display_name,
                        },
                    )
                )
                # Read the id back rather than reusing the one just computed.
                # The conflict is on `subject`, so if this person already exists
                # the row keeps whatever id it was created with - and first-login
                # provisioning creates them with a random one. Assuming the
                # derived id then pointed the membership below at a user that was
                # never inserted, and the whole seed died on a foreign key. One
                # person signing in before the first seed was enough to do it.
                user_id = (
                    await conn.execute(
                        sa.select(tables.users.c.id).where(tables.users.c.subject == subject)
                    )
                ).scalar_one()

                membership_stmt = pg_insert(tables.memberships).values(
                    id=sid("membership", f"{tenant_slug}:{subject}"),
                    tenant_id=sid("tenant", tenant_slug),
                    workspace_id=sid("workspace", f"{tenant_slug}:main"),
                    user_id=user_id,
                    role_keys=role_keys,
                    department=department,
                )
                await conn.execute(
                    membership_stmt.on_conflict_do_update(
                        constraint="uq_memberships_scope_user",
                        set_={
                            "role_keys": membership_stmt.excluded.role_keys,
                            "department": membership_stmt.excluded.department,
                        },
                    )
                )

            # ADR-003: point each member at their manager. By subject, not the
            # derived id, because a person who signed in before the seed keeps a
            # random id (same reason the membership loop reads the id back).
            for subject, manager_subject, tenant_slug in HIERARCHY:
                await conn.execute(
                    sa.text(
                        "UPDATE platform.memberships SET manager_user_id = "
                        "(SELECT id FROM platform.users WHERE subject = :mgr) "
                        "WHERE workspace_id = :ws "
                        "AND user_id = (SELECT id FROM platform.users WHERE subject = :sub)"
                    ),
                    {
                        "mgr": manager_subject,
                        "sub": subject,
                        "ws": str(sid("workspace", f"{tenant_slug}:main")),
                    },
                )

            # ADR-001 Phase 3: attach demo permission sets to a member.
            for subject, set_keys, tenant_slug in PERMISSION_SET_ASSIGNMENTS:
                user_id_subq = (
                    sa.select(tables.users.c.id)
                    .where(tables.users.c.subject == subject)
                    .scalar_subquery()
                )
                await conn.execute(
                    sa.update(tables.memberships)
                    .where(
                        tables.memberships.c.workspace_id
                        == sid("workspace", f"{tenant_slug}:main"),
                        tables.memberships.c.user_id == user_id_subq,
                    )
                    .values(permission_set_keys=set_keys)
                )

            # ADR-002: make the demo super-admin a Platform Operator too, so the
            # /platform provisioning UI is reachable in the demo. A human may hold
            # both a data membership and operator authority; the two authorities
            # never combine in one request (operator endpoints carry no tenant).
            operator_stmt = pg_insert(tables.platform_operators).values(
                user_id=sid("user", "dev|chi.le"),
                note="demo super admin",
                created_by=sid("user", "dev|chi.le"),
            )
            await conn.execute(operator_stmt.on_conflict_do_nothing(index_elements=["user_id"]))

            for kc_id, subject in KEYCLOAK_IDENTITIES:
                ext_stmt = pg_insert(tables.external_identities).values(
                    id=sid("extid", f"{KEYCLOAK_ISSUER}:{kc_id}"),
                    user_id=sid("user", subject),
                    issuer=KEYCLOAK_ISSUER,
                    subject=kc_id,
                    provider="keycloak",
                )
                await conn.execute(
                    # Conflict on the primary key `id`, not the (issuer, subject)
                    # unique. `id` is derived from (issuer, kc_id) here, so a
                    # re-seed reuses the same id; if a stray row already holds
                    # that id under a different (issuer, subject) — a legacy
                    # seed, an issuer that has since changed — the (issuer,
                    # subject) target never fires and the insert dies on the id
                    # PK (external_identities_pkey). Keying on id re-asserts the
                    # demo identity's fields and stays idempotent whatever the
                    # stray row held.
                    ext_stmt.on_conflict_do_update(
                        index_elements=["id"],
                        set_={
                            "user_id": ext_stmt.excluded.user_id,
                            "issuer": ext_stmt.excluded.issuer,
                            "subject": ext_stmt.excluded.subject,
                            "provider": ext_stmt.excluded.provider,
                        },
                    )
                )

        async with engine.connect() as conn:
            counts: dict[str, int] = {}
            for name, table in {
                "tenants": tables.tenants,
                "workspaces": tables.workspaces,
                "users": tables.users,
                "memberships": tables.memberships,
                "entitlements": tables.entitlements,
                "external_identities": tables.external_identities,
            }.items():
                counts[name] = (
                    await conn.execute(sa.select(sa.func.count()).select_from(table))
                ).scalar_one()
        return counts
    finally:
        await engine.dispose()
