"""Org Admin grants and revokes membership within their tenant.

The authority split (ADR-001): an Org Admin administers *who is in their tenant*
and *what they may do there*, but is not a super admin. Two rails keep that true:

- Scope: every entry point requires ``platform.members.read`` / ``.write`` —
  an Org Admin has these, a plain member does not, a Platform Admin bypasses.
- Tenant: the repository runs under the caller's tenant RLS, so a grant lands in
  the admin's tenant and nowhere else. An Org Admin of FDX cannot touch FIS.

A third rail stops privilege escalation: only a Platform Admin may grant an
*administrative* role — one carrying any ``platform.*`` scope (platform_admin,
org_admin). An Org Admin onboards business roles (sales, manager, AM…) in their
tenant but cannot mint another admin; without this, ``platform.members.write``
would be a route to platform_admin. The test is the role's own scopes, so a new
admin role added to the catalog is covered without touching this code.

The grant/revoke and its audit record land in one transaction: the repository
writes both under the caller's tenant, so there is never a change without its
audit trail.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from dw_kernel.errors import NotFoundError, PermissionDeniedError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import PLATFORM_ADMIN_ROLE
from dw_platform.application.ports import AuthorizationPort
from dw_platform.domain.audit import AuditEvent

MEMBERS_READ = "platform.members.read"
MEMBERS_WRITE = "platform.members.write"

# Audit action verbs; the resource is the target user.
_ACTION_GRANT = "platform.membership.grant"
_ACTION_REVOKE = "platform.membership.revoke"
_RESOURCE = "membership"


# A role is administrative when it carries any scope under this prefix
# (platform_admin, org_admin). Read by `forbid_escalation` and by the
# per-workspace role replacement, which keeps administrative roles as they are.
ADMIN_SCOPE_PREFIX = "platform."


def is_administrative(scopes: frozenset[str]) -> bool:
    return any(scope.startswith(ADMIN_SCOPE_PREFIX) for scope in scopes)


def forbid_escalation(context: AccessContext, granted_scopes: frozenset[str]) -> None:
    """Only a Platform Admin may hand out an administrative role."""
    if PLATFORM_ADMIN_ROLE in context.roles:
        return  # a super admin may grant anything
    administrative = {s for s in granted_scopes if s.startswith(ADMIN_SCOPE_PREFIX)}
    if administrative:
        raise PermissionDeniedError(
            "only a platform admin may grant an administrative role",
            details={"scopes": sorted(administrative)},
        )


@dataclass(frozen=True, slots=True)
class UserRef:
    """A platform identity an admin can act on."""

    user_id: uuid.UUID
    email: str | None
    display_name: str


@dataclass(frozen=True, slots=True)
class GrantMembership:
    """Give an existing identity access to one workspace with a set of roles."""

    email: str
    workspace_id: uuid.UUID
    role_keys: frozenset[str]
    department: str = "general"


@dataclass(frozen=True, slots=True)
class RevokeMembership:
    """Remove an identity's access to one workspace of the caller's tenant."""

    user_id: uuid.UUID
    workspace_id: uuid.UUID


class MembershipAdminRepositoryPort(Protocol):
    """Reads/writes membership on the admin's tenant (under its RLS)."""

    async def find_user_by_email(self, email: str) -> UserRef | None:
        """Identity plane, no tenant scope: locate the user to be granted."""
        ...

    async def scopes_for_roles(self, role_keys: frozenset[str]) -> frozenset[str]:
        """The union of scopes the given roles carry."""
        ...

    async def known_roles(self, role_keys: frozenset[str]) -> frozenset[str]:
        """Which of the given role keys actually exist in the catalog."""
        ...

    async def grant(
        self,
        context: AccessContext,
        *,
        user_id: uuid.UUID,
        workspace_id: uuid.UUID,
        role_keys: frozenset[str],
        department: str,
        audit: AuditEvent,
    ) -> None:
        """Insert-or-update the membership and record ``audit`` in the same
        transaction. Verifies the workspace is in the caller's tenant; RLS keeps
        the write there."""
        ...

    async def revoke(
        self,
        context: AccessContext,
        *,
        user_id: uuid.UUID,
        workspace_id: uuid.UUID,
        audit: AuditEvent,
    ) -> bool:
        """Remove the membership and, if there was one, record ``audit`` in the
        same transaction. Returns False if there was none to remove."""
        ...


@dataclass(frozen=True)
class GrantMembershipHandler:
    repo: MembershipAdminRepositoryPort
    authz: AuthorizationPort
    clock: UtcClock
    id_generator: IdGenerator

    async def handle(self, context: AccessContext, command: GrantMembership) -> UserRef:
        await self.authz.require(context=context, action=MEMBERS_WRITE, resource_type=_RESOURCE)
        if not command.role_keys:
            raise PermissionDeniedError("a membership needs at least one role")

        unknown = command.role_keys - await self.repo.known_roles(command.role_keys)
        if unknown:
            raise NotFoundError("unknown role", details={"roles": sorted(unknown)})

        await self._forbid_escalation(context, command.role_keys)

        user = await self.repo.find_user_by_email(command.email)
        if user is None:
            # The person must have signed in once (identity created, default-deny)
            # before an admin can place them. Say what to do next.
            raise NotFoundError(
                "no user with that email has signed in yet",
                details={"email": command.email},
            )

        await self.repo.grant(
            context,
            user_id=user.user_id,
            workspace_id=command.workspace_id,
            role_keys=command.role_keys,
            department=command.department,
            audit=self._event(
                context,
                _ACTION_GRANT,
                user.user_id,
                command.workspace_id,
                {"roles": sorted(command.role_keys), "department": command.department},
            ),
        )
        return user

    async def _forbid_escalation(self, context: AccessContext, role_keys: frozenset[str]) -> None:
        forbid_escalation(context, await self.repo.scopes_for_roles(role_keys))

    def _event(
        self,
        context: AccessContext,
        action: str,
        user_id: uuid.UUID,
        workspace_id: uuid.UUID,
        details: dict[str, object],
    ) -> AuditEvent:
        return AuditEvent(
            id=self.id_generator.new_uuid(),
            tenant_id=TenantId(context.tenant_id),
            workspace_id=WorkspaceId(workspace_id),
            actor_id=UserId(context.principal_id),
            action=action,
            resource_type=_RESOURCE,
            resource_id=str(user_id),
            occurred_at=self.clock.now(),
            details=details,
        )


@dataclass(frozen=True)
class RevokeMembershipHandler:
    repo: MembershipAdminRepositoryPort
    authz: AuthorizationPort
    clock: UtcClock
    id_generator: IdGenerator

    async def handle(self, context: AccessContext, command: RevokeMembership) -> None:
        await self.authz.require(context=context, action=MEMBERS_WRITE, resource_type=_RESOURCE)
        removed = await self.repo.revoke(
            context,
            user_id=command.user_id,
            workspace_id=command.workspace_id,
            audit=AuditEvent(
                id=self.id_generator.new_uuid(),
                tenant_id=TenantId(context.tenant_id),
                workspace_id=WorkspaceId(command.workspace_id),
                actor_id=UserId(context.principal_id),
                action=_ACTION_REVOKE,
                resource_type=_RESOURCE,
                resource_id=str(command.user_id),
                occurred_at=self.clock.now(),
            ),
        )
        if not removed:
            raise NotFoundError(
                "no such membership in this tenant",
                details={"user_id": str(command.user_id)},
            )
