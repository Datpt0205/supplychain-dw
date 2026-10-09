"""Preparing a step and waiting for a person to approve the move (ADR 0025;
ticket ai-automation/05).

Three nodes, the review graph's shape (`advance_product_case_graph.py`) with a
preparation in front:

- `prepare` asks the injected preparer (`application.step_preparation.
  PrepareStep`) for the proposal, as the run's own principal (the lane). It
  reads, drafts and records; it never moves the case. Nothing to propose ends
  the run (`not_prepared`, its reason recorded for the case page).
- `request` pauses on LangGraph's own `interrupt()` with ONE approval:
  `supply_chain.step_proposal.<action>`, stamped with the `required_scope` the
  lane resolved from the tenant's duty policy when it started the run, and the
  subject version and typed input the preparer wrote. Written here from code,
  never by a model. Its only effect before the pause is the pause: the node
  re-executes from its top on resume.
- `apply` hands the decision to the injected applier (`application.
  step_proposals.ApplyStepProposal`) as the DECIDER, read from the resume
  value's `decided_by` (`ApproveAndResumeService.decide` builds it from the
  decider's verified context), bounded by the run's tenant and workspace. The
  typed result is the resume value's `input`, never the interrupt payload.

No model call, tool, SQL or concrete adapter here.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, Literal, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import get_runtime
from langgraph.types import interrupt

from dw_agent_runtime.context import access_context_from_run
from dw_agent_runtime.contracts import RunContext
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.application.step_preparation import PreparationRequest, Prepared
from dw_supply_chain.domain.step_proposal import PreparationOutcome, proposal_type
from dw_supply_chain.step_preparation_policy import PreparedStep

WORKER_ID = "supply_chain_step_preparation"
WORKER_VERSION = "1.0.0"
GRAPH_VERSION = "1.0.0"


class StepPreparerPort(Protocol):
    async def prepare(self, context: AccessContext, request: PreparationRequest) -> Prepared: ...


class StepProposalApplierPort(Protocol):
    async def apply(
        self,
        context: AccessContext,
        payload: Mapping[str, Any],
        *,
        approved: bool,
        comment: str,
        typed_input: Mapping[str, str],
        run_id: uuid.UUID | None,
    ) -> str: ...


class StepPreparationState(TypedDict, total=False):
    product_dev_case_id: str
    transition_id: str
    policy_version: str
    step: dict[str, Any]
    required_scope: str
    outcome: str
    reason: str
    proposal: dict[str, Any]
    approved: bool
    approver_comment: str
    decided_by: str
    typed_input: dict[str, str]


def _reason(payload: Mapping[str, Any]) -> str:
    drafts = ", ".join(str(d.get("doc_type")) for d in payload.get("drafts") or [])
    paper = " với chứng từ đã soạn: " + drafts if drafts else ""
    return (
        f"AI đề xuất bước {payload.get('action')}: {payload.get('proposal_code')} ·"
        f" {payload.get('product_name')}{paper}"
    )


def _request(state: StepPreparationState) -> StepPreparationState:
    proposal = state["proposal"]
    step = PreparedStep.model_validate(state["step"])
    decision: dict[str, Any] = interrupt(
        {
            **proposal,
            "approval_type": proposal_type(step.action),
            "reason": _reason(proposal),
            "required_scope": state["required_scope"],
        }
    )
    approved = decision.get("approved")
    if not isinstance(approved, bool):
        # Neither yes nor no is read as neither: the run fails and the lane
        # prepares the step again.
        raise ValueError("the decision carries no yes or no")
    raw = decision.get("input")
    typed = {str(k): str(v) for k, v in raw.items()} if isinstance(raw, Mapping) else {}
    return {
        "approved": approved,
        "approver_comment": str(decision.get("comment", "")),
        "decided_by": str(decision.get("decided_by", "")),
        "typed_input": typed,
    }


def _after_prepare(state: StepPreparationState) -> Literal["request", "__end__"]:
    return "request" if state.get("outcome") == PreparationOutcome.PROPOSED.value else "__end__"


class _Nodes:
    def __init__(self, preparer: StepPreparerPort, applier: StepProposalApplierPort) -> None:
        self.preparer, self.applier = preparer, applier

    async def prepare(self, state: StepPreparationState) -> StepPreparationState:
        run_context = get_runtime(RunContext).context
        prepared = await self.preparer.prepare(
            access_context_from_run(run_context),
            PreparationRequest.of(state, run_context.run_id),
        )
        return {
            "outcome": prepared.outcome.value,
            "reason": prepared.reason or "",
            "proposal": prepared.payload,
        }

    async def apply(self, state: StepPreparationState) -> StepPreparationState:
        # A resume without a decider fails the run here rather than record nobody.
        decided_by = uuid.UUID(state.get("decided_by", ""))
        run_context = get_runtime(RunContext).context
        context = access_context_from_run(run_context).model_copy(
            update={"principal_id": decided_by}
        )
        outcome = await self.applier.apply(
            context,
            state["proposal"],
            approved=bool(state.get("approved")),
            comment=state.get("approver_comment", ""),
            typed_input=state.get("typed_input", {}),
            run_id=run_context.run_id,
        )
        return {"outcome": outcome}


def build_step_preparation_graph(
    preparer: StepPreparerPort, applier: StepProposalApplierPort
) -> StateGraph:  # type: ignore[type-arg]
    """Uncompiled, closure-injected (`registry.GraphFactory`'s contract)."""
    nodes = _Nodes(preparer, applier)
    graph: StateGraph = StateGraph(StepPreparationState)  # type: ignore[type-arg]
    graph.add_node("prepare", nodes.prepare)
    graph.add_node("request", _request)
    graph.add_node("apply", nodes.apply)
    graph.add_edge(START, "prepare")
    graph.add_conditional_edges("prepare", _after_prepare)
    graph.add_edge("request", "apply")
    graph.add_edge("apply", END)
    return graph
