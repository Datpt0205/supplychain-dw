"""The approvals routes over the real `ApproveAndResumeService` with a fake store.

What the page reads (`required_scope`, `requested_by_me`) and that the server
refuses a decision the page would have locked, called directly over HTTP with
no page in front of it (ADR 0020). Tenant isolation of the read is the
database's job, covered against a real one by `dw_agent_runtime`'s
`test_approval_decider_scope.py`; workspace isolation is the repository's,
covered against a real one by `test_approval_workspace.py` there. Here: the
routes hand the repository the caller's own workspace, and answer 404 for
another workspace's request (approval-audit-and-workspace/02).
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
from dw_kernel.pagination import CursorPosition, Page, PageQuery, PageRequest, encode_cursor
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
OTHER_WORKSPACE = uuid.uuid4()
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
    """Honours the port: `save` refuses a stale version like the SQL one, and
    reads see the asked workspace only, like the SQL ones."""

    request: ApprovalRequest
    decisions: list[ApprovalDecision] = field(default_factory=list)
    saved_version: int = 1
    asked_workspaces: list[uuid.UUID] = field(default_factory=list)

    def _mine(self, workspace_id: uuid.UUID) -> bool:
        self.asked_workspaces.append(workspace_id)
        return workspace_id == self.request.workspace_id.value

    async def get(
        self, request_id: uuid.UUID, *, workspace_id: uuid.UUID
    ) -> ApprovalRequest | None:
        found = self._mine(workspace_id) and request_id == self.request.id
        return self.request if found else None

    async def save(self, request: ApprovalRequest) -> None:
        if request.version - 1 != self.saved_version:
            raise ConflictError("approval request was modified concurrently")
        self.saved_version = request.version

    async def add_decision(self, decision: ApprovalDecision) -> None:
        self.decisions.append(decision)

    async def add(self, request: ApprovalRequest) -> None:
        raise NotImplementedError("not exercised by the approvals routes")

    async def list_pending(self, request: PageRequest, *, workspace_id: uuid.UUID) -> Page[Any]:
        pending = self._mine(workspace_id) and self.request.status is ApprovalStatus.PENDING
        return Page(items=(self.request,) if pending else (), next_cursor=None)


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


def make_request(
    *, requested_by: uuid.UUID, required_scope: str | None, workspace: uuid.UUID = WORKSPACE
) -> ApprovalRequest:
    return ApprovalRequest(
        id=uuid.uuid4(),
        tenant_id=TenantId(TENANT),
        workspace_id=WorkspaceId(workspace),
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
    container: ApiContainer,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
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
                method, f"/api/v1/approvals{path}", headers=headers, json=body, params=params
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


async def test_another_workspaces_approval_is_neither_listed_nor_found_nor_decided() -> None:
    """B in W2 holds `approvals.decide`; the request is W1's. The inbox does not
    list it, by id it is a 404 (not a 403: "not yours" and "never existed" are
    one answer), and a decision is refused before anything is recorded."""
    request = make_request(
        requested_by=SOMEONE_ELSE, required_scope=None, workspace=OTHER_WORKSPACE
    )
    repo = FakeApprovalRepo(request)
    container = make_container(repo, frozenset({"approvals.read", "approvals.decide"}))

    listed = await _call(container, "GET", "")
    by_id = await _call(container, "GET", f"/{request.id}")
    decided = await _call(
        container, "POST", f"/{request.id}/decisions", {"approve": True, "comment": ""}
    )

    assert listed.status_code == 200
    assert listed.json()["items"] == []
    assert by_id.status_code == 404
    assert decided.status_code == 404
    assert repo.decisions == []
    assert request.status is ApprovalStatus.PENDING
    # Every read asked for the caller's own workspace, never one from the request.
    assert set(repo.asked_workspaces) == {WORKSPACE}


async def test_the_inbox_lists_the_callers_own_workspace() -> None:
    request = make_request(requested_by=SOMEONE_ELSE, required_scope=None)
    container = make_container(FakeApprovalRepo(request), frozenset({"approvals.read"}))

    response = await _call(container, "GET", "")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [str(request.id)]


@pytest.mark.parametrize(("workspace", "status"), [(WORKSPACE, 200), (OTHER_WORKSPACE, 422)])
async def test_a_cursor_is_bound_to_the_workspace_it_was_issued_in(
    workspace: uuid.UUID, status: int
) -> None:
    """The workspace is in the cursor's fingerprint as the tenant is."""
    request = make_request(requested_by=SOMEONE_ELSE, required_scope=None)
    container = make_container(FakeApprovalRepo(request), frozenset({"approvals.read"}))
    cursor = encode_cursor(
        CursorPosition(sort_value=datetime.now(UTC), tiebreaker=uuid.uuid4()),
        PageQuery(key="approvals.pending", filters={"tenant": TENANT, "workspace": workspace}),
    )

    response = await _call(container, "GET", "", params={"cursor": cursor})

    assert response.status_code == status
