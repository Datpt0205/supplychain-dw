"""Integration: durable pause at approval, resume with a FRESH runner stack.

"Restart" is simulated by disposing every runtime object (engine, saver,
compiled graph, runner) and rebuilding from scratch — the only shared state is
PostgreSQL, which is exactly the durability claim being verified.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from runtime_harness import TENANT_B, RuntimeUrls, make_run_context
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dw_agent_runtime.adapters.checkpoint import SqlAlchemyCheckpointSaver
from dw_agent_runtime.adapters.langgraph_runner import LangGraphWorkflowRunner
from dw_agent_runtime.adapters.run_store import RunStatus, SqlWorkerRunStore
from dw_agent_runtime.adapters.runtime_tables import run_checkpoints
from dw_agent_runtime.autonomy import AutonomyApprovalPolicy
from dw_agent_runtime.context import access_context_from_run
from dw_agent_runtime.model.budget import RunBudgetLedger
from dw_agent_runtime.registry import GraphRegistry, WorkerRegistry
from dw_agent_runtime.testing.demo_graph import DEMO_WORKER_YAML, build_demo_graph
from dw_kernel.errors import ConflictError
from dw_kernel.pagination import PageQuery, PageRequest
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.adapters.persistence.uow import SqlPlatformUnitOfWorkFactory
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ApprovalAudience, ScopeAuthorizationService

pytestmark = pytest.mark.integration


def _sees(context: AccessContext) -> ApprovalAudience:
    """What `context` may see of the approvals (ADR 0004), as the API asks it."""
    return ApprovalAudience.of(context, ScopeAuthorizationService())


# The shipped threshold (`configs/policies/worker_runs@1.0.0.yaml`). Stated
# rather than loaded: these tests never age a row, so the number only has
# to be a number.
STALE_AFTER_SECONDS_LOCAL = 3600


class _UnmeteredPlan:
    """Any plan, no daily limit — the run quota is not what these tests exercise."""

    def runs_per_day(self, plan_id: str) -> int | None:
        return None

    def spend_usd_per_day(self, plan_id: str) -> Decimal | None:
        return None


class RunnerStack:
    """One 'process' worth of runtime objects."""

    def __init__(self, app_url: str, worker_config: Path) -> None:
        self.engine = create_async_engine(app_url, poolclass=NullPool)
        session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        graphs = GraphRegistry()
        graphs.register("demo_approval", "1.0.0", build_demo_graph)
        workers = WorkerRegistry(graph_registry=graphs)
        workers.load_file(worker_config)
        self.uow_factory = SqlPlatformUnitOfWorkFactory(session_factory)
        self.run_store = SqlWorkerRunStore(
            session_factory, stale_run_after_seconds=STALE_AFTER_SECONDS_LOCAL
        )
        self.runner = LangGraphWorkflowRunner(
            worker_registry=workers,
            graph_registry=graphs,
            checkpoint_saver=SqlAlchemyCheckpointSaver(session_factory),
            run_store=self.run_store,
            uow_factory=self.uow_factory,
            clock=SystemClock(),
            id_generator=Uuid4Generator(),
            allowance=_UnmeteredPlan(),
            budget=RunBudgetLedger(),
            approval_policy=AutonomyApprovalPolicy(),
        )

    async def dispose(self) -> None:
        await self.engine.dispose()


@pytest.fixture
def worker_config(tmp_path: Path) -> Path:
    path = tmp_path / "demo_approval.yaml"
    path.write_text(DEMO_WORKER_YAML, encoding="utf-8")
    return path


async def test_pause_restart_resume_approved(urls: RuntimeUrls, worker_config: Path) -> None:
    context = make_run_context()

    # --- process 1: start, pause at approval ---
    stack1 = RunnerStack(urls.app, worker_config)
    run_id = await stack1.runner.start(
        run_context=context, input_payload={"subject": "Báo cáo quý 3"}
    )
    record = await stack1.run_store.get(context, run_id)
    assert record.status is RunStatus.WAITING_APPROVAL
    assert record.approval_request_id is not None

    # Approval request row really exists (via platform UoW under RLS).
    async with stack1.uow_factory(access_context_from_run(context)) as uow:
        approval = await uow.approvals.get(
            record.approval_request_id,
            workspace_id=context.workspace_id,
            audience=_sees(access_context_from_run(context)),
        )
        assert approval is not None
        assert approval.approval_type == "demo.dispatch"
        assert approval.run_id == run_id
    await stack1.dispose()

    # --- process 2: brand-new stack resumes from the checkpoint ---
    stack2 = RunnerStack(urls.app, worker_config)
    await stack2.runner.resume(
        run_context=context,
        run_id=run_id,
        resume_payload={"approved": True, "comment": "Đồng ý"},
    )
    final = await stack2.run_store.get(context, run_id)
    assert final.status is RunStatus.COMPLETED
    assert final.result is not None
    assert final.result["dispatched"] is True
    assert final.result["approver_comment"] == "Đồng ý"

    # Audit trail covers the full lifecycle.
    async with stack2.uow_factory(access_context_from_run(context)) as uow:
        actions = [
            e.action
            for e in (
                await uow.audit.list_page(
                    PageRequest(limit=50, after=None, query=PageQuery(key="test.audit")),
                    workspace_id=context.workspace_id,
                )
            ).items
        ]
    for expected in ("run.started", "run.waiting_approval", "run.resumed", "run.completed"):
        assert expected in actions
    await stack2.dispose()


async def test_rejected_resume_does_not_dispatch(urls: RuntimeUrls, worker_config: Path) -> None:
    context = make_run_context()
    stack = RunnerStack(urls.app, worker_config)
    run_id = await stack.runner.start(
        run_context=context, input_payload={"subject": "Nhiệm vụ nhạy cảm"}
    )
    await stack.runner.resume(
        run_context=context,
        run_id=run_id,
        resume_payload={"approved": False, "comment": "Từ chối"},
    )
    final = await stack.run_store.get(context, run_id)
    assert final.status is RunStatus.COMPLETED
    assert final.result is not None
    assert final.result["dispatched"] is False
    await stack.dispose()


async def test_resume_requires_waiting_state(urls: RuntimeUrls, worker_config: Path) -> None:
    context = make_run_context()
    stack = RunnerStack(urls.app, worker_config)
    run_id = await stack.runner.start(run_context=context, input_payload={"subject": "x"})
    await stack.runner.resume(run_context=context, run_id=run_id, resume_payload={"approved": True})
    with pytest.raises(ConflictError, match="not waiting"):
        await stack.runner.resume(
            run_context=context, run_id=run_id, resume_payload={"approved": True}
        )
    await stack.dispose()


async def test_checkpoints_are_tenant_isolated(urls: RuntimeUrls, worker_config: Path) -> None:
    context = make_run_context()
    stack = RunnerStack(urls.app, worker_config)
    await stack.runner.start(run_context=context, input_payload={"subject": "bí mật A"})

    engine = create_async_engine(urls.app, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            async with conn.begin():
                await conn.execute(
                    text("SELECT set_config('app.tenant_id', :t, true)"),
                    {"t": str(TENANT_B)},
                )
                rows = (await conn.execute(sa.select(run_checkpoints.c.thread_id))).all()
                assert rows == [], "tenant B must not see tenant A checkpoints"
    finally:
        await engine.dispose()
    await stack.dispose()


async def test_runs_can_share_one_checkpoint_thread(urls: RuntimeUrls, worker_config: Path) -> None:
    """Conversation workers keep many runs on one thread."""
    thread_id = uuid.uuid4()
    stack = RunnerStack(urls.app, worker_config)

    first = make_run_context(thread_id=thread_id)
    await stack.runner.start(run_context=first, input_payload={"subject": "lượt 1"})
    await stack.runner.resume(
        run_context=first, run_id=first.run_id, resume_payload={"approved": True}
    )

    second = make_run_context(thread_id=thread_id)
    await stack.runner.start(run_context=second, input_payload={"subject": "lượt 2"})

    assert (await stack.run_store.get(second, second.run_id)).thread_id == thread_id
    async with stack.engine.connect() as conn, conn.begin():
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(first.tenant_id)}
        )
        threads = (
            await conn.execute(
                sa.select(run_checkpoints.c.thread_id)
                .where(run_checkpoints.c.thread_id.in_([thread_id, first.run_id, second.run_id]))
                .distinct()
            )
        ).scalars()
        assert list(threads) == [thread_id], "both turns must checkpoint on one thread"
    await stack.dispose()


async def test_worker_runs_visible_only_in_own_tenant(
    urls: RuntimeUrls, worker_config: Path
) -> None:
    context = make_run_context()
    stack = RunnerStack(urls.app, worker_config)
    run_id = await stack.runner.start(run_context=context, input_payload={"subject": "x"})

    # The run's own workspace id under another tenant: only the tenant boundary
    # can refuse, not `get`'s workspace filter (approval-audit-and-workspace/02).
    foreign = make_run_context(tenant=TENANT_B, workspace=context.workspace_id)
    from dw_kernel.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await stack.run_store.get(foreign, run_id)
    await stack.dispose()
