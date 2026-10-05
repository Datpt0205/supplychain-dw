"""SLA evaluation: has this case's current state overrun its reference SLA.

Pure computation, no model call — matches `missing_update.py`'s own framing:
the workflow engine decides, nothing here needs a judgment call. The four
milestones below are exactly the ones `configs/policies/supply_chain_sla@
*.yaml`'s own comments map onto a `POCase` transition; `bm04`/
`supplier_confirmation` are pre-PO, DW-SC-01 scope, and have no `CaseState`
to attach to yet (Phase 4+, not built).

Uses `state`, never `interrupted_state` — a case currently `BLOCKED` mid-
`WAITING_DEPOSIT` is reported `NOT_APPLICABLE` rather than still evaluated
against the deposit SLA. Stated simplification, not a hidden gap: the doc
gives no guidance on how an interrupt should affect an SLA clock, and
guessing one (paused? still running? reset?) is a business decision, not an
engineering default to invent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from dw_supply_chain.domain.po_case import CaseState
from dw_supply_chain.sla_policy import SupplyChainSLAPolicy

_MILESTONE_FOR_STATE: dict[CaseState, str] = {
    CaseState.WAITING_DEPOSIT: "deposit",
    CaseState.IN_TRANSIT: "port_arrival",
    CaseState.WAITING_PAYMENT: "payment",
    CaseState.PAYMENT_COMPLETED: "warehouse_receipt",
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


def evaluate_sla(
    *,
    state: CaseState,
    entered_current_state_at: datetime,
    now: datetime,
    policy: SupplyChainSLAPolicy,
) -> SLAEvaluation:
    age_days = (now - entered_current_state_at).days
    milestone = _MILESTONE_FOR_STATE.get(state)
    threshold_days = policy.duration_days_for(milestone) if milestone is not None else None

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
