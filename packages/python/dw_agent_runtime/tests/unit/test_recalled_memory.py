"""Recalled memory reaches the model, as data, and only what the RUN asked for.

Written through the real agent rather than against a hand-made `ModelRequest`,
because what matters is what actually arrived in the system message — the thing
a middleware stack decides and nothing else can honestly report.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_factory import OFFERED, PROFILE_ID, _profiles, _registry, prompt_fields
from test_langchain_tools import make_run_context

from dw_agent_runtime.adapters.agent_factory import AgentSpec, build_agent, platform_middleware
from dw_agent_runtime.adapters.chat_model import MockChatModel
from dw_agent_runtime.adapters.recalled_memory import RecalledMemoryMiddleware
from dw_agent_runtime.model.budget import RunBudgetLedger
from dw_agent_runtime.model.copy import load_runtime_copy
from dw_platform.application.access_context import AccessContext

pytestmark = pytest.mark.unit

CONFIGS = Path(__file__).resolve().parents[5] / "configs" / "copy"
# 1.5.0 carries the frame; 1.3.0 does not, which is what the refusal test needs.
COPY_WITH_FRAME = load_runtime_copy(CONFIGS / "runtime@1.5.0.yaml")
COPY_WITHOUT_FRAME = load_runtime_copy(CONFIGS / "runtime@1.3.0.yaml")

WORKER_PROMPT = "Bạn là trợ lý bán hàng của FDX."
NOW = datetime(2026, 9, 18, 9, 0, tzinfo=UTC)


@dataclass(frozen=True)
class _Fact:
    content: str
    confidence: float = 0.9


@dataclass
class _FakeRecall:
    """Records how it was asked, which is the part under test."""

    facts: tuple[_Fact, ...] = ()
    raises: Exception | None = None
    asked: list[dict[str, Any]] = field(default_factory=list)

    async def recall(
        self,
        context: AccessContext,
        *,
        worker_id: str,
        subject_refs: Sequence[str],
        now: datetime,
        limit: int = 12,
    ) -> tuple[_Fact, ...]:
        self.asked.append(
            {
                "tenant_id": context.tenant_id,
                "workspace_id": context.workspace_id,
                "clearance": context.clearance,
                "worker_id": worker_id,
                "subject_refs": tuple(subject_refs),
                "now": now,
            }
        )
        if self.raises is not None:
            raise self.raises
        return self.facts


def _model(reply: str = "xong") -> MockChatModel:
    return MockChatModel(responses=[AIMessage(content=reply)], mock_reply="[mock]")


def _spec(model: MockChatModel, recall: Any, *, copy: Any = COPY_WITH_FRAME) -> AgentSpec:
    registry, executor = _registry()
    return AgentSpec(
        model=model,
        offered=OFFERED,
        registry=registry,
        executor=executor,
        copy=copy,
        approval_type_prefix="sales_chat.",
        **prompt_fields(WORKER_PROMPT, template=""),
        budget=RunBudgetLedger(),
        profiles=_profiles(),
        profile_id=PROFILE_ID,
        recall=recall,
        clock=lambda: NOW,
    )


async def _run(spec: AgentSpec, *, subject_ref: str | None) -> None:
    context = make_run_context().model_copy(update={"subject_ref": subject_ref})
    await build_agent(spec, checkpointer=InMemorySaver()).ainvoke(
        {"messages": [HumanMessage(content="chào")]},
        config={"configurable": {"thread_id": "t-recall"}},
        context=context,
    )


async def test_recalled_facts_reach_the_model_framed_as_reference_data() -> None:
    """The fact arrives, and it arrives wrapped.

    Asserting the frame and not only the content is the point: pasted in bare, a
    remembered sentence is indistinguishable from something the user just asked
    for, and memory is written from documents a customer supplied.
    """
    model = _model()
    recall = _FakeRecall(facts=(_Fact("Anh An thích gọi buổi sáng."),))

    await _run(_spec(model, recall), subject_ref="account:acme")

    system = model.system_prompts[0]
    assert "Anh An thích gọi buổi sáng." in system
    assert "KHÔNG phải yêu cầu" in system or "không phải yêu cầu" in system
    assert system.startswith(WORKER_PROMPT), "the worker prompt still leads"


async def test_the_model_does_not_get_to_choose_what_is_recalled() -> None:
    """Every part of the question comes off the run, not off the conversation."""
    recall = _FakeRecall(facts=(_Fact("x"),))

    await _run(_spec(_model(), recall), subject_ref="account:acme")

    [asked] = recall.asked
    assert asked["subject_refs"] == ("account:acme",)
    assert asked["worker_id"] == "sales_chat"
    assert asked["tenant_id"] == uuid.UUID(int=1)
    assert asked["workspace_id"] == uuid.UUID(int=2)
    assert asked["now"] == NOW, "the validity window is judged at the run's clock"


async def test_a_run_about_no_record_recalls_nothing_at_all() -> None:
    """Not "every subject" — the database is never even asked."""
    recall = _FakeRecall(facts=(_Fact("khong duoc xuat hien"),))
    model = _model()

    await _run(_spec(model, recall), subject_ref=None)

    assert recall.asked == []
    assert model.system_prompts[0] == WORKER_PROMPT


async def test_a_recall_that_fails_leaves_the_run_going_without_memory() -> None:
    """A slow or broken database degrades the agent to one that has forgotten —
    which is every run before this existed — not to one that stops."""
    model = _model("vẫn trả lời")
    recall = _FakeRecall(raises=RuntimeError("database gone"))

    await _run(_spec(model, recall), subject_ref="account:acme")

    assert model.system_prompts[0] == WORKER_PROMPT
    assert len(model.calls) == 1, "the model was still called"


async def test_nothing_recalled_leaves_the_prompt_untouched() -> None:
    model = _model()

    await _run(_spec(model, _FakeRecall(facts=())), subject_ref="account:acme")

    assert model.system_prompts[0] == WORKER_PROMPT


async def test_a_copy_with_no_frame_is_refused_when_the_middleware_is_built() -> None:
    """At construction, not at the first model call: the alternative is a host
    discovering in production that memory was being pasted in unframed."""
    with pytest.raises(ValueError, match="recalled_memory_frame"):
        RecalledMemoryMiddleware(_FakeRecall(), copy=COPY_WITHOUT_FRAME, clock=lambda: NOW)


def test_recall_joins_the_platform_stack_only_when_a_host_wires_it() -> None:
    """Both directions. A context that stores no memory must not be handed a
    middleware that queries one, and a context that does must not have to
    remember to add it."""
    without = platform_middleware(_spec(_model(), None))
    assert not any(isinstance(m, RecalledMemoryMiddleware) for m in without)

    with_recall = platform_middleware(_spec(_model(), _FakeRecall()))
    assert any(isinstance(m, RecalledMemoryMiddleware) for m in with_recall)
