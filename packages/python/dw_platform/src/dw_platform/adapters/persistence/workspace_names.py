"""The display name of the caller's own workspace.

A chat reply names the workspace it acted in, so a person who belongs to several
can tell where a proposal went (ADR 0012: "tin gửi đi mang tên tenant và
workspace"). Read under the caller's tenant (RLS) and keyed by the context's own
workspace, never by anything a message said.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext


@dataclass(frozen=True)
class SqlWorkspaceNames:
    session_factory: async_sessionmaker[AsyncSession]

    async def name_of(self, context: AccessContext) -> str | None:
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            name: str | None = await session.scalar(
                sa.select(tables.workspaces.c.name).where(
                    tables.workspaces.c.tenant_id == context.tenant_id,
                    tables.workspaces.c.id == context.workspace_id,
                )
            )
        return name
