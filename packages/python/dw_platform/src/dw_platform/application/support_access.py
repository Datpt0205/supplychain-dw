"""Customer-granted support access (ADR 0024): the grant's lifecycle.

The customer asks for (`support.request`) or grants (`support.grant`) a
scoped, time-limited, revocable access to one workspace or one resource in
it; the operators choose the staff member who carries it (the provisioning
side, `ProvisioningService`); the staff member then acts under their own
identity with only the scopes stamped on the grant (the support access
context, a later slice).

The platform does not know a context's resources. A context registers, at the
composition root, the scope sets a customer may grant (`SupportScopeCatalog`)
and who names its resources (`SupportResourcePort`); narrowing reads to one
resource is that context's business.

Three rules here are the reason the feature exists, and each has one owner:

- **Grant only what you hold.** Granting checks the set's scopes against the
  granter's own scopes in that workspace. `support.*` is read literally from
  the caller's scopes: no role stands in for it, `platform_admin` included,
  because a grant's validity is re-derived later from the granter's
  membership scopes, where a role bypass does not exist (`grant_effective_state`).
- **The customer never picks the person.** Nothing here takes a staff id.
- **A grant is ended by any of: expiry, revocation, or the granter losing
  `support.grant` or a stamped scope** — `grant_effective_state`, read by the
  list here and by the support context, so the screen and the door agree.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from dw_kernel.errors import (
    ConfigError,
    ConflictError,
    DomainError,
    NotFoundError,
    PermissionDeniedError,
)
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext, SupportScope
from dw_platform.application.authorization import permission_denied
from dw_platform.domain.audit import AuditEvent

SUPPORT_GRANT_SCOPE = "support.grant"
SUPPORT_REQUEST_SCOPE = "support.request"
# In no plan: a tenant gets it through `entitlements.feature_overrides`.
SUPPORT_ACCESS_FEATURE = "support_access"
WORKSPACE_RESOURCE = "workspace"
MAX_DURATION_HOURS = 336
MAX_REASON_LENGTH = 300

# A support context must never carry these, whatever a context registers:
# deciding, reading runs, audit, knowledge, memory, the platform's own
# administration, support itself, and the directory. Until each read path has
# its own negative test under a support context, the safe set is none of them.
FORBIDDEN_SCOPE_PREFIXES = (
    "approvals.",
    "runs.",
    "audit.",
    "knowledge.",
    "memory.",
    "platform.",
    "support.",
    "directory.",
)

_KEY = re.compile(r"[a-z][a-z0-9_.]{0,63}")
_RESOURCE_TYPE = re.compile(r"[a-z_]{1,40}")

_RESOURCE = "support_grant"


class SupportRefusal(StrEnum):
    """`details.reason_code` of a support refusal: what the web branches on.

    The HTTP status and top-level `code` stay the platform's taxonomy
    (`permission_denied` 403, `conflict` 409, `validation_failed` 422); this
    says which refusal it was.
    """

    NOT_ENABLED = "support_access_not_enabled"
    SCOPE_NOT_HELD = "support_scope_not_held"
    SCOPE_SET_UNKNOWN = "support_scope_set_unknown"
    RESOURCE_TYPE_NOT_OFFERED = "support_resource_type_not_offered"
    WRONG_STATUS = "support_grant_wrong_status"
    STAFF_REQUIRED = "support_staff_required"
    STAFF_NOT_MEMBER = "support_staff_not_member"
    MFA_REQUIRED = "support_mfa_required"
    GRANT_ENDED = "support_grant_ended"
    CONTEXT_NOT_ALLOWED = "support_context_not_allowed"


class GrantStatus(StrEnum):
    """What is stored. `expired` and `ineffective` are never stored."""

    PENDING_APPROVAL = "pending_approval"
    PENDING_ASSIGNMENT = "pending_assignment"
    ACTIVE = "active"
    REJECTED = "rejected"
    REVOKED = "revoked"


EXPIRED = "expired"
INEFFECTIVE = "ineffective"
_OPEN = frozenset(
    {GrantStatus.PENDING_APPROVAL, GrantStatus.PENDING_ASSIGNMENT, GrantStatus.ACTIVE}
)


def support_staff_not_member() -> ConflictError:
    """The refusal every membership path gives a support staff member (SA9, TM3)."""
    return ConflictError(
        "a support staff member cannot be a member of a customer's tenant",
        details={"reason_code": SupportRefusal.STAFF_NOT_MEMBER.value},
    )


# ---- catalog ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SupportScopeSet:
    """A set of scopes a customer may grant, as a context registered it."""

    key: str
    label: str
    scopes: frozenset[str]
    resource_types: frozenset[str]


class SupportResourcePort(Protocol):
    """Names one resource of a context for a grant (consumer: the platform).

    ``None`` means the resource is not in the caller's workspace, and the
    request answers 404, the same as a resource that never existed. The
    platform names ``workspace`` itself.
    """

    async def describe(
        self, context: AccessContext, resource_type: str, resource_id: UUID
    ) -> str | None: ...


@dataclass
class SupportScopeCatalog:
    """The scope sets a customer may grant, registered at the composition root.

    Refuses a set carrying a scope from `FORBIDDEN_SCOPE_PREFIXES`, a key
    registered twice, and any registration after `freeze()`, which the
    composition root calls once every context has registered. A resource type
    other than ``workspace`` must have a `SupportResourcePort` by then, or the
    process does not start: a set offered for a resource nobody can name is a
    request that can only fail.
    """

    _sets: dict[str, SupportScopeSet] = field(default_factory=dict)
    _resources: dict[str, SupportResourcePort] = field(default_factory=dict)
    _frozen: bool = False

    def register(
        self,
        key: str,
        label: str,
        scopes: Iterable[str],
        resource_types: Iterable[str],
    ) -> None:
        self._refuse_when_frozen()
        if not _KEY.fullmatch(key):
            raise ConfigError("support scope set key is malformed", details={"key": key})
        if key in self._sets:
            raise ConfigError("support scope set registered twice", details={"key": key})
        if not label.strip():
            raise ConfigError("support scope set needs a label", details={"key": key})
        held = frozenset(scopes)
        if not held:
            raise ConfigError("support scope set has no scopes", details={"key": key})
        forbidden = sorted(s for s in held if s.startswith(FORBIDDEN_SCOPE_PREFIXES))
        if forbidden:
            raise ConfigError(
                "support scope set carries a scope support may never hold",
                details={"key": key, "scopes": forbidden},
            )
        types = frozenset(resource_types)
        if not types or not all(_RESOURCE_TYPE.fullmatch(t) for t in types):
            raise ConfigError(
                "support scope set needs resource types of [a-z_]{1,40}",
                details={"key": key, "resource_types": sorted(types)},
            )
        self._sets[key] = SupportScopeSet(
            key=key, label=label.strip(), scopes=held, resource_types=types
        )

    def register_resource(self, resource_type: str, describer: SupportResourcePort) -> None:
        self._refuse_when_frozen()
        if resource_type == WORKSPACE_RESOURCE or not _RESOURCE_TYPE.fullmatch(resource_type):
            raise ConfigError(
                "a context names its own resource types, never 'workspace'",
                details={"resource_type": resource_type},
            )
        if resource_type in self._resources:
            raise ConfigError(
                "support resource type registered twice", details={"resource_type": resource_type}
            )
        self._resources[resource_type] = describer

    def freeze(self) -> None:
        unnamed = sorted(
            {t for s in self._sets.values() for t in s.resource_types}
            - {WORKSPACE_RESOURCE}
            - set(self._resources)
        )
        if unnamed:
            raise ConfigError(
                "a support scope set names a resource type nobody describes",
                details={"resource_types": unnamed},
            )
        self._frozen = True

    def sets(self) -> list[SupportScopeSet]:
        return sorted(self._sets.values(), key=lambda s: s.key)

    def get(self, key: str) -> SupportScopeSet | None:
        return self._sets.get(key)

    def describer(self, resource_type: str) -> SupportResourcePort | None:
        return self._resources.get(resource_type)

    def _refuse_when_frozen(self) -> None:
        if self._frozen:
            raise ConfigError("the support scope catalog is frozen once the process is wired")


# ---- the grant -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SupportGrant:
    id: UUID
    code: str
    tenant_id: UUID
    workspace_id: UUID
    resource_type: str
    resource_id: UUID | None
    resource_label: str
    scope_set_key: str
    scope_set_label: str
    scopes: frozenset[str]
    reason: str
    duration_hours: int
    status: GrantStatus
    requested_by: UUID | None
    requested_at: datetime
    granted_by: UUID | None = None
    granted_at: datetime | None = None
    rejected_by: UUID | None = None
    rejected_at: datetime | None = None
    reject_reason: str | None = None
    staff_user_id: UUID | None = None
    assigned_by: UUID | None = None
    activated_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_by: UUID | None = None
    revoked_at: datetime | None = None


def grant_effective_state(
    grant: SupportGrant, now: datetime, grantor_scopes: frozenset[str]
) -> str:
    """What a grant is now: `active`, `expired`, `ineffective`, or its stored status.

    The one reading of "is this grant in force", for the customer's list and
    for the support context alike. `grantor_scopes` are the granter's current
    membership scopes in the grant's workspace (empty when the membership, the
    granter or an active tenant is gone). Expiry is checked first and is
    exclusive: at `expires_at` the grant has ended.
    """
    if grant.status is not GrantStatus.ACTIVE:
        return grant.status.value
    if grant.expires_at is None or now >= grant.expires_at:
        return EXPIRED
    if SUPPORT_GRANT_SCOPE not in grantor_scopes or not grant.scopes <= grantor_scopes:
        return INEFFECTIVE
    return GrantStatus.ACTIVE.value


@dataclass(frozen=True, slots=True)
class SupportGrantView:
    grant: SupportGrant
    state: str


@dataclass(frozen=True, slots=True)
class NewSupportGrant:
    """A grant as the customer's side inserts it; the code comes from the database."""

    id: UUID
    tenant_id: UUID
    workspace_id: UUID
    resource_type: str
    resource_id: UUID | None
    resource_label: str
    scope_set_key: str
    scope_set_label: str
    scopes: frozenset[str]
    reason: str
    duration_hours: int
    status: GrantStatus
    requested_by: UUID
    requested_at: datetime
    granted_by: UUID | None
    granted_at: datetime | None


@dataclass(frozen=True, slots=True)
class GrantChange:
    """One customer-side step: the new status and the columns that step writes."""

    status: GrantStatus
    granted_by: UUID | None = None
    granted_at: datetime | None = None
    rejected_by: UUID | None = None
    rejected_at: datetime | None = None
    reject_reason: str | None = None
    revoked_by: UUID | None = None
    revoked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RequestSupportGrant:
    scope_set_key: str
    resource_type: str
    resource_id: UUID | None
    reason: str
    duration_hours: int


AuditFor = Callable[[SupportGrant], AuditEvent]


class SupportGrantRepositoryPort(Protocol):
    """The customer's side of `platform.support_grants`, under the caller's
    tenant (RLS) AND workspace (every read and every write by id): a grant of
    another workspace reads as absent."""

    async def workspace_name(self, context: AccessContext) -> str | None: ...

    async def create(
        self, context: AccessContext, grant: NewSupportGrant, audit: AuditFor
    ) -> SupportGrant:
        """Insert and audit in one transaction; the code is assigned by the database."""
        ...

    async def get(
        self, context: AccessContext, grant_id: UUID, *, requested_by: UUID | None = None
    ) -> SupportGrant | None: ...

    async def list_grants(
        self, context: AccessContext, *, requested_by: UUID | None = None
    ) -> list[SupportGrant]:
        """Newest first. With `requested_by`, only that person's requests."""
        ...

    async def change(
        self,
        context: AccessContext,
        grant_id: UUID,
        *,
        expected: frozenset[GrantStatus],
        change: GrantChange,
        audit: AuditFor,
    ) -> SupportGrant | None:
        """Apply `change` if the grant is still in one of `expected`, with its
        audit, in one transaction. None when it was not (moved meanwhile)."""
        ...


class MemberScopesPort(Protocol):
    """A person's current membership scopes in one workspace, from their
    membership alone (no role stands in). Empty when there is no membership or
    the tenant is not active."""

    async def scopes_of(
        self, tenant_id: UUID, workspace_id: UUID, user_id: UUID
    ) -> frozenset[str]: ...


@dataclass(frozen=True)
class SupportGrantService:
    """The customer's use-cases: catalog, list, request or grant, approve,
    reject, revoke. Each write and its audit event land in one transaction."""

    repo: SupportGrantRepositoryPort
    member_scopes: MemberScopesPort
    catalog: SupportScopeCatalog
    clock: UtcClock
    ids: IdGenerator

    def catalog_for(self, context: AccessContext) -> list[SupportScopeSet]:
        _require_enabled(context)
        _side(context)
        return self.catalog.sets()

    async def list_grants(self, context: AccessContext) -> list[SupportGrantView]:
        _require_enabled(context)
        granter = _side(context) == SUPPORT_GRANT_SCOPE
        grants = await self.repo.list_grants(
            context, requested_by=None if granter else context.principal_id
        )
        return await self._views(grants)

    async def request(
        self, context: AccessContext, command: RequestSupportGrant
    ) -> SupportGrantView:
        _require_enabled(context)
        side = _side(context)
        scope_set = self.catalog.get(command.scope_set_key)
        if scope_set is None:
            raise DomainError(
                "no such support scope set",
                details={
                    "reason_code": SupportRefusal.SCOPE_SET_UNKNOWN.value,
                    "scope_set_key": command.scope_set_key,
                },
            )
        if command.resource_type not in scope_set.resource_types:
            raise DomainError(
                "this scope set is not offered for that resource type",
                details={
                    "reason_code": SupportRefusal.RESOURCE_TYPE_NOT_OFFERED.value,
                    "resource_types": sorted(scope_set.resource_types),
                },
            )
        reason = _checked_reason(command.reason)
        if not 1 <= command.duration_hours <= MAX_DURATION_HOURS:
            raise DomainError(
                "duration must be 1 to 336 hours",
                details={"duration_hours": command.duration_hours},
            )
        label = await self._label(context, command.resource_type, command.resource_id)
        now = self.clock.now()
        granting = side == SUPPORT_GRANT_SCOPE
        if granting:
            _require_holds(context, scope_set.scopes)
        new = NewSupportGrant(
            id=self.ids.new_uuid(),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            resource_type=command.resource_type,
            resource_id=command.resource_id,
            resource_label=label,
            scope_set_key=scope_set.key,
            scope_set_label=scope_set.label,
            scopes=scope_set.scopes,
            reason=reason,
            duration_hours=command.duration_hours,
            status=GrantStatus.PENDING_ASSIGNMENT if granting else GrantStatus.PENDING_APPROVAL,
            requested_by=context.principal_id,
            requested_at=now,
            granted_by=context.principal_id if granting else None,
            granted_at=now if granting else None,
        )
        grant = await self.repo.create(
            context, new, self._audit(context, "support.grant.requested", now)
        )
        return SupportGrantView(grant=grant, state=grant.status.value)

    async def approve(self, context: AccessContext, grant_id: UUID) -> SupportGrantView:
        _require_enabled(context)
        _require_granter(context)
        grant = await self._get(context, grant_id)
        _require_status(grant, {GrantStatus.PENDING_APPROVAL})
        _require_holds(context, grant.scopes)
        now = self.clock.now()
        return await self._change(
            context,
            grant_id,
            {GrantStatus.PENDING_APPROVAL},
            GrantChange(
                status=GrantStatus.PENDING_ASSIGNMENT,
                granted_by=context.principal_id,
                granted_at=now,
            ),
            "support.grant.approved",
            now,
        )

    async def reject(self, context: AccessContext, grant_id: UUID, reason: str) -> SupportGrantView:
        # Closing a request needs no feature flag: switching support off must
        # never leave the customer unable to say no.
        _require_granter(context)
        checked = _checked_reason(reason)
        grant = await self._get(context, grant_id)
        _require_status(grant, {GrantStatus.PENDING_APPROVAL})
        now = self.clock.now()
        return await self._change(
            context,
            grant_id,
            {GrantStatus.PENDING_APPROVAL},
            GrantChange(
                status=GrantStatus.REJECTED,
                rejected_by=context.principal_id,
                rejected_at=now,
                reject_reason=checked,
            ),
            "support.grant.rejected",
            now,
        )

    async def revoke(self, context: AccessContext, grant_id: UUID) -> SupportGrantView:
        # Like `reject`, never behind the flag. Takes effect at the next request.
        _require_granter(context)
        grant = await self._get(context, grant_id)
        _require_status(grant, _OPEN)
        now = self.clock.now()
        return await self._change(
            context,
            grant_id,
            _OPEN,
            GrantChange(
                status=GrantStatus.REVOKED, revoked_by=context.principal_id, revoked_at=now
            ),
            "support.grant.revoked",
            now,
        )

    async def _get(self, context: AccessContext, grant_id: UUID) -> SupportGrant:
        grant = await self.repo.get(context, grant_id)
        if grant is None:
            raise NotFoundError("support grant not found", details={"grant_id": str(grant_id)})
        return grant

    async def _change(
        self,
        context: AccessContext,
        grant_id: UUID,
        expected: set[GrantStatus] | frozenset[GrantStatus],
        change: GrantChange,
        action: str,
        now: datetime,
    ) -> SupportGrantView:
        changed = await self.repo.change(
            context,
            grant_id,
            expected=frozenset(expected),
            change=change,
            audit=self._audit(context, action, now),
        )
        if changed is None:
            # Moved by someone else between the read and the write.
            raise _wrong_status(None)
        return (await self._views([changed]))[0]

    async def _label(
        self, context: AccessContext, resource_type: str, resource_id: UUID | None
    ) -> str:
        if resource_type == WORKSPACE_RESOURCE:
            if resource_id is not None:
                raise DomainError("a workspace grant names no resource id")
            name = await self.repo.workspace_name(context)
        else:
            if resource_id is None:
                raise DomainError("this resource type needs a resource id")
            describer = self.catalog.describer(resource_type)
            name = (
                None
                if describer is None
                else await describer.describe(context, resource_type, resource_id)
            )
        if name is None:
            raise NotFoundError("resource not found in this workspace")
        return name[:200]

    async def _views(self, grants: list[SupportGrant]) -> list[SupportGrantView]:
        now = self.clock.now()
        held: dict[tuple[UUID, UUID], frozenset[str]] = {}
        views: list[SupportGrantView] = []
        for grant in grants:
            grantor_scopes: frozenset[str] = frozenset()
            if grant.status is GrantStatus.ACTIVE and grant.granted_by is not None:
                key = (grant.workspace_id, grant.granted_by)
                if key not in held:
                    held[key] = await self.member_scopes.scopes_of(
                        grant.tenant_id, grant.workspace_id, grant.granted_by
                    )
                grantor_scopes = held[key]
            views.append(
                SupportGrantView(
                    grant=grant, state=grant_effective_state(grant, now, grantor_scopes)
                )
            )
        return views

    def _audit(self, context: AccessContext, action: str, now: datetime) -> AuditFor:
        def build(grant: SupportGrant) -> AuditEvent:
            return support_grant_audit(
                event_id=self.ids.new_uuid(),
                grant=grant,
                actor_id=context.principal_id,
                action=action,
                occurred_at=now,
            )

        return build


def support_grant_audit(
    *,
    event_id: UUID,
    grant: SupportGrant,
    actor_id: UUID,
    action: str,
    occurred_at: datetime,
    extra: dict[str, object] | None = None,
) -> AuditEvent:
    """The tenant's audit event for one step of a grant: identifiers only,
    never the reason, so the trail never repeats what the customer wrote."""
    return AuditEvent(
        id=event_id,
        tenant_id=TenantId(grant.tenant_id),
        workspace_id=WorkspaceId(grant.workspace_id),
        actor_id=UserId(actor_id),
        action=action,
        resource_type=_RESOURCE,
        resource_id=str(grant.id),
        occurred_at=occurred_at,
        details={
            "support_grant_id": str(grant.id),
            "code": grant.code,
            "scope_set_key": grant.scope_set_key,
            "resource_type": grant.resource_type,
            "status": grant.status.value,
            **(extra or {}),
        },
    )


def _require_enabled(context: AccessContext) -> None:
    if not context.has_feature(SUPPORT_ACCESS_FEATURE):
        raise PermissionDeniedError(
            "support access is not enabled for this organisation",
            details={"reason_code": SupportRefusal.NOT_ENABLED.value},
        )


def _side(context: AccessContext) -> str:
    """Which side of support the caller is on: granting beats requesting."""
    if context.has_scope(SUPPORT_GRANT_SCOPE):
        return SUPPORT_GRANT_SCOPE
    if context.has_scope(SUPPORT_REQUEST_SCOPE):
        return SUPPORT_REQUEST_SCOPE
    raise permission_denied(action=SUPPORT_REQUEST_SCOPE, resource_type=_RESOURCE)


def _require_granter(context: AccessContext) -> None:
    if not context.has_scope(SUPPORT_GRANT_SCOPE):
        raise permission_denied(action=SUPPORT_GRANT_SCOPE, resource_type=_RESOURCE)


def _require_holds(context: AccessContext, scopes: frozenset[str]) -> None:
    """A granter hands over only scopes they hold themselves, in this workspace."""
    missing = scopes - context.scopes
    if missing:
        raise PermissionDeniedError(
            "you can only grant scopes you hold yourself",
            details={
                "reason_code": SupportRefusal.SCOPE_NOT_HELD.value,
                "scopes": sorted(missing),
            },
        )


def _require_status(
    grant: SupportGrant, allowed: set[GrantStatus] | frozenset[GrantStatus]
) -> None:
    if grant.status not in allowed:
        raise _wrong_status(grant.status)


def _wrong_status(status: GrantStatus | None) -> ConflictError:
    return ConflictError(
        "the support grant is not in a state that allows this",
        details={
            "reason_code": SupportRefusal.WRONG_STATUS.value,
            **({"status": status.value} if status is not None else {}),
        },
    )


def _checked_reason(reason: str) -> str:
    stripped = reason.strip()
    if not 1 <= len(stripped) <= MAX_REASON_LENGTH:
        raise DomainError("a reason of 1 to 300 characters is required")
    return stripped


# ---- the support context (ticket 02) ---------------------------------------

# RFC 8176 method names that mean a second factor. Keycloak 26.7 emits `otp`
# for an OTP execution that carries an authentication reference (measured
# 2026-10-08); `pwd` alone is one factor.
SECOND_FACTOR_METHODS = frozenset({"otp", "hwk", "mfa"})

SUPPORT_ACCESS_ACTION = "support.access"


@dataclass(frozen=True, slots=True)
class TenantPlan:
    plan_id: str
    feature_flags: frozenset[str]


class StaffGrantsPort(Protocol):
    """What the support context reads about a staff member and their grant,
    without being a member of the customer's tenant."""

    async def is_support_staff(self, user_id: UUID) -> bool: ...

    async def grant_for_staff(self, staff_user_id: UUID, grant_id: UUID) -> SupportGrant | None:
        """The grant, only when it is assigned to `staff_user_id`."""
        ...

    async def tenant_plan(self, tenant_id: UUID) -> TenantPlan | None: ...


@dataclass(frozen=True, slots=True)
class StaffGrantRow:
    """One line of a staff member's "my grants": assigned to them, in force or
    ended in the last 30 days. No reason: what the customer wrote is theirs."""

    grant: SupportGrant
    tenant_name: str
    workspace_name: str


class StaffGrantsListPort(Protocol):
    async def grants_for_staff(self, staff_user_id: UUID) -> list[StaffGrantRow]: ...


class SupportAccessAuditPort(Protocol):
    async def record_access(self, event: AuditEvent) -> None:
        """Append one `support.access` event to the customer's trail."""
        ...


def support_access_refused(
    reason: SupportRefusal, message: str, **details: object
) -> PermissionDeniedError:
    return PermissionDeniedError(message, details={"reason_code": reason.value, **details})


@dataclass(frozen=True)
class SupportAccessContextFactory:
    """A support staff member's access context, built from a customer's grant.

    Every step is checked on every request, in this order: the person is
    support staff, the token carries a second factor, the grant is assigned
    to them, it is in force now (`grant_effective_state`, the same reading as
    the customer's list), and the tenant has `support_access`. The context
    takes tenant and workspace from the grant, never from a header; it carries
    no role (so no role bypass, `platform_admin` included) and exactly the
    scopes stamped on the grant. It is never cached.
    """

    grants: StaffGrantsPort
    member_scopes: MemberScopesPort
    clock: UtcClock
    ids: IdGenerator

    def access_event(
        self, context: AccessContext, *, method: str, route: str, path_ids: dict[str, str]
    ) -> AuditEvent:
        return support_access_audit(
            event_id=self.ids.new_uuid(),
            context=context,
            method=method,
            route=route,
            path_ids=path_ids,
            occurred_at=self.clock.now(),
        )

    async def state_of(self, grant: SupportGrant) -> str:
        """The grant's effective state now (for "my grants"), by the same rule."""
        granter_scopes = (
            await self.member_scopes.scopes_of(
                grant.tenant_id, grant.workspace_id, grant.granted_by
            )
            if grant.granted_by is not None
            else frozenset()
        )
        return grant_effective_state(grant, self.clock.now(), granter_scopes)

    async def build(
        self, *, principal_id: UUID, auth_methods: frozenset[str], grant_id: UUID
    ) -> AccessContext:
        if not await self.grants.is_support_staff(principal_id):
            raise support_access_refused(SupportRefusal.STAFF_REQUIRED, "support staff only")
        if not auth_methods & SECOND_FACTOR_METHODS:
            raise support_access_refused(
                SupportRefusal.MFA_REQUIRED,
                "a second authentication factor is required for support access",
            )
        return await self._context(principal_id, grant_id)

    async def recheck(self, context: AccessContext) -> None:
        """Before the last write of a long operation: the grant is still in
        force (a revocation or expiry since the request began refuses it)."""
        if context.support is None:
            return
        await self._context(context.principal_id, context.support.grant_id)

    async def _context(self, principal_id: UUID, grant_id: UUID) -> AccessContext:
        grant = await self.grants.grant_for_staff(principal_id, grant_id)
        if grant is None:
            raise NotFoundError("support grant not found")
        now = self.clock.now()
        granter_scopes = (
            await self.member_scopes.scopes_of(
                grant.tenant_id, grant.workspace_id, grant.granted_by
            )
            if grant.granted_by is not None
            else frozenset()
        )
        state = grant_effective_state(grant, now, granter_scopes)
        if state != GrantStatus.ACTIVE.value:
            ended_reason = state if state in (EXPIRED, INEFFECTIVE) else "revoked"
            ended_at = {
                EXPIRED: grant.expires_at,
                INEFFECTIVE: now,
            }.get(ended_reason, grant.revoked_at or now)
            raise support_access_refused(
                SupportRefusal.GRANT_ENDED,
                "this support grant is no longer in force",
                ended_reason=ended_reason,
                ended_at=ended_at.isoformat() if ended_at else None,
            )
        plan = await self.grants.tenant_plan(grant.tenant_id)
        if plan is None or SUPPORT_ACCESS_FEATURE not in plan.feature_flags:
            raise support_access_refused(
                SupportRefusal.NOT_ENABLED,
                "support access is not enabled for this organisation",
            )
        return AccessContext(
            tenant_id=grant.tenant_id,
            workspace_id=grant.workspace_id,
            principal_id=principal_id,
            roles=frozenset(),
            scopes=grant.scopes,
            plan_id=plan.plan_id,
            feature_flags=plan.feature_flags,
            support=SupportScope(
                grant_id=grant.id,
                code=grant.code,
                resource_type=grant.resource_type,
                resource_id=grant.resource_id,
                scope_set_key=grant.scope_set_key,
            ),
        )


def support_access_audit(
    *,
    event_id: UUID,
    context: AccessContext,
    method: str,
    route: str,
    path_ids: dict[str, str],
    occurred_at: datetime,
) -> AuditEvent:
    """One access under a grant, on the customer's trail: the method, the route
    template and the ids in its path. Never a query string, never a body."""
    assert context.support is not None
    return AuditEvent(
        id=event_id,
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        actor_id=UserId(context.principal_id),
        action=SUPPORT_ACCESS_ACTION,
        resource_type=_RESOURCE,
        resource_id=str(context.support.grant_id),
        occurred_at=occurred_at,
        details={
            "support_grant_id": str(context.support.grant_id),
            "code": context.support.code,
            "method": method,
            "route": route,
            "path_ids": path_ids,
        },
    )
