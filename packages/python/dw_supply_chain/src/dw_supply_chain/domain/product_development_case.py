"""The product-development case: one proposed product through stage 1 of the
Elmich process, steps 1-5 in this slice (stage-1 ticket 01, ADR 0016).

Same shape as `POCase`: a mutable dataclass whose named methods are the only
way its state moves, a closed action enum, and one dispatch,
`apply_product_action`. What differs, and why:

- **One table of forward steps.** `_FORWARD` says, for each step, the state it
  leaves and the state it reaches. The methods read it to guard themselves and
  `available_actions` reads it to say what a page may offer, so the buttons a
  person sees and the steps the case accepts cannot disagree.
- **Each step records who took it and what it did to the sample rounds.** A
  pending `ProductCaseStep` carries the action and the actor, not only the two
  states, and says whether it opened a round or closed one. The repository
  writes exactly that, in the transaction that saves the state.
- **A step that needs paper checks the paper here.** Passing a sample needs a
  Biên bản đánh giá mẫu of THIS case, uploaded after THIS round opened;
  requesting a revision needs a Phiếu yêu cầu chỉnh sửa the same way. The
  handler fetches the document the caller named under RLS and hands it in; the
  rule that it belongs to the round is decided here, once.

The PIC is stamped from the actor at `propose` and nowhere else takes one.
Steps 6 onwards (BGĐ review, BM04, item code, sign-off, ĐẶT HÀNG) are later
tickets; `pending_bod_review` is where this slice stops.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Self

from dw_kernel.errors import ConflictError, DomainError, DWError
from dw_kernel.ids import EntityId, TenantId, WorkspaceId
from dw_supply_chain.domain.case_document import CaseDocument, CaseKind, DocumentType


@dataclass(frozen=True, slots=True)
class ProductDevelopmentCaseId(EntityId):
    """Identifies one product-development case (Hồ sơ phát triển sản phẩm)."""


class ProductDevState(StrEnum):
    """The states this slice reaches. Labels live in `CONTEXT.md` and, for the
    web, in one table beside the product-case pages."""

    PROPOSED = "proposed"
    SAMPLE_REQUESTED = "sample_requested"
    SAMPLE_TESTING = "sample_testing"
    REVISION_REQUESTED = "revision_requested"
    # Terminal for now: S2 starts the BGĐ review from `pass_sample`.
    PENDING_BOD_REVIEW = "pending_bod_review"

    WAITING_EXTERNAL = "waiting_external"
    BLOCKED = "blocked"
    MANUAL_REVIEW = "manual_review"
    CANCELLED = "cancelled"


PRODUCT_TERMINAL_STATES = frozenset({ProductDevState.CANCELLED})
PRODUCT_INTERRUPT_STATES = frozenset(
    {ProductDevState.WAITING_EXTERNAL, ProductDevState.BLOCKED, ProductDevState.MANUAL_REVIEW}
)


class ProductAction(StrEnum):
    """Every step a person takes on a product-development case.

    Its own enum, never `CaseAction`: five names are shared (`cancel`,
    `resume`, `wait_for_external`, `flag_blocked`, `flag_manual_review`) and a
    PO policy or approval keyed by one of them must not reach this case."""

    PROPOSE = "propose"
    REQUEST_SAMPLE = "request_sample"
    RECEIVE_SAMPLE = "receive_sample"
    PASS_SAMPLE = "pass_sample"
    REQUEST_REVISION = "request_revision"
    RECEIVE_REVISED_SAMPLE = "receive_revised_sample"
    REJECT_SAMPLE = "reject_sample"
    WAIT_FOR_EXTERNAL = "wait_for_external"
    FLAG_BLOCKED = "flag_blocked"
    FLAG_MANUAL_REVIEW = "flag_manual_review"
    RESUME = "resume"
    CANCEL = "cancel"


PRODUCT_REASON_REQUIRED_ACTIONS = frozenset(
    {
        ProductAction.REQUEST_REVISION,
        ProductAction.REJECT_SAMPLE,
        ProductAction.WAIT_FOR_EXTERNAL,
        ProductAction.FLAG_BLOCKED,
        ProductAction.FLAG_MANUAL_REVIEW,
        ProductAction.CANCEL,
    }
)

# Each forward step: the state it leaves, the state it reaches.
_FORWARD: dict[ProductAction, tuple[ProductDevState, ProductDevState]] = {
    ProductAction.REQUEST_SAMPLE: (ProductDevState.PROPOSED, ProductDevState.SAMPLE_REQUESTED),
    ProductAction.RECEIVE_SAMPLE: (
        ProductDevState.SAMPLE_REQUESTED,
        ProductDevState.SAMPLE_TESTING,
    ),
    ProductAction.PASS_SAMPLE: (ProductDevState.SAMPLE_TESTING, ProductDevState.PENDING_BOD_REVIEW),
    ProductAction.REQUEST_REVISION: (
        ProductDevState.SAMPLE_TESTING,
        ProductDevState.REVISION_REQUESTED,
    ),
    ProductAction.RECEIVE_REVISED_SAMPLE: (
        ProductDevState.REVISION_REQUESTED,
        ProductDevState.SAMPLE_TESTING,
    ),
    ProductAction.REJECT_SAMPLE: (ProductDevState.SAMPLE_TESTING, ProductDevState.CANCELLED),
}
_INTERRUPTS: dict[ProductAction, ProductDevState] = {
    ProductAction.WAIT_FOR_EXTERNAL: ProductDevState.WAITING_EXTERNAL,
    ProductAction.FLAG_BLOCKED: ProductDevState.BLOCKED,
    ProductAction.FLAG_MANUAL_REVIEW: ProductDevState.MANUAL_REVIEW,
}

# The document type a step takes. `pass_sample` and `request_revision` cannot
# be taken without theirs; `reject_sample` may carry an evaluation.
ACTION_DOCUMENT_TYPE: dict[ProductAction, DocumentType] = {
    ProductAction.PASS_SAMPLE: DocumentType.SAMPLE_EVALUATION,
    ProductAction.REQUEST_REVISION: DocumentType.SAMPLE_REVISION_REQUEST,
    ProductAction.REJECT_SAMPLE: DocumentType.SAMPLE_EVALUATION,
}
DOCUMENT_REQUIRED_ACTIONS = frozenset({ProductAction.PASS_SAMPLE, ProductAction.REQUEST_REVISION})


@dataclass(frozen=True, slots=True)
class ProductActionOption:
    """A step the case accepts now, and what a person must send with it.
    Read from the same tables the methods guard themselves with, so a form
    asks for exactly what the step will check."""

    action: ProductAction
    reason_required: bool
    takes_supplier: bool
    document_type: DocumentType | None
    document_required: bool


class SampleResult(StrEnum):
    """How a sample round closed (Vòng mẫu: Đạt, Cần chỉnh sửa, Hủy)."""

    PASSED = "passed"
    NEEDS_REVISION = "needs_revision"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class RoundClosure:
    """A step closed the current round: its result, set once, and the paper
    it was closed on."""

    round_no: int
    result: SampleResult
    evaluation_document_id: uuid.UUID | None
    # Set for a revision: the Phiếu yêu cầu chỉnh sửa the request row keeps.
    revision_document_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class ProductCaseStep:
    """One step taken in memory and not yet saved. The repository writes its
    history row, and the round it opens or closes, with the state."""

    action: ProductAction
    # None only for `propose`: the case did not exist before it.
    from_state: ProductDevState | None
    to_state: ProductDevState
    actor_id: uuid.UUID
    reason: str | None
    opens_round: int | None = None
    closes_round: RoundClosure | None = None


def document_refusal(case_id: uuid.UUID, action: ProductAction) -> DWError:
    """The one refusal for a document a step cannot take.

    A step that takes no document refuses any. For one that does, a paper
    absent, foreign, unreadable to the caller, of the wrong type or from an
    earlier round is one 409 naming the type that is missing, never which of
    those it was: whether a document id exists in another case is not the
    caller's to learn."""
    expected = ACTION_DOCUMENT_TYPE.get(action)
    if expected is None:
        return DomainError("this action takes no document", details={"action": action.value})
    return ConflictError(
        f"{action.value} cần {expected.value} của vòng mẫu hiện tại, thuộc hồ sơ này",
        details={
            "case_id": str(case_id),
            "action": action.value,
            "missing_document_type": expected.value,
        },
    )


def _required_text(value: str, name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise DomainError(f"{name} must not be blank", details={"field": name})
    return cleaned


def _reason(action: ProductAction, reason: str | None) -> str:
    if reason is None or not reason.strip():
        raise DomainError("this action requires a reason", details={"action": action.value})
    return reason


@dataclass(slots=True)
class ProductDevelopmentCase:
    """One product proposed at step 1, in one workspace.

    `round_opened_at` is when the CURRENT sample round's row was written, read
    back by the repository; None before the first sample and for a round
    opened in this instance and not yet saved."""

    id: ProductDevelopmentCaseId
    tenant_id: TenantId
    workspace_id: WorkspaceId
    proposal_code: str
    product_name: str
    category: str
    pic_user_id: uuid.UUID
    created_by: uuid.UUID
    supplier_name: str | None = None
    state: ProductDevState = ProductDevState.PROPOSED
    interrupted_state: ProductDevState | None = None
    sample_round: int = 0
    round_opened_at: datetime | None = None
    version: int = 1
    created_at: datetime | None = None
    _pending_steps: list[ProductCaseStep] = field(default_factory=list, compare=False, repr=False)

    def __post_init__(self) -> None:
        self.proposal_code = _required_text(self.proposal_code, "proposal_code")
        self.product_name = _required_text(self.product_name, "product_name")
        self.category = _required_text(self.category, "category")
        if self.supplier_name is not None:
            self.supplier_name = _required_text(self.supplier_name, "supplier_name")

    @classmethod
    def propose(
        cls,
        *,
        id: ProductDevelopmentCaseId,
        tenant_id: TenantId,
        workspace_id: WorkspaceId,
        proposal_code: str,
        product_name: str,
        category: str,
        actor_id: uuid.UUID,
    ) -> Self:
        """Step 1. Whoever proposes is the PIC: there is no other way to name
        one (Elmich's PIC rule, process.md section 3)."""
        case = cls(
            id=id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            proposal_code=proposal_code,
            product_name=product_name,
            category=category,
            pic_user_id=actor_id,
            created_by=actor_id,
        )
        case._pending_steps.append(
            ProductCaseStep(
                action=ProductAction.PROPOSE,
                from_state=None,
                to_state=ProductDevState.PROPOSED,
                actor_id=actor_id,
                reason=None,
            )
        )
        return case

    def pop_pending_steps(self) -> list[ProductCaseStep]:
        steps = self._pending_steps
        self._pending_steps = []
        return steps

    def available_actions(self) -> frozenset[ProductAction]:
        """What a person may ask of the case now; who may is the duty policy's."""
        if self.state in PRODUCT_TERMINAL_STATES:
            return frozenset()
        if self.state in PRODUCT_INTERRUPT_STATES:
            return frozenset({ProductAction.RESUME, ProductAction.CANCEL})
        forward = {action for action, (source, _) in _FORWARD.items() if source is self.state}
        return frozenset({*forward, *_INTERRUPTS, ProductAction.CANCEL})

    def action_options(self) -> list[ProductActionOption]:
        """`available_actions`, in declaration order, each with what it takes."""
        available = self.available_actions()
        return [
            ProductActionOption(
                action=action,
                reason_required=action in PRODUCT_REASON_REQUIRED_ACTIONS,
                takes_supplier=action is ProductAction.REQUEST_SAMPLE,
                document_type=ACTION_DOCUMENT_TYPE.get(action),
                document_required=action in DOCUMENT_REQUIRED_ACTIONS,
            )
            for action in ProductAction
            if action in available
        ]

    # -- the guard every step goes through ---------------------------------------

    def _move(
        self,
        action: ProductAction,
        target: ProductDevState,
        *,
        actor_id: uuid.UUID,
        reason: str | None = None,
        opens_round: int | None = None,
        closes_round: RoundClosure | None = None,
    ) -> None:
        self._pending_steps.append(
            ProductCaseStep(
                action=action,
                from_state=self.state,
                to_state=target,
                actor_id=actor_id,
                reason=reason,
                opens_round=opens_round,
                closes_round=closes_round,
            )
        )
        self.state = target
        self.version += 1

    def _expect(self, action: ProductAction) -> ProductDevState:
        source, target = _FORWARD[action]
        if self.state is not source:
            raise ConflictError(
                f"cannot {action.value} from {self.state.value} (expected {source.value})",
                details={
                    "case_id": str(self.id),
                    "current_state": self.state.value,
                    "expected_state": source.value,
                    "action": action.value,
                },
            )
        return target

    def _round_document(
        self, action: ProductAction, document: CaseDocument | None
    ) -> uuid.UUID | None:
        """The id of `document` if it is this round's paper for `action`."""
        if document is None:
            if action in DOCUMENT_REQUIRED_ACTIONS:
                raise document_refusal(self.id.value, action)
            return None
        belongs = (
            document.case_kind is CaseKind.PRODUCT
            and document.case_id == self.id.value
            and document.tenant_id == self.tenant_id.value
            and document.workspace_id == self.workspace_id.value
            and document.doc_type is ACTION_DOCUMENT_TYPE[action]
            and self.round_opened_at is not None
            and document.uploaded_at >= self.round_opened_at
        )
        if not belongs:
            raise document_refusal(self.id.value, action)
        return document.id.value

    # -- steps 2-5 ---------------------------------------------------------------

    def request_sample(self, *, actor_id: uuid.UUID, supplier_name: str) -> None:
        """Step 2: Cung ứng asks the supplier for a sample; the supplier is
        named here, where it is first contacted."""
        name = _required_text(supplier_name, "supplier_name")
        target = self._expect(ProductAction.REQUEST_SAMPLE)
        self._move(ProductAction.REQUEST_SAMPLE, target, actor_id=actor_id)
        self.supplier_name = name

    def receive_sample(self, *, actor_id: uuid.UUID) -> None:
        """Step 2 ends: the sample is in, round 1 opens."""
        target = self._expect(ProductAction.RECEIVE_SAMPLE)
        self._open_round(ProductAction.RECEIVE_SAMPLE, target, actor_id=actor_id)

    def receive_revised_sample(self, *, actor_id: uuid.UUID) -> None:
        """Step 5: the revised sample is in, the next round opens."""
        target = self._expect(ProductAction.RECEIVE_REVISED_SAMPLE)
        self._open_round(ProductAction.RECEIVE_REVISED_SAMPLE, target, actor_id=actor_id)

    def _open_round(
        self, action: ProductAction, target: ProductDevState, *, actor_id: uuid.UUID
    ) -> None:
        self._move(action, target, actor_id=actor_id, opens_round=self.sample_round + 1)
        self.sample_round += 1
        self.round_opened_at = None

    def pass_sample(self, *, actor_id: uuid.UUID, evaluation: CaseDocument | None) -> None:
        """Step 3, Đạt: closes the round on this round's Biên bản đánh giá mẫu."""
        target = self._expect(ProductAction.PASS_SAMPLE)
        document_id = self._round_document(ProductAction.PASS_SAMPLE, evaluation)
        self._move(
            ProductAction.PASS_SAMPLE,
            target,
            actor_id=actor_id,
            closes_round=RoundClosure(self.sample_round, SampleResult.PASSED, document_id, None),
        )

    def request_revision(
        self, *, actor_id: uuid.UUID, reason: str | None, revision_request: CaseDocument | None
    ) -> None:
        """Steps 3-4, Cần chỉnh sửa: closes the round on the Phiếu yêu cầu chỉnh
        sửa; the reason is what the supplier is asked to change."""
        changes = _reason(ProductAction.REQUEST_REVISION, reason)
        target = self._expect(ProductAction.REQUEST_REVISION)
        document_id = self._round_document(ProductAction.REQUEST_REVISION, revision_request)
        self._move(
            ProductAction.REQUEST_REVISION,
            target,
            actor_id=actor_id,
            reason=changes,
            closes_round=RoundClosure(
                self.sample_round, SampleResult.NEEDS_REVISION, None, document_id
            ),
        )

    def reject_sample(
        self,
        *,
        actor_id: uuid.UUID,
        reason: str | None,
        evaluation: CaseDocument | None = None,
    ) -> None:
        """Step 3, Hủy: closes the round as rejected and cancels the case."""
        why = _reason(ProductAction.REJECT_SAMPLE, reason)
        target = self._expect(ProductAction.REJECT_SAMPLE)
        document_id = self._round_document(ProductAction.REJECT_SAMPLE, evaluation)
        self._move(
            ProductAction.REJECT_SAMPLE,
            target,
            actor_id=actor_id,
            reason=why,
            closes_round=RoundClosure(self.sample_round, SampleResult.REJECTED, document_id, None),
        )

    # -- interrupts, resume, cancel ----------------------------------------------

    def _interrupt(self, action: ProductAction, *, actor_id: uuid.UUID, reason: str | None) -> None:
        why = _reason(action, reason)
        if self.state in PRODUCT_TERMINAL_STATES or self.state in PRODUCT_INTERRUPT_STATES:
            raise ConflictError(
                f"cannot {action.value} from {self.state.value}",
                details={"case_id": str(self.id), "current_state": self.state.value},
            )
        paused_at = self.state
        self._move(action, _INTERRUPTS[action], actor_id=actor_id, reason=why)
        self.interrupted_state = paused_at

    def wait_for_external(self, *, actor_id: uuid.UUID, reason: str | None) -> None:
        self._interrupt(ProductAction.WAIT_FOR_EXTERNAL, actor_id=actor_id, reason=reason)

    def flag_blocked(self, *, actor_id: uuid.UUID, reason: str | None) -> None:
        self._interrupt(ProductAction.FLAG_BLOCKED, actor_id=actor_id, reason=reason)

    def flag_manual_review(self, *, actor_id: uuid.UUID, reason: str | None) -> None:
        self._interrupt(ProductAction.FLAG_MANUAL_REVIEW, actor_id=actor_id, reason=reason)

    def resume(self, *, actor_id: uuid.UUID) -> None:
        paused_at = self.interrupted_state
        if self.state not in PRODUCT_INTERRUPT_STATES or paused_at is None:
            raise ConflictError(
                f"cannot resume from {self.state.value}",
                details={"case_id": str(self.id), "current_state": self.state.value},
            )
        self._move(ProductAction.RESUME, paused_at, actor_id=actor_id)
        self.interrupted_state = None

    def cancel(self, *, actor_id: uuid.UUID, reason: str | None) -> None:
        """Cancelling while a sample is in test, or paused in test, closes that
        round as rejected (Hủy) in the same step, so no round of a cancelled
        case stays open."""
        why = _reason(ProductAction.CANCEL, reason)
        if self.state in PRODUCT_TERMINAL_STATES:
            raise ConflictError(
                f"cannot cancel from {self.state.value}",
                details={"case_id": str(self.id), "current_state": self.state.value},
            )
        in_test = ProductDevState.SAMPLE_TESTING in (self.state, self.interrupted_state)
        self._move(
            ProductAction.CANCEL,
            ProductDevState.CANCELLED,
            actor_id=actor_id,
            reason=why,
            closes_round=(
                RoundClosure(self.sample_round, SampleResult.REJECTED, None, None)
                if in_test
                else None
            ),
        )
        self.interrupted_state = None


@dataclass(frozen=True, slots=True)
class ProductActionInput:
    """What a person sent with a step. Each step reads what it takes and
    refuses what it does not, so nothing sent is silently dropped."""

    actor_id: uuid.UUID
    reason: str | None = None
    supplier_name: str | None = None
    # Already fetched by the handler under the caller's RLS; None when the
    # caller named none.
    document: CaseDocument | None = None


_STEPS: dict[ProductAction, Callable[[ProductDevelopmentCase, ProductActionInput], None]] = {
    ProductAction.REQUEST_SAMPLE: lambda case, given: case.request_sample(
        actor_id=given.actor_id, supplier_name=given.supplier_name or ""
    ),
    ProductAction.RECEIVE_SAMPLE: lambda case, given: case.receive_sample(actor_id=given.actor_id),
    ProductAction.PASS_SAMPLE: lambda case, given: case.pass_sample(
        actor_id=given.actor_id, evaluation=given.document
    ),
    ProductAction.REQUEST_REVISION: lambda case, given: case.request_revision(
        actor_id=given.actor_id, reason=given.reason, revision_request=given.document
    ),
    ProductAction.RECEIVE_REVISED_SAMPLE: lambda case, given: case.receive_revised_sample(
        actor_id=given.actor_id
    ),
    ProductAction.REJECT_SAMPLE: lambda case, given: case.reject_sample(
        actor_id=given.actor_id, reason=given.reason, evaluation=given.document
    ),
    ProductAction.WAIT_FOR_EXTERNAL: lambda case, given: case.wait_for_external(
        actor_id=given.actor_id, reason=given.reason
    ),
    ProductAction.FLAG_BLOCKED: lambda case, given: case.flag_blocked(
        actor_id=given.actor_id, reason=given.reason
    ),
    ProductAction.FLAG_MANUAL_REVIEW: lambda case, given: case.flag_manual_review(
        actor_id=given.actor_id, reason=given.reason
    ),
    ProductAction.RESUME: lambda case, given: case.resume(actor_id=given.actor_id),
    ProductAction.CANCEL: lambda case, given: case.cancel(
        actor_id=given.actor_id, reason=given.reason
    ),
}


def apply_product_action(
    case: ProductDevelopmentCase, *, action: ProductAction, given: ProductActionInput
) -> None:
    """Dispatches one `ProductAction` to the method it names: the one owner of
    that mapping, as `apply_action` is for `POCase`. `propose` is not here:
    it opens a case rather than moving one."""
    if action is ProductAction.PROPOSE:
        raise DomainError(
            "propose opens a new case; it is not a step on one", details={"action": action.value}
        )
    if given.supplier_name is not None and action is not ProductAction.REQUEST_SAMPLE:
        raise DomainError(
            "only request_sample takes a supplier name", details={"action": action.value}
        )
    if given.reason is not None and action not in PRODUCT_REASON_REQUIRED_ACTIONS:
        raise DomainError("this action takes no reason", details={"action": action.value})
    if given.document is not None and action not in ACTION_DOCUMENT_TYPE:
        raise document_refusal(case.id.value, action)
    _STEPS[action](case, given)


@dataclass(frozen=True, slots=True)
class ProductCaseTransition:
    """One saved row of the case's history, read back for its timeline."""

    action: ProductAction
    from_state: ProductDevState | None
    to_state: ProductDevState
    reason: str | None
    actor_id: uuid.UUID
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class SampleRound:
    """One saved sample round, with the revision request that closed it if
    it closed as needing revision."""

    round_no: int
    opened_at: datetime
    opened_by: uuid.UUID
    result: SampleResult | None
    evaluation_document_id: uuid.UUID | None
    closed_at: datetime | None
    closed_by: uuid.UUID | None
    revision_document_id: uuid.UUID | None
    requested_changes: str | None
