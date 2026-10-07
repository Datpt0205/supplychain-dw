"""Who in a workspace holds a scope: the members a sender should address.

A membership's scopes are read by `effective_scopes`, the same function that
builds the access context, so "who may act" and "who is told to act" cannot
disagree. A tenant that is not active has nobody to tell, as it has nobody
to let in.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.membership_lookup import effective_scopes
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext


@dataclass(frozen=True)
class SqlScopeHolders:
    session_factory: async_sessionmaker[AsyncSession]

    async def holding(
        self, context: AccessContext, workspace_id: uuid.UUID, scopes: frozenset[str]
    ) -> list[uuid.UUID]:
        if not scopes:
            return []
        scope = TenantScope(tenant_id=context.tenant_id, workspace_id=workspace_id)
        async with tenant_session(self.session_factory, scope) as session:
            members = (
                await session.execute(
                    sa.select(
                        tables.memberships.c.user_id,
                        tables.memberships.c.role_keys,
                        tables.memberships.c.permission_set_keys,
                    )
                    .select_from(
                        tables.memberships.join(
                            tables.tenants, tables.tenants.c.id == tables.memberships.c.tenant_id
                        )
                    )
                    .where(
                        tables.memberships.c.tenant_id == context.tenant_id,
                        tables.memberships.c.workspace_id == workspace_id,
                        tables.tenants.c.status == "active",
                    )
                    .order_by(tables.memberships.c.user_id)
                )
            ).all()
            holders: list[uuid.UUID] = []
            for member in members:
                held = await effective_scopes(
                    session,
                    frozenset(member.role_keys),
                    frozenset(member.permission_set_keys),
                )
                if held & scopes:
                    holders.append(member.user_id)
        return holders

    async def members(
        self, context: AccessContext, workspace_id: uuid.UUID, user_ids: frozenset[uuid.UUID]
    ) -> frozenset[uuid.UUID]:
        """Which of `user_ids` hold a membership of the workspace, in an
        active tenant, now: a named person (a case's PIC) is told only while
        they still belong where the case is."""
        if not user_ids:
            return frozenset()
        scope = TenantScope(tenant_id=context.tenant_id, workspace_id=workspace_id)
        async with tenant_session(self.session_factory, scope) as session:
            found = (
                await session.scalars(
                    sa.select(tables.memberships.c.user_id)
                    .select_from(
                        tables.memberships.join(
                            tables.tenants, tables.tenants.c.id == tables.memberships.c.tenant_id
                        )
                    )
                    .where(
                        tables.memberships.c.tenant_id == context.tenant_id,
                        tables.memberships.c.workspace_id == workspace_id,
                        tables.memberships.c.user_id.in_(sorted(user_ids)),
                        tables.tenants.c.status == "active",
                    )
                )
            ).all()
        return frozenset(found)
