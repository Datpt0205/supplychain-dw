from datetime import UTC, datetime, timedelta

import pytest

from dw_supply_chain.domain.po_case import CaseState
from dw_supply_chain.domain.sla_evaluation import SLAEvaluationStatus, evaluate_sla
from dw_supply_chain.sla_policy import (
    SLAConfirmationStatus,
    SLAMilestone,
    SupplierUpdateCadence,
    SupplyChainSLAPolicy,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 24, tzinfo=UTC)


def _policy(**milestones: SLAMilestone) -> SupplyChainSLAPolicy:
    return SupplyChainSLAPolicy(
        schema_version="1.0",
        policy_id="supply_chain_sla",
        policy_version="1.0.0",
        sla=milestones,
        supplier_update=SupplierUpdateCadence(reminder_after="5d", escalation_after="10d"),
    )


def _confirmed(duration_days: int) -> SLAMilestone:
    return SLAMilestone(duration=f"{duration_days}d", status=SLAConfirmationStatus.CONFIRMED)


def _pending(duration_days: int) -> SLAMilestone:
    return SLAMilestone(
        duration=f"{duration_days}d", status=SLAConfirmationStatus.PENDING_BUSINESS_CONFIRMATION
    )


@pytest.mark.parametrize(
    "state",
    [
        CaseState.PO_CREATED,
        CaseState.DEPOSIT_CONFIRMED,
        CaseState.PRE_PRODUCTION,
        CaseState.PRODUCTION,
        CaseState.QC,
        CaseState.ARRIVED_PORT,
        CaseState.WAREHOUSE_RECEIVING,
        CaseState.COMPLETED,
        CaseState.BLOCKED,
        CaseState.WAITING_EXTERNAL,
        CaseState.MANUAL_REVIEW,
        CaseState.REWORK,
        CaseState.CANCELLED,
    ],
)
def test_a_state_with_no_milestone_mapping_is_not_applicable(state: CaseState) -> None:
    evaluation = evaluate_sla(
        state=state, entered_current_state_at=_NOW, now=_NOW, policy=_policy()
    )
    assert evaluation.status is SLAEvaluationStatus.NOT_APPLICABLE
    assert evaluation.milestone is None
    assert evaluation.threshold_days is None


@pytest.mark.parametrize(
    "state,milestone",
    [
        (CaseState.WAITING_DEPOSIT, "deposit"),
        (CaseState.IN_TRANSIT, "port_arrival"),
        (CaseState.WAITING_PAYMENT, "payment"),
        (CaseState.PAYMENT_COMPLETED, "warehouse_receipt"),
    ],
)
def test_each_mapped_state_names_its_own_milestone(state: CaseState, milestone: str) -> None:
    evaluation = evaluate_sla(
        state=state,
        entered_current_state_at=_NOW,
        now=_NOW,
        policy=_policy(**{milestone: _confirmed(10)}),
    )
    assert evaluation.milestone == milestone


def test_a_mapped_milestone_still_pending_confirmation_is_not_evaluable() -> None:
    evaluation = evaluate_sla(
        state=CaseState.WAITING_DEPOSIT,
        entered_current_state_at=_NOW - timedelta(days=30),
        now=_NOW,
        policy=_policy(deposit=_pending(10)),
    )
    assert evaluation.status is SLAEvaluationStatus.NOT_EVALUABLE
    assert evaluation.threshold_days is None
    assert evaluation.age_days == 30


def test_just_under_the_confirmed_threshold_is_on_track() -> None:
    evaluation = evaluate_sla(
        state=CaseState.WAITING_DEPOSIT,
        entered_current_state_at=_NOW - timedelta(days=9),
        now=_NOW,
        policy=_policy(deposit=_confirmed(10)),
    )
    assert evaluation.status is SLAEvaluationStatus.ON_TRACK
    assert evaluation.threshold_days == 10


def test_reaching_the_confirmed_threshold_is_breached() -> None:
    evaluation = evaluate_sla(
        state=CaseState.WAITING_DEPOSIT,
        entered_current_state_at=_NOW - timedelta(days=10),
        now=_NOW,
        policy=_policy(deposit=_confirmed(10)),
    )
    assert evaluation.status is SLAEvaluationStatus.BREACHED


def test_entered_current_state_at_and_age_are_always_reported() -> None:
    """Even for NOT_APPLICABLE — a UI can still show 'been in PRODUCTION for
    N days' as information, only the SLA verdict is withheld."""
    entered = _NOW - timedelta(days=4)
    evaluation = evaluate_sla(
        state=CaseState.PRODUCTION, entered_current_state_at=entered, now=_NOW, policy=_policy()
    )
    assert evaluation.entered_current_state_at == entered
    assert evaluation.age_days == 4
