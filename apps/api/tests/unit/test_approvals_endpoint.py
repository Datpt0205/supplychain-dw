"""The approvals routes over the real `ApproveAndResumeService` with a fake store.

What the page reads (`required_scope`, `can_decide`, `requested_by_me`) and that the server
refuses a decision the page would have locked, called directly over HTTP with
no page in front of it (ADR 0004). Tenant isolation of the read is the
database's job, covered against a real one by `dw_agent_runtime`'s
`test_approval_decider_scope.py`; workspace isolation is the repository's,
covered against a real one by `test_approval_workspace.py` there. Here: the
routes hand the repository the caller's own workspace, and answer 404 for
another workspace's request (approval-audit-and-workspace/02); and they hand
it the caller's `ApprovalAudience`, so a stamped request the caller may
neither decide nor asked for is a 404 by id, absent from the list, and a 404
to a decision (ADR 0004, amendment 2026-10-07). The SQL filter itself is held
to `ApprovalAudience.may_see` by `dw_platform`'s `test_approval_visibility.py`.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager

from dw_agent_runtime.approval_codes import ApprovalViewService
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
from dw_platform.application.approval_codes import (
    ApprovalSubjectVersions,
    DecisionCodeKey,
    NewDecisionCode,
    OpenCode,
    ViewReceipt,
)
from dw_platform.application.authorization import ApprovalAudience, ScopeAuthorizationService
from dw_platform.application.entitlement import DEFAULT_PLANS, PlanEntitlementService
from dw_platform.application.identity import DbAccessContextFactory, MembershipAccess
from dw_platform.domain.approval import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
    decided_event_type,
)
from dw_platform.domain.outbox import OutboxEvent

pytestmark = pytest.mark.unit

SECRET = "unit-test-secret-0123456789abcdef"
TENANT = uuid.uuid4()
WORKSPACE = uuid.uuid4()
OTHER_WORKSPACE = uuid.uuid4()
VIEWER = uuid.uuid4()
SOMEONE_ELSE = uuid.uuid4()
BOARD_SCOPE = "demo.approve.board"


@dataclass
class FakeMembershipLookup:
    scopes: frozenset[str]
    roles: frozenset[str] = frozenset({"approver"})

    async def find_access(
        self, subject: str, issuer: str, tenant_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> MembershipAccess | None:
        if tenant_id != TENANT or workspace_id != WORKSPACE:
            return None
        return MembershipAccess(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            principal_id=VIEWER,
            roles=self.roles,
            scopes=self.scopes,
            groups=frozenset(),
            clearance="internal",
            plan_id="professional",
            feature_flags=frozenset(),
        )


@dataclass
class FakeApprovalRepo:
    """Honours the port: `save` refuses a stale version like the SQL one, and
    reads see the asked workspace only and what the audience may see, like the
    SQL ones."""

    request: ApprovalRequest
    decisions: list[ApprovalDecision] = field(default_factory=list)
    saved_version: int = 1
    asked_workspaces: list[uuid.UUID] = field(default_factory=list)

    def _mine(self, workspace_id: uuid.UUID, audience: ApprovalAudience) -> bool:
        self.asked_workspaces.append(workspace_id)
        return workspace_id == self.request.workspace_id.value and audience.may_see(self.request)

    async def get(
        self, request_id: uuid.UUID, *, workspace_id: uuid.UUID, audience: ApprovalAudience
    ) -> ApprovalRequest | None:
        found = self._mine(workspace_id, audience) and request_id == self.request.id
        return self.request if found else None

    async def save(self, request: ApprovalRequest) -> None:
        if request.version - 1 != self.saved_version:
            raise ConflictError("approval request was modified concurrently")
        self.saved_version = request.version

    async def add_decision(self, decision: ApprovalDecision) -> None:
        self.decisions.append(decision)

    async def add(self, request: ApprovalRequest) -> None:
        raise NotImplementedError("not exercised by the approvals routes")

    async def list_pending(
        self, request: PageRequest, *, workspace_id: uuid.UUID, audience: ApprovalAudience
    ) -> Page[Any]:
        pending = (
            self._mine(workspace_id, audience) and self.request.status is ApprovalStatus.PENDING
        )
        return Page(items=(self.request,) if pending else (), next_cursor=None)


@dataclass
class FakeOutbox:
    """A request with no run announces its decision through the outbox."""

    events: list[OutboxEvent] = field(default_factory=list)

    async def add(self, event: OutboxEvent) -> None:
        self.events.append(event)

    async def list_unprocessed(self, limit: int = 100) -> list[OutboxEvent]:
        raise NotImplementedError("not exercised by the approvals routes")

    async def has_unprocessed(self, event_type: str, aggregate_id: uuid.UUID) -> bool:
        raise NotImplementedError("not exercised by the approvals routes")


@dataclass
class FakeUoW:
    approvals: FakeApprovalRepo
    outbox: FakeOutbox

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
        approval_type="demo.action.approve",
        requested_by=UserId(requested_by),
        reason="cần duyệt",
        required_scope=required_scope,
    )


def make_container(
    repo: FakeApprovalRepo,
    scopes: frozenset[str],
    outbox: FakeOutbox | None = None,
    roles: frozenset[str] = frozenset({"approver"}),
) -> ApiContainer:
    resolved_outbox = outbox or FakeOutbox()

    async def ok_probe() -> CheckState:
        return "ok"

    def uow_factory(context: AccessContext) -> Any:
        return FakeUoW(approvals=repo, outbox=resolved_outbox)

    return ApiContainer(
        settings=ApiSettings(profile="test", dev_secret=SECRET),
        engine=None,
        health_service=HealthService(probes={"database": ok_probe}),
        token_verifier=DevTokenVerifier(SECRET),
        access_context_factory=DbAccessContextFactory(FakeMembershipLookup(scopes, roles)),
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
    [(SOMEONE_ELSE, BOARD_SCOPE, False), (VIEWER, BOARD_SCOPE, True), (VIEWER, None, True)],
)
async def test_the_view_carries_the_stamp_and_whose_request_it_is(
    requested_by: uuid.UUID, required_scope: str | None, mine: bool
) -> None:
    request = make_request(requested_by=requested_by, required_scope=required_scope)
    # Someone else's stamped request is served to a holder of the stamp only.
    scopes = {"approvals.read"} | (set() if mine else {"approvals.decide", BOARD_SCOPE})
    container = make_container(FakeApprovalRepo(request), frozenset(scopes))

    response = await _call(container, "GET", f"/{request.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["required_scope"] == required_scope
    assert body["requested_by_me"] is mine
    # A boolean, not an id: no other member's identity reaches the browser.
    assert str(requested_by) not in response.text


PLATFORM_ADMIN = frozenset({"platform_admin"})


@pytest.mark.parametrize(
    ("roles", "scopes", "required_scope", "can_decide"),
    [
        (frozenset({"approver"}), {"approvals.decide", BOARD_SCOPE}, BOARD_SCOPE, True),
        (frozenset({"approver"}), set(), None, False),
        (PLATFORM_ADMIN, {"platform.admin", BOARD_SCOPE}, BOARD_SCOPE, True),
        (PLATFORM_ADMIN, {"platform.admin"}, None, True),
    ],
    ids=["holder", "no-decide-right", "admin-with-stamp", "admin-unstamped"],
)
async def test_can_decide_is_the_servers_answer_in_the_list_and_the_detail(
    roles: frozenset[str], scopes: set[str], required_scope: str | None, can_decide: bool
) -> None:
    """ADR 0004: the session's `hasScope` passes `platform_admin` on every scope, so
    the page locks on this instead, built from the checks `decide` runs."""
    request = make_request(requested_by=SOMEONE_ELSE, required_scope=required_scope)
    container = make_container(
        FakeApprovalRepo(request), frozenset({"approvals.read", *scopes}), roles=roles
    )

    detail = await _call(container, "GET", f"/{request.id}")
    listed = await _call(container, "GET", "")

    assert detail.status_code == 200
    assert detail.json()["can_decide"] is can_decide
    assert [item["can_decide"] for item in listed.json()["items"]] == [can_decide]


@pytest.mark.parametrize(
    ("roles", "scopes"),
    [
        (frozenset({"approver"}), {"approvals.read", "approvals.decide"}),
        (frozenset({"approver"}), {"approvals.read", BOARD_SCOPE}),
        (frozenset({"approver"}), {"approvals.read"}),
        (PLATFORM_ADMIN, {"platform.admin"}),
    ],
    ids=["decide-right-only", "stamp-without-decide-right", "reader", "admin-without-stamp"],
)
async def test_a_stamped_request_is_absent_to_whoever_may_not_decide_it(
    roles: frozenset[str], scopes: set[str]
) -> None:
    """ADR 0004, amendment 2026-10-07: someone else's stamped request is served
    only to who may decide it. Everyone else gets "never existed" on every
    route, the decision included: a 403 would confirm it exists and name the
    scope it needs. Nothing is recorded."""
    request = make_request(requested_by=SOMEONE_ELSE, required_scope=BOARD_SCOPE)
    repo = FakeApprovalRepo(request)
    container = make_container(repo, frozenset(scopes), roles=roles)

    listed = await _call(container, "GET", "")
    by_id = await _call(container, "GET", f"/{request.id}")
    decisions = [
        await _call(
            container, "POST", f"/{request.id}/decisions", {"approve": approve, "comment": "x"}
        )
        for approve in (True, False)
    ]

    assert listed.status_code == 200
    assert listed.json()["items"] == []
    assert by_id.status_code == 404
    assert [d.status_code for d in decisions] == [404, 404]
    assert BOARD_SCOPE not in by_id.text + "".join(d.text for d in decisions)
    assert repo.decisions == []
    assert request.status is ApprovalStatus.PENDING


async def test_the_requester_sees_their_stamped_request_but_cannot_approve_it() -> None:
    """Seeing is not deciding: the requester is served their own stamped request,
    locked (`can_decide` false), and an approval over HTTP is a 403 naming the
    stamp, with nothing recorded."""
    request = make_request(requested_by=VIEWER, required_scope=BOARD_SCOPE)
    repo = FakeApprovalRepo(request)
    container = make_container(repo, frozenset({"approvals.read", "approvals.decide"}))

    listed = await _call(container, "GET", "")
    response = await _call(
        container, "POST", f"/{request.id}/decisions", {"approve": True, "comment": ""}
    )

    assert [(i["id"], i["can_decide"]) for i in listed.json()["items"]] == [
        (str(request.id), False)
    ]
    assert response.status_code == 403
    assert response.json()["details"]["action"] == BOARD_SCOPE
    assert repo.decisions == []
    assert request.status is ApprovalStatus.PENDING


async def test_a_holder_of_the_stamp_decides_over_http() -> None:
    request = make_request(requested_by=SOMEONE_ELSE, required_scope=BOARD_SCOPE)
    repo = FakeApprovalRepo(request)
    outbox = FakeOutbox()
    container = make_container(
        repo, frozenset({"approvals.read", "approvals.decide", BOARD_SCOPE}), outbox
    )

    response = await _call(
        container, "POST", f"/{request.id}/decisions", {"approve": True, "comment": "đồng ý"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert response.json()["required_scope"] == BOARD_SCOPE
    assert len(repo.decisions) == 1
    # No run to resume, so the decision is announced once, in its own transaction.
    assert [e.event_type for e in outbox.events] == [decided_event_type("demo.action.approve")]


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


# ---- opening an approval: the receipt and the code (channels Z5) ----

_CODE_KEY = DecisionCodeKey(b"api-unit-code-secret-0123456789")


@dataclass
class FakeCodeStore:
    """Keeps what the route asked to record; issues nothing on its own."""

    views: list[tuple[ViewReceipt, NewDecisionCode | None]] = field(default_factory=list)

    async def record_view(
        self, context: AccessContext, receipt: ViewReceipt, code: NewDecisionCode | None
    ) -> None:
        self.views.append((receipt, code))

    async def open_codes(self, user_id: uuid.UUID) -> list[OpenCode]:
        return []

    async def codes_of(self, user_id: uuid.UUID) -> Any:
        raise NotImplementedError("not exercised by the view route")

    async def record_wrong_try(self, user_id: uuid.UUID) -> int:
        raise NotImplementedError("not exercised by the view route")

    async def coded_approval(self, code: Any) -> Any:
        raise NotImplementedError("not exercised by the view route")

    async def record_refusal(self, event: Any) -> None:
        raise NotImplementedError("not exercised by the view route")


@dataclass
class _Chats:
    linked: bool = True

    async def zalo_id_for(self, user_id: uuid.UUID) -> str | None:
        return "chat-1" if self.linked else None


@dataclass
class _Versions:
    async def version_of(self, context: AccessContext, request: ApprovalRequest) -> str | None:
        return "7"


def _with_views(
    container: ApiContainer, *, linked: bool = True
) -> tuple[ApiContainer, FakeCodeStore]:
    store = FakeCodeStore()
    subjects = ApprovalSubjectVersions()
    subjects.register("demo.", _Versions())
    assert container.uow_factory is not None and container.approval_flow is not None
    container.approval_views = ApprovalViewService(
        uow_factory=container.uow_factory,
        approval_flow=container.approval_flow,
        store=store,
        subjects=subjects,
        chats=_Chats(linked),
        key=_CODE_KEY,
        clock=SystemClock(),
        ids=Uuid4Generator(),
    )
    return container, store


_DECIDER = frozenset({"approvals.read", "approvals.decide", BOARD_SCOPE})


async def test_opening_issues_a_code_once_and_stores_only_its_hash() -> None:
    request = make_request(requested_by=SOMEONE_ELSE, required_scope=BOARD_SCOPE)
    container, store = _with_views(make_container(FakeApprovalRepo(request), _DECIDER))

    response = await _call(
        container, "POST", f"/{request.id}/view", {"comment": "Đã xem.", "issue_code": True}
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    code = body["code"]
    assert len(code) == 6 and code.isdigit()
    assert body["command_approve"] == f"DUYỆT {code}"
    assert body["command_reject"] == f"KHÔNG {code} <lý do>"
    assert body["unavailable_reason"] is None
    ((receipt, issued),) = store.views
    assert receipt.approval_id == request.id and receipt.user_id == VIEWER
    assert receipt.approval_version == 1 and receipt.subject_version == "7"
    assert issued is not None
    assert issued.comment == "Đã xem."
    assert issued.code_hash == _CODE_KEY.digest(request.id, VIEWER, code)
    assert code.encode() not in issued.code_hash


async def test_opening_without_asking_records_the_view_and_issues_nothing() -> None:
    request = make_request(requested_by=SOMEONE_ELSE, required_scope=BOARD_SCOPE)
    container, store = _with_views(make_container(FakeApprovalRepo(request), _DECIDER))

    body = (await _call(container, "POST", f"/{request.id}/view", {})).json()

    assert body["code"] is None and body["unavailable_reason"] is None
    assert body["requires_comment"] is False
    ((_, issued),) = store.views
    assert issued is None


@pytest.mark.parametrize(
    ("requested_by", "linked", "reason"),
    [(VIEWER, True, "requester"), (SOMEONE_ELSE, False, "not_linked")],
)
async def test_the_page_is_told_why_no_code_is_issued(
    requested_by: uuid.UUID, linked: bool, reason: str
) -> None:
    request = make_request(requested_by=requested_by, required_scope=BOARD_SCOPE)
    container, store = _with_views(
        make_container(FakeApprovalRepo(request), _DECIDER), linked=linked
    )

    body = (await _call(container, "POST", f"/{request.id}/view", {"issue_code": True})).json()

    assert body["code"] is None
    assert body["unavailable_reason"] == reason
    ((_, issued),) = store.views
    assert issued is None


async def test_a_type_nobody_versions_is_decided_on_the_web_only() -> None:
    request = make_request(requested_by=SOMEONE_ELSE, required_scope=BOARD_SCOPE)
    request.approval_type = "memory.review"
    container, store = _with_views(make_container(FakeApprovalRepo(request), _DECIDER))

    body = (await _call(container, "POST", f"/{request.id}/view", {"issue_code": True})).json()

    assert body["unavailable_reason"] == "web_only"
    ((receipt, issued),) = store.views
    assert receipt.subject_version is None and issued is None


async def test_a_stamped_request_the_caller_may_not_see_is_not_found_and_not_recorded() -> None:
    request = make_request(requested_by=SOMEONE_ELSE, required_scope=BOARD_SCOPE)
    container, store = _with_views(
        make_container(FakeApprovalRepo(request), frozenset({"approvals.read", "approvals.decide"}))
    )

    response = await _call(container, "POST", f"/{request.id}/view", {"issue_code": True})

    assert response.status_code == 404
    assert store.views == []
