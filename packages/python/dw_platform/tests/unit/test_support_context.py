"""Unit: the support context a staff member gets from a customer's grant (ADR 0024).

SA4 (no role, the stamped scopes, tenant and workspace from the grant), SA5 (a
second factor), SA6 (expiry, revocation and a granter who lost the scope end it,
on every build and on a recheck), SA11 (the tenant's flag), and that a grant
assigned to someone else does not exist for the caller.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

import pytest

from dw_kernel.errors import NotFoundError, PermissionDeniedError
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.support_access import (
    SUPPORT_ACCESS_FEATURE,
    SUPPORT_GRANT_SCOPE,
    GrantStatus,
    SupportAccessContextFactory,
    SupportGrant,
    TenantPlan,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
STAFF = uuid.uuid4()
GRANTER = uuid.uuid4()
STAMPED = frozenset({"ctx.records.read"})


@dataclass
class Clock:
    now_value: datetime = NOW

    def now(self) -> datetime:
        return self.now_value


class Ids:
    def new_uuid(self) -> uuid.UUID:
        return uuid.uuid4()


def a_grant(**overrides: object) -> SupportGrant:
    grant = SupportGrant(
        id=uuid.uuid4(),
        code="SG-0001",
        tenant_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        resource_type="workspace",
        resource_id=None,
        resource_label="Main",
        scope_set_key="ctx.read",
        scope_set_label="Read",
        scopes=STAMPED,
        reason="r",
        duration_hours=4,
        status=GrantStatus.ACTIVE,
        requested_by=GRANTER,
        requested_at=NOW - timedelta(hours=1),
        granted_by=GRANTER,
        granted_at=NOW - timedelta(hours=1),
        staff_user_id=STAFF,
        activated_at=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(hours=3),
    )
    return replace(grant, **overrides)  # type: ignore[arg-type]


@dataclass
class World:
    """A fake honouring the ports: a grant exists only for the staff member it
    is assigned to, and the granter's scopes are whatever their membership holds."""

    grant: SupportGrant = field(default_factory=a_grant)
    staff: set[uuid.UUID] = field(default_factory=lambda: {STAFF})
    granter_scopes: frozenset[str] = frozenset({SUPPORT_GRANT_SCOPE}) | STAMPED
    flags: frozenset[str] = frozenset({SUPPORT_ACCESS_FEATURE})

    async def is_support_staff(self, user_id: uuid.UUID) -> bool:
        return user_id in self.staff

    async def grant_for_staff(
        self, staff_user_id: uuid.UUID, grant_id: uuid.UUID
    ) -> SupportGrant | None:
        g = self.grant
        return g if g.id == grant_id and g.staff_user_id == staff_user_id else None

    async def tenant_plan(self, tenant_id: uuid.UUID) -> TenantPlan | None:
        return TenantPlan(plan_id="professional", feature_flags=self.flags)

    async def scopes_of(
        self, tenant_id: uuid.UUID, workspace_id: uuid.UUID, user_id: uuid.UUID
    ) -> frozenset[str]:
        return self.granter_scopes if user_id == GRANTER else frozenset()


def factory(world: World, clock: Clock | None = None) -> SupportAccessContextFactory:
    return SupportAccessContextFactory(
        grants=world, member_scopes=world, clock=clock or Clock(), ids=Ids()
    )


async def build(
    world: World, *, methods: frozenset[str] = frozenset({"pwd", "otp"}), who: uuid.UUID = STAFF
) -> AccessContext:
    return await factory(world).build(
        principal_id=who, auth_methods=methods, grant_id=world.grant.id
    )


def reason(error: PermissionDeniedError) -> object:
    return error.details.get("reason_code")


async def test_sa4_no_role_the_stamped_scopes_and_the_grants_tenant() -> None:
    world = World()
    context = await build(world)
    assert context.roles == frozenset()
    assert context.scopes == STAMPED
    assert context.tenant_id == world.grant.tenant_id
    assert context.workspace_id == world.grant.workspace_id
    assert context.principal_id == STAFF
    assert context.support is not None and context.support.grant_id == world.grant.id
    # No role means no admin bypass: a scope outside the stamp is refused.
    authz = ScopeAuthorizationService()
    assert not authz.is_allowed(context, "approvals.decide")
    assert authz.is_allowed(context, "ctx.records.read")


async def test_only_support_staff() -> None:
    world = World(staff=set())
    with pytest.raises(PermissionDeniedError) as refused:
        await build(world)
    assert reason(refused.value) == "support_staff_required"


@pytest.mark.parametrize("methods", [frozenset(), frozenset({"pwd"})])
async def test_sa5_a_second_factor_is_required(methods: frozenset[str]) -> None:
    with pytest.raises(PermissionDeniedError) as refused:
        await build(World(), methods=methods)
    assert reason(refused.value) == "support_mfa_required"


async def test_a_grant_assigned_to_someone_else_does_not_exist() -> None:
    world = World(grant=a_grant(staff_user_id=uuid.uuid4()))
    with pytest.raises(NotFoundError):
        await build(world)


async def test_sa6_expired_exactly_at_expires_at() -> None:
    world = World()
    clock = Clock(world.grant.expires_at)  # type: ignore[arg-type]
    with pytest.raises(PermissionDeniedError) as refused:
        await factory(world, clock).build(
            principal_id=STAFF, auth_methods=frozenset({"otp"}), grant_id=world.grant.id
        )
    assert reason(refused.value) == "support_grant_ended"
    assert refused.value.details["ended_reason"] == "expired"


async def test_sa6_revoked_and_the_recheck_sees_it() -> None:
    world = World()
    context = await build(world)
    world.grant = replace(world.grant, status=GrantStatus.REVOKED, revoked_at=NOW)
    with pytest.raises(PermissionDeniedError) as refused:
        await factory(world).recheck(context)
    assert refused.value.details["ended_reason"] == "revoked"
    with pytest.raises(PermissionDeniedError):
        await build(world)


async def test_sa6_a_granter_who_lost_support_grant_ends_it() -> None:
    world = World(granter_scopes=STAMPED)
    with pytest.raises(PermissionDeniedError) as refused:
        await build(world)
    assert refused.value.details["ended_reason"] == "ineffective"


async def test_sa11_the_tenants_flag_turned_off_after_granting() -> None:
    world = World(flags=frozenset())
    with pytest.raises(PermissionDeniedError) as refused:
        await build(world)
    assert reason(refused.value) == "support_access_not_enabled"


async def test_the_access_event_names_the_grant_not_a_body() -> None:
    world = World()
    context = await build(world)
    event = factory(world).access_event(
        context, method="GET", route="/api/v1/x/{id}", path_ids={"id": "1"}
    )
    assert event.action == "support.access"
    assert event.details["support_grant_id"] == str(world.grant.id)
    assert set(event.details) == {"support_grant_id", "code", "method", "route", "path_ids"}
