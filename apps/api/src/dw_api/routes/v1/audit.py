"""Audit events API (read-only; audit is append-only by construction)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from dw_api.dependencies.auth import RequireAccessContext
from dw_api.dependencies.services import RequireContainer
from dw_kernel.errors import InfrastructureError
from dw_kernel.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page, PageQuery, page_request


class AuditEventView(BaseModel):
    """One audit row as the screen shows it.

    ``actor_id`` is here because it was the one thing the table could not show:
    the column exists on the row and is not inside ``details``, so dropping it
    from this view made "who did this" unanswerable from the UI for every action,
    not only sharing (spec 03.1 BR10 names it explicitly).
    """

    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    policy_decision: str | None
    trace_id: str | None
    occurred_at: datetime
    details: dict[str, Any]


router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/events", response_model=Page[AuditEventView])
async def list_events(
    context: RequireAccessContext,
    container: RequireContainer,
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None, description="Opaque cursor from a previous page."),
) -> Page[AuditEventView]:
    if container.uow_factory is None:
        raise InfrastructureError("database is not configured")
    # The audit trail's own scope (`director`, `executive`), not the inbox's
    # `approvals.read`, which every `member` holds.
    await container.authorization.require(
        context=context, action="audit.events", resource_type="audit_event"
    )
    # The tenant and workspace are in the fingerprint even though neither comes
    # from the client: a cursor that somehow crossed accounts or workspaces is
    # then refused outright instead of being answered from the other window.
    request = page_request(
        limit=limit,
        cursor=cursor,
        query=PageQuery(
            key="audit.events",
            filters={"tenant": context.tenant_id, "workspace": context.workspace_id},
        ),
    )
    # Only the caller's workspace: RLS on audit_events narrows by tenant only.
    async with container.uow_factory(context) as uow:
        page = await uow.audit.list_page(request, workspace_id=context.workspace_id)
    return page.map_items(
        lambda e: AuditEventView(
            actor_id=str(e.actor_id),
            action=e.action,
            resource_type=e.resource_type,
            resource_id=e.resource_id,
            policy_decision=e.policy_decision,
            trace_id=e.trace_id,
            occurred_at=e.occurred_at,
            details=dict(e.details),
        )
    )
