"""The approval-gated PO-case transition graph.

Replaces the scaffold's own placeholder `workflows/graph.py` (a "summarise"
node nothing ever registered) — Supply Chain is the first bounded context to
actually wire a graph through `RuntimeSeam.graphs`/`.workers`, so there is no
in-context precedent; this mirrors `dw_agent_runtime.testing.demo_graph`
instead, the platform's own reference implementation for the pause/resume
shape (`interrupt()`, the worker YAML fields, `GraphFactory`'s "uncompiled,
closure-injected" contract).

Two nodes, always in the same order: `request_approval` unconditionally
pauses via LangGraph's own `interrupt()` — this graph is only ever started
for an action `AdvancePOCase` already determined needs approval (checked
against the tenant's `SupplyChainApprovalMatrix` BEFORE `runner.start()` is
called), so there is no "does this need approval" branch inside the graph
itself, the same way `demo_graph.py`'s own `human_review` node asks
unconditionally. `apply` performs the transition only if approved — through
`domain.po_case.apply_action`, the SAME dispatch `AdvancePOCase` calls
directly for an action that needs no approval, so there is exactly one
place that decides which `CaseAction` maps to which guarded method,
not one copy per caller.

No model call, no tool, no `ToolExecutor`/`AutonomyApprovalPolicy` — a plain
node calling `interrupt()` needs neither (confirmed against this platform's
own code before writing this: `AutonomyApprovalPolicy.decide()` only gates a
tool call inside an LLM agent loop, and never sees a node that pauses
because a business rule said so directly).
"""

from __future__ import annotations

import uuid
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import get_runtime
from langgraph.types import interrupt

from dw_agent_runtime.context import access_context_from_run
from dw_agent_runtime.contracts import RunContext
from dw_kernel.errors import NotFoundError
from dw_kernel.ports import IdGenerator, UtcClock
from dw_supply_chain.application.po_case_audit import po_case_audit
from dw_supply_chain.application.ports import PaperGatePort, POCaseRepositoryPort
from dw_supply_chain.application.production_gate import ProductionGateResolver
from dw_supply_chain.domain.po_case import GATED_ACTIONS, CaseAction, POCaseId, apply_action

WORKER_ID = "supply_chain_advance_case"
GRAPH_VERSION = "1.0.0"

# Every ApprovalRequest this graph raises has an approval_type starting with
# this. The composition root wires `strict_approval_prefixes` against
# exactly this prefix — separation of duties + a mandatory comment,
# platform-wide, not something a tenant's own approval-matrix override can
# weaken. See `approval_matrix.py`'s own docstring for why that split.
APPROVAL_TYPE_PREFIX = "supply_chain.case_action."


class AdvanceCaseState(TypedDict, total=False):
    po_case_id: str
    action: str
    reason: str | None
    approved: bool
    approver_comment: str
    applied: bool


def _request_approval(state: AdvanceCaseState) -> AdvanceCaseState:
    action = state["action"]
    decision: dict[str, Any] = interrupt(
        {
            "approval_type": f"{APPROVAL_TYPE_PREFIX}{action}",
            "reason": state.get("reason")
            or f"'{action}' on PO case {state['po_case_id']} requires approval",
            "po_case_id": state["po_case_id"],
            "action": action,
        }
    )
    return {
        "approved": bool(decision.get("approved")),
        "approver_comment": str(decision.get("comment", "")),
    }


def _build_apply_node(
    repo: POCaseRepositoryPort,
    ids: IdGenerator,
    clock: UtcClock,
    production_gate: ProductionGateResolver,
    papers: PaperGatePort,
) -> Any:
    # Untyped return on purpose: LangGraph's own `add_node` overloads infer
    # `NodeInputT` from a directly-passed function's real signature, and
    # mypy does not unify that inference through an explicit `Callable[...]`
    # alias here (reproduced: annotating this as `Callable[[AdvanceCaseState],
    # Awaitable[AdvanceCaseState]]` makes `add_node` see `_Node[Never]`
    # instead of `_Node[AdvanceCaseState]`) — a stub-matching quirk, not a
    # real type hole; `_apply`'s own signature below is still fully typed.
    async def _apply(state: AdvanceCaseState) -> AdvanceCaseState:
        if not state.get("approved"):
            return {"applied": False}
        # The requester's own verified context, re-derived from the run
        # exactly like every other in-run DB access in this platform —
        # never the approver's (`ApproveAndResumeService` already replays
        # the requester's roles/scopes/clearance on resume, not the
        # approver's; this is that same rule reaching the repository call).
        run_context = get_runtime(RunContext).context
        context = access_context_from_run(run_context)
        case_id = POCaseId(uuid.UUID(state["po_case_id"]))
        case = await repo.get(context, case_id)
        if case is None:
            raise NotFoundError("PO case not found", details={"case_id": str(case_id)})
        action = CaseAction(state["action"])
        before = case.state
        # Step 13's gate is asked when the step is applied, not when it was
        # requested: an approval decided after the test failed still refuses.
        gate = (
            await production_gate.for_case(context, case.id.value)
            if action in GATED_ACTIONS
            else None
        )
        # The paper the tenant's policy names for the step (ticket
        # ai-automation/15): asked when the step is taken, like the gate.
        await papers.require(context, case.id.value, action)
        apply_action(case, action=action, reason=state.get("reason"), gate=gate)
        # The step and its audit event are one transaction (ticket P2), under
        # the requester who asked for it; the run id ties it to the decision
        # the approval inbox audited.
        await repo.save(
            context,
            case,
            audit=po_case_audit(
                context,
                ids,
                clock,
                case.id,
                action.value,
                {
                    "from_state": before.value,
                    "to_state": case.state.value,
                    "reason": state.get("reason"),
                    "run_id": str(run_context.run_id),
                },
            ),
        )
        return {"applied": True}

    return _apply


def build_advance_case_graph(
    repo: POCaseRepositoryPort,
    ids: IdGenerator,
    clock: UtcClock,
    production_gate: ProductionGateResolver,
    papers: PaperGatePort,
) -> StateGraph:  # type: ignore[type-arg]
    """`repo` is injected by closure, never a concrete adapter imported
    here — the same "workflow nodes take what they need by injection" rule
    every graph in this platform follows. Returns an UNCOMPILED graph on
    purpose: `LangGraphWorkflowRunner` compiles it with the tenant-aware
    checkpointer (`registry.GraphFactory`'s own contract)."""
    graph: StateGraph = StateGraph(AdvanceCaseState)  # type: ignore[type-arg]
    graph.add_node("request_approval", _request_approval)
    graph.add_node("apply", _build_apply_node(repo, ids, clock, production_gate, papers))
    graph.add_edge(START, "request_approval")
    graph.add_edge("request_approval", "apply")
    graph.add_edge("apply", END)
    return graph
