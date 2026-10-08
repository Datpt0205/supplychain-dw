"""Platform provisioning: create/lock tenants, assign the first Org Admin, and
manage the Platform Operator allowlist (ADR-002).

This path is deliberately *tenant-less*. A ``ProvisioningContext`` carries only
the operator's principal id — the FastAPI dependency refuses to build one for a
non-operator, so every method here already runs on behalf of a verified
operator. The repository runs as ``dw_provisioner``, whose grants reach only the
platform provisioning tables, so nothing here can touch a tenant's business data.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from dw_kernel.errors import ConflictError, DomainError, NotFoundError
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.support_access import SupportGrant, support_grant_audit
from dw_platform.domain.audit import AuditEvent

# A tenant slug is a URL-safe handle: lowercase, digits, single hyphens.
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ORG_ADMIN_ROLE = "org_admin"
_MAIN_WORKSPACE_SLUG = "main"
_TENANT_STATUSES = frozenset({"active", "locked"})


@dataclass(frozen=True, slots=True)
class ProvisioningContext:
    """A verified Platform Operator acting outside any tenant."""

    principal_id: UUID


@dataclass(frozen=True, slots=True)
class TenantSummary:
    id: UUID
    slug: str
    name: str
    status: str
    plan_id: str | None
    workspace_count: int
    member_count: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class OperatorRef:
    user_id: UUID
    email: str | None
    display_name: str
    note: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class UserRef:
    user_id: UUID
    email: str | None
    display_name: str


@dataclass(frozen=True, slots=True)
class OffboardingStatus:
    """The state of a tenant's export + purge, as the worker lane reports it.

    `status` mirrors `ck_tenant_offboarding_requests_status`:
    requested -> exporting -> purging -> completed, or -> failed from any of
    the first three. `export_key` is set once the export bundle is uploaded;
    `error` is set only on `failed`.
    """

    request_id: UUID
    tenant_id: UUID
    status: str
    requested_by: UUID
    requested_at: datetime
    export_key: str | None
    error: str | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SupportStaffRef:
    user_id: UUID
    email: str | None
    display_name: str
    note: str | None
    added_at: datetime


@dataclass(frozen=True, slots=True)
class SupportRequestSummary:
    """A grant waiting for the operators to pick who carries it.

    What the operator needs to choose a person, and nothing of the customer's
    business: no content, no figures."""

    grant_id: UUID
    code: str
    tenant_id: UUID
    tenant_name: str
    workspace_id: UUID
    workspace_name: str
    resource_type: str
    resource_label: str
    scope_set_key: str
    scope_set_label: str
    duration_hours: int
    reason: str
    requested_at: datetime


@dataclass(frozen=True, slots=True)
class NewTenant:
    tenant_id: UUID
    workspace_id: UUID
    slug: str
    name: str


class ProvisioningRepositoryPort(Protocol):
    """Cross-tenant writes on the platform provisioning tables. Implemented over
    the ``dw_provisioner`` role, which cannot read any business schema."""

    async def list_tenants(self) -> list[TenantSummary]: ...

    async def list_users(self) -> list[UserRef]:
        """Every signed-in identity — the picker behind the email boxes."""
        ...

    async def slug_exists(self, slug: str) -> bool: ...

    async def plan_exists(self, plan_id: str) -> bool: ...

    async def create_tenant(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        entitlement_id: UUID,
        slug: str,
        name: str,
        plan_id: str,
    ) -> None: ...

    async def set_tenant_status(self, tenant_id: UUID, status: str) -> bool:
        """Returns False if no tenant had that id."""
        ...

    async def create_offboarding_request(
        self, *, request_id: UUID, tenant_id: UUID, requested_by: UUID
    ) -> None:
        """Fails on the DB's own unique-active-request index if one is already
        in flight for this tenant — the caller surfaces that as a conflict."""
        ...

    async def get_offboarding_status(self, tenant_id: UUID) -> OffboardingStatus | None:
        """The tenant's own request, whichever status it's in. `None` if none
        was ever filed."""
        ...

    async def rename_tenant(self, tenant_id: UUID, name: str) -> bool:
        """Change a tenant's display name. Returns False if no tenant had that id."""
        ...

    async def main_workspace_id(self, tenant_id: UUID) -> UUID | None: ...

    async def find_user_by_email(self, email: str) -> UserRef | None: ...

    async def grant_role(
        self,
        *,
        membership_id: UUID,
        tenant_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        role: str,
    ) -> None:
        """Add ``role`` to the user's membership in that workspace, creating the
        membership if absent. Idempotent in the role set."""
        ...

    async def list_operators(self) -> list[OperatorRef]: ...

    async def is_operator(self, user_id: UUID) -> bool: ...

    async def add_operator(self, *, user_id: UUID, note: str | None, created_by: UUID) -> None: ...

    async def remove_operator(self, user_id: UUID) -> bool:
        """Returns False if the user was not an operator."""
        ...

    async def list_support_staff(self) -> list[SupportStaffRef]: ...

    async def add_support_staff(self, *, user_id: UUID, note: str | None, added_by: UUID) -> None:
        """Idempotent: adding someone already listed changes nothing."""
        ...

    async def remove_support_staff(self, user_id: UUID) -> bool:
        """Returns False if the user was not support staff."""
        ...

    async def list_support_requests(self) -> list[SupportRequestSummary]:
        """Every tenant's grants in `pending_assignment`, oldest first."""
        ...

    async def assign_support_grant(
        self,
        *,
        grant_id: UUID,
        staff_user_id: UUID,
        assigned_by: UUID,
        activated_at: datetime,
        tenant_audit: Callable[[SupportGrant], AuditEvent],
        provisioning_audit_id: UUID,
    ) -> SupportGrant:
        """Make a `pending_assignment` grant `active` for a support staff
        member, with the tenant's audit event and the provisioning record, in
        one transaction. NotFoundError for no such grant; ConflictError when it
        is not pending assignment or the person is not support staff."""
        ...

    async def record_audit(
        self,
        *,
        audit_id: UUID,
        actor_id: UUID,
        action: str,
        target_type: str,
        target_id: str | None,
        details: dict[str, object],
        occurred_at: datetime,
    ) -> None: ...


@dataclass(frozen=True)
class ProvisioningService:
    """The operator's use-cases. Each write leaves a provisioning-audit row."""

    repo: ProvisioningRepositoryPort
    clock: UtcClock
    ids: IdGenerator

    async def list_tenants(self, context: ProvisioningContext) -> list[TenantSummary]:
        return await self.repo.list_tenants()

    async def list_users(self, context: ProvisioningContext) -> list[UserRef]:
        return await self.repo.list_users()

    async def create_tenant(
        self, context: ProvisioningContext, *, slug: str, name: str, plan_id: str
    ) -> NewTenant:
        slug = slug.strip().lower()
        name = name.strip()
        if not _SLUG.match(slug):
            raise DomainError(
                "tenant slug must be lowercase letters, digits and single hyphens",
                details={"slug": slug},
            )
        if not name:
            raise DomainError("tenant name must not be blank")
        if not await self.repo.plan_exists(plan_id):
            raise NotFoundError("unknown plan", details={"plan_id": plan_id})
        if await self.repo.slug_exists(slug):
            raise ConflictError("a tenant with that slug already exists", details={"slug": slug})

        tenant = NewTenant(
            tenant_id=self.ids.new_uuid(),
            workspace_id=self.ids.new_uuid(),
            slug=slug,
            name=name,
        )
        await self.repo.create_tenant(
            tenant_id=tenant.tenant_id,
            workspace_id=tenant.workspace_id,
            entitlement_id=self.ids.new_uuid(),
            slug=slug,
            name=name,
            plan_id=plan_id,
        )
        await self._audit(
            context,
            "platform.tenant.create",
            "tenant",
            str(tenant.tenant_id),
            {"slug": slug, "name": name, "plan_id": plan_id},
        )
        return tenant

    async def set_tenant_status(
        self, context: ProvisioningContext, *, tenant_id: UUID, status: str
    ) -> None:
        if status not in _TENANT_STATUSES:
            raise DomainError("status must be 'active' or 'locked'", details={"status": status})
        changed = await self.repo.set_tenant_status(tenant_id, status)
        if not changed:
            raise NotFoundError("unknown tenant", details={"tenant_id": str(tenant_id)})
        await self._audit(
            context, "platform.tenant.status", "tenant", str(tenant_id), {"status": status}
        )

    async def initiate_offboarding(
        self, context: ProvisioningContext, *, tenant_id: UUID
    ) -> OffboardingStatus:
        """File the request a worker lane will pick up, then set the tenant to
        `"offboarding"` — through `self.repo.set_tenant_status` directly, not
        the public `set_tenant_status` above, which refuses anything outside
        `active`/`locked` on purpose: an operator must not be able to set this
        status by hand, only by starting this flow.

        The request is filed FIRST, deliberately: `create_offboarding_request`
        raises `ConflictError` when one is already in flight for this tenant
        (the DB's own unique-active-request index), and at that point nothing
        has been touched yet — no status flip to un-do, no risk of clobbering
        the "offboarding" status a still-in-flight first request already set.
        """
        request_id = self.ids.new_uuid()
        await self.repo.create_offboarding_request(
            request_id=request_id, tenant_id=tenant_id, requested_by=context.principal_id
        )
        changed = await self.repo.set_tenant_status(tenant_id, "offboarding")
        if not changed:
            raise NotFoundError("unknown tenant", details={"tenant_id": str(tenant_id)})
        await self._audit(
            context, "platform.tenant.offboarding_initiate", "tenant", str(tenant_id), {}
        )
        status = await self.repo.get_offboarding_status(tenant_id)
        assert status is not None, "just inserted"
        return status

    async def get_offboarding_status(
        self, context: ProvisioningContext, *, tenant_id: UUID
    ) -> OffboardingStatus:
        status = await self.repo.get_offboarding_status(tenant_id)
        if status is None:
            raise NotFoundError(
                "no offboarding request for this tenant", details={"tenant_id": str(tenant_id)}
            )
        return status

    async def finalize_offboarding(self, context: ProvisioningContext, *, tenant_id: UUID) -> None:
        """The operator's explicit second step, once the worker lane reports
        `completed`. Keeps `platform.tenants` / `provisioning_audit` mutation
        inside the provisioner boundary as designed — the worker, running as
        `dw_app` under the target tenant's own context, never writes either
        directly."""
        status = await self.repo.get_offboarding_status(tenant_id)
        if status is None:
            raise NotFoundError(
                "no offboarding request for this tenant", details={"tenant_id": str(tenant_id)}
            )
        if status.status != "completed":
            raise DomainError(
                "export/purge has not completed yet", details={"status": status.status}
            )
        changed = await self.repo.set_tenant_status(tenant_id, "offboarded")
        if not changed:
            raise NotFoundError("unknown tenant", details={"tenant_id": str(tenant_id)})
        await self._audit(
            context, "platform.tenant.offboarding_finalize", "tenant", str(tenant_id), {}
        )

    async def rename_tenant(
        self, context: ProvisioningContext, *, tenant_id: UUID, name: str
    ) -> TenantSummary:
        name = name.strip()
        if not name:
            raise DomainError("tenant name must not be blank")
        changed = await self.repo.rename_tenant(tenant_id, name)
        if not changed:
            raise NotFoundError("unknown tenant", details={"tenant_id": str(tenant_id)})
        await self._audit(
            context, "platform.tenant.rename", "tenant", str(tenant_id), {"name": name}
        )
        for tenant in await self.repo.list_tenants():
            if tenant.id == tenant_id:
                return tenant
        raise NotFoundError("unknown tenant", details={"tenant_id": str(tenant_id)})

    async def assign_org_admin(
        self, context: ProvisioningContext, *, tenant_id: UUID, email: str
    ) -> UserRef:
        workspace_id = await self.repo.main_workspace_id(tenant_id)
        if workspace_id is None:
            raise NotFoundError("unknown tenant", details={"tenant_id": str(tenant_id)})
        user = await self.repo.find_user_by_email(email)
        if user is None:
            raise NotFoundError(
                "no user with that email has signed in yet", details={"email": email}
            )
        await self.repo.grant_role(
            membership_id=self.ids.new_uuid(),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user.user_id,
            role=_ORG_ADMIN_ROLE,
        )
        await self._audit(
            context,
            "platform.org_admin.assign",
            "membership",
            str(user.user_id),
            {"tenant_id": str(tenant_id), "workspace_id": str(workspace_id)},
        )
        return user

    async def list_operators(self, context: ProvisioningContext) -> list[OperatorRef]:
        return await self.repo.list_operators()

    async def add_operator(
        self, context: ProvisioningContext, *, email: str, note: str | None
    ) -> UserRef:
        user = await self.repo.find_user_by_email(email)
        if user is None:
            raise NotFoundError(
                "no user with that email has signed in yet", details={"email": email}
            )
        await self.repo.add_operator(
            user_id=user.user_id, note=note, created_by=context.principal_id
        )
        await self._audit(
            context, "platform.operator.add", "operator", str(user.user_id), {"email": email}
        )
        return user

    async def remove_operator(self, context: ProvisioningContext, *, user_id: UUID) -> None:
        if user_id == context.principal_id:
            # Fail closed: never let the last operator lock themselves out by a
            # slip; removing yourself must go through another operator.
            raise DomainError("an operator cannot remove themselves")
        removed = await self.repo.remove_operator(user_id)
        if not removed:
            raise NotFoundError("not a platform operator", details={"user_id": str(user_id)})
        await self._audit(context, "platform.operator.remove", "operator", str(user_id), {})

    async def list_support_staff(self, context: ProvisioningContext) -> list[SupportStaffRef]:
        return await self.repo.list_support_staff()

    async def add_support_staff(
        self, context: ProvisioningContext, *, email: str, note: str | None
    ) -> UserRef:
        """List an existing identity as support staff (ADR 0024).

        From then on no membership path can place them in a tenant
        (`platform.refuse_support_staff_membership`); a membership they held
        before stays, and a support context never merges with it."""
        if note is not None and len(note) > 200:
            raise DomainError("a note is at most 200 characters")
        user = await self.repo.find_user_by_email(email)
        if user is None:
            raise NotFoundError(
                "no user with that email has signed in yet", details={"email": email}
            )
        await self.repo.add_support_staff(
            user_id=user.user_id, note=note, added_by=context.principal_id
        )
        await self._audit(
            context, "platform.support_staff.add", "support_staff", str(user.user_id), {}
        )
        return user

    async def remove_support_staff(self, context: ProvisioningContext, *, user_id: UUID) -> None:
        """Their grants stay assigned and stop working at the next request:
        the support context requires a listed staff member."""
        removed = await self.repo.remove_support_staff(user_id)
        if not removed:
            raise NotFoundError("not support staff", details={"user_id": str(user_id)})
        await self._audit(
            context, "platform.support_staff.remove", "support_staff", str(user_id), {}
        )

    async def list_support_requests(
        self, context: ProvisioningContext
    ) -> list[SupportRequestSummary]:
        return await self.repo.list_support_requests()

    async def assign_support_request(
        self, context: ProvisioningContext, *, grant_id: UUID, staff_user_id: UUID
    ) -> SupportGrant:
        """Pick who carries a granted support access; it starts now.

        **No conflict-of-interest check** (support-access spec, open question
        2): nothing here asks whether this person serves a competitor of the
        customer. That check needs a decision on how it is made; until then
        only tenants with the `support_access` flag can be assigned at all.
        The customer never chooses the person and never sees a refusal."""
        now = self.clock.now()

        def tenant_audit(grant: SupportGrant) -> AuditEvent:
            return support_grant_audit(
                event_id=self.ids.new_uuid(),
                grant=grant,
                actor_id=context.principal_id,
                action="support.grant.assigned",
                occurred_at=now,
                extra={"staff_user_id": str(staff_user_id)},
            )

        return await self.repo.assign_support_grant(
            grant_id=grant_id,
            staff_user_id=staff_user_id,
            assigned_by=context.principal_id,
            activated_at=now,
            tenant_audit=tenant_audit,
            provisioning_audit_id=self.ids.new_uuid(),
        )

    async def _audit(
        self,
        context: ProvisioningContext,
        action: str,
        target_type: str,
        target_id: str | None,
        details: dict[str, object],
    ) -> None:
        await self.repo.record_audit(
            audit_id=self.ids.new_uuid(),
            actor_id=context.principal_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
            occurred_at=self.clock.now(),
        )
