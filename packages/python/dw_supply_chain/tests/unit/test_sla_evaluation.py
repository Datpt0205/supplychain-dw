from datetime import UTC, datetime, timedelta

import pytest

from dw_supply_chain.domain.po_case import CaseState
from dw_supply_chain.domain.product_development_case import ProductDevState
from dw_supply_chain.domain.sla_evaluation import (
    SLAEvaluationStatus,
    evaluate_product_sla,
    evaluate_sla,
)
from dw_supply_chain.sla_policy import (
    ProductCategory,
    SLAConfirmationStatus,
    SLAMilestone,
    SupplierUpdateCadence,
    SupplyChainSLAPolicy,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 24, tzinfo=UTC)


def _policy(
    *, by_category: dict[str, dict[str, SLAMilestone]] | None = None, **milestones: SLAMilestone
) -> SupplyChainSLAPolicy:
    return SupplyChainSLAPolicy(
        schema_version="2.0",
        policy_id="supply_chain_sla",
        policy_version="2.0.0",
        categories=(
            ProductCategory(key="noi", label="Nồi"),
            ProductCategory(key="chao", label="Chảo"),
        ),
        default=milestones,
        by_category=by_category or {},
        supplier_update=SupplierUpdateCadence(reminder_after="5d", escalation_after="10d"),
    )


def _policy_with(milestones: dict[str, SLAMilestone]) -> SupplyChainSLAPolicy:
    return _policy(**milestones)  # type: ignore[arg-type]


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
        state=state, category=None, entered_current_state_at=_NOW, now=_NOW, policy=_policy()
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
        category=None,
        entered_current_state_at=_NOW,
        now=_NOW,
        policy=_policy_with({milestone: _confirmed(10)}),
    )
    assert evaluation.milestone == milestone


def test_a_mapped_milestone_still_pending_confirmation_is_not_evaluable() -> None:
    evaluation = evaluate_sla(
        state=CaseState.WAITING_DEPOSIT,
        category=None,
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
        category=None,
        entered_current_state_at=_NOW - timedelta(days=9),
        now=_NOW,
        policy=_policy(deposit=_confirmed(10)),
    )
    assert evaluation.status is SLAEvaluationStatus.ON_TRACK
    assert evaluation.threshold_days == 10


def test_reaching_the_confirmed_threshold_is_breached() -> None:
    evaluation = evaluate_sla(
        state=CaseState.WAITING_DEPOSIT,
        category=None,
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
        state=CaseState.PRODUCTION,
        category=None,
        entered_current_state_at=entered,
        now=_NOW,
        policy=_policy(),
    )
    assert evaluation.entered_current_state_at == entered
    assert evaluation.age_days == 4


# ---- Category (ADR 0019) ------------------------------------------------------


def test_a_po_case_of_a_category_with_its_own_number_uses_that_number() -> None:
    policy = _policy(by_category={"chao": {"deposit": _confirmed(3)}}, deposit=_confirmed(10))
    for category, threshold, status in (
        ("chao", 3, SLAEvaluationStatus.BREACHED),
        ("noi", 10, SLAEvaluationStatus.ON_TRACK),
        (None, 10, SLAEvaluationStatus.ON_TRACK),
    ):
        evaluation = evaluate_sla(
            state=CaseState.WAITING_DEPOSIT,
            category=category,
            entered_current_state_at=_NOW - timedelta(days=4),
            now=_NOW,
            policy=policy,
        )
        assert (evaluation.threshold_days, evaluation.status) == (threshold, status), category


# ---- product-development cases (stage-1 ticket 06) ------------------------------


@pytest.mark.parametrize(
    "state,milestone",
    [
        (ProductDevState.SAMPLE_REQUESTED, "sample_collection"),
        (ProductDevState.SAMPLE_TESTING, "sample_testing"),
        (ProductDevState.PENDING_BOD_REVIEW, "bod_review"),
        (ProductDevState.PROFILE_IN_PROGRESS, "bm04"),
        (ProductDevState.SUPPLIER_CONFIRMATION, "supplier_confirmation"),
        (ProductDevState.ITEM_CODING, "item_coding"),
        (ProductDevState.PENDING_SIGNOFF, "signoff"),
    ],
)
def test_each_stage_1_step_names_its_milestone(state: ProductDevState, milestone: str) -> None:
    evaluation = evaluate_product_sla(
        state=state,
        category="noi",
        entered_current_state_at=_NOW - timedelta(days=5),
        now=_NOW,
        policy=_policy_with({milestone: _confirmed(4)}),
    )
    assert (evaluation.milestone, evaluation.status) == (milestone, SLAEvaluationStatus.BREACHED)


@pytest.mark.parametrize(
    "state",
    [
        ProductDevState.PROPOSED,
        ProductDevState.REVISION_REQUESTED,
        ProductDevState.READY_TO_ORDER,
        ProductDevState.ORDERED,
        ProductDevState.WAITING_EXTERNAL,
        ProductDevState.BLOCKED,
        ProductDevState.MANUAL_REVIEW,
        ProductDevState.CANCELLED,
    ],
)
def test_a_product_state_with_no_milestone_is_not_applicable(state: ProductDevState) -> None:
    """A paused case (QE-14, provisional) has no clock, as a PO case has none."""
    evaluation = evaluate_product_sla(
        state=state,
        category="noi",
        entered_current_state_at=_NOW - timedelta(days=50),
        now=_NOW,
        policy=_policy(bm04=_confirmed(1), sample_testing=_confirmed(1)),
    )
    assert evaluation.status is SLAEvaluationStatus.NOT_APPLICABLE


def test_bm04_past_its_confirmed_days_is_a_breach_and_pending_is_nothing() -> None:
    """Ticket 06's criterion: `bm04` now has a reader, and a pending number
    still opens nothing."""
    entered = _NOW - timedelta(days=5)
    breached = evaluate_product_sla(
        state=ProductDevState.PROFILE_IN_PROGRESS,
        category="noi",
        entered_current_state_at=entered,
        now=_NOW,
        policy=_policy(bm04=_confirmed(4)),
    )
    assert breached.status is SLAEvaluationStatus.BREACHED
    assert (breached.age_days, breached.threshold_days) == (5, 4)
    pending = evaluate_product_sla(
        state=ProductDevState.PROFILE_IN_PROGRESS,
        category="noi",
        entered_current_state_at=entered,
        now=_NOW,
        policy=_policy(bm04=_pending(4)),
    )
    assert pending.status is SLAEvaluationStatus.NOT_EVALUABLE


def test_category_a_uses_its_own_number_and_a_category_without_one_uses_the_default() -> None:
    policy = _policy(
        by_category={"chao": {"sample_testing": _confirmed(1)}}, sample_testing=_confirmed(3)
    )
    entered = _NOW - timedelta(days=2)

    def status(category: str) -> SLAEvaluationStatus:
        return evaluate_product_sla(
            state=ProductDevState.SAMPLE_TESTING,
            category=category,
            entered_current_state_at=entered,
            now=_NOW,
            policy=policy,
        ).status

    assert status("chao") is SLAEvaluationStatus.BREACHED
    assert status("noi") is SLAEvaluationStatus.ON_TRACK
    # A case stamped before the list existed: not in it, evaluated on default.
    assert status("Nồi cũ") is SLAEvaluationStatus.ON_TRACK


def test_a_category_whose_own_number_is_pending_is_not_evaluated_on_the_default() -> None:
    policy = _policy(by_category={"chao": {"bm04": _pending(4)}}, bm04=_confirmed(1))
    evaluation = evaluate_product_sla(
        state=ProductDevState.PROFILE_IN_PROGRESS,
        category="chao",
        entered_current_state_at=_NOW - timedelta(days=9),
        now=_NOW,
        policy=policy,
    )
    assert evaluation.status is SLAEvaluationStatus.NOT_EVALUABLE
