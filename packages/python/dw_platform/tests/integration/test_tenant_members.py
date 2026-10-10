# ruff: noqa: F811  (`provisioner_engine` is a fixture imported from test_provisioning)
"""Integration: everyone in a tenant, roles per workspace, invitations.

`dw_app` under RLS, PostgreSQL compose (tenant-members-and-invitations 01).
TM1 (administrative roles kept, none handed out), TM2 (only the caller's
tenant), TM3 (support staff never members), TM4 (one email, one person), the
first sign-in of an invited person, and the Vietnamese name order.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from pg_harness import DatabaseUrls
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from support_world import ISSUER, SupportWorld, build_world, context_of, provisioning
from test_provisioning import provisioner_engine  # noqa: F401  (a fixture, used by name)

from dw_kernel.errors import ConflictError, NotFoundError, PermissionDeniedError
from dw_kernel.ports import FixedClock, Uuid4Generator
from dw_platform.adapters.persistence.directory import SqlWorkspaceDirectory
from dw_platform.adapters.persistence.identity_provisioning import SqlIdentityBootstrap
from dw_platform.adapters.persistence.tenant_members import SqlTenantMembersRepository
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.tenant_members import (
    InviteMember,
    SetMemberships,
    TenantMember,
    TenantMembersService,
    WorkspaceRoles,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)


@dataclass(frozen=True)
class _Identity:
    subject: str
    email: str | None
    issuer: str = ISSUER
    name: str | None = None
    auth_methods: frozenset[str] = frozenset()
    acr: str | None = None


@pytest.fixture
async def migrator(db_urls: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(db_urls.migrator, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def app_engine(db_urls: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def world(migrator: AsyncEngine) -> SupportWorld:
    return await build_world(migrator)


@pytest.fixture
def members(app_engine: AsyncEngine) -> TenantMembersService:
    return TenantMembersService(
        SqlTenantMembersRepository(async_sessionmaker(app_engine, expire_on_commit=False)),
        ScopeAuthorizationService(),
        FixedClock(NOW),
        Uuid4Generator(),
    )


async def _admin(app: AsyncEngine, w: SupportWorld) -> AccessContext:
    """`requester` holds `org_admin` in ws1 of tenant A."""
    return await context_of(app, w, "requester", w.tenant_a, w.ws1)


async def _roles(migrator: AsyncEngine, user: uuid.UUID) -> dict[uuid.UUID, set[str]]:
    async with migrator.connect() as conn:
        rows = (
            await conn.execute(
                sa.text(
                    "SELECT workspace_id, role_keys FROM platform.memberships WHERE user_id = :u"
                ),
                {"u": user},
            )
        ).all()
    return {row.workspace_id: set(row.role_keys) for row in rows}


async def _place(
    migrator: AsyncEngine, w: SupportWorld, user: uuid.UUID, ws: uuid.UUID, roles: list[str]
) -> None:
    async with migrator.begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO platform.memberships (id, tenant_id, workspace_id, user_id, role_keys)"
                " VALUES (:id, :t, :ws, :u, CAST(:r AS jsonb))"
            ),
            {
                "id": uuid.uuid4(),
                "t": w.tenant_a,
                "ws": ws,
                "u": user,
                "r": "[" + ", ".join(f'"{r}"' for r in roles) + "]",
            },
        )


async def _audit(migrator: AsyncEngine, user: uuid.UUID) -> list[tuple[str, uuid.UUID]]:
    async with migrator.connect() as conn:
        rows = (
            await conn.execute(
                sa.text(
                    "SELECT action, workspace_id FROM platform.audit_events"
                    " WHERE resource_type = 'membership' AND resource_id = :u"
                    " ORDER BY action, workspace_id"
                ),
                {"u": str(user)},
            )
        ).all()
    return [(row.action, row.workspace_id) for row in rows]


def _plain(w: SupportWorld) -> str:
    return f"probe_plain_{w.tag}"


# ---- TM2: only the caller's tenant ------------------------------------------


async def test_the_list_holds_this_tenant_only(
    world: SupportWorld, app_engine: AsyncEngine, members: TenantMembersService
) -> None:
    listed = {m.user_id: m for m in await members.list_members(await _admin(app_engine, world))}
    for name in ("granter", "granter2", "narrow", "requester", "plain", "granter_ws2"):
        assert world.users[name] in listed, name
    for name in ("b_granter", "c_granter", "staff", "not_staff"):
        assert world.users[name] not in listed, name
    ws2_member = listed[world.users["granter_ws2"]]
    assert [m.workspace_name for m in ws2_member.memberships] == ["Workspace 2"]
    plain = await context_of(app_engine, world, "plain", world.tenant_a, world.ws1)
    with pytest.raises(PermissionDeniedError):
        await members.list_members(plain)


async def test_another_tenants_person_or_workspace_is_not_found(
    world: SupportWorld,
    app_engine: AsyncEngine,
    members: TenantMembersService,
    migrator: AsyncEngine,
) -> None:
    admin = await _admin(app_engine, world)
    with pytest.raises(NotFoundError):
        await members.set_memberships(
            admin,
            SetMemberships(
                user_id=world.users["b_granter"],
                memberships=(WorkspaceRoles(world.ws1, frozenset({_plain(world)})),),
            ),
        )
    before = await _roles(migrator, world.users["plain"])
    with pytest.raises(NotFoundError):
        await members.set_memberships(
            admin,
            SetMemberships(
                user_id=world.users["plain"],
                memberships=(
                    WorkspaceRoles(world.ws1, frozenset({_plain(world)})),
                    WorkspaceRoles(world.ws_b, frozenset({_plain(world)})),
                ),
            ),
        )
    assert await _roles(migrator, world.users["plain"]) == before
    assert await _roles(migrator, world.users["b_granter"]) == {world.ws_b: {world.granter_role}}


# ---- TM1: administrative roles kept, never handed out ----------------------


async def test_replacing_roles_keeps_an_administrative_role(
    world: SupportWorld,
    app_engine: AsyncEngine,
    members: TenantMembersService,
    migrator: AsyncEngine,
) -> None:
    person = world.users["not_staff"]
    await _place(migrator, world, person, world.ws1, ["org_admin", _plain(world)])
    admin = await _admin(app_engine, world)
    await members.set_memberships(
        admin,
        SetMemberships(
            user_id=person,
            memberships=(WorkspaceRoles(world.ws1, frozenset({world.granter_role})),),
        ),
    )
    assert await _roles(migrator, person) == {world.ws1: {"org_admin", world.granter_role}}


async def test_an_org_admin_cannot_hand_out_an_administrative_role(
    world: SupportWorld,
    app_engine: AsyncEngine,
    members: TenantMembersService,
    migrator: AsyncEngine,
) -> None:
    person = world.users["plain"]
    before = await _roles(migrator, person)
    with pytest.raises(PermissionDeniedError):
        await members.set_memberships(
            await _admin(app_engine, world),
            SetMemberships(
                user_id=person,
                memberships=(
                    WorkspaceRoles(world.ws2, frozenset({_plain(world)})),
                    WorkspaceRoles(world.ws1, frozenset({"org_admin"})),
                ),
            ),
        )
    assert await _roles(migrator, person) == before


async def test_an_unknown_role_is_named(
    world: SupportWorld, app_engine: AsyncEngine, members: TenantMembersService
) -> None:
    with pytest.raises(NotFoundError) as refused:
        await members.set_memberships(
            await _admin(app_engine, world),
            SetMemberships(
                user_id=world.users["plain"],
                memberships=(WorkspaceRoles(world.ws1, frozenset({"no_such_role"})),),
            ),
        )
    assert refused.value.details["roles"] == ["no_such_role"]


async def test_a_workspace_left_out_is_removed_and_audited(
    world: SupportWorld,
    app_engine: AsyncEngine,
    members: TenantMembersService,
    migrator: AsyncEngine,
) -> None:
    person = world.users["not_staff"]
    await _place(migrator, world, person, world.ws1, [_plain(world)])
    await _place(migrator, world, person, world.ws2, [_plain(world)])
    changes = await members.set_memberships(
        await _admin(app_engine, world),
        SetMemberships(
            user_id=person,
            memberships=(WorkspaceRoles(world.ws1, frozenset({_plain(world)})),),
        ),
    )
    assert [(c.workspace_id, c.after) for c in changes] == [(world.ws2, frozenset())]
    assert await _roles(migrator, person) == {world.ws1: {_plain(world)}}
    assert await _audit(migrator, person) == [("platform.membership.revoke", world.ws2)]


# ---- invitations --------------------------------------------------------------


async def test_an_invited_person_signs_in_and_finds_their_workspace(
    world: SupportWorld,
    app_engine: AsyncEngine,
    members: TenantMembersService,
    migrator: AsyncEngine,
) -> None:
    admin = await _admin(app_engine, world)
    email = f"newcomer-{world.tag}@support.test"
    ref = await members.invite(
        admin,
        InviteMember(
            display_name="Người Mới",
            email=email.upper(),
            memberships=(WorkspaceRoles(world.ws1, frozenset({_plain(world)})),),
        ),
    )
    assert ref.email == email  # lower-cased
    listed = {m.user_id: m for m in await members.list_members(admin)}
    assert listed[ref.user_id].status == "invited"
    assert await _roles(migrator, ref.user_id) == {world.ws1: {_plain(world)}}
    assert await _audit(migrator, ref.user_id) == [("platform.member.invited", world.ws1)]
    directory = SqlWorkspaceDirectory(async_sessionmaker(app_engine, expire_on_commit=False))
    in_ws1 = {m.user_id: m.status for m in await directory.list_members(admin)}
    assert in_ws1[ref.user_id] == "invited"
    assert in_ws1[world.users["requester"]] == "invited"  # seeded without a sign-in

    # The first sign-in, from an IdP that verified the same email.
    view = await SqlIdentityBootstrap(
        session_factory=async_sessionmaker(app_engine, expire_on_commit=False),
        default_tenant_id=world.tenant_a,
        default_workspace_id=world.ws1,
    ).bootstrap(_Identity(subject=f"kc-{uuid.uuid4()}", email=email))
    assert view.principal_id == ref.user_id
    assert [m.workspace_id for m in view.memberships] == [world.ws1]
    listed = {m.user_id: m for m in await members.list_members(admin)}
    assert listed[ref.user_id].status == "active"
    assert {m.user_id: m.status for m in await directory.list_members(admin)}[
        ref.user_id
    ] == "active"


async def test_an_email_already_in_the_tenant_is_refused(
    world: SupportWorld, app_engine: AsyncEngine, members: TenantMembersService
) -> None:
    with pytest.raises(ConflictError) as refused:
        await members.invite(
            await _admin(app_engine, world),
            InviteMember(
                display_name="Someone",
                email=world.email("plain"),
                memberships=(WorkspaceRoles(world.ws2, frozenset({_plain(world)})),),
            ),
        )
    assert refused.value.details["reason_code"] == "member_email_exists_in_tenant"


async def test_two_invitations_of_one_new_email_make_one_person(
    world: SupportWorld,
    app_engine: AsyncEngine,
    members: TenantMembersService,
    migrator: AsyncEngine,
) -> None:
    admin = await _admin(app_engine, world)
    email = f"twice-{world.tag}@support.test"
    command = InviteMember(
        display_name="Twice Invited",
        email=email,
        memberships=(WorkspaceRoles(world.ws1, frozenset({_plain(world)})),),
    )
    outcomes = await asyncio.gather(
        members.invite(admin, command), members.invite(admin, command), return_exceptions=True
    )
    conflicts = [o for o in outcomes if isinstance(o, ConflictError)]
    made = [o for o in outcomes if not isinstance(o, BaseException)]
    assert len(made) == 1 and len(conflicts) == 1, outcomes
    async with migrator.connect() as conn:
        assert (
            await conn.scalar(
                sa.text("SELECT count(*) FROM platform.users WHERE lower(email) = :e"), {"e": email}
            )
            == 1
        )


# ---- TM3: support staff never members ------------------------------------------


async def test_support_staff_are_refused_on_every_member_path(
    world: SupportWorld,
    app_engine: AsyncEngine,
    members: TenantMembersService,
    migrator: AsyncEngine,
    provisioner_engine: AsyncEngine,
) -> None:
    # A membership held before the person was listed: kept, but frozen.
    await _place(migrator, world, world.users["staff"], world.ws1, [_plain(world)])
    await provisioning(provisioner_engine, FixedClock(NOW)).add_support_staff(
        world.operator, email=world.email("staff"), note=None
    )
    admin = await _admin(app_engine, world)
    with pytest.raises(ConflictError) as refused:
        await members.invite(
            admin,
            InviteMember(
                display_name="Support person",
                email=world.email("staff"),
                memberships=(WorkspaceRoles(world.ws2, frozenset({_plain(world)})),),
            ),
        )
    # Already a member here: that answer comes first.
    assert refused.value.details["reason_code"] == "member_email_exists_in_tenant"
    with pytest.raises(ConflictError) as refused:
        await members.set_memberships(
            admin,
            SetMemberships(
                user_id=world.users["staff"],
                memberships=(
                    WorkspaceRoles(world.ws1, frozenset({_plain(world)})),
                    WorkspaceRoles(world.ws2, frozenset({_plain(world)})),
                ),
            ),
        )
    assert refused.value.details["reason_code"] == "support_staff_not_member"
    assert await _roles(migrator, world.users["staff"]) == {world.ws1: {_plain(world)}}

    # Listed staff who is a member nowhere here.
    await provisioning(provisioner_engine, FixedClock(NOW)).add_support_staff(
        world.operator, email=world.email("other_staff"), note=None
    )
    with pytest.raises(ConflictError) as refused:
        await members.invite(
            admin,
            InviteMember(
                display_name="Other support",
                email=world.email("other_staff"),
                memberships=(WorkspaceRoles(world.ws1, frozenset({_plain(world)})),),
            ),
        )
    assert refused.value.details["reason_code"] == "support_staff_not_member"
    assert await _roles(migrator, world.users["other_staff"]) == {}


# ---- the order of names ------------------------------------------------------------


async def test_names_sort_the_vietnamese_way(
    world: SupportWorld,
    app_engine: AsyncEngine,
    members: TenantMembersService,
    migrator: AsyncEngine,
) -> None:
    names = ["Đạt Phùng", "Dũng Lê", "Zoe Ng", "Ánh Vũ", "An Trần", "Ê Bê"]
    for name in names:
        user = uuid.uuid4()
        async with migrator.begin() as conn:
            await conn.execute(
                sa.text(
                    "INSERT INTO platform.users (id, subject, display_name) VALUES (:id, :s, :n)"
                ),
                {"id": user, "s": f"vi-{user}", "n": name},
            )
        await _place(migrator, world, user, world.ws2, [_plain(world)])
    listed: list[TenantMember] = await members.list_members(await _admin(app_engine, world))
    order = [m.display_name for m in listed if m.display_name in names]
    assert order == ["An Trần", "Ánh Vũ", "Dũng Lê", "Đạt Phùng", "Ê Bê", "Zoe Ng"]
