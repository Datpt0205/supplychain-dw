"""The sign-off of a coded product (step 9), as an approval-gated graph
(stage-1 ticket 04, ADR 0016 and ADR 0020).

The review graph's pattern (`advance_product_case_graph.py`), with the steps
in a loop: one node pauses on LangGraph's own `interrupt()` for the CURRENT
sign-off step, and after each decision the graph either moves to the next
step or applies the outcome through `apply_product_action`. No model call, no
tool, no SQL and no concrete adapter here; the case records come in by
injection. Its own worker, so the review graph's runs already waiting on
graph 1.0.0 stay resumable exactly as they were.

- **The order is the one the case was submitted under.** The steps (key,
  label, `required_scope`) are resolved from the tenant's
  `supply_chain_product_approvals` when the run is started
  (`application.product_reviews`) and travel in its input. Each pause is a new
  approval of type `supply_chain.product_action.signoff`, stamped with that
  step's scope and naming the step; a policy change reaches only cases
  submitted after it.
- **Every step must approve.** An approval moves to the next step (audited on
  the case as `signoff_step_approved`, by its decider); the last approval
  applies `signoff_approve` (→ `ready_to_order`). A step not approved applies
  `signoff_reject` (→ `item_coding`) at once, the decider's comment the
  reason; later steps are never raised. The item code and SKUs are kept.
- **The decider is the actor**, read from the resume value's `decided_by`
  that `ApproveAndResumeService.decide` builds from the decider's verified
  context, never from the interrupt payload. Database access still runs under
  the run's own tenant and workspace.
- **The case may have moved on while a step was decided** (a person may
  cancel a case waiting for sign-off). After every decision the graph reads
  the case again; if it is no longer waiting on THIS sign-off round, it
  applies nothing, raises no further step, audits that, and completes with
  outcome `superseded`.
- **The pause is the node's only effect before `interrupt()`**, as in the
  review graph: the node re-executes from its top on resume.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import get_runtime
from langgraph.types import interrupt

from dw_agent_runtime.context import access_context_from_run
from dw_agent_runtime.contracts import RunContext
from dw_kernel.errors import NotFoundError
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.application.ports import ProductCaseReviewPort
from dw_supply_chain.application.product_case_audit import product_case_audit
from dw_supply_chain.domain.product_development_case import (
    ProductAction,
    ProductActionInput,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
    apply_product_action,
)
from dw_supply_chain.workflows.advance_product_case_graph import (
    APPROVAL_TYPE_PREFIX,
    BOD_REVIEW_CASE_KEY,
    SUPERSEDED,
)

WORKER_ID = "supply_chain_product_signoff"
WORKER_VERSION = "1.0.0"
GRAPH_VERSION = "1.0.0"

SIGNOFF_APPROVAL_TYPE = f"{APPROVAL_TYPE_PREFIX}signoff"
# The payload key naming the case: the review's, so the case page, the
# reconcile lane and the Zalo subject-version port find both the same way.
SIGNOFF_CASE_KEY = BOD_REVIEW_CASE_KEY


class SignoffStepState(TypedDict):
    step: str
    label: str
    required_scope: str


class ProductSignoffState(TypedDict, total=False):
    product_dev_case_id: str
    proposal_code: str
    product_name: str
    item_code: str
    signoff_round: int
    steps: list[SignoffStepState]
    step_index: int
    approved: bool
    approver_comment: str
    decided_by: str
    outcome: str


def _request_signoff(state: ProductSignoffState) -> ProductSignoffState:
    index = state.get("step_index", 0)
    steps = state["steps"]
    step = steps[index]
    decision: dict[str, Any] = interrupt(
        {
            "approval_type": SIGNOFF_APPROVAL_TYPE,
            "reason": (
                f"Ký hồ sơ ({step['label']}, bước {index + 1}/{len(steps)}):"
                f" {state['proposal_code']} · {state['product_name']},"
                f" mã hàng {state['item_code']}"
            ),
            SIGNOFF_CASE_KEY: state["product_dev_case_id"],
            "proposal_code": state["proposal_code"],
            "product_name": state["product_name"],
            "item_code": state["item_code"],
            "signoff_round": state["signoff_round"],
            "step": step["step"],
            "step_label": step["label"],
            "step_no": index + 1,
            "steps": [{"step": s["step"], "label": s["label"]} for s in steps],
            "required_scope": step["required_scope"],
        }
    )
    approved = decision.get("approved")
    if not isinstance(approved, bool):
        # Neither yes nor no is read as neither: the run fails, the case keeps
        # waiting, and the reconcile lane raises the sign-off afresh.
        raise ValueError("the decision carries no yes or no")
    return {
        "approved": approved,
        "approver_comment": str(decision.get("comment", "")),
        "decided_by": str(decision.get("decided_by", "")),
    }


def _after_decision(state: ProductSignoffState) -> Literal["next_step", "apply"]:
    more = state.get("step_index", 0) + 1 < len(state["steps"])
    return "next_step" if state.get("approved") and more else "apply"


def _after_next_step(state: ProductSignoffState) -> Literal["request_signoff", "__end__"]:
    return "__end__" if state.get("outcome") == SUPERSEDED else "request_signoff"


def _decider(state: ProductSignoffState) -> tuple[uuid.UUID, RunContext, AccessContext]:
    # The decider, from the resume value `decide` built. A resume without one
    # fails the run here (ValueError) rather than recording nobody.
    decided_by = uuid.UUID(state.get("decided_by", ""))
    run_context = get_runtime(RunContext).context
    # The run's tenant and workspace bound the reads and writes; the decider
    # is who the step and the audit name.
    context = access_context_from_run(run_context).model_copy(update={"principal_id": decided_by})
    return decided_by, run_context, context


def _still_waiting(case: ProductDevelopmentCase, state: ProductSignoffState) -> bool:
    return (
        case.state is ProductDevState.PENDING_SIGNOFF
        and case.signoff_round == state["signoff_round"]
    )


class _Nodes:
    def __init__(self, repo: ProductCaseReviewPort, ids: IdGenerator, clock: UtcClock) -> None:
        self.repo, self.ids, self.clock = repo, ids, clock

    async def _case(
        self, context: AccessContext, state: ProductSignoffState
    ) -> ProductDevelopmentCase:
        case_id = ProductDevelopmentCaseId(uuid.UUID(state["product_dev_case_id"]))
        case = await self.repo.get(context, case_id)
        if case is None:
            raise NotFoundError("product case not found", details={"case_id": str(case_id)})
        return case

    def _step_details(
        self, state: ProductSignoffState, run_context: RunContext
    ) -> dict[str, object]:
        index = state.get("step_index", 0)
        return {
            "signoff_round": state["signoff_round"],
            "step": state["steps"][index]["step"],
            "step_no": index + 1,
            "run_id": str(run_context.run_id),
        }

    async def _superseded(
        self,
        context: AccessContext,
        case: ProductDevelopmentCase,
        state: ProductSignoffState,
        run_context: RunContext,
    ) -> ProductSignoffState:
        await self.repo.append_audit(
            context,
            product_case_audit(
                context,
                self.ids,
                self.clock,
                case,
                "signoff_superseded",
                {
                    **self._step_details(state, run_context),
                    "approved": bool(state.get("approved")),
                    "state": case.state.value,
                    "case_signoff_round": case.signoff_round,
                },
            ),
        )
        return {"outcome": SUPERSEDED}

    async def next_step(self, state: ProductSignoffState) -> ProductSignoffState:
        """A step approved and another follows: record who signed, then
        raise the next one, unless the case left this sign-off meanwhile."""
        _, run_context, context = _decider(state)
        case = await self._case(context, state)
        if not _still_waiting(case, state):
            return await self._superseded(context, case, state, run_context)
        await self.repo.append_audit(
            context,
            product_case_audit(
                context,
                self.ids,
                self.clock,
                case,
                "signoff_step_approved",
                self._step_details(state, run_context),
            ),
        )
        return {"step_index": state.get("step_index", 0) + 1}

    async def apply(self, state: ProductSignoffState) -> ProductSignoffState:
        decided_by, run_context, context = _decider(state)
        case = await self._case(context, state)
        approved = bool(state.get("approved"))
        if not _still_waiting(case, state):
            return await self._superseded(context, case, state, run_context)
        action = ProductAction.SIGNOFF_APPROVE if approved else ProductAction.SIGNOFF_REJECT
        before = case.state
        apply_product_action(
            case,
            action=action,
            given=ProductActionInput(
                actor_id=decided_by,
                reason=None if approved else state.get("approver_comment"),
            ),
        )
        await self.repo.save(
            context,
            case,
            audit=product_case_audit(
                context,
                self.ids,
                self.clock,
                case,
                action.value,
                {
                    "from_state": before.value,
                    "to_state": case.state.value,
                    **self._step_details(state, run_context),
                },
            ),
        )
        return {"outcome": action.value}


def build_product_signoff_graph(
    repo: ProductCaseReviewPort, ids: IdGenerator, clock: UtcClock
) -> StateGraph:  # type: ignore[type-arg]
    """Uncompiled, closure-injected (`registry.GraphFactory`'s contract): the
    runner compiles it with the tenant-aware checkpointer."""
    nodes = _Nodes(repo, ids, clock)
    graph: StateGraph = StateGraph(ProductSignoffState)  # type: ignore[type-arg]
    graph.add_node("request_signoff", _request_signoff)
    graph.add_node("next_step", nodes.next_step)
    graph.add_node("apply", nodes.apply)
    graph.add_edge(START, "request_signoff")
    graph.add_conditional_edges("request_signoff", _after_decision)
    graph.add_conditional_edges("next_step", _after_next_step)
    graph.add_edge("apply", END)
    return graph
