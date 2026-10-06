"""Integration: a run always reaches a terminal state, however it ended.

Covers the three ways a run used to get stranded at ``running`` for ever: the
graph raising, the caller abandoning a stream part-way, and the driving task
being cancelled by a shutdown.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa
from langgraph.graph import END, START, StateGraph
from runtime_harness import RuntimeUrls, make_run_context
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dw_agent_runtime.adapters.checkpoint import SqlAlchemyCheckpointSaver
from dw_agent_runtime.adapters.langgraph_runner import LangGraphWorkflowRunner
from dw_agent_runtime.adapters.run_store import RunStatus, SqlWorkerRunStore
from dw_agent_runtime.autonomy import AUTONOMY_POLICY_VERSION, AutonomyApprovalPolicy
from dw_agent_runtime.context import access_context_from_run
from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.model.budget import RunBudgetLedger
from dw_agent_runtime.registry import GraphRegistry, WorkerRegistry
from dw_agent_runtime.testing.demo_graph import (
    DEMO_WORKER,
    DEMO_WORKER_YAML,
    DemoState,
    build_demo_graph,
)
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

BOOM = "nhà cung cấp mô hình từ chối"


async def _recorded_actions(uow: Any, workspace_id: uuid.UUID) -> list[str]:
    """Every action on the workspace's audit trail, newest first."""
    page = await uow.audit.list_page(
        PageRequest(limit=50, after=None, query=PageQuery(key="test.audit")),
        workspace_id=workspace_id,
    )
    return [event.action for event in page.items]


def build_exploding_graph() -> StateGraph:  # type: ignore[type-arg]
    def _explode(state: DemoState) -> DemoState:
        raise RuntimeError(BOOM)

    graph: StateGraph = StateGraph(DemoState)  # type: ignore[type-arg]
    graph.add_node("explode", _explode)
    graph.add_edge(START, "explode")
    graph.add_edge("explode", END)
    return graph


def build_unending_graph() -> StateGraph:  # type: ignore[type-arg]
    """Emits once, then outlives the process it runs in so shutdown cancels it.

    The first node exists so a test can tell that the run is genuinely under way
    before cancelling it. Cancelling the task before its body has run is a
    different thing entirely -- the coroutine never starts, so no handler of
    ours ever executes.
    """

    def _announce(state: DemoState) -> DemoState:
        return {"prepared_message": "đang chạy"}

    async def _never_finishes(state: DemoState) -> DemoState:
        await asyncio.sleep(60)
        return {"dispatched": True}

    graph: StateGraph = StateGraph(DemoState)  # type: ignore[type-arg]
    graph.add_node("announce", _announce)
    graph.add_node("never_finishes", _never_finishes)
    graph.add_edge(START, "announce")
    graph.add_edge("announce", "never_finishes")
    graph.add_edge("never_finishes", END)
    return graph


class _UnmeteredPlan:
    """Any plan, no daily limit — the run quota is not what these tests exercise."""

    def runs_per_day(self, plan_id: str) -> int | None:
        return None

    def spend_usd_per_day(self, plan_id: str) -> Decimal | None:
        return None


class RunnerStack:
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

    async def dispose(self) -> None:
        await self.engine.dispose()


@pytest.fixture
def worker_config(tmp_path: Path) -> Path:
    path = tmp_path / "demo_approval.yaml"
    path.write_text(DEMO_WORKER_YAML, encoding="utf-8")
    return path


async def test_a_graph_that_raises_leaves_the_run_failed(
    urls: RuntimeUrls, worker_config: Path
) -> None:
    context = make_run_context()
    stack = RunnerStack(urls.app, worker_config, build_exploding_graph)

    with pytest.raises(RuntimeError, match=BOOM):
        await stack.runner.start(run_context=context, input_payload={"subject": "x"})

    record = await stack.run_store.get(context, context.run_id)
    assert record.status is RunStatus.FAILED
    assert record.error is not None
    assert record.error["type"] == "RuntimeError"
    assert BOOM in record.error["message"]

    async with stack.uow_factory(access_context_from_run(context)) as uow:
        actions = await _recorded_actions(uow, context.workspace_id)
    assert "run.failed" in actions
    await stack.dispose()


async def test_abandoning_a_stream_still_records_the_approval(
    urls: RuntimeUrls, worker_config: Path
) -> None:
    """A client that closes the tab must not strand the run.

    The approval request is created after the graph pauses, so a stream that is
    dropped mid-flight used to leave the graph paused in its checkpoint with no
    platform row pointing at it and nobody able to approve.
    """
    context = make_run_context()
    stack = RunnerStack(urls.app, worker_config, build_demo_graph)

    # Breaking out of the loop is what a dropped connection does: the generator
    # is closed part-way rather than drained.
    stream = await stack.runner.stream(run_context=context, input_payload={"subject": "Báo cáo"})
    async for _chunk in stream:
        break
    await cast(AsyncGenerator[dict[str, Any], None], stream).aclose()
    await stack.runner.drain()

    record = await stack.run_store.get(context, context.run_id)
    assert record.status is RunStatus.WAITING_APPROVAL
    assert record.approval_request_id is not None

    async with stack.uow_factory(access_context_from_run(context)) as uow:
        approval = await uow.approvals.get(
            record.approval_request_id,
            workspace_id=context.workspace_id,
            audience=_sees(access_context_from_run(context)),
        )
    assert approval is not None
    assert approval.approval_type == "demo.dispatch"
    await stack.dispose()


async def _wait_for(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(30):
        while not condition():
            await asyncio.sleep(0.01)


async def test_cancelling_a_run_settles_it_instead_of_reporting_success(
    urls: RuntimeUrls, worker_config: Path
) -> None:
    """SIGTERM used to leave the row at `running` and tell the reader `end`.

    ``CancelledError`` is a ``BaseException``, so ``except Exception`` never saw
    it, while the ``finally`` still published a clean end-of-stream.
    """
    context = make_run_context()
    stack = RunnerStack(urls.app, worker_config, build_unending_graph)

    stream = await stack.runner.stream(run_context=context, input_payload={"subject": "x"})
    seen: list[dict[str, Any]] = []

    async def read_until_it_stops() -> None:
        async for chunk in stream:
            seen.append(chunk)

    reader = asyncio.create_task(read_until_it_stops())
    await _wait_for(lambda: bool(seen))

    for task in tuple(stack.runner._running):
        task.cancel()
    await stack.runner.drain()

    # The reader must learn the run died; returning normally would mean the
    # client was told the turn had finished.
    with pytest.raises(asyncio.CancelledError):
        await reader

    record = await stack.run_store.get(context, context.run_id)
    assert record.status is RunStatus.CANCELLED
    assert record.error is not None
    assert record.error["type"] == "CancelledError"

    async with stack.uow_factory(access_context_from_run(context)) as uow:
        actions = await _recorded_actions(uow, context.workspace_id)
    assert "run.cancelled" in actions
    await stack.dispose()


class RefusingRunStore:
    """The real store, except that settling a run always fails.

    Stands in for the database being in trouble at the exact moment the runner
    tries to record that a run died - which is when `_audit` turns any failure
    into an `InfrastructureError`.
    """

    def __init__(self, real: SqlWorkerRunStore) -> None:
        self._real = real

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    async def set_status(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("cơ sở dữ liệu không ghi được")


async def test_a_reader_is_told_even_when_recording_the_failure_fails(
    urls: RuntimeUrls, worker_config: Path
) -> None:
    """The failure path used to be able to fail, silently, and hang the browser.

    `_fail` writes to the database, so a blip there raised straight past the
    line that publishes the error - and the reader was left awaiting a queue
    nothing would ever be put into again. The turn was over; the person waiting
    was never told, and never would be.
    """
    context = make_run_context()
    stack = RunnerStack(urls.app, worker_config, build_exploding_graph)
    stack.runner.run_store = cast(SqlWorkerRunStore, RefusingRunStore(stack.run_store))

    stream = await stack.runner.stream(run_context=context, input_payload={"subject": "x"})

    # The timeout IS the assertion: before the fix this iterator never returned.
    async with asyncio.timeout(30):
        with pytest.raises(RuntimeError, match=BOOM):
            async for _chunk in stream:
                pass

        # And `drain` must return too. It is called on shutdown, and this is
        # precisely the window it used to spin in: the task has finished but
        # its done-callback has not run, so the set is not empty yet.
        await stack.runner.drain()

    await stack.dispose()


async def _age_run(stack: RunnerStack, context: RunContext, seconds: int) -> None:
    """Push a run's `updated_at` into the past, the way time would.

    Raw SQL and the DATABASE clock, because that is what the reaper compares
    against - `updated_at` is written with `now()` on the server, and aging it
    from Python would test a comparison the code does not make.
    """
    async with stack.engine.begin() as connection:
        await connection.execute(
            sa.text("SELECT set_config('app.tenant_id', :tenant, true)"),
            {"tenant": str(context.tenant_id)},
        )
        await connection.execute(
            sa.text(
                "UPDATE platform.worker_runs "
                "SET updated_at = now() - make_interval(secs => :age) WHERE id = :run"
            ),
            {"age": seconds, "run": context.run_id},
        )


async def test_a_thread_stranded_by_a_hard_kill_is_freed_by_the_next_turn(
    urls: RuntimeUrls, worker_config: Path
) -> None:
    """SIGKILL runs no handler, so nothing in the process can settle the row.

    `uq_worker_runs_active_thread` then refused every later turn on that
    conversation with 409 - for ever, with no way back. One eviction locked a
    conversation permanently.
    """
    stack = RunnerStack(urls.app, worker_config, build_demo_graph)
    thread = uuid.uuid4()
    killed = make_run_context(thread_id=thread)
    await stack.run_store.create(killed, worker=DEMO_WORKER, input_payload={"subject": "x"})
    await _age_run(stack, killed, STALE_AFTER_SECONDS_LOCAL + 60)

    # Same thread, a person trying again.
    nxt = make_run_context(thread_id=thread)
    await stack.run_store.create(nxt, worker=DEMO_WORKER, input_payload={"subject": "y"})

    assert (await stack.run_store.get(nxt, nxt.run_id)).status is RunStatus.RUNNING
    reaped = await stack.run_store.get(killed, killed.run_id)
    assert reaped.status is RunStatus.FAILED
    assert reaped.error is not None
    assert reaped.error["type"] == "StaleRun"
    await stack.dispose()


async def test_a_run_that_is_merely_slow_is_not_reaped(
    urls: RuntimeUrls, worker_config: Path
) -> None:
    """The threshold is not a lease: `updated_at` does not move while a run works.

    So a turn still going is indistinguishable from a dead one except by age,
    and reaping too eagerly would put two runs on one checkpoint thread - the
    exact thing the index exists to prevent.
    """
    stack = RunnerStack(urls.app, worker_config, build_demo_graph)
    thread = uuid.uuid4()
    busy = make_run_context(thread_id=thread)
    await stack.run_store.create(busy, worker=DEMO_WORKER, input_payload={"subject": "x"})
    await _age_run(stack, busy, STALE_AFTER_SECONDS_LOCAL - 60)

    nxt = make_run_context(thread_id=thread)
    with pytest.raises(ConflictError, match="already has a turn in flight"):
        await stack.run_store.create(nxt, worker=DEMO_WORKER, input_payload={"subject": "y"})

    assert (await stack.run_store.get(busy, busy.run_id)).status is RunStatus.RUNNING
    await stack.dispose()


async def test_a_thread_waiting_for_approval_is_never_reaped(
    urls: RuntimeUrls, worker_config: Path
) -> None:
    """A pause is not staleness, however long a person takes to answer it.

    The reaper is keyed on `running` alone, so this row is out of its reach by
    construction. Which of the two conflicts the caller then sees is the
    approval lookup's business, asserted by whichever context owns the
    conversation - this one is only about the row surviving.
    """
    stack = RunnerStack(urls.app, worker_config, build_demo_graph)
    thread = uuid.uuid4()
    parked = make_run_context(thread_id=thread)
    await stack.run_store.create(parked, worker=DEMO_WORKER, input_payload={"subject": "x"})
    await stack.run_store.set_status(parked, parked.run_id, RunStatus.WAITING_APPROVAL)
    await _age_run(stack, parked, STALE_AFTER_SECONDS_LOCAL * 10)

    nxt = make_run_context(thread_id=thread)
    with pytest.raises(ConflictError):
        await stack.run_store.create(nxt, worker=DEMO_WORKER, input_payload={"subject": "y"})

    assert (await stack.run_store.get(parked, parked.run_id)).status is RunStatus.WAITING_APPROVAL
    await stack.dispose()


async def test_only_one_run_can_claim_a_thread(urls: RuntimeUrls, worker_config: Path) -> None:
    """The rule the old read-then-insert check could not actually enforce.

    Both callers used to read "no active run" and both insert, which is exactly
    what a double-clicked send does. The claim is now the insert itself.
    """
    thread = uuid.uuid4()
    first = make_run_context(thread_id=thread)
    second = make_run_context(thread_id=thread)
    stack = RunnerStack(urls.app, worker_config, build_demo_graph)

    outcomes: list[AsyncIterator[dict[str, object]] | BaseException] = list(
        await asyncio.gather(
            stack.runner.stream(run_context=first, input_payload={"subject": "một"}),
            stack.runner.stream(run_context=second, input_payload={"subject": "hai"}),
            return_exceptions=True,
        )
    )
    conflicts = [o for o in outcomes if isinstance(o, ConflictError)]
    streams = [o for o in outcomes if not isinstance(o, BaseException)]

    assert len(conflicts) == 1, outcomes
    assert len(streams) == 1
    await cast(AsyncGenerator[dict[str, Any], None], streams[0]).aclose()
    await stack.runner.drain()
    await stack.dispose()


async def test_a_finished_run_frees_the_thread(urls: RuntimeUrls, worker_config: Path) -> None:
    """The index is partial, so settling a run must release its thread."""
    thread = uuid.uuid4()
    stack = RunnerStack(urls.app, worker_config, build_exploding_graph)

    first = make_run_context(thread_id=thread)
    with pytest.raises(RuntimeError, match=BOOM):
        await stack.runner.start(run_context=first, input_payload={"subject": "một"})
    assert (await stack.run_store.get(first, first.run_id)).status is RunStatus.FAILED

    second = make_run_context(thread_id=thread)
    with pytest.raises(RuntimeError, match=BOOM):
        await stack.runner.start(run_context=second, input_payload={"subject": "hai"})
    await stack.dispose()


# ------------------------------------------------------------ spend ledger --
#
# The ledger keeps per-run spend in the process, keyed by run id, and nothing ever
# removed an entry: a worker that ran for a month held one for every run of that
# month. Each test seeds the entry BEFORE the run, because the demo graph calls no
# model and so records no spend of its own — without the seed, "freed" and "never
# added" look identical and a runner that forgot nothing would still pass.


def _seed_spend(stack: RunnerStack, context: RunContext) -> None:
    stack.runner.budget.record(context.run_id, input_tokens=10, output_tokens=5, cost_usd=0.01)


async def test_a_failed_run_frees_its_spend_entry(urls: RuntimeUrls, worker_config: Path) -> None:
    context = make_run_context()
    stack = RunnerStack(urls.app, worker_config, build_exploding_graph)
    _seed_spend(stack, context)

    with pytest.raises(RuntimeError, match=BOOM):
        await stack.runner.start(run_context=context, input_payload={"subject": "x"})

    assert context.run_id not in stack.runner.budget.spend
    await stack.dispose()


async def test_a_completed_run_frees_its_spend_entry(
    urls: RuntimeUrls, worker_config: Path
) -> None:
    context = make_run_context()
    stack = RunnerStack(urls.app, worker_config, build_demo_graph)
    run_id = await stack.runner.start(run_context=context, input_payload={"subject": "x"})
    _seed_spend(stack, context)

    await stack.runner.resume(run_context=context, run_id=run_id, resume_payload={"approved": True})

    assert (await stack.run_store.get(context, run_id)).status is RunStatus.COMPLETED
    assert run_id not in stack.runner.budget.spend
    await stack.dispose()


async def test_a_paused_run_keeps_its_spend_entry(urls: RuntimeUrls, worker_config: Path) -> None:
    """A pause is not an end. Freeing the entry here would let a run reset its own
    ceiling by pausing and being resumed — spend that reaches the limit, a pause,
    and the next step starts again from zero."""
    context = make_run_context()
    stack = RunnerStack(urls.app, worker_config, build_demo_graph)
    _seed_spend(stack, context)

    run_id = await stack.runner.start(run_context=context, input_payload={"subject": "x"})

    assert (await stack.run_store.get(context, run_id)).status is RunStatus.WAITING_APPROVAL
    assert stack.runner.budget.spend[run_id].input_tokens == 10
    await stack.dispose()


# --------------------------------------------------------------- autonomy --
#
# The level is resolved by the runner — the lower of the worker's declared level
# and the tenant's ceiling — and stamped on the row, where a resume replays it
# from. Asserted against real SQL: the columns and their CHECK constraints are
# migration 0006, and a stamp that never reached the row would replay as None.


@pytest.mark.parametrize(("ceiling", "expected"), [("A4", "A2"), ("A1", "A1")])
async def test_a_run_row_carries_the_level_the_runner_resolved(
    urls: RuntimeUrls, worker_config: Path, ceiling: str, expected: str
) -> None:
    """The demo worker is declared A2. No tenant ceiling leaves it at A2; a tenant
    ceiling of A1 holds it there — and the row records which one it ran at."""
    context = make_run_context().model_copy(
        update={"autonomy_ceiling": ceiling, "autonomy_level": None}
    )
    stack = RunnerStack(urls.app, worker_config, build_demo_graph)

    run_id = await stack.runner.start(run_context=context, input_payload={"subject": "x"})

    record = await stack.run_store.get(context, run_id)
    assert record.autonomy_level == expected
    assert record.approval_policy_version == AUTONOMY_POLICY_VERSION
    await stack.dispose()


async def test_the_database_refuses_a_run_level_nobody_can_read(
    urls: RuntimeUrls, worker_config: Path
) -> None:
    """A CHECK constraint, not only application validation: a level the runtime does
    not recognise is one nobody can say what it permits."""
    context = make_run_context()
    stack = RunnerStack(urls.app, worker_config, build_demo_graph)
    run_id = await stack.runner.start(run_context=context, input_payload={"subject": "x"})

    with pytest.raises(sa.exc.IntegrityError, match="ck_worker_runs_autonomy_level"):
        async with stack.engine.begin() as connection:
            await connection.execute(
                sa.text("SELECT set_config('app.tenant_id', :tenant, true)"),
                {"tenant": str(context.tenant_id)},
            )
            await connection.execute(
                sa.text("UPDATE platform.worker_runs SET autonomy_level = 'A9' WHERE id = :run"),
                {"run": run_id},
            )
    await stack.dispose()
