"""Unit: the preparation graph, pause and resume for real (ticket
ai-automation/05). LangGraph's own in-memory checkpointer, the real preparer
and applier over the in-memory world: real `interrupt()` and
`Command(resume=...)` semantics, no database.

Also a measurement of the library, not of this code (failure-modes #4): a
thread whose last pass ended is started again from START with new input, and
a pass that stopped at the interrupt is too. The lane re-prepares a step entry
on its one thread and relies on both.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from dw_agent_runtime.approval_flow import ApproveAndResumeService
from dw_agent_runtime.contracts import RunContext
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.domain.approval import APPROVALS_DECIDE, ApprovalRequest
from dw_supply_chain.application.step_preparation import lane_actor, preparation_input
from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.product_development_case import ProductDevState
from dw_supply_chain.testing.step_preparation import SAMPLE_TESTING, StepWorld
from dw_supply_chain.workflows.step_preparation_graph import build_step_preparation_graph

pytestmark = pytest.mark.unit

RND = "supply_chain.duty.rnd"


class Run:
    def __init__(self, world: StepWorld, thread: uuid.UUID | None = None) -> None:
        self.graph: Any = build_step_preparation_graph(world.preparer(), world.applier()).compile(
            checkpointer=MemorySaver()
        )
        self.world = world
        self.thread = thread or uuid.uuid4()

    def context(self) -> RunContext:
        run_id = uuid.uuid4()
        return RunContext(
            run_id=run_id,
            thread_id=self.thread,
            tenant_id=self.world.tenant_id,
            workspace_id=self.world.workspace_id,
            actor_id=lane_actor(),
            worker_id="supply_chain_step_preparation",
            worker_version="1.0.0",
            channel="worker",
            plan_id="professional",
            roles=frozenset(),
            scopes=frozenset(),
            trace_id=str(run_id),
        )

    def config(self) -> dict[str, Any]:
        return {"configurable": {"thread_id": str(self.thread)}}

    async def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        state: dict[str, Any] = await self.graph.ainvoke(
            payload, self.config(), context=self.context()
        )
        return state

    async def resume(self, value: dict[str, Any]) -> dict[str, Any]:
        state: dict[str, Any] = await self.graph.ainvoke(
            Command(resume=value), self.config(), context=self.context()
        )
        return state


def _world() -> tuple[StepWorld, dict[str, Any]]:
    world = StepWorld()
    case, entered = world.add_case(ProductDevState.SAMPLE_TESTING)
    report = world.add_document(case, DocumentType.SAMPLE_EVALUATION)
    world.add_reading(report, {"result": {"value": "pass", "quote": "Đạt"}})
    assert entered.id is not None
    return world, preparation_input(case, entered.id, "1.0.0", SAMPLE_TESTING, RND)


async def test_a_prepared_step_pauses_on_one_stamped_approval() -> None:
    world, payload = _world()
    state = await Run(world).start(payload)

    approval = state["__interrupt__"][0].value
    assert approval["approval_type"] == "supply_chain.step_proposal.pass_sample"
    # Who decides is the duty of the step, stamped when the run started.
    assert approval["required_scope"] == RND
    assert approval["required_input"] == ["evaluated_on", "conclusion"]
    assert approval["subject_version"]
    assert world.cases.cases[uuid.UUID(payload["product_dev_case_id"])].state is (
        ProductDevState.SAMPLE_TESTING
    )


async def test_the_stamp_keeps_an_administrator_without_the_duty_out() -> None:
    """The stamp written by the graph is what the platform decides by: with
    `approvals.decide` alone (an administrator's), the proposal cannot be
    decided; with the duty too, it can."""
    world, payload = _world()
    approval = (await Run(world).start(payload))["__interrupt__"][0].value
    request = ApprovalRequest(
        id=uuid.uuid4(),
        tenant_id=TenantId(world.tenant_id),
        workspace_id=WorkspaceId(world.workspace_id),
        approval_type=approval["approval_type"],
        requested_by=UserId(lane_actor()),
        reason=approval["reason"],
        payload=approval,
        required_scope=approval.get("required_scope"),
    )

    def person(scopes: frozenset[str], roles: frozenset[str]) -> AccessContext:
        return world.context(scopes=scopes).model_copy(update={"roles": roles})

    authz = ScopeAuthorizationService()
    admin = person(frozenset({APPROVALS_DECIDE}), frozenset({"platform_admin"}))
    holder = person(frozenset({APPROVALS_DECIDE, RND}), frozenset({"member"}))
    assert not ApproveAndResumeService.may_decide(request, admin, authz)
    assert ApproveAndResumeService.may_decide(request, holder, authz)


async def test_approving_resumes_as_the_decider_with_the_typed_result() -> None:
    world, payload = _world()
    run = Run(world)
    await run.start(payload)
    decider = uuid.uuid4()
    state = await run.resume(
        {
            "approved": True,
            "comment": "đạt",
            "decided_by": str(decider),
            "input": {"evaluated_on": "2026-10-09", "conclusion": "Đạt"},
        }
    )
    assert state["outcome"] == "pass_sample"
    case = world.cases.cases[uuid.UUID(payload["product_dev_case_id"])]
    assert case.state is ProductDevState.PENDING_BOD_REVIEW
    assert world.drafts.rows[-1].created_by == decider


async def test_a_resume_naming_a_decider_only_in_the_payload_fails() -> None:
    world, payload = _world()
    run = Run(world)
    await run.start(payload)
    with pytest.raises(ValueError):
        await run.resume({"approved": True, "comment": "ok"})
    case = world.cases.cases[uuid.UUID(payload["product_dev_case_id"])]
    assert case.state is ProductDevState.SAMPLE_TESTING


async def test_nothing_to_propose_ends_without_an_approval() -> None:
    world, payload = _world()
    world.readings.rows.clear()
    state = await Run(world).start(payload)
    assert "__interrupt__" not in state
    assert state["outcome"] == "not_prepared"
    assert state["reason"] == "source_not_read_yet:sample_evaluation"


async def test_a_thread_is_prepared_again_from_start_after_a_pass_ended() -> None:
    world, payload = _world()
    again = await _same_saver_restart(world, uuid.uuid4(), payload)
    assert again["__interrupt__"][0].value["approval_type"].endswith("pass_sample")


async def _same_saver_restart(
    world: StepWorld, thread: uuid.UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """Two passes on one thread through ONE checkpointer: the first ends, the
    second (new input, as the lane sends) starts from START."""
    saver = MemorySaver()
    run = Run(world, thread)
    run.graph = build_step_preparation_graph(world.preparer(), world.applier()).compile(
        checkpointer=saver
    )
    readings = list(world.readings.rows)
    world.readings.rows.clear()
    ended = await run.start(payload)
    assert ended["outcome"] == "not_prepared" and "__interrupt__" not in ended
    world.readings.rows.extend(readings)
    return await run.start(payload)


async def test_a_thread_paused_at_its_interrupt_restarts_from_start_on_new_input() -> None:
    world, payload = _world()
    saver = MemorySaver()
    run = Run(world)
    run.graph = build_step_preparation_graph(world.preparer(), world.applier()).compile(
        checkpointer=saver
    )
    first = await run.start(payload)
    first_draft = first["__interrupt__"][0].value["drafts"][0]["draft_id"]
    # Superseded: the run was cancelled at its pause; the lane starts the
    # thread again with fresh input, and the graph prepares again.
    second = await run.start(payload)
    assert second["__interrupt__"][0].value["drafts"][0]["draft_id"] == first_draft
    assert len(world.records.outcomes()) == 2
