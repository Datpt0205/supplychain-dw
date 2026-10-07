"""HTTP surface of product-development cases (stage-1 ticket 01). Decisions
live in the handlers (`application.product_cases`).

A router of its own, mounted by the composition root on its own guard, as
the documents router is, rather than seven more arguments to the Supply Chain
router. The product documents themselves are served by the documents router
(`/product-cases/{case_id}/documents`), the same handlers as a PO case's.

The propose body has no PIC field and refuses unknown fields
(`extra="forbid"`), so a body naming a `pic_user_id` is a 422; the command
behind it has no parameter for one either. A step body naming one of BGĐ's
outcomes, or of the sign-off's (`GRAPH_ONLY_ACTIONS`), is a 422 too, and the
command refuses it again: they are decided on the approval, at `/approvals`.

Step 9's coding goes through the same step route: `item_code` for
`issue_item_code`, `sku` for `add_sku`, `sku_id` for `remove_sku`. A code
already taken in the tenant is a 409 whose `details` name the constraint and
the code, so the page can show it at the field.

ĐẶT HÀNG has its own route, `POST /product-cases/{case_id}/order`, taking
nothing: the PO case takes the PIC, Category and supplier from the case, never
from the request (a body naming any field is a 422). The step route refuses
`place_order` (422).

No `from __future__ import annotations`, for the reason `routes.py` gives.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Protocol, TypeVar

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from dw_kernel.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.application.handlers import (
    GetProductActionDuties,
    ListProductCategories,
    SetProductActionDutiesOverride,
    duty_scope,
)
from dw_supply_chain.application.ports import (
    PendingApprovalRecord,
    ProductCaseListFilter,
    ReviewRaise,
)
from dw_supply_chain.application.product_cases import (
    AdvanceProductCase,
    GetProductCase,
    ListProductCases,
    ListProductCaseTransitions,
    PlaceOrder,
    ProposeProductCase,
    ReassignProductCasePic,
)
from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.product_development_case import (
    COMMAND_ONLY_ACTIONS,
    GRAPH_ONLY_ACTIONS,
    ProductAction,
    ProductCaseTransition,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
    SampleResult,
    SampleRound,
    SkuDraft,
)
from dw_supply_chain.presentation.routes import (
    ReassignPicRequest,
    SLAEvaluationView,
    sla_evaluation_view,
)
from dw_supply_chain.product_action_duties import SupplyChainProductActionDuties
from dw_supply_chain.sla_policy import ProductCategory

AccessContextResolver = Callable[..., Awaitable[AccessContext]]
IdempotencyResolver = Callable[..., object]

_ResultT = TypeVar("_ResultT", bound=BaseModel)


class SupportsIdempotentRecord(Protocol):
    """What a route needs from `dw_api`'s `IdempotentOperation`."""

    async def record(self, result: _ResultT, *, status_code: int = 200) -> _ResultT: ...


# Free text that reaches PostgreSQL carries no NUL (see `routes.py`).
_NO_NUL = r"^[^\x00]*$"


class ProposeProductCaseRequest(BaseModel):
    """Step 1. JSON only: product images are uploaded after the case exists,
    through its documents (doc_type `product_image`). `category` is a key of
    the tenant's list (`GET /product-categories`); the handler refuses any
    other."""

    model_config = ConfigDict(extra="forbid")

    proposal_code: str = Field(min_length=1, max_length=100, pattern=_NO_NUL)
    product_name: str = Field(min_length=1, max_length=300, pattern=_NO_NUL)
    category: str = Field(min_length=1, max_length=100, pattern=_NO_NUL)


class SkuInput(BaseModel):
    """A SKU to add under the item code (step 9). `planned_quantity` is open
    (QE-11): omitted, or above zero."""

    model_config = ConfigDict(extra="forbid")

    sku_code: str = Field(min_length=1, max_length=100, pattern=_NO_NUL)
    variant_label: str = Field(min_length=1, max_length=200, pattern=_NO_NUL)
    planned_quantity: int | None = Field(default=None, gt=0, le=10_000_000)


class AdvanceProductCaseRequest(BaseModel):
    """One step. Each step reads the fields it takes and refuses the others:
    `supplier_name` for `request_sample`, `document_id` for `pass_sample`,
    `request_revision`, `complete_profile`, `confirm_with_supplier` and
    (optionally) `reject_sample`, `item_code` for `issue_item_code`, `sku` for
    `add_sku`, `sku_id` for `remove_sku`, `reason` where the step needs one."""

    model_config = ConfigDict(extra="forbid")

    action: ProductAction
    reason: str | None = Field(default=None, max_length=2000, pattern=_NO_NUL)
    supplier_name: str | None = Field(default=None, min_length=1, max_length=200, pattern=_NO_NUL)
    document_id: uuid.UUID | None = None
    item_code: str | None = Field(default=None, min_length=1, max_length=100, pattern=_NO_NUL)
    sku: SkuInput | None = None
    sku_id: uuid.UUID | None = None

    @field_validator("action")
    @classmethod
    def _a_step_a_person_takes(cls, action: ProductAction) -> ProductAction:
        if action in GRAPH_ONLY_ACTIONS:
            raise ValueError(f"{action.value} is an approval's outcome, decided on the approval")
        if action in COMMAND_ONLY_ACTIONS:
            raise ValueError(f"{action.value} is taken at /product-cases/{{case_id}}/order")
        return action


class PlaceOrderRequest(BaseModel):
    """ĐẶT HÀNG takes nothing: the PO case takes the PIC, Category, supplier
    and SKUs from the case. A body may be sent empty or not at all; any field
    in it (a PIC, a Category, a supplier) is a 422, never silently dropped."""

    model_config = ConfigDict(extra="forbid")


class ProductCaseView(BaseModel):
    id: uuid.UUID
    proposal_code: str
    product_name: str
    category: str
    supplier_name: str | None
    pic_user_id: uuid.UUID
    state: ProductDevState
    interrupted_state: ProductDevState | None
    sample_round: int
    signoff_round: int
    created_by: uuid.UUID
    created_at: datetime | None
    version: int


class ItemCodeView(BaseModel):
    """The case's official item code (step 9)."""

    id: uuid.UUID
    code: str


class SkuView(BaseModel):
    id: uuid.UUID
    sku_code: str
    variant_label: str
    planned_quantity: int | None


class SampleRoundView(BaseModel):
    round_no: int
    opened_at: datetime
    opened_by: uuid.UUID
    result: SampleResult | None
    evaluation_document_id: uuid.UUID | None
    closed_at: datetime | None
    closed_by: uuid.UUID | None
    revision_document_id: uuid.UUID | None
    requested_changes: str | None


class ProductActionOptionView(BaseModel):
    """A step the case accepts from its state, what it must carry, and the
    scope its duty needs under the tenant's policy. The page draws its button
    and form from this; the server checks all of it again on the step.
    `documents_since` is the earliest upload the step takes as its paper
    (when the round opened, or when the case reached the step); null when it
    takes none or the bound is not known, and then no paper qualifies."""

    action: ProductAction
    required_scope: str
    reason_required: bool
    takes_supplier: bool
    document_type: DocumentType | None
    document_required: bool
    documents_since: datetime | None
    # What the case still lacks for this step (`item_code`, `sku`): offered,
    # and refused until empty. The domain's own answer, not the page's.
    unmet: list[str]


class SignoffStepView(BaseModel):
    step: str
    label: str


class PendingReviewView(BaseModel):
    """The approval a waiting case is held on (BGĐ's review, or the current
    sign-off step): which approval, since when, and the scope stamped on it,
    which is who may decide it. For a sign-off, the step it is, its number,
    and every step of the round in order, all as stamped when the case was
    submitted. Decided at `/approvals`, never from the case."""

    approval_id: uuid.UUID
    created_at: datetime | None
    required_scope: str | None
    step: str | None
    step_label: str | None
    step_no: int | None
    steps: list[SignoffStepView]


class ProductCategoryView(BaseModel):
    """One Category of the tenant's list: the key a case is stamped with, the
    label a person reads."""

    key: str
    label: str


class ProductCaseDetailView(ProductCaseView):
    # The current step's SLA under the case's Category (ticket 06).
    sla: SLAEvaluationView
    rounds: list[SampleRoundView]
    actions: list[ProductActionOptionView]
    pending_review: PendingReviewView | None
    item_code: ItemCodeView | None
    skus: list[SkuView]
    # The PO case ĐẶT HÀNG opened, once the case is ordered.
    po_case_id: uuid.UUID | None


class OrderPlacedView(ProductCaseView):
    """ĐẶT HÀNG done: the case, now ordered, and the PO case it opened."""

    po_case_id: uuid.UUID


class ProductCaseStepView(ProductCaseView):
    """A step taken. `review` is set only for a step that left the case
    waiting on an approval (BGĐ's review, the sign-off): `not_raised` means
    the step is recorded and the approval is not raised yet (the worker
    retries it)."""

    review: ReviewRaise | None


class ProductCaseTransitionView(BaseModel):
    """One history row. `document_id` is the paper of a step outside the
    sample rounds (BM04, the supplier's email); a round's is on the round."""

    action: ProductAction
    from_state: ProductDevState | None
    to_state: ProductDevState
    reason: str | None
    actor_id: uuid.UUID
    occurred_at: datetime
    document_id: uuid.UUID | None


def _fields(case: ProductDevelopmentCase) -> dict[str, object]:
    return {
        "id": case.id.value,
        "proposal_code": case.proposal_code,
        "product_name": case.product_name,
        "category": case.category,
        "supplier_name": case.supplier_name,
        "pic_user_id": case.pic_user_id,
        "state": case.state,
        "interrupted_state": case.interrupted_state,
        "sample_round": case.sample_round,
        "signoff_round": case.signoff_round,
        "created_by": case.created_by,
        "created_at": case.created_at,
        "version": case.version,
    }


def _view(case: ProductDevelopmentCase) -> ProductCaseView:
    return ProductCaseView.model_validate(_fields(case))


def _round_view(sample_round: SampleRound) -> SampleRoundView:
    return SampleRoundView(
        round_no=sample_round.round_no,
        opened_at=sample_round.opened_at,
        opened_by=sample_round.opened_by,
        result=sample_round.result,
        evaluation_document_id=sample_round.evaluation_document_id,
        closed_at=sample_round.closed_at,
        closed_by=sample_round.closed_by,
        revision_document_id=sample_round.revision_document_id,
        requested_changes=sample_round.requested_changes,
    )


def _pending_review_view(approval: PendingApprovalRecord | None) -> PendingReviewView | None:
    if approval is None:
        return None
    payload = approval.payload
    steps = payload.get("steps")
    step_no = payload.get("step_no")
    return PendingReviewView(
        approval_id=approval.id,
        created_at=approval.created_at,
        required_scope=approval.required_scope,
        step=_text(payload.get("step")),
        step_label=_text(payload.get("step_label")),
        step_no=step_no if isinstance(step_no, int) else None,
        steps=[
            SignoffStepView(step=str(s["step"]), label=str(s["label"]))
            for s in (steps if isinstance(steps, list) else [])
            if isinstance(s, dict) and "step" in s and "label" in s
        ],
    )


def _category_view(category: ProductCategory) -> ProductCategoryView:
    return ProductCategoryView(key=category.key, label=category.label)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _transition_view(transition: ProductCaseTransition) -> ProductCaseTransitionView:
    return ProductCaseTransitionView(
        action=transition.action,
        from_state=transition.from_state,
        to_state=transition.to_state,
        reason=transition.reason,
        actor_id=transition.actor_id,
        occurred_at=transition.occurred_at,
        document_id=transition.document_id,
    )


def build_product_cases_router(
    propose: ProposeProductCase,
    get: GetProductCase,
    list_cases: ListProductCases,
    advance: AdvanceProductCase,
    list_transitions: ListProductCaseTransitions,
    get_duties: GetProductActionDuties,
    set_duties: SetProductActionDutiesOverride,
    *,
    place_order: PlaceOrder,
    reassign_pic: ReassignProductCasePic,
    list_categories: ListProductCategories,
    resolve_access_context: AccessContextResolver,
    resolve_idempotency: IdempotencyResolver,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/supply-chain", tags=["supply_chain"])
    require_access_context = Annotated[AccessContext, Depends(resolve_access_context)]
    require_idempotency = Annotated[SupportsIdempotentRecord, Depends(resolve_idempotency)]

    @router.post("/product-cases", response_model=ProductCaseView)
    async def propose_product_case(
        body: ProposeProductCaseRequest,
        context: require_access_context,
        idempotency: require_idempotency,
    ) -> ProductCaseView:
        """Step 1. The caller becomes the PIC."""
        case = await propose.handle(
            context,
            proposal_code=body.proposal_code,
            product_name=body.product_name,
            category=body.category,
        )
        return await idempotency.record(_view(case))

    @router.get("/product-categories", response_model=list[ProductCategoryView])
    async def list_product_categories(
        context: require_access_context,
    ) -> list[ProductCategoryView]:
        """The caller's tenant's Category list, in its own order."""
        return [_category_view(c) for c in await list_categories.handle(context)]

    @router.post("/product-cases/{case_id}/pic", response_model=ProductCaseView)
    async def reassign_product_case_pic(
        case_id: uuid.UUID,
        body: ReassignPicRequest,
        context: require_access_context,
        idempotency: require_idempotency,
    ) -> ProductCaseView:
        """Hands the case to another PIC (TP Cung ứng's duty, ticket 06). A
        case ordered or cancelled refuses (409): its PIC lives on the PO case."""
        case = await reassign_pic.handle(
            context,
            case_id=ProductDevelopmentCaseId(case_id),
            new_pic=body.pic_user_id,
            reason=body.reason,
        )
        return await idempotency.record(_view(case))

    @router.get("/product-cases", response_model=Page[ProductCaseView])
    async def list_product_cases(
        context: require_access_context,
        limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
        cursor: str | None = Query(default=None, description="Opaque cursor from a previous page."),
        state: Annotated[
            ProductDevState | None, Query(description="Only cases in this state.")
        ] = None,
        pic_user_id: Annotated[
            uuid.UUID | None, Query(description="Only cases this person is PIC of.")
        ] = None,
        category: Annotated[
            str | None,
            Query(
                min_length=1,
                max_length=64,
                description="Only cases stamped with this Category key.",
            ),
        ] = None,
    ) -> Page[ProductCaseView]:
        page = await list_cases.handle(
            context,
            ProductCaseListFilter(state=state, pic_user_id=pic_user_id, category=category),
            limit=limit,
            cursor=cursor,
        )
        return page.map_items(_view)

    @router.get("/product-cases/{case_id}", response_model=ProductCaseDetailView)
    async def get_product_case(
        case_id: uuid.UUID, context: require_access_context
    ) -> ProductCaseDetailView:
        detail = await get.handle(context, ProductDevelopmentCaseId(case_id))
        return ProductCaseDetailView.model_validate(
            {
                **_fields(detail.case),
                "sla": sla_evaluation_view(detail.sla),
                "rounds": [_round_view(r) for r in detail.rounds],
                "actions": [
                    ProductActionOptionView(
                        action=option.action,
                        required_scope=duty_scope(detail.duties.duty_for(option.action)),
                        reason_required=option.reason_required,
                        takes_supplier=option.takes_supplier,
                        document_type=option.document_type,
                        document_required=option.document_required,
                        documents_since=option.documents_since,
                        unmet=list(option.unmet),
                    )
                    for option in detail.case.action_options()
                ],
                "pending_review": _pending_review_view(detail.pending_review),
                "item_code": (
                    ItemCodeView(id=detail.case.item_code.id, code=detail.case.item_code.code)
                    if detail.case.item_code
                    else None
                ),
                "skus": [
                    SkuView(
                        id=s.id,
                        sku_code=s.sku_code,
                        variant_label=s.variant_label,
                        planned_quantity=s.planned_quantity,
                    )
                    for s in detail.case.skus
                ],
                "po_case_id": detail.po_case_id,
            }
        )

    @router.post("/product-cases/{case_id}/order", response_model=OrderPlacedView)
    async def place_product_order(
        case_id: uuid.UUID,
        context: require_access_context,
        idempotency: require_idempotency,
        body: PlaceOrderRequest | None = None,
    ) -> OrderPlacedView:
        """ĐẶT HÀNG: the case is ordered and its PO case opens awaiting its
        PO. A second click is a 409 and opens nothing (a replay under the
        same `Idempotency-Key` returns the first answer)."""
        placed = await place_order.handle(context, case_id=ProductDevelopmentCaseId(case_id))
        return await idempotency.record(
            OrderPlacedView.model_validate(
                {**_fields(placed.case), "po_case_id": placed.po_case.id.value}
            )
        )

    @router.post("/product-cases/{case_id}/transitions", response_model=ProductCaseStepView)
    async def create_product_case_transition(
        case_id: uuid.UUID,
        body: AdvanceProductCaseRequest,
        context: require_access_context,
        idempotency: require_idempotency,
    ) -> ProductCaseStepView:
        result = await advance.handle(
            context,
            case_id=ProductDevelopmentCaseId(case_id),
            action=body.action,
            reason=body.reason,
            supplier_name=body.supplier_name,
            document_id=body.document_id,
            item_code=body.item_code,
            sku=(
                SkuDraft(
                    sku_code=body.sku.sku_code,
                    variant_label=body.sku.variant_label,
                    planned_quantity=body.sku.planned_quantity,
                )
                if body.sku
                else None
            ),
            sku_id=body.sku_id,
        )
        return await idempotency.record(
            ProductCaseStepView.model_validate({**_fields(result.case), "review": result.review})
        )

    @router.get(
        "/product-cases/{case_id}/transitions", response_model=list[ProductCaseTransitionView]
    )
    async def get_product_case_transitions(
        case_id: uuid.UUID, context: require_access_context
    ) -> list[ProductCaseTransitionView]:
        transitions = await list_transitions.handle(context, ProductDevelopmentCaseId(case_id))
        return [_transition_view(t) for t in transitions]

    @router.get("/product-action-duties", response_model=SupplyChainProductActionDuties)
    async def get_product_action_duties(
        context: require_access_context,
    ) -> SupplyChainProductActionDuties:
        """Which duty each product step belongs to for the caller's tenant."""
        return await get_duties.handle(context)

    @router.put("/product-action-duties", response_model=SupplyChainProductActionDuties)
    async def set_product_action_duties_override(
        body: SupplyChainProductActionDuties, context: require_access_context
    ) -> SupplyChainProductActionDuties:
        """Replaces the tenant's own product step-to-duty mapping, whole. No
        `Idempotency-Key`, as for the other policies' `PUT`."""
        await set_duties.handle(context, body)
        return body

    return router
