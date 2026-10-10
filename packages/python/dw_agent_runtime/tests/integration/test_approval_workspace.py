"""Integration: approvals, runs and audit stay inside their workspace.

One tenant, two workspaces, real Postgres and the real runner. RLS on
`approval_requests`, `worker_runs` and `audit_events` narrows by tenant only,
so every one of these refusals is the repository's workspace filter, not the
database's (platform-runtime/approval-audit-and-workspace/02). A is a member
of W1, where the run paused; B holds `approvals.decide` in W2.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa
from runtime_harness import TENANT_A, WORKSPACE_A, RuntimeUrls, make_run_context
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dw_agent_runtime.adapters.checkpoint import SqlAlchemyCheckpointSaver
from dw_agent_runtime.adapters.langgraph_runner import LangGraphWorkflowRunner
from dw_agent_runtime.adapters.run_store import RunStatus, SqlWorkerRunStore
from dw_agent_runtime.approval_flow import ApproveAndResumeService
from dw_agent_runtime.autonomy import AutonomyApprovalPolicy
from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.model.budget import RunBudgetLedger
from dw_agent_runtime.ports import WorkflowRunnerPort
from dw_agent_runtime.registry import GraphRegistry, WorkerRegistry
from dw_agent_runtime.testing.demo_graph import DEMO_WORKER_YAML, build_demo_graph
from dw_kernel.errors import NotFoundError
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


STALE_AFTER_SECONDS_LOCAL = 3600
WORKSPACE_W2 = uuid.UUID(int=0xA02)
_PAGE = PageRequest(limit=100, after=None, query=PageQuery(key="test.approval_workspace"))


class _UnmeteredPlan:
    def runs_per_day(self, plan_id: str) -> int | None:
        return None

    def spend_usd_per_day(self, plan_id: str) -> Decimal | None:
        return None


@dataclass
class RecordingRunner:
    """The real runner, plus the `RunContext` each resume was handed."""

    inner: LangGraphWorkflowRunner
    resumed_with: list[RunContext] = field(default_factory=list)

    def hosts(self, *, worker_id: str, worker_version: str, graph_version: str) -> bool:
        return self.inner.hosts(
            worker_id=worker_id, worker_version=worker_version, graph_version=graph_version
        )

    async def resume(
        self, *, run_context: RunContext, run_id: uuid.UUID, resume_payload: dict[str, Any]
    ) -> None:
        self.resumed_with.append(run_context)
        await self.inner.resume(
            run_context=run_context, run_id=run_id, resume_payload=resume_payload
        )


class Stack:
    def __init__(self, app_url: str, worker_config: Path) -> None:
        self.engine = create_async_engine(app_url, poolclass=NullPool)
        sessions = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        graphs = GraphRegistry()
        graphs.register("demo_approval", "1.0.0", build_demo_graph)
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
        self.recorder = RecordingRunner(self.runner)
        self.approvals = ApproveAndResumeService(
            uow_factory=self.uow_factory,
            runner=cast(WorkflowRunnerPort, self.recorder),
            run_store=self.run_store,
            clock=SystemClock(),
            id_generator=Uuid4Generator(),
        )

    async def decision_rows(self, approval_id: uuid.UUID) -> int:
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(TENANT_A)}
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


@pytest.fixture
async def stack(urls: RuntimeUrls, worker_config: Path) -> AsyncIterator[Stack]:
    built = Stack(urls.app, worker_config)
    yield built
    await built.dispose()


def _member(workspace: uuid.UUID, *scopes: str) -> AccessContext:
    return AccessContext(
        tenant_id=TENANT_A,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset({"approver"}),
        scopes=frozenset({"approvals.read", "approvals.decide", "runs.read", *scopes}),
        plan_id="professional",
    )


def _lookup(context: AccessContext, run_id: uuid.UUID) -> RunContext:
    """What `GET /runs/{id}` hands the store: the caller's own context."""
    return RunContext(
        run_id=run_id,
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        actor_id=context.principal_id,
        worker_id="lookup",
        worker_version="0.0.0",
        channel="web",
        plan_id=context.plan_id,
        roles=context.roles,
        scopes=context.scopes,
        trace_id="lookup",
    )


async def _paused_in_w1(stack: Stack) -> tuple[RunContext, uuid.UUID]:
    run = make_run_context(workspace=WORKSPACE_A)
    await stack.runner.start(run_context=run, input_payload={"subject": "W1 secret"})
    record = await stack.run_store.get(run, run.run_id)
    assert record.status is RunStatus.WAITING_APPROVAL
    assert record.approval_request_id is not None
    return run, record.approval_request_id


async def test_another_workspace_neither_sees_nor_decides_the_approval(stack: Stack) -> None:
    run, approval_id = await _paused_in_w1(stack)
    b = _member(WORKSPACE_W2)

    async with stack.uow_factory(b) as uow:
        inbox = await uow.approvals.list_pending(
            _PAGE, workspace_id=b.workspace_id, audience=_sees(b)
        )
        by_id = await uow.approvals.get(approval_id, workspace_id=b.workspace_id, audience=_sees(b))
    assert approval_id not in {request.id for request in inbox.items}
    assert by_id is None

    with pytest.raises(NotFoundError) as not_found:
        await stack.approvals.decide(
            approval_id=approval_id,
            approve=True,
            comment="",
            context=b,
            authorization=ScopeAuthorizationService(),
        )
    # The approval read refused, not the later run lookup (whose own
    # `run not found` would keep a bare `raises(NotFoundError)` green).
    assert not_found.value.message == "approval request not found"
    assert not_found.value.details == {"approval_id": str(approval_id)}

    a = _member(WORKSPACE_A)
    async with stack.uow_factory(a) as uow:
        current = await uow.approvals.get(
            approval_id, workspace_id=a.workspace_id, audience=_sees(a)
        )
    assert current is not None
    assert current.status is ApprovalStatus.PENDING
    assert await stack.decision_rows(approval_id) == 0
    assert (await stack.run_store.get(run, run.run_id)).status is RunStatus.WAITING_APPROVAL
    assert stack.recorder.resumed_with == []


async def test_another_workspace_cannot_decide_an_approval_without_a_run(stack: Stack) -> None:
    """No run behind it: the approval read is the only thing in the way."""
    a = _member(WORKSPACE_A)
    request = ApprovalRequest(
        id=uuid.uuid4(),
        tenant_id=TenantId(TENANT_A),
        workspace_id=WorkspaceId(WORKSPACE_A),
        approval_type="demo.dispatch",
        requested_by=UserId(a.principal_id),
        reason="W1 only",
    )
    async with stack.uow_factory(a) as uow:
        await uow.approvals.add(request)
        await uow.commit()

    with pytest.raises(NotFoundError) as not_found:
        await stack.approvals.decide(
            approval_id=request.id,
            approve=False,
            comment="",
            context=_member(WORKSPACE_W2),
            authorization=ScopeAuthorizationService(),
        )

    assert not_found.value.message == "approval request not found"
    async with stack.uow_factory(a) as uow:
        current = await uow.approvals.get(
            request.id, workspace_id=a.workspace_id, audience=_sees(a)
        )
    assert current is not None
    assert current.status is ApprovalStatus.PENDING
    assert current.version == 1
    assert await stack.decision_rows(request.id) == 0


async def test_the_member_of_the_runs_workspace_decides_and_it_resumes_there(
    stack: Stack,
) -> None:
    run, approval_id = await _paused_in_w1(stack)

    decided = await stack.approvals.decide(
        approval_id=approval_id,
        approve=True,
        comment="ok",
        context=_member(WORKSPACE_A),
        authorization=ScopeAuthorizationService(),
    )

    assert decided.status is ApprovalStatus.APPROVED
    [resumed] = stack.recorder.resumed_with
    assert resumed.workspace_id == WORKSPACE_A
    assert resumed.tenant_id == TENANT_A
    assert (await stack.run_store.get(run, run.run_id)).status is RunStatus.COMPLETED


async def test_another_workspace_cannot_read_the_run_its_timeline_or_its_audit(
    stack: Stack,
) -> None:
    run, _ = await _paused_in_w1(stack)
    b = _member(WORKSPACE_W2)

    with pytest.raises(NotFoundError) as not_found:
        await stack.run_store.get(_lookup(b, run.run_id), run.run_id)
    assert not_found.value.message == "run not found"
    assert not await stack.run_store.thread_belongs_to(
        TENANT_A, WORKSPACE_W2, run.thread_id or run.run_id
    )

    async with stack.uow_factory(b) as uow:
        timeline = await uow.audit.list_for_run(run.run_id, workspace_id=b.workspace_id)
        trail = await uow.audit.list_page(_PAGE, workspace_id=b.workspace_id)
    assert timeline == []
    assert all(event.run_id != run.run_id for event in trail.items)

    # The run's own workspace still reads all of it.
    a = _member(WORKSPACE_A)
    record = await stack.run_store.get(_lookup(a, run.run_id), run.run_id)
    assert record.workspace_id == WORKSPACE_A
    assert await stack.run_store.thread_belongs_to(
        TENANT_A, WORKSPACE_A, run.thread_id or run.run_id
    )
    async with stack.uow_factory(a) as uow:
        timeline = await uow.audit.list_for_run(run.run_id, workspace_id=a.workspace_id)
    assert "run.waiting_approval" in [event.action for event in timeline]
