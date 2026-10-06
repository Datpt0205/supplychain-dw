"""Approvals API: inbox + decisions (decision resumes the paused run)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from dw_agent_runtime.approval_flow import ApproveAndResumeService
from dw_api.dependencies.auth import RequireAccessContext
from dw_api.dependencies.idempotency import RequireIdempotency
from dw_api.dependencies.services import RequireContainer
from dw_kernel.errors import InfrastructureError, NotFoundError
from dw_kernel.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page, PageQuery, page_request
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.domain.approval import ApprovalRequest


class ApprovalView(BaseModel):
    id: uuid.UUID
    approval_type: str
    reason: str
    status: str
    run_id: uuid.UUID | None
    payload: dict[str, Any]
    created_at: datetime | None
    decided_at: datetime | None
    # The server refuses a blank comment for a strict type; without this the
    # form would have to keep its own copy of the prefix list.
    requires_comment: bool
    # The scope a decider must hold besides `approvals.decide`, stamped when the
    # request was raised (ADR 0020). Shown as the reason when `can_decide` is
    # false; the page never compares it with the session's scopes itself.
    required_scope: str | None
    # Whether the caller's scopes let them decide this request, by the same
    # checks `ApproveAndResumeService.decide` runs (`may_decide`). Computed here
    # because the session's `hasScope` lets `platform_admin` pass any scope and
    # a stamped scope is not passed by that role (QO-8, 2026-10-06).
    can_decide: bool
    # Whether the caller raised this request. Withdrawing your own needs no
    # scope at all, and the page cannot tell which requests are the viewer's
    # without it; a boolean, so no other member's id reaches the browser.
    requested_by_me: bool


def _view(
    request: ApprovalRequest,
    context: AccessContext,
    approval_flow: ApproveAndResumeService,
    authorization: ScopeAuthorizationService,
) -> ApprovalView:
    return ApprovalView(
        id=request.id,
        approval_type=request.approval_type,
        reason=request.reason,
        status=request.status.value,
        run_id=request.run_id,
        payload=dict(request.payload),
        created_at=request.created_at,
        decided_at=request.decided_at,
        requires_comment=approval_flow.is_strict(request.approval_type),
        required_scope=request.required_scope,
        can_decide=approval_flow.may_decide(request, context, authorization),
        requested_by_me=request.requested_by.value == context.principal_id,
    )


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approve: bool
    comment: str = ""
    approved_action_ids: list[str] | None = None


router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=Page[ApprovalView])
async def list_pending(
    context: RequireAccessContext,
    container: RequireContainer,
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None, description="Opaque cursor from a previous page."),
) -> Page[ApprovalView]:
    if container.uow_factory is None or container.approval_flow is None:
        raise InfrastructureError("database is not configured")
    await container.authorization.require(
        context=context, action="approvals.read", resource_type="approval_request"
    )
    # The workspace is in the fingerprint as the tenant is: a cursor taken in
    # one workspace is refused in another rather than answered from its window.
    request = page_request(
        limit=limit,
        cursor=cursor,
        query=PageQuery(
            key="approvals.pending",
            filters={"tenant": context.tenant_id, "workspace": context.workspace_id},
        ),
    )
    approval_flow = container.approval_flow
    authorization = container.authorization
    async with container.uow_factory(context) as uow:
        page = await uow.approvals.list_pending(request, workspace_id=context.workspace_id)
    return page.map_items(lambda p: _view(p, context, approval_flow, authorization))


@router.get("/{approval_id}", response_model=ApprovalView)
async def get_approval(
    approval_id: uuid.UUID, context: RequireAccessContext, container: RequireContainer
) -> ApprovalView:
    if container.uow_factory is None or container.approval_flow is None:
        raise InfrastructureError("database is not configured")
    await container.authorization.require(
        context=context,
        action="approvals.read",
        resource_type="approval_request",
        resource_id=str(approval_id),
    )
    async with container.uow_factory(context) as uow:
        request = await uow.approvals.get(approval_id, workspace_id=context.workspace_id)
    # Another workspace's request is the same answer as one that never existed.
    if request is None:
        raise NotFoundError("approval request not found")
    return _view(request, context, container.approval_flow, container.authorization)


# A decision resumes a checkpointed run, and the run is where the side effects
# are — so a retry after a timeout is the one request on this router that must
# never be executed twice. `Idempotency-Key` is honoured here (optional; see
# README), and on nothing else in this module: the two GETs are reads.
@router.post("/{approval_id}/decisions", response_model=ApprovalView)
async def decide(
    approval_id: uuid.UUID,
    body: DecisionRequest,
    context: RequireAccessContext,
    container: RequireContainer,
    idempotency: RequireIdempotency,
) -> ApprovalView:
    if container.approval_flow is None:
        raise InfrastructureError("approval flow is not configured")
    request = await container.approval_flow.decide(
        approval_id=approval_id,
        approve=body.approve,
        comment=body.comment,
        context=context,
        authorization=container.authorization,
        approved_action_ids=body.approved_action_ids,
    )
    return await idempotency.record(
        _view(request, context, container.approval_flow, container.authorization)
    )
