"""SQL side of the tenant-wide member list, role replacement and invitations.

Runs as `dw_app` under the caller's tenant (`tenant_session`): RLS on
`platform.memberships` and `platform.workspaces` bounds every read and write to
that tenant, and each query names the tenant again. `platform.users` is the
identity plane (no tenant column), so every read of it hangs off a membership
of this tenant, except the invitation's lookup by email, which reveals nothing
beyond "already a member here" (409).

Names sort with the ICU Vietnamese collation (`vi-VN-x-icu`), present in the
pinned `postgres:16-alpine` image (measured 2026-10-08: Đ after D, Ê after E;
the database default puts every accented name after Z). The list is not paged:
it is one tenant's people, read through `ix_memberships_tenant_user`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_kernel.errors import ConflictError, NotFoundError
from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.directory import member_status
from dw_platform.adapters.persistence.repositories import SqlAuditRepository
from dw_platform.adapters.persistence.separation_of_duties import (
    separation_of_duties_conflict,
)
from dw_platform.adapters.persistence.support_grants import support_staff_conflict
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.application.membership_admin import UserRef
from dw_platform.application.tenant_members import (
    MembershipChange,
    MemberWorkspace,
    Roles,
    TenantMember,
)
from dw_platform.domain.audit import AuditEvent

VIETNAMESE = "vi-VN-x-icu"
_M = tables.memberships


@dataclass(frozen=True)
class SqlTenantMembersRepository:
    """Implements ``TenantMembersRepositoryPort``."""

    session_factory: async_sessionmaker[AsyncSession]

    async def role_catalog(self) -> dict[str, frozenset[str]]:
        async with self.session_factory() as session:
            rows = (
                await session.execute(sa.select(tables.roles.c.key, tables.roles.c.scopes))
            ).all()
        return {row.key: frozenset(row.scopes) for row in rows}

    async def list_members(self, context: AccessContext) -> list[TenantMember]:
        query = (
            sa.select(
                tables.users.c.id,
                tables.users.c.display_name,
                tables.users.c.email,
                member_status().label("status"),
                _M.c.workspace_id,
                tables.workspaces.c.name.label("workspace_name"),
                _M.c.role_keys,
            )
            .select_from(
                _M.join(tables.users, tables.users.c.id == _M.c.user_id).join(
                    tables.workspaces, tables.workspaces.c.id == _M.c.workspace_id
                )
            )
            .where(_M.c.tenant_id == context.tenant_id)
            .order_by(
                tables.users.c.display_name.collate(VIETNAMESE),
                tables.users.c.id,
                tables.workspaces.c.name.collate(VIETNAMESE),
                _M.c.workspace_id,
            )
        )
        async with tenant_session(self.session_factory, _scope(context)) as session:
            rows = (await session.execute(query)).all()
        members: dict[uuid.UUID, TenantMember] = {}
        for row in rows:
            entry = MemberWorkspace(
                workspace_id=row.workspace_id,
                workspace_name=row.workspace_name,
                role_keys=tuple(sorted(row.role_keys)),
            )
            known = members.get(row.id)
            members[row.id] = TenantMember(
                user_id=row.id,
                display_name=row.display_name,
                email=row.email,
                status=row.status,
                memberships=((*known.memberships, entry) if known else (entry,)),
            )
        return list(members.values())

    async def replace_memberships(
        self,
        context: AccessContext,
        *,
        user_id: uuid.UUID,
        plan: Callable[[Roles], Roles],
        audit: Callable[[MembershipChange], AuditEvent],
    ) -> list[MembershipChange]:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            held = (
                await session.execute(
                    sa.select(_M.c.workspace_id, _M.c.role_keys)
                    .where(_M.c.tenant_id == context.tenant_id, _M.c.user_id == user_id)
                    .with_for_update()
                )
            ).all()
            if not held:
                # Not a member of this tenant — or of no tenant at all: one answer.
                raise NotFoundError(
                    "no such member in this tenant", details={"user_id": str(user_id)}
                )
            current = {row.workspace_id: frozenset(row.role_keys) for row in held}
            planned = plan(current)
            await _require_workspaces(session, context, set(planned) - set(current))
            changes = [
                MembershipChange(ws, current.get(ws, frozenset()), roles)
                for ws, roles in sorted(planned.items(), key=lambda item: str(item[0]))
                if roles != current.get(ws, frozenset())
            ]
            await _write(session, context, user_id, changes)
            for change in changes:
                await SqlAuditRepository(session).append(audit(change))
        return changes

    async def invite(
        self,
        context: AccessContext,
        *,
        display_name: str,
        email: str,
        memberships: Roles,
        audit: Callable[[uuid.UUID, uuid.UUID, frozenset[str]], AuditEvent],
    ) -> UserRef:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            await _require_workspaces(session, context, set(memberships))
            existing = (
                await session.execute(
                    sa.select(
                        tables.users.c.id, tables.users.c.email, tables.users.c.display_name
                    ).where(sa.func.lower(tables.users.c.email) == email)
                )
            ).first()
            if existing is None:
                created = (
                    await session.execute(
                        pg_insert(tables.users)
                        .values(
                            id=uuid.uuid4(),
                            subject=f"invite:{uuid.uuid4()}",
                            email=email,
                            display_name=display_name,
                        )
                        .on_conflict_do_nothing(index_elements=["email"])
                        .returning(tables.users.c.id)
                    )
                ).first()
                if created is None:
                    # A concurrent invitation created this person first.
                    raise _already_member()
                user = UserRef(user_id=created.id, email=email, display_name=display_name)
            else:
                user = UserRef(
                    user_id=existing.id, email=existing.email, display_name=existing.display_name
                )
                in_tenant = await session.scalar(
                    sa.select(sa.func.count())
                    .select_from(_M)
                    .where(_M.c.tenant_id == context.tenant_id, _M.c.user_id == user.user_id)
                )
                if in_tenant:
                    raise _already_member()
            await _write(
                session,
                context,
                user.user_id,
                [MembershipChange(ws, frozenset(), roles) for ws, roles in memberships.items()],
            )
            for ws, roles in memberships.items():
                await SqlAuditRepository(session).append(audit(user.user_id, ws, roles))
        return user


async def _require_workspaces(
    session: AsyncSession, context: AccessContext, workspace_ids: set[uuid.UUID]
) -> None:
    if not workspace_ids:
        return
    found = set(
        (
            await session.execute(
                sa.select(tables.workspaces.c.id).where(
                    tables.workspaces.c.tenant_id == context.tenant_id,
                    tables.workspaces.c.id.in_(workspace_ids),
                )
            )
        ).scalars()
    )
    missing = workspace_ids - found
    if missing:
        raise NotFoundError(
            "workspace not found in this tenant",
            details={"workspace_ids": sorted(str(ws) for ws in missing)},
        )


async def _write(
    session: AsyncSession,
    context: AccessContext,
    user_id: uuid.UUID,
    changes: list[MembershipChange],
) -> None:
    try:
        for change in changes:
            mine = (
                _M.c.tenant_id == context.tenant_id,
                _M.c.workspace_id == change.workspace_id,
                _M.c.user_id == user_id,
            )
            if not change.after:
                await session.execute(sa.delete(_M).where(*mine))
            elif not change.before:
                await session.execute(
                    sa.insert(_M).values(
                        id=uuid.uuid4(),
                        tenant_id=context.tenant_id,
                        workspace_id=change.workspace_id,
                        user_id=user_id,
                        role_keys=sorted(change.after),
                    )
                )
            else:
                await session.execute(
                    sa.update(_M).where(*mine).values(role_keys=sorted(change.after))
                )
    except IntegrityError as exc:
        conflict = separation_of_duties_conflict(exc) or support_staff_conflict(exc)
        if conflict is None:
            raise
        raise conflict from exc


def _already_member() -> ConflictError:
    return ConflictError(
        "a person with that email is already a member of this organisation",
        details={"reason_code": "member_email_exists_in_tenant"},
    )


def _scope(context: AccessContext) -> TenantScope:
    return TenantScope.from_access_context(context)
