"""Stop, over HTTP, without letting one tenant stop another's work.

`LangGraphWorkflowRunner.cancel_thread` takes a thread id and nothing else — it
looks the task up in an in-process dict. That is safe while the only caller is a
run that started it. The moment it becomes a route, the id is whatever the
caller typed, and the ownership check has to exist somewhere. These tests are
about where.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from asgi_lifespan import LifespanManager

from dw_api.bootstrap import ApiContainer
from dw_api.health import CheckState, HealthService
from dw_api.main import create_app
from dw_api.settings import ApiSettings
from dw_platform.adapters.identity.dev_token import DevTokenVerifier
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.entitlement import DEFAULT_PLANS, PlanEntitlementService
from dw_platform.application.identity import DbAccessContextFactory, MembershipAccess

pytestmark = pytest.mark.unit

SECRET = "unit-test-secret-0123456789abcdef"
TENANT = uuid.uuid4()
WORKSPACE = uuid.uuid4()
PRINCIPAL = uuid.uuid4()
MINE = uuid.uuid4()
THEIRS = uuid.uuid4()


class _Lookup:
    def __init__(self, scopes: frozenset[str]) -> None:
        self._scopes = scopes

    async def find_access(
        self, subject: str, issuer: str, tenant_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> MembershipAccess | None:
        if tenant_id != TENANT or workspace_id != WORKSPACE:
            return None
        return MembershipAccess(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            principal_id=PRINCIPAL,
            roles=frozenset({"member"}),
            scopes=self._scopes,
            groups=frozenset(),
            clearance="internal",
            plan_id="professional",
            feature_flags=frozenset(),
        )


class _RunStore:
    """Answers ownership the way the SQL store does: another tenant's thread is
    invisible by RLS, another workspace's by the store's own filter."""

    def __init__(self) -> None:
        self.asked: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID]] = []

    async def thread_belongs_to(
        self, tenant_id: uuid.UUID, workspace_id: uuid.UUID, thread_id: uuid.UUID
    ) -> bool:
        self.asked.append((tenant_id, workspace_id, thread_id))
        return tenant_id == TENANT and workspace_id == WORKSPACE and thread_id == MINE


class _Runner:
    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.cancelled: list[uuid.UUID] = []

    async def cancel_thread(self, thread_id: uuid.UUID) -> bool:
        self.cancelled.append(thread_id)
        return self.result


def _container(
    store: _RunStore,
    runner: _Runner,
    scopes: frozenset[str] = frozenset({"runs.cancel"}),
) -> ApiContainer:
    async def ok_probe() -> CheckState:
        return "ok"

    return ApiContainer(
        settings=ApiSettings(profile="test", dev_secret=SECRET),
        engine=None,
        health_service=HealthService(probes={"database": ok_probe}),
        token_verifier=DevTokenVerifier(SECRET),
        access_context_factory=DbAccessContextFactory(_Lookup(scopes)),
        identity_bootstrap=None,
        uow_factory=None,
        authorization=ScopeAuthorizationService(),
        entitlement=PlanEntitlementService(DEFAULT_PLANS),
        run_store=store,  # type: ignore[arg-type]
        runner=runner,  # type: ignore[arg-type]
    )


def _headers() -> dict[str, str]:
    token = DevTokenVerifier(SECRET).issue("dev|an.nguyen", email="an@alpha.local")
    return {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": str(TENANT),
        "X-Workspace-Id": str(WORKSPACE),
    }


async def _cancel(container: ApiContainer, thread_id: uuid.UUID) -> httpx.Response:
    app = create_app(container)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(f"/api/v1/runs/threads/{thread_id}/cancel", headers=_headers())


async def test_stopping_your_own_thread_stops_it() -> None:
    runner = _Runner()
    response = await _cancel(_container(_RunStore(), runner), MINE)

    assert response.status_code == 200
    assert response.json() == {"cancelled": True}
    assert runner.cancelled == [MINE]


async def test_another_tenants_thread_cannot_be_stopped() -> None:
    """The reason this endpoint needed writing carefully. The runner would have
    cancelled it — it has no tenant to check against."""
    runner = _Runner()
    response = await _cancel(_container(_RunStore(), runner), THEIRS)

    assert response.status_code == 404
    assert runner.cancelled == [], "the runner must never have been asked"


async def test_a_thread_that_is_not_yours_looks_the_same_as_one_that_never_existed() -> None:
    """404 rather than 403. The difference between "not yours" and "no such
    thread" is exactly what someone probing ids is trying to learn."""
    runner = _Runner()
    unknown = await _cancel(_container(_RunStore(), runner), uuid.uuid4())
    other = await _cancel(_container(_RunStore(), runner), THEIRS)

    assert unknown.status_code == other.status_code == 404


async def test_ownership_is_asked_under_the_callers_own_tenant_and_workspace() -> None:
    """Not under a tenant or workspace from the request body. The check is only
    worth anything if it runs as the caller: RLS on worker_runs narrows by
    tenant only, so the workspace has to reach the store too."""
    store = _RunStore()
    await _cancel(_container(store, _Runner()), MINE)

    assert store.asked == [(TENANT, WORKSPACE, MINE)]


async def test_the_scope_is_required() -> None:
    runner = _Runner()
    response = await _cancel(_container(_RunStore(), runner, scopes=frozenset()), MINE)

    assert response.status_code == 403
    assert runner.cancelled == []


async def test_a_thread_that_already_finished_is_a_plain_no() -> None:
    """Pressing Stop twice is not an error. The second press describes a thread
    that is already not running, which is what the caller wanted."""
    response = await _cancel(_container(_RunStore(), _Runner(result=False)), MINE)

    assert response.status_code == 200
    assert response.json() == {"cancelled": False}
