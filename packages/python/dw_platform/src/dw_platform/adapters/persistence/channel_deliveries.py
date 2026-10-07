"""SQL side of the channel outbox (migration 5a25154e0296, ADR 0006).

Rows are created by ``platform.deliver_notification`` alone, in the statement
that addresses the in-app notification; nothing here inserts. What this module
does is let the worker's ``channel_delivery`` lane find, hold and settle them:

- ``due_scopes`` is the one read across tenants, through the SECURITY DEFINER
  ``platform.channel_delivery_scopes_due``; it returns ids only.
- ``claim_next`` opens one transaction bound to one tenant and workspace (the
  table's RLS is narrowed by both), takes the oldest due row with
  ``FOR UPDATE SKIP LOCKED`` and holds it until the caller settles it. A second
  worker skips a held row instead of waiting on it, and the ``status =
  'pending'`` in the claim is re-checked by READ COMMITTED once a lock it
  waited on is released, so a row sent by one worker is never claimed by
  another. One delivery per transaction: a crash re-sends at most the one
  message in flight, not a batch (ADR 0006: at least once).
- Every settlement but a retry writes one ``platform.audit_events`` row in the
  same transaction.

Structurally implements ``dw_worker.consumers.channel_delivery.ChannelOutboxPort``
(the consumer declares it; this package does not import the worker), and
``SqlChannelDeliveryRetention`` its ``RetentionPrunePort``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.tenant_session import TenantScope, bind_tenant

_d = tables.channel_deliveries
_m = tables.memberships
_t = tables.tenants
_w = tables.workspaces

_SCOPES_DUE = sa.text(
    "SELECT tenant_id, workspace_id FROM platform.channel_delivery_scopes_due(:channel)"
)

# Who acted, on every audit row this writes: the lane, on the recipient's behalf.
_ACTOR = "channel_delivery_lane"
# last_error is CHECKed at 500 characters.
_ERROR_LIMIT = 500


@dataclass(frozen=True)
class DeliveryScope:
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID


class _Scope(Protocol):
    @property
    def tenant_id(self) -> uuid.UUID: ...

    @property
    def workspace_id(self) -> uuid.UUID: ...


@dataclass
class SqlClaimedDelivery:
    """One due delivery, locked in an open transaction until settled."""

    session: AsyncSession
    id: uuid.UUID
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    recipient_user_id: uuid.UUID
    channel: str
    title: str
    link: str | None
    attempts: int
    tenant_name: str
    workspace_name: str

    async def recipient_may_receive(self) -> bool:
        """Still a member of this workspace, in a tenant that is active: what
        the notification's own door checked, asked again at send time."""
        found = await self.session.scalar(
            sa.select(sa.literal(True))
            .select_from(_m.join(_t, _t.c.id == _m.c.tenant_id))
            .where(
                _m.c.tenant_id == self.tenant_id,
                _m.c.workspace_id == self.workspace_id,
                _m.c.user_id == self.recipient_user_id,
                _t.c.status == "active",
            )
            .limit(1)
        )
        return bool(found)

    async def mark_sent(self, external_message_id: str, chat_reference: str) -> None:
        await self._settle(
            "sent",
            attempts=self.attempts + 1,
            external_message_id=external_message_id,
            last_error=None,
        )
        await self._audit(
            "channel_delivery.sent",
            {"external_message_id": external_message_id, "chat_id_hash": chat_reference},
            attempts=self.attempts + 1,
        )

    async def mark_failed(self, reason: str, error: str) -> None:
        """One more attempt made and none will follow."""
        await self._settle("failed", attempts=self.attempts + 1, last_error=f"{reason}: {error}")
        await self._audit("channel_delivery.failed", {"reason": reason}, self.attempts + 1)

    async def mark_cancelled(self, reason: str) -> None:
        """Not attempted, and never will be: the row says why."""
        await self._settle("cancelled", attempts=self.attempts, last_error=reason)
        await self._audit("channel_delivery.cancelled", {"reason": reason}, self.attempts)

    async def retry_later(self, error: str, delay: timedelta) -> None:
        """Still pending, one attempt more, due again after ``delay`` by the
        database's clock. No audit row: only an outcome is audited."""
        await self._settle(
            "pending",
            attempts=self.attempts + 1,
            last_error=error,
            next_attempt_at=sa.func.now() + delay,
        )

    async def _settle(self, status: str, *, attempts: int, **values: Any) -> None:
        if values.get("last_error") is not None:
            values["last_error"] = values["last_error"][:_ERROR_LIMIT]
        await self.session.execute(
            sa.update(_d)
            .where(_d.c.id == self.id, _d.c.status == "pending")
            .values(status=status, attempts=attempts, **values)
        )

    async def _audit(self, action: str, details: dict[str, Any], attempts: int) -> None:
        await self.session.execute(
            sa.insert(tables.audit_events).values(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                workspace_id=self.workspace_id,
                actor_id=self.recipient_user_id,
                action=action,
                resource_type="channel_delivery",
                resource_id=str(self.id),
                details={
                    "channel": self.channel,
                    "attempts": attempts,
                    "actor": _ACTOR,
                    **details,
                },
                occurred_at=sa.func.now(),
            )
        )


@dataclass(frozen=True)
class SqlChannelOutbox:
    session_factory: async_sessionmaker[AsyncSession]

    async def due_scopes(self, channel: str) -> list[DeliveryScope]:
        async with self.session_factory() as session:
            rows = (await session.execute(_SCOPES_DUE, {"channel": channel})).all()
        return [DeliveryScope(row.tenant_id, row.workspace_id) for row in rows]

    @asynccontextmanager
    async def claim_next(
        self, channel: str, scope: _Scope
    ) -> AsyncIterator[SqlClaimedDelivery | None]:
        """The oldest due row of ``scope``, locked until the block exits.

        Commits what the caller settled on a clean exit; on an exception the
        row is left exactly as it was, pending, for the next tick.
        """
        async with self.session_factory() as session, session.begin():
            await bind_tenant(
                session, TenantScope(tenant_id=scope.tenant_id, workspace_id=scope.workspace_id)
            )
            row = (
                await session.execute(
                    sa.select(
                        _d.c.id,
                        _d.c.recipient_user_id,
                        _d.c.title,
                        _d.c.link,
                        _d.c.attempts,
                    )
                    .where(
                        _d.c.tenant_id == scope.tenant_id,
                        _d.c.workspace_id == scope.workspace_id,
                        _d.c.channel == channel,
                        _d.c.status == "pending",
                        _d.c.next_attempt_at <= sa.func.now(),
                    )
                    .order_by(_d.c.next_attempt_at, _d.c.id)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            ).first()
            if row is None:
                yield None
                return
            names = (
                await session.execute(
                    sa.select(_t.c.name.label("tenant"), _w.c.name.label("workspace"))
                    .select_from(_w.join(_t, _t.c.id == _w.c.tenant_id))
                    .where(_w.c.id == scope.workspace_id, _w.c.tenant_id == scope.tenant_id)
                )
            ).one()
            yield SqlClaimedDelivery(
                session=session,
                id=row.id,
                tenant_id=scope.tenant_id,
                workspace_id=scope.workspace_id,
                recipient_user_id=row.recipient_user_id,
                channel=channel,
                title=row.title,
                link=row.link,
                attempts=row.attempts,
                tenant_name=names.tenant,
                workspace_name=names.workspace,
            )


@dataclass(frozen=True)
class SqlChannelDeliveryRetention:
    """Implements ``dw_worker.consumers.retention.RetentionPrunePort``.

    The window is the database's (``platform.prune_channel_deliveries()``, 90
    days, never a pending row), like the inbox it follows."""

    session_factory: async_sessionmaker[AsyncSession]

    async def prune(self) -> None:
        async with self.session_factory() as session, session.begin():
            await session.execute(sa.text("SELECT platform.prune_channel_deliveries()"))
