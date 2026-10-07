"""The workspace a person uses for chat commands, and the memberships to pick from.

``platform.channel_preferences`` (migration 9f2becb1bf80) is narrowed by
``app.principal_id`` alone: the row is the person's, and the bot reads it before
it knows a tenant. Every transaction here binds that setting per transaction
(``set_config(..., true)``) to the user the caller resolved server-side — the
linked chat's user on the bot side, the verified access context's principal on
``/settings`` — and nothing else. The person's memberships are read through the
baseline's ``memberships_self_select`` policy, the same setting. No SECURITY
DEFINER function: one mechanism for "mine, before a tenant is known".

Bound to a ``dw_app`` session factory: SELECT and INSERT on the table, UPDATE of
``tenant_id``/``workspace_id``; the row is deleted only by the membership's
cascade.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_platform.adapters.persistence import tables

_preferences = tables.channel_preferences
_memberships = tables.memberships

_SET_PRINCIPAL = sa.text("SELECT set_config('app.principal_id', :principal_id, true)")


@dataclass(frozen=True)
class SqlChannelPreferences:
    """Implements ``dw_platform.application.channel_access.ChannelPreferencesPort``."""

    session_factory: async_sessionmaker[AsyncSession]

    async def chosen(self, user_id: UUID) -> tuple[UUID, UUID] | None:
        """``(tenant, workspace)`` the person chose, or None if they never did."""
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_PRINCIPAL, {"principal_id": str(user_id)})
            row = (
                await session.execute(
                    sa.select(_preferences.c.tenant_id, _preferences.c.workspace_id).where(
                        _preferences.c.user_id == user_id
                    )
                )
            ).first()
        return (row.tenant_id, row.workspace_id) if row else None

    async def memberships(self, user_id: UUID) -> list[tuple[UUID, UUID]]:
        """Every ``(tenant, workspace)`` the person is a member of, in a stable order."""
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_PRINCIPAL, {"principal_id": str(user_id)})
            rows = (
                await session.execute(
                    sa.select(_memberships.c.tenant_id, _memberships.c.workspace_id)
                    .where(_memberships.c.user_id == user_id)
                    .order_by(_memberships.c.tenant_id, _memberships.c.workspace_id)
                )
            ).all()
        return [(r.tenant_id, r.workspace_id) for r in rows]

    async def choose(self, user_id: UUID, tenant_id: UUID, workspace_id: UUID) -> bool:
        """Make ``(tenant, workspace)`` the person's choice; False if not a member there.

        The membership is confirmed under the person's own principal before the
        write, so a workspace they do not belong to answers the same as one that
        does not exist. The FK would refuse it anyway; asking first turns that
        refusal into a plain False instead of an integrity error.
        """
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_PRINCIPAL, {"principal_id": str(user_id)})
            member = (
                await session.execute(
                    sa.select(_memberships.c.id).where(
                        _memberships.c.user_id == user_id,
                        _memberships.c.tenant_id == tenant_id,
                        _memberships.c.workspace_id == workspace_id,
                    )
                )
            ).first()
            if member is None:
                return False
            await session.execute(
                insert(_preferences)
                .values(user_id=user_id, tenant_id=tenant_id, workspace_id=workspace_id)
                .on_conflict_do_update(
                    index_elements=["user_id"],
                    set_={"tenant_id": tenant_id, "workspace_id": workspace_id},
                )
            )
        return True
