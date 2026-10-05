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

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from dw_kernel.errors import ConflictError, DomainError
from dw_kernel.ids import EntityId, TenantId, WorkspaceId


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
    """

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
    po_reference: str
    supplier_name: str
    state: CaseState = CaseState.PO_CREATED
    created_at: datetime | None = None
    version: int = 1
    interrupted_state: CaseState | None = None
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

    def __post_init__(self) -> None:
        if not self.po_reference.strip():
            raise ValueError("po_reference must not be blank")
        if not self.supplier_name.strip():
            raise ValueError("supplier_name must not be blank")

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

    # -- happy path: one guarded method per business action -----------------

    def request_deposit(self) -> None:
        self._advance(expected=CaseState.PO_CREATED, target=CaseState.WAITING_DEPOSIT)

    def confirm_deposit(self) -> None:
        self._advance(expected=CaseState.WAITING_DEPOSIT, target=CaseState.DEPOSIT_CONFIRMED)

    def start_pre_production(self) -> None:
        self._advance(expected=CaseState.DEPOSIT_CONFIRMED, target=CaseState.PRE_PRODUCTION)

    def start_production(self) -> None:
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
        if self.state in TERMINAL_STATES or self.state in _INTERRUPT_STATES:
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
# reason-requiring one (above) through the next. Together they cover
# CaseAction exhaustively — apply_action() trusts that rather than
# re-checking it, the same way missing_update.py's terminal-state set is
# trusted once built from CaseState itself.
_NO_REASON_ACTIONS: dict[CaseAction, Callable[[POCase], None]] = {
    CaseAction.REQUEST_DEPOSIT: POCase.request_deposit,
    CaseAction.CONFIRM_DEPOSIT: POCase.confirm_deposit,
    CaseAction.START_PRE_PRODUCTION: POCase.start_pre_production,
    CaseAction.START_PRODUCTION: POCase.start_production,
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


def apply_action(case: POCase, *, action: CaseAction, reason: str | None) -> None:
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
    not an error with no taxonomy code behind it.
    """
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

    from_state: CaseState
    to_state: CaseState
    reason: str | None
    occurred_at: datetime
