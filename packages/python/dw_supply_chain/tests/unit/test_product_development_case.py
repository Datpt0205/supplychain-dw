"""Unit: the product-development case, steps 1-8 (stage-1 tickets 01-03, ADR 0016).

Every legal step, every step refused from the wrong state, a reason where one
is required, the sample round counting each revision, an interrupt returning
to where it paused, and the documents a step needs: a sample evaluation of the
CURRENT round for a pass, a revision request for a revision, each of this case.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from dw_kernel.errors import ConflictError, DomainError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_supply_chain.domain.case_document import (
    CaseDocument,
    CaseDocumentId,
    CaseKind,
    DocumentType,
)
from dw_supply_chain.domain.product_development_case import (
    ACTION_DOCUMENT_TYPE,
    DOCUMENT_REQUIRED_ACTIONS,
    GRAPH_ONLY_ACTIONS,
    PRODUCT_REASON_REQUIRED_ACTIONS,
    ProductAction,
    ProductActionInput,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
    RoundClosure,
    SampleResult,
    apply_product_action,
)

pytestmark = pytest.mark.unit

TENANT, WORKSPACE = uuid.uuid4(), uuid.uuid4()
PIC = uuid.uuid4()
RND = uuid.uuid4()
OPENED = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)


def _proposed(**overrides: object) -> ProductDevelopmentCase:
    fields: dict[str, object] = {
        "id": ProductDevelopmentCaseId(uuid.uuid4()),
        "tenant_id": TenantId(TENANT),
        "workspace_id": WorkspaceId(WORKSPACE),
        "proposal_code": "  DX-2026-001 ",
        "product_name": "Nồi inox 3 đáy 24cm",
        "category": " Nồi ",
        "actor_id": PIC,
    }
    fields.update(overrides)
    return ProductDevelopmentCase.propose(**fields)  # type: ignore[arg-type]


def _document(
    case: ProductDevelopmentCase,
    doc_type: DocumentType,
    *,
    uploaded_at: datetime | None = None,
    case_id: uuid.UUID | None = None,
    case_kind: CaseKind = CaseKind.PRODUCT,
) -> CaseDocument:
    document_id = uuid.uuid4()
    return CaseDocument(
        id=CaseDocumentId(document_id),
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        case_kind=case_kind,
        case_id=case_id or case.id.value,
        doc_type=doc_type,
        object_key=f"k/{document_id}",
        filename="bien-ban.pdf",
        content_type="application/pdf",
        size_bytes=10,
        sha256="0" * 64,
        version=1,
        uploaded_by=RND,
        uploaded_at=uploaded_at or OPENED + timedelta(hours=1),
    )


def _persisted(case: ProductDevelopmentCase) -> ProductDevelopmentCase:
    """What the repository hands back after a save: steps drained, and the
    current round's opening time read from its row."""
    case.pop_pending_steps()
    if case.sample_round:
        case.round_opened_at = OPENED
    return case


def _testing() -> ProductDevelopmentCase:
    case = _proposed()
    case.request_sample(actor_id=PIC, supplier_name="NCC Minh Long")
    case.receive_sample(actor_id=RND)
    return _persisted(case)


def assert_state(case: ProductDevelopmentCase, expected: ProductDevState) -> None:
    # Through a parameter, for the mypy narrowing reason test_po_case.py gives.
    assert case.state is expected


def assert_interrupted(case: ProductDevelopmentCase, expected: ProductDevState | None) -> None:
    assert case.interrupted_state is expected


# --- propose -------------------------------------------------------------------


def test_propose_stamps_the_pic_from_the_actor_and_records_the_first_step() -> None:
    case = _proposed()

    assert case.pic_user_id == PIC
    assert case.created_by == PIC
    assert_state(case, ProductDevState.PROPOSED)
    assert (case.proposal_code, case.category) == ("DX-2026-001", "Nồi")
    assert case.supplier_name is None
    assert case.sample_round == 0
    (step,) = case.pop_pending_steps()
    assert (step.action, step.from_state, step.to_state) == (
        ProductAction.PROPOSE,
        None,
        ProductDevState.PROPOSED,
    )
    assert step.actor_id == PIC


@pytest.mark.parametrize("field", ["proposal_code", "product_name", "category"])
def test_a_blank_required_field_is_refused(field: str) -> None:
    with pytest.raises(DomainError, match=field):
        _proposed(**{field: " \t "})


# --- the happy path and the revision loop ---------------------------------------


def test_steps_one_to_five_reach_pending_bod_review_and_count_rounds() -> None:
    case = _proposed()
    case.request_sample(actor_id=PIC, supplier_name=" NCC Minh Long ")
    assert case.supplier_name == "NCC Minh Long"
    case.receive_sample(actor_id=RND)
    assert (case.state, case.sample_round) == (ProductDevState.SAMPLE_TESTING, 1)
    _persisted(case)

    revision = _document(case, DocumentType.SAMPLE_REVISION_REQUEST)
    case.request_revision(actor_id=RND, reason="Tay cầm lỏng", revision_request=revision)
    assert_state(case, ProductDevState.REVISION_REQUESTED)
    (step,) = case.pop_pending_steps()
    assert step.closes_round == RoundClosure(
        round_no=1,
        result=SampleResult.NEEDS_REVISION,
        evaluation_document_id=None,
        revision_document_id=revision.id.value,
    )

    case.receive_revised_sample(actor_id=RND)
    assert (case.state, case.sample_round) == (ProductDevState.SAMPLE_TESTING, 2)
    (step,) = case.pop_pending_steps()
    assert step.opens_round == 2
    case.round_opened_at = OPENED + timedelta(days=3)

    evaluation = _document(
        case, DocumentType.SAMPLE_EVALUATION, uploaded_at=OPENED + timedelta(days=4)
    )
    case.pass_sample(actor_id=RND, evaluation=evaluation)
    assert_state(case, ProductDevState.PENDING_BOD_REVIEW)
    (step,) = case.pop_pending_steps()
    assert step.closes_round == RoundClosure(2, SampleResult.PASSED, evaluation.id.value, None)
    assert case.version == 1 + 5  # five steps after propose


def test_receiving_the_first_sample_opens_round_one() -> None:
    case = _proposed()
    case.request_sample(actor_id=PIC, supplier_name="NCC")
    case.pop_pending_steps()
    case.receive_sample(actor_id=RND)
    (step,) = case.pop_pending_steps()
    assert (step.opens_round, step.actor_id) == (1, RND)


def test_reject_closes_the_round_as_rejected_and_cancels() -> None:
    case = _testing()
    case.reject_sample(actor_id=RND, reason="Sai chất liệu")
    assert_state(case, ProductDevState.CANCELLED)
    (step,) = case.pop_pending_steps()
    assert step.reason == "Sai chất liệu"
    assert step.closes_round == RoundClosure(1, SampleResult.REJECTED, None, None)


def test_reject_may_carry_this_rounds_evaluation() -> None:
    case = _testing()
    evaluation = _document(case, DocumentType.SAMPLE_EVALUATION)
    case.reject_sample(actor_id=RND, reason="Sai chất liệu", evaluation=evaluation)
    (step,) = case.pop_pending_steps()
    assert step.closes_round is not None
    assert step.closes_round.evaluation_document_id == evaluation.id.value


# --- every step refused from the wrong state ------------------------------------

_FORWARD_FROM: dict[ProductAction, ProductDevState] = {
    ProductAction.REQUEST_SAMPLE: ProductDevState.PROPOSED,
    ProductAction.RECEIVE_SAMPLE: ProductDevState.SAMPLE_REQUESTED,
    ProductAction.PASS_SAMPLE: ProductDevState.SAMPLE_TESTING,
    ProductAction.REQUEST_REVISION: ProductDevState.SAMPLE_TESTING,
    ProductAction.RECEIVE_REVISED_SAMPLE: ProductDevState.REVISION_REQUESTED,
    ProductAction.REJECT_SAMPLE: ProductDevState.SAMPLE_TESTING,
    ProductAction.BOD_APPROVE: ProductDevState.PENDING_BOD_REVIEW,
    ProductAction.BOD_REJECT: ProductDevState.PENDING_BOD_REVIEW,
    ProductAction.COMPLETE_PROFILE: ProductDevState.PROFILE_IN_PROGRESS,
    ProductAction.CONFIRM_WITH_SUPPLIER: ProductDevState.SUPPLIER_CONFIRMATION,
}


def _in_state(state: ProductDevState) -> ProductDevelopmentCase:
    case = _proposed()
    case.pop_pending_steps()
    case.state = state
    case.sample_round = 1
    case.round_opened_at = OPENED
    case.stage_entered_at = OPENED
    if state in {
        ProductDevState.WAITING_EXTERNAL,
        ProductDevState.BLOCKED,
        ProductDevState.MANUAL_REVIEW,
    }:
        case.interrupted_state = ProductDevState.SAMPLE_TESTING
    return case


def _input(case: ProductDevelopmentCase, action: ProductAction) -> ProductActionInput:
    document_type = {
        ProductAction.PASS_SAMPLE: DocumentType.SAMPLE_EVALUATION,
        ProductAction.REQUEST_REVISION: DocumentType.SAMPLE_REVISION_REQUEST,
        ProductAction.COMPLETE_PROFILE: DocumentType.PRODUCT_PROFILE_BM04,
        ProductAction.CONFIRM_WITH_SUPPLIER: DocumentType.SUPPLIER_CONFIRMATION_EMAIL,
    }.get(action)
    return ProductActionInput(
        actor_id=RND,
        reason="lý do" if action in PRODUCT_REASON_REQUIRED_ACTIONS else None,
        supplier_name="NCC" if action is ProductAction.REQUEST_SAMPLE else None,
        document=_document(case, document_type) if document_type else None,
    )


@pytest.mark.parametrize(
    ("action", "state"),
    [
        (action, state)
        for action, legal in _FORWARD_FROM.items()
        for state in ProductDevState
        if state is not legal
    ],
)
def test_a_forward_step_from_any_other_state_is_refused(
    action: ProductAction, state: ProductDevState
) -> None:
    case = _in_state(state)
    with pytest.raises(ConflictError):
        apply_product_action(case, action=action, given=_input(case, action))
    assert_state(case, state)
    assert case.pop_pending_steps() == []


@pytest.mark.parametrize(("action", "state"), list(_FORWARD_FROM.items()))
def test_every_forward_step_is_legal_from_its_own_state(
    action: ProductAction, state: ProductDevState
) -> None:
    case = _in_state(state)
    apply_product_action(case, action=action, given=_input(case, action))
    (step,) = case.pop_pending_steps()
    assert (step.action, step.from_state) == (action, state)


@pytest.mark.parametrize("action", sorted(PRODUCT_REASON_REQUIRED_ACTIONS))
@pytest.mark.parametrize("reason", [None, "", "   "])
def test_an_action_that_needs_a_reason_is_refused_without_one(
    action: ProductAction, reason: str | None
) -> None:
    state = _FORWARD_FROM.get(action, ProductDevState.SAMPLE_TESTING)
    case = _in_state(state)
    given = _input(case, action)
    with pytest.raises(DomainError, match="reason"):
        apply_product_action(
            case,
            action=action,
            given=ProductActionInput(
                actor_id=given.actor_id,
                reason=reason,
                supplier_name=given.supplier_name,
                document=given.document,
            ),
        )
    assert_state(case, state)


def test_the_reason_required_set_is_the_tickets() -> None:
    assert {
        ProductAction.REQUEST_REVISION,
        ProductAction.REJECT_SAMPLE,
        ProductAction.WAIT_FOR_EXTERNAL,
        ProductAction.FLAG_BLOCKED,
        ProductAction.FLAG_MANUAL_REVIEW,
        ProductAction.CANCEL,
        # BGĐ's comment is the reason a rejected case was cancelled (QE-08).
        ProductAction.BOD_REJECT,
    } == PRODUCT_REASON_REQUIRED_ACTIONS


def test_request_sample_needs_a_supplier() -> None:
    case = _proposed()
    with pytest.raises(DomainError, match="supplier"):
        case.request_sample(actor_id=PIC, supplier_name="  ")
    assert_state(case, ProductDevState.PROPOSED)


def test_propose_is_not_an_action_on_an_existing_case() -> None:
    case = _proposed()
    with pytest.raises(DomainError):
        apply_product_action(case, action=ProductAction.PROPOSE, given=ProductActionInput(PIC))


@pytest.mark.parametrize(
    "given",
    [
        ProductActionInput(RND, supplier_name="NCC khác"),
        ProductActionInput(RND, document=None),
    ],
)
def test_receive_sample_takes_no_supplier(given: ProductActionInput) -> None:
    case = _in_state(ProductDevState.SAMPLE_REQUESTED)
    if given.supplier_name is None:
        apply_product_action(case, action=ProductAction.RECEIVE_SAMPLE, given=given)
        return
    with pytest.raises(DomainError, match="supplier"):
        apply_product_action(case, action=ProductAction.RECEIVE_SAMPLE, given=given)


@pytest.mark.parametrize(
    "action",
    sorted(set(ProductAction) - PRODUCT_REASON_REQUIRED_ACTIONS - {ProductAction.PROPOSE}),
)
def test_a_reason_on_a_step_that_takes_none_is_refused(action: ProductAction) -> None:
    """Nothing sent is silently dropped: a step that records no reason
    refuses one rather than accepting it and storing it nowhere."""
    state = ProductDevState.BLOCKED if action is ProductAction.RESUME else _FORWARD_FROM[action]
    case = _in_state(state)
    given = _input(case, action)
    with pytest.raises(DomainError, match="reason"):
        apply_product_action(
            case,
            action=action,
            given=ProductActionInput(
                given.actor_id,
                reason="ghi chú",
                supplier_name=given.supplier_name,
                document=given.document,
            ),
        )
    assert_state(case, state)


def test_an_action_without_a_document_type_refuses_a_document() -> None:
    case = _in_state(ProductDevState.SAMPLE_REQUESTED)
    with pytest.raises(DomainError, match="document"):
        apply_product_action(
            case,
            action=ProductAction.RECEIVE_SAMPLE,
            given=ProductActionInput(RND, document=_document(case, DocumentType.SAMPLE_PHOTO)),
        )


# --- interrupts -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "target"),
    [
        (ProductAction.WAIT_FOR_EXTERNAL, ProductDevState.WAITING_EXTERNAL),
        (ProductAction.FLAG_BLOCKED, ProductDevState.BLOCKED),
        (ProductAction.FLAG_MANUAL_REVIEW, ProductDevState.MANUAL_REVIEW),
    ],
)
@pytest.mark.parametrize(
    "paused_at",
    [
        ProductDevState.PROPOSED,
        ProductDevState.SAMPLE_REQUESTED,
        ProductDevState.SAMPLE_TESTING,
        ProductDevState.REVISION_REQUESTED,
        ProductDevState.PROFILE_IN_PROGRESS,
        ProductDevState.SUPPLIER_CONFIRMATION,
        ProductDevState.ITEM_CODING,
    ],
)
def test_an_interrupt_then_resume_returns_to_where_it_paused(
    action: ProductAction, target: ProductDevState, paused_at: ProductDevState
) -> None:
    case = _in_state(paused_at)
    apply_product_action(case, action=action, given=ProductActionInput(PIC, reason="chờ NCC"))
    assert_state(case, target)
    assert_interrupted(case, paused_at)

    apply_product_action(case, action=ProductAction.RESUME, given=ProductActionInput(PIC))
    assert_state(case, paused_at)
    assert_interrupted(case, None)
    steps = case.pop_pending_steps()
    assert [(s.from_state, s.to_state) for s in steps] == [
        (paused_at, target),
        (target, paused_at),
    ]


@pytest.mark.parametrize(
    "state",
    [
        ProductDevState.WAITING_EXTERNAL,
        ProductDevState.BLOCKED,
        ProductDevState.MANUAL_REVIEW,
        ProductDevState.CANCELLED,
    ],
)
def test_an_interrupted_or_cancelled_case_cannot_be_interrupted_again(
    state: ProductDevState,
) -> None:
    case = _in_state(state)
    with pytest.raises(ConflictError):
        case.flag_blocked(actor_id=PIC, reason="x")


@pytest.mark.parametrize(
    "action",
    [ProductAction.WAIT_FOR_EXTERNAL, ProductAction.FLAG_BLOCKED, ProductAction.FLAG_MANUAL_REVIEW],
)
def test_a_case_waiting_for_bgd_cannot_be_paused(action: ProductAction) -> None:
    """While BGĐ decides nothing outside is awaited (lead decision 6): the
    only step is cancel. Refused here, not only left off the page."""
    case = _in_state(ProductDevState.PENDING_BOD_REVIEW)
    with pytest.raises(ConflictError):
        apply_product_action(case, action=action, given=ProductActionInput(PIC, reason="chờ"))
    assert_state(case, ProductDevState.PENDING_BOD_REVIEW)
    assert_interrupted(case, None)
    assert case.pop_pending_steps() == []


def test_resume_is_refused_when_nothing_is_paused() -> None:
    case = _in_state(ProductDevState.SAMPLE_TESTING)
    with pytest.raises(ConflictError):
        case.resume(actor_id=PIC)


@pytest.mark.parametrize("paused_first", [False, True])
def test_cancel_while_a_sample_is_in_test_closes_its_round(paused_first: bool) -> None:
    """A cancelled case leaves no round reading 'Đang test' for good: the
    open round closes as rejected (Hủy), in the same step, by whoever
    cancelled, also when the case was paused in testing first."""
    case = _testing()
    if paused_first:
        case.flag_blocked(actor_id=PIC, reason="NCC chậm")
        case.pop_pending_steps()
    case.cancel(actor_id=PIC, reason="NCC ngừng hợp tác")
    (step,) = case.pop_pending_steps()
    assert step.closes_round == RoundClosure(1, SampleResult.REJECTED, None, None)
    assert step.actor_id == PIC


@pytest.mark.parametrize(
    "state",
    [
        ProductDevState.PROPOSED,
        ProductDevState.SAMPLE_REQUESTED,
        ProductDevState.REVISION_REQUESTED,
        ProductDevState.PENDING_BOD_REVIEW,
        ProductDevState.PROFILE_IN_PROGRESS,
        ProductDevState.SUPPLIER_CONFIRMATION,
        ProductDevState.ITEM_CODING,
        ProductDevState.WAITING_EXTERNAL,
    ],
)
def test_cancel_with_no_round_open_closes_none(state: ProductDevState) -> None:
    case = _in_state(state)
    if state is ProductDevState.WAITING_EXTERNAL:
        case.interrupted_state = ProductDevState.REVISION_REQUESTED
    case.cancel(actor_id=PIC, reason="x")
    (step,) = case.pop_pending_steps()
    assert step.closes_round is None


def test_cancel_from_an_interrupt_clears_it_and_a_cancelled_case_cannot_cancel() -> None:
    case = _in_state(ProductDevState.BLOCKED)
    case.cancel(actor_id=PIC, reason="NCC ngừng hợp tác")
    assert_state(case, ProductDevState.CANCELLED)
    assert case.interrupted_state is None
    with pytest.raises(ConflictError):
        case.cancel(actor_id=PIC, reason="lần hai")


# --- step 6: BGĐ's decision, applied by the graph --------------------------------

BOD = uuid.uuid4()


def test_bgd_approving_moves_the_case_to_the_profile_as_the_decider() -> None:
    case = _in_state(ProductDevState.PENDING_BOD_REVIEW)
    apply_product_action(case, action=ProductAction.BOD_APPROVE, given=ProductActionInput(BOD))
    assert_state(case, ProductDevState.PROFILE_IN_PROGRESS)
    (step,) = case.pop_pending_steps()
    assert (step.action, step.from_state, step.to_state) == (
        ProductAction.BOD_APPROVE,
        ProductDevState.PENDING_BOD_REVIEW,
        ProductDevState.PROFILE_IN_PROGRESS,
    )
    assert (step.actor_id, step.reason, step.closes_round) == (BOD, None, None)


def test_bgd_rejecting_cancels_the_case_with_bgds_comment_as_the_reason() -> None:
    case = _in_state(ProductDevState.PENDING_BOD_REVIEW)
    apply_product_action(
        case,
        action=ProductAction.BOD_REJECT,
        given=ProductActionInput(BOD, reason="Giá vốn quá cao so với mục tiêu"),
    )
    assert_state(case, ProductDevState.CANCELLED)
    (step,) = case.pop_pending_steps()
    assert (step.action, step.actor_id, step.reason) == (
        ProductAction.BOD_REJECT,
        BOD,
        "Giá vốn quá cao so với mục tiêu",
    )
    # The round closed when the sample passed; a rejection closes nothing.
    assert step.closes_round is None


# --- documents: this case, this type, this round --------------------------------


def test_pass_without_an_evaluation_names_the_missing_document_type() -> None:
    case = _testing()
    with pytest.raises(ConflictError) as raised:
        case.pass_sample(actor_id=RND, evaluation=None)
    assert raised.value.details["missing_document_type"] == "sample_evaluation"
    assert_state(case, ProductDevState.SAMPLE_TESTING)


@pytest.mark.parametrize(
    "wrong",
    [
        "another_case",
        "a_po_case",
        "wrong_type",
        "before_the_round_opened",
    ],
)
def test_pass_refuses_a_document_that_is_not_this_rounds_evaluation(wrong: str) -> None:
    case = _testing()
    document = {
        "another_case": lambda: _document(
            case, DocumentType.SAMPLE_EVALUATION, case_id=uuid.uuid4()
        ),
        "a_po_case": lambda: _document(case, DocumentType.SAMPLE_EVALUATION, case_kind=CaseKind.PO),
        "wrong_type": lambda: _document(case, DocumentType.SAMPLE_PHOTO),
        "before_the_round_opened": lambda: _document(
            case, DocumentType.SAMPLE_EVALUATION, uploaded_at=OPENED - timedelta(seconds=1)
        ),
    }[wrong]()
    with pytest.raises(ConflictError) as raised:
        case.pass_sample(actor_id=RND, evaluation=document)
    assert raised.value.details["missing_document_type"] == "sample_evaluation"
    assert_state(case, ProductDevState.SAMPLE_TESTING)


def test_a_round_one_report_cannot_pass_round_two() -> None:
    case = _testing()
    round_one_report = _document(case, DocumentType.SAMPLE_EVALUATION)
    case.request_revision(
        actor_id=RND,
        reason="Tay cầm lỏng",
        revision_request=_document(case, DocumentType.SAMPLE_REVISION_REQUEST),
    )
    case.receive_revised_sample(actor_id=RND)
    case.pop_pending_steps()
    case.round_opened_at = round_one_report.uploaded_at + timedelta(days=2)

    with pytest.raises(ConflictError, match="sample_evaluation"):
        case.pass_sample(actor_id=RND, evaluation=round_one_report)


def test_a_round_opened_in_this_instance_and_not_yet_saved_accepts_no_document() -> None:
    """Its opening time is the database's; until it is read back, no document
    can be shown to have come after it, so none is accepted."""
    case = _proposed()
    case.request_sample(actor_id=PIC, supplier_name="NCC")
    case.receive_sample(actor_id=RND)
    with pytest.raises(ConflictError):
        case.pass_sample(actor_id=RND, evaluation=_document(case, DocumentType.SAMPLE_EVALUATION))


def test_a_revision_needs_this_cases_revision_request() -> None:
    case = _testing()
    with pytest.raises(ConflictError) as raised:
        case.request_revision(actor_id=RND, reason="x", revision_request=None)
    assert raised.value.details["missing_document_type"] == "sample_revision_request"
    with pytest.raises(ConflictError):
        case.request_revision(
            actor_id=RND,
            reason="x",
            revision_request=_document(case, DocumentType.SAMPLE_EVALUATION),
        )


def test_reject_refuses_an_evaluation_from_another_case() -> None:
    case = _testing()
    with pytest.raises(ConflictError):
        case.reject_sample(
            actor_id=RND,
            reason="x",
            evaluation=_document(case, DocumentType.SAMPLE_EVALUATION, case_id=uuid.uuid4()),
        )


# --- steps 7-8: BM04 and the supplier's confirmation, each on its own paper --------

R_AND_D = uuid.uuid4()
SUPPLY_LEAD = uuid.uuid4()
_STAGE_PAPER = {
    ProductAction.COMPLETE_PROFILE: (
        ProductDevState.PROFILE_IN_PROGRESS,
        DocumentType.PRODUCT_PROFILE_BM04,
        ProductDevState.SUPPLIER_CONFIRMATION,
    ),
    ProductAction.CONFIRM_WITH_SUPPLIER: (
        ProductDevState.SUPPLIER_CONFIRMATION,
        DocumentType.SUPPLIER_CONFIRMATION_EMAIL,
        ProductDevState.ITEM_CODING,
    ),
}


@pytest.mark.parametrize("action", list(_STAGE_PAPER))
def test_steps_seven_and_eight_move_on_and_record_their_paper(action: ProductAction) -> None:
    source, doc_type, target = _STAGE_PAPER[action]
    case = _in_state(source)
    paper = _document(case, doc_type)

    apply_product_action(case, action=action, given=ProductActionInput(R_AND_D, document=paper))

    assert_state(case, target)
    (step,) = case.pop_pending_steps()
    assert (step.action, step.from_state, step.to_state, step.actor_id) == (
        action,
        source,
        target,
        R_AND_D,
    )
    # The paper sits on the history row; no sample round is touched.
    assert step.document_id == paper.id.value
    assert (step.opens_round, step.closes_round, step.reason) == (None, None, None)


def test_bm04_then_the_suppliers_email_reach_item_coding() -> None:
    case = _in_state(ProductDevState.PROFILE_IN_PROGRESS)
    case.complete_profile(
        actor_id=R_AND_D, profile=_document(case, DocumentType.PRODUCT_PROFILE_BM04)
    )
    case.pop_pending_steps()
    # Read back: the case reached supplier_confirmation when that step was saved.
    case.stage_entered_at = OPENED + timedelta(days=1)
    case.confirm_with_supplier(
        actor_id=SUPPLY_LEAD,
        confirmation=_document(
            case,
            DocumentType.SUPPLIER_CONFIRMATION_EMAIL,
            uploaded_at=OPENED + timedelta(days=2),
        ),
    )
    assert_state(case, ProductDevState.ITEM_CODING)


@pytest.mark.parametrize("action", list(_STAGE_PAPER))
def test_steps_seven_and_eight_without_their_paper_name_the_missing_type(
    action: ProductAction,
) -> None:
    source, doc_type, _ = _STAGE_PAPER[action]
    case = _in_state(source)
    with pytest.raises(ConflictError) as raised:
        apply_product_action(case, action=action, given=ProductActionInput(R_AND_D))
    assert raised.value.details["missing_document_type"] == doc_type.value
    assert_state(case, source)
    assert case.pop_pending_steps() == []


@pytest.mark.parametrize("action", list(_STAGE_PAPER))
@pytest.mark.parametrize(
    "wrong",
    [
        "another_case",
        "a_po_case",
        "wrong_type",
        "another_tenant",
        "another_workspace",
        "before_the_case_reached_this_step",
    ],
)
def test_steps_seven_and_eight_refuse_paper_that_is_not_this_steps(
    action: ProductAction, wrong: str
) -> None:
    source, doc_type, _ = _STAGE_PAPER[action]
    case = _in_state(source)
    other_type = (
        DocumentType.SUPPLIER_CONFIRMATION_EMAIL
        if doc_type is DocumentType.PRODUCT_PROFILE_BM04
        else DocumentType.PRODUCT_PROFILE_BM04
    )
    paper = {
        "another_case": lambda: _document(case, doc_type, case_id=uuid.uuid4()),
        "a_po_case": lambda: _document(case, doc_type, case_kind=CaseKind.PO),
        "wrong_type": lambda: _document(case, other_type),
        "another_tenant": lambda: replace(_document(case, doc_type), tenant_id=uuid.uuid4()),
        "another_workspace": lambda: replace(_document(case, doc_type), workspace_id=uuid.uuid4()),
        "before_the_case_reached_this_step": lambda: _document(
            case, doc_type, uploaded_at=OPENED - timedelta(seconds=1)
        ),
    }[wrong]()
    with pytest.raises(ConflictError) as raised:
        apply_product_action(case, action=action, given=ProductActionInput(R_AND_D, document=paper))
    assert raised.value.details["missing_document_type"] == doc_type.value
    assert_state(case, source)


@pytest.mark.parametrize("action", list(_STAGE_PAPER))
def test_a_step_reached_in_this_instance_and_not_yet_saved_accepts_no_paper(
    action: ProductAction,
) -> None:
    """When the case reached the step is the database's; until it is read
    back no paper can be shown to have come after it, so none is accepted."""
    source, doc_type, _ = _STAGE_PAPER[action]
    case = _in_state(source)
    case.stage_entered_at = None
    with pytest.raises(ConflictError):
        apply_product_action(
            case,
            action=action,
            given=ProductActionInput(R_AND_D, document=_document(case, doc_type)),
        )


def test_a_step_reached_by_a_step_in_this_instance_forgets_the_old_bound() -> None:
    """Completing the BM04 in memory reaches step 8 anew: the bound step 7
    was read with must not admit an email uploaded before step 8 was saved."""
    case = _in_state(ProductDevState.PROFILE_IN_PROGRESS)
    case.complete_profile(
        actor_id=R_AND_D, profile=_document(case, DocumentType.PRODUCT_PROFILE_BM04)
    )
    assert case.stage_entered_at is None
    with pytest.raises(ConflictError):
        case.confirm_with_supplier(
            actor_id=SUPPLY_LEAD,
            confirmation=_document(case, DocumentType.SUPPLIER_CONFIRMATION_EMAIL),
        )


def test_the_paper_steps_and_their_types_are_the_tickets() -> None:
    assert ACTION_DOCUMENT_TYPE == {
        ProductAction.PASS_SAMPLE: DocumentType.SAMPLE_EVALUATION,
        ProductAction.REQUEST_REVISION: DocumentType.SAMPLE_REVISION_REQUEST,
        ProductAction.REJECT_SAMPLE: DocumentType.SAMPLE_EVALUATION,
        ProductAction.COMPLETE_PROFILE: DocumentType.PRODUCT_PROFILE_BM04,
        ProductAction.CONFIRM_WITH_SUPPLIER: DocumentType.SUPPLIER_CONFIRMATION_EMAIL,
    }
    assert {
        ProductAction.PASS_SAMPLE,
        ProductAction.REQUEST_REVISION,
        ProductAction.COMPLETE_PROFILE,
        ProductAction.CONFIRM_WITH_SUPPLIER,
    } == DOCUMENT_REQUIRED_ACTIONS


def test_each_paper_option_says_since_when_its_paper_counts() -> None:
    """The page offers papers from the bound the step will check, read from
    the case: a round's paper since the round opened, a stage's since the
    case reached that step. One owner; the page holds no copy of the rule."""
    testing = _in_state(ProductDevState.SAMPLE_TESTING)
    testing.round_opened_at = OPENED + timedelta(hours=3)
    testing.stage_entered_at = OPENED
    since = {o.action: o.documents_since for o in testing.action_options()}
    assert since[ProductAction.PASS_SAMPLE] == OPENED + timedelta(hours=3)
    assert since[ProductAction.CANCEL] is None

    for action, (source, doc_type, _) in _STAGE_PAPER.items():
        case = _in_state(source)
        case.stage_entered_at = OPENED + timedelta(days=4)
        (option,) = [o for o in case.action_options() if o.action is action]
        assert (option.document_type, option.document_required, option.documents_since) == (
            doc_type,
            True,
            OPENED + timedelta(days=4),
        )


def test_pausing_at_a_step_does_not_move_the_bound_its_paper_counts_from() -> None:
    """A resume returns the case to a step it already reached: a BM04
    uploaded before the pause still counts. The bound is read back by the
    repository; the domain never resets it on a resume."""
    case = _in_state(ProductDevState.PROFILE_IN_PROGRESS)
    paper = _document(case, DocumentType.PRODUCT_PROFILE_BM04)
    case.flag_blocked(actor_id=PIC, reason="Chờ thông số NCC")
    case.resume(actor_id=PIC)
    assert case.stage_entered_at == OPENED
    case.complete_profile(actor_id=R_AND_D, profile=paper)
    assert_state(case, ProductDevState.SUPPLIER_CONFIRMATION)


# --- what a person may do next ---------------------------------------------------


@pytest.mark.parametrize(
    ("state", "forward"),
    [
        (ProductDevState.PROPOSED, {ProductAction.REQUEST_SAMPLE}),
        (ProductDevState.SAMPLE_REQUESTED, {ProductAction.RECEIVE_SAMPLE}),
        (
            ProductDevState.SAMPLE_TESTING,
            {
                ProductAction.PASS_SAMPLE,
                ProductAction.REQUEST_REVISION,
                ProductAction.REJECT_SAMPLE,
            },
        ),
        (ProductDevState.REVISION_REQUESTED, {ProductAction.RECEIVE_REVISED_SAMPLE}),
        (ProductDevState.PROFILE_IN_PROGRESS, {ProductAction.COMPLETE_PROFILE}),
        (ProductDevState.SUPPLIER_CONFIRMATION, {ProductAction.CONFIRM_WITH_SUPPLIER}),
        # Step 9 (item code, SKU, sign-off) is S4's.
        (ProductDevState.ITEM_CODING, set()),
    ],
)
def test_available_actions_are_the_states_own_steps_plus_the_exceptions(
    state: ProductDevState, forward: set[ProductAction]
) -> None:
    exceptions = {
        ProductAction.WAIT_FOR_EXTERNAL,
        ProductAction.FLAG_BLOCKED,
        ProductAction.FLAG_MANUAL_REVIEW,
        ProductAction.CANCEL,
    }
    assert _in_state(state).available_actions() == frozenset(forward | exceptions)


def test_a_case_waiting_for_bgd_offers_only_cancel() -> None:
    """BGĐ's two outcomes are the graph's to apply, never a person's button,
    and nothing outside is awaited while BGĐ decides (lead decision 6)."""
    assert _in_state(ProductDevState.PENDING_BOD_REVIEW).available_actions() == {
        ProductAction.CANCEL
    }


@pytest.mark.parametrize("state", list(ProductDevState))
def test_no_state_offers_a_graph_only_action(state: ProductDevState) -> None:
    assert not _in_state(state).available_actions() & GRAPH_ONLY_ACTIONS


def test_the_graph_only_actions_are_bgds_two_outcomes() -> None:
    assert {ProductAction.BOD_APPROVE, ProductAction.BOD_REJECT} == GRAPH_ONLY_ACTIONS


def test_available_actions_of_a_paused_and_a_cancelled_case() -> None:
    assert _in_state(ProductDevState.BLOCKED).available_actions() == {
        ProductAction.RESUME,
        ProductAction.CANCEL,
    }
    assert _in_state(ProductDevState.CANCELLED).available_actions() == frozenset()


@pytest.mark.parametrize("state", list(ProductDevState))
def test_every_available_action_is_one_the_case_accepts(state: ProductDevState) -> None:
    """The list a page draws buttons from and the guards agree, from every
    state: a button offered is a step the aggregate takes."""
    for action in _in_state(state).available_actions():
        case = _in_state(state)
        apply_product_action(case, action=action, given=_input(case, action))
