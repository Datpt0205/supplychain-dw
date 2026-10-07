"""The product-development case: one proposed product through stage 1 of the
Elmich process, steps 1-9 so far (stage-1 tickets 01-04, ADR 0016).

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
  requesting a revision needs a Phiếu yêu cầu chỉnh sửa the same way.
  Completing the profile (step 7) needs a BM04, and confirming with the
  supplier (step 8) the supplier's confirmation email, each of THIS case and
  uploaded since the case reached THAT step (`stage_entered_at`). The handler
  fetches the document the caller named under RLS and hands it in; the rule
  that it belongs to the round or the step is decided here, once, and
  `action_options` hands the page the same bound (`documents_since`).

- **Some steps are no person's button.** BGĐ's two outcomes of step 6 and
  the sign-off's two of step 9 (`GRAPH_ONLY_ACTIONS`) are applied by their
  approval graph after the approval is decided, with the decider as the
  actor; `available_actions` never offers them and the step command refuses
  them.
- **Step 9 codes the product before anyone signs.** In `item_coding` Cung ứng
  issues the official item code and adds or removes SKUs (`CODING_ACTIONS`,
  each a step that leaves the case where it is). A SKU needs the item code
  first; submitting for sign-off needs both (`unmet_for`, the one answer
  the guard and the page read). Whether a code is taken is the database's
  answer, never this class's (ADR 0018). A rejected sign-off returns the case
  to `item_coding` with its codes kept.

- **ĐẶT HÀNG ends the case and opens the PO case (ticket 05, ADR 0017).**
  `place_order` moves `ready_to_order` to `ordered`, terminal, and hands back
  the PO case awaiting its PO, carrying this case's PIC, Category, supplier
  and SKUs. It is a command of its own (`PlaceOrder`), never a plain step:
  the two rows are written in one transaction.

The PIC is stamped from the actor at `propose` and nowhere else takes one.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Self

from dw_kernel.errors import ConflictError, DomainError, DWError, NotFoundError
from dw_kernel.ids import EntityId, TenantId, WorkspaceId
from dw_supply_chain.domain.case_document import CaseDocument, CaseKind, DocumentType
from dw_supply_chain.domain.po_case import POCase, POCaseId, POCaseLine


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
    # Waiting for BGĐ's decision on the passed sample (step 6). Only the review
    # graph moves it on; a person may only cancel.
    PENDING_BOD_REVIEW = "pending_bod_review"
    # BGĐ approved; R&D completes the BM04 profile (step 7, S3).
    PROFILE_IN_PROGRESS = "profile_in_progress"
    # BM04 done; TP Cung ứng confirms the product with the supplier (step 8).
    SUPPLIER_CONFIRMATION = "supplier_confirmation"
    # The supplier confirmed; the item code and SKUs come next (step 9, S4).
    ITEM_CODING = "item_coding"
    # Submitted for sign-off (step 9): its approvals are decided in the order
    # the tenant's policy gives. Only the sign-off graph moves it on; a person
    # may only cancel.
    PENDING_SIGNOFF = "pending_signoff"
    # Every sign-off step approved; ĐẶT HÀNG comes next.
    READY_TO_ORDER = "ready_to_order"
    # ĐẶT HÀNG pressed: the PO case carries on (ticket 05). Terminal.
    ORDERED = "ordered"

    WAITING_EXTERNAL = "waiting_external"
    BLOCKED = "blocked"
    MANUAL_REVIEW = "manual_review"
    CANCELLED = "cancelled"


PRODUCT_TERMINAL_STATES = frozenset({ProductDevState.CANCELLED, ProductDevState.ORDERED})
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
    # Step 7: R&D completes the BM04 profile.
    COMPLETE_PROFILE = "complete_profile"
    # Step 8: TP Cung ứng confirms the product agreed with the supplier, on
    # the supplier's email (QE-09: confirmed in the app, no mailbox read).
    CONFIRM_WITH_SUPPLIER = "confirm_with_supplier"
    # Step 9: Cung ứng codes the product, then submits it for sign-off.
    ISSUE_ITEM_CODE = "issue_item_code"
    ADD_SKU = "add_sku"
    REMOVE_SKU = "remove_sku"
    SUBMIT_FOR_SIGNOFF = "submit_for_signoff"
    # ĐẶT HÀNG: the case is ordered and its PO case opened (ticket 05).
    PLACE_ORDER = "place_order"
    WAIT_FOR_EXTERNAL = "wait_for_external"
    FLAG_BLOCKED = "flag_blocked"
    FLAG_MANUAL_REVIEW = "flag_manual_review"
    RESUME = "resume"
    CANCEL = "cancel"
    # Step 6: BGĐ's decision, applied by the review graph, never by a person.
    BOD_APPROVE = "bod_approve"
    BOD_REJECT = "bod_reject"
    # Step 9's sign-off outcome, applied by the sign-off graph: every step
    # approved, or one step not approved.
    SIGNOFF_APPROVE = "signoff_approve"
    SIGNOFF_REJECT = "signoff_reject"


# The steps only a workflow applies, after an approval is decided. One owner:
# the step command refuses them, the duty policy may not name them, and no
# state offers them.
GRAPH_ONLY_ACTIONS = frozenset(
    {
        ProductAction.BOD_APPROVE,
        ProductAction.BOD_REJECT,
        ProductAction.SIGNOFF_APPROVE,
        ProductAction.SIGNOFF_REJECT,
    }
)

# The steps a person takes through a command of their own, never the step
# command: ĐẶT HÀNG writes the PO case with the step (`PlaceOrder`).
COMMAND_ONLY_ACTIONS = frozenset({ProductAction.PLACE_ORDER})

# Step 9's coding: each leaves the case in `item_coding`, and only there.
CODING_ACTIONS = frozenset(
    {ProductAction.ISSUE_ITEM_CODE, ProductAction.ADD_SKU, ProductAction.REMOVE_SKU}
)

# The states where a person may only cancel: an approval is being decided, and
# a pause there would leave it waiting on a case that is not.
AWAITING_APPROVAL_STATES = frozenset(
    {ProductDevState.PENDING_BOD_REVIEW, ProductDevState.PENDING_SIGNOFF}
)

PRODUCT_REASON_REQUIRED_ACTIONS = frozenset(
    {
        ProductAction.REQUEST_REVISION,
        ProductAction.REJECT_SAMPLE,
        ProductAction.WAIT_FOR_EXTERNAL,
        ProductAction.FLAG_BLOCKED,
        ProductAction.FLAG_MANUAL_REVIEW,
        ProductAction.CANCEL,
        # BGĐ's comment is why the case was cancelled (QE-08, provisional).
        ProductAction.BOD_REJECT,
        # The signer's comment is why the case is back in item coding.
        ProductAction.SIGNOFF_REJECT,
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
    ProductAction.BOD_APPROVE: (
        ProductDevState.PENDING_BOD_REVIEW,
        ProductDevState.PROFILE_IN_PROGRESS,
    ),
    ProductAction.BOD_REJECT: (ProductDevState.PENDING_BOD_REVIEW, ProductDevState.CANCELLED),
    ProductAction.COMPLETE_PROFILE: (
        ProductDevState.PROFILE_IN_PROGRESS,
        ProductDevState.SUPPLIER_CONFIRMATION,
    ),
    ProductAction.CONFIRM_WITH_SUPPLIER: (
        ProductDevState.SUPPLIER_CONFIRMATION,
        ProductDevState.ITEM_CODING,
    ),
    ProductAction.SUBMIT_FOR_SIGNOFF: (
        ProductDevState.ITEM_CODING,
        ProductDevState.PENDING_SIGNOFF,
    ),
    ProductAction.SIGNOFF_APPROVE: (
        ProductDevState.PENDING_SIGNOFF,
        ProductDevState.READY_TO_ORDER,
    ),
    ProductAction.SIGNOFF_REJECT: (
        ProductDevState.PENDING_SIGNOFF,
        ProductDevState.ITEM_CODING,
    ),
    ProductAction.PLACE_ORDER: (ProductDevState.READY_TO_ORDER, ProductDevState.ORDERED),
}
_INTERRUPTS: dict[ProductAction, ProductDevState] = {
    ProductAction.WAIT_FOR_EXTERNAL: ProductDevState.WAITING_EXTERNAL,
    ProductAction.FLAG_BLOCKED: ProductDevState.BLOCKED,
    ProductAction.FLAG_MANUAL_REVIEW: ProductDevState.MANUAL_REVIEW,
}

# The document type a step takes. Every one but `reject_sample` (which may
# carry an evaluation) cannot be taken without its own.
ACTION_DOCUMENT_TYPE: dict[ProductAction, DocumentType] = {
    ProductAction.PASS_SAMPLE: DocumentType.SAMPLE_EVALUATION,
    ProductAction.REQUEST_REVISION: DocumentType.SAMPLE_REVISION_REQUEST,
    ProductAction.REJECT_SAMPLE: DocumentType.SAMPLE_EVALUATION,
    ProductAction.COMPLETE_PROFILE: DocumentType.PRODUCT_PROFILE_BM04,
    ProductAction.CONFIRM_WITH_SUPPLIER: DocumentType.SUPPLIER_CONFIRMATION_EMAIL,
}
DOCUMENT_REQUIRED_ACTIONS = frozenset(
    {
        ProductAction.PASS_SAMPLE,
        ProductAction.REQUEST_REVISION,
        ProductAction.COMPLETE_PROFILE,
        ProductAction.CONFIRM_WITH_SUPPLIER,
    }
)
# The steps whose paper belongs to the current sample round, counted from
# when the round opened; every other paper step's counts from when the case
# reached that step. The round steps record their paper on the round; the
# others on their history row (`ProductCaseStep.document_id`).
ROUND_DOCUMENT_ACTIONS = frozenset(
    {ProductAction.PASS_SAMPLE, ProductAction.REQUEST_REVISION, ProductAction.REJECT_SAMPLE}
)


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
    # The earliest upload the step accepts as its paper; None when it takes
    # none, or when the bound is not known yet (then no paper qualifies).
    documents_since: datetime | None = None
    # What the case still lacks for this step (`unmet_for`): the step is
    # offered, and refused until this is empty.
    unmet: tuple[str, ...] = ()


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
class ItemCode:
    """The case's official item code (Mã hàng, ADR 0018): unique in the
    tenant, which only the database can answer."""

    id: uuid.UUID
    code: str


@dataclass(frozen=True, slots=True)
class Sku:
    """One sellable variant under the item code, unique in the tenant by
    `sku_code`. `planned_quantity` is open (QE-11): None, or above zero."""

    id: uuid.UUID
    sku_code: str
    variant_label: str
    planned_quantity: int | None = None


@dataclass(frozen=True, slots=True)
class SkuDraft:
    """A SKU as a person sends it, before the step gives it an id."""

    sku_code: str
    variant_label: str
    planned_quantity: int | None = None


@dataclass(frozen=True, slots=True)
class ItemCodeIssued:
    """The step issued the item code, or replaced its text (same id)."""

    item_code: ItemCode
    replaces: bool


@dataclass(frozen=True, slots=True)
class SkuAdded:
    item_code_id: uuid.UUID
    sku: Sku


@dataclass(frozen=True, slots=True)
class SkuRemoved:
    sku_id: uuid.UUID


CodingChange = ItemCodeIssued | SkuAdded | SkuRemoved


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
    # The paper of a step outside the sample rounds (BM04, the supplier's
    # email), written on its history row; a round's paper is on the round.
    document_id: uuid.UUID | None = None
    # What a coding step (step 9) does to the item code or the SKUs, written
    # in the same transaction as its history row.
    coding: CodingChange | None = None


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
    when = (
        "của vòng mẫu hiện tại"
        if action in ROUND_DOCUMENT_ACTIONS
        else "tải lên từ khi hồ sơ tới bước này"
    )
    return ConflictError(
        f"{action.value} cần {expected.value} {when}, thuộc hồ sơ này",
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


def _quantity(value: int | None) -> int | None:
    if value is not None and value <= 0:
        raise DomainError(
            "planned_quantity must be above zero when given",
            details={"field": "planned_quantity"},
        )
    return value


def _reason(action: ProductAction, reason: str | None) -> str:
    if reason is None or not reason.strip():
        raise DomainError("this action requires a reason", details={"action": action.value})
    return reason


@dataclass(slots=True)
class ProductDevelopmentCase:
    """One product proposed at step 1, in one workspace.

    `round_opened_at` is when the CURRENT sample round's row was written, read
    back by the repository; None before the first sample and for a round
    opened in this instance and not yet saved.

    `stage_entered_at` is when the case reached its current state by a step,
    read back by the repository from the history; a resume returns the case
    to a step it already reached and does not count. None for a state reached
    in this instance and not yet saved."""

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
    stage_entered_at: datetime | None = None
    # Step 9: the item code and its SKUs, read back with the case; and how many
    # times it was submitted for sign-off, which names the sign-off a decision
    # belongs to (a rejected one is submitted again under the next number).
    item_code: ItemCode | None = None
    skus: tuple[Sku, ...] = ()
    signoff_round: int = 0
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
        if self.state in AWAITING_APPROVAL_STATES:
            return frozenset({ProductAction.CANCEL})
        forward = {
            action
            for action, (source, _) in _FORWARD.items()
            if source is self.state and action not in GRAPH_ONLY_ACTIONS
        }
        coding = CODING_ACTIONS if self.state is ProductDevState.ITEM_CODING else frozenset()
        return frozenset({*forward, *coding, *_INTERRUPTS, ProductAction.CANCEL})

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
                documents_since=self._documents_since(action),
                unmet=self.unmet_for(action),
            )
            for action in ProductAction
            if action in available
        ]

    def unmet_for(self, action: ProductAction) -> tuple[str, ...]:
        """What the case lacks for `action` (`item_code`, `sku`), or nothing:
        a SKU needs the item code, a submission needs both. The one answer the
        step refuses by and the page locks its control with."""
        missing: list[str] = []
        needs_code = {
            ProductAction.ADD_SKU,
            ProductAction.SUBMIT_FOR_SIGNOFF,
            ProductAction.PLACE_ORDER,
        }
        if action in needs_code and self.item_code is None:
            missing.append("item_code")
        needs_sku = {ProductAction.SUBMIT_FOR_SIGNOFF, ProductAction.PLACE_ORDER}
        if action in needs_sku and not self.skus:
            missing.append("sku")
        return tuple(missing)

    def _documents_since(self, action: ProductAction) -> datetime | None:
        if action not in ACTION_DOCUMENT_TYPE:
            return None
        return self.round_opened_at if action in ROUND_DOCUMENT_ACTIONS else self.stage_entered_at

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
        document_id: uuid.UUID | None = None,
        coding: CodingChange | None = None,
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
                document_id=document_id,
                coding=coding,
            )
        )
        self.state = target
        self.version += 1
        if action in _FORWARD:
            # Reached anew: when exactly is the database's, read back on load.
            # A pause and a resume return to a step already reached.
            self.stage_entered_at = None

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

    def _step_document(
        self, action: ProductAction, document: CaseDocument | None
    ) -> uuid.UUID | None:
        """The id of `document` if it is this case's paper for `action`,
        uploaded since the round opened or the case reached the step."""
        if document is None:
            if action in DOCUMENT_REQUIRED_ACTIONS:
                raise document_refusal(self.id.value, action)
            return None
        since = self._documents_since(action)
        belongs = (
            document.case_kind is CaseKind.PRODUCT
            and document.case_id == self.id.value
            and document.tenant_id == self.tenant_id.value
            and document.workspace_id == self.workspace_id.value
            and document.doc_type is ACTION_DOCUMENT_TYPE[action]
            and since is not None
            and document.uploaded_at >= since
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
        document_id = self._step_document(ProductAction.PASS_SAMPLE, evaluation)
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
        document_id = self._step_document(ProductAction.REQUEST_REVISION, revision_request)
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
        document_id = self._step_document(ProductAction.REJECT_SAMPLE, evaluation)
        self._move(
            ProductAction.REJECT_SAMPLE,
            target,
            actor_id=actor_id,
            reason=why,
            closes_round=RoundClosure(self.sample_round, SampleResult.REJECTED, document_id, None),
        )

    # -- step 6, applied by the review graph ---------------------------------------

    def bod_approve(self, *, actor_id: uuid.UUID) -> None:
        """BGĐ approved the sample: R&D completes the profile next. `actor_id`
        is the decider; their comment stays on the approval's decision row."""
        target = self._expect(ProductAction.BOD_APPROVE)
        self._move(ProductAction.BOD_APPROVE, target, actor_id=actor_id)

    def bod_reject(self, *, actor_id: uuid.UUID, reason: str | None) -> None:
        """BGĐ did not approve: the case is cancelled, BGĐ's comment its reason
        (QE-08). The round closed when the sample passed."""
        why = _reason(ProductAction.BOD_REJECT, reason)
        target = self._expect(ProductAction.BOD_REJECT)
        self._move(ProductAction.BOD_REJECT, target, actor_id=actor_id, reason=why)

    # -- steps 7-8 ---------------------------------------------------------------

    def complete_profile(self, *, actor_id: uuid.UUID, profile: CaseDocument | None) -> None:
        """Step 7: R&D completes the Profile SP (BM04) of this case."""
        target = self._expect(ProductAction.COMPLETE_PROFILE)
        document_id = self._step_document(ProductAction.COMPLETE_PROFILE, profile)
        self._move(
            ProductAction.COMPLETE_PROFILE, target, actor_id=actor_id, document_id=document_id
        )

    def confirm_with_supplier(
        self, *, actor_id: uuid.UUID, confirmation: CaseDocument | None
    ) -> None:
        """Step 8: TP Cung ứng confirms the product is agreed with the
        supplier, on the supplier's confirmation email uploaded to this case
        (QE-09: in the app, no mailbox read). Item coding comes next."""
        target = self._expect(ProductAction.CONFIRM_WITH_SUPPLIER)
        document_id = self._step_document(ProductAction.CONFIRM_WITH_SUPPLIER, confirmation)
        self._move(
            ProductAction.CONFIRM_WITH_SUPPLIER, target, actor_id=actor_id, document_id=document_id
        )

    # -- step 9: item code, SKUs, sign-off ---------------------------------------

    def _expect_coding(self, action: ProductAction) -> None:
        if self.state is not ProductDevState.ITEM_CODING:
            raise ConflictError(
                f"cannot {action.value} from {self.state.value} (expected item_coding)",
                details={
                    "case_id": str(self.id),
                    "current_state": self.state.value,
                    "expected_state": ProductDevState.ITEM_CODING.value,
                    "action": action.value,
                },
            )

    def issue_item_code(self, *, actor_id: uuid.UUID, new_id: uuid.UUID, code: str) -> None:
        """Step 9: Cung ứng issues the official item code, or corrects it
        before the case is submitted (the same row, so its SKUs stay under
        it). Whether the code is free in the tenant is the database's answer
        (ADR 0018); no format is checked until Elmich sends its rule (QE-11)."""
        self._expect_coding(ProductAction.ISSUE_ITEM_CODE)
        cleaned = _required_text(code, "item_code")
        current = self.item_code
        if current is not None and current.code == cleaned:
            raise DomainError("the case already has this item code", details={"item_code": cleaned})
        issued = ItemCode(id=current.id if current else new_id, code=cleaned)
        self._move(
            ProductAction.ISSUE_ITEM_CODE,
            self.state,
            actor_id=actor_id,
            coding=ItemCodeIssued(item_code=issued, replaces=current is not None),
        )
        self.item_code = issued

    def add_sku(self, *, actor_id: uuid.UUID, new_id: uuid.UUID, sku: SkuDraft) -> None:
        """Step 9: a SKU under the item code, never before it (ADR 0018; the
        NOT NULL foreign key holds the same rule for a row written directly)."""
        self._expect_coding(ProductAction.ADD_SKU)
        missing = self.unmet_for(ProductAction.ADD_SKU)
        if missing or self.item_code is None:
            raise ConflictError(
                "add_sku cần mã hàng chính thức trước",
                details={"case_id": str(self.id), "missing": ",".join(missing)},
            )
        added = Sku(
            id=new_id,
            sku_code=_required_text(sku.sku_code, "sku_code"),
            variant_label=_required_text(sku.variant_label, "variant_label"),
            planned_quantity=_quantity(sku.planned_quantity),
        )
        self._move(
            ProductAction.ADD_SKU,
            self.state,
            actor_id=actor_id,
            coding=SkuAdded(item_code_id=self.item_code.id, sku=added),
        )
        self.skus = (*self.skus, added)

    def remove_sku(self, *, actor_id: uuid.UUID, sku_id: uuid.UUID) -> None:
        """Step 9: a SKU taken off before the case is submitted."""
        self._expect_coding(ProductAction.REMOVE_SKU)
        if sku_id not in {s.id for s in self.skus}:
            raise NotFoundError("sku not found", details={"sku_id": str(sku_id)})
        self._move(
            ProductAction.REMOVE_SKU, self.state, actor_id=actor_id, coding=SkuRemoved(sku_id)
        )
        self.skus = tuple(s for s in self.skus if s.id != sku_id)

    def submit_for_signoff(self, *, actor_id: uuid.UUID) -> None:
        """Step 9: the coded product goes to sign-off, under a new sign-off
        round. The step command starts the sign-off run once this is saved."""
        target = self._expect(ProductAction.SUBMIT_FOR_SIGNOFF)
        missing = self.unmet_for(ProductAction.SUBMIT_FOR_SIGNOFF)
        if missing:
            raise ConflictError(
                "submit_for_signoff cần mã hàng chính thức và ít nhất một SKU",
                details={"case_id": str(self.id), "missing": ",".join(missing)},
            )
        self._move(ProductAction.SUBMIT_FOR_SIGNOFF, target, actor_id=actor_id)
        self.signoff_round += 1

    def signoff_approve(self, *, actor_id: uuid.UUID) -> None:
        """Every sign-off step approved: ready to order. `actor_id` decided
        the last step."""
        target = self._expect(ProductAction.SIGNOFF_APPROVE)
        self._move(ProductAction.SIGNOFF_APPROVE, target, actor_id=actor_id)

    def signoff_reject(self, *, actor_id: uuid.UUID, reason: str | None) -> None:
        """One sign-off step not approved: back to item coding, the signer's
        comment the reason. The item code and SKUs are kept."""
        why = _reason(ProductAction.SIGNOFF_REJECT, reason)
        target = self._expect(ProductAction.SIGNOFF_REJECT)
        self._move(ProductAction.SIGNOFF_REJECT, target, actor_id=actor_id, reason=why)

    # -- ĐẶT HÀNG ------------------------------------------------------------------

    def place_order(self, *, actor_id: uuid.UUID, po_case_id: POCaseId) -> POCase:
        """ĐẶT HÀNG: the signed product is ordered, and the PO case of steps
        10-17 opens awaiting its PO (ADR 0017). The PO case carries this case's
        PIC, Category and supplier as they are now, a stamp never looked up
        again, and one line per SKU with its planned quantity (open when the
        SKU has none; `create_po` sets it). The caller saves both together."""
        target = self._expect(ProductAction.PLACE_ORDER)
        missing = [*self.unmet_for(ProductAction.PLACE_ORDER)]
        if self.supplier_name is None:
            missing.append("supplier")
        if missing or self.supplier_name is None:
            raise ConflictError(
                "place_order cần mã hàng, SKU và NCC",
                details={"case_id": str(self.id), "missing": ",".join(missing)},
            )
        self._move(ProductAction.PLACE_ORDER, target, actor_id=actor_id)
        return POCase.requested(
            id=po_case_id,
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            supplier_name=self.supplier_name,
            product_dev_case_id=self.id.value,
            pic_user_id=self.pic_user_id,
            category=self.category,
            lines=tuple(
                POCaseLine(
                    sku_id=sku.id,
                    quantity=sku.planned_quantity,
                    sku_code=sku.sku_code,
                    variant_label=sku.variant_label,
                )
                for sku in self.skus
            ),
        )

    # -- interrupts, resume, cancel ----------------------------------------------

    def _interrupt(self, action: ProductAction, *, actor_id: uuid.UUID, reason: str | None) -> None:
        why = _reason(action, reason)
        # Nothing outside is awaited while an approval is decided (lead
        # decision 6): a pause there would leave the approval waiting on a
        # case that is not.
        if (
            self.state in PRODUCT_TERMINAL_STATES
            or self.state in PRODUCT_INTERRUPT_STATES
            or self.state in AWAITING_APPROVAL_STATES
        ):
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
    # Step 9's coding fields: the item code (`issue_item_code`), the SKU
    # (`add_sku`), the SKU to remove (`remove_sku`).
    item_code: str | None = None
    sku: SkuDraft | None = None
    sku_id: uuid.UUID | None = None
    # Minted by the step command, never sent by a person: the id of a row the
    # step creates (the item code, a SKU). Steps that create none ignore it.
    new_id: uuid.UUID | None = None


def _minted(given: ProductActionInput) -> uuid.UUID:
    if given.new_id is None:
        raise DomainError("this step creates a row and needs a minted id")
    return given.new_id


def _named_sku(given: ProductActionInput) -> uuid.UUID:
    if given.sku_id is None:
        raise DomainError("remove_sku names the SKU to remove", details={"field": "sku_id"})
    return given.sku_id


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
    ProductAction.BOD_APPROVE: lambda case, given: case.bod_approve(actor_id=given.actor_id),
    ProductAction.BOD_REJECT: lambda case, given: case.bod_reject(
        actor_id=given.actor_id, reason=given.reason
    ),
    ProductAction.COMPLETE_PROFILE: lambda case, given: case.complete_profile(
        actor_id=given.actor_id, profile=given.document
    ),
    ProductAction.CONFIRM_WITH_SUPPLIER: lambda case, given: case.confirm_with_supplier(
        actor_id=given.actor_id, confirmation=given.document
    ),
    ProductAction.ISSUE_ITEM_CODE: lambda case, given: case.issue_item_code(
        actor_id=given.actor_id, new_id=_minted(given), code=given.item_code or ""
    ),
    ProductAction.ADD_SKU: lambda case, given: case.add_sku(
        actor_id=given.actor_id,
        new_id=_minted(given),
        sku=given.sku or SkuDraft(sku_code="", variant_label=""),
    ),
    ProductAction.REMOVE_SKU: lambda case, given: case.remove_sku(
        actor_id=given.actor_id, sku_id=_named_sku(given)
    ),
    ProductAction.SUBMIT_FOR_SIGNOFF: lambda case, given: case.submit_for_signoff(
        actor_id=given.actor_id
    ),
    ProductAction.SIGNOFF_APPROVE: lambda case, given: case.signoff_approve(
        actor_id=given.actor_id
    ),
    ProductAction.SIGNOFF_REJECT: lambda case, given: case.signoff_reject(
        actor_id=given.actor_id, reason=given.reason
    ),
}


def apply_product_action(
    case: ProductDevelopmentCase, *, action: ProductAction, given: ProductActionInput
) -> None:
    """Dispatches one `ProductAction` to the method it names: the one owner of
    that mapping, as `apply_action` is for `POCase`, for the step command and
    the review graph alike. `propose` is not here: it opens a case rather
    than moving one. Who may ask for which action is the callers' to decide
    (`AdvanceProductCase` refuses `GRAPH_ONLY_ACTIONS`)."""
    if action is ProductAction.PROPOSE:
        raise DomainError(
            "propose opens a new case; it is not a step on one", details={"action": action.value}
        )
    if action in COMMAND_ONLY_ACTIONS:
        raise DomainError(
            f"{action.value} is taken through its own command, not as a plain step",
            details={"action": action.value},
        )
    if given.supplier_name is not None and action is not ProductAction.REQUEST_SAMPLE:
        raise DomainError(
            "only request_sample takes a supplier name", details={"action": action.value}
        )
    if given.reason is not None and action not in PRODUCT_REASON_REQUIRED_ACTIONS:
        raise DomainError("this action takes no reason", details={"action": action.value})
    if given.document is not None and action not in ACTION_DOCUMENT_TYPE:
        raise document_refusal(case.id.value, action)
    for name, value, takes in (
        ("item_code", given.item_code, ProductAction.ISSUE_ITEM_CODE),
        ("sku", given.sku, ProductAction.ADD_SKU),
        ("sku_id", given.sku_id, ProductAction.REMOVE_SKU),
    ):
        if value is not None and action is not takes:
            raise DomainError(f"only {takes.value} takes {name}", details={"action": action.value})
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
    # The paper of a step outside the rounds (BM04, the supplier's email).
    document_id: uuid.UUID | None = None


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
