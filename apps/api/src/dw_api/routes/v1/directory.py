"""GET /api/v1/directory/members — the people in the caller's workspace.

Read-only, and scoped by construction: the access context already proves
membership of exactly one workspace, and the query filters on that workspace, so
there is no id for a caller to substitute.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from dw_api.dependencies.auth import RequireAccessContext
from dw_api.dependencies.services import RequireContainer
from dw_kernel.errors import InfrastructureError


class WorkspaceMemberView(BaseModel):
    user_id: UUID
    display_name: str
    email: str | None
    role_keys: list[str]
    permission_set_keys: list[str]
    department: str
    # `invited` until the first sign-in, then `active`.
    status: str


class IdentityRefView(BaseModel):
    user_id: UUID
    display_name: str
    email: str | None


router = APIRouter(prefix="/directory", tags=["identity"])


@router.get("/members", response_model=list[WorkspaceMemberView])
async def list_members(
    context: RequireAccessContext,
    container: RequireContainer,
) -> list[WorkspaceMemberView]:
    if container.workspace_directory is None:
        raise InfrastructureError("database is not configured")
    await container.authorization.require(
        context=context, action="directory.read", resource_type="workspace_member"
    )
    members = await container.workspace_directory.list_members(context)
    return [
        WorkspaceMemberView(
            user_id=member.user_id,
            display_name=member.display_name,
            email=member.email,
            role_keys=list(member.role_keys),
            permission_set_keys=list(member.permission_set_keys),
            department=member.department,
            status=member.status,
        )
        for member in members
    ]


@router.get("/candidates", response_model=list[IdentityRefView])
async def list_candidates(
    context: RequireAccessContext,
    container: RequireContainer,
) -> list[IdentityRefView]:
    """Signed-in identities an admin can grant into the workspace — the picker
    behind the 'add member' email box, so an already-known account is chosen,
    not retyped."""
    if container.workspace_directory is None:
        raise InfrastructureError("database is not configured")
    await container.authorization.require(
        context=context, action="platform.members.write", resource_type="workspace_member"
    )
    candidates = await container.workspace_directory.list_candidates(context)
    return [
        IdentityRefView(user_id=c.user_id, display_name=c.display_name, email=c.email)
        for c in candidates
    ]
