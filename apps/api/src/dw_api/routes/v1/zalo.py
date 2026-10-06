"""The signed-in user's own Zalo link: status, connect, disconnect.

A person links their own Zalo, no hand-mapping: ``connect`` hands out a
one-time ``/start <code>`` line, they send it to the deployment's bot, and the
worker's poll lane (the API webhook once a public host exists) redeems it.

Every route acts on ``context.principal_id`` — the user resolved server-side
from the verified bearer token — and takes no body, query or path value that
could name somebody else. The link belongs to the person, not to one of their
workspaces (ADR 0012): the same user reads "linked" from every tenant they are
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
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict

from dw_api.dependencies.auth import RequireAccessContext
from dw_api.dependencies.services import RequireContainer
from dw_connectors.adapters.zalo_link import ZaloLinking
from dw_kernel.errors import NotFoundError

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
