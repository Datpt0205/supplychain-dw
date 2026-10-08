"""Separation of duties as an Org Admin sees it: the rules, and waivers.

The rules and their enforcement belong to the database (the trigger on
`platform.memberships`, migrations b9862fa13a80 and 6b26771e549d). Nothing
here decides whether a membership breaks a rule, or whether a rule may be
waived; those answers come back from the database as refusals. This service
only authorizes the caller, insists on a reason, and puts an audit event
beside every waiver it grants or revokes.

A waiver is how a company too small to staff both sides of a rule lifts it,
on the record: who, when, and why, for the whole tenant. It takes two people
(migration f381f1694395): one proposes it, a DIFFERENT holder of the waiver
scope confirms it with a reason, and until then it lifts nothing. The database
refuses a confirmation by the proposer; this service only reads that back.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from dw_kernel.errors import DomainError, NotFoundError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.admin_console import ROLES_READ
from dw_platform.application.ports import AuthorizationPort
from dw_platform.domain.audit import AuditEvent

SOD_WAIVERS_WRITE = "platform.sod_waivers.write"

_RESOURCE = "sod_rule"
_ACTION_WAIVE = "platform.sod.waive"
_ACTION_REVOKE = "platform.sod.waiver_revoke"
_ACTION_CONFIRM = "platform.sod.waiver_confirm"


@dataclass(frozen=True, slots=True)
class SodWaiver:
    reason: str
    granted_by: uuid.UUID
    granted_at: datetime
    # None while it waits for a second person; it lifts nothing until then.
    confirmed_by: uuid.UUID | None = None
    confirmed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SodRuleStatus:
    """A rule, and the caller's tenant's open waiver of it if there is one."""

    key: str
    description: str
    left_scopes: tuple[str, ...]
    right_scopes: tuple[str, ...]
    waivable: bool
    waiver: SodWaiver | None


@dataclass(frozen=True, slots=True)
class WaiveRule:
    rule_key: str
    reason: str


@dataclass(frozen=True, slots=True)
class RevokeWaiver:
    rule_key: str
    reason: str


@dataclass(frozen=True, slots=True)
class ConfirmWaiver:
    rule_key: str
    reason: str


class SeparationOfDutiesRepositoryPort(Protocol):
    async def list_rules(self, context: AccessContext) -> list[SodRuleStatus]: ...

    async def waive(
        self,
        context: AccessContext,
        *,
        waiver_id: uuid.UUID,
        rule_key: str,
        reason: str,
        audit: AuditEvent,
    ) -> None:
        """Record an open waiver for the caller's tenant. Raises
        ``NotFoundError`` for an unknown rule and ``ConflictError`` when the
        rule cannot be waived or already is."""
        ...

    async def revoke(
        self, context: AccessContext, *, rule_key: str, reason: str, audit: AuditEvent
    ) -> bool:
        """Close the tenant's open waiver; False if there is none. Raises
        ``ConflictError`` while memberships still rely on it."""
        ...

    async def confirm(
        self, context: AccessContext, *, rule_key: str, reason: str, audit: AuditEvent
    ) -> bool:
        """Confirm the tenant's open, unconfirmed waiver as the caller; False if
        there is none. Raises ``ConflictError`` when the caller proposed it."""
        ...


@dataclass(frozen=True)
class SeparationOfDutiesService:
    repo: SeparationOfDutiesRepositoryPort
    authz: AuthorizationPort
    clock: UtcClock
    id_generator: IdGenerator

    async def list_rules(self, context: AccessContext) -> list[SodRuleStatus]:
        await self.authz.require(context=context, action=ROLES_READ, resource_type=_RESOURCE)
        return await self.repo.list_rules(context)

    async def waive(self, context: AccessContext, command: WaiveRule) -> None:
        await self.authz.require(context=context, action=SOD_WAIVERS_WRITE, resource_type=_RESOURCE)
        reason = _reason(command.reason)
        await self.repo.waive(
            context,
            waiver_id=self.id_generator.new_uuid(),
            rule_key=command.rule_key,
            reason=reason,
            audit=self._event(context, _ACTION_WAIVE, command.rule_key, reason),
        )

    async def revoke(self, context: AccessContext, command: RevokeWaiver) -> None:
        await self.authz.require(context=context, action=SOD_WAIVERS_WRITE, resource_type=_RESOURCE)
        reason = _reason(command.reason)
        revoked = await self.repo.revoke(
            context,
            rule_key=command.rule_key,
            reason=reason,
            audit=self._event(context, _ACTION_REVOKE, command.rule_key, reason),
        )
        if not revoked:
            raise NotFoundError(
                "this tenant has no open waiver of that rule",
                details={"rule_key": command.rule_key},
            )

    async def confirm(self, context: AccessContext, command: ConfirmWaiver) -> None:
        await self.authz.require(context=context, action=SOD_WAIVERS_WRITE, resource_type=_RESOURCE)
        reason = _reason(command.reason)
        confirmed = await self.repo.confirm(
            context,
            rule_key=command.rule_key,
            reason=reason,
            audit=self._event(context, _ACTION_CONFIRM, command.rule_key, reason),
        )
        if not confirmed:
            raise NotFoundError(
                "this tenant has no waiver of that rule waiting for confirmation",
                details={"rule_key": command.rule_key},
            )

    def _event(self, context: AccessContext, action: str, rule_key: str, reason: str) -> AuditEvent:
        return AuditEvent(
            id=self.id_generator.new_uuid(),
            tenant_id=TenantId(context.tenant_id),
            workspace_id=WorkspaceId(context.workspace_id),
            actor_id=UserId(context.principal_id),
            action=action,
            resource_type=_RESOURCE,
            resource_id=rule_key,
            occurred_at=self.clock.now(),
            details={"reason": reason},
        )


def _reason(reason: str) -> str:
    stripped = reason.strip()
    if not stripped:
        raise DomainError("a waiver decision needs a reason")
    return stripped
