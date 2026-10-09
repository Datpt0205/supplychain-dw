"""A PO case's step prepared by code, approved by the person whose duty it is
(tickets ai-automation/15 to 18; ADR 0025 amended AI-14 and AI-15).

Steps 11 to 17 belong to the PO case, which the product-case proposal
machinery (approval runs, `step_preparations`) does not carry: they take
typed results (an amount paid, a QC verdict, counted quantities) that a Zalo
code cannot, on a case of another kind. So they follow step 10's pattern
(AI-14): a worker lane drafts the step's paper once, the PO case page shows
what code finds now beside the result field a person types, and the person
holding the step's duty approves there, in one transaction.

What this module owns:

- **Which steps exist** (`POStepKind`) and what each is (`PO_STEPS`): the state
  it is taken from, the step it takes (`action`), the paper code drafts for
  it, the documents it reads, the results a person types, and whether
  approving it writes a price. A tenant turns steps on in its step
  preparation policy (`po_steps`); what a step does is code, not policy.
- **One step per state.** `step_for_state` finds the enabled step of the
  case's state; `PO_STEPS` never names a state twice (asserted at import).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.po_case import CaseAction, CaseState


class POStepKind(StrEnum):
    # Step 11 (ticket ai-automation/15): Cung ứng asks for the deposit with a
    # request code drafted; Kế toán pays at the bank, then confirms it.
    DEPOSIT_REQUEST = "deposit_request"
    DEPOSIT_PAYMENT = "deposit_payment"
    # Step 16 (ticket ai-automation/15): the same for the final payment.
    FINAL_PAYMENT_REQUEST = "final_payment_request"
    FINAL_PAYMENT = "final_payment"


class ResultKind(StrEnum):
    """What a person types for a physical step: an amount (a price field,
    hidden without the commercial scope), a date."""

    AMOUNT = "amount"
    DATE = "date"


@dataclass(frozen=True, slots=True)
class ResultField:
    name: str
    kind: ResultKind
    label: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class POStepSpec:
    kind: POStepKind
    state: CaseState
    action: CaseAction
    # The paper code drafts for the step; it becomes the case's document of
    # that type when the step is approved. None: the step drafts nothing.
    draft: DocumentType | None
    # The documents the step reads (their newest version on the case).
    sources: tuple[DocumentType, ...]
    results: tuple[ResultField, ...] = ()
    # Approving writes an amount: a payment row, or a paper that prints prices
    # (a request). The decider also needs `supply_chain.commercial.write`.
    writes_prices: bool = False


_PAID_AMOUNT = ResultField("paid_amount", ResultKind.AMOUNT, "Số tiền đã chi")
_PAID_ON = ResultField("paid_on", ResultKind.DATE, "Ngày chi")

PO_STEPS: Mapping[POStepKind, POStepSpec] = {
    spec.kind: spec
    for spec in (
        POStepSpec(
            kind=POStepKind.DEPOSIT_REQUEST,
            state=CaseState.PO_CREATED,
            action=CaseAction.REQUEST_DEPOSIT,
            draft=DocumentType.DEPOSIT_DOCS,
            sources=(DocumentType.PROFORMA_INVOICE,),
            writes_prices=True,
        ),
        POStepSpec(
            kind=POStepKind.DEPOSIT_PAYMENT,
            state=CaseState.WAITING_DEPOSIT,
            action=CaseAction.CONFIRM_DEPOSIT,
            draft=None,
            sources=(DocumentType.BANK_TRANSFER_RECEIPT,),
            results=(_PAID_AMOUNT, _PAID_ON),
            writes_prices=True,
        ),
        POStepSpec(
            kind=POStepKind.FINAL_PAYMENT_REQUEST,
            state=CaseState.ARRIVED_PORT,
            action=CaseAction.REQUEST_FINAL_PAYMENT,
            draft=DocumentType.PAYMENT_DOCS,
            sources=(DocumentType.COMMERCIAL_INVOICE,),
            writes_prices=True,
        ),
        POStepSpec(
            kind=POStepKind.FINAL_PAYMENT,
            state=CaseState.WAITING_PAYMENT,
            action=CaseAction.CONFIRM_PAYMENT,
            draft=None,
            sources=(DocumentType.BANK_TRANSFER_RECEIPT,),
            results=(_PAID_AMOUNT, _PAID_ON),
            writes_prices=True,
        ),
    )
}

_STATES = Counter(spec.state for spec in PO_STEPS.values())
assert all(n == 1 for n in _STATES.values()), "a PO state has two prepared steps"


def step_for_state(state: CaseState, enabled: Iterable[POStepKind]) -> POStepSpec | None:
    """The enabled step taken from `state`, if any."""
    return next((PO_STEPS[k] for k in enabled if PO_STEPS[k].state is state), None)


__all__ = [
    "PO_STEPS",
    "POStepKind",
    "POStepSpec",
    "ResultField",
    "ResultKind",
    "step_for_state",
]
