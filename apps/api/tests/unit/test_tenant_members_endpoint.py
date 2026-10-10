"""The tenant member routes over the real service, the database faked honestly.

What the route owns: dropping every cached AccessContext of each workspace a
change touched, and replaying an `Idempotency-Key` without a second write or
audit. The rules themselves are integration-tested against PostgreSQL
(`dw_platform/tests/integration/test_tenant_members.py`).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import httpx
import pytest
from asgi_lifespan import LifespanManager
from test_idempotency_dependency import MemoryStore

from dw_api.bootstrap import ApiContainer
from dw_api.health import CheckState, HealthService
from dw_api.main import create_app
from dw_api.settings import ApiSettings
from dw_kernel.errors import NotFoundError
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.adapters.identity.dev_token import DevTokenVerifier
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.cache import membership_cache_pattern
from dw_platform.application.entitlement import DEFAULT_PLANS, PlanEntitlementService
from dw_platform.application.idempotency import HttpIdempotency
from dw_platform.application.identity import DbAccessContextFactory, MembershipAccess
from dw_platform.application.membership_admin import UserRef
from dw_platform.application.tenant_members import (
    MembershipChange,
    MemberWorkspace,
    Roles,
    TenantMember,
    TenantMembersService,
)
from dw_platform.domain.audit import AuditEvent

pytestmark = pytest.mark.unit

SECRET = "unit-test-secret-0123456789abcdef"
TENANT, WS1, WS2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
PRINCIPAL, PERSON = uuid.uuid4(), uuid.uuid4()
ROLES = {"org_admin": frozenset({"platform.members.write"}), "sales": frozenset({"crm.read"})}


class Lookup:
    async def find_access(
        self, subject: str, issuer: str, tenant_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> MembershipAccess | None:
        return MembershipAccess(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            principal_id=PRINCIPAL,
            roles=frozenset({"org_admin"}),
            scopes=frozenset({"platform.members.read", "platform.members.write"}),
            groups=frozenset(),
            clearance="internal",
            plan_id="professional",
            feature_flags=frozenset(),
        )


class Repo:
    """One person in this tenant, in ws1 and ws2, held in memory and changed
    the way the SQL repository changes them; every write audits."""

    def __init__(self) -> None:
        self.held: dict[uuid.UUID, frozenset[str]] = {
            WS1: frozenset({"sales"}),
            WS2: frozenset({"sales"}),
        }
        self.audits: list[AuditEvent] = []

    async def role_catalog(self) -> dict[str, frozenset[str]]:
        return dict(ROLES)

    async def list_members(self, context: AccessContext) -> list[TenantMember]:
        if not self.held:
            return []
        return [
            TenantMember(
                user_id=PERSON,
                display_name="Person",
                email="p@example.test",
                status="active",
                memberships=tuple(
                    MemberWorkspace(ws, "W", tuple(sorted(roles)))
                    for ws, roles in sorted(self.held.items(), key=lambda i: str(i[0]))
                ),
            )
        ]

    async def replace_memberships(
        self,
        context: AccessContext,
        *,
        user_id: uuid.UUID,
        plan: Callable[[Roles], Roles],
        audit: Callable[[MembershipChange], AuditEvent],
    ) -> list[MembershipChange]:
        if user_id != PERSON or not self.held:
            raise NotFoundError("no such member in this tenant")
        planned = plan(self.held)
        changes = [
            MembershipChange(ws, self.held.get(ws, frozenset()), roles)
            for ws, roles in planned.items()
            if roles != self.held.get(ws, frozenset())
        ]
        self.held = {ws: roles for ws, roles in planned.items() if roles}
        self.audits += [audit(change) for change in changes]
        return changes

    async def invite(
        self,
        context: AccessContext,
        *,
        display_name: str,
        email: str,
        memberships: Roles,
        audit: Callable[[uuid.UUID, uuid.UUID, frozenset[str]], AuditEvent],
    ) -> UserRef:
        raise NotImplementedError("not exercised by the route tests")


class Cache:
    """Honest about patterns: records what was dropped."""

    def __init__(self) -> None:
        self.dropped: list[str] = []

    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        return None

    async def delete_pattern(self, pattern: str) -> None:
        self.dropped.append(pattern)


def _container(repo: Repo, cache: Cache, store: MemoryStore) -> ApiContainer:
    async def ok() -> CheckState:
        return "ok"

    authz = ScopeAuthorizationService()
    return ApiContainer(
        settings=ApiSettings(profile="test", dev_secret=SECRET),
        engine=None,
        health_service=HealthService(probes={"database": ok}),
        token_verifier=DevTokenVerifier(SECRET),
        access_context_factory=DbAccessContextFactory(Lookup()),
        identity_bootstrap=None,
        uow_factory=None,
        authorization=authz,
        entitlement=PlanEntitlementService(DEFAULT_PLANS),
        cache=cache,
        idempotency=HttpIdempotency(store, SystemClock()),
        tenant_members=TenantMembersService(repo, authz, SystemClock(), Uuid4Generator()),
    )


async def _put(container: ApiContainer, body: object, key: str | None = None) -> httpx.Response:
    token = DevTokenVerifier(SECRET).issue("dev|admin", email="admin@example.test")
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": str(TENANT),
        "X-Workspace-Id": str(WS1),
    }
    if key:
        headers["Idempotency-Key"] = key
    app = create_app(container)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.put(
                f"/api/v1/admin/members/{PERSON}/memberships", json=body, headers=headers
            )


BODY = {"memberships": [{"workspace_id": str(WS1), "role_keys": ["sales"]}]}


async def test_dropping_a_workspace_drops_its_cached_contexts() -> None:
    repo, cache = Repo(), Cache()
    response = await _put(_container(repo, cache, MemoryStore()), BODY)
    assert response.status_code == 200, response.text
    assert [m["workspace_id"] for m in response.json()["memberships"]] == [str(WS1)]
    assert cache.dropped == [membership_cache_pattern(TENANT, WS2)]
    assert [a.action for a in repo.audits] == ["platform.membership.revoke"]


async def test_a_replayed_key_answers_the_same_and_audits_once() -> None:
    repo, cache, store = Repo(), Cache(), MemoryStore()
    container = _container(repo, cache, store)
    first = await _put(container, BODY, key="put-1")
    again = await _put(container, BODY, key="put-1")
    assert first.status_code == again.status_code == 200
    assert first.json() == again.json()
    assert len(repo.audits) == 1


async def test_an_unknown_body_field_is_refused() -> None:
    response = await _put(
        _container(Repo(), Cache(), MemoryStore()), {**BODY, "user_id": str(uuid.uuid4())}
    )
    assert response.status_code == 422
