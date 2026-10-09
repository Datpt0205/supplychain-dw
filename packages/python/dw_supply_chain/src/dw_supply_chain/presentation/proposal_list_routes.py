"""HTTP surface of proposal lists (step 1; ticket ai-automation/08). Decisions
live in `application.proposal_lists`.

`POST /proposal-lists` — a list file (multipart), stored as uploaded; AI reads
it in the worker. `GET /proposal-lists` — the workspace's recent lists.
`GET /proposal-lists/{id}` — the reading's rows, each with its cited fields,
gaps, AI's Category and priority suggestions, code's findings and the PIC's
decision. `POST /proposal-lists/{id}/rows/{index}/proposal` — the PIC proposes
the row with the values they checked (the product case `propose` makes).
`POST /proposal-lists/{id}/rows/{index}/dismissal` — the row is dropped.

No `from __future__ import annotations`, for the reason `routes.py` gives.
"""

import hashlib
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from dw_platform.application.access_context import AccessContext
from dw_supply_chain.application.proposal_lists import (
    MAX_LIST_BYTES,
    DropFromList,
    GetProposalList,
    ListProposalLists,
    ProposalList,
    ProposalListView,
    ProposeFromList,
    RowDecision,
    UploadProposalList,
)
from dw_supply_chain.domain.extraction import ExtractionStatus
from dw_supply_chain.domain.proposal_list import FINDING_WORDS, ROW_FIELDS, RowFinding
from dw_supply_chain.presentation.document_routes import (
    MULTIPART_OVERHEAD_BYTES,
    SupportsFormIdempotency,
    body_capped_route,
)
from dw_supply_chain.presentation.routes import SupportsIdempotentRecord

AccessContextResolver = Callable[..., object]
IdempotencyResolver = Callable[..., object]
_NO_NUL = r"^[^\x00]*$"


class ProposalListSummaryView(BaseModel):
    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    uploaded_by: uuid.UUID
    created_at: datetime


class CitedValueView(BaseModel):
    value: str
    quote: str


class RowFindingView(BaseModel):
    code: RowFinding
    message: str


class RowDecisionView(BaseModel):
    decision: RowDecision
    product_dev_case_id: uuid.UUID | None
    reason: str | None
    decided_by: uuid.UUID
    decided_at: datetime


class ProposalRowView(BaseModel):
    index: int
    # Each field the list proved, with its quote; null where it did not.
    fields: dict[str, CitedValueView | None]
    # Fields the model named that the text did not prove.
    gaps: list[str]
    # A key of the tenant's Category list, or null: AI's suggestion only.
    category: str | None
    category_reason: str | None
    priority: str | None
    priority_reason: str | None
    findings: list[RowFindingView]
    decision: RowDecisionView | None


class ProposalListDetailView(BaseModel):
    """The list, how AI read it, and its rows."""

    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    uploaded_by: uuid.UUID
    created_at: datetime
    # null: not read yet.
    status: ExtractionStatus | None
    rows: list[ProposalRowView]


class ProposeRowRequest(BaseModel):
    """The values the PIC checked: what `propose` takes."""

    model_config = ConfigDict(extra="forbid")

    proposal_code: str = Field(min_length=1, max_length=100, pattern=_NO_NUL)
    product_name: str = Field(min_length=1, max_length=300, pattern=_NO_NUL)
    category: str = Field(min_length=1, max_length=100, pattern=_NO_NUL)


class DropRowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1000, pattern=_NO_NUL)


def _summary(found: ProposalList) -> ProposalListSummaryView:
    return ProposalListSummaryView(
        id=found.id,
        filename=found.filename,
        content_type=found.content_type,
        size_bytes=found.size_bytes,
        uploaded_by=found.uploaded_by,
        created_at=found.created_at,
    )


def _cited(raw: Any) -> CitedValueView | None:
    if not isinstance(raw, Mapping):
        return None
    return CitedValueView(value=str(raw.get("value", "")), quote=str(raw.get("quote", "")))


def _detail(view: ProposalListView) -> ProposalListDetailView:
    found, reading = view.proposal_list, view.reading
    rows: list[ProposalRowView] = []
    for raw in [] if reading is None else reading.rows:
        index = int(raw["index"])
        decided = view.decisions.get(index)
        fields = raw.get("fields") or {}
        rows.append(
            ProposalRowView(
                index=index,
                fields={name: _cited(fields.get(name)) for name in ROW_FIELDS},
                gaps=[str(g) for g in raw.get("gaps") or []],
                category=raw.get("category"),
                category_reason=raw.get("category_reason"),
                priority=raw.get("priority"),
                priority_reason=raw.get("priority_reason"),
                findings=[
                    RowFindingView(code=RowFinding(f), message=FINDING_WORDS[RowFinding(f)])
                    for f in raw.get("findings") or []
                ],
                decision=None
                if decided is None
                else RowDecisionView(
                    decision=decided.decision,
                    product_dev_case_id=decided.product_dev_case_id,
                    reason=decided.reason,
                    decided_by=decided.decided_by,
                    decided_at=decided.decided_at,
                ),
            )
        )
    return ProposalListDetailView(
        id=found.id,
        filename=found.filename,
        content_type=found.content_type,
        size_bytes=found.size_bytes,
        uploaded_by=found.uploaded_by,
        created_at=found.created_at,
        status=None if reading is None else reading.status,
        rows=rows,
    )


@dataclass(frozen=True)
class ProposalListHandlers:
    upload: UploadProposalList
    list_recent: ListProposalLists
    get: GetProposalList
    propose: ProposeFromList
    drop: DropFromList


def build_proposal_list_router(
    handlers: ProposalListHandlers,
    *,
    resolve_access_context: AccessContextResolver,
    resolve_idempotency: IdempotencyResolver,
    resolve_form_idempotency: IdempotencyResolver,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/supply-chain", tags=["supply_chain"])
    require_access_context = Annotated[AccessContext, Depends(resolve_access_context)]
    require_idempotency = Annotated[SupportsIdempotentRecord, Depends(resolve_idempotency)]
    require_form_idempotency = Annotated[SupportsFormIdempotency, Depends(resolve_form_idempotency)]
    h = handlers

    async def upload_proposal_list(
        context: require_access_context,
        idempotency: require_form_idempotency,
        file: Annotated[UploadFile, File()],
    ) -> ProposalListDetailView:
        data = await file.read(MAX_LIST_BYTES + 1)
        await idempotency.claim_fields(
            {
                "filename": file.filename or "",
                "content_type": file.content_type or "",
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
        stored = await h.upload.handle(
            context,
            filename=file.filename or "",
            content_type=file.content_type or "",
            data=data,
        )
        view = _detail(await h.get.handle(context, stored.id))
        recorded: ProposalListDetailView = await idempotency.record(view, status_code=201)
        return recorded

    router.add_api_route(
        "/proposal-lists",
        upload_proposal_list,
        methods=["POST"],
        name="upload_proposal_list",
        response_model=ProposalListDetailView,
        status_code=201,
        route_class_override=body_capped_route(MAX_LIST_BYTES + MULTIPART_OVERHEAD_BYTES),
    )

    @router.get("/proposal-lists", response_model=list[ProposalListSummaryView])
    async def list_proposal_lists(
        context: require_access_context,
    ) -> list[ProposalListSummaryView]:
        return [_summary(found) for found in await h.list_recent.handle(context)]

    @router.get("/proposal-lists/{list_id}", response_model=ProposalListDetailView)
    async def get_proposal_list(
        list_id: uuid.UUID, context: require_access_context
    ) -> ProposalListDetailView:
        return _detail(await h.get.handle(context, list_id))

    @router.post(
        "/proposal-lists/{list_id}/rows/{index}/proposal",
        response_model=ProposalListDetailView,
        status_code=201,
    )
    async def propose_from_list(
        list_id: uuid.UUID,
        index: int,
        body: ProposeRowRequest,
        context: require_access_context,
        idempotency: require_idempotency,
    ) -> ProposalListDetailView:
        await h.propose.handle(
            context,
            list_id,
            index,
            proposal_code=body.proposal_code,
            product_name=body.product_name,
            category=body.category,
        )
        view = _detail(await h.get.handle(context, list_id))
        return await idempotency.record(view, status_code=201)

    @router.post(
        "/proposal-lists/{list_id}/rows/{index}/dismissal",
        response_model=ProposalListDetailView,
    )
    async def drop_from_list(
        list_id: uuid.UUID,
        index: int,
        body: DropRowRequest,
        context: require_access_context,
        idempotency: require_idempotency,
    ) -> ProposalListDetailView:
        await h.drop.handle(context, list_id, index, reason=body.reason)
        view = _detail(await h.get.handle(context, list_id))
        return await idempotency.record(view)

    return router
