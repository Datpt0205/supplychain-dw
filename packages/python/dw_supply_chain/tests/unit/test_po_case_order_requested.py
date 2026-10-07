"""The PO case before its PO exists (`order_requested`) and step 10's
`create_po` (stage-1 ticket 05, ADR 0017)."""

import uuid

import pytest

from dw_kernel.errors import ConflictError, DomainError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_supply_chain.domain import po_case as po_case_module
from dw_supply_chain.domain.po_case import (
    REASON_REQUIRED_ACTIONS,
    CaseAction,
    CaseState,
    OrderKind,
    POCase,
    POCaseId,
    POCaseLine,
    apply_action,
)

pytestmark = pytest.mark.unit

_SKU_A = uuid.uuid4()
_SKU_B = uuid.uuid4()


def _requested(**overrides: object) -> POCase:
    defaults: dict[str, object] = {
        "id": POCaseId(uuid.uuid4()),
        "tenant_id": TenantId(uuid.uuid4()),
        "workspace_id": WorkspaceId(uuid.uuid4()),
        "supplier_name": "NCC Minh Long",
        "product_dev_case_id": uuid.uuid4(),
        "pic_user_id": uuid.uuid4(),
        "category": "Nồi",
        "lines": (
            POCaseLine(sku_id=_SKU_A, quantity=100),
            POCaseLine(sku_id=_SKU_B, quantity=None),
        ),
    }
    defaults.update(overrides)
    return POCase.requested(**defaults)  # type: ignore[arg-type]


def _state(case: POCase) -> CaseState:
    return case.state


def test_a_requested_case_has_no_reference_and_is_a_new_order() -> None:
    case = _requested()
    assert _state(case) is CaseState.ORDER_REQUESTED
    assert case.po_reference is None
    assert case.order_kind is OrderKind.NEW
    assert next(iter(CaseState)) is CaseState.ORDER_REQUESTED


def test_only_a_case_awaiting_its_po_or_cancelled_may_lack_a_reference() -> None:
    with pytest.raises(ValueError, match="po_reference"):
        POCase(
            id=POCaseId(uuid.uuid4()),
            tenant_id=TenantId(uuid.uuid4()),
            workspace_id=WorkspaceId(uuid.uuid4()),
            po_reference=None,
            supplier_name="NCC",
            order_kind=OrderKind.REORDER,
        )


def test_create_po_sets_the_reference_and_kind_and_moves_to_po_created() -> None:
    case = _requested()
    case.create_po(
        po_reference="  PO-2026-0101 ", order_kind=OrderKind.NEW, quantities={_SKU_B: 40}
    )
    assert _state(case) is CaseState.PO_CREATED
    assert case.po_reference == "PO-2026-0101"
    assert case.order_kind is OrderKind.NEW
    assert [line.quantity for line in case.lines] == [100, 40]
    assert case.version == 2
    assert case.pop_pending_transitions() == [
        (CaseState.ORDER_REQUESTED, CaseState.PO_CREATED, None)
    ]
    assert case.pop_pending_line_quantities() == {_SKU_B: 40}


def test_create_po_needs_a_quantity_on_every_line() -> None:
    case = _requested()
    with pytest.raises(ConflictError) as raised:
        case.create_po(po_reference="PO-1", order_kind=OrderKind.NEW)
    assert raised.value.details["missing_quantity"] == str(_SKU_B)
    assert _state(case) is CaseState.ORDER_REQUESTED
    assert case.pop_pending_transitions() == []


@pytest.mark.parametrize("quantity", [0, -3])
def test_create_po_refuses_a_quantity_below_one(quantity: int) -> None:
    case = _requested()
    with pytest.raises(DomainError, match="quantity"):
        case.create_po(po_reference="PO-1", order_kind=OrderKind.NEW, quantities={_SKU_B: quantity})


def test_create_po_refuses_a_sku_the_order_does_not_carry() -> None:
    case = _requested()
    with pytest.raises(DomainError, match="sku"):
        case.create_po(
            po_reference="PO-1",
            order_kind=OrderKind.NEW,
            quantities={_SKU_B: 1, uuid.uuid4(): 5},
        )


def test_create_po_refuses_a_blank_reference() -> None:
    case = _requested()
    with pytest.raises(ValueError, match="po_reference"):
        case.create_po(po_reference="  ", order_kind=OrderKind.NEW, quantities={_SKU_B: 1})


def test_create_po_only_from_order_requested() -> None:
    case = _requested()
    case.create_po(po_reference="PO-1", order_kind=OrderKind.NEW, quantities={_SKU_B: 1})
    with pytest.raises(ConflictError):
        case.create_po(po_reference="PO-2", order_kind=OrderKind.NEW)


@pytest.mark.parametrize(
    "interrupt", [POCase.wait_for_external, POCase.flag_blocked, POCase.flag_manual_review]
)
def test_a_case_awaiting_its_po_cannot_be_paused(interrupt: object) -> None:
    case = _requested()
    with pytest.raises(ConflictError):
        interrupt(case, "chờ NCC")  # type: ignore[operator]


def test_a_case_awaiting_its_po_can_be_cancelled_and_keeps_no_reference() -> None:
    case = _requested()
    case.cancel("đặt nhầm")
    assert _state(case) is CaseState.CANCELLED
    assert case.po_reference is None


def test_no_happy_path_step_moves_a_case_awaiting_its_po() -> None:
    case = _requested()
    with pytest.raises(ConflictError):
        case.request_deposit()


def test_apply_action_refuses_create_po_by_name() -> None:
    case = _requested()
    with pytest.raises(DomainError, match="create_po") as raised:
        apply_action(case, action=CaseAction.CREATE_PO, reason=None)
    assert raised.value.details["action"] == "create_po"
    assert _state(case) is CaseState.ORDER_REQUESTED


def test_every_action_is_in_exactly_one_dispatch_set() -> None:
    """The three sets cover `CaseAction` exactly: an action added without
    being placed in one is caught here, not as a KeyError on a click."""
    no_reason = set(po_case_module._NO_REASON_ACTIONS)
    reason = set(po_case_module._REASON_ACTIONS)
    command_only = set(po_case_module._COMMAND_ONLY_ACTIONS)
    for action in CaseAction:
        homes = [action in no_reason, action in reason, action in command_only]
        assert homes.count(True) == 1, action
    assert reason == set(REASON_REQUIRED_ACTIONS)
