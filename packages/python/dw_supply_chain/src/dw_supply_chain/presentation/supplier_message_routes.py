"""HTTP surface of messages to a supplier (ADR 0029; ticket ai-automation/07).
Decisions live in `application.supplier_messages`.

`GET /po-cases/{id}/supplier-messages`, `GET /product-cases/{id}/supplier-messages`
— the case's drafted messages, newest first, each with who marked it sent.
`POST /supplier-messages/{id}/sent` — "Đã gửi": the person sent this text
(`content_sha256`) from their own mailbox. Nothing here sends anything.

No `from __future__ import annotations`, for the reason `routes.py` gives.
"""

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from dw_platform.application.access_context import AccessContext
from dw_supply_chain.application.supplier_messages import (
    ListSupplierMessages,
    MarkSupplierMessageSent,
    SupplierMessage,
)
from dw_supply_chain.domain.case_document import CaseKind
from dw_supply_chain.domain.supplier_message import MessagePurpose, MessageStatus
from dw_supply_chain.presentation.routes import SupportsIdempotentRecord

AccessContextResolver = Callable[..., object]
IdempotencyResolver = Callable[..., object]


class MessageCitationView(BaseModel):
    """One paragraph AI wrote and code kept, with the evidence it cites."""

    text: str
    cites: list[str]


class SupplierMessageView(BaseModel):
    id: uuid.UUID
    case_kind: CaseKind
    case_id: uuid.UUID
    purpose: MessagePurpose
    status: MessageStatus
    supplier_name: str | None
    recipient_name: str | None
    recipient_email: str | None
    subject: str
    body: str
    attachments: list[uuid.UUID]
    citations: list[MessageCitationView]
    # Paragraphs the model wrote that code dropped (a number or a citation that
    # did not check out).
    dropped: int
    template_version: str
    prompt_id: str
    prompt_version: str
    content_sha256: str
    created_at: datetime
    sent_by: uuid.UUID | None
    sent_at: datetime | None


class MarkSentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The text the person copied: what "Đã gửi" records.
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _citation(raw: Mapping[str, Any]) -> MessageCitationView:
    return MessageCitationView(
        text=str(raw.get("text", "")), cites=[str(c) for c in raw.get("cites") or []]
    )


def _view(message: SupplierMessage) -> SupplierMessageView:
    return SupplierMessageView(
        id=message.id,
        case_kind=message.case_kind,
        case_id=message.case_id,
        purpose=message.purpose,
        status=message.status,
        supplier_name=message.supplier_name,
        recipient_name=message.recipient_name,
        recipient_email=message.recipient_email,
        subject=message.subject,
        body=message.body,
        attachments=list(message.attachments),
        citations=[_citation(c) for c in message.citations],
        dropped=message.dropped,
        template_version=message.template_version,
        prompt_id=message.prompt_id,
        prompt_version=message.prompt_version,
        content_sha256=message.content_sha256,
        created_at=message.created_at,
        sent_by=message.sent_by,
        sent_at=message.sent_at,
    )


@dataclass(frozen=True)
class SupplierMessageHandlers:
    """What the composition root builds for this router."""

    list_messages: ListSupplierMessages
    mark_sent: MarkSupplierMessageSent


_CASE_SEGMENTS = {
    CaseKind.PO: ("po-cases", "po_case"),
    CaseKind.PRODUCT: ("product-cases", "product_case"),
}


def build_supplier_message_router(
    handlers: SupplierMessageHandlers,
    *,
    resolve_access_context: AccessContextResolver,
    resolve_idempotency: IdempotencyResolver,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/supply-chain", tags=["supply_chain"])
    require_access_context = Annotated[AccessContext, Depends(resolve_access_context)]
    require_idempotency = Annotated[SupportsIdempotentRecord, Depends(resolve_idempotency)]
    h = handlers

    for kind, (segment, name) in _CASE_SEGMENTS.items():

        async def list_case_messages(
            case_id: uuid.UUID, context: require_access_context, kind: CaseKind = kind
        ) -> list[SupplierMessageView]:
            return [_view(m) for m in await h.list_messages.handle(context, kind, case_id)]

        router.add_api_route(
            f"/{segment}/{{case_id}}/supplier-messages",
            list_case_messages,
            methods=["GET"],
            name=f"list_{name}_supplier_messages",
            response_model=list[SupplierMessageView],
        )

    @router.post("/supplier-messages/{message_id}/sent", response_model=SupplierMessageView)
    async def mark_supplier_message_sent(
        message_id: uuid.UUID,
        body: MarkSentRequest,
        context: require_access_context,
        idempotency: require_idempotency,
    ) -> SupplierMessageView:
        sent = await h.mark_sent.handle(context, message_id, content_sha256=body.content_sha256)
        return await idempotency.record(_view(sent))

    return router
