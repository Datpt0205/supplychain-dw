"""The customer's support routes over the real service, the database faked.

What the route itself owns: the body schema (a staff member cannot be named),
the refusal shape the web branches on (`details.reason_code`), and that an
empty catalog — `main` registers no scope set — answers `[]` and refuses a
request as an unknown set.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager

from dw_api.bootstrap import ApiContainer
from dw_api.health import CheckState, HealthService
from dw_api.main import create_app
from dw_api.settings import ApiSettings
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.adapters.identity.dev_token import DevTokenVerifier
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.entitlement import DEFAULT_PLANS, PlanEntitlementService
from dw_platform.application.identity import DbAccessContextFactory, MembershipAccess
from dw_platform.application.support_access import (
    AuditFor,
    GrantChange,
    GrantStatus,
    NewSupportGrant,
    SupportGrant,
    SupportGrantService,
    SupportScopeCatalog,
)

pytestmark = pytest.mark.unit

SECRET = "unit-test-secret-0123456789abcdef"
TENANT = uuid.uuid4()
WORKSPACE = uuid.uuid4()
PRINCIPAL = uuid.uuid4()


class FakeLookup:
    def __init__(self, scopes: frozenset[str], flags: frozenset[str]) -> None:
        self.scopes, self.flags = scopes, flags

    async def find_access(
        self, subject: str, issuer: str, tenant_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> MembershipAccess | None:
        return MembershipAccess(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            principal_id=PRINCIPAL,
            roles=frozenset({"org_admin"}),
            scopes=self.scopes,
            groups=frozenset(),
            clearance="internal",
            plan_id="professional",
            feature_flags=self.flags,
        )


class FakeRepo:
    """Holds nothing: these tests never reach a write. A call that would
    write says so instead of pretending it succeeded."""

    async def workspace_name(self, context: AccessContext) -> str | None:
        return "Main"

    async def create(
        self, context: AccessContext, grant: NewSupportGrant, audit: AuditFor
    ) -> SupportGrant:
        raise NotImplementedError("not exercised by the route tests")

    async def get(
        self, context: AccessContext, grant_id: uuid.UUID, *, requested_by: uuid.UUID | None = None
    ) -> SupportGrant | None:
        return None

    async def list_grants(
        self, context: AccessContext, *, requested_by: uuid.UUID | None = None
    ) -> list[SupportGrant]:
        return []

    async def change(
        self,
        context: AccessContext,
        grant_id: uuid.UUID,
        *,
        expected: frozenset[GrantStatus],
        change: GrantChange,
        audit: AuditFor,
    ) -> SupportGrant | None:
        raise NotImplementedError("not exercised by the route tests")


class NoScopes:
    async def scopes_of(
        self, tenant_id: uuid.UUID, workspace_id: uuid.UUID, user_id: uuid.UUID
    ) -> frozenset[str]:
        return frozenset()


def _container(scopes: frozenset[str], flags: frozenset[str]) -> ApiContainer:
    async def ok() -> CheckState:
        return "ok"

    catalog = SupportScopeCatalog()
    catalog.freeze()
    return ApiContainer(
        settings=ApiSettings(profile="test", dev_secret=SECRET),
        engine=None,
        health_service=HealthService(probes={"database": ok}),
        token_verifier=DevTokenVerifier(SECRET),
        access_context_factory=DbAccessContextFactory(FakeLookup(scopes, flags)),
        identity_bootstrap=None,
        uow_factory=None,
        authorization=ScopeAuthorizationService(),
        entitlement=PlanEntitlementService(DEFAULT_PLANS),
        support_catalog=catalog,
        support_grants=SupportGrantService(
            repo=FakeRepo(),
            member_scopes=NoScopes(),
            catalog=catalog,
            clock=SystemClock(),
            ids=Uuid4Generator(),
        ),
    )


def _headers() -> dict[str, str]:
    token = DevTokenVerifier(SECRET).issue("dev|admin", email="admin@example.test")
    return {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": str(TENANT),
        "X-Workspace-Id": str(WORKSPACE),
    }


async def _call(
    container: ApiContainer, method: str, path: str, body: Any = None
) -> httpx.Response:
    app = create_app(container)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, f"/api/v1{path}", json=body, headers=_headers())


ENABLED = frozenset({"support_access"})
GRANTER = frozenset({"support.grant", "x.read"})
BODY = {
    "scope_set_key": "ctx.read",
    "resource_type": "workspace",
    "reason": "the parser misread page 3",
    "duration_hours": 72,
}


async def test_a_body_naming_a_staff_member_is_refused() -> None:
    response = await _call(
        _container(GRANTER, ENABLED),
        "POST",
        "/support/grants",
        {**BODY, "staff_user_id": str(uuid.uuid4())},
    )
    assert response.status_code == 422


async def test_without_the_flag_every_customer_route_says_not_enabled() -> None:
    container = _container(GRANTER, frozenset())
    for method, path, body in (
        ("GET", "/support/catalog", None),
        ("GET", "/support/grants", None),
        ("POST", "/support/grants", BODY),
    ):
        response = await _call(container, method, path, body)
        assert response.status_code == 403, path
        assert response.json()["code"] == "permission_denied"
        assert response.json()["details"]["reason_code"] == "support_access_not_enabled", path


async def test_an_empty_catalog_lists_nothing_and_refuses_a_request() -> None:
    container = _container(GRANTER, ENABLED)
    assert (await _call(container, "GET", "/support/catalog")).json() == []
    response = await _call(container, "POST", "/support/grants", BODY)
    assert response.status_code == 422
    assert response.json()["details"]["reason_code"] == "support_scope_set_unknown"


async def test_a_member_without_support_scopes_is_refused() -> None:
    response = await _call(_container(frozenset({"x.read"}), ENABLED), "GET", "/support/grants")
    assert response.status_code == 403
    assert "reason_code" not in response.json()["details"]


async def test_a_grant_of_another_workspace_is_not_found() -> None:
    response = await _call(
        _container(GRANTER, ENABLED), "POST", f"/support/grants/{uuid.uuid4()}/revoke"
    )
    assert response.status_code == 404
