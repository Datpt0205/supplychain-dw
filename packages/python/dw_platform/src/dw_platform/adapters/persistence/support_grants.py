"""SQL side of customer-granted support access (ADR 0024), as `dw_app`.

RLS on `platform.support_grants` narrows to the bound tenant only, so every
read and every write by id here also names the caller's workspace: a grant of
another workspace is absent, the same answer as one that never existed. Each
write commits with its audit event or not at all. The status machine itself is
the database's (`platform.guard_support_grant()`); the `expected` statuses here
only make a lost race read as "moved meanwhile" instead of a database error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_kernel.errors import ConflictError
from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.repositories import SqlAuditRepository
from dw_platform.adapters.persistence.separation_of_duties import refusal
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.application.support_access import (
    AuditFor,
    GrantChange,
    GrantStatus,
    NewSupportGrant,
    StaffGrantRow,
    SupportGrant,
    TenantPlan,
    support_staff_not_member,
)
from dw_platform.domain.audit import AuditEvent

_G = tables.support_grants
_NOT_SUPPORT_STAFF = "ck_memberships_not_support_staff"


def support_staff_conflict(error: IntegrityError) -> ConflictError | None:
    """The membership guard's refusal (`ck_memberships_not_support_staff`) as
    the 409 every membership path gives; None for any other integrity error."""
    refused = refusal(error)
    if refused is None or refused[0] != _NOT_SUPPORT_STAFF:
        return None
    return support_staff_not_member()


def grant_from_row(row: Any) -> SupportGrant:
    return SupportGrant(
        id=row.id,
        code=row.code,
        tenant_id=row.tenant_id,
        workspace_id=row.workspace_id,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        resource_label=row.resource_label,
        scope_set_key=row.scope_set_key,
        scope_set_label=row.scope_set_label,
        scopes=frozenset(row.scopes),
        reason=row.reason,
        duration_hours=row.duration_hours,
        status=GrantStatus(row.status),
        requested_by=row.requested_by,
        requested_at=row.requested_at,
        granted_by=row.granted_by,
        granted_at=row.granted_at,
        rejected_by=row.rejected_by,
        rejected_at=row.rejected_at,
        reject_reason=row.reject_reason,
        staff_user_id=row.staff_user_id,
        assigned_by=row.assigned_by,
        activated_at=row.activated_at,
        expires_at=row.expires_at,
        revoked_by=row.revoked_by,
        revoked_at=row.revoked_at,
    )


@dataclass(frozen=True)
class SqlSupportGrantRepository:
    """Implements ``SupportGrantRepositoryPort``."""

    session_factory: async_sessionmaker[AsyncSession]

    async def workspace_name(self, context: AccessContext) -> str | None:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            name: str | None = await session.scalar(
                sa.select(tables.workspaces.c.name).where(
                    tables.workspaces.c.id == context.workspace_id
                )
            )
        return name

    async def create(
        self, context: AccessContext, grant: NewSupportGrant, audit: AuditFor
    ) -> SupportGrant:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            row = (
                await session.execute(
                    sa.insert(_G)
                    .values(
                        id=grant.id,
                        tenant_id=context.tenant_id,
                        workspace_id=context.workspace_id,
                        resource_type=grant.resource_type,
                        resource_id=grant.resource_id,
                        resource_label=grant.resource_label,
                        scope_set_key=grant.scope_set_key,
                        scope_set_label=grant.scope_set_label,
                        scopes=sorted(grant.scopes),
                        reason=grant.reason,
                        duration_hours=grant.duration_hours,
                        status=grant.status.value,
                        requested_by=grant.requested_by,
                        requested_at=grant.requested_at,
                        granted_by=grant.granted_by,
                        granted_at=grant.granted_at,
                    )
                    .returning(*_G.c)
                )
            ).one()
            created = grant_from_row(row)
            await SqlAuditRepository(session).append(audit(created))
        return created

    async def get(
        self, context: AccessContext, grant_id: UUID, *, requested_by: UUID | None = None
    ) -> SupportGrant | None:
        query = sa.select(_G).where(_G.c.id == grant_id, *_mine(context, requested_by))
        async with tenant_session(self.session_factory, _scope(context)) as session:
            row = (await session.execute(query)).first()
        return None if row is None else grant_from_row(row)

    async def list_grants(
        self, context: AccessContext, *, requested_by: UUID | None = None
    ) -> list[SupportGrant]:
        query = (
            sa.select(_G)
            .where(*_mine(context, requested_by))
            .order_by(_G.c.requested_at.desc(), _G.c.id.desc())
        )
        async with tenant_session(self.session_factory, _scope(context)) as session:
            rows = (await session.execute(query)).all()
        return [grant_from_row(row) for row in rows]

    async def change(
        self,
        context: AccessContext,
        grant_id: UUID,
        *,
        expected: frozenset[GrantStatus],
        change: GrantChange,
        audit: AuditFor,
    ) -> SupportGrant | None:
        values: dict[str, object] = {"status": change.status.value}
        for column in (
            "granted_by",
            "granted_at",
            "rejected_by",
            "rejected_at",
            "reject_reason",
            "revoked_by",
            "revoked_at",
        ):
            value = getattr(change, column)
            if value is not None:
                values[column] = value
        async with tenant_session(self.session_factory, _scope(context)) as session:
            row = (
                await session.execute(
                    sa.update(_G)
                    .where(
                        _G.c.id == grant_id,
                        *_mine(context, None),
                        _G.c.status.in_([s.value for s in expected]),
                    )
                    .values(**values)
                    .returning(*_G.c)
                )
            ).first()
            if row is None:
                return None
            changed = grant_from_row(row)
            await SqlAuditRepository(session).append(audit(changed))
        return changed


def _scope(context: AccessContext) -> TenantScope:
    return TenantScope.from_access_context(context)


def _mine(context: AccessContext, requested_by: UUID | None) -> list[sa.ColumnElement[bool]]:
    """The caller's tenant (RLS says it too) and workspace; a requester's own."""
    clauses = [_G.c.tenant_id == context.tenant_id, _G.c.workspace_id == context.workspace_id]
    if requested_by is not None:
        clauses.append(_G.c.requested_by == requested_by)
    return clauses


_SET_PRINCIPAL = sa.text("SELECT set_config('app.principal_id', :principal_id, true)")


@dataclass(frozen=True)
class SqlStaffGrants:
    """Implements ``StaffGrantsPort`` and ``SupportAccessAuditPort`` as `dw_app`.

    A staff member is in no customer tenant, so RLS shows them no grant; the
    two `SECURITY DEFINER` functions return only the grants whose
    `staff_user_id` is the transaction's `app.principal_id`, bound here from
    the verified identity's user, per transaction.
    """

    session_factory: async_sessionmaker[AsyncSession]

    async def is_support_staff(self, user_id: UUID) -> bool:
        async with self.session_factory() as session, session.begin():
            row = (
                await session.execute(
                    sa.select(tables.support_staff.c.user_id).where(
                        tables.support_staff.c.user_id == user_id
                    )
                )
            ).first()
        return row is not None

    async def grant_for_staff(self, staff_user_id: UUID, grant_id: UUID) -> SupportGrant | None:
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_PRINCIPAL, {"principal_id": str(staff_user_id)})
            row = (
                await session.execute(
                    sa.text("SELECT * FROM platform.support_grant_for_staff(:grant_id)"),
                    {"grant_id": str(grant_id)},
                )
            ).first()
        return grant_from_row(row) if row is not None else None

    async def grants_for_staff(self, staff_user_id: UUID) -> list[StaffGrantRow]:
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_PRINCIPAL, {"principal_id": str(staff_user_id)})
            rows = (
                await session.execute(sa.text("SELECT * FROM platform.support_grants_for_staff()"))
            ).all()
        return [
            StaffGrantRow(
                grant=SupportGrant(
                    id=r.id,
                    code=r.code,
                    tenant_id=r.tenant_id,
                    workspace_id=r.workspace_id,
                    resource_type=r.resource_type,
                    resource_id=r.resource_id,
                    resource_label=r.resource_label,
                    scope_set_key=r.scope_set_key,
                    scope_set_label=r.scope_set_label,
                    scopes=frozenset(r.scopes),
                    # Not returned to the staff member: the customer's reason
                    # is theirs (ADR 0024).
                    reason="",
                    duration_hours=0,
                    status=GrantStatus(r.status),
                    requested_by=None,
                    requested_at=r.activated_at,
                    granted_by=r.granted_by,
                    staff_user_id=staff_user_id,
                    activated_at=r.activated_at,
                    expires_at=r.expires_at,
                    revoked_at=r.revoked_at,
                ),
                tenant_name=r.tenant_name,
                workspace_name=r.workspace_name,
            )
            for r in rows
        ]

    async def tenant_plan(self, tenant_id: UUID) -> TenantPlan | None:
        async with tenant_session(self.session_factory, TenantScope(tenant_id, None)) as session:
            row = (
                await session.execute(
                    sa.select(
                        tables.entitlements.c.plan_id,
                        tables.entitlements.c.feature_overrides,
                        tables.plans.c.features,
                        tables.tenants.c.status,
                    )
                    .select_from(
                        tables.entitlements.join(
                            tables.plans,
                            tables.entitlements.c.plan_id == tables.plans.c.plan_id,
                        ).join(
                            tables.tenants, tables.tenants.c.id == tables.entitlements.c.tenant_id
                        )
                    )
                    .where(tables.entitlements.c.tenant_id == tenant_id)
                )
            ).first()
        # A tenant without a plan, or not active, enables nothing.
        if row is None or row.status != "active":
            return None
        return TenantPlan(
            plan_id=row.plan_id,
            feature_flags=frozenset(row.features) | frozenset(row.feature_overrides),
        )

    async def record_access(self, event: AuditEvent) -> None:
        scope = TenantScope(event.tenant_id.value, event.workspace_id.value)
        async with tenant_session(self.session_factory, scope) as session:
            await SqlAuditRepository(session).append(event)
