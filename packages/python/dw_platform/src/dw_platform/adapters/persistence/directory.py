"""SQL implementation of the workspace directory.

Two filters, both needed. RLS on ``platform.memberships`` bounds the rows to the
tenant in context, and the explicit predicate bounds them to one workspace -
the same split the 2026-08-14 decision already records for the sales-chat write
path, because no policy in this repo keys on workspace.

``platform.users`` is the identity plane and carries no tenant column, so the
join must hang off ``memberships``. Selecting from ``users`` first and filtering
afterwards would read every user in the deployment before narrowing, which is
exactly the shape RLS exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.application.directory import IdentityRef, WorkspaceMember


def member_status() -> sa.ColumnElement[str]:
    """`invited` until the person has signed in once, `active` after.

    The one reading of a member's status, for the workspace directory and the
    tenant-wide member list alike: signed in means a sign-in identity is linked
    to the user. A linked chat is a delivery address, not a sign-in, so it does
    not count. Correlated on `platform.users`, which the caller's query joins.
    """
    signed_in = sa.exists().where(
        tables.external_identities.c.user_id == tables.users.c.id,
        tables.external_identities.c.provider.not_in(tables.CHANNEL_LINK_PROVIDERS),
    )
    return sa.case((signed_in, sa.literal("active")), else_=sa.literal("invited"))


@dataclass(frozen=True)
class SqlWorkspaceDirectory:
    """Implements ``WorkspaceDirectoryPort``."""

    session_factory: async_sessionmaker[AsyncSession]

    async def list_members(self, context: AccessContext) -> list[WorkspaceMember]:
        query = (
            sa.select(
                tables.users.c.id,
                tables.users.c.display_name,
                tables.users.c.email,
                tables.memberships.c.role_keys,
                tables.memberships.c.permission_set_keys,
                tables.memberships.c.department,
                member_status().label("status"),
            )
            .select_from(
                tables.memberships.join(
                    tables.users, tables.memberships.c.user_id == tables.users.c.id
                )
            )
            .where(tables.memberships.c.workspace_id == context.workspace_id)
            .order_by(tables.users.c.display_name)
        )
        scope = TenantScope.from_access_context(context)
        async with tenant_session(self.session_factory, scope) as session:
            rows = (await session.execute(query)).all()
        return [
            WorkspaceMember(
                user_id=row.id,
                display_name=row.display_name,
                email=row.email,
                role_keys=tuple(row.role_keys),
                permission_set_keys=tuple(row.permission_set_keys),
                department=row.department,
                status=row.status,
            )
            for row in rows
        ]

    async def list_candidates(self, context: AccessContext) -> list[IdentityRef]:
        # Suggestions for the grant picker: people already in THIS TENANT who are
        # not yet in this workspace, PLUS brand-new signups who belong to no
        # company yet — the ones an admin actually onboards. A member of ANOTHER
        # tenant is excluded (the cross-company leak ADR-001 forbids): the
        # `in_this_tenant` subquery is RLS-bounded so it only ever names this
        # tenant's members, and `user_has_any_membership` (a SECURITY DEFINER
        # probe, migration 0081) tells a brand-new signup apart from a member of
        # some other tenant without exposing a single row.
        already = sa.select(tables.memberships.c.user_id).where(
            tables.memberships.c.workspace_id == context.workspace_id
        )
        in_this_tenant = sa.select(tables.memberships.c.user_id)
        belongs_somewhere = sa.func.platform.user_has_any_membership(tables.users.c.id)
        query = (
            sa.select(
                tables.users.c.id,
                tables.users.c.display_name,
                tables.users.c.email,
            )
            .where(
                tables.users.c.email.is_not(None),
                tables.users.c.id.not_in(already),
                sa.or_(
                    tables.users.c.id.in_(in_this_tenant),
                    sa.not_(belongs_somewhere),
                ),
            )
            .order_by(tables.users.c.display_name)
        )
        scope = TenantScope.from_access_context(context)
        async with tenant_session(self.session_factory, scope) as session:
            rows = (await session.execute(query)).all()
        return [
            IdentityRef(user_id=row.id, display_name=row.display_name, email=row.email)
            for row in rows
        ]
