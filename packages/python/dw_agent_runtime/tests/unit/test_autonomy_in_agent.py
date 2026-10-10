"""Autonomy decides, end to end — through the real agent and the real runner.

`test_autonomy_policy.py` proves the table. This proves the table is what the
system actually does: the same tool, the same model script, and only the run's
autonomy changed. If any one of the three gates — the approval middleware, the
tool wrapper, the executor — still read the tool alone, one of these would pass
at a level the others refused, and the run would either stall or act unasked.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from fakes import NOW, FakeExecutionStore, FakeUoWFactory
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel
from test_agent_factory import PROFILE_ID, THREAD, _profiles, prompt_fields
from test_langchain_tools import COPY, LeadInput, LeadOutput, make_definition, make_run_context

from dw_agent_runtime.adapters.agent_factory import AgentSpec, build_agent
from dw_agent_runtime.adapters.chat_model import MockChatModel
from dw_agent_runtime.adapters.langgraph_runner import LangGraphWorkflowRunner
from dw_agent_runtime.autonomy import AUTONOMY_POLICY_VERSION, AutonomyApprovalPolicy
from dw_agent_runtime.contracts import RunContext, ToolDefinition, WorkerDefinition
from dw_agent_runtime.executor import ToolExecutor
from dw_agent_runtime.model.budget import RunBudgetLedger
from dw_agent_runtime.registry import GraphRegistry, WorkerRegistry
from dw_agent_runtime.testing.demo_graph import DEMO_WORKER_YAML, build_demo_graph
from dw_agent_runtime.tools import RegisteredTool, ToolRegistry
from dw_kernel.autonomy import AutonomyLevel
from dw_kernel.ports import FixedClock, SequentialIdGenerator

pytestmark = pytest.mark.unit

# External, idempotent, declared `never`: the tool whose fate autonomy decides.
# It asks at A1 and runs at A3 — exactly the boundary the table draws.
QUOTE = make_definition(name="crm.save_quote")


async def _saved(payload: BaseModel, run_context: RunContext) -> LeadOutput:
    return LeadOutput(lead_id="q-1")


def _spec(
    model: MockChatModel,
    offered: tuple[ToolDefinition, ...] = (QUOTE,),
    input_model: type[BaseModel] = LeadInput,
) -> AgentSpec:
    registry = ToolRegistry()
    for definition in offered:
        registry.register(
            RegisteredTool(
                definition=definition,
                input_model=input_model,
                output_model=LeadOutput,
                handler=_saved,
            )
        )
    executor = ToolExecutor(
        registry=registry,
        execution_store=FakeExecutionStore(),
        uow_factory=FakeUoWFactory(),
        clock=FixedClock(NOW),
        id_generator=SequentialIdGenerator(),
        approval_policy=AutonomyApprovalPolicy(),
    )
    return AgentSpec(
        model=model,
        offered=offered,
        registry=registry,
        executor=executor,
        copy=COPY,
        approval_type_prefix="sales_chat.",
        **prompt_fields("Trợ lý.", template=""),
        budget=RunBudgetLedger(),
        profiles=_profiles(ceiling_tokens=1_000_000),
        profile_id=PROFILE_ID,
    )


def _calls_quote(*extra_ids: str) -> MockChatModel:
    calls = [{"name": "crm__save_quote", "args": {"company": "a"}, "id": "q1"}]
    calls += [{"name": "crm__save_quote", "args": {"company": i}, "id": i} for i in extra_ids]
    return MockChatModel(
        responses=[AIMessage(content="", tool_calls=calls), AIMessage(content="xong")],
        mock_reply="[mock]",
    )


def _at(level: AutonomyLevel) -> RunContext:
    return make_run_context().model_copy(
        update={"run_id": uuid.uuid4(), "autonomy_level": level, "autonomy_ceiling": level}
    )


# ------------------------------------------------------------ the acceptance --


async def test_the_same_tool_waits_at_a1_and_runs_at_a3() -> None:
    """The criterion Mốc 2 was defined by: proven in code, not in a prompt."""
    at_a1 = await build_agent(_spec(_calls_quote()), checkpointer=InMemorySaver()).ainvoke(
        {"messages": [HumanMessage("lưu báo giá")]}, THREAD, context=_at("A1")
    )
    at_a3 = await build_agent(_spec(_calls_quote()), checkpointer=InMemorySaver()).ainvoke(
        {"messages": [HumanMessage("lưu báo giá")]}, THREAD, context=_at("A3")
    )

    assert "__interrupt__" in at_a1, "A1 must stop and ask before an external write"
    assert "__interrupt__" not in at_a3, "A3 must run an idempotent external write unasked"
    assert at_a3["messages"][-1].content == "xong"


class _ScopeNamedInput(BaseModel):
    """A tool whose own argument happens to share the stamp's name."""

    company: str
    required_scope: str


async def test_a_model_cannot_stamp_who_may_decide() -> None:
    """ADR 0004: `required_scope` comes from the node, never from the model.

    The runner stamps the approval from the interrupt's top-level keys. A tool
    call's arguments, which the model writes, travel nested under `payload`, so
    even an argument named `required_scope` cannot reach the stamp.
    """
    model = MockChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "crm__save_quote",
                        "args": {"company": "a", "required_scope": "platform.anyone"},
                        "id": "q1",
                    }
                ],
            ),
            AIMessage(content="xong"),
        ],
        mock_reply="[mock]",
    )

    state = await build_agent(
        _spec(model, input_model=_ScopeNamedInput), checkpointer=InMemorySaver()
    ).ainvoke({"messages": [HumanMessage("lưu báo giá")]}, THREAD, context=_at("A1"))

    (pause,) = state["__interrupt__"]
    assert "required_scope" not in pause.value
    assert pause.value["payload"]["required_scope"] == "platform.anyone"


async def test_a3_still_waits_for_a_non_idempotent_external_write() -> None:
    """A3's reach stops one rung below A4: repeatable writes only."""
    risky = make_definition(name="crm.save_quote", idempotent=False)

    state = await build_agent(
        _spec(_calls_quote(), offered=(risky,)), checkpointer=InMemorySaver()
    ).ainvoke({"messages": [HumanMessage("lưu báo giá")]}, THREAD, context=_at("A3"))

    assert "__interrupt__" in state


async def test_a_critical_tool_waits_even_at_a4() -> None:
    critical = make_definition(name="crm.save_quote", side_effect_level="critical")

    state = await build_agent(
        _spec(_calls_quote(), offered=(critical,)), checkpointer=InMemorySaver()
    ).ainvoke({"messages": [HumanMessage("lưu báo giá")]}, THREAD, context=_at("A4"))

    assert "__interrupt__" in state


async def test_a_run_whose_level_was_never_resolved_asks_about_everything() -> None:
    """Built without going through the runner: no level. It must not act."""
    unresolved = make_run_context().model_copy(
        update={"run_id": uuid.uuid4(), "autonomy_level": None}
    )

    state = await build_agent(_spec(_calls_quote()), checkpointer=InMemorySaver()).ainvoke(
        {"messages": [HumanMessage("lưu báo giá")]}, THREAD, context=unresolved
    )

    assert "__interrupt__" in state


async def test_a_run_stamped_under_another_policy_version_is_not_decided_by_this_one() -> None:
    """A stamp nothing checks is decoration. A run promised other rules asks."""
    stamped = _at("A4").model_copy(update={"approval_policy_version": "0.9.0"})

    state = await build_agent(_spec(_calls_quote()), checkpointer=InMemorySaver()).ainvoke(
        {"messages": [HumanMessage("lưu báo giá")]}, THREAD, context=stamped
    )

    assert "__interrupt__" in state


# --------------------------------------- the middleware reads the run, not the tool --


async def test_sibling_calls_are_deferred_only_where_the_run_gates_them() -> None:
    """`OneApprovalPerStepMiddleware` used to freeze its gated set at build time.
    Two sibling calls: gated at A1 (one card, the other deferred), free at A3
    (no card at all) — from one compiled agent's worth of middleware."""
    at_a1 = await build_agent(_spec(_calls_quote("q2")), checkpointer=InMemorySaver()).ainvoke(
        {"messages": [HumanMessage("lưu hai báo giá")]}, THREAD, context=_at("A1")
    )
    at_a3 = await build_agent(_spec(_calls_quote("q2")), checkpointer=InMemorySaver()).ainvoke(
        {"messages": [HumanMessage("lưu hai báo giá")]}, THREAD, context=_at("A3")
    )

    assert len(at_a1.get("__interrupt__", ())) == 1
    assert "__interrupt__" not in at_a3


# ------------------------------------------------ the runner resolves the level --


class _PassedTheGateError(Exception):
    """Raised at the claim, carrying the context the run would have stamped."""


@dataclass
class _CapturingRunStore:
    captured: list[RunContext] = field(default_factory=list)

    async def started_since(self, tenant_id: uuid.UUID, since: datetime) -> int:
        return 0

    async def create(self, run_context: RunContext, **kwargs: Any) -> None:
        self.captured.append(run_context)
        raise _PassedTheGateError


class _UnmeteredPlan:
    def runs_per_day(self, plan_id: str) -> int | None:
        return None

    def spend_usd_per_day(self, plan_id: str) -> Decimal | None:
        return None


def _runner(tmp_path: Path, store: _CapturingRunStore) -> LangGraphWorkflowRunner:
    config = tmp_path / "demo_approval.yaml"
    config.write_text(DEMO_WORKER_YAML, encoding="utf-8")
    graphs = GraphRegistry()
    graphs.register("demo_approval", "1.0.0", build_demo_graph)
    workers = WorkerRegistry(graph_registry=graphs)
    workers.load_file(config)
    return LangGraphWorkflowRunner(
        worker_registry=workers,
        graph_registry=graphs,
        checkpoint_saver=cast(Any, None),
        run_store=cast(Any, store),
        uow_factory=cast(Any, None),
        clock=cast(Any, FixedClock(NOW)),
        id_generator=cast(Any, None),
        allowance=_UnmeteredPlan(),
        budget=RunBudgetLedger(),
        approval_policy=AutonomyApprovalPolicy(),
    )


def _demo_worker_level(tmp_path: Path) -> AutonomyLevel:
    config = tmp_path / "w.yaml"
    config.write_text(DEMO_WORKER_YAML, encoding="utf-8")
    graphs = GraphRegistry()
    graphs.register("demo_approval", "1.0.0", build_demo_graph)
    definition: WorkerDefinition = (
        WorkerRegistry(graph_registry=graphs).load_file(config).definition
    )
    return definition.autonomy_level


@pytest.mark.parametrize(
    ("ceiling", "expected"),
    [
        ("A4", "A2"),  # no tenant limit: the worker's own A2 governs
        ("A1", "A1"),  # the tenant holds it below its design
        ("A3", "A2"),  # a ceiling above the worker lifts nothing
    ],
)
async def test_the_runner_takes_the_lower_of_worker_and_tenant(
    tmp_path: Path, ceiling: AutonomyLevel, expected: AutonomyLevel
) -> None:
    assert _demo_worker_level(tmp_path) == "A2", "the cases below assume the demo worker is A2"
    store = _CapturingRunStore()
    context = make_run_context().model_copy(
        update={
            "run_id": uuid.uuid4(),
            "worker_id": "demo_approval",
            "autonomy_level": None,
            "autonomy_ceiling": ceiling,
        }
    )

    with pytest.raises(_PassedTheGateError):
        await _runner(tmp_path, store).start(run_context=context, input_payload={})

    [stamped] = store.captured
    assert stamped.autonomy_level == expected
    assert stamped.approval_policy_version == AUTONOMY_POLICY_VERSION
