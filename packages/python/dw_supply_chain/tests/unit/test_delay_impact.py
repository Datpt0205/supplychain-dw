import uuid

import pytest

from dw_kernel.ids import TenantId, WorkspaceId
from dw_supply_chain.domain.delay_impact import impacted_milestones, propagated_estimates
from dw_supply_chain.domain.po_case import CaseState, POCase, POCaseId

pytestmark = pytest.mark.unit


def _case(**overrides: object) -> POCase:
    defaults: dict[str, object] = {
        "id": POCaseId(uuid.uuid4()),
        "tenant_id": TenantId(uuid.uuid4()),
        "workspace_id": WorkspaceId(uuid.uuid4()),
        "po_reference": "PO-2026-0001",
        "supplier_name": "Elmich Co.",
    }
    defaults.update(overrides)
    return POCase(**defaults)  # type: ignore[arg-type]


def test_a_delay_reported_in_production_impacts_everything_after_it() -> None:
    case = _case(state=CaseState.PRODUCTION)
    assert impacted_milestones(case) == [
        CaseState.QC,
        CaseState.IN_TRANSIT,
        CaseState.ARRIVED_PORT,
        CaseState.WAITING_PAYMENT,
        CaseState.PAYMENT_COMPLETED,
        CaseState.WAREHOUSE_RECEIVING,
        CaseState.COMPLETED,
    ]


def test_a_delay_reported_at_po_created_impacts_the_whole_happy_path() -> None:
    case = _case()  # PO_CREATED, the default
    milestones = impacted_milestones(case)
    assert milestones[0] is CaseState.WAITING_DEPOSIT
    assert milestones[-1] is CaseState.COMPLETED
    assert len(milestones) == 11


def test_completed_has_nothing_left_to_impact() -> None:
    case = _case(state=CaseState.COMPLETED)
    assert impacted_milestones(case) == []


def test_cancelled_has_nothing_left_to_impact() -> None:
    case = _case(state=CaseState.CANCELLED)
    assert impacted_milestones(case) == []


def test_mid_interrupt_uses_the_state_it_will_resume_to_not_the_interrupt_itself() -> None:
    case = _case()
    case.request_deposit()
    case.confirm_deposit()
    case.start_pre_production()
    case.flag_blocked("missing evidence")  # interrupted_state = PRE_PRODUCTION
    assert case.state is CaseState.BLOCKED

    milestones = impacted_milestones(case)
    assert milestones[0] is CaseState.PRODUCTION
    assert CaseState.BLOCKED not in milestones


def test_rework_resumes_into_production_specifically() -> None:
    case = _case()
    case.request_deposit()
    case.confirm_deposit()
    case.start_pre_production()
    case.start_production()
    case.send_to_qc()
    case.fail_qc("failed dimensional check")
    assert case.state is CaseState.REWORK
    assert case.interrupted_state is None  # REWORK does not set it

    milestones = impacted_milestones(case)
    assert milestones[0] is CaseState.QC
    assert CaseState.PRODUCTION not in milestones


def test_propagated_estimates_apply_the_reported_delay_uniformly() -> None:
    case = _case(state=CaseState.ARRIVED_PORT)
    estimates = propagated_estimates(case, delay_days=5)
    assert [e.milestone for e in estimates] == [
        CaseState.WAITING_PAYMENT,
        CaseState.PAYMENT_COMPLETED,
        CaseState.WAREHOUSE_RECEIVING,
        CaseState.COMPLETED,
    ]
    assert all(e.estimated_delay_days == 5 for e in estimates)


def test_propagated_estimates_is_empty_for_a_completed_case() -> None:
    case = _case(state=CaseState.COMPLETED)
    assert propagated_estimates(case, delay_days=5) == []
