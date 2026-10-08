"""Org Admin membership management: /api/v1/admin/members and /admin/invitations.

Grant and revoke a person's access to a workspace of the caller's tenant; list
everyone in the tenant with their roles per workspace; replace a person's roles
in several workspaces at once; invite someone who has never signed in. The
handlers own every rule (scope, tenant RLS, no privilege escalation, audit,
administrative roles kept, support staff refused); the route only shapes the
request and lets a domain error map to its status.

Refusals the web branches on: 409 with `details.reason_code`
`member_email_exists_in_tenant` or `support_staff_not_member`; 404 for a person
or workspace outside the tenant, or an unknown role (`details.roles`); 403 for
an administrative role asked by a non-Platform-Admin.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from dw_api.bootstrap import ApiContainer
from dw_api.dependencies.auth import RequireAccessContext
from dw_api.dependencies.idempotency import RequireIdempotency
from dw_api.dependencies.services import RequireContainer
from dw_kernel.errors import InfrastructureError
from dw_platform.application.cache import membership_cache_pattern
from dw_platform.application.membership_admin import GrantMembership, RevokeMembership
from dw_platform.application.tenant_members import (
    InviteMember,
    SetMemberships,
    TenantMember,
    TenantMembersService,
    WorkspaceRoles,
)

router = APIRouter(prefix="/admin/members", tags=["admin"])
invitations_router = APIRouter(prefix="/admin/invitations", tags=["admin"])


class GrantMemberRequest(BaseModel):
    email: str
    workspace_id: UUID
    role_keys: list[str] = Field(min_length=1)
    department: str = "general"


class MemberRefView(BaseModel):
    user_id: UUID
    email: str | None
    display_name: str


# Both routes below honour `Idempotency-Key` (optional; see README). Granting
# and revoking access are the mutations on this API whose duplicate is a
# security event rather than a nuisance: each one writes an audit record and
# invalidates every cached AccessContext in the workspace, so a client that
# retried a timed-out call used to leave a second "who granted what" entry
# behind with nothing to tie the two together.
@router.post("", response_model=MemberRefView, status_code=201)
async def grant_member(
    body: GrantMemberRequest,
    context: RequireAccessContext,
    container: RequireContainer,
    idempotency: RequireIdempotency,
) -> MemberRefView:
    if container.grant_membership is None:
        raise InfrastructureError("database is not configured")
    ref = await container.grant_membership.handle(
        context,
        GrantMembership(
            email=body.email,
            workspace_id=body.workspace_id,
            role_keys=frozenset(body.role_keys),
            department=body.department,
        ),
    )
    # The grant changed this workspace's roles → drop its cached AccessContexts so
    # the new access takes effect at once, not after the TTL.
    if container.cache is not None:
        await container.cache.delete_pattern(
            membership_cache_pattern(context.tenant_id, body.workspace_id)
        )
    return await idempotency.record(
        MemberRefView(user_id=ref.user_id, email=ref.email, display_name=ref.display_name),
        status_code=201,
    )


@router.delete("/{user_id}", status_code=204)
async def revoke_member(
    user_id: UUID,
    workspace_id: UUID,
    context: RequireAccessContext,
    container: RequireContainer,
    idempotency: RequireIdempotency,
) -> None:
    if container.revoke_membership is None:
        raise InfrastructureError("database is not configured")
    await container.revoke_membership.handle(
        context, RevokeMembership(user_id=user_id, workspace_id=workspace_id)
    )
    if container.cache is not None:
        await container.cache.delete_pattern(
            membership_cache_pattern(context.tenant_id, workspace_id)
        )
    await idempotency.record_no_content()


class MemberWorkspaceView(BaseModel):
    workspace_id: UUID
    workspace_name: str
    role_keys: list[str]


class TenantMemberView(BaseModel):
    user_id: UUID
    display_name: str
    email: str | None
    # `invited` until the person's first sign-in, then `active`.
    status: str
    memberships: list[MemberWorkspaceView]


class MemberMembershipsView(BaseModel):
    """A person's memberships in this tenant after a change; empty when none is left."""

    user_id: UUID
    memberships: list[MemberWorkspaceView]


class WorkspaceRolesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    role_keys: list[str]


class SetMembershipsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memberships: list[WorkspaceRolesBody]


class InviteMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=4, max_length=120)
    email: str = Field(min_length=3, max_length=320)
    memberships: list[WorkspaceRolesBody] = Field(min_length=1)


def _members(container: ApiContainer) -> TenantMembersService:
    if container.tenant_members is None:
        raise InfrastructureError("database is not configured")
    return container.tenant_members


def _member_view(member: TenantMember) -> TenantMemberView:
    return TenantMemberView(
        user_id=member.user_id,
        display_name=member.display_name,
        email=member.email,
        status=member.status,
        memberships=[
            MemberWorkspaceView(
                workspace_id=m.workspace_id,
                workspace_name=m.workspace_name,
                role_keys=list(m.role_keys),
            )
            for m in member.memberships
        ],
    )


def _rows(body: list[WorkspaceRolesBody]) -> tuple[WorkspaceRoles, ...]:
    return tuple(
        WorkspaceRoles(workspace_id=row.workspace_id, role_keys=frozenset(row.role_keys))
        for row in body
    )


async def _drop_cached_contexts(
    container: ApiContainer, tenant_id: UUID, workspace_ids: list[UUID]
) -> None:
    if container.cache is not None:
        for workspace_id in workspace_ids:
            await container.cache.delete_pattern(membership_cache_pattern(tenant_id, workspace_id))


@router.get("", response_model=list[TenantMemberView])
async def list_members(
    context: RequireAccessContext, container: RequireContainer
) -> list[TenantMemberView]:
    """Everyone in the caller's tenant, by name (Vietnamese collation), with
    their roles in each workspace."""
    return [_member_view(m) for m in await _members(container).list_members(context)]


@router.put("/{user_id}/memberships", response_model=MemberMembershipsView)
async def set_memberships(
    user_id: UUID,
    body: SetMembershipsRequest,
    context: RequireAccessContext,
    container: RequireContainer,
    idempotency: RequireIdempotency,
) -> MemberMembershipsView:
    """Replace the person's non-administrative roles, workspace by workspace,
    in one transaction. A workspace in the body without a membership gets one;
    one left out keeps only its administrative roles, and goes without any.
    Answers the person's memberships as they now are (empty: none left)."""
    service = _members(container)
    changes = await service.set_memberships(
        context, SetMemberships(user_id=user_id, memberships=_rows(body.memberships))
    )
    await _drop_cached_contexts(container, context.tenant_id, [c.workspace_id for c in changes])
    now = [m for m in await service.list_members(context) if m.user_id == user_id]
    return await idempotency.record(
        MemberMembershipsView(
            user_id=user_id,
            memberships=_member_view(now[0]).memberships if now else [],
        )
    )


@invitations_router.post("", response_model=MemberRefView, status_code=201)
async def invite_member(
    body: InviteMemberRequest,
    context: RequireAccessContext,
    container: RequireContainer,
    idempotency: RequireIdempotency,
) -> MemberRefView:
    """Create the person (no sign-in yet) and their memberships. No email is
    sent: the admin tells the person, who then signs in with that email."""
    ref = await _members(container).invite(
        context,
        InviteMember(
            display_name=body.display_name, email=body.email, memberships=_rows(body.memberships)
        ),
    )
    await _drop_cached_contexts(
        container, context.tenant_id, [row.workspace_id for row in body.memberships]
    )
    return await idempotency.record(
        MemberRefView(user_id=ref.user_id, email=ref.email, display_name=ref.display_name),
        status_code=201,
    )
