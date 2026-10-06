"""`build_agent` carries every property the six platform middlewares protect.

The contract is presence, not order — measured: all 720 orderings of the six were
run against these properties and none broke. So each test below is written to
fail when the middleware that owns its property is missing, and
`test_every_middleware_is_load_bearing` proves that for all six at once: drop any
single one and exactly its property goes red. A test here that could not fail
would be the same mistake as a middleware nobody installed.

Seven properties, six owners. "An approval pauses the run" belongs to no single
middleware — the pause comes from the tool itself — and is measured to catch the
one hazard presence does not cover: a seventh middleware that swallows it.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fakes import NOW, FakeAuditRepo, FakeExecutionStore, FakeUoWFactory
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel
from test_langchain_tools import COPY, LeadInput, LeadOutput, make_definition, make_run_context

from dw_agent_runtime.adapters.agent_factory import AgentSpec, build_agent, platform_middleware
from dw_agent_runtime.adapters.chat_model import MockChatModel
from dw_agent_runtime.adapters.langchain_tools import platform_tools
from dw_agent_runtime.autonomy import AutonomyApprovalPolicy
from dw_agent_runtime.contracts import RunContext, ToolDefinition
from dw_agent_runtime.executor import ToolExecutor
from dw_agent_runtime.model.budget import BudgetExceededError, RunBudgetLedger
from dw_agent_runtime.model.copy import load_runtime_copy
from dw_agent_runtime.model.profiles import ModelProfile, ModelProfileRegistry
from dw_agent_runtime.model.prompts import PromptArtifact, PromptRegistry
from dw_agent_runtime.registry import ConfigError
from dw_agent_runtime.tools import RegisteredTool, ToolRegistry
from dw_kernel.errors import TenantContextMissingError
from dw_kernel.ports import FixedClock, SequentialIdGenerator

pytestmark = pytest.mark.unit

WORKER_PROMPT = "Bạn là trợ lý bán hàng của FDX."
# The worker prompt is a registry artifact, rendered per model call with the
# variables of that call (today's date, the screen in view).
PROMPT_ID = "agent_test.worker"
PROMPT_VERSION = "1.0.0"
DATED = "Hôm nay là {today}."
TODAY = "06/10/2026"
# Compaction needs the 1.4.0 text; everything else here still runs on 1.3.0.
COMPACTING_COPY = load_runtime_copy(
    Path(__file__).resolve().parents[5] / "configs" / "copy" / "runtime@1.6.0.yaml"
)
THREAD: RunnableConfig = {"configurable": {"thread_id": "t-1"}}


async def _ok(payload: BaseModel, run_context: RunContext) -> LeadOutput:
    return LeadOutput(lead_id="ok")


async def _boom(payload: BaseModel, run_context: RunContext) -> LeadOutput:
    raise RuntimeError("nhà cung cấp sập")


READ = make_definition(name="crm.read_lead", side_effect_level="none")
BOOM = make_definition(name="crm.boom", side_effect_level="none")
GATED = make_definition(name="crm.send_quote", approval_policy="always")
# Registered, but not in this worker's toolset.
FOREIGN = make_definition(name="billing.refund")
ADMIN_ONLY = make_definition(name="crm.purge", required_scopes=frozenset({"crm.admin"}))


class _Builtin(BaseTool):
    """Stands in for a tool a harness would install without any worker asking."""

    name: str = "write_file"
    description: str = "Ghi một tệp."

    def _run(self, *args: Any, **kwargs: Any) -> str:
        return "ghi xong"


def _registry() -> tuple[ToolRegistry, ToolExecutor]:
    registry = ToolRegistry()
    for definition, handler in (
        (READ, _ok),
        (BOOM, _boom),
        (GATED, _ok),
        (FOREIGN, _ok),
        (ADMIN_ONLY, _ok),
    ):
        registry.register(
            RegisteredTool(
                definition=definition,
                input_model=LeadInput,
                output_model=LeadOutput,
                handler=handler,
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
    return registry, executor


# The worker's toolset: FOREIGN is deliberately absent.
OFFERED: tuple[ToolDefinition, ...] = (READ, BOOM, GATED, ADMIN_ONLY)

PROFILE_ID = "agent_test"
# Small enough that a looping agent reaches it in a handful of steps, so a test
# can tell "the budget stopped it" from "recursion_limit stopped it".
LOOP_CEILING_TOKENS = 60


def _profiles(
    ceiling_tokens: int = LOOP_CEILING_TOKENS, summary_input_tokens: int | None = 8000
) -> ModelProfileRegistry:
    profiles = ModelProfileRegistry()
    route: dict[str, object] = {"provider": "mock", "model": "mock-1"}
    if summary_input_tokens is not None:
        # The chat route doubles as the summariser's in these tests, and
        # compaction refuses a summary route without an input budget.
        route["max_input_tokens"] = summary_input_tokens
    profiles.register(
        ModelProfile.model_validate(
            {
                "schema_version": "1.0",
                "profile_id": PROFILE_ID,
                "routing_policy_version": "1.0.0",
                "structured_extraction": route,
                "reasoning": route,
                "chat": route,
                "budgets": {
                    "max_input_tokens_per_run": ceiling_tokens,
                    "max_cost_usd_per_run": 100.0,
                },
            }
        )
    )
    return profiles


def prompt_artifact(
    system: str = WORKER_PROMPT, template: str = DATED, version: str = PROMPT_VERSION
) -> PromptArtifact:
    return PromptArtifact(
        schema_version="1.0",
        prompt_id=PROMPT_ID,
        version=version,
        system=system,
        template=template,
        variables=frozenset({"today"}) if "{today}" in template else frozenset(),
    )


def prompt_fields(
    system: str = WORKER_PROMPT, template: str = DATED, prompts: PromptRegistry | None = None
) -> dict[str, Any]:
    """The four `AgentSpec` fields that pin the worker prompt to the registry."""
    if prompts is None:
        prompts = PromptRegistry()
        prompts.register(prompt_artifact(system, template))
    return {
        "prompt_id": PROMPT_ID,
        "prompt_version": PROMPT_VERSION,
        "prompts": prompts,
        "prompt_variables": lambda request: {"today": TODAY} if "{today}" in template else {},
    }


def _spec(model: MockChatModel, budget: RunBudgetLedger | None = None) -> AgentSpec:
    registry, executor = _registry()
    return AgentSpec(
        model=model,
        offered=OFFERED,
        registry=registry,
        executor=executor,
        copy=COPY,
        approval_type_prefix="sales_chat.",
        **prompt_fields(),
        budget=budget or RunBudgetLedger(),
        profiles=_profiles(),
        profile_id=PROFILE_ID,
    )


def _calling(tool: str) -> MockChatModel:
    return MockChatModel(
        responses=[
            AIMessage(
                content="", tool_calls=[{"name": tool, "args": {"company": "a"}, "id": "c1"}]
            ),
            AIMessage(content="xong"),
        ],
        mock_reply="[mock]",
    )


def _unreadable_file_message() -> HumanMessage:
    return HumanMessage(
        content=[
            {"type": "text", "text": "xem giúp tệp này"},
            {
                "type": "file",
                "mime_type": "application/octet-stream",
                "base64": "AAAA",
                "filename": "bao_gia.bin",
            },
        ]
    )


def _own_run() -> RunContext:
    """A context with a run id of its own.

    Spend is keyed by run id, so scenarios that share one are one run as far as
    the ceiling is concerned: they add up, and the later ones are refused for
    what the earlier ones spent. `make_run_context` fixes the id for tests that
    need it stable; every scenario here is a separate run and must say so.
    """
    return make_run_context().model_copy(update={"run_id": uuid.uuid4()})


# --------------------------------------------------------------- properties --


async def test_only_the_workers_own_tools_are_offered() -> None:
    model = MockChatModel(responses=[AIMessage(content="xong")], mock_reply="[mock]")

    await build_agent(_spec(model)).ainvoke(
        {"messages": [HumanMessage("chào")]}, context=make_run_context()
    )

    assert "billing__refund" not in model.bound_tool_names
    assert "crm__read_lead" in model.bound_tool_names


async def test_a_tool_outside_the_runs_scopes_is_not_offered() -> None:
    model = MockChatModel(responses=[AIMessage(content="xong")], mock_reply="[mock]")

    await build_agent(_spec(model)).ainvoke(
        {"messages": [HumanMessage("chào")]}, context=make_run_context()
    )

    assert "crm__purge" not in model.bound_tool_names


async def test_the_worker_prompt_reaches_the_model_first() -> None:
    model = MockChatModel(responses=[AIMessage(content="xong")], mock_reply="[mock]")

    await build_agent(_spec(model)).ainvoke(
        {"messages": [HumanMessage("chào")]}, context=make_run_context()
    )

    assert model.system_prompts[0].startswith(WORKER_PROMPT)
    # Rendered per call from the registry artifact, the call's variables in it.
    assert f"Hôm nay là {TODAY}." in model.system_prompts[0]


async def test_a_tenants_own_prompt_reaches_its_runs_and_no_one_elses() -> None:
    """The tenant comes from the run, and resolution falls back to the platform.

    Tenant A has its own wording. A's run renders it; B's run renders the
    platform's - never A's, which would be one customer's wording shown to
    another.
    """
    tenant_a = make_run_context()
    tenant_b = make_run_context().model_copy(update={"tenant_id": uuid.uuid4()})
    prompts = PromptRegistry()
    prompts.register(prompt_artifact())
    prompts.register(prompt_artifact("Bạn là trợ lý của riêng A."), tenant_id=tenant_a.tenant_id)
    spec = replace(
        _spec(MockChatModel(responses=[], mock_reply="x")), **prompt_fields(prompts=prompts)
    )

    seen = []
    for run in (tenant_a, tenant_b):
        model = MockChatModel(responses=[AIMessage(content="xong")], mock_reply="[mock]")
        await build_agent(replace(spec, model=model)).ainvoke(
            {"messages": [HumanMessage("chào")]}, context=run
        )
        seen.append(model.system_prompts[0])

    assert seen[0].startswith("Bạn là trợ lý của riêng A.")
    assert seen[1].startswith(WORKER_PROMPT)


async def test_a_prompt_missing_from_the_registry_fails_the_build() -> None:
    """Not a run that later finds no prompt, and not a free text in its place."""
    spec = replace(_spec(MockChatModel(responses=[], mock_reply="x")), prompt_version="9.9.9")

    with pytest.raises(ConfigError):
        build_agent(spec)


async def test_a_run_without_a_run_context_is_not_given_a_prompt() -> None:
    """Whose wording to render is the run's tenant; with no run, nobody's."""
    model = MockChatModel(responses=[AIMessage(content="xong")], mock_reply="[mock]")

    with pytest.raises(TenantContextMissingError):
        await build_agent(_spec(model)).ainvoke({"messages": [HumanMessage("chào")]})
    assert model.system_prompts == []


async def test_a_file_the_model_cannot_read_never_reaches_it() -> None:
    model = MockChatModel(responses=[AIMessage(content="xong")], mock_reply="[mock]")

    await build_agent(_spec(model)).ainvoke(
        {"messages": [_unreadable_file_message()]}, context=make_run_context()
    )

    blocks = [
        block
        for message in model.calls[0]
        if isinstance(message.content, list)
        for block in message.content
    ]
    assert not any(isinstance(b, dict) and b.get("type") == "file" for b in blocks)


async def test_a_failing_tool_does_not_end_the_turn() -> None:
    model = _calling("crm__boom")

    state = await build_agent(_spec(model)).ainvoke(
        {"messages": [HumanMessage("tra lead")]}, context=make_run_context()
    )

    assert state["messages"][-1].content == "xong"


async def test_a_gated_tool_pauses_the_run_for_a_person() -> None:
    model = _calling("crm__send_quote")

    state = await build_agent(_spec(model), checkpointer=InMemorySaver()).ainvoke(
        {"messages": [HumanMessage("gửi báo giá")]}, THREAD, context=make_run_context()
    )

    assert "__interrupt__" in state


# ---------------------------------------------------------- the contract itself --


async def _properties(middleware: list[AgentMiddleware[Any, Any]]) -> dict[str, bool]:
    """Every property the stack protects, measured against an arbitrary middleware list.

    Rebuilds the agent directly rather than through `build_agent`, because the
    point is to remove one middleware at a time and watch what breaks.
    """
    registry, executor = _registry()

    def agent(model: MockChatModel, **kwargs: Any) -> Any:
        tools = [
            *platform_tools(
                list(OFFERED), registry, executor, approval_type_prefix="sales_chat.", copy=COPY
            ),
            _Builtin(),
        ]
        return create_agent(
            model=model,
            tools=tools,
            middleware=middleware,
            context_schema=RunContext,
            **kwargs,
        )

    view = MockChatModel(responses=[AIMessage(content="xong")], mock_reply="[mock]")
    await agent(view).ainvoke({"messages": [_unreadable_file_message()]}, context=_own_run())
    blocks = [b for m in view.calls[0] if isinstance(m.content, list) for b in m.content]

    failing = _calling("crm__boom")
    try:
        turn = await agent(failing).ainvoke({"messages": [HumanMessage("x")]}, context=_own_run())
        survived = turn["messages"][-1].content == "xong"
    except Exception:
        survived = False

    gated = _calling("crm__send_quote")
    try:
        paused = "__interrupt__" in await agent(gated, checkpointer=InMemorySaver()).ainvoke(
            {"messages": [HumanMessage("x")]}, THREAD, context=_own_run()
        )
    except Exception:
        paused = False

    # Two gated calls in ONE model step. Everything downstream of a pause assumes
    # exactly one: one approval row, one card, and a resume payload that carries
    # no interrupt id — so with two, the decision a person makes about one card
    # is consumed by whichever call reaches `interrupt()` first.
    siblings = MockChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "crm__send_quote", "args": {"company": "a"}, "id": "g1"},
                    {"name": "crm__send_quote", "args": {"company": "b"}, "id": "g2"},
                ],
            ),
            AIMessage(content="xong"),
        ],
        mock_reply="[mock]",
    )
    try:
        pending = await agent(siblings, checkpointer=InMemorySaver()).ainvoke(
            {"messages": [HumanMessage("x")]}, THREAD, context=_own_run()
        )
        cards = len(pending.get("__interrupt__", ()))
    except Exception:
        cards = 0

    # A model that calls a tool on every turn and never answers: the loop a
    # ceiling exists for. Capped by recursion_limit too, so without the budget
    # middleware the run still ends — and the test can tell which one ended it.
    looping = MockChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "crm__read_lead", "args": {"company": "a"}, "id": "loop"}],
            )
        ],
        mock_reply="[mock]",
    )
    try:
        await agent(looping).ainvoke(
            {"messages": [HumanMessage("x")]},
            {"recursion_limit": RUNAWAY_STEP_CAP},
            context=_own_run(),
        )
        stopped_by = "nothing"
    except BudgetExceededError:
        stopped_by = "budget"
    except Exception as exc:
        stopped_by = type(exc).__name__

    return {
        "runaway stopped by budget": stopped_by == "budget",
        "builtin hidden": "write_file" not in view.bound_tool_names,
        "out-of-scope hidden": "crm__purge" not in view.bound_tool_names,
        "prompt first": view.system_prompts[0].startswith(WORKER_PROMPT),
        "unreadable file stripped": not any(
            isinstance(b, dict) and b.get("type") == "file" for b in blocks
        ),
        "failure survived": survived,
        "approval paused": paused,
        "one approval card per step": cards == 1,
    }


# Which property each of the seven owns. Order matches `platform_middleware`.
OWNS = (
    "prompt first",
    "unreadable file stripped",
    "builtin hidden",
    "out-of-scope hidden",
    "one approval card per step",
    "failure survived",
    "runaway stopped by budget",
)

# Well above the handful of steps the budget needs to stop a loop at
# LOOP_CEILING_TOKENS, so reaching it means the ceiling did not.
RUNAWAY_STEP_CAP = 60


async def test_the_full_stack_holds_every_property() -> None:
    spec = _spec(MockChatModel(responses=[AIMessage(content="xong")], mock_reply="[mock]"))

    measured = await _properties(platform_middleware(spec))

    assert measured == dict.fromkeys(measured, True), measured


@pytest.mark.parametrize("dropped", range(len(OWNS)))
async def test_every_middleware_is_load_bearing(dropped: int) -> None:
    """Drop any one of the six: the property it owns must go red.

    This is the test that makes the others mean something. It would pass
    vacuously if a property check could never fail — so it asserts the failure,
    not the success.
    """
    owned = OWNS[dropped]
    spec = _spec(MockChatModel(responses=[AIMessage(content="xong")], mock_reply="[mock]"))
    stack = platform_middleware(spec)
    del stack[dropped]

    measured = await _properties(stack)

    assert measured[owned] is False, f"dropping middleware #{dropped} did not break {owned!r}"


async def test_a_middleware_that_swallows_everything_loses_the_approval() -> None:
    """Why `platform_middleware` warns that additions must let GraphBubbleUp through.

    Presence is the contract for the six; this is the hazard a SEVENTH brings. A
    hand-written catch-all around a tool call eats the `interrupt()` an approval
    raises, and the run goes on as if somebody had said yes.
    """

    class SwallowEverything(AgentMiddleware[Any, Any]):
        async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
            try:
                return await handler(request)
            except BaseException:
                return ToolMessage(content="đã xử lý", tool_call_id=request.tool_call["id"])

    spec = _spec(MockChatModel(responses=[AIMessage(content="xong")], mock_reply="[mock]"))

    measured = await _properties([*platform_middleware(spec), SwallowEverything()])

    assert measured["approval paused"] is False


# --------------------------------------------------------------- compaction --


async def test_compaction_is_off_unless_a_worker_asks_for_it() -> None:
    spec = _spec(MockChatModel(responses=[AIMessage(content="xong")], mock_reply="[mock]"))

    names = [type(m).__name__ for m in platform_middleware(spec)]

    assert "PlatformSummarizationMiddleware" not in names


async def test_a_worker_that_asks_for_compaction_actually_compacts() -> None:
    """The wiring must do the thing, not merely accept the setting. A spec field
    `build_agent` read and dropped would pass every test that only builds."""
    from dataclasses import replace

    from langchain_core.messages import BaseMessage

    from dw_agent_runtime.adapters.agent_factory import CompactionSpec

    audit = FakeAuditRepo()
    base = _spec(MockChatModel(responses=[AIMessage(content="xong")], mock_reply="[mock]"))
    spec = replace(
        base,
        copy=COMPACTING_COPY,
        # A ceiling this test is not about. The default one is sized to stop a
        # runaway in a handful of steps, and a 16-message history plus its
        # summary crosses it — which proves the summary is counted, and is not
        # what this test measures.
        profiles=_profiles(ceiling_tokens=1_000_000),
        compaction=CompactionSpec(
            model=MockChatModel(responses=[AIMessage(content="TÓM TẮT")], mock_reply="TÓM TẮT"),
            summary_profile_id=PROFILE_ID,
            uow_factory=FakeUoWFactory(audit_repo=audit),
            clock=FixedClock(NOW),
            ids=SequentialIdGenerator(),
            trigger=("messages", 10),
            keep=("messages", 4),
        ),
    )
    history: list[BaseMessage] = []
    for i in range(8):
        history += [
            HumanMessage(content=f"câu {i}", id=f"h{i}"),
            AIMessage(content=f"đáp {i}", id=f"a{i}"),
        ]
    agent = build_agent(spec, checkpointer=InMemorySaver())

    await agent.ainvoke({"messages": history}, THREAD, context=_own_run())
    state = await agent.aget_state(THREAD)

    assert "h0" not in [m.id for m in state.values["messages"]]
    assert [e.action for e in audit.events] == ["run.context_compacted"]
