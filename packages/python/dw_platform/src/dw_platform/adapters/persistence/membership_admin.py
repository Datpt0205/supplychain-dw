"""SQL implementation of the Org Admin membership operations.

Reads that need a tenant (the workspace check, the membership write, the revoke)
go through ``tenant_session`` so RLS bounds them to the caller's tenant. Reads on
the global planes — ``users`` (identity, no tenant column) and ``roles`` (the
catalog) — use a plain session, because neither is RLS-scoped and pinning a
tenant would say nothing about them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_kernel.errors import NotFoundError
from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.repositories import SqlAuditRepository
from dw_platform.adapters.persistence.separation_of_duties import (
    separation_of_duties_conflict,
)
from dw_platform.adapters.persistence.support_grants import support_staff_conflict
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.application.membership_admin import UserRef
from dw_platform.domain.audit import AuditEvent


@dataclass(frozen=True)
class SqlMembershipAdminRepository:
    """Implements ``MembershipAdminRepositoryPort``."""

    session_factory: async_sessionmaker[AsyncSession]

    async def find_user_by_email(self, email: str) -> UserRef | None:
        # Match case-insensitively on both sides so "A@x.com" and "a@x.com" find
        # the one identity (SRS: one email, one user).
        normalised = email.strip().lower()
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    sa.select(
                        tables.users.c.id,
                        tables.users.c.email,
                        tables.users.c.display_name,
                    ).where(sa.func.lower(tables.users.c.email) == normalised)
                )
            ).first()
        if row is None:
            return None
        return UserRef(user_id=row.id, email=row.email, display_name=row.display_name)

    async def known_roles(self, role_keys: frozenset[str]) -> frozenset[str]:
        if not role_keys:
            return frozenset()
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    sa.select(tables.roles.c.key).where(tables.roles.c.key.in_(role_keys))
                )
            ).all()
        return frozenset(row.key for row in rows)

    async def scopes_for_roles(self, role_keys: frozenset[str]) -> frozenset[str]:
        if not role_keys:
            return frozenset()
        scopes: set[str] = set()
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    sa.select(tables.roles.c.scopes).where(tables.roles.c.key.in_(role_keys))
                )
            ).all()
        for (role_scopes,) in rows:
            scopes.update(role_scopes)
        return frozenset(scopes)

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
        scope = TenantScope.from_access_context(context)
        async with tenant_session(self.session_factory, scope) as session:
            # RLS shows only this tenant's workspaces, so a workspace from another
            # tenant reads as missing — the grant is refused rather than landing a
            # membership whose workspace lives somewhere else.
            workspace = (
                await session.execute(
                    sa.select(tables.workspaces.c.id).where(tables.workspaces.c.id == workspace_id)
                )
            ).first()
            if workspace is None:
                raise NotFoundError(
                    "workspace not found in this tenant",
                    details={"workspace_id": str(workspace_id)},
                )
            try:
                await session.execute(
                    pg_insert(tables.memberships)
                    .values(
                        id=uuid.uuid4(),
                        tenant_id=context.tenant_id,
                        workspace_id=workspace_id,
                        user_id=user_id,
                        role_keys=sorted(role_keys),
                        department=department,
                    )
                    .on_conflict_do_update(
                        constraint="uq_memberships_scope_user",
                        set_={"role_keys": sorted(role_keys), "department": department},
                    )
                )
            except IntegrityError as exc:
                # The database refused a combination of roles one person may
                # not hold (the membership's existing permission sets
                # included), or a support staff member (ADR 0024): nothing
                # was granted, and no audit row claims it.
                conflict = separation_of_duties_conflict(exc) or support_staff_conflict(exc)
                if conflict is None:
                    raise
                raise conflict from exc
            await SqlAuditRepository(session).append(audit)

    async def revoke(
        self,
        context: AccessContext,
        *,
        user_id: uuid.UUID,
        workspace_id: uuid.UUID,
        audit: AuditEvent,
    ) -> bool:
        scope = TenantScope.from_access_context(context)
        async with tenant_session(self.session_factory, scope) as session:
            deleted = (
                await session.execute(
                    sa.delete(tables.memberships)
                    .where(
                        tables.memberships.c.user_id == user_id,
                        tables.memberships.c.workspace_id == workspace_id,
                    )
                    .returning(tables.memberships.c.id)
                )
            ).first()
            if deleted is None:
                return False
            await SqlAuditRepository(session).append(audit)
        return True
