"""A delegated turn is still inside the run's ceiling, and still narrower.

The measurement these tests exist for: the parent's middleware does NOT wrap a
sub-agent's model call. A probe middleware on both runs parent, child, parent —
the child's turn sees only its own stack. So every guarantee the platform makes
has to be re-applied inside the child, and the one that costs money if it is
not is the spend ceiling.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel
from test_agent_factory import OFFERED, PROFILE_ID, READ, _profiles, _registry, prompt_fields
from test_langchain_tools import COPY, LeadInput, LeadOutput, make_run_context

from dw_agent_runtime.adapters.agent_factory import AgentSpec, build_agent, platform_middleware
from dw_agent_runtime.adapters.chat_model import MockChatModel
from dw_agent_runtime.adapters.langchain_tools import model_facing_name
from dw_agent_runtime.adapters.sub_agents import SubAgentSpec, sub_agent_spec
from dw_agent_runtime.contracts import RunContext, ToolDefinition
from dw_agent_runtime.model.budget import RunBudgetLedger
from dw_agent_runtime.tools import RegisteredTool, ToolRegistry

pytestmark = pytest.mark.unit

WORKER_PROMPT = "Trợ lý."


def _spec(
    model: MockChatModel,
    *,
    budget: RunBudgetLedger | None = None,
    children: tuple[SubAgentSpec, ...] = (),
) -> AgentSpec:
    registry, executor = _registry()
    return AgentSpec(
        model=model,
        offered=OFFERED,
        registry=registry,
        executor=executor,
        copy=COPY,
        approval_type_prefix="sales_chat.",
        **prompt_fields(WORKER_PROMPT, template=""),
        budget=budget or RunBudgetLedger(),
        profiles=_profiles(),
        profile_id=PROFILE_ID,
        sub_agents=children,
    )


def _child(**overrides: Any) -> SubAgentSpec:
    fields: dict[str, Any] = {
        "name": "researcher",
        "description": "Tra cứu hồ sơ",
        "offered": (READ,),
    }
    fields.update(overrides)
    return SubAgentSpec(**fields)


def test_a_sub_agent_spends_against_its_callers_ledger() -> None:
    """The escape this module exists to close. The child's middleware must hold
    the SAME ledger object — a copy would let a delegated turn spend a second
    ceiling that nobody set."""
    budget = RunBudgetLedger()
    parent = _spec(
        MockChatModel(responses=[AIMessage(content="x")], mock_reply="[m]"), budget=budget
    )

    built = sub_agent_spec(parent, _child())

    ledgers = [getattr(m, "_ledger", getattr(m, "ledger", None)) for m in built["middleware"]]
    assert any(ledger is budget for ledger in ledgers), (
        "the child must hold the parent's ledger object itself, not an equal one"
    )


def test_a_sub_agent_cannot_offer_a_tool_its_caller_lacks() -> None:
    """Otherwise delegation is a way to reach a tool by asking for it twice."""
    parent = _spec(MockChatModel(responses=[AIMessage(content="x")], mock_reply="[m]"))
    outsider = ToolDefinition(
        name="billing.refund",
        version="1.0.0",
        description="Hoàn tiền",
        input_schema_ref="i",
        output_schema_ref="o",
        required_scopes=frozenset(),
        side_effect_level="external",
        approval_policy="conditional",
        timeout_seconds=10,
        max_retries=0,
        idempotent=True,
        data_classification=frozenset({"internal"}),
    )

    with pytest.raises(ValueError, match=r"billing\.refund"):
        sub_agent_spec(parent, _child(offered=(READ, outsider)))


def test_a_sub_agents_tools_are_built_through_the_executor() -> None:
    """It takes DEFINITIONS, never ready-made tools: a caller that could pass a
    tool could pass one the executor was never wrapped around."""
    parent = _spec(MockChatModel(responses=[AIMessage(content="x")], mock_reply="[m]"))

    built = sub_agent_spec(parent, _child())

    names = {tool.name for tool in built["tools"]}
    assert names, "the child was given no tools at all"
    # Every tool came from `platform_tools`, which is the only thing that wraps
    # a definition in the executor.
    assert len(names) == 1


def test_the_child_carries_the_same_gates_the_parent_does() -> None:
    parent = _spec(MockChatModel(responses=[AIMessage(content="x")], mock_reply="[m]"))

    built = sub_agent_spec(parent, _child())

    kinds = {type(m).__name__ for m in built["middleware"]}
    assert {
        "OfferedToolsOnlyMiddleware",
        "ScopedToolsMiddleware",
        "OneApprovalPerStepMiddleware",
        "PlatformToolErrorsMiddleware",
        "RunBudgetMiddleware",
    } <= kinds


def test_no_task_tool_exists_until_a_context_names_something_to_delegate_to() -> None:
    """An agent that can spawn is a larger surface than one that cannot, so the
    `task` tool is absent by default rather than present and unused."""
    plain = platform_middleware(_spec(MockChatModel(responses=[], mock_reply="[m]")))
    assert not any(type(m).__name__ == "SubAgentMiddleware" for m in plain)

    delegating = platform_middleware(
        _spec(MockChatModel(responses=[], mock_reply="[m]"), children=(_child(),))
    )
    assert any(type(m).__name__ == "SubAgentMiddleware" for m in delegating)


async def test_a_delegated_turn_inherits_the_runs_tenancy_and_no_more() -> None:
    """Measured rather than assumed: the context is graph level, so a child sees
    the caller's tenant, scopes and autonomy without being handed them — and has
    no field with which to replace them.

    Asserted from inside the child's TOOL, which is the only place that can
    honestly report what the delegated turn was running as.
    """
    seen: list[RunContext] = []

    async def _capture(payload: BaseModel, run_context: RunContext) -> LeadOutput:
        seen.append(run_context)
        return LeadOutput(lead_id="ok")

    async def _plain(payload: BaseModel, run_context: RunContext) -> LeadOutput:
        return LeadOutput(lead_id="ok")

    # Every definition the PARENT offers has to resolve, because the parent's own
    # tools are built from the same registry; only READ carries the probe.
    registry = ToolRegistry()
    for definition in OFFERED:
        registry.register(
            RegisteredTool(
                definition=definition,
                input_model=LeadInput,
                output_model=LeadOutput,
                handler=_capture if definition is READ else _plain,
            )
        )
    _, borrowed = _registry()
    executor = replace(borrowed, registry=registry)

    parent_model = MockChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": "tra cứu", "subagent_type": "researcher"},
                        "id": "c1",
                    }
                ],
            ),
            AIMessage(content="xong"),
        ],
        mock_reply="[m]",
    )
    child_model = MockChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": model_facing_name(READ.name), "args": {"company": "a"}, "id": "c2"}
                ],
            ),
            AIMessage(content="con xong"),
        ],
        mock_reply="[m]",
    )
    spec = replace(
        _spec(parent_model, children=(_child(model=child_model),)),
        registry=registry,
        executor=executor,
    )
    context = make_run_context()

    await build_agent(spec, checkpointer=InMemorySaver()).ainvoke(
        {"messages": [HumanMessage(content="chào")]},
        config={"configurable": {"thread_id": f"t-{uuid.uuid4()}"}},
        context=context,
    )

    assert seen, "the child never reached a tool, so this proves nothing"
    inside = seen[0]
    assert inside.tenant_id == context.tenant_id
    assert inside.workspace_id == context.workspace_id
    assert inside.scopes == context.scopes, "not one scope more than the caller"
    assert inside.autonomy_level == context.autonomy_level
