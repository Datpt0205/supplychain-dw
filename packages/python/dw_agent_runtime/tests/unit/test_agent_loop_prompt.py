"""An agent loop's spend is recorded under the prompt it actually ran.

The ledger used to stamp every agent loop `agent_loop@0.0.0`: a version that
named no artifact, so a cost breakdown by prompt could not tell two releases of
a worker's wording apart. The prompt is pinned on the worker definition, the
runner bills under that pin, and the release manifest lists it.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, cast

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError
from test_langchain_tools import make_run_context

from dw_agent_runtime.adapters.chat_model import MockChatModel
from dw_agent_runtime.adapters.langchain_usage import LangchainUsageMeter
from dw_agent_runtime.adapters.langgraph_runner import LangGraphWorkflowRunner
from dw_agent_runtime.autonomy import AutonomyApprovalPolicy
from dw_agent_runtime.contracts import RunContext, WorkerDefinition
from dw_agent_runtime.model.budget import RunBudgetLedger
from dw_agent_runtime.model.gateway import ModelUsage
from dw_agent_runtime.model.profiles import ModelRoute
from dw_agent_runtime.ports import ModelRequest
from dw_agent_runtime.registry import GraphRegistry, WorkerRegistry

pytestmark = pytest.mark.unit

_WORKER: dict[str, Any] = {
    "worker_id": "sales_chat",
    "worker_version": "1.2.0",
    "domain": "sales",
    "graph_version": "1.1.0",
    "prompt_bundle_version": "1.0.0",
    "policy_version": "1.0.0",
    "memory_policy_version": "1.0.0",
    "default_model_profile": "gateway",
    "supported_channels": frozenset({"web"}),
    "autonomy_level": "A2",
}


class _Recorder:
    def __init__(self) -> None:
        self.rows: list[tuple[RunContext, ModelRequest, ModelUsage]] = []

    async def record(
        self, run_context: RunContext, request: ModelRequest, usage: ModelUsage
    ) -> None:
        self.rows.append((run_context, request, usage))


class _Profiles:
    def resolve(self, profile_id: str) -> Any:
        return type("P", (), {"chat": ModelRoute(provider="mock", model="mock-1")})()


def _runner(recorder: _Recorder) -> LangGraphWorkflowRunner:
    return LangGraphWorkflowRunner(
        worker_registry=WorkerRegistry(graph_registry=GraphRegistry()),
        graph_registry=GraphRegistry(),
        checkpoint_saver=cast(Any, None),
        run_store=cast(Any, None),
        uow_factory=cast(Any, None),
        clock=cast(Any, None),
        id_generator=cast(Any, None),
        allowance=cast(Any, None),
        budget=RunBudgetLedger(),
        approval_policy=AutonomyApprovalPolicy(),
        usage_meter=LangchainUsageMeter(profiles=cast(Any, _Profiles()), recorder=recorder),
    )


async def _one_metered_call(worker: WorkerDefinition) -> ModelRequest:
    recorder = _Recorder()
    model = MockChatModel(responses=[AIMessage(content="x" * 40)], mock_reply="ok")
    run = make_run_context().model_copy(update={"run_id": uuid.uuid4()})
    async with _runner(recorder)._metered(run, worker) as callbacks:
        await model.ainvoke([{"role": "user", "content": "y"}], config={"callbacks": callbacks})
    [(_, request, _)] = recorder.rows
    return request


async def test_an_agent_loop_is_billed_under_the_prompt_its_worker_pins() -> None:
    worker = WorkerDefinition(
        **_WORKER, agent_prompt_id="sales_chat.assistant", agent_prompt_version="2.3.0"
    )

    request = await _one_metered_call(worker)

    assert (request.prompt_id, request.prompt_version) == ("sales_chat.assistant", "2.3.0")
    assert request.task == "agent_loop"


async def test_a_plain_graph_is_billed_under_its_graph_version_not_a_made_up_one() -> None:
    """A worker with no loop prompt still spends; the graph is what produced it."""
    request = await _one_metered_call(WorkerDefinition(**_WORKER))

    assert (request.prompt_id, request.prompt_version) == ("graph:sales_chat", "1.1.0")
    assert request.prompt_version != "0.0.0"


@pytest.mark.parametrize(
    "half",
    [{"agent_prompt_id": "sales_chat.assistant"}, {"agent_prompt_version": "1.0.0"}],
)
def test_a_prompt_pin_is_both_halves_or_neither(half: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        WorkerDefinition(**_WORKER, **half)


def test_a_worker_yaml_carries_its_agent_prompt(tmp_path: Path) -> None:
    graphs = GraphRegistry()
    graphs.register("sales_chat", "1.1.0", cast(Any, lambda: None))
    config = tmp_path / "sales_chat.yaml"
    config.write_text(
        "\n".join(
            [
                'schema_version: "1.0"',
                "worker_id: sales_chat",
                'worker_version: "1.2.0"',
                "domain: sales",
                'graph_version: "1.1.0"',
                'prompt_bundle_version: "1.0.0"',
                'policy_version: "1.0.0"',
                'memory_policy_version: "1.0.0"',
                "default_model_profile: gateway",
                "supported_channels: [web]",
                "autonomy_level: A2",
                "agent_prompt_id: sales_chat.assistant",
                'agent_prompt_version: "2.3.0"',
            ]
        ),
        encoding="utf-8",
    )

    definition = WorkerRegistry(graph_registry=graphs).load_file(config).definition

    assert (definition.agent_prompt_id, definition.agent_prompt_version) == (
        "sales_chat.assistant",
        "2.3.0",
    )
