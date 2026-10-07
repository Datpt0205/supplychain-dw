"""The signed-in user's own Zalo link: status, connect, disconnect.

A person links their own Zalo, no hand-mapping: ``connect`` hands out a
one-time ``/start <code>`` line, they send it to the deployment's bot, and the
worker's poll lane (the API webhook once a public host exists) redeems it.

Every route acts on ``context.principal_id`` — the user resolved server-side
from the verified bearer token — and takes no body, query or path value that
could name somebody else. The link belongs to the person, not to one of their
workspaces (ADR 0005): the same user reads "linked" from every tenant they are
a member of, by design.

Mounted only when the bot token and the link secret are both configured; an
unconfigured deployment answers 404 and the settings page shows "chưa cấu hình".

No ``Idempotency-Key`` on ``connect``: its response is a credential, and the
platform's replay store fingerprints method, path, workspace and body but not
the caller, so a replay could hand one person's code to another who sent the
same key. A second press simply mints a second code; the first stays usable
until it expires, and each redeems at most once.

``/workspace`` reads and sets which workspace the caller's Zalo commands act in
("Workspace dùng cho Zalo"). The body names a tenant and workspace, and that is
a choice, not a claim: the store confirms it is one of the caller's own
memberships, under ``app.principal_id`` bound to ``context.principal_id`` in
the same transaction, and anything else answers 404 — the same answer for a
workspace that exists and one that does not.

``webhook_router`` is the other door: Zalo itself, not a signed-in person
(ADR 0008). It is mounted only in webhook mode with a
secret set, so a poll deployment has no such route. It carries no bearer token,
so the one thing that admits a call is the ``X-Bot-Api-Secret-Token`` header,
compared in constant time before a byte of the body is read; a missing or
wrong one is refused with the platform's 403 and nothing is queued. The body is
capped at 64 KB, validated as a bot update, and refused without echoing it. An
accepted update is queued unchanged and answered 200 at once: the worker's
drain hands it to the same ``ZaloInbound.handle`` the poll lane calls, where
the chat is resolved to a linked person and the message id is claimed once.
Nothing here reads a person, a tenant or a scope out of the update.
"""

from __future__ import annotations

import hmac
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from dw_api.dependencies.auth import RequireAccessContext
from dw_api.dependencies.services import RequireContainer
from dw_connectors.adapters.zalo_inbound import CHANNEL
from dw_connectors.adapters.zalo_link import ZaloLinking
from dw_kernel.errors import (
    DomainError,
    NotFoundError,
    PayloadTooLargeError,
    PermissionDeniedError,
)

router = APIRouter(prefix="/zalo", tags=["zalo"])


class ZaloStatusView(BaseModel):
    # Whether the caller is linked, and nothing else: the chat id is a delivery
    # address the browser has no use for.
    linked: bool


class ZaloConnectView(BaseModel):
    # Sent to the bot as ``/start <code>``; redeemable once, until expires_at.
    code: str
    # The bot's chat with the code prefilled, when the deployment set one.
    deep_link: str | None
    expires_at: datetime


class ZaloWorkspaceView(BaseModel):
    # Where the caller's Zalo commands act; both null until they choose. With
    # one membership and no choice the bot uses that one.
    tenant_id: UUID | None
    workspace_id: UUID | None


class ZaloWorkspaceChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID
    workspace_id: UUID


def _linking(container: RequireContainer) -> ZaloLinking:
    if container.zalo_linking is None:
        raise NotFoundError("zalo linking is not configured")
    return container.zalo_linking


@router.get("/status", response_model=ZaloStatusView)
async def zalo_status(context: RequireAccessContext, container: RequireContainer) -> ZaloStatusView:
    return ZaloStatusView(linked=await _linking(container).is_linked(context.principal_id))


@router.post("/connect", response_model=ZaloConnectView)
async def zalo_connect(
    context: RequireAccessContext, container: RequireContainer
) -> ZaloConnectView:
    offer = await _linking(container).connect(context.principal_id)
    return ZaloConnectView(code=offer.code, deep_link=offer.deep_link, expires_at=offer.expires_at)


@router.post("/disconnect", status_code=204)
async def zalo_disconnect(context: RequireAccessContext, container: RequireContainer) -> Response:
    await _linking(container).disconnect(context.principal_id)
    return Response(status_code=204)


@router.get("/workspace", response_model=ZaloWorkspaceView)
async def zalo_workspace(
    context: RequireAccessContext, container: RequireContainer
) -> ZaloWorkspaceView:
    _linking(container)
    if container.channel_preferences is None:
        raise NotFoundError("zalo linking is not configured")
    chosen = await container.channel_preferences.chosen(context.principal_id)
    tenant_id, workspace_id = chosen if chosen is not None else (None, None)
    return ZaloWorkspaceView(tenant_id=tenant_id, workspace_id=workspace_id)


@router.put("/workspace", response_model=ZaloWorkspaceView)
async def zalo_choose_workspace(
    body: ZaloWorkspaceChoice, context: RequireAccessContext, container: RequireContainer
) -> ZaloWorkspaceView:
    _linking(container)
    if container.channel_preferences is None:
        raise NotFoundError("zalo linking is not configured")
    if not await container.channel_preferences.choose(
        context.principal_id, body.tenant_id, body.workspace_id
    ):
        raise NotFoundError("no such workspace among your memberships")
    return ZaloWorkspaceView(tenant_id=body.tenant_id, workspace_id=body.workspace_id)


# ---- the webhook -----------------------------------------------------------

webhook_router = APIRouter(prefix="/zalo", tags=["zalo"])

# The header Zalo's Bot Platform sends the ``secret_token`` given to
# ``setWebhook`` in (Telegram's dialect). PROVISIONAL until a live run
# sees a real call carry it; without it every call is refused and
# the deployment stays on poll, which is the safe way to be wrong.
WEBHOOK_SECRET_HEADER = "X-Bot-Api-Secret-Token"
# One chat update is a few hundred bytes; 64 KB is far past any and still small.
MAX_WEBHOOK_BYTES = 64 * 1024


class ZaloWebhookUpdate(BaseModel):
    """A bot update, in either envelope ``parse_update`` reads: the message at
    the top level (the webhook, as sales_dw saw it) or under ``result`` (the
    poll path's). Unknown fields are kept: the update is queued as it came."""

    model_config = ConfigDict(extra="allow")

    message: dict[str, Any] | None = None
    result: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _carries_a_message(self) -> ZaloWebhookUpdate:
        # The envelope ``parse_update`` will read: ``result`` when present.
        if self.result is not None:
            if not isinstance(self.result.get("message"), dict):
                raise ValueError("not a bot update")
        elif self.message is None:
            raise ValueError("not a bot update")
        return self


def _admitted(request: Request, secret: str) -> bool:
    presented = request.headers.get(WEBHOOK_SECRET_HEADER, "")
    return hmac.compare_digest(presented.encode(), secret.encode())


async def _body_within_cap(request: Request) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_WEBHOOK_BYTES:
        raise PayloadTooLargeError(f"a webhook update is at most {MAX_WEBHOOK_BYTES} bytes")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_WEBHOOK_BYTES:
            raise PayloadTooLargeError(f"a webhook update is at most {MAX_WEBHOOK_BYTES} bytes")
    return bytes(body)


@webhook_router.post("/webhook", include_in_schema=False)
async def zalo_webhook(request: Request, container: RequireContainer) -> Response:
    inbox = container.zalo_webhook_inbox
    settings = container.settings
    # Mounted only when enabled; asked again so a route mounted by mistake
    # still answers as if it did not exist.
    if inbox is None or not settings.zalo_webhook_enabled:
        raise NotFoundError("not found")
    if not _admitted(request, settings.zalo_webhook_secret.get_secret_value()):
        raise PermissionDeniedError("webhook secret missing or wrong")
    body = await _body_within_cap(request)
    try:
        update = ZaloWebhookUpdate.model_validate_json(body)
    except ValidationError:
        # Pydantic's own message quotes the input; this one never does.
        raise DomainError("the body is not a Zalo bot update") from None
    await inbox.enqueue(CHANNEL, update.model_dump(mode="json", exclude_unset=True))
    return Response(status_code=200)
