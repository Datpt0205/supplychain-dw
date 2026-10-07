"""Access for a person who writes through a linked chat (ADR 0005 condition 2).

A chat command never builds its context from anything the chat sent. The chain
is: chat id -> the link row's user (``SqlZaloLink.user_id_for``) -> that user's
own membership, or the workspace they chose on ``/settings`` -> an
``AccessContext`` whose scopes are the membership's cut to the command's
ceiling. Every step is a database read keyed by the step before it; the
message text contributes nothing. ``DbAccessContextFactory`` (a verified token)
and ``LinkedUserAccess`` (a linked chat) are the only two doors, and both build
the context through ``context_from``.

``LinkedUserAccess`` structurally implements
``dw_connectors.inbound.LinkedAccessPort[AccessContext]``; the Protocol is not
imported so the platform keeps no dependency on connectors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from dw_platform.application.access_context import AccessContext
from dw_platform.application.identity import MembershipAccess, context_from


class ChannelPreferencesPort(Protocol):
    """The person's chat workspace choice and the memberships to choose from.

    Every method is keyed by a user id the caller resolved server-side; the
    adapter binds it as ``app.principal_id`` for its own transaction.
    """

    async def chosen(self, user_id: UUID) -> tuple[UUID, UUID] | None: ...

    async def memberships(self, user_id: UUID) -> list[tuple[UUID, UUID]]: ...

    async def choose(self, user_id: UUID, tenant_id: UUID, workspace_id: UUID) -> bool: ...


class LinkedMembershipLookupPort(Protocol):
    async def find_linked_access(
        self,
        user_id: UUID,
        tenant_id: UUID,
        workspace_id: UUID,
        ceiling: frozenset[str],
    ) -> MembershipAccess | None: ...


@dataclass(frozen=True)
class LinkedUserAccess:
    preferences: ChannelPreferencesPort
    lookup: LinkedMembershipLookupPort

    async def chosen_workspace(self, user_id: UUID) -> tuple[UUID, UUID] | None:
        return await self.preferences.chosen(user_id)

    async def workspaces_of(self, user_id: UUID) -> list[tuple[UUID, UUID]]:
        return await self.preferences.memberships(user_id)

    async def access_for(
        self,
        user_id: UUID,
        tenant_id: UUID,
        workspace_id: UUID,
        ceiling: frozenset[str],
    ) -> AccessContext | None:
        """The context a command runs with, or None when the person may not act there.

        None when the membership is gone or the tenant is locked or has no plan
        — the same refusals sign-in gives. The scopes never exceed ``ceiling``
        and the context carries no role (see ``find_linked_access``).
        """
        access = await self.lookup.find_linked_access(user_id, tenant_id, workspace_id, ceiling)
        return None if access is None else context_from(access)
