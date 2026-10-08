"""Integration: an Org Admin grants and revokes membership, under RLS.

The vertical slice of ADR-001's "admin adds membership": a person signs in
(default-deny, no access), an Org Admin grants them a business role in their
tenant, and it takes. The three rails are exercised — scope, tenant, and the
no-escalation rule — plus the audit trail every change leaves.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from pg_harness import DatabaseUrls
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dw_kernel.errors import NotFoundError, PermissionDeniedError
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.identity_provisioning import SqlIdentityBootstrap
from dw_platform.adapters.persistence.membership_admin import SqlMembershipAdminRepository
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.membership_admin import (
    MEMBERS_READ,
    MEMBERS_WRITE,
    GrantMembership,
    GrantMembershipHandler,
    RevokeMembership,
    RevokeMembershipHandler,
)
from dw_platform.testing.seed_env import seed_test_env

pytestmark = pytest.mark.integration

# Seeded tenant-alpha / its workspace (uuid5, matches dw_platform.testing.seed_env).
ALPHA = uuid.UUID("d6b43d0e-c3c6-5dbc-bc08-150621bd9a5d")
ALPHA_WS = uuid.UUID("64764894-718d-5558-ba17-9a2949214063")
ORG_ADMIN_SCOPES = frozenset({MEMBERS_READ, MEMBERS_WRITE, "directory.read"})


@pytest.fixture
async def app_engine(db_urls: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    await seed_test_env(db_urls.migrator)
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def migrator_engine(db_urls: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(db_urls.migrator, poolclass=NullPool)
    yield engine
    await engine.dispose()


@dataclass(frozen=True)
class _Identity:
    subject: str
    email: str | None
    issuer: str = "https://issuer.test/realms/dw"
    name: str | None = None
    auth_methods: frozenset[str] = frozenset()
    acr: str | None = None


def _admin_context(tenant: uuid.UUID = ALPHA, workspace: uuid.UUID = ALPHA_WS) -> AccessContext:
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset({"org_admin"}),
        scopes=ORG_ADMIN_SCOPES,
        plan_id="professional",
    )


async def _sign_in(engine: AsyncEngine, email: str) -> uuid.UUID:
    """A first login: identity created, default-deny (no membership)."""
    bootstrap = SqlIdentityBootstrap(
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        default_tenant_id=ALPHA,
        default_workspace_id=ALPHA_WS,
    )
    view = await bootstrap.bootstrap(_Identity(subject=f"sub-{uuid.uuid4()}", email=email))
    assert view.memberships == ()
    return view.principal_id


def _handlers(engine: AsyncEngine) -> tuple[GrantMembershipHandler, RevokeMembershipHandler]:
    repo = SqlMembershipAdminRepository(async_sessionmaker(engine, expire_on_commit=False))
    authz = ScopeAuthorizationService()
    grant = GrantMembershipHandler(repo, authz, SystemClock(), Uuid4Generator())
    revoke = RevokeMembershipHandler(repo, authz, SystemClock(), Uuid4Generator())
    return grant, revoke


async def _audit_actions(engine: AsyncEngine, resource_id: uuid.UUID) -> list[str]:
    """Membership audit rows for one target user, oldest first."""
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await session.execute(
            sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(ALPHA)}
        )
        rows = (
            await session.execute(
                sa.select(tables.audit_events.c.action)
                .where(
                    tables.audit_events.c.resource_type == "membership",
                    tables.audit_events.c.resource_id == str(resource_id),
                )
                .order_by(tables.audit_events.c.occurred_at)
            )
        ).all()
    return [r.action for r in rows]


async def _memberships_of(
    engine: AsyncEngine, user_id: uuid.UUID
) -> list[tuple[uuid.UUID, list[str]]]:
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await session.execute(
            sa.text("SELECT set_config('app.principal_id', :p, true)"), {"p": str(user_id)}
        )
        rows = (
            await session.execute(
                sa.select(tables.memberships.c.tenant_id, tables.memberships.c.role_keys).where(
                    tables.memberships.c.user_id == user_id
                )
            )
        ).all()
    return [(r.tenant_id, r.role_keys) for r in rows]


async def test_org_admin_grants_a_business_role_and_it_is_audited(app_engine: AsyncEngine) -> None:
    user_id = await _sign_in(app_engine, "newhire@fpt.com")
    grant, _revoke = _handlers(app_engine)

    ref = await grant.handle(
        _admin_context(),
        GrantMembership(
            email="newhire@fpt.com", workspace_id=ALPHA_WS, role_keys=frozenset({"member"})
        ),
    )

    assert ref.user_id == user_id
    assert await _memberships_of(app_engine, user_id) == [(ALPHA, ["member"])]
    assert await _audit_actions(app_engine, user_id) == ["platform.membership.grant"]


async def test_a_plain_member_cannot_grant(app_engine: AsyncEngine) -> None:
    await _sign_in(app_engine, "target@fpt.com")
    grant, _revoke = _handlers(app_engine)
    not_admin = AccessContext(
        tenant_id=ALPHA,
        workspace_id=ALPHA_WS,
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=frozenset({"crm.account.read"}),
        plan_id="professional",
    )

    with pytest.raises(PermissionDeniedError):
        await grant.handle(
            not_admin,
            GrantMembership(
                email="target@fpt.com", workspace_id=ALPHA_WS, role_keys=frozenset({"member"})
            ),
        )


async def test_org_admin_cannot_mint_an_administrative_role(app_engine: AsyncEngine) -> None:
    """No escalation: an Org Admin may not hand out platform_admin/org_admin."""
    await _sign_in(app_engine, "crony@fpt.com")
    grant, _revoke = _handlers(app_engine)

    for elevated in ("platform_admin", "org_admin"):
        with pytest.raises(PermissionDeniedError, match="administrative"):
            await grant.handle(
                _admin_context(),
                GrantMembership(
                    email="crony@fpt.com",
                    workspace_id=ALPHA_WS,
                    role_keys=frozenset({elevated}),
                ),
            )


async def test_grant_into_another_tenants_workspace_is_refused(
    app_engine: AsyncEngine, migrator_engine: AsyncEngine
) -> None:
    """Tenant rail: an admin of alpha cannot place someone in beta's workspace."""
    user_id = await _sign_in(app_engine, "outsider@fpt.com")
    beta, beta_ws = uuid.uuid4(), uuid.uuid4()
    async with migrator_engine.begin() as conn:
        await conn.execute(
            sa.insert(tables.tenants).values(id=beta, slug=f"beta-{beta.hex[:8]}", name="BETA")
        )
        await conn.execute(
            sa.insert(tables.workspaces).values(
                id=beta_ws, tenant_id=beta, slug="beta-sales", name="Sales"
            )
        )
    grant, _revoke = _handlers(app_engine)

    with pytest.raises(NotFoundError, match="workspace"):
        await grant.handle(
            _admin_context(),  # admin of alpha
            GrantMembership(
                email="outsider@fpt.com", workspace_id=beta_ws, role_keys=frozenset({"member"})
            ),
        )
    assert await _memberships_of(app_engine, user_id) == []


async def test_revoke_removes_access(app_engine: AsyncEngine) -> None:
    user_id = await _sign_in(app_engine, "leaver@fpt.com")
    grant, revoke = _handlers(app_engine)
    await grant.handle(
        _admin_context(),
        GrantMembership(
            email="leaver@fpt.com", workspace_id=ALPHA_WS, role_keys=frozenset({"member"})
        ),
    )

    await revoke.handle(_admin_context(), RevokeMembership(user_id=user_id, workspace_id=ALPHA_WS))

    assert await _memberships_of(app_engine, user_id) == []
    assert await _audit_actions(app_engine, user_id) == [
        "platform.membership.grant",
        "platform.membership.revoke",
    ]
