import uuid

import pytest

from dw_kernel.errors import ConflictError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_supply_chain.domain.po_case import CaseState, POCase, POCaseId

pytestmark = pytest.mark.unit


def make_case(**overrides: object) -> POCase:
    defaults: dict[str, object] = {
        "id": POCaseId(uuid.uuid4()),
        "tenant_id": TenantId(uuid.uuid4()),
        "workspace_id": WorkspaceId(uuid.uuid4()),
        "po_reference": "PO-2026-0001",
        "supplier_name": "Elmich Co.",
    }
    defaults.update(overrides)
    return POCase(**defaults)  # type: ignore[arg-type]


def assert_state(case: POCase, expected: CaseState) -> None:
    """A plain `assert case.state is CaseState.X` narrows mypy's view of
    `case.state` to that literal, and mypy does not invalidate that
    narrowing across a later method call that mutates it (confirmed against
    mypy 1.20.2 with a minimal repro) — the next differing check then reads
    as a real contradiction. Routing the comparison through a same-typed
    parameter here keeps `is` identity checks (StrEnum, singleton members)
    without a false positive on every subsequent state in a walked test."""
    assert case.state is expected


def assert_interrupted_state(case: POCase, expected: CaseState | None) -> None:
    assert case.interrupted_state is expected


def test_blank_po_reference_is_refused_by_the_constructor() -> None:
    with pytest.raises(ValueError, match="po_reference"):
        make_case(po_reference="   ")


def test_blank_supplier_name_is_refused_by_the_constructor() -> None:
    with pytest.raises(ValueError, match="supplier_name"):
        make_case(supplier_name="")


def test_full_happy_path_reaches_completed_and_versions_every_step() -> None:
    case = make_case()
    assert_state(case, CaseState.PO_CREATED)
    assert case.version == 1

    case.request_deposit()
    assert_state(case, CaseState.WAITING_DEPOSIT)
    case.confirm_deposit()
    assert_state(case, CaseState.DEPOSIT_CONFIRMED)
    case.start_pre_production()
    assert_state(case, CaseState.PRE_PRODUCTION)
    case.start_production()
    assert_state(case, CaseState.PRODUCTION)
    case.send_to_qc()
    assert_state(case, CaseState.QC)
    case.pass_qc()
    assert_state(case, CaseState.IN_TRANSIT)
    case.arrive_at_port()
    assert_state(case, CaseState.ARRIVED_PORT)
    case.request_final_payment()
    assert_state(case, CaseState.WAITING_PAYMENT)
    case.confirm_payment()
    assert_state(case, CaseState.PAYMENT_COMPLETED)
    case.start_warehouse_receiving()
    assert_state(case, CaseState.WAREHOUSE_RECEIVING)
    case.complete()
    assert_state(case, CaseState.COMPLETED)

    # 11 forward transitions from version 1.
    assert case.version == 12


@pytest.mark.parametrize(
    "method_name",
    [
        "confirm_deposit",
        "start_pre_production",
        "start_production",
        "send_to_qc",
        "pass_qc",
        "arrive_at_port",
        "request_final_payment",
        "confirm_payment",
        "start_warehouse_receiving",
        "complete",
    ],
)
def test_every_happy_path_method_refuses_when_called_out_of_order(method_name: str) -> None:
    """A freshly created case is in PO_CREATED; every method that expects a
    later state must refuse rather than silently jumping ahead."""
    case = make_case()
    method = getattr(case, method_name)
    with pytest.raises(ConflictError):
        method()
    # And the refusal must not have mutated state or bumped the version.
    assert_state(case, CaseState.PO_CREATED)
    assert case.version == 1


def test_qc_failure_moves_to_rework_and_resume_returns_to_production() -> None:
    case = make_case()
    case.request_deposit()
    case.confirm_deposit()
    case.start_pre_production()
    case.start_production()
    case.send_to_qc()

    case.fail_qc("solder joints out of spec")
    assert_state(case, CaseState.REWORK)

    case.resume_from_rework()
    assert_state(case, CaseState.PRODUCTION)
    # rework is a normal loop back into the happy path, not a dead end
    case.send_to_qc()
    assert_state(case, CaseState.QC)


def test_fail_qc_requires_a_non_blank_reason() -> None:
    case = make_case()
    case.request_deposit()
    case.confirm_deposit()
    case.start_pre_production()
    case.start_production()
    case.send_to_qc()

    with pytest.raises(ValueError, match="reason"):
        case.fail_qc("  ")
    assert_state(case, CaseState.QC)


def test_fail_qc_refuses_outside_qc() -> None:
    case = make_case()
    with pytest.raises(ConflictError):
        case.fail_qc("too early to fail qc")


@pytest.mark.parametrize(
    "interrupt_method,expected_state",
    [
        ("wait_for_external", CaseState.WAITING_EXTERNAL),
        ("flag_blocked", CaseState.BLOCKED),
        ("flag_manual_review", CaseState.MANUAL_REVIEW),
    ],
)
def test_generic_interrupt_pauses_and_resume_returns_to_the_exact_state(
    interrupt_method: str, expected_state: CaseState
) -> None:
    case = make_case()
    case.request_deposit()
    case.confirm_deposit()
    case.start_pre_production()
    # interrupt mid-production
    getattr(case, interrupt_method)("supplier stopped responding")
    assert_state(case, expected_state)
    assert_interrupted_state(case, CaseState.PRE_PRODUCTION)

    case.resume()
    assert_state(case, CaseState.PRE_PRODUCTION)
    assert_interrupted_state(case, None)
    # and the happy path continues normally after resuming
    case.start_production()
    assert_state(case, CaseState.PRODUCTION)


def test_interrupt_requires_a_non_blank_reason() -> None:
    case = make_case()
    with pytest.raises(ValueError, match="reason"):
        case.flag_blocked("")
    assert_state(case, CaseState.PO_CREATED)


def test_cannot_interrupt_from_a_terminal_state() -> None:
    case = make_case()
    case.cancel("customer walked away")
    assert_state(case, CaseState.CANCELLED)
    with pytest.raises(ConflictError):
        case.flag_blocked("too late")


def test_cannot_stack_interrupts() -> None:
    case = make_case()
    case.flag_blocked("missing evidence")
    with pytest.raises(ConflictError):
        case.wait_for_external("also waiting on supplier")


def test_cannot_interrupt_from_rework() -> None:
    case = make_case()
    case.request_deposit()
    case.confirm_deposit()
    case.start_pre_production()
    case.start_production()
    case.send_to_qc()
    case.fail_qc("failed dimensional check")
    with pytest.raises(ConflictError):
        case.flag_blocked("also blocked")


def test_resume_refuses_when_not_interrupted() -> None:
    case = make_case()
    with pytest.raises(ConflictError):
        case.resume()


def test_cancel_is_allowed_from_an_interrupt_state_and_clears_it() -> None:
    case = make_case()
    case.flag_manual_review("conflicting supplier updates")
    case.cancel("PO withdrawn while under review")
    assert_state(case, CaseState.CANCELLED)
    assert_interrupted_state(case, None)


def test_cancel_requires_a_non_blank_reason() -> None:
    case = make_case()
    with pytest.raises(ValueError, match="reason"):
        case.cancel("")
    assert_state(case, CaseState.PO_CREATED)


def test_cannot_cancel_a_completed_case() -> None:
    case = make_case()
    case.request_deposit()
    case.confirm_deposit()
    case.start_pre_production()
    case.start_production()
    case.send_to_qc()
    case.pass_qc()
    case.arrive_at_port()
    case.request_final_payment()
    case.confirm_payment()
    case.start_warehouse_receiving()
    case.complete()
    with pytest.raises(ConflictError):
        case.cancel("too late")


def test_cannot_cancel_an_already_cancelled_case() -> None:
    case = make_case()
    case.cancel("first cancel")
    with pytest.raises(ConflictError):
        case.cancel("second cancel")


# -- pending transition bookkeeping ------------------------------------------


def test_happy_path_steps_are_recorded_as_from_to_pairs_in_order() -> None:
    case = make_case()
    case.request_deposit()
    case.confirm_deposit()
    case.start_pre_production()

    assert case.pop_pending_transitions() == [
        (CaseState.PO_CREATED, CaseState.WAITING_DEPOSIT, None),
        (CaseState.WAITING_DEPOSIT, CaseState.DEPOSIT_CONFIRMED, None),
        (CaseState.DEPOSIT_CONFIRMED, CaseState.PRE_PRODUCTION, None),
    ]


def test_popping_clears_the_queue() -> None:
    case = make_case()
    case.request_deposit()
    case.pop_pending_transitions()
    assert case.pop_pending_transitions() == []


def test_a_refused_transition_records_nothing() -> None:
    case = make_case()
    with pytest.raises(ConflictError):
        case.confirm_deposit()  # PO_CREATED cannot skip straight to this
    assert case.pop_pending_transitions() == []


def test_interrupt_and_resume_are_each_recorded() -> None:
    case = make_case()
    case.request_deposit()
    case.pop_pending_transitions()

    case.flag_blocked("missing evidence")
    case.resume()

    assert case.pop_pending_transitions() == [
        (CaseState.WAITING_DEPOSIT, CaseState.BLOCKED, "missing evidence"),
        (CaseState.BLOCKED, CaseState.WAITING_DEPOSIT, None),
    ]


def test_cancel_is_recorded() -> None:
    case = make_case()
    case.cancel("customer walked away")
    assert case.pop_pending_transitions() == [
        (CaseState.PO_CREATED, CaseState.CANCELLED, "customer walked away")
    ]


def test_fail_qc_records_its_reason() -> None:
    case = make_case(state=CaseState.QC)
    case.fail_qc("solder joints failing inspection")
    assert case.pop_pending_transitions() == [
        (CaseState.QC, CaseState.REWORK, "solder joints failing inspection")
    ]
