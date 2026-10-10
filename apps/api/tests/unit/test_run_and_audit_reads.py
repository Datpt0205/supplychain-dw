"""`GET /runs/{id}`, its timeline and `GET /audit/events`: scope, then workspace.

Each read route checks its own scope (`runs.read`, `audit.events`) and hands
the store the caller's own workspace, so another workspace's run is a 404 and
its audit rows are absent (approval-audit-and-workspace/02). The stores here
honour the SQL ones' contract; the SQL ones are tested against a real database
in `dw_platform`'s `test_workspace_reads.py` and `dw_agent_runtime`'s
`test_approval_workspace.py`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager

from dw_agent_runtime.adapters.run_store import RunRecord, RunStatus
from dw_agent_runtime.contracts import RunContext
from dw_api.bootstrap import ApiContainer
from dw_api.health import CheckState, HealthService
from dw_api.main import create_app
from dw_api.settings import ApiSettings
from dw_kernel.errors import NotFoundError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.pagination import (
    CursorPosition,
    Page,
    PageQuery,
    PageRequest,
    build_page,
    encode_cursor,
)
from dw_platform.adapters.identity.dev_token import DevTokenVerifier
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.entitlement import DEFAULT_PLANS, PlanEntitlementService
from dw_platform.application.identity import DbAccessContextFactory, MembershipAccess
from dw_platform.domain.audit import AuditEvent

pytestmark = pytest.mark.unit

SECRET = "unit-test-secret-0123456789abcdef"
TENANT = uuid.uuid4()
WORKSPACE = uuid.uuid4()
OTHER_WORKSPACE = uuid.uuid4()
PRINCIPAL = uuid.uuid4()
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)

# What the two roles of the criterion hold of these scopes: `member` reads the
# inbox and runs, `director` also reads the audit trail.
MEMBER_READS = frozenset({"approvals.read", "runs.read"})
DIRECTOR_READS = MEMBER_READS | {"audit.events"}


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
            principal_id=PRINCIPAL,
            roles=frozenset({"member"}),
            scopes=self.scopes,
            groups=frozenset(),
            clearance="internal",
            plan_id="professional",
            feature_flags=frozenset(),
        )


def _record(run_id: uuid.UUID, workspace: uuid.UUID) -> RunRecord:
    return RunRecord(
        id=run_id,
        thread_id=run_id,
        workspace_id=workspace,
        status=RunStatus.COMPLETED,
        worker_id="demo",
        worker_version="1.0.0",
        graph_version="1.0.0",
        prompt_bundle_version=None,
        toolset_version=None,
        policy_version=None,
        memory_policy_version=None,
        autonomy_level=None,
        approval_policy_version=None,
        input={},
        result={"secret": f"of {workspace}"},
        error=None,
        approval_request_id=None,
        release_manifest_ref=None,
        requested_by=PRINCIPAL,
        actor_roles=frozenset(),
        actor_scopes=frozenset(),
        actor_plan_id="professional",
        actor_clearance="internal",
        actor_record_visibility="open",
        actor_visible_owners=None,
    )


@dataclass
class FakeRunStore:
    """Honours `SqlWorkerRunStore.get`: a run is found in its own workspace only."""

    runs: dict[uuid.UUID, uuid.UUID] = field(default_factory=dict)
    asked: list[RunContext] = field(default_factory=list)

    async def get(self, run_context: RunContext, run_id: uuid.UUID) -> RunRecord:
        self.asked.append(run_context)
        if self.runs.get(run_id) != run_context.workspace_id:
            raise NotFoundError("run not found", details={"run_id": str(run_id)})
        return _record(run_id, run_context.workspace_id)


def _event(workspace: uuid.UUID, run_id: uuid.UUID | None = None) -> AuditEvent:
    return AuditEvent(
        id=uuid.uuid4(),
        tenant_id=TenantId(TENANT),
        workspace_id=WorkspaceId(workspace),
        actor_id=UserId(PRINCIPAL),
        action="run.completed",
        resource_type="worker_run",
        resource_id=str(run_id),
        run_id=run_id,
        details={"secret": f"of {workspace}"},
        occurred_at=NOW,
    )


@dataclass
class FakeAuditRepo:
    """Honours the port: the asked workspace's rows only."""

    events: list[AuditEvent]
    asked_workspaces: list[uuid.UUID] = field(default_factory=list)

    def _in(self, workspace_id: uuid.UUID) -> list[AuditEvent]:
        self.asked_workspaces.append(workspace_id)
        return [event for event in self.events if event.workspace_id.value == workspace_id]

    async def append(self, event: AuditEvent) -> None:
        raise NotImplementedError("not exercised by the read routes")

    async def list_page(self, request: PageRequest, *, workspace_id: uuid.UUID) -> Page[AuditEvent]:
        return build_page(
            self._in(workspace_id),
            request=request,
            position_of=lambda event: CursorPosition(
                sort_value=event.occurred_at, tiebreaker=event.id
            ),
        )

    async def list_for_run(
        self, run_id: uuid.UUID, *, workspace_id: uuid.UUID, limit: int = 100
    ) -> list[AuditEvent]:
        return [event for event in self._in(workspace_id) if event.run_id == run_id][:limit]


@dataclass
class FakeUoW:
    audit: FakeAuditRepo

    async def __aenter__(self) -> FakeUoW:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


def make_container(
    scopes: frozenset[str],
    *,
    store: FakeRunStore | None = None,
    audit: FakeAuditRepo | None = None,
) -> ApiContainer:
    async def ok_probe() -> CheckState:
        return "ok"

    repo = audit or FakeAuditRepo(events=[])

    def uow_factory(context: AccessContext) -> Any:
        return FakeUoW(audit=repo)

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
        run_store=store or FakeRunStore(),  # type: ignore[arg-type]
    )


async def _get(
    container: ApiContainer, path: str, params: dict[str, str] | None = None
) -> httpx.Response:
    token = DevTokenVerifier(SECRET).issue("dev|reader", email="reader@fpt.com")
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": str(TENANT),
        "X-Workspace-Id": str(WORKSPACE),
    }
    app = create_app(container)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(f"/api/v1{path}", headers=headers, params=params)


async def test_reading_a_run_needs_runs_read() -> None:
    run_id = uuid.uuid4()
    store = FakeRunStore(runs={run_id: WORKSPACE})

    response = await _get(
        make_container(frozenset({"approvals.read"}), store=store), f"/runs/{run_id}"
    )

    assert response.status_code == 403
    assert store.asked == [], "refused before the run was read"


async def test_another_workspaces_run_is_the_same_404_as_no_run() -> None:
    theirs = uuid.uuid4()
    store = FakeRunStore(runs={theirs: OTHER_WORKSPACE})
    container = make_container(MEMBER_READS, store=store)

    other = await _get(container, f"/runs/{theirs}")
    unknown = await _get(container, f"/runs/{uuid.uuid4()}")

    assert other.status_code == unknown.status_code == 404
    assert "secret" not in other.text
    assert {context.workspace_id for context in store.asked} == {WORKSPACE}


async def test_the_callers_own_run_is_read() -> None:
    mine = uuid.uuid4()
    store = FakeRunStore(runs={mine: WORKSPACE})

    response = await _get(make_container(MEMBER_READS, store=store), f"/runs/{mine}")

    assert response.status_code == 200
    assert response.json()["id"] == str(mine)
    assert [context.workspace_id for context in store.asked] == [WORKSPACE]
    assert [context.tenant_id for context in store.asked] == [TENANT]


async def test_a_runs_timeline_is_read_in_the_callers_workspace() -> None:
    run_id = uuid.uuid4()
    mine, theirs = _event(WORKSPACE, run_id), _event(OTHER_WORKSPACE, run_id)
    audit = FakeAuditRepo(events=[mine, theirs])

    response = await _get(make_container(MEMBER_READS, audit=audit), f"/runs/{run_id}/timeline")

    assert response.status_code == 200
    assert [event["details"] for event in response.json()] == [mine.details]
    assert audit.asked_workspaces == [WORKSPACE]


async def test_the_audit_trail_needs_audit_events_not_the_inbox_scope() -> None:
    """`approvals.read` is a `member`'s; the audit trail is `audit.events`
    (`director`, `executive`)."""
    audit = FakeAuditRepo(events=[_event(WORKSPACE)])

    as_member = await _get(make_container(MEMBER_READS, audit=audit), "/audit/events")
    assert as_member.status_code == 403
    assert audit.asked_workspaces == [], "refused before the trail was read"

    as_director = await _get(make_container(DIRECTOR_READS, audit=audit), "/audit/events")
    assert as_director.status_code == 200


async def test_the_audit_trail_is_the_callers_workspace_only() -> None:
    mine, theirs = _event(WORKSPACE), _event(OTHER_WORKSPACE)
    audit = FakeAuditRepo(events=[mine, theirs])

    response = await _get(make_container(DIRECTOR_READS, audit=audit), "/audit/events")

    assert response.status_code == 200
    assert [item["details"] for item in response.json()["items"]] == [mine.details]
    assert audit.asked_workspaces == [WORKSPACE]


@pytest.mark.parametrize(("workspace", "status"), [(WORKSPACE, 200), (OTHER_WORKSPACE, 422)])
async def test_an_audit_cursor_is_bound_to_the_workspace_it_was_issued_in(
    workspace: uuid.UUID, status: int
) -> None:
    cursor = encode_cursor(
        CursorPosition(sort_value=NOW, tiebreaker=uuid.uuid4()),
        PageQuery(key="audit.events", filters={"tenant": TENANT, "workspace": workspace}),
    )

    response = await _get(
        make_container(DIRECTOR_READS), "/audit/events", params={"cursor": cursor}
    )

    assert response.status_code == status
