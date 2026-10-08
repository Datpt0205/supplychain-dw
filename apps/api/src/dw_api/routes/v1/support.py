"""Customer-granted support access, the customer's side: /api/v1/support/* (ADR 0024).

The service owns every rule (the feature flag, which side of support the caller
is on, granting only held scopes, workspace narrowing, audit); a route shapes
the request and the response. A body naming a staff member is refused by the
schema (`extra="forbid"`): the customer never picks the person.

Refusals keep the platform's status and `code`; `details.reason_code` says
which one (`SupportRefusal`): `support_access_not_enabled` (403),
`support_scope_not_held` (403), `support_scope_set_unknown` (422),
`support_resource_type_not_offered` (422), `support_grant_wrong_status` (409).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from dw_api.bootstrap import ApiContainer
from dw_api.dependencies.auth import RequireAccessContext, RequireVerifiedIdentity
from dw_api.dependencies.idempotency import RequireIdempotency
from dw_api.dependencies.services import RequireContainer
from dw_kernel.errors import InfrastructureError
from dw_platform.application.support_access import (
    MAX_DURATION_HOURS,
    MAX_REASON_LENGTH,
    RequestSupportGrant,
    SupportGrantService,
    SupportGrantView,
    SupportRefusal,
    support_access_refused,
)

router = APIRouter(prefix="/support", tags=["support"])


class SupportScopeSetView(BaseModel):
    """A grantable set: no scope list, only what a person chooses by."""

    key: str
    label: str
    resource_types: list[str]


class SupportGrantModel(BaseModel):
    id: UUID
    code: str
    workspace_id: UUID
    resource_type: str
    resource_id: UUID | None
    resource_label: str
    scope_set_key: str
    scope_set_label: str
    reason: str
    duration_hours: int
    # What is stored (`pending_approval`, `pending_assignment`, `active`,
    # `rejected`, `revoked`) and what it is now (`state` adds `expired` and
    # `ineffective` for a stored `active`).
    status: str
    state: str
    requested_by: UUID | None
    requested_at: datetime
    granted_by: UUID | None
    granted_at: datetime | None
    rejected_at: datetime | None
    reject_reason: str | None
    staff_user_id: UUID | None
    activated_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


class RequestSupportGrantBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_set_key: str = Field(min_length=1, max_length=64)
    resource_type: str = Field(min_length=1, max_length=40)
    resource_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=MAX_REASON_LENGTH)
    duration_hours: int = Field(ge=1, le=MAX_DURATION_HOURS)


class RejectSupportGrantBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=MAX_REASON_LENGTH)


def _service(container: ApiContainer) -> SupportGrantService:
    if container.support_grants is None:
        raise InfrastructureError("database is not configured")
    return container.support_grants


def _model(view: SupportGrantView) -> SupportGrantModel:
    g = view.grant
    return SupportGrantModel(
        id=g.id,
        code=g.code,
        workspace_id=g.workspace_id,
        resource_type=g.resource_type,
        resource_id=g.resource_id,
        resource_label=g.resource_label,
        scope_set_key=g.scope_set_key,
        scope_set_label=g.scope_set_label,
        reason=g.reason,
        duration_hours=g.duration_hours,
        status=g.status.value,
        state=view.state,
        requested_by=g.requested_by,
        requested_at=g.requested_at,
        granted_by=g.granted_by,
        granted_at=g.granted_at,
        rejected_at=g.rejected_at,
        reject_reason=g.reject_reason,
        staff_user_id=g.staff_user_id,
        activated_at=g.activated_at,
        expires_at=g.expires_at,
        revoked_at=g.revoked_at,
    )


@router.get("/catalog", response_model=list[SupportScopeSetView])
async def catalog(
    context: RequireAccessContext, container: RequireContainer
) -> list[SupportScopeSetView]:
    return [
        SupportScopeSetView(key=s.key, label=s.label, resource_types=sorted(s.resource_types))
        for s in _service(container).catalog_for(context)
    ]


@router.get("/grants", response_model=list[SupportGrantModel])
async def list_grants(
    context: RequireAccessContext, container: RequireContainer
) -> list[SupportGrantModel]:
    """`support.grant`: every grant of this workspace. `support.request` only:
    the caller's own requests."""
    return [_model(v) for v in await _service(container).list_grants(context)]


@router.post("/grants", response_model=SupportGrantModel, status_code=201)
async def request_grant(
    body: RequestSupportGrantBody,
    context: RequireAccessContext,
    container: RequireContainer,
    idempotency: RequireIdempotency,
) -> SupportGrantModel:
    """With `support.grant` the grant is made (`pending_assignment`); with only
    `support.request` it waits for a granter (`pending_approval`)."""
    view = await _service(container).request(
        context,
        RequestSupportGrant(
            scope_set_key=body.scope_set_key,
            resource_type=body.resource_type,
            resource_id=body.resource_id,
            reason=body.reason,
            duration_hours=body.duration_hours,
        ),
    )
    return await idempotency.record(_model(view), status_code=201)


@router.post("/grants/{grant_id}/approve", response_model=SupportGrantModel)
async def approve_grant(
    grant_id: UUID,
    context: RequireAccessContext,
    container: RequireContainer,
    idempotency: RequireIdempotency,
) -> SupportGrantModel:
    view = await _service(container).approve(context, grant_id)
    return await idempotency.record(_model(view))


@router.post("/grants/{grant_id}/reject", response_model=SupportGrantModel)
async def reject_grant(
    grant_id: UUID,
    body: RejectSupportGrantBody,
    context: RequireAccessContext,
    container: RequireContainer,
    idempotency: RequireIdempotency,
) -> SupportGrantModel:
    view = await _service(container).reject(context, grant_id, body.reason)
    return await idempotency.record(_model(view))


@router.post("/grants/{grant_id}/revoke", response_model=SupportGrantModel)
async def revoke_grant(
    grant_id: UUID,
    context: RequireAccessContext,
    container: RequireContainer,
    idempotency: RequireIdempotency,
) -> SupportGrantModel:
    """Takes effect at the staff member's next request."""
    view = await _service(container).revoke(context, grant_id)
    return await idempotency.record(_model(view))


# ---- the staff side (ticket 02) ---------------------------------------------


class MySupportGrantModel(BaseModel):
    """A grant assigned to the caller: enough to open it, nothing the customer
    wrote (no reason)."""

    id: UUID
    code: str
    tenant_id: UUID
    tenant_name: str
    workspace_id: UUID
    workspace_name: str
    resource_type: str
    resource_id: UUID | None
    resource_label: str
    scope_set_key: str
    scope_set_label: str
    # `active`, `expired`, `ineffective` or `revoked`, by the same rule the
    # support context applies on every request.
    state: str
    activated_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


@router.get("/my-grants", response_model=list[MySupportGrantModel])
async def my_grants(
    identity: RequireVerifiedIdentity,
    container: RequireContainer,
) -> list[MySupportGrantModel]:
    """The caller's grants, in force or ended in the last 30 days. Support
    staff only (403 `support_staff_required`); no tenant header is read."""
    if (
        container.identity_bootstrap is None
        or container.support_access is None
        or container.staff_grants is None
    ):
        raise InfrastructureError("support access is not configured")
    view = await container.identity_bootstrap.bootstrap(identity)
    if not view.is_support_staff:
        raise support_access_refused(SupportRefusal.STAFF_REQUIRED, "support staff only")
    rows = await container.staff_grants.grants_for_staff(view.principal_id)
    return [
        MySupportGrantModel(
            id=row.grant.id,
            code=row.grant.code,
            tenant_id=row.grant.tenant_id,
            tenant_name=row.tenant_name,
            workspace_id=row.grant.workspace_id,
            workspace_name=row.workspace_name,
            resource_type=row.grant.resource_type,
            resource_id=row.grant.resource_id,
            resource_label=row.grant.resource_label,
            scope_set_key=row.grant.scope_set_key,
            scope_set_label=row.grant.scope_set_label,
            state=await container.support_access.state_of(row.grant),
            activated_at=row.grant.activated_at,
            expires_at=row.grant.expires_at,
            revoked_at=row.grant.revoked_at,
        )
        for row in rows
    ]
