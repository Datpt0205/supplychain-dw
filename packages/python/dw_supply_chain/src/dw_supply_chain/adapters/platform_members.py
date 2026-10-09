"""`MemberDirectoryPort` over the platform's own member services (ADR 0027
point 5; ticket onboarding/01): an imported user goes through the same path
an Org Admin's invitation does, so the platform checks `platform.members.*`,
refuses an administrative role from anyone but a Platform Admin, audits each
membership, and links the person's sign-in by verified email the first time
they log in. Nothing here decides who may do what.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from dw_kernel.errors import ConflictError
from dw_platform.application.access_context import AccessContext
from dw_platform.application.admin_console import AdminConsoleService
from dw_platform.application.membership_admin import is_administrative
from dw_platform.application.tenant_members import (
    InviteMember,
    TenantMembersService,
    WorkspaceRoles,
)
from dw_supply_chain.application.data_import import WorkspaceRef

_ALREADY_A_MEMBER = "member_email_exists_in_tenant"


@dataclass(frozen=True)
class PlatformMemberDirectory:
    """Implements `MemberDirectoryPort`."""

    console: AdminConsoleService
    members: TenantMembersService

    async def workspaces(self, context: AccessContext) -> list[WorkspaceRef]:
        return [
            WorkspaceRef(id=w.workspace_id, slug=w.slug, name=w.name)
            for w in await self.console.list_workspaces(context)
            if not w.archived
        ]

    async def assignable_roles(self, context: AccessContext) -> frozenset[str]:
        return frozenset(
            role.key
            for role in await self.console.list_roles(context)
            if not is_administrative(frozenset(role.scopes))
        )

    async def member_emails(self, context: AccessContext) -> frozenset[str]:
        return frozenset(
            m.email.lower() for m in await self.members.list_members(context) if m.email
        )

    async def invite(
        self,
        context: AccessContext,
        *,
        display_name: str,
        email: str,
        memberships: Mapping[uuid.UUID, frozenset[str]],
    ) -> bool:
        try:
            await self.members.invite(
                context,
                InviteMember(
                    display_name=display_name,
                    email=email,
                    memberships=tuple(
                        WorkspaceRoles(workspace_id=ws, role_keys=roles)
                        for ws, roles in memberships.items()
                    ),
                ),
            )
        except ConflictError as exc:
            if exc.details.get("reason_code") == _ALREADY_A_MEMBER:
                return False
            raise
        return True


__all__ = ["PlatformMemberDirectory"]
