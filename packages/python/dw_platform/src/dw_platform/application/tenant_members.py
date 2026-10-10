"""Everyone in a tenant, their roles per workspace, and invitations.

An Org Admin sees every person of their tenant with their roles in each
workspace, replaces a person's roles in several workspaces in one save, and
invites someone who has never signed in. The rails of `membership_admin` hold
here unchanged: `platform.members.read` / `.write`, the caller's tenant RLS,
no administrative role handed out by anyone but a Platform Admin
(`forbid_escalation`), and one audit event per change in the same transaction.

Two rules are this module's own:

- **Replacing roles never touches an administrative role** (one carrying a
  `platform.*` scope). The roles a workspace ends with are the roles asked for
  plus every administrative role the person already holds there
  (`plan_memberships`); a workspace left out of the request keeps only its
  administrative roles, and goes when it has none.
- **An invitation creates the person, not their sign-in.** It creates a
  `platform.users` row (display name, lower-cased email) and no external
  identity: the person reads as `invited` until their first sign-in links a
  verified identity to that row by email (`identity_provisioning.py`).
  **No email is sent**: the admin tells the person outside the product.
  Sending one is P1.

A support staff member never becomes a member (ADR 0024): the database
refuses the membership on every path and the repository answers 409
`support_staff_not_member`.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from dw_kernel.errors import DomainError, NotFoundError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.membership_admin import (
    MEMBERS_READ,
    MEMBERS_WRITE,
    UserRef,
    forbid_escalation,
    is_administrative,
)
from dw_platform.application.ports import AuthorizationPort
from dw_platform.domain.audit import AuditEvent

MemberStatus = Literal["invited", "active"]

_ACTION_GRANT = "platform.membership.grant"
_ACTION_REVOKE = "platform.membership.revoke"
_ACTION_INVITED = "platform.member.invited"
_RESOURCE = "membership"
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


@dataclass(frozen=True, slots=True)
class MemberWorkspace:
    workspace_id: uuid.UUID
    workspace_name: str
    role_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TenantMember:
    user_id: uuid.UUID
    display_name: str
    email: str | None
    status: MemberStatus
    memberships: tuple[MemberWorkspace, ...]


@dataclass(frozen=True, slots=True)
class WorkspaceRoles:
    workspace_id: uuid.UUID
    role_keys: frozenset[str]


@dataclass(frozen=True, slots=True)
class SetMemberships:
    user_id: uuid.UUID
    memberships: tuple[WorkspaceRoles, ...]


@dataclass(frozen=True, slots=True)
class InviteMember:
    display_name: str
    email: str
    memberships: tuple[WorkspaceRoles, ...]


@dataclass(frozen=True, slots=True)
class MembershipChange:
    """One workspace whose roles changed: `before` empty is a new membership,
    `after` empty a removed one."""

    workspace_id: uuid.UUID
    before: frozenset[str]
    after: frozenset[str]


Roles = Mapping[uuid.UUID, frozenset[str]]


def plan_memberships(
    current: Roles, desired: Roles, administrative: frozenset[str]
) -> dict[uuid.UUID, frozenset[str]]:
    """The roles each workspace ends with (empty: no membership).

    What was asked, plus every administrative role already held there. A
    workspace not asked about keeps its administrative roles and nothing else.
    """
    return {
        ws: (current.get(ws, frozenset()) & administrative) | desired.get(ws, frozenset())
        for ws in current.keys() | desired.keys()
    }


class TenantMembersRepositoryPort(Protocol):
    """Membership of the caller's tenant (its RLS), and the identity plane."""

    async def role_catalog(self) -> dict[str, frozenset[str]]:
        """Every role key with its scopes."""
        ...

    async def list_members(self, context: AccessContext) -> list[TenantMember]:
        """Everyone with a membership in the caller's tenant, by name."""
        ...

    async def replace_memberships(
        self,
        context: AccessContext,
        *,
        user_id: uuid.UUID,
        plan: Callable[[Roles], Roles],
        audit: Callable[[MembershipChange], AuditEvent],
    ) -> list[MembershipChange]:
        """In one transaction: read the person's memberships in this tenant
        (NotFoundError when there are none), apply `plan` to them, write the
        changes with one audit event each. NotFoundError for a planned
        workspace outside the tenant."""
        ...

    async def invite(
        self,
        context: AccessContext,
        *,
        display_name: str,
        email: str,
        memberships: Roles,
        audit: Callable[[uuid.UUID, uuid.UUID, frozenset[str]], AuditEvent],
    ) -> UserRef:
        """In one transaction: find the person by email or create them (no
        external identity), refuse one already in this tenant (409
        `member_email_exists_in_tenant`), create the memberships, audit each.
        NotFoundError for a workspace outside the tenant."""
        ...


@dataclass(frozen=True)
class TenantMembersService:
    repo: TenantMembersRepositoryPort
    authz: AuthorizationPort
    clock: UtcClock
    ids: IdGenerator

    async def list_members(self, context: AccessContext) -> list[TenantMember]:
        await self.authz.require(context=context, action=MEMBERS_READ, resource_type=_RESOURCE)
        return await self.repo.list_members(context)

    async def set_memberships(
        self, context: AccessContext, command: SetMemberships
    ) -> list[MembershipChange]:
        await self.authz.require(context=context, action=MEMBERS_WRITE, resource_type=_RESOURCE)
        desired = _by_workspace(command.memberships)
        administrative = await self._checked_roles(context, desired)
        return await self.repo.replace_memberships(
            context,
            user_id=command.user_id,
            plan=lambda current: plan_memberships(current, desired, administrative),
            audit=lambda change: self._change_event(context, command.user_id, change),
        )

    async def invite(self, context: AccessContext, command: InviteMember) -> UserRef:
        await self.authz.require(context=context, action=MEMBERS_WRITE, resource_type=_RESOURCE)
        display_name = command.display_name.strip()
        if not 4 <= len(display_name) <= 120:
            raise DomainError("a display name of 4 to 120 characters is required")
        email = command.email.strip().lower()
        if not _EMAIL.fullmatch(email):
            raise DomainError("a valid email is required")
        desired = {ws: roles for ws, roles in _by_workspace(command.memberships).items() if roles}
        if not desired:
            raise DomainError("an invitation needs at least one workspace with a role")
        await self._checked_roles(context, desired)
        return await self.repo.invite(
            context,
            display_name=display_name,
            email=email,
            memberships=desired,
            audit=lambda user_id, ws, roles: self._event(
                context,
                _ACTION_INVITED,
                user_id,
                ws,
                {"user_id": str(user_id), "roles": sorted(roles)},
            ),
        )

    async def _checked_roles(self, context: AccessContext, desired: Roles) -> frozenset[str]:
        """Refuse unknown roles (404) and administrative ones from a
        non-Platform-Admin (403); return the administrative role keys."""
        catalog = await self.repo.role_catalog()
        asked = frozenset(role for roles in desired.values() for role in roles)
        unknown = asked - catalog.keys()
        if unknown:
            raise NotFoundError("unknown role", details={"roles": sorted(unknown)})
        forbid_escalation(context, frozenset(s for role in asked for s in catalog[role]))
        return frozenset(key for key, scopes in catalog.items() if is_administrative(scopes))

    def _change_event(
        self, context: AccessContext, user_id: uuid.UUID, change: MembershipChange
    ) -> AuditEvent:
        if not change.after:
            return self._event(context, _ACTION_REVOKE, user_id, change.workspace_id, {})
        return self._event(
            context,
            _ACTION_GRANT,
            user_id,
            change.workspace_id,
            {"roles": sorted(change.after), "previous_roles": sorted(change.before)},
        )

    def _event(
        self,
        context: AccessContext,
        action: str,
        user_id: uuid.UUID,
        workspace_id: uuid.UUID,
        details: dict[str, object],
    ) -> AuditEvent:
        return AuditEvent(
            id=self.ids.new_uuid(),
            tenant_id=TenantId(context.tenant_id),
            workspace_id=WorkspaceId(workspace_id),
            actor_id=UserId(context.principal_id),
            action=action,
            resource_type=_RESOURCE,
            resource_id=str(user_id),
            occurred_at=self.clock.now(),
            details=details,
        )


def _by_workspace(rows: tuple[WorkspaceRoles, ...]) -> dict[uuid.UUID, frozenset[str]]:
    merged: dict[uuid.UUID, frozenset[str]] = {}
    for row in rows:
        if row.workspace_id in merged:
            raise DomainError(
                "a workspace appears twice", details={"workspace_id": str(row.workspace_id)}
            )
        merged[row.workspace_id] = row.role_keys
    return merged
