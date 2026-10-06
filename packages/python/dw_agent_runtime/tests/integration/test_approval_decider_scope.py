"""Integration: an approval's `required_scope`, from the pause to the decision (ADR 0004).

Real Postgres end to end: the node's interrupt payload stamps the row, the CHECK
refuses a malformed stamp and the run ends failed instead of parking, and
`ApproveAndResumeService.decide` enforces the stamp before anything is written,
and `platform_admin` does not pass it (2026-10-06). Whoever may not decide a
stamped request and did not ask for it does not find it at all: the decision
answers 404, as the inbox does (amendment 2026-10-07).
Another tenant's approval is not found, even by a holder of the scope; another
workspace of the same tenant is `test_approval_workspace.py`
(platform-runtime/approval-audit-and-workspace/02).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from runtime_harness import TENANT_B, RuntimeUrls, make_run_context
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dw_agent_runtime.adapters.checkpoint import SqlAlchemyCheckpointSaver
from dw_agent_runtime.adapters.langgraph_runner import LangGraphWorkflowRunner
from dw_agent_runtime.adapters.run_store import RunStatus, SqlWorkerRunStore
from dw_agent_runtime.approval_flow import ApproveAndResumeService
from dw_agent_runtime.autonomy import AutonomyApprovalPolicy
from dw_agent_runtime.context import access_context_from_run
from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.model.budget import RunBudgetLedger
from dw_agent_runtime.registry import GraphRegistry, WorkerRegistry
from dw_agent_runtime.testing.demo_graph import DEMO_WORKER_YAML, DemoState
from dw_kernel.errors import NotFoundError, PermissionDeniedError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.pagination import PageQuery, PageRequest
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.adapters.persistence.uow import SqlPlatformUnitOfWorkFactory
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ApprovalAudience, ScopeAuthorizationService
from dw_platform.domain.approval import ApprovalRequest, ApprovalStatus

pytestmark = pytest.mark.integration


def _sees(context: AccessContext) -> ApprovalAudience:
    """What `context` may see of the approvals (ADR 0004), as the API asks it."""
    return ApprovalAudience.of(context, ScopeAuthorizationService())


BOARD_SCOPE = "demo.approve.board"
STALE_AFTER_SECONDS_LOCAL = 3600
_ABSENT = object()


def build_stamping_graph(required_scope: object) -> Callable[[], StateGraph]:  # type: ignore[type-arg]
    """The demo graph's shape, pausing with `required_scope` in its payload as a
    context node would, having read it from the tenant's policy."""

    def factory() -> StateGraph:  # type: ignore[type-arg]
        def _review(state: DemoState) -> DemoState:
            body: dict[str, Any] = {"approval_type": "demo.dispatch", "reason": "cần duyệt"}
            if required_scope is not _ABSENT:
                body["required_scope"] = required_scope
            decision: dict[str, Any] = interrupt(body)
            return {"approved": bool(decision.get("approved"))}

        def _dispatch(state: DemoState) -> DemoState:
            return {"dispatched": bool(state.get("approved"))}

        graph: StateGraph = StateGraph(DemoState)  # type: ignore[type-arg]
        graph.add_node("review", _review)
        graph.add_node("dispatch", _dispatch)
        graph.add_edge(START, "review")
        graph.add_edge("review", "dispatch")
        graph.add_edge("dispatch", END)
        return graph

    return factory


class _UnmeteredPlan:
    def runs_per_day(self, plan_id: str) -> int | None:
        return None

    def spend_usd_per_day(self, plan_id: str) -> Decimal | None:
        return None


class Stack:
    def __init__(self, app_url: str, worker_config: Path, factory: Any) -> None:
        self.engine = create_async_engine(app_url, poolclass=NullPool)
        sessions = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        graphs = GraphRegistry()
        graphs.register("demo_approval", "1.0.0", factory)
        workers = WorkerRegistry(graph_registry=graphs)
        workers.load_file(worker_config)
        self.uow_factory = SqlPlatformUnitOfWorkFactory(sessions)
        self.run_store = SqlWorkerRunStore(
            sessions, stale_run_after_seconds=STALE_AFTER_SECONDS_LOCAL
        )
        self.runner = LangGraphWorkflowRunner(
            worker_registry=workers,
            graph_registry=graphs,
            checkpoint_saver=SqlAlchemyCheckpointSaver(sessions),
            run_store=self.run_store,
            uow_factory=self.uow_factory,
            clock=SystemClock(),
            id_generator=Uuid4Generator(),
            allowance=_UnmeteredPlan(),
            budget=RunBudgetLedger(),
            approval_policy=AutonomyApprovalPolicy(),
        )
        self.approvals = ApproveAndResumeService(
            uow_factory=self.uow_factory,
            runner=self.runner,
            run_store=self.run_store,
            clock=SystemClock(),
            id_generator=Uuid4Generator(),
        )

    async def approval_rows_for(self, run: RunContext) -> int:
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": str(run.tenant_id)},
            )
            count = await conn.scalar(
                sa.text("SELECT count(*) FROM platform.approval_requests WHERE run_id = :r"),
                {"r": run.run_id},
            )
        return int(count or 0)

    async def decision_rows_for(self, run: RunContext, approval_id: uuid.UUID) -> int:
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": str(run.tenant_id)},
            )
            count = await conn.scalar(
                sa.text("SELECT count(*) FROM platform.approval_decisions WHERE request_id = :a"),
                {"a": approval_id},
            )
        return int(count or 0)

    async def dispose(self) -> None:
        await self.engine.dispose()


@pytest.fixture
def worker_config(tmp_path: Path) -> Path:
    path = tmp_path / "demo_approval.yaml"
    path.write_text(DEMO_WORKER_YAML, encoding="utf-8")
    return path


def _decider(
    *scopes: str, tenant: uuid.UUID, workspace: uuid.UUID, principal: uuid.UUID | None = None
) -> AccessContext:
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=principal or uuid.uuid4(),
        roles=frozenset({"approver"}),
        scopes=frozenset({"approvals.decide", *scopes}),
        plan_id="professional",
    )


async def _audit_actions(stack: Stack, run: RunContext) -> list[str]:
    async with stack.uow_factory(access_context_from_run(run)) as uow:
        page = await uow.audit.list_page(
            PageRequest(limit=50, after=None, query=PageQuery(key="test.audit")),
            workspace_id=run.workspace_id,
        )
    return [event.action for event in page.items if event.run_id == run.run_id]


def _assert_no_driver_text(error: dict[str, Any] | None) -> None:
    """`worker_runs.error` is returned as is by `GET /runs/{id}`: it must say the
    pause could not be recorded, never carry the driver's SQL or constraint text."""
    assert error is not None
    shown = f"{error.get('type')} {error.get('message')}"
    for leak in ("[SQL:", "INSERT INTO", "approval_requests", "ck_", "asyncpg", "sqlalchemy"):
        assert leak not in shown, f"run error leaks {leak!r}: {shown}"


def _assert_the_approval_was_not_found(error: NotFoundError, approval_id: uuid.UUID) -> None:
    """The approval read is what refused, not a later lookup.

    `decide` also reads the run, and `run not found` from worker_runs RLS is
    a NotFoundError too: a bare `pytest.raises(NotFoundError)` stayed green
    with RLS removed from approval_requests (round-2 mutation M15)."""
    assert error.message == "approval request not found"
    assert error.details == {"approval_id": str(approval_id)}


@pytest.mark.parametrize(
    "malformed",
    ["Demo.approve", "demo", "", 5, ["demo.approve.board"]],
    ids=["upper-case", "one-segment", "empty", "number", "list"],
)
async def test_a_malformed_stamp_fails_the_run_and_raises_no_approval(
    urls: RuntimeUrls, worker_config: Path, malformed: object
) -> None:
    """Closed on error (failure-modes #7): not skipped, not coerced, not parked.

    The CHECK is the one owner of the shape; the runner passes the value
    through, and a refused INSERT must end the run rather than leave it at
    `running` with a checkpoint nobody can approve."""
    run = make_run_context()
    stack = Stack(urls.app, worker_config, build_stamping_graph(malformed))

    await stack.runner.start(run_context=run, input_payload={"subject": "x"})

    record = await stack.run_store.get(run, run.run_id)
    assert record.status is RunStatus.FAILED
    assert record.approval_request_id is None
    _assert_no_driver_text(record.error)
    assert await stack.approval_rows_for(run) == 0
    assert "run.failed" in await _audit_actions(stack, run)
    await stack.dispose()


async def test_no_stamp_keeps_todays_rule(urls: RuntimeUrls, worker_config: Path) -> None:
    run = make_run_context()
    stack = Stack(urls.app, worker_config, build_stamping_graph(_ABSENT))
    await stack.runner.start(run_context=run, input_payload={"subject": "x"})
    record = await stack.run_store.get(run, run.run_id)
    assert record.approval_request_id is not None

    decided = await stack.approvals.decide(
        approval_id=record.approval_request_id,
        approve=True,
        comment="",
        context=_decider(tenant=run.tenant_id, workspace=run.workspace_id),
        authorization=ScopeAuthorizationService(),
    )

    assert decided.required_scope is None
    assert decided.status is ApprovalStatus.APPROVED
    assert (await stack.run_store.get(run, run.run_id)).status is RunStatus.COMPLETED
    await stack.dispose()


async def test_the_stamp_decides_who_may_decide(urls: RuntimeUrls, worker_config: Path) -> None:
    run = make_run_context()
    stack = Stack(urls.app, worker_config, build_stamping_graph(BOARD_SCOPE))
    await stack.runner.start(run_context=run, input_payload={"subject": "x"})
    record = await stack.run_store.get(run, run.run_id)
    assert record.status is RunStatus.WAITING_APPROVAL
    approval_id = record.approval_request_id
    assert approval_id is not None
    async with stack.uow_factory(access_context_from_run(run)) as uow:
        stamped = await uow.approvals.get(
            approval_id, workspace_id=run.workspace_id, audience=_sees(access_context_from_run(run))
        )
    assert stamped is not None
    assert stamped.required_scope == BOARD_SCOPE

    async def still_pending() -> None:
        async with stack.uow_factory(access_context_from_run(run)) as uow:
            current = await uow.approvals.get(
                approval_id,
                workspace_id=run.workspace_id,
                audience=_sees(access_context_from_run(run)),
            )
        assert current is not None
        assert current.status is ApprovalStatus.PENDING
        assert await stack.decision_rows_for(run, approval_id) == 0
        assert (await stack.run_store.get(run, run.run_id)).status is RunStatus.WAITING_APPROVAL

    # Another tenant, holding both scopes, under the SAME workspace id (UUIDs
    # are not tenant-bound): only the tenant boundary can refuse it, not the
    # workspace filter of ticket platform-runtime/approval-audit-and-workspace/02.
    with pytest.raises(NotFoundError) as not_found:
        await stack.approvals.decide(
            approval_id=approval_id,
            approve=True,
            comment="",
            context=_decider(BOARD_SCOPE, tenant=TENANT_B, workspace=run.workspace_id),
            authorization=ScopeAuthorizationService(),
        )
    _assert_the_approval_was_not_found(not_found.value, approval_id)
    await still_pending()

    # The right tenant, `approvals.decide` without the stamp: not found, the
    # answer the inbox gives them too (ADR 0004, amendment 2026-10-07).
    with pytest.raises(NotFoundError) as hidden:
        await stack.approvals.decide(
            approval_id=approval_id,
            approve=True,
            comment="",
            context=_decider(tenant=run.tenant_id, workspace=run.workspace_id),
            authorization=ScopeAuthorizationService(),
        )
    _assert_the_approval_was_not_found(hidden.value, approval_id)
    await still_pending()

    # Its requester sees it, and seeing is not deciding: `approvals.decide`
    # without the stamp is refused by name, as before the amendment.
    with pytest.raises(PermissionDeniedError) as refused:
        await stack.approvals.decide(
            approval_id=approval_id,
            approve=True,
            comment="",
            context=_decider(
                tenant=run.tenant_id, workspace=run.workspace_id, principal=run.actor_id
            ),
            authorization=ScopeAuthorizationService(),
        )
    assert refused.value.details["action"] == BOARD_SCOPE
    await still_pending()

    # A holder of the stamp decides, and the run carries on.
    decided = await stack.approvals.decide(
        approval_id=approval_id,
        approve=True,
        comment="đồng ý",
        context=_decider(BOARD_SCOPE, tenant=run.tenant_id, workspace=run.workspace_id),
        authorization=ScopeAuthorizationService(),
    )
    assert decided.status is ApprovalStatus.APPROVED
    assert await stack.decision_rows_for(run, approval_id) == 1
    final = await stack.run_store.get(run, run.run_id)
    assert final.status is RunStatus.COMPLETED
    assert final.result is not None
    assert final.result["dispatched"] is True
    await stack.dispose()


def _platform_admin(*scopes: str, tenant: uuid.UUID, workspace: uuid.UUID) -> AccessContext:
    """As the seed grants the role: `platform.admin` only, and `approvals.decide`
    through `ScopeAuthorizationService`'s admin rule, not as a scope."""
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset({"platform_admin"}),
        scopes=frozenset({"platform.admin", *scopes}),
        plan_id="professional",
    )


async def test_platform_admin_does_not_pass_the_stamp(
    urls: RuntimeUrls, worker_config: Path
) -> None:
    """ADR 0004 (2026-10-06): a platform operator is not the business's board. Same
    tenant and workspace, approve and reject: not found (the amendment of
    2026-10-07: they may not decide it, so they do not see it), nothing written,
    the run still parked. A holder of the scope then decides it as before."""
    run = make_run_context()
    stack = Stack(urls.app, worker_config, build_stamping_graph(BOARD_SCOPE))
    await stack.runner.start(run_context=run, input_payload={"subject": "x"})
    record = await stack.run_store.get(run, run.run_id)
    approval_id = record.approval_request_id
    assert approval_id is not None

    for approve in (True, False):
        with pytest.raises(NotFoundError) as refused:
            await stack.approvals.decide(
                approval_id=approval_id,
                approve=approve,
                comment="vận hành",
                context=_platform_admin(tenant=run.tenant_id, workspace=run.workspace_id),
                authorization=ScopeAuthorizationService(),
            )
        _assert_the_approval_was_not_found(refused.value, approval_id)

    async with stack.uow_factory(access_context_from_run(run)) as uow:
        current = await uow.approvals.get(
            approval_id, workspace_id=run.workspace_id, audience=_sees(access_context_from_run(run))
        )
    assert current is not None
    assert current.status is ApprovalStatus.PENDING
    assert current.version == 1
    assert await stack.decision_rows_for(run, approval_id) == 0
    assert (await stack.run_store.get(run, run.run_id)).status is RunStatus.WAITING_APPROVAL

    decided = await stack.approvals.decide(
        approval_id=approval_id,
        approve=True,
        comment="đồng ý",
        context=_decider(BOARD_SCOPE, tenant=run.tenant_id, workspace=run.workspace_id),
        authorization=ScopeAuthorizationService(),
    )
    assert decided.status is ApprovalStatus.APPROVED
    assert await stack.decision_rows_for(run, approval_id) == 1
    assert (await stack.run_store.get(run, run.run_id)).status is RunStatus.COMPLETED
    await stack.dispose()


async def test_platform_admin_still_decides_an_unstamped_approval(
    urls: RuntimeUrls, worker_config: Path
) -> None:
    run = make_run_context()
    stack = Stack(urls.app, worker_config, build_stamping_graph(_ABSENT))
    await stack.runner.start(run_context=run, input_payload={"subject": "x"})
    record = await stack.run_store.get(run, run.run_id)
    assert record.approval_request_id is not None

    decided = await stack.approvals.decide(
        approval_id=record.approval_request_id,
        approve=True,
        comment="",
        context=_platform_admin(tenant=run.tenant_id, workspace=run.workspace_id),
        authorization=ScopeAuthorizationService(),
    )

    assert decided.required_scope is None
    assert decided.status is ApprovalStatus.APPROVED
    assert await stack.decision_rows_for(run, record.approval_request_id) == 1
    assert (await stack.run_store.get(run, run.run_id)).status is RunStatus.COMPLETED
    await stack.dispose()


async def test_another_tenant_cannot_decide_a_stamped_approval_without_a_run(
    urls: RuntimeUrls, worker_config: Path
) -> None:
    """With no run behind it, the approval read is the only thing between
    another tenant and the decision: worker_runs RLS cannot stand in for it.
    The other tenant names the approval's own workspace id, so the workspace
    filter cannot stand in for the tenant boundary either."""
    run = make_run_context()
    stack = Stack(urls.app, worker_config, build_stamping_graph(BOARD_SCOPE))
    request = ApprovalRequest(
        id=uuid.uuid4(),
        tenant_id=TenantId(run.tenant_id),
        workspace_id=WorkspaceId(run.workspace_id),
        approval_type="demo.dispatch",
        requested_by=UserId(run.actor_id),
        reason="cần duyệt",
        required_scope=BOARD_SCOPE,
    )
    async with stack.uow_factory(access_context_from_run(run)) as uow:
        await uow.approvals.add(request)
        await uow.commit()

    with pytest.raises(NotFoundError) as not_found:
        await stack.approvals.decide(
            approval_id=request.id,
            approve=True,
            comment="",
            context=_decider(BOARD_SCOPE, tenant=TENANT_B, workspace=run.workspace_id),
            authorization=ScopeAuthorizationService(),
        )

    _assert_the_approval_was_not_found(not_found.value, request.id)
    async with stack.uow_factory(access_context_from_run(run)) as uow:
        current = await uow.approvals.get(
            request.id, workspace_id=run.workspace_id, audience=_sees(access_context_from_run(run))
        )
    assert current is not None
    assert current.status is ApprovalStatus.PENDING
    assert current.version == 1
    assert await stack.decision_rows_for(run, request.id) == 0
    await stack.dispose()


async def test_a_malformed_stamp_fails_a_streamed_run_too(
    urls: RuntimeUrls, worker_config: Path
) -> None:
    """A chat turn reaches the same pause through `_settle_stream`."""
    run = make_run_context()
    stack = Stack(urls.app, worker_config, build_stamping_graph("Demo.approve"))

    stream = await stack.runner.stream(run_context=run, input_payload={"subject": "x"})
    async for _chunk in stream:
        pass
    await stack.runner.drain()

    record = await stack.run_store.get(run, run.run_id)
    assert record.status is RunStatus.FAILED
    assert record.approval_request_id is None
    _assert_no_driver_text(record.error)
    assert await stack.approval_rows_for(run) == 0
    await stack.dispose()
