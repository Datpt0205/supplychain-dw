"""BGĐ's review of a passed sample (step 6), as an approval-gated graph
(stage-1 ticket 02, ADR 0016 and ADR 0020).

Shaped like `advance_case_graph.py`: one node pauses with LangGraph's own
`interrupt()`, one applies the outcome through the context's one dispatch,
`apply_product_action`. No model call, no tool, no SQL and no concrete
adapter here; the case records come in by injection.

What differs from the PO graph, and why:

- **A rejection is a step too.** The PO graph applies nothing when the
  approval is rejected (`test_advance_case_approval.py::
  test_rejecting_applies_nothing`). Here BGĐ not approving cancels the case
  (`bod_reject`, QE-08 provisional), with BGĐ's comment as the reason.
- **The decider is the actor.** The history row and the audit event of
  `bod_approve`/`bod_reject` name who decided, read from the resume value's
  `decided_by`, which `ApproveAndResumeService.decide` builds from the
  decider's verified context. Never from the interrupt payload: a key named
  `decided_by` there is not read. Database access still runs under the run's
  own tenant and workspace.
- **The case may have moved on while BGĐ thought.** A person may cancel a
  case waiting for BGĐ. On resume the graph reads the case again; if it is no
  longer waiting on THIS round's review, it applies nothing, audits that, and
  completes with outcome `superseded`.
- **The pause is the node's only effect before `interrupt()`.** The node
  re-executes from its top on resume, so it reads and writes nothing before
  pausing; everything that touches the case is in `apply`.

The approval's payload is written here, from the case and the tenant's
policy, never by a model: `required_scope` comes from
`supply_chain_product_approvals` as resolved when the review was raised
(`application.product_reviews`), and the runner stamps it on the approval row.
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
from dw_supply_chain.application.ports import ProductCaseReviewPort
from dw_supply_chain.application.product_case_audit import product_case_audit
from dw_supply_chain.domain.product_development_case import (
    ProductAction,
    ProductActionInput,
    ProductDevelopmentCaseId,
    ProductDevState,
    apply_product_action,
)

WORKER_ID = "supply_chain_advance_product_case"
WORKER_VERSION = "1.0.0"
GRAPH_VERSION = "1.0.0"

# Every approval a product case raises starts with this. The composition root
# adds it to `strict_approval_prefixes`: the requester cannot decide their own
# review, and a decision needs a comment.
APPROVAL_TYPE_PREFIX = "supply_chain.product_action."
BOD_REVIEW_APPROVAL_TYPE = f"{APPROVAL_TYPE_PREFIX}bod_review"
# The payload key naming the case, which the case page and the reconcile lane
# find the review by.
BOD_REVIEW_CASE_KEY = "product_dev_case_id"

# The run's result when the case had left this review before it was decided.
SUPERSEDED = "superseded"


class ProductReviewState(TypedDict, total=False):
    product_dev_case_id: str
    proposal_code: str
    product_name: str
    sample_round: int
    required_scope: str
    # The tờ trình AI drafted before the review was raised (ticket
    # ai-automation/10): its draft id, content hash and gaps; None: none.
    bod_submission: dict[str, Any] | None
    approved: bool
    approver_comment: str
    decided_by: str
    outcome: str


def _request_review(state: ProductReviewState) -> ProductReviewState:
    decision: dict[str, Any] = interrupt(
        {
            "approval_type": BOD_REVIEW_APPROVAL_TYPE,
            "reason": (
                f"BGĐ duyệt mẫu đã đạt: {state['proposal_code']} · {state['product_name']},"
                f" vòng mẫu {state['sample_round']}"
            ),
            BOD_REVIEW_CASE_KEY: state["product_dev_case_id"],
            "proposal_code": state["proposal_code"],
            "product_name": state["product_name"],
            "sample_round": state["sample_round"],
            "required_scope": state["required_scope"],
            # Written by code, never a model: the draft BGĐ reads beside the
            # decision (prices redacted per reader), or null ("chưa có tờ trình").
            "bod_submission": state.get("bod_submission"),
        }
    )
    approved = decision.get("approved")
    if not isinstance(approved, bool):
        # A rejection cancels the case, so a decision that is neither yes nor
        # no is not read as either: the run fails, the case keeps waiting, and
        # the reconcile lane raises a fresh review.
        raise ValueError("the decision carries no yes or no")
    return {
        "approved": approved,
        "approver_comment": str(decision.get("comment", "")),
        "decided_by": str(decision.get("decided_by", "")),
    }


def _build_apply_node(repo: ProductCaseReviewPort, ids: IdGenerator, clock: UtcClock) -> Any:
    # Untyped return for the LangGraph `add_node` inference reason
    # `advance_case_graph._build_apply_node` gives.
    async def _apply(state: ProductReviewState) -> ProductReviewState:
        # The decider, from the resume value `decide` built. A resume without
        # one fails the run here (ValueError) rather than recording nobody.
        decided_by = uuid.UUID(state.get("decided_by", ""))
        run_context = get_runtime(RunContext).context
        # The run's tenant and workspace bound the reads and writes; the
        # decider is who the step and the audit name.
        context = access_context_from_run(run_context).model_copy(
            update={"principal_id": decided_by}
        )
        case_id = ProductDevelopmentCaseId(uuid.UUID(state["product_dev_case_id"]))
        case = await repo.get(context, case_id)
        if case is None:
            raise NotFoundError("product case not found", details={"case_id": str(case_id)})
        approved = bool(state.get("approved"))
        if (
            case.state is not ProductDevState.PENDING_BOD_REVIEW
            or case.sample_round != state["sample_round"]
        ):
            await repo.append_audit(
                context,
                product_case_audit(
                    context,
                    ids,
                    clock,
                    case,
                    "bod_review_superseded",
                    {
                        "approved": approved,
                        "state": case.state.value,
                        "review_round": state["sample_round"],
                        "sample_round": case.sample_round,
                    },
                ),
            )
            return {"outcome": SUPERSEDED}
        action = ProductAction.BOD_APPROVE if approved else ProductAction.BOD_REJECT
        before = case.state
        apply_product_action(
            case,
            action=action,
            given=ProductActionInput(
                actor_id=decided_by,
                reason=None if approved else state.get("approver_comment"),
            ),
        )
        await repo.save(
            context,
            case,
            audit=product_case_audit(
                context,
                ids,
                clock,
                case,
                action.value,
                {
                    "from_state": before.value,
                    "to_state": case.state.value,
                    "sample_round": case.sample_round,
                    "run_id": str(run_context.run_id),
                },
            ),
        )
        return {"outcome": action.value}

    return _apply


def build_advance_product_case_graph(
    repo: ProductCaseReviewPort, ids: IdGenerator, clock: UtcClock
) -> StateGraph:  # type: ignore[type-arg]
    """Uncompiled, closure-injected (`registry.GraphFactory`'s contract): the
    runner compiles it with the tenant-aware checkpointer."""
    graph: StateGraph = StateGraph(ProductReviewState)  # type: ignore[type-arg]
    graph.add_node("request_review", _request_review)
    graph.add_node("apply", _build_apply_node(repo, ids, clock))
    graph.add_edge(START, "request_review")
    graph.add_edge("request_review", "apply")
    graph.add_edge("apply", END)
    return graph
