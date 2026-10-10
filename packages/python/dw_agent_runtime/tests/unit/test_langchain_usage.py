"""The LangChain path bills like the gateway: tokens from the callback,
price from the profile's own route, one run id per tracked invocation."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from dw_agent_runtime.adapters.chat_model import MockChatModel
from dw_agent_runtime.adapters.langchain_usage import (
    LangchainUsageMeter,
    metered_invoke,
    usage_run_context,
)
from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.model.gateway import ModelUsage
from dw_agent_runtime.model.profiles import ModelRoute
from dw_agent_runtime.ports import ModelRequest
from dw_platform.application.access_context import AccessContext

pytestmark = pytest.mark.unit

# Every tracked invocation names the prompt it ran; these tests are about the
# tally, so one pin serves them all.
_LOOP = {"prompt_id": "sales_chat.assistant", "prompt_version": "1.0.0"}

_ROUTE = ModelRoute(
    provider="openai_responses",
    model="gpt-5.6-luna",
    price_per_million_input=0.20,
    price_per_million_output=1.20,
)


class _Recorder:
    def __init__(self) -> None:
        self.rows: list[tuple[RunContext, ModelRequest, ModelUsage]] = []

    async def record(
        self, run_context: RunContext, request: ModelRequest, usage: ModelUsage
    ) -> None:
        self.rows.append((run_context, request, usage))


class _Profiles:
    def resolve(self, profile_id: str) -> Any:
        return SimpleNamespace(chat=_ROUTE)


def _context() -> AccessContext:
    return AccessContext(
        tenant_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
        roles=frozenset({"sales"}),
        scopes=frozenset({"crm.account.read"}),
        plan_id="pro",
    )


async def test_a_tracked_call_lands_in_the_ledger_priced_by_the_route() -> None:
    recorder = _Recorder()
    meter = LangchainUsageMeter(profiles=_Profiles(), recorder=recorder)  # type: ignore[arg-type]
    model = MockChatModel(responses=[AIMessage(content="x" * 400)], mock_reply="ok")

    answer = await metered_invoke(
        model,
        [{"role": "user", "content": "y" * 400}],
        meter=meter,
        context=_context(),
        worker_id="sales_crm.stakeholder_digest",
        profile_id="gateway",
        task="roster_digest",
        prompt_id="sales_crm.stakeholder_digest",
        prompt_version="1.1.0",
        timeout_seconds=5.0,
    )
    assert isinstance(answer, AIMessage)
    assert len(recorder.rows) == 1
    run_context, request, usage = recorder.rows[0]
    assert run_context.worker_id == "sales_crm.stakeholder_digest"
    assert request.task == "roster_digest" and request.prompt_id == "sales_crm.stakeholder_digest"
    # The mock speaks ~4 chars per token: 400-char turns become ~100 tokens.
    assert usage.input_tokens == 100 and usage.output_tokens == 100
    assert usage.cost_usd == pytest.approx((100 * 0.20 + 100 * 1.20) / 1_000_000)


async def test_without_a_meter_the_call_still_answers_and_nothing_records() -> None:
    model = MockChatModel(responses=[], mock_reply="ok")
    answer = await metered_invoke(
        model,
        [{"role": "user", "content": "hi"}],
        meter=None,
        context=_context(),
        worker_id="x",
        profile_id="gateway",
        task="t",
        prompt_id="p",
        prompt_version="1.0.0",
        timeout_seconds=5.0,
    )
    assert isinstance(answer, AIMessage)


async def test_usage_survives_the_block_raising() -> None:
    recorder = _Recorder()
    meter = LangchainUsageMeter(profiles=_Profiles(), recorder=recorder)  # type: ignore[arg-type]
    model = MockChatModel(responses=[], mock_reply="answered before the crash")
    run_context = usage_run_context(_context(), worker_id="person_research")

    with pytest.raises(RuntimeError):
        async with meter.track(run_context, profile_id="gateway", task="t", **_LOOP) as callbacks:
            await model.ainvoke(
                [{"role": "user", "content": "hi"}],
                config=RunnableConfig(callbacks=list(callbacks)),
            )
            raise RuntimeError("the agent died after spending")
    # The spend still reached the ledger — a truncated run is the one whose
    # bill gets asked about.
    assert len(recorder.rows) == 1
    assert recorder.rows[0][0].run_id == run_context.run_id


def test_a_minted_run_context_carries_the_caller_not_an_invented_tenant() -> None:
    context = _context()
    run_context = usage_run_context(context, worker_id="sales_crm.stakeholder_arrange")
    assert run_context.tenant_id == context.tenant_id
    assert run_context.workspace_id == context.workspace_id
    assert run_context.actor_id == context.principal_id
    assert run_context.trace_id == str(run_context.run_id)


# ---------------------------------------------------- nested trackers ------
# LangChain propagates a parent's callbacks into every nested runnable, which
# is what lets the runner meter a whole graph from one seam — and is also why
# a handler attached inside a node sees the same calls twice. Measured
# 2026-08-28: sales_research and sales_signals were billing double.


async def test_an_inner_tracker_keeps_its_label_and_the_outer_stops_double_billing() -> None:
    """The shape the runner and a lane agent actually make.

    The outer handler observes the inner call too, so recording both in full
    put the same tokens on the ledger twice.
    """
    recorder = _Recorder()
    meter = LangchainUsageMeter(profiles=_Profiles(), recorder=recorder)  # type: ignore[arg-type]
    model = MockChatModel(responses=[AIMessage(content="x" * 400)], mock_reply="ok")
    run_context = usage_run_context(_context(), worker_id="sales_research")

    async with meter.track(run_context, profile_id="gateway", task="agent_loop", **_LOOP) as outer:
        async with meter.track(
            run_context, profile_id="gateway", task="lane_financials", **_LOOP
        ) as inner:
            await model.ainvoke(
                [{"role": "user", "content": "y" * 400}],
                config={"callbacks": [*outer, *inner]},
            )

    assert [row[1].task for row in recorder.rows] == ["lane_financials"]
    assert recorder.rows[0][2].input_tokens == 100
    assert recorder.rows[0][2].output_tokens == 100


async def test_the_outer_still_bills_the_calls_no_inner_tracker_claimed() -> None:
    """A graph that meters itself in one node and not in another.

    The un-lane'd call has to reach the ledger, or the fix for double billing
    becomes a hole that hides spend.
    """
    recorder = _Recorder()
    meter = LangchainUsageMeter(profiles=_Profiles(), recorder=recorder)  # type: ignore[arg-type]
    lane_model = MockChatModel(responses=[AIMessage(content="x" * 400)], mock_reply="ok")
    loose_model = MockChatModel(responses=[AIMessage(content="x" * 800)], mock_reply="ok")
    run_context = usage_run_context(_context(), worker_id="sales_signals")

    async with meter.track(run_context, profile_id="gateway", task="agent_loop", **_LOOP) as outer:
        async with meter.track(
            run_context, profile_id="gateway", task="news_agent", **_LOOP
        ) as inner:
            await lane_model.ainvoke(
                [{"role": "user", "content": "y" * 400}], config={"callbacks": [*outer, *inner]}
            )
        await loose_model.ainvoke(
            [{"role": "user", "content": "y" * 800}],
            config=RunnableConfig(callbacks=list(outer)),
        )

    by_task = {row[1].task: row[2] for row in recorder.rows}
    assert set(by_task) == {"news_agent", "agent_loop"}
    assert by_task["news_agent"].input_tokens == 100
    # 300 observed by the outer, 100 already claimed by the inner.
    assert by_task["agent_loop"].input_tokens == 200
    total_in = sum(usage.input_tokens for usage in by_task.values())
    assert total_in == 300  # exactly what was spent, counted once


async def test_a_lone_tracker_is_untouched() -> None:
    """The probe that must fail if the tally leaks across runs.

    Person research and the enrichment agent run outside any graph, so they
    are the outermost tracker and must still bill their whole call.
    """
    recorder = _Recorder()
    meter = LangchainUsageMeter(profiles=_Profiles(), recorder=recorder)  # type: ignore[arg-type]
    model = MockChatModel(responses=[AIMessage(content="x" * 400)], mock_reply="ok")

    for _ in range(2):
        run_context = usage_run_context(_context(), worker_id="person_research")
        async with meter.track(
            run_context, profile_id="gateway", task="person_research", **_LOOP
        ) as cb:
            await model.ainvoke(
                [{"role": "user", "content": "y" * 400}],
                config=RunnableConfig(callbacks=list(cb)),
            )

    assert [row[1].task for row in recorder.rows] == ["person_research", "person_research"]
    assert all(row[2].input_tokens == 100 for row in recorder.rows)
