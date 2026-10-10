"""The PO case aggregate: one purchase order's journey from creation to
warehouse receipt, as guarded state transitions.

Shape follows dw_platform.domain.approval.ApprovalRequest already in this
repo — a mutable dataclass that owns every transition as a method raising on
an invalid one, rather than a second convention for the same kind of
aggregate. Scope for this file only: the state machine itself. SLA timing,
persistence and the AI reasoning skills that call these methods are separate,
later pieces — kept out so this stays reviewable on its own.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import StrEnum

from dw_kernel.errors import ConflictError, DomainError
from dw_kernel.ids import EntityId, TenantId, WorkspaceId
from dw_supply_chain.domain.packaging_design import ProductionGate


@dataclass(frozen=True, slots=True)
class POCaseId(EntityId):
    """Identifies one purchase-order case.

    Defined here rather than in dw_kernel: only truly universal identifiers
    live there, per that module's own docstring.
    """


class CaseState(StrEnum):
    """The Elmich product-to-stock happy path, plus the exceptions a real PO
    actually hits.

    Declaration order is the happy-path order; the five exception states
    after COMPLETED are not part of that sequence and are reached only
    through the interrupt/rework/cancel methods below, never by position.

    `ORDER_REQUESTED` heads it: ĐẶT HÀNG on a product case (step 9) opened
    this case and its PO does not exist yet; step 10's `create_po` gives it
    its reference (ADR 0017). Labelled "Chờ tạo PO".
    """

    ORDER_REQUESTED = "order_requested"
    PO_CREATED = "po_created"
    WAITING_DEPOSIT = "waiting_deposit"
    DEPOSIT_CONFIRMED = "deposit_confirmed"
    PRE_PRODUCTION = "pre_production"
    PRODUCTION = "production"
    QC = "qc"
    IN_TRANSIT = "in_transit"
    ARRIVED_PORT = "arrived_port"
    WAITING_PAYMENT = "waiting_payment"
    PAYMENT_COMPLETED = "payment_completed"
    WAREHOUSE_RECEIVING = "warehouse_receiving"
    COMPLETED = "completed"

    WAITING_EXTERNAL = "waiting_external"
    BLOCKED = "blocked"
    REWORK = "rework"
    MANUAL_REVIEW = "manual_review"
    CANCELLED = "cancelled"


# Public: `domain.missing_update` and the attention-queue repository query
# both need "which states are terminal" and previously each kept its own
# copy — the exact `failure-modes.md` #2 shape. One owner here, imported by
# both instead.
TERMINAL_STATES = frozenset({CaseState.COMPLETED, CaseState.CANCELLED})
_INTERRUPT_STATES = frozenset(
    {CaseState.WAITING_EXTERNAL, CaseState.BLOCKED, CaseState.MANUAL_REVIEW}
)
# Where a case may sit without a PO reference: awaiting its PO, or cancelled
# from there. The table's CHECK `ck_po_cases_po_reference` says the same.
_NO_REFERENCE_STATES = frozenset({CaseState.ORDER_REQUESTED, CaseState.CANCELLED})


def reference_label(po_reference: str | None) -> str:
    """What a message or a prompt names a case by: its PO reference, or, for
    a case awaiting its PO, that it has none yet. Never a made-up number
    (ADR 0017 refused a placeholder reference)."""
    return po_reference if po_reference is not None else "Chưa có số PO"


class OrderKind(StrEnum):
    """Step 10's classification: Hàng mới (a product out of stage 1) or Hàng
    đặt lại (a reorder, opened by `CreatePOCase` without stage 1)."""

    NEW = "new"
    REORDER = "reorder"


_CONTAINER = re.compile(r"^[A-Z]{4}[0-9]{7}$")


def container_number(raw: str) -> str:
    """An ISO 6346 container number (owner code, category, serial, check
    digit: four letters and seven digits), spaces and dashes dropped, or a 422."""
    code = re.sub(r"[\s-]", "", raw).upper()
    if not _CONTAINER.fullmatch(code):
        raise DomainError(
            "số container phải là 4 chữ cái và 7 chữ số (ISO 6346)",
            details={"field": "container_number"},
        )
    return code


@dataclass(frozen=True, slots=True)
class Shipping:
    """Steps 13-15's dates and container (ticket ai-automation/17): the ETD the
    schedule gave, the arrival at port, the container QC passed. Each written
    by the step that learns it, a person typing it beside AI's reading."""

    etd: date | None = None
    eta: date | None = None
    container_number: str | None = None


@dataclass(frozen=True, slots=True)
class POCaseLine:
    """One planned line of the order: a SKU of the product and how many.
    `quantity` starts from the SKU's planned quantity, which may be open
    (QE-11); `create_po` refuses to create the PO until every line has one.
    `sku_code` and `variant_label` are read back for display only."""

    sku_id: uuid.UUID
    quantity: int | None
    sku_code: str | None = None
    variant_label: str | None = None


@dataclass(slots=True)
class POCase:
    """One purchase order under management.

    Every transition is a named method below; nothing outside this class
    sets `state` directly, so an illegal transition cannot happen by
    forgetting a check at a call site — it cannot happen at all.
    """

    id: POCaseId
    tenant_id: TenantId
    workspace_id: WorkspaceId
    po_reference: str | None
    supplier_name: str
    state: CaseState = CaseState.PO_CREATED
    created_at: datetime | None = None
    version: int = 1
    interrupted_state: CaseState | None = None
    # Step 10's classification. The default is what every case opened before
    # ticket 05 was (the migration backfills `reorder`); `CreatePOCase` and
    # ĐẶT HÀNG always name one.
    order_kind: OrderKind = OrderKind.REORDER
    # Set when ĐẶT HÀNG opened the case: the product case it came from, and
    # its PIC and Category, stamped from that row in the same transaction and
    # never looked up again. `CreatePOCase` stamps its caller as PIC; a case
    # opened before ticket 05 has none.
    product_dev_case_id: uuid.UUID | None = None
    pic_user_id: uuid.UUID | None = None
    category: str | None = None
    # Read back by `get` only; a listed case carries none.
    lines: tuple[POCaseLine, ...] = ()
    # Steps 13-15's dates and container (ticket ai-automation/17).
    shipping: Shipping = field(default_factory=Shipping)
    # Accumulates one (from, to, reason) triple per transition this in-memory
    # instance has made since the last drain — `reason` is the value the five
    # REASON_REQUIRED_ACTIONS methods below were already given and validated
    # non-blank, carried through rather than discarded after the check; every
    # other transition has none. Excluded from eq/repr: it is bookkeeping for
    # the repository, not part of the case's own business identity.
    # `SqlPOCaseRepository.save()`/`add()` drains it in the same transaction
    # as the state write itself, so nothing outside a successful persist can
    # observe one of these as "happened".
    _pending_transitions: list[tuple[CaseState, CaseState, str | None]] = field(
        default_factory=list, compare=False, repr=False
    )
    # Line quantities `create_po` set, by SKU, for the repository to write.
    _pending_line_quantities: dict[uuid.UUID, int] = field(
        default_factory=dict, compare=False, repr=False
    )

    def __post_init__(self) -> None:
        if self.po_reference is None:
            if self.state not in _NO_REFERENCE_STATES:
                raise ValueError(
                    "po_reference may be absent only while the case awaits its PO"
                    f" (state {self.state.value})"
                )
        elif not self.po_reference.strip():
            raise ValueError("po_reference must not be blank")
        if not self.supplier_name.strip():
            raise ValueError("supplier_name must not be blank")

    @classmethod
    def requested(
        cls,
        *,
        id: POCaseId,
        tenant_id: TenantId,
        workspace_id: WorkspaceId,
        supplier_name: str,
        product_dev_case_id: uuid.UUID,
        pic_user_id: uuid.UUID,
        category: str,
        lines: tuple[POCaseLine, ...],
    ) -> POCase:
        """The case ĐẶT HÀNG opens (ADR 0017): no PO yet, a new product, the
        product case's PIC, Category and supplier as given."""
        return cls(
            id=id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            po_reference=None,
            supplier_name=supplier_name,
            state=CaseState.ORDER_REQUESTED,
            order_kind=OrderKind.NEW,
            product_dev_case_id=product_dev_case_id,
            pic_user_id=pic_user_id,
            category=category,
            lines=lines,
        )

    def pop_pending_line_quantities(self) -> dict[uuid.UUID, int]:
        """Returns and clears the line quantities set since the last call."""
        quantities = self._pending_line_quantities
        self._pending_line_quantities = {}
        return quantities

    def pop_pending_transitions(self) -> list[tuple[CaseState, CaseState, str | None]]:
        """Returns and clears the transitions recorded since the last call."""
        transitions = self._pending_transitions
        self._pending_transitions = []
        return transitions

    # -- internal guard shared by every named happy-path transition --------

    def _advance(
        self, *, expected: CaseState, target: CaseState, reason: str | None = None
    ) -> None:
        if self.state is not expected:
            raise ConflictError(
                f"cannot move to {target.value} from {self.state.value} "
                f"(expected {expected.value})",
                details={
                    "case_id": str(self.id),
                    "current_state": self.state.value,
                    "expected_state": expected.value,
                    "target_state": target.value,
                },
            )
        self._pending_transitions.append((self.state, target, reason))
        self.state = target
        self.version += 1

    # -- step 10: the PO is created -------------------------------------------

    def create_po(
        self,
        *,
        po_reference: str,
        order_kind: OrderKind,
        quantities: Mapping[uuid.UUID, int] | None = None,
    ) -> None:
        """Step 10: Cung ứng creates the PO, giving its reference and kind,
        and the line quantities still open or to correct. Every line must end
        with a quantity. Takes arguments, so it is a command of its own
        (`CreatePO`), never an `apply_action` dispatch."""
        reference = po_reference.strip()
        if not reference:
            raise ValueError("po_reference must not be blank")
        given = dict(quantities or {})
        known = {line.sku_id for line in self.lines}
        unknown = sorted(str(sku_id) for sku_id in given if sku_id not in known)
        if unknown:
            raise DomainError(
                "the order carries no line for this sku", details={"sku_id": ",".join(unknown)}
            )
        if any(quantity < 1 for quantity in given.values()):
            raise DomainError("a line quantity must be at least 1")
        lines = tuple(
            replace(line, quantity=given.get(line.sku_id, line.quantity)) for line in self.lines
        )
        unset = [str(line.sku_id) for line in lines if line.quantity is None]
        if self.state is CaseState.ORDER_REQUESTED and unset:
            raise ConflictError(
                "create_po cần số lượng cho mọi dòng",
                details={"case_id": str(self.id), "missing_quantity": ",".join(unset)},
            )
        self._advance(expected=CaseState.ORDER_REQUESTED, target=CaseState.PO_CREATED)
        self.po_reference = reference
        self.order_kind = order_kind
        self.lines = lines
        self._pending_line_quantities = given

    # -- happy path: one guarded method per business action -----------------

    def request_deposit(self) -> None:
        self._advance(expected=CaseState.PO_CREATED, target=CaseState.WAITING_DEPOSIT)

    def confirm_deposit(self) -> None:
        self._advance(expected=CaseState.WAITING_DEPOSIT, target=CaseState.DEPOSIT_CONFIRMED)

    def start_pre_production(self) -> None:
        self._advance(expected=CaseState.DEPOSIT_CONFIRMED, target=CaseState.PRE_PRODUCTION)

    def start_production(self, gate: ProductionGate) -> None:
        """Step 13. `gate` is the tenant's packaging rule and this case's
        pre-production test (slice PK): required by the signature, so no
        caller reaches `production` without asking it."""
        if self.state is CaseState.PRE_PRODUCTION:
            gate.check(self.id.value)
        self._advance(expected=CaseState.PRE_PRODUCTION, target=CaseState.PRODUCTION)

    def send_to_qc(self) -> None:
        self._advance(expected=CaseState.PRODUCTION, target=CaseState.QC)

    def pass_qc(self) -> None:
        self._advance(expected=CaseState.QC, target=CaseState.IN_TRANSIT)

    def arrive_at_port(self) -> None:
        self._advance(expected=CaseState.IN_TRANSIT, target=CaseState.ARRIVED_PORT)

    def request_final_payment(self) -> None:
        self._advance(expected=CaseState.ARRIVED_PORT, target=CaseState.WAITING_PAYMENT)

    def confirm_payment(self) -> None:
        self._advance(expected=CaseState.WAITING_PAYMENT, target=CaseState.PAYMENT_COMPLETED)

    def start_warehouse_receiving(self) -> None:
        self._advance(expected=CaseState.PAYMENT_COMPLETED, target=CaseState.WAREHOUSE_RECEIVING)

    def complete(self) -> None:
        self._advance(expected=CaseState.WAREHOUSE_RECEIVING, target=CaseState.COMPLETED)

    # -- QC exception: fails back into production, not a generic interrupt --

    def fail_qc(self, reason: str) -> None:
        if not reason.strip():
            raise ValueError("fail_qc requires a non-blank reason")
        self._advance(expected=CaseState.QC, target=CaseState.REWORK, reason=reason)

    def resume_from_rework(self) -> None:
        self._advance(expected=CaseState.REWORK, target=CaseState.PRODUCTION)

    # -- generic interrupts: any active state can pause for one of these
    #    three reasons, and resume() returns to exactly where it paused ------

    def _interrupt(self, *, reason: str, target: CaseState) -> None:
        if not reason.strip():
            raise ValueError(f"{target.value} requires a non-blank reason")
        # Awaiting its PO, nothing outside is awaited yet: no supplier holds
        # an order. Cancel the case, or create the PO.
        if (
            self.state in TERMINAL_STATES
            or self.state in _INTERRUPT_STATES
            or self.state is CaseState.ORDER_REQUESTED
        ):
            raise ConflictError(
                f"cannot interrupt from {self.state.value}",
                details={"case_id": str(self.id), "current_state": self.state.value},
            )
        if self.state is CaseState.REWORK:
            raise ConflictError(
                "cannot interrupt from rework; resume_from_rework first",
                details={"case_id": str(self.id), "current_state": self.state.value},
            )
        self.interrupted_state = self.state
        self._pending_transitions.append((self.state, target, reason))
        self.state = target
        self.version += 1

    def wait_for_external(self, reason: str) -> None:
        self._interrupt(reason=reason, target=CaseState.WAITING_EXTERNAL)

    def flag_blocked(self, reason: str) -> None:
        self._interrupt(reason=reason, target=CaseState.BLOCKED)

    def flag_manual_review(self, reason: str) -> None:
        self._interrupt(reason=reason, target=CaseState.MANUAL_REVIEW)

    def resume(self) -> None:
        if self.state not in _INTERRUPT_STATES or self.interrupted_state is None:
            raise ConflictError(
                f"cannot resume from {self.state.value}",
                details={"case_id": str(self.id), "current_state": self.state.value},
            )
        self._pending_transitions.append((self.state, self.interrupted_state, None))
        self.state = self.interrupted_state
        self.interrupted_state = None
        self.version += 1

    # -- the PIC ---------------------------------------------------------------

    def reassign_pic(self, *, new_pic: uuid.UUID, reason: str | None) -> uuid.UUID | None:
        """Hands the case to another PIC (stage-1 ticket 06), with a reason.
        Not a state transition: the state stays, no history row is written,
        the audit event says who changed it and why. A product case's PIC is
        its own, so this never reaches back to the case ĐẶT HÀNG opened this
        one from. Refused on a finished case, and to the PIC it already has.
        Returns the PIC it had."""
        if reason is None or not reason.strip():
            raise DomainError("reassign_pic requires a reason", details={"field": "reason"})
        if self.state in TERMINAL_STATES:
            raise ConflictError(
                f"cannot reassign the PIC of a case in {self.state.value}",
                details={"case_id": str(self.id), "current_state": self.state.value},
            )
        if new_pic == self.pic_user_id:
            raise DomainError("this person is already the PIC", details={"field": "pic_user_id"})
        previous = self.pic_user_id
        self.pic_user_id = new_pic
        self.version += 1
        return previous

    # -- terminal escape hatch ----------------------------------------------

    def cancel(self, reason: str) -> None:
        if not reason.strip():
            raise ValueError("cancel requires a non-blank reason")
        if self.state in TERMINAL_STATES:
            raise ConflictError(
                f"cannot cancel from {self.state.value}",
                details={"case_id": str(self.id), "current_state": self.state.value},
            )
        self._pending_transitions.append((self.state, CaseState.CANCELLED, reason))
        self.state = CaseState.CANCELLED
        self.interrupted_state = None
        self.version += 1


class CaseAction(StrEnum):
    """Every guarded method above, named — the closed set an `AdvancePOCase`
    caller may request.

    A closed enum, not a free string: this is a discrete choice a human
    makes from a fixed set of buttons a real UI shows for the case's current
    state, not free-form text a model interprets — the line between a model
    interpreting and code deciding: no model output is ever used directly
    as a route.
    Values match each method's own name exactly, so the mapping in
    `application/handlers.py` needs no separate translation table to drift
    from this list.
    """

    REQUEST_DEPOSIT = "request_deposit"
    CONFIRM_DEPOSIT = "confirm_deposit"
    START_PRE_PRODUCTION = "start_pre_production"
    START_PRODUCTION = "start_production"
    SEND_TO_QC = "send_to_qc"
    PASS_QC = "pass_qc"
    ARRIVE_AT_PORT = "arrive_at_port"
    REQUEST_FINAL_PAYMENT = "request_final_payment"
    CONFIRM_PAYMENT = "confirm_payment"
    START_WAREHOUSE_RECEIVING = "start_warehouse_receiving"
    COMPLETE = "complete"
    RESUME_FROM_REWORK = "resume_from_rework"
    RESUME = "resume"

    FAIL_QC = "fail_qc"
    WAIT_FOR_EXTERNAL = "wait_for_external"
    FLAG_BLOCKED = "flag_blocked"
    FLAG_MANUAL_REVIEW = "flag_manual_review"
    CANCEL = "cancel"

    # Step 10. Takes the reference and the kind, so its own command
    # (`CreatePO`) calls `POCase.create_po`; `apply_action` refuses it.
    CREATE_PO = "create_po"


# Every CaseAction whose underlying method requires `reason: str` — checked
# by `AdvancePOCase` before dispatch, since a blank reason should read as
# "action requires a reason", not surface as the method's own bare
# ValueError.
REASON_REQUIRED_ACTIONS = frozenset(
    {
        CaseAction.FAIL_QC,
        CaseAction.WAIT_FOR_EXTERNAL,
        CaseAction.FLAG_BLOCKED,
        CaseAction.FLAG_MANUAL_REVIEW,
        CaseAction.CANCEL,
    }
)

# Every no-argument CaseAction dispatches through this table; every
# reason-requiring one (above) through the next; one that takes arguments of
# its own has a command of its own (`_COMMAND_ONLY_ACTIONS`, below). The
# three cover CaseAction exactly, each action in one
# (`test_every_action_is_in_exactly_one_dispatch_set`).
_NO_REASON_ACTIONS: dict[CaseAction, Callable[[POCase], None]] = {
    CaseAction.REQUEST_DEPOSIT: POCase.request_deposit,
    CaseAction.CONFIRM_DEPOSIT: POCase.confirm_deposit,
    CaseAction.START_PRE_PRODUCTION: POCase.start_pre_production,
    CaseAction.SEND_TO_QC: POCase.send_to_qc,
    CaseAction.PASS_QC: POCase.pass_qc,
    CaseAction.ARRIVE_AT_PORT: POCase.arrive_at_port,
    CaseAction.REQUEST_FINAL_PAYMENT: POCase.request_final_payment,
    CaseAction.CONFIRM_PAYMENT: POCase.confirm_payment,
    CaseAction.START_WAREHOUSE_RECEIVING: POCase.start_warehouse_receiving,
    CaseAction.COMPLETE: POCase.complete,
    CaseAction.RESUME_FROM_REWORK: POCase.resume_from_rework,
    CaseAction.RESUME: POCase.resume,
}

_REASON_ACTIONS: dict[CaseAction, Callable[[POCase, str], None]] = {
    CaseAction.FAIL_QC: POCase.fail_qc,
    CaseAction.WAIT_FOR_EXTERNAL: POCase.wait_for_external,
    CaseAction.FLAG_BLOCKED: POCase.flag_blocked,
    CaseAction.FLAG_MANUAL_REVIEW: POCase.flag_manual_review,
    CaseAction.CANCEL: POCase.cancel,
}


# Actions with arguments of their own, each taken through its own command.
_COMMAND_ONLY_ACTIONS = frozenset({CaseAction.CREATE_PO})

# Step 13 asks the production gate (slice PK); `apply_action` takes it.
GATED_ACTIONS = frozenset({CaseAction.START_PRODUCTION})


def apply_action(
    case: POCase,
    *,
    action: CaseAction,
    reason: str | None,
    gate: ProductionGate | None = None,
) -> None:
    """Dispatches one `CaseAction` to the guarded method it names.

    The single owner of "which method does this action call" — moved here
    from `application/handlers.py` once a second caller (the approval-gated
    graph's own apply node, `workflows/advance_case_graph.py`) needed the
    exact same dispatch: a case-transition rule belongs in the domain layer,
    not duplicated once per caller. `AdvancePOCase` calls this directly for
    an action that needs no approval; the graph's apply node calls it after
    a human approves. A blank/missing reason on a `REASON_REQUIRED_ACTIONS`
    member is refused here as `DomainError`, before the method's own bare
    `ValueError` guard — the caller gets `this action requires a reason`,
    not an error with no taxonomy code behind it. An action with a command of
    its own (`create_po`) is refused by name. `start_production` needs the
    production gate; without one it is refused, never let through.
    """
    if action in GATED_ACTIONS:
        if gate is None:
            raise DomainError(
                f"{action.value} needs the production gate", details={"action": action.value}
            )
        case.start_production(gate)
        return
    if action in _COMMAND_ONLY_ACTIONS:
        raise DomainError(
            f"{action.value} is taken through its own command, not as a plain step",
            details={"action": action.value},
        )
    if action in REASON_REQUIRED_ACTIONS:
        if reason is None or not reason.strip():
            raise DomainError("this action requires a reason", details={"action": action.value})
        _REASON_ACTIONS[action](case, reason)
    else:
        _NO_REASON_ACTIONS[action](case)


@dataclass(frozen=True, slots=True)
class CaseTransition:
    """One already-persisted row of `supply_chain.po_case_state_transitions`
    — the Case Workspace's own timeline, read back rather than accumulated
    like `POCase._pending_transitions`, which drains on every save and
    exists only to get a new row written, not to be re-read as history."""

    # None only for an imported case's first row (ticket onboarding/02).
    from_state: CaseState | None
    to_state: CaseState
    reason: str | None
    occurred_at: datetime
