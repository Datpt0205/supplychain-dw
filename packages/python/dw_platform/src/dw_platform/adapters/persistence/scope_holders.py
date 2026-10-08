"""Who in a workspace holds a scope: the members a sender should address.

A membership's scopes are read by `effective_scopes`, the same function that
builds the access context, so "who may act" and "who is told to act" cannot
disagree. A tenant that is not active has nobody to tell, as it has nobody
to let in.

Every question here — "who holds one of these scopes" (`holding`), "does this
person hold this scope" (`holds`) and "which of these people still belong"
(`members`) — reads memberships through one query,
`_members`, so the answers cannot drift apart either. Neither takes an
`AccessContext`: a workflow node resuming after someone else's decision, or a
worker lane, has a tenant and a workspace from a verified source but no
signed-in caller.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.membership_lookup import effective_scopes
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session


async def _members(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> Sequence[sa.Row[Any]]:
    """The memberships of one workspace of an active tenant, by `user_id`.

    The one membership query of this module. RLS already bounds the rows to the
    bound tenant; the explicit tenant predicate says so again, and the tenant
    join refuses a tenant that is not `active` (an absent or unreadable status
    is not active either). With `user_id`, at most that one membership.
    """
    query = (
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
            tables.memberships.c.tenant_id == tenant_id,
            tables.memberships.c.workspace_id == workspace_id,
            tables.tenants.c.status == "active",
        )
        .order_by(tables.memberships.c.user_id)
    )
    if user_id is not None:
        query = query.where(tables.memberships.c.user_id == user_id)
    return (await session.execute(query)).all()


async def _scopes_of(session: AsyncSession, member: sa.Row[Any]) -> frozenset[str]:
    return await effective_scopes(
        session, frozenset(member.role_keys), frozenset(member.permission_set_keys)
    )


@dataclass(frozen=True)
class SqlScopeHolders:
    session_factory: async_sessionmaker[AsyncSession]

    async def holding(
        self, tenant_id: uuid.UUID, workspace_id: uuid.UUID, scopes: frozenset[str]
    ) -> list[uuid.UUID]:
        """The members of `workspace_id` holding any of `scopes`, by user id.

        `tenant_id` and `workspace_id` must come from a source the caller has
        already verified — a row the database tagged with its tenant, a state
        stamped when the run started, an `AccessContext` — never from a request
        body. This function does not check where they came from.
        """
        if not scopes:
            return []
        scope = TenantScope(tenant_id=tenant_id, workspace_id=workspace_id)
        async with tenant_session(self.session_factory, scope) as session:
            holders: list[uuid.UUID] = []
            for member in await _members(session, tenant_id, workspace_id):
                if await _scopes_of(session, member) & scopes:
                    holders.append(member.user_id)
        return holders

    async def members(
        self, tenant_id: uuid.UUID, workspace_id: uuid.UUID, user_ids: frozenset[uuid.UUID]
    ) -> frozenset[uuid.UUID]:
        """Which of `user_ids` hold a membership of the workspace, in an
        active tenant, now: a named person (a case's PIC) is told only while
        they still belong where the case is. Same provenance rule as `holding`."""
        if not user_ids:
            return frozenset()
        scope = TenantScope(tenant_id=tenant_id, workspace_id=workspace_id)
        async with tenant_session(self.session_factory, scope) as session:
            found = await _members(session, tenant_id, workspace_id)
        return frozenset(member.user_id for member in found) & user_ids

    async def holds(
        self, tenant_id: uuid.UUID, workspace_id: uuid.UUID, user_id: uuid.UUID, scope: str
    ) -> bool:
        """Whether `user_id`'s membership of `workspace_id` carries `scope`.

        False for no membership, a tenant that is not active, or an empty
        `scope` (fail closed). No role stands in for a scope here,
        `platform_admin` included: the question is what this membership holds,
        the same question `holding` answers.

        `tenant_id` and `workspace_id` must come from a source the caller has
        already verified (a stamped state, a tenant-tagged row), never from a
        request body; this function does not check where they came from.
        """
        if not scope:
            return False
        return scope in await self.scopes_of(tenant_id, workspace_id, user_id)

    async def scopes_of(
        self, tenant_id: uuid.UUID, workspace_id: uuid.UUID, user_id: uuid.UUID
    ) -> frozenset[str]:
        """Every scope `user_id`'s membership of `workspace_id` carries; empty
        for no membership or a tenant that is not active. Implements
        `MemberScopesPort` (a support grant is in force only while its granter
        still holds what it stamped). Same provenance rule as `holds`."""
        bound = TenantScope(tenant_id=tenant_id, workspace_id=workspace_id)
        async with tenant_session(self.session_factory, bound) as session:
            members = await _members(session, tenant_id, workspace_id, user_id)
            if len(members) != 1:
                return frozenset()
            return await _scopes_of(session, members[0])
