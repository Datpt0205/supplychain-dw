"""SLA evaluation: has this case's current state overrun its reference SLA.

Pure computation, no model call — matches `missing_update.py`'s own framing:
the workflow engine decides, nothing here needs a judgment call. Each
milestone of `configs/policies/supply_chain_sla@*.yaml` is attached to the
one state it measures: four `POCase` states, and the stage-1 states of a
`ProductDevelopmentCase` (ADR 0019; `bm04` and `supplier_confirmation` were in
the file with no reader until stage-1 ticket 06). The two tables below are the
only place that attachment is written.

The case's stamped Category picks the number (`duration_days_for`): its own
entry for the milestone, or the policy's `default`.

Uses `state`, never `interrupted_state` — a case currently `BLOCKED` mid-
`WAITING_DEPOSIT` is reported `NOT_APPLICABLE` rather than still evaluated
against the deposit SLA, and its clock starts again from the resume (the
caller passes the latest transition into the current state). Stated
simplification, provisional until Elmich answers QE-14: the doc gives no
guidance on how an interrupt should affect an SLA clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from dw_supply_chain.domain.po_case import CaseState
from dw_supply_chain.domain.product_development_case import ProductDevState
from dw_supply_chain.sla_policy import SupplyChainSLAPolicy

_MILESTONE_FOR_STATE: dict[CaseState, str] = {
    CaseState.WAITING_DEPOSIT: "deposit",
    CaseState.IN_TRANSIT: "port_arrival",
    CaseState.WAITING_PAYMENT: "payment",
    CaseState.PAYMENT_COMPLETED: "warehouse_receipt",
}

# Stage 1. A separate table, not one keyed by both enums: the two share
# string values ("blocked", "cancelled"), and a StrEnum member is equal to its
# value, so one dict would let a PO state find a product milestone.
_MILESTONE_FOR_PRODUCT_STATE: dict[ProductDevState, str] = {
    ProductDevState.SAMPLE_REQUESTED: "sample_collection",
    ProductDevState.SAMPLE_TESTING: "sample_testing",
    ProductDevState.PENDING_BOD_REVIEW: "bod_review",
    ProductDevState.PROFILE_IN_PROGRESS: "bm04",
    ProductDevState.SUPPLIER_CONFIRMATION: "supplier_confirmation",
    ProductDevState.ITEM_CODING: "item_coding",
    ProductDevState.PENDING_SIGNOFF: "signoff",
}


class SLAEvaluationStatus(StrEnum):
    # The current state has no SLA milestone mapped to it at all (e.g.
    # PRODUCTION, QC, or any exception state) — not a breach, not on track,
    # simply nothing to measure here.
    NOT_APPLICABLE = "not_applicable"
    # A milestone applies, but its duration is still `pending_business_
    # confirmation` in the policy — never enforced or alerted on as if real.
    NOT_EVALUABLE = "not_evaluable"
    ON_TRACK = "on_track"
    BREACHED = "breached"


@dataclass(frozen=True, slots=True)
class SLAEvaluation:
    status: SLAEvaluationStatus
    milestone: str | None
    entered_current_state_at: datetime
    age_days: int
    threshold_days: int | None


def _evaluate(
    *,
    milestone: str | None,
    category: str | None,
    entered_current_state_at: datetime,
    now: datetime,
    policy: SupplyChainSLAPolicy,
) -> SLAEvaluation:
    age_days = (now - entered_current_state_at).days
    threshold_days = (
        policy.duration_days_for(milestone, category) if milestone is not None else None
    )

    if milestone is None:
        status = SLAEvaluationStatus.NOT_APPLICABLE
    elif threshold_days is None:
        status = SLAEvaluationStatus.NOT_EVALUABLE
    elif age_days >= threshold_days:
        status = SLAEvaluationStatus.BREACHED
    else:
        status = SLAEvaluationStatus.ON_TRACK

    return SLAEvaluation(
        status=status,
        milestone=milestone,
        entered_current_state_at=entered_current_state_at,
        age_days=age_days,
        threshold_days=threshold_days,
    )


def evaluate_sla(
    *,
    state: CaseState,
    category: str | None,
    entered_current_state_at: datetime,
    now: datetime,
    policy: SupplyChainSLAPolicy,
) -> SLAEvaluation:
    """A PO case. `category` is the case's stamp; None for a case opened
    without one, which is evaluated on `default`."""
    return _evaluate(
        milestone=_MILESTONE_FOR_STATE.get(state),
        category=category,
        entered_current_state_at=entered_current_state_at,
        now=now,
        policy=policy,
    )


def evaluate_product_sla(
    *,
    state: ProductDevState,
    category: str,
    entered_current_state_at: datetime,
    now: datetime,
    policy: SupplyChainSLAPolicy,
) -> SLAEvaluation:
    """A product-development case, under its stamped Category."""
    return _evaluate(
        milestone=_MILESTONE_FOR_PRODUCT_STATE.get(state),
        category=category,
        entered_current_state_at=entered_current_state_at,
        now=now,
        policy=policy,
    )
