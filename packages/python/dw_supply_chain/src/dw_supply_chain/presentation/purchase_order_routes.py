"""HTTP surface of step 10's purchase order draft (ticket ai-automation/14).
Decisions live in `application.purchase_orders`.

`GET /po-cases/{id}/purchase-order` — the case's latest PO draft (its id,
version, status and content hash; its fields are read through the drafts API,
prices hidden per scope), what code finds in it now, and whether the caller
may approve it (and why not). No price in any of it.
`POST /po-cases/{id}/purchase-order/approval` — Cung ứng approves the draft
it saw (`content_sha256`) with the PO number: the PO is created, the draft
becomes the `purchase_order` document and the case's terms and prices are
set, together.

No `from __future__ import annotations`, for the reason `routes.py` gives.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from dw_platform.application.access_context import AccessContext
from dw_supply_chain.application.purchase_orders import (
    ApprovePurchaseOrder,
    GetPurchaseOrderProposal,
    PurchaseOrderProposal,
)
from dw_supply_chain.domain.document_draft import DraftStatus
from dw_supply_chain.domain.po_case import POCaseId
from dw_supply_chain.presentation.routes import SupportsIdempotentRecord

AccessContextResolver = Callable[..., object]
IdempotencyResolver = Callable[..., object]
_NO_NUL = r"^[^\x00]*$"


class PurchaseOrderFindingView(BaseModel):
    code: str
    subject: str
    message: str


class PurchaseOrderProposalView(BaseModel):
    # null: no draft yet.
    draft_id: uuid.UUID | None
    draft_version: int | None
    draft_status: DraftStatus | None
    content_sha256: str | None
    findings: list[PurchaseOrderFindingView]
    can_approve: bool
    # Why the caller may not approve, in words; null when they may.
    blocked_reason: str | None


class ApprovePurchaseOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: uuid.UUID
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    po_reference: str = Field(min_length=1, max_length=100, pattern=_NO_NUL)


def _view(found: PurchaseOrderProposal) -> PurchaseOrderProposalView:
    draft = found.draft
    return PurchaseOrderProposalView(
        draft_id=None if draft is None else draft.id,
        draft_version=None if draft is None else draft.version,
        draft_status=None if draft is None else draft.status,
        content_sha256=None if draft is None else draft.content_sha256,
        findings=[
            PurchaseOrderFindingView(code=f.code, subject=f.subject, message=f.message)
            for f in found.findings
        ],
        can_approve=found.can_approve,
        blocked_reason=found.blocked,
    )


@dataclass(frozen=True)
class PurchaseOrderHandlers:
    get: GetPurchaseOrderProposal
    approve: ApprovePurchaseOrder


def build_purchase_order_router(
    handlers: PurchaseOrderHandlers,
    *,
    resolve_access_context: AccessContextResolver,
    resolve_idempotency: IdempotencyResolver,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/supply-chain", tags=["supply_chain"])
    require_access_context = Annotated[AccessContext, Depends(resolve_access_context)]
    require_idempotency = Annotated[SupportsIdempotentRecord, Depends(resolve_idempotency)]
    h = handlers

    @router.get("/po-cases/{case_id}/purchase-order", response_model=PurchaseOrderProposalView)
    async def get_purchase_order_proposal(
        case_id: uuid.UUID, context: require_access_context
    ) -> PurchaseOrderProposalView:
        return _view(await h.get.handle(context, POCaseId(case_id)))

    @router.post(
        "/po-cases/{case_id}/purchase-order/approval",
        response_model=PurchaseOrderProposalView,
    )
    async def approve_purchase_order(
        case_id: uuid.UUID,
        body: ApprovePurchaseOrderRequest,
        context: require_access_context,
        idempotency: require_idempotency,
    ) -> PurchaseOrderProposalView:
        await h.approve.handle(
            context,
            POCaseId(case_id),
            draft_id=body.draft_id,
            content_sha256=body.content_sha256,
            po_reference=body.po_reference,
        )
        view = _view(await h.get.handle(context, POCaseId(case_id)))
        recorded: PurchaseOrderProposalView = await idempotency.record(view)
        return recorded

    return router
