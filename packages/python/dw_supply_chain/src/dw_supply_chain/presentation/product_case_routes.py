"""HTTP surface of product-development cases (stage-1 ticket 01). Decisions
live in the handlers (`application.product_cases`).

A router of its own, mounted by the composition root on its own guard, as
the documents router is, rather than seven more arguments to the Supply Chain
router. The product documents themselves are served by the documents router
(`/product-cases/{case_id}/documents`), the same handlers as a PO case's.

The propose body has no PIC field and refuses unknown fields
(`extra="forbid"`), so a body naming a `pic_user_id` is a 422; the command
behind it has no parameter for one either.

No `from __future__ import annotations`, for the reason `routes.py` gives.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Protocol, TypeVar

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from dw_kernel.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.application.handlers import (
    GetProductActionDuties,
    SetProductActionDutiesOverride,
    duty_scope,
)
from dw_supply_chain.application.ports import ProductCaseListFilter
from dw_supply_chain.application.product_cases import (
    AdvanceProductCase,
    GetProductCase,
    ListProductCases,
    ListProductCaseTransitions,
    ProposeProductCase,
)
from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.product_development_case import (
    ProductAction,
    ProductCaseTransition,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
    SampleResult,
    SampleRound,
)
from dw_supply_chain.product_action_duties import SupplyChainProductActionDuties

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
    through its documents (doc_type `product_image`)."""

    model_config = ConfigDict(extra="forbid")

    proposal_code: str = Field(min_length=1, max_length=100, pattern=_NO_NUL)
    product_name: str = Field(min_length=1, max_length=300, pattern=_NO_NUL)
    category: str = Field(min_length=1, max_length=100, pattern=_NO_NUL)


class AdvanceProductCaseRequest(BaseModel):
    """One step. Each step reads the fields it takes and refuses the others:
    `supplier_name` for `request_sample`, `document_id` for `pass_sample`,
    `request_revision` and (optionally) `reject_sample`, `reason` where the
    step needs one."""

    model_config = ConfigDict(extra="forbid")

    action: ProductAction
    reason: str | None = Field(default=None, max_length=2000, pattern=_NO_NUL)
    supplier_name: str | None = Field(default=None, min_length=1, max_length=200, pattern=_NO_NUL)
    document_id: uuid.UUID | None = None


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
    created_by: uuid.UUID
    created_at: datetime | None
    version: int


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
    and form from this; the server checks all of it again on the step."""

    action: ProductAction
    required_scope: str
    reason_required: bool
    takes_supplier: bool
    document_type: DocumentType | None
    document_required: bool


class ProductCaseDetailView(ProductCaseView):
    rounds: list[SampleRoundView]
    actions: list[ProductActionOptionView]


class ProductCaseTransitionView(BaseModel):
    action: ProductAction
    from_state: ProductDevState | None
    to_state: ProductDevState
    reason: str | None
    actor_id: uuid.UUID
    occurred_at: datetime


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


def _transition_view(transition: ProductCaseTransition) -> ProductCaseTransitionView:
    return ProductCaseTransitionView(
        action=transition.action,
        from_state=transition.from_state,
        to_state=transition.to_state,
        reason=transition.reason,
        actor_id=transition.actor_id,
        occurred_at=transition.occurred_at,
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
    ) -> Page[ProductCaseView]:
        page = await list_cases.handle(
            context,
            ProductCaseListFilter(state=state, pic_user_id=pic_user_id),
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
                "rounds": [_round_view(r) for r in detail.rounds],
                "actions": [
                    ProductActionOptionView(
                        action=option.action,
                        required_scope=duty_scope(detail.duties.duty_for(option.action)),
                        reason_required=option.reason_required,
                        takes_supplier=option.takes_supplier,
                        document_type=option.document_type,
                        document_required=option.document_required,
                    )
                    for option in detail.case.action_options()
                ],
            }
        )

    @router.post("/product-cases/{case_id}/transitions", response_model=ProductCaseView)
    async def create_product_case_transition(
        case_id: uuid.UUID,
        body: AdvanceProductCaseRequest,
        context: require_access_context,
        idempotency: require_idempotency,
    ) -> ProductCaseView:
        case = await advance.handle(
            context,
            case_id=ProductDevelopmentCaseId(case_id),
            action=body.action,
            reason=body.reason,
            supplier_name=body.supplier_name,
            document_id=body.document_id,
        )
        return await idempotency.record(_view(case))

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
