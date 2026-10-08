# ruff: noqa: F811  (`provisioner_engine` is a fixture imported from test_provisioning)
"""Integration: the support grant lifecycle (support-access ticket 01, ADR 0024).

Customer side as `dw_app`, operator side as `dw_provisioner`, PostgreSQL
compose. The controls: SA1 (only `support.grant` grants; a request is never
active without one), SA2 (grant only what you hold), SA3 (the customer never
picks the person; only support staff are assigned), SA9 (support staff never
become members), SA11 (the flag), SA13 (tenant and workspace isolation), the
CHECKs and the status machine, and one audit event per command.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from pg_harness import DatabaseUrls
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from support_world import (
    ITEM,
    SET_KEY,
    SupportWorld,
    build_world,
    context_of,
    grant_service,
    provisioning,
)
from test_provisioning import provisioner_engine  # noqa: F401  (a fixture, used by name)

from dw_kernel.errors import (
    ConflictError,
    DomainError,
    NotFoundError,
    PermissionDeniedError,
)
from dw_kernel.ports import FixedClock, Uuid4Generator
from dw_platform.adapters.persistence.membership_admin import SqlMembershipAdminRepository
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.membership_admin import (
    MEMBERS_WRITE,
    GrantMembership,
    GrantMembershipHandler,
)
from dw_platform.application.support_access import (
    GrantStatus,
    RequestSupportGrant,
    SupportGrantService,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)


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
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def service(app_engine: AsyncEngine, clock: FixedClock) -> SupportGrantService:
    return grant_service(app_engine, clock)


def _workspace_request(reason: str = "the parser misread page 3") -> RequestSupportGrant:
    return RequestSupportGrant(
        scope_set_key=SET_KEY,
        resource_type="workspace",
        resource_id=None,
        reason=reason,
        duration_hours=72,
    )


async def _ctx(app: AsyncEngine, w: SupportWorld, name: str, ws: str = "ws1") -> AccessContext:
    tenant = {"ws1": w.tenant_a, "ws2": w.tenant_a, "ws_b": w.tenant_b, "ws_c": w.tenant_c}[ws]
    return await context_of(app, w, name, tenant, getattr(w, ws))


async def _row(migrator: AsyncEngine, grant_id: uuid.UUID) -> dict[str, object]:
    async with migrator.connect() as conn:
        row = (
            (
                await conn.execute(
                    sa.text("SELECT * FROM platform.support_grants WHERE id = :id"),
                    {"id": grant_id},
                )
            )
            .mappings()
            .one()
        )
    return dict(row)


async def _grant_count(migrator: AsyncEngine, tenant: uuid.UUID) -> int:
    async with migrator.connect() as conn:
        count = await conn.scalar(
            sa.text("SELECT count(*) FROM platform.support_grants WHERE tenant_id = :t"),
            {"t": tenant},
        )
    return int(count or 0)


async def _audit(migrator: AsyncEngine, grant_id: uuid.UUID) -> list[dict[str, object]]:
    async with migrator.connect() as conn:
        rows = (
            (
                await conn.execute(
                    sa.text(
                        "SELECT action, actor_id, details FROM platform.audit_events"
                        " WHERE resource_type = 'support_grant' AND resource_id = :id"
                        " ORDER BY occurred_at, action"
                    ),
                    {"id": str(grant_id)},
                )
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


async def _staff(provisioner_engine: AsyncEngine, w: SupportWorld, clock: FixedClock) -> None:
    ops = provisioning(provisioner_engine, clock)
    await ops.add_support_staff(w.operator, email=w.email("staff"), note="on call")


# ---- SA1: only support.grant grants ---------------------------------------


async def test_a_member_without_support_scopes_cannot_ask(
    world: SupportWorld,
    app_engine: AsyncEngine,
    service: SupportGrantService,
    migrator: AsyncEngine,
) -> None:
    plain = await _ctx(app_engine, world, "plain")
    with pytest.raises(PermissionDeniedError):
        await service.request(plain, _workspace_request())
    with pytest.raises(PermissionDeniedError):
        service.catalog_for(plain)
    assert await _grant_count(migrator, world.tenant_a) == 0


async def test_an_org_admin_only_requests_and_nothing_makes_it_active_unapproved(
    world: SupportWorld,
    app_engine: AsyncEngine,
    service: SupportGrantService,
    migrator: AsyncEngine,
    provisioner_engine: AsyncEngine,
    clock: FixedClock,
) -> None:
    requester = await _ctx(app_engine, world, "requester")
    view = await service.request(requester, _workspace_request())
    assert view.grant.status is GrantStatus.PENDING_APPROVAL
    assert view.grant.granted_by is None
    assert view.grant.code.startswith("SG-")

    # The operator cannot assign it: it was never granted.
    await _staff(provisioner_engine, world, clock)
    with pytest.raises(ConflictError):
        await provisioning(provisioner_engine, clock).assign_support_request(
            world.operator, grant_id=view.grant.id, staff_user_id=world.users["staff"]
        )
    # Not even the application's own credentials can jump it to active.
    async with app_engine.connect() as conn:
        await conn.execute(
            sa.text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(world.tenant_a)}
        )
        with pytest.raises(DBAPIError, match=r"permission denied|cannot go from"):
            await conn.execute(
                sa.text("UPDATE platform.support_grants SET status = 'active' WHERE id = :id"),
                {"id": view.grant.id},
            )
    # Nor the migrator, which owns the table: the status machine is the database's.
    async with migrator.connect() as conn:
        with pytest.raises(DBAPIError, match="cannot go from"):
            await conn.execute(
                sa.text(
                    "UPDATE platform.support_grants SET status = 'active',"
                    " staff_user_id = :s, activated_at = now(),"
                    " expires_at = now() + interval '72 hours' WHERE id = :id"
                ),
                {"id": view.grant.id, "s": world.users["staff"]},
            )
    assert (await _row(migrator, view.grant.id))["status"] == "pending_approval"


async def test_a_granter_grants_and_a_requester_sees_only_their_own(
    world: SupportWorld, app_engine: AsyncEngine, service: SupportGrantService
) -> None:
    granter = await _ctx(app_engine, world, "granter")
    requester = await _ctx(app_engine, world, "requester")
    granted = await service.request(granter, _workspace_request())
    asked = await service.request(requester, _workspace_request("please re-import"))
    assert granted.grant.status is GrantStatus.PENDING_ASSIGNMENT
    assert granted.grant.granted_by == world.users["granter"]
    assert granted.grant.scopes == frozenset({"x.read"})  # stamped
    assert granted.grant.resource_label == "Workspace 1"

    assert {v.grant.id for v in await service.list_grants(granter)} == {
        granted.grant.id,
        asked.grant.id,
    }
    assert [v.grant.id for v in await service.list_grants(requester)] == [asked.grant.id]


async def test_codes_count_per_tenant(
    world: SupportWorld, app_engine: AsyncEngine, service: SupportGrantService
) -> None:
    a = await _ctx(app_engine, world, "granter")
    b = await _ctx(app_engine, world, "b_granter", "ws_b")
    first = await service.request(a, _workspace_request())
    second = await service.request(a, _workspace_request())
    other = await service.request(b, _workspace_request())
    assert (first.grant.code, second.grant.code, other.grant.code) == (
        "SG-0001",
        "SG-0002",
        "SG-0001",
    )


async def test_a_resource_is_named_by_its_context_or_is_not_found(
    world: SupportWorld, app_engine: AsyncEngine, service: SupportGrantService
) -> None:
    granter = await _ctx(app_engine, world, "granter")
    view = await service.request(
        granter,
        RequestSupportGrant(
            scope_set_key=SET_KEY,
            resource_type="probe_item",
            resource_id=ITEM,
            reason="item 1",
            duration_hours=4,
        ),
    )
    assert view.grant.resource_label == "Probe item"
    with pytest.raises(NotFoundError):
        await service.request(
            granter,
            RequestSupportGrant(
                scope_set_key=SET_KEY,
                resource_type="probe_item",
                resource_id=uuid.uuid4(),
                reason="nope",
                duration_hours=4,
            ),
        )


async def test_an_unknown_scope_set_is_refused(
    world: SupportWorld, app_engine: AsyncEngine, service: SupportGrantService
) -> None:
    granter = await _ctx(app_engine, world, "granter")
    with pytest.raises(DomainError) as refused:
        await service.request(
            granter,
            RequestSupportGrant(
                scope_set_key="nope",
                resource_type="workspace",
                resource_id=None,
                reason="x",
                duration_hours=1,
            ),
        )
    assert refused.value.details["reason_code"] == "support_scope_set_unknown"


# ---- SA2: grant only what you hold ----------------------------------------


async def test_a_granter_missing_a_scope_of_the_set_grants_nothing(
    world: SupportWorld,
    app_engine: AsyncEngine,
    service: SupportGrantService,
    migrator: AsyncEngine,
) -> None:
    narrow = await _ctx(app_engine, world, "narrow")  # support.grant, no x.read
    with pytest.raises(PermissionDeniedError) as refused:
        await service.request(narrow, _workspace_request())
    assert refused.value.details["reason_code"] == "support_scope_not_held"
    assert refused.value.details["scopes"] == ["x.read"]
    assert await _grant_count(migrator, world.tenant_a) == 0

    # Approving someone else's request is held to the same rule.
    requester = await _ctx(app_engine, world, "requester")
    asked = await service.request(requester, _workspace_request())
    with pytest.raises(PermissionDeniedError) as refused:
        await service.approve(narrow, asked.grant.id)
    assert refused.value.details["reason_code"] == "support_scope_not_held"
    assert (await _row(migrator, asked.grant.id))["status"] == "pending_approval"


async def test_a_grant_goes_ineffective_when_the_granter_loses_the_role(
    world: SupportWorld,
    app_engine: AsyncEngine,
    service: SupportGrantService,
    migrator: AsyncEngine,
    provisioner_engine: AsyncEngine,
    clock: FixedClock,
) -> None:
    granter = await _ctx(app_engine, world, "granter")
    view = await service.request(granter, _workspace_request())
    await _staff(provisioner_engine, world, clock)
    await provisioning(provisioner_engine, clock).assign_support_request(
        world.operator, grant_id=view.grant.id, staff_user_id=world.users["staff"]
    )
    second = await _ctx(app_engine, world, "granter2")
    assert [v.state for v in await service.list_grants(second)] == ["active"]
    async with migrator.begin() as conn:
        await conn.execute(
            sa.text(
                "UPDATE platform.memberships SET role_keys = '[\"org_admin\"]'"
                " WHERE user_id = :u AND workspace_id = :ws"
            ),
            {"u": world.users["granter"], "ws": world.ws1},
        )
    assert [v.state for v in await service.list_grants(second)] == ["ineffective"]
    clock.advance_to(NOW + timedelta(hours=72))
    assert [v.state for v in await service.list_grants(second)] == ["expired"]


# ---- SA3: the customer never picks; only support staff are assigned -------


async def test_assignment_needs_support_staff_and_a_grant_waiting_for_one(
    world: SupportWorld,
    app_engine: AsyncEngine,
    service: SupportGrantService,
    migrator: AsyncEngine,
    provisioner_engine: AsyncEngine,
    clock: FixedClock,
) -> None:
    granter = await _ctx(app_engine, world, "granter")
    view = await service.request(granter, _workspace_request())
    ops = provisioning(provisioner_engine, clock)
    await _staff(provisioner_engine, world, clock)

    pending = [
        r for r in await ops.list_support_requests(world.operator) if r.grant_id == view.grant.id
    ]
    assert len(pending) == 1
    assert pending[0].tenant_name.startswith("Company ")
    assert pending[0].workspace_name == "Workspace 1"

    with pytest.raises(ConflictError) as refused:
        await ops.assign_support_request(
            world.operator, grant_id=view.grant.id, staff_user_id=world.users["not_staff"]
        )
    assert refused.value.details["reason_code"] == "support_staff_required"
    assert (await _row(migrator, view.grant.id))["status"] == "pending_assignment"

    assigned = await ops.assign_support_request(
        world.operator, grant_id=view.grant.id, staff_user_id=world.users["staff"]
    )
    assert assigned.status is GrantStatus.ACTIVE
    assert assigned.activated_at == NOW
    assert assigned.expires_at == NOW + timedelta(hours=72)
    assert not [
        r for r in await ops.list_support_requests(world.operator) if r.grant_id == view.grant.id
    ]

    with pytest.raises(ConflictError):  # already active
        await ops.assign_support_request(
            world.operator, grant_id=view.grant.id, staff_user_id=world.users["staff"]
        )

    requester = await _ctx(app_engine, world, "requester")
    asked = await service.request(requester, _workspace_request())
    await service.reject(granter, asked.grant.id, "not now")
    with pytest.raises(ConflictError):  # rejected
        await ops.assign_support_request(
            world.operator, grant_id=asked.grant.id, staff_user_id=world.users["staff"]
        )
    with pytest.raises(NotFoundError):
        await ops.assign_support_request(
            world.operator, grant_id=uuid.uuid4(), staff_user_id=world.users["staff"]
        )


# ---- SA9: support staff never become members ------------------------------


async def test_support_staff_cannot_be_made_a_member(
    world: SupportWorld,
    app_engine: AsyncEngine,
    provisioner_engine: AsyncEngine,
    clock: FixedClock,
) -> None:
    await _staff(provisioner_engine, world, clock)
    admin = AccessContext(
        tenant_id=world.tenant_a,
        workspace_id=world.ws1,
        principal_id=world.users["requester"],
        roles=frozenset({"org_admin"}),
        scopes=frozenset({MEMBERS_WRITE}),
        plan_id="professional",
    )
    handler = GrantMembershipHandler(
        SqlMembershipAdminRepository(async_sessionmaker(app_engine, expire_on_commit=False)),
        ScopeAuthorizationService(),
        clock,
        Uuid4Generator(),
    )
    roles = frozenset({f"probe_plain_{world.tag}"})
    with pytest.raises(ConflictError) as refused:
        await handler.handle(
            admin,
            GrantMembership(email=world.email("staff"), workspace_id=world.ws1, role_keys=roles),
        )
    assert refused.value.details["reason_code"] == "support_staff_not_member"
    # The operators' own path is held to it too.
    with pytest.raises(ConflictError) as refused:
        await provisioning(provisioner_engine, clock).assign_org_admin(
            world.operator, tenant_id=world.tenant_a, email=world.email("staff")
        )
    assert refused.value.details["reason_code"] == "support_staff_not_member"
    # Someone not listed is placed as before.
    await handler.handle(
        admin,
        GrantMembership(email=world.email("not_staff"), workspace_id=world.ws1, role_keys=roles),
    )


# ---- SA11: the flag ---------------------------------------------------------


async def test_a_tenant_without_the_flag_cannot_ask_until_it_is_on(
    world: SupportWorld,
    app_engine: AsyncEngine,
    service: SupportGrantService,
    migrator: AsyncEngine,
) -> None:
    c = await _ctx(app_engine, world, "c_granter", "ws_c")
    with pytest.raises(PermissionDeniedError) as refused:
        service.catalog_for(c)
    assert refused.value.details["reason_code"] == "support_access_not_enabled"
    for refused_call in (service.list_grants(c), service.request(c, _workspace_request())):
        with pytest.raises(PermissionDeniedError) as refused:
            await refused_call
        assert refused.value.details["reason_code"] == "support_access_not_enabled"
    assert await _grant_count(migrator, world.tenant_c) == 0

    async with migrator.begin() as conn:
        await conn.execute(
            sa.text(
                "UPDATE platform.entitlements SET feature_overrides = '[\"support_access\"]'"
                " WHERE tenant_id = :t"
            ),
            {"t": world.tenant_c},
        )
    c = await _ctx(app_engine, world, "c_granter", "ws_c")
    assert (
        await service.request(c, _workspace_request())
    ).grant.status is GrantStatus.PENDING_ASSIGNMENT


# ---- SA13: tenant and workspace isolation ----------------------------------


async def test_another_tenant_or_workspace_cannot_touch_a_grant(
    world: SupportWorld,
    app_engine: AsyncEngine,
    service: SupportGrantService,
    migrator: AsyncEngine,
) -> None:
    requester = await _ctx(app_engine, world, "requester")
    asked = await service.request(requester, _workspace_request())
    before_row = await _row(migrator, asked.grant.id)
    before_audit = await _audit(migrator, asked.grant.id)
    for name, ws in (("b_granter", "ws_b"), ("granter_ws2", "ws2")):
        intruder = await _ctx(app_engine, world, name, ws)
        assert asked.grant.id not in {v.grant.id for v in await service.list_grants(intruder)}
        for attempt in (
            service.approve(intruder, asked.grant.id),
            service.reject(intruder, asked.grant.id, "mine now"),
            service.revoke(intruder, asked.grant.id),
        ):
            with pytest.raises(NotFoundError):
                await attempt
    assert await _row(migrator, asked.grant.id) == before_row
    assert await _audit(migrator, asked.grant.id) == before_audit
    # A connection that names no tenant reads nothing.
    async with app_engine.connect() as conn:
        assert await conn.scalar(sa.text("SELECT count(*) FROM platform.support_grants")) == 0


# ---- the database's own refusals --------------------------------------------


async def test_the_table_refuses_what_no_status_allows(
    world: SupportWorld, migrator: AsyncEngine, app_engine: AsyncEngine
) -> None:
    base = {
        "t": world.tenant_a,
        "ws": world.ws1,
        "by": world.users["granter"],
    }
    insert = (
        "INSERT INTO platform.support_grants (id, tenant_id, workspace_id, resource_type,"
        " resource_label, scope_set_key, scope_set_label, scopes, reason, duration_hours,"
        " status, requested_by, granted_by, granted_at)"
        " VALUES (gen_random_uuid(), :t, :ws, 'workspace', 'W', 'k', 'K', ARRAY['x.read'],"
        " 'r', :hours, :status, :by, :by, now())"
    )
    async with migrator.connect() as conn:
        with pytest.raises(DBAPIError, match="never active"):
            await conn.execute(sa.text(insert), {**base, "hours": 72, "status": "active"})
        await conn.rollback()
        with pytest.raises(DBAPIError, match="ck_support_grants_duration"):
            await conn.execute(
                sa.text(insert), {**base, "hours": 337, "status": "pending_assignment"}
            )
        await conn.rollback()
        grant_id = (
            await conn.execute(
                sa.text(insert + " RETURNING id"),
                {**base, "hours": 72, "status": "pending_assignment"},
            )
        ).scalar_one()
        await conn.commit()
        # Active without an expiry: the CHECK, whoever writes it.
        with pytest.raises(DBAPIError, match="ck_support_grants_assigned"):
            await conn.execute(
                sa.text(
                    "UPDATE platform.support_grants SET status = 'active', staff_user_id = :s,"
                    " activated_at = now() WHERE id = :id"
                ),
                {"id": grant_id, "s": world.users["staff"]},
            )
        await conn.rollback()
    async with app_engine.connect() as conn:
        await conn.execute(
            sa.text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(world.tenant_a)}
        )
        with pytest.raises(DBAPIError, match="permission denied"):
            await conn.execute(
                sa.text("DELETE FROM platform.support_grants WHERE id = :id"), {"id": grant_id}
            )
        await conn.rollback()
        with pytest.raises(DBAPIError, match="permission denied"):
            await conn.execute(
                sa.text("UPDATE platform.support_grants SET staff_user_id = :s WHERE id = :id"),
                {"id": grant_id, "s": world.users["staff"]},
            )


# ---- audit ------------------------------------------------------------------


async def test_each_command_writes_one_event_without_the_reason(
    world: SupportWorld,
    app_engine: AsyncEngine,
    service: SupportGrantService,
    migrator: AsyncEngine,
    provisioner_engine: AsyncEngine,
    clock: FixedClock,
) -> None:
    granter = await _ctx(app_engine, world, "granter")
    requester = await _ctx(app_engine, world, "requester")
    secret = "ZZ-secret-reason-ZZ"

    asked = await service.request(requester, _workspace_request(secret))
    assert [e["action"] for e in await _audit(migrator, asked.grant.id)] == [
        "support.grant.requested"
    ]
    clock.advance_to(NOW + timedelta(minutes=1))
    await service.approve(granter, asked.grant.id)
    await _staff(provisioner_engine, world, clock)
    clock.advance_to(NOW + timedelta(minutes=2))
    await provisioning(provisioner_engine, clock).assign_support_request(
        world.operator, grant_id=asked.grant.id, staff_user_id=world.users["staff"]
    )
    clock.advance_to(NOW + timedelta(minutes=3))
    await service.revoke(granter, asked.grant.id)
    events = await _audit(migrator, asked.grant.id)
    assert [e["action"] for e in events] == [
        "support.grant.requested",
        "support.grant.approved",
        "support.grant.assigned",
        "support.grant.revoked",
    ]
    rejected = await service.request(requester, _workspace_request(secret))
    clock.advance_to(NOW + timedelta(minutes=4))
    await service.reject(granter, rejected.grant.id, secret)
    events += await _audit(migrator, rejected.grant.id)
    assert [e["action"] for e in events][-2:] == [
        "support.grant.requested",
        "support.grant.rejected",
    ]
    for event in events:
        details = event["details"]
        assert isinstance(details, dict)
        assert details["support_grant_id"] in {str(asked.grant.id), str(rejected.grant.id)}
        assert "reason" not in details
        assert secret not in str(details)
    assigned = next(e for e in events if e["action"] == "support.grant.assigned")
    assert assigned["actor_id"] == world.users["operator"]
    assert assigned["details"]["staff_user_id"] == str(world.users["staff"])  # type: ignore[index]

    # After revocation nothing can move it again.
    with pytest.raises(ConflictError):
        await service.revoke(granter, asked.grant.id)
