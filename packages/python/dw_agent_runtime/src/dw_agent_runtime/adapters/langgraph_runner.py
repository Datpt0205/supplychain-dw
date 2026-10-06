"""LangGraph implementation of ``WorkflowRunnerPort``.

Responsibilities:
- compile registered graphs with the tenant-aware checkpoint saver;
- persist run lifecycle in platform.worker_runs;
- map LangGraph interrupts to platform ApprovalRequests (pause);
- resume a durable run from its checkpoint after approval — including after a
  full process restart, since all state lives in PostgreSQL.

LangGraph never leaks outside this adapter (import-linter enforced).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, timedelta
from typing import Any, cast

from langgraph.store.base import BaseStore
from langgraph.types import Command

from dw_agent_runtime.adapters.checkpoint import SqlAlchemyCheckpointSaver
from dw_agent_runtime.adapters.langchain_usage import LangchainUsageMeter
from dw_agent_runtime.adapters.run_store import (
    TERMINAL_STATUSES,
    RunStatus,
    SqlWorkerRunStore,
)
from dw_agent_runtime.adapters.spend_guard import SqlSpendGuardStore
from dw_agent_runtime.autonomy import AutonomyApprovalPolicy, lower_autonomy
from dw_agent_runtime.context import access_context_from_run
from dw_agent_runtime.contracts import RunContext, WorkerDefinition
from dw_agent_runtime.model.budget import RunBudgetLedger
from dw_agent_runtime.ports import RunAllowancePort
from dw_agent_runtime.registry import GraphRegistry, WorkerRegistry
from dw_kernel.errors import (
    ConflictError,
    InfrastructureError,
    NotFoundError,
    QuotaExceededError,
)
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import IdGenerator, UtcClock
from dw_observability.metrics import DW_RUN_TOTAL
from dw_observability.telemetry import NullTelemetry, TelemetryPort
from dw_platform.application.ports import PlatformUnitOfWorkFactory
from dw_platform.domain.approval import ApprovalRequest
from dw_platform.domain.audit import AuditEvent

logger = logging.getLogger(__name__)

# How long Stop waits for a cancelled run to record itself. Settling is one row
# write and one audit row; past this the request answers and the reaper covers
# a run that never settled.
_CANCEL_SETTLE_SECONDS = 10
_ERROR_MESSAGE_LIMIT = 500

# Enough to absorb a slow reader on a normal turn without letting an absent one
# accumulate a whole run's tokens: a turn streams a few hundred chunks, and a
# reader that has fallen this far behind is not coming back.
_STREAM_BACKLOG_LIMIT = 256


def _publish(queue: asyncio.Queue[tuple[str, Any]], item: tuple[str, Any]) -> None:
    """Hand an event to the reader without ever blocking the run on it.

    A run outlives its reader by design, so a client that closes its tab leaves
    nobody draining. Dropping the oldest display chunk is safe -- the transcript
    is the record of what was said, not this queue -- and it keeps the newest
    output, which is what a reconnecting reader wants.
    """
    if queue.full():
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()
    queue.put_nowait(item)


def _jsonable(value: Any) -> Any:
    """Best-effort conversion of graph state to JSON-storable data."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items() if not str(k).startswith("__")}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


@dataclass
class LangGraphWorkflowRunner:
    """Implements ``WorkflowRunnerPort`` on LangGraph + PostgreSQL."""

    worker_registry: WorkerRegistry
    graph_registry: GraphRegistry
    checkpoint_saver: SqlAlchemyCheckpointSaver
    run_store: SqlWorkerRunStore
    uow_factory: PlatformUnitOfWorkFactory
    clock: UtcClock
    id_generator: IdGenerator
    # Injected, never defaulted, for the same reason `stale_run_after_seconds`
    # is: a default would be "unlimited", and a metering hole that ships quietly
    # is the one bug in this file a customer finds before we do.
    allowance: RunAllowancePort
    # The SAME ledger the gateway and every agent's budget middleware hold.
    # Required rather than defaulted: a default would be a second ledger, and the
    # runner would free entries in one while spend accumulated in the other.
    budget: RunBudgetLedger
    # Whose version is stamped on every run. The SAME instance the executor and
    # the agent's middleware decide with, so the stamp names the rules that
    # were actually applied.
    approval_policy: AutonomyApprovalPolicy
    store: BaseStore | None = None
    release_manifest_ref: str | None = None
    telemetry: TelemetryPort = field(default_factory=NullTelemetry)
    # F6: what the graph's model calls cost. None keeps old wirings valid;
    # a run under None simply stays off the ledger, as every run did before.
    usage_meter: LangchainUsageMeter | None = None
    # The daily spend guard (Ops hardening Phase 3). Same shape as usage_meter:
    # None keeps every wiring that predates this field valid, and a run without
    # it simply stays off the guard, as every run did before this existed.
    spend_store: SqlSpendGuardStore | None = None
    _compiled: dict[tuple[str, str], Any] = field(default_factory=dict)
    # Holds a reference to every in-flight streamed run; without one the task is
    # garbage-collectable mid-run.
    _running: set[asyncio.Task[None]] = field(default_factory=set)
    # The streamed run each thread has in flight in THIS process, so a reader
    # who presses Stop can end the run itself and not only their own view of it.
    _by_thread: dict[uuid.UUID, asyncio.Task[None]] = field(default_factory=dict)

    def _span_attributes(
        self,
        run_context: RunContext,
        run_id: uuid.UUID,
        graph_version: str,
        worker: WorkerDefinition | None = None,
    ) -> dict[str, object]:
        """Safe identifiers only — never prompt/content payloads (section 21.2).

        Every artifact version the run resolved, not only the graph's. A trace
        that says which worker ran but not which prompt bundle or toolset it ran
        under cannot answer the question traces exist for: "this lead scored
        differently last week — what changed?" `CLAUDE.md` section Observability asks
        for all of them, and they cost one string each.
        """
        attributes: dict[str, object] = {
            "dw.tenant_id": str(run_context.tenant_id),
            "dw.workspace_id": str(run_context.workspace_id),
            "dw.run_id": str(run_id),
            "dw.worker_id": run_context.worker_id,
            "dw.worker_version": run_context.worker_version,
            "dw.graph_version": graph_version,
            "dw.trace_id": run_context.trace_id,
            "dw.release_manifest_ref": self.release_manifest_ref,
            # What woke the run, so a spike in runs can be traced to its cause
            # rather than to a count.
            "dw.channel": run_context.channel,
        }
        if run_context.subject_ref:
            attributes["dw.subject_ref"] = run_context.subject_ref
        if worker is not None:
            attributes["dw.prompt_bundle_version"] = worker.prompt_bundle_version
            attributes["dw.toolset_version"] = worker.toolset_version
            attributes["dw.policy_version"] = worker.policy_version
            attributes["dw.model_profile"] = worker.default_model_profile
        return attributes

    def hosts(self, *, worker_id: str, worker_version: str, graph_version: str) -> bool:
        try:
            self.graph_registry.resolve(worker_id, graph_version)
            self.worker_registry.resolve(worker_id, worker_version)
        except NotFoundError:
            return False
        return True

    def _graph(self, worker_id: str, graph_version: str) -> Any:
        key = (worker_id, graph_version)
        if key not in self._compiled:
            factory = self.graph_registry.resolve(worker_id, graph_version)
            self._compiled[key] = factory().compile(
                checkpointer=self.checkpoint_saver, store=self.store
            )
        return self._compiled[key]

    def _config(
        self,
        run_context: RunContext,
        run_id: uuid.UUID,
        recursion_limit: int,
        callbacks: list[Any] | None = None,
    ) -> dict[str, Any]:
        config: dict[str, Any] = {
            "recursion_limit": recursion_limit,
            "configurable": {
                "thread_id": str(run_context.thread_id or run_id),
                "tenant_id": str(run_context.tenant_id),
                "workspace_id": str(run_context.workspace_id),
            },
        }
        if callbacks:
            # Parent-level callbacks propagate to every model call in the
            # graph, subgraphs included — the one seam that meters them all.
            config["callbacks"] = callbacks
        return config

    @contextlib.asynccontextmanager
    async def _metered(
        self, run_context: RunContext, worker: WorkerDefinition
    ) -> AsyncIterator[list[Any] | None]:
        if self.usage_meter is None:
            yield None
            return
        prompt_id, prompt_version = _loop_prompt(worker)
        async with self.usage_meter.track(
            run_context,
            profile_id=worker.default_model_profile,
            task="agent_loop",
            prompt_id=prompt_id,
            prompt_version=prompt_version,
        ) as callbacks:
            yield list(callbacks)

    def _with_autonomy(self, run_context: RunContext, worker: WorkerDefinition) -> RunContext:
        """Resolve the level this run runs at — once, here, where the run begins.

        The lower of what the worker was built for and what its tenant allows. A
        tenant can hold a worker below its design and never lift it above.

        Only at start. `resume` does not come through here: a paused run carries
        the level it started with, replayed from its row by the approval flow, so
        a ceiling lowered while it waited does not rewrite what it was allowed.
        """
        return run_context.model_copy(
            update={
                "autonomy_level": lower_autonomy(
                    worker.autonomy_level, run_context.autonomy_ceiling
                ),
                "approval_policy_version": self.approval_policy.policy_version,
            }
        )

    async def _require_run_allowance(self, run_context: RunContext) -> None:
        """Refuse a run the tenant's plan has no allowance left for today.

        Here rather than at the API, because the API is not the only door: a
        worker reacting to an inbound event starts runs nobody clicked, and a
        quota enforced on one door only is a quota a connector can walk around.
        This is the single place a run begins.

        The plan comes from `run_context`, which carries what the requester was
        entitled to when the turn started — the same stamp a resume replays. A
        resume does not pass through here at all: the run was already counted
        when it started, and charging it again would let an approval a manager
        signs on Tuesday be refused by Tuesday's quota.

        Not transactional, and deliberately so: counting is a read, and holding
        a lock across the whole start path to make the count exact would
        serialise every run a tenant makes. The slack is bounded by how many
        runs one tenant starts in the same instant, which is the difference
        between 200 and 203 a day, not between 200 and unlimited.
        """
        day_start = (
            self.clock.now().astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        )
        limit = self.allowance.runs_per_day(run_context.plan_id)
        if limit is None:
            return
        used = await self.run_store.started_since(run_context.tenant_id, day_start)
        if used < limit:
            return
        raise QuotaExceededError(
            "hôm nay đã dùng hết số lượt chạy của gói; thử lại sau 00:00 UTC"
            " hoặc nâng gói để có thêm lượt",
            details={
                "quota": "runs_per_day",
                "limit": str(limit),
                "used": str(used),
                "plan_id": run_context.plan_id,
                "resets_at": (day_start + timedelta(days=1)).isoformat(),
            },
        )

    async def _require_spend_allowance(self, run_context: RunContext) -> None:
        """Refuse a run once the tenant has spent its plan's daily ceiling.

        Same shape and same slack as `_require_run_allowance`: not
        transactional, so a burst of runs starting in the same instant can
        overshoot by whatever they spend before the next one checks — bounded
        by how much one tenant spends in an instant, not by the ceiling being
        meaningless.

        `spend_store` is `None` for any wiring that predates this guard, and
        every plan ships `spend_usd_per_day=None` today (no dollar thresholds
        decided yet) — both mean this returns immediately, same as before this
        existed. Recording still runs either way; only the gate is off.
        """
        if self.spend_store is None:
            return
        limit = self.allowance.spend_usd_per_day(run_context.plan_id)
        if limit is None:
            return
        today = self.clock.now().astimezone(UTC).date()
        spent = await self.spend_store.spend_today(run_context.tenant_id, today)
        if spent < limit:
            return
        raise QuotaExceededError(
            "hôm nay đã dùng hết trần chi tiêu của gói; thử lại sau 00:00 UTC"
            " hoặc nâng gói để có thêm trần",
            details={
                "quota": "spend_usd_per_day",
                "limit": str(limit),
                "used": str(spent),
                "plan_id": run_context.plan_id,
            },
        )

    async def start(
        self,
        *,
        run_context: RunContext,
        input_payload: dict[str, Any],
    ) -> uuid.UUID:
        worker = self.worker_registry.resolve(run_context.worker_id, run_context.worker_version)
        run_context = self._with_autonomy(run_context, worker.definition)
        await self._require_run_allowance(run_context)
        await self._require_spend_allowance(run_context)
        graph = self._graph(worker.definition.worker_id, worker.definition.graph_version)

        await self.run_store.create(
            run_context,
            worker=worker.definition,
            input_payload=input_payload,
            release_manifest_ref=self.release_manifest_ref,
        )
        await self._audit(run_context, "run.started", str(run_context.run_id))

        attributes = self._span_attributes(
            run_context,
            run_context.run_id,
            worker.definition.graph_version,
            worker.definition,
        )
        with self.telemetry.span("dw.run.start", attributes):
            try:
                async with self._metered(run_context, worker.definition) as callbacks:
                    state = await graph.ainvoke(
                        input_payload,
                        self._config(
                            run_context,
                            run_context.run_id,
                            worker.definition.recursion_limit,
                            callbacks,
                        ),
                        context=run_context,
                    )
            except Exception as exc:
                await self._fail(run_context, run_context.run_id, exc)
                raise
        await self._handle_outcome(run_context, run_context.run_id, state)
        return run_context.run_id

    async def stream(
        self,
        *,
        run_context: RunContext,
        input_payload: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        """Same lifecycle as ``start``, but the caller sees progress as it happens.

        Awaiting this claims the thread and starts the run; the iterator it
        returns only observes. Claiming eagerly is what lets a refused claim
        reach the caller as a 409 — inside the iterator it would arrive after
        the response headers had already been written.

        The graph runs on its own task, so a client closing its tab does not
        stop it mid-step and strand a paused checkpoint nobody can approve.
        Use ``drain`` to wait for runs whose reader left before shutting down.
        """
        worker = self.worker_registry.resolve(run_context.worker_id, run_context.worker_version)
        run_context = self._with_autonomy(run_context, worker.definition)
        await self._require_run_allowance(run_context)
        await self._require_spend_allowance(run_context)
        graph = self._graph(worker.definition.worker_id, worker.definition.graph_version)

        await self.run_store.create(
            run_context,
            worker=worker.definition,
            input_payload=input_payload,
            release_manifest_ref=self.release_manifest_ref,
        )
        await self._audit(run_context, "run.started", str(run_context.run_id))

        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=_STREAM_BACKLOG_LIMIT)
        task = asyncio.create_task(
            self._drive(
                run_context=run_context,
                graph=graph,
                worker=worker.definition,
                input_payload=input_payload,
                config=self._config(
                    run_context, run_context.run_id, worker.definition.recursion_limit
                ),
                attributes=self._span_attributes(
                    run_context, run_context.run_id, worker.definition.graph_version
                ),
                queue=queue,
            )
        )
        self._running.add(task)
        task.add_done_callback(self._running.discard)
        thread_id = run_context.thread_id
        if thread_id is not None:
            self._by_thread[thread_id] = task

            def _forget(done: asyncio.Task[None]) -> None:
                # Only if it is still ours: a later turn on the same thread may
                # already have taken the slot.
                if self._by_thread.get(thread_id) is done:
                    del self._by_thread[thread_id]

            task.add_done_callback(_forget)
        return self._forward(queue)

    async def cancel_thread(self, thread_id: uuid.UUID) -> bool:
        """End the streamed run in flight on this thread, if this process holds it.

        The graph runs on its own task precisely so that a reader closing a tab
        does not strand a half-finished step - which also meant Stop only closed
        the browser's connection while the run kept calling tools and could
        still raise an approval card for work the person had just abandoned.

        Cancelling the task is the path shutdown already takes: `_drive` records
        the run as cancelled and releases the thread. This waits for that to
        land, so a turn sent right after Stop is not refused as still in flight.
        The next turn starts cleanly even if the cancel caught a tool call
        half-answered, because the agent patches dangling tool calls before it
        runs.

        False when nothing is running here - the turn already ended, or another
        process holds it.
        """
        task = self._by_thread.get(thread_id)
        if task is None or task.done():
            return False
        task.cancel()
        await asyncio.wait({task}, timeout=_CANCEL_SETTLE_SECONDS)
        return True

    async def _forward(
        self, queue: asyncio.Queue[tuple[str, Any]]
    ) -> AsyncIterator[dict[str, Any]]:
        while True:
            kind, payload = await queue.get()
            if kind == "end":
                return
            if kind == "error":
                raise cast(Exception, payload)
            yield cast(dict[str, Any], payload)

    async def _drive(
        self,
        *,
        run_context: RunContext,
        graph: Any,
        worker: WorkerDefinition,
        input_payload: dict[str, Any],
        config: dict[str, Any],
        attributes: dict[str, Any],
        queue: asyncio.Queue[tuple[str, Any]],
    ) -> None:
        try:
            with self.telemetry.span("dw.run.start", attributes):
                async with self._metered(run_context, worker) as callbacks:
                    run_config = {**config, "callbacks": callbacks} if callbacks else config
                    async for chunk in graph.astream(
                        input_payload,
                        run_config,
                        context=run_context,
                        stream_mode=["messages", "updates"],
                        subgraphs=True,
                        version="v2",
                    ):
                        _publish(queue, ("chunk", chunk))
                await self._settle_stream(run_context, graph, config)
        except asyncio.CancelledError as exc:
            # A CancelledError is a BaseException, so the handler below never saw
            # it: shutdown left the row at `running` while the reader was told
            # the stream had finished.
            #
            # uncancel() first, or the very next await here is cancelled again
            # and the run is left half-settled with its reader still waiting on
            # a queue nothing will ever be put into. The reader is told either
            # way, hence the finally.
            current = asyncio.current_task()
            if current is not None:
                current.uncancel()
            try:
                await self._fail(
                    run_context,
                    run_context.run_id,
                    exc,
                    status=RunStatus.CANCELLED,
                    event="run.cancelled",
                )
            finally:
                _publish(queue, ("error", exc))
            raise
        except Exception as exc:
            # A `finally`, for the same reason the branch above grew one.
            # `_fail` writes to the database and `_audit` turns ANY failure
            # there into an `InfrastructureError`, so a blip while recording
            # the failure used to skip the line below - and the reader was left
            # awaiting a queue nothing would ever be put into again. The run
            # was over; the browser was never told, and never would be.
            try:
                await self._fail(run_context, run_context.run_id, exc)
            except Exception:
                # Recording the failure failed too. The row may be stranded at
                # `running` and the reaper settles that; what must not ALSO
                # fail is telling the reader, which the `finally` guarantees.
                # Logged rather than swallowed: this is a database in trouble,
                # not a condition the caller can act on.
                logger.exception(
                    "could not record a failed run", extra={"run_id": str(run_context.run_id)}
                )
            finally:
                _publish(queue, ("error", exc))
        else:
            _publish(queue, ("end", None))

    async def drain(self) -> None:
        """Wait for runs still settling after their reader disconnected.

        Keyed on `done()` rather than on the set emptying, and that is the
        whole of it. A task leaves `_running` through a done-callback, which
        the loop runs via `call_soon`; awaiting a `gather` whose children have
        ALREADY finished does not suspend, so the old `while self._running`
        never gave that callback a turn. It span - at full CPU, with the event
        loop wedged so not even a timeout could fire.

        The window is small but ordinary: a reader is handed the failure the
        instant `_publish` puts it, one step before `_drive` returns, so any
        `drain()` right after a failed turn could land inside it. Measured on
        shutdown, which is exactly where it hurts - the process would not stop,
        so the orchestrator killed it, and a kill is what strands runs at
        `running` and locks a conversation behind `uq_worker_runs_active_thread`.

        A finished task has settled, whether or not its callback has run yet,
        so waiting on the unfinished ones answers the question this asks.
        """
        while pending := tuple(task for task in self._running if not task.done()):
            await asyncio.gather(*pending, return_exceptions=True)

    async def resume(
        self,
        *,
        run_context: RunContext,
        run_id: uuid.UUID,
        resume_payload: dict[str, Any],
    ) -> None:
        record = await self.run_store.get(run_context, run_id)
        if record.status is not RunStatus.WAITING_APPROVAL:
            raise ConflictError(
                "run is not waiting for approval",
                details={"run_id": str(run_id), "status": record.status.value},
            )
        if not self.hosts(
            worker_id=record.worker_id,
            worker_version=record.worker_version,
            graph_version=record.graph_version,
        ):
            raise ConflictError(
                "this service does not run the graph that owns the approval",
                details={
                    "run_id": str(run_id),
                    "worker_id": record.worker_id,
                    "worker_version": record.worker_version,
                    "graph_version": record.graph_version,
                },
            )
        graph = self._graph(record.worker_id, record.graph_version)
        worker = self.worker_registry.resolve(record.worker_id, record.worker_version)

        await self._audit(run_context, "run.resumed", str(run_id))
        attributes = self._span_attributes(
            run_context, run_id, record.graph_version, worker.definition
        )
        with self.telemetry.span("dw.run.resume", attributes):
            try:
                async with self._metered(run_context, worker.definition) as callbacks:
                    state = await graph.ainvoke(
                        Command(resume=resume_payload),
                        self._config(
                            run_context, run_id, worker.definition.recursion_limit, callbacks
                        ),
                        context=run_context,
                    )
            except Exception as exc:
                await self._fail(run_context, run_id, exc)
                raise
        await self._handle_outcome(run_context, run_id, state)

    async def _settle_stream(
        self, run_context: RunContext, graph: Any, config: dict[str, Any]
    ) -> None:
        """Give a streamed run its terminal status, however the stream ended."""
        record = await self.run_store.get(run_context, run_context.run_id)
        if record.status in TERMINAL_STATUSES:
            return
        snapshot = await graph.aget_state(config)
        state = dict(snapshot.values)
        if snapshot.interrupts:
            state["__interrupt__"] = list(snapshot.interrupts)
        await self._handle_outcome(run_context, run_context.run_id, state)

    async def _fail(
        self,
        run_context: RunContext,
        run_id: uuid.UUID,
        exc: BaseException,
        *,
        status: RunStatus = RunStatus.FAILED,
        event: str = "run.failed",
    ) -> None:
        """Record why a run stopped, so it cannot sit at `running` for ever."""
        await self.run_store.set_status(
            run_context,
            run_id,
            status,
            error={"type": type(exc).__name__, "message": str(exc)[:_ERROR_MESSAGE_LIMIT]},
        )
        await self._audit(run_context, event, str(run_id))
        self.telemetry.add_metric(
            DW_RUN_TOTAL, 1, {"worker": run_context.worker_id, "status": status.value}
        )
        self.budget.forget(run_id)

    async def _handle_outcome(
        self, run_context: RunContext, run_id: uuid.UUID, state: dict[str, Any]
    ) -> None:
        interrupts = state.get("__interrupt__") if isinstance(state, dict) else None
        if interrupts:
            distinct = list(
                {getattr(item, "id", None) or id(item): item for item in interrupts}.values()
            )
            if len(distinct) > 1:
                # One run row holds one approval, and resume carries no interrupt
                # id - so a second pause would get no card and could take the
                # first one's decision. `OneApprovalPerStepMiddleware` prevents
                # it; if it ever happens anyway the run ends as failed, visibly,
                # rather than parking on a guess.
                await self._fail(
                    run_context,
                    run_id,
                    ConflictError(
                        "a step paused on more than one approval",
                        details={"pauses": len(distinct)},
                    ),
                )
                return
            first = distinct[0]
            payload = first.value if hasattr(first, "value") else first
            if not isinstance(payload, dict):
                payload = {"value": _jsonable(payload)}
            try:
                approval_id = await self._create_approval(run_context, run_id, payload)
            except Exception:
                # The pause could not be recorded (a malformed `required_scope`
                # refused by its CHECK, ADR 0020). `start` and `resume` call this
                # outside their own failure handling, so without this the row
                # stayed `running` over a checkpoint no request points at. Ended
                # as failed, visibly, like the double pause above. The driver's
                # text (SQL, constraint name) stays in the server log: the run's
                # `error` is returned as is by `GET /runs/{id}`.
                logger.exception(
                    "could not record an approval request", extra={"run_id": str(run_id)}
                )
                await self._fail(
                    run_context,
                    run_id,
                    InfrastructureError("the approval request could not be recorded"),
                )
                return
            await self.run_store.set_status(
                run_context,
                run_id,
                RunStatus.WAITING_APPROVAL,
                approval_request_id=approval_id,
            )
            await self._audit(
                run_context,
                "run.waiting_approval",
                str(run_id),
                details={"approval_request_id": str(approval_id)},
            )
            self.telemetry.add_metric(
                DW_RUN_TOTAL,
                1,
                {"worker": run_context.worker_id, "status": "waiting_approval"},
            )
            return

        await self.run_store.set_status(
            run_context, run_id, RunStatus.COMPLETED, result=_jsonable(state)
        )
        await self._audit(run_context, "run.completed", str(run_id))
        self.telemetry.add_metric(
            DW_RUN_TOTAL, 1, {"worker": run_context.worker_id, "status": "completed"}
        )
        # Freed only at a terminal state, never at a pause. The ledger keyed every
        # run and nothing ever removed one, so a long-lived process grew by one
        # entry per run for as long as it lived. A paused run keeps its entry on
        # purpose: freeing it there would let a loop reset its own ceiling by
        # pausing and being resumed.
        self.budget.forget(run_id)

    async def _create_approval(
        self, run_context: RunContext, run_id: uuid.UUID, payload: dict[str, Any]
    ) -> uuid.UUID:
        """Raise the approval request a paused run waits on.

        `payload` is the interrupt value a graph node wrote, from code and the
        tenant's policy, never from model output: a tool call's own arguments
        travel nested under `payload["payload"]` (`_ask_human`), so no model can
        set a top-level key such as `required_scope`.

        `required_scope` (ADR 0020) is passed through exactly as the node wrote
        it, neither coerced nor dropped: its shape has one owner, the CHECK on
        the column, and a malformed or non-string value fails this INSERT so the
        run ends failed instead of raising a request nobody could rightly decide.
        """
        approval = ApprovalRequest(
            id=self.id_generator.new_uuid(),
            tenant_id=TenantId(run_context.tenant_id),
            workspace_id=WorkspaceId(run_context.workspace_id),
            approval_type=str(payload.get("approval_type", "workflow.review")),
            requested_by=UserId(run_context.actor_id),
            reason=str(payload.get("reason", "workflow requested human review")),
            payload=_jsonable(payload),
            run_id=run_id,
            required_scope=payload.get("required_scope"),
        )
        context = access_context_from_run(run_context)
        async with self.uow_factory(context) as uow:
            await uow.approvals.add(approval)
            await uow.commit()
        return approval.id

    async def _audit(
        self,
        run_context: RunContext,
        action: str,
        resource_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        context = access_context_from_run(run_context)
        event = AuditEvent(
            id=self.id_generator.new_uuid(),
            tenant_id=TenantId(run_context.tenant_id),
            workspace_id=WorkspaceId(run_context.workspace_id),
            actor_id=UserId(run_context.actor_id),
            action=action,
            resource_type="worker_run",
            resource_id=resource_id,
            run_id=run_context.run_id,
            trace_id=run_context.trace_id,
            details=details or {},
            occurred_at=self.clock.now().astimezone(UTC),
        )
        try:
            async with self.uow_factory(context) as uow:
                await uow.audit.append(event)
                await uow.commit()
        except Exception as exc:
            raise InfrastructureError(
                "failed to write audit event", details={"action": action}
            ) from exc


def _loop_prompt(worker: WorkerDefinition) -> tuple[str, str]:
    """The versioned artifact a run's unclaimed model spend is billed under.

    An agent loop: the prompt its worker pins, which is the one
    `WorkerSystemPrompt` renders. A plain graph has no loop prompt - its nodes
    bill their own prompts through inner trackers - so what is left over was
    produced by the graph, and is named by the graph's version rather than by a
    version that names nothing.
    """
    if worker.agent_prompt_id is not None and worker.agent_prompt_version is not None:
        return worker.agent_prompt_id, worker.agent_prompt_version
    return f"graph:{worker.worker_id}", worker.graph_version
