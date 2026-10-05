"""The approvals routes over the real `ApproveAndResumeService` with a fake store.

What the page reads (`required_scope`, `requested_by_me`) and that the server
refuses a decision the page would have locked, called directly over HTTP with
no page in front of it (ADR 0020). Tenant isolation of the read is the
database's job, covered against a real one by `dw_agent_runtime`'s
`test_approval_decider_scope.py`.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager

from dw_agent_runtime.approval_flow import ApproveAndResumeService
from dw_api.bootstrap import ApiContainer
from dw_api.health import CheckState, HealthService
from dw_api.main import create_app
from dw_api.settings import ApiSettings
from dw_kernel.errors import ConflictError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.adapters.identity.dev_token import DevTokenVerifier
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.entitlement import DEFAULT_PLANS, PlanEntitlementService
from dw_platform.application.identity import DbAccessContextFactory, MembershipAccess
from dw_platform.domain.approval import ApprovalDecision, ApprovalRequest, ApprovalStatus

pytestmark = pytest.mark.unit

SECRET = "unit-test-secret-0123456789abcdef"
TENANT = uuid.uuid4()
WORKSPACE = uuid.uuid4()
VIEWER = uuid.uuid4()
SOMEONE_ELSE = uuid.uuid4()
BOD_SCOPE = "supply_chain.approve.bod"


@dataclass
class FakeMembershipLookup:
    scopes: frozenset[str]

    async def find_access(
        self, subject: str, issuer: str, tenant_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> MembershipAccess | None:
        if tenant_id != TENANT or workspace_id != WORKSPACE:
            return None
        return MembershipAccess(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            principal_id=VIEWER,
            roles=frozenset({"approver"}),
            scopes=self.scopes,
            groups=frozenset(),
            clearance="internal",
            plan_id="professional",
            feature_flags=frozenset(),
        )


@dataclass
class FakeApprovalRepo:
    """Honours the port: `save` refuses a stale version like the SQL one."""

    request: ApprovalRequest
    decisions: list[ApprovalDecision] = field(default_factory=list)
    saved_version: int = 1

    async def get(self, request_id: uuid.UUID) -> ApprovalRequest | None:
        return self.request if request_id == self.request.id else None

    async def save(self, request: ApprovalRequest) -> None:
        if request.version - 1 != self.saved_version:
            raise ConflictError("approval request was modified concurrently")
        self.saved_version = request.version

    async def add_decision(self, decision: ApprovalDecision) -> None:
        self.decisions.append(decision)

    async def add(self, request: ApprovalRequest) -> None:
        raise NotImplementedError("not exercised by the approvals routes")

    async def list_pending(self, request: Any) -> Any:
        raise NotImplementedError("not exercised by these tests")


@dataclass
class FakeUoW:
    approvals: FakeApprovalRepo

    async def __aenter__(self) -> "FakeUoW":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class RunnerThatMustNotRun:
    """These requests carry no run; reaching the runner would be a bug."""

    def hosts(self, **_: Any) -> bool:
        raise AssertionError("no run to host")

    async def resume(self, **_: Any) -> None:
        raise AssertionError("no run to resume")


def make_request(*, requested_by: uuid.UUID, required_scope: str | None) -> ApprovalRequest:
    return ApprovalRequest(
        id=uuid.uuid4(),
        tenant_id=TenantId(TENANT),
        workspace_id=WorkspaceId(WORKSPACE),
        approval_type="supply_chain.product_action.approve",
        requested_by=UserId(requested_by),
        reason="BGĐ duyệt phát triển sản phẩm",
        required_scope=required_scope,
    )


def make_container(repo: FakeApprovalRepo, scopes: frozenset[str]) -> ApiContainer:
    async def ok_probe() -> CheckState:
        return "ok"

    def uow_factory(context: AccessContext) -> Any:
        return FakeUoW(approvals=repo)

    return ApiContainer(
        settings=ApiSettings(profile="test", dev_secret=SECRET),
        engine=None,
        health_service=HealthService(probes={"database": ok_probe}),
        token_verifier=DevTokenVerifier(SECRET),
        access_context_factory=DbAccessContextFactory(FakeMembershipLookup(scopes)),
        identity_bootstrap=None,
        uow_factory=uow_factory,
        authorization=ScopeAuthorizationService(),
        entitlement=PlanEntitlementService(DEFAULT_PLANS),
        approval_flow=ApproveAndResumeService(
            uow_factory=uow_factory,
            runner=RunnerThatMustNotRun(),  # type: ignore[arg-type]
            run_store=None,  # type: ignore[arg-type]
            clock=SystemClock(),
            id_generator=Uuid4Generator(),
        ),
    )


async def _call(
    container: ApiContainer, method: str, path: str, body: dict[str, Any] | None = None
) -> httpx.Response:
    token = DevTokenVerifier(SECRET).issue("dev|approver", email="approver@fpt.com")
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": str(TENANT),
        "X-Workspace-Id": str(WORKSPACE),
    }
    app = create_app(container)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(
                method, f"/api/v1/approvals{path}", headers=headers, json=body
            )


@pytest.mark.parametrize(
    ("requested_by", "required_scope", "mine"),
    [(SOMEONE_ELSE, BOD_SCOPE, False), (VIEWER, None, True)],
)
async def test_the_view_carries_the_stamp_and_whose_request_it_is(
    requested_by: uuid.UUID, required_scope: str | None, mine: bool
) -> None:
    request = make_request(requested_by=requested_by, required_scope=required_scope)
    container = make_container(FakeApprovalRepo(request), frozenset({"approvals.read"}))

    response = await _call(container, "GET", f"/{request.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["required_scope"] == required_scope
    assert body["requested_by_me"] is mine
    # A boolean, not an id: no other member's identity reaches the browser.
    assert str(requested_by) not in response.text


async def test_the_server_refuses_a_decision_the_page_would_have_locked() -> None:
    """No page in front: `approvals.decide` without the stamp is a 403, and
    nothing is recorded."""
    request = make_request(requested_by=SOMEONE_ELSE, required_scope=BOD_SCOPE)
    repo = FakeApprovalRepo(request)
    container = make_container(repo, frozenset({"approvals.read", "approvals.decide"}))

    response = await _call(
        container, "POST", f"/{request.id}/decisions", {"approve": True, "comment": ""}
    )

    assert response.status_code == 403
    assert repo.decisions == []
    assert request.status is ApprovalStatus.PENDING


async def test_a_holder_of_the_stamp_decides_over_http() -> None:
    request = make_request(requested_by=SOMEONE_ELSE, required_scope=BOD_SCOPE)
    repo = FakeApprovalRepo(request)
    container = make_container(repo, frozenset({"approvals.read", "approvals.decide", BOD_SCOPE}))

    response = await _call(
        container, "POST", f"/{request.id}/decisions", {"approve": True, "comment": "đồng ý"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert response.json()["required_scope"] == BOD_SCOPE
    assert len(repo.decisions) == 1
