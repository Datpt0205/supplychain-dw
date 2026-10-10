"""The plan a tenant is on, for work no person started.

A run a person starts is counted against the plan their access context
carries. A system lane that starts a run on a tenant's behalf (Supply Chain's
BGĐ review reconcile) has no person to take it from, so it asks here: the same
`entitlements` row the membership lookup reads, under the tenant's RLS. A
tenant that is not active, or has no plan, answers None, and the lane starts
nothing, as the access context fails closed for both.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_platform.adapters.persistence import tables

_SET_TENANT = text("SELECT set_config('app.tenant_id', :tenant_id, true)")


@dataclass(frozen=True)
class SqlTenantPlans:
    session_factory: async_sessionmaker[AsyncSession]

    async def plan_of(self, tenant_id: uuid.UUID) -> str | None:
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_TENANT, {"tenant_id": str(tenant_id)})
            plan: str | None = await session.scalar(
                sa.select(tables.entitlements.c.plan_id)
                .select_from(
                    tables.entitlements.join(
                        tables.tenants, tables.tenants.c.id == tables.entitlements.c.tenant_id
                    )
                )
                .where(
                    tables.entitlements.c.tenant_id == tenant_id,
                    tables.tenants.c.status == "active",
                )
            )
        return plan
