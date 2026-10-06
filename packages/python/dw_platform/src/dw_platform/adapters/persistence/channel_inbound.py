"""Inbound chat messages: claim each message id once, record what came of it.

``platform.channel_inbound_messages`` (migration 988592a8100f) is identity plane:
no tenant column and no RLS, because the claim happens before any tenant is
resolved. Every statement names ``(channel, external_message_id)``. Bound to a
``dw_app`` session factory, which may insert, read, update ``outcome`` alone and
delete.

Structurally implements ``dw_connectors.inbound.InboundLedgerPort``; the
Protocol is not imported so the platform keeps no dependency on connectors.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_platform.adapters.persistence import tables

_messages = tables.channel_inbound_messages

INBOUND_MESSAGE_RETENTION = timedelta(days=7)
"""How long an inbound message id is remembered.

A technical bound, not tenant policy and not a legal term (the row holds an id,
a user and an outcome, never the text): long enough to outlast a redelivery of
the same update by either path (how soon Zalo's webhook retries is not measured
yet; days is far past any retry window we know of) and to answer "what happened
to the message I sent yesterday?", short enough
that the table stays small. Not in ``retention@*.yaml`` for the same reason the
link nonces' day is not.
"""


@dataclass(frozen=True)
class SqlChannelInboundLedger:
    session_factory: async_sessionmaker[AsyncSession]

    async def claim(self, channel: str, message_id: str, user_id: UUID) -> bool:
        """Record the id as ``processing`` and commit; False when it was seen before.

        One ``INSERT ... ON CONFLICT DO NOTHING``, committed before the caller
        acts: two deliveries of one message in flight both try, the primary key
        lets exactly one through, and the loser sees zero rows. Never a read
        followed by a write.
        """
        async with self.session_factory() as session, session.begin():
            inserted = (
                await session.execute(
                    insert(_messages)
                    .values(channel=channel, external_message_id=message_id, user_id=user_id)
                    .on_conflict_do_nothing(index_elements=["channel", "external_message_id"])
                    .returning(_messages.c.external_message_id)
                )
            ).first()
        return inserted is not None

    async def settle(self, channel: str, message_id: str, outcome: str) -> None:
        """What became of a claimed message: ``done``, ``ignored`` or ``failed``.

        Only a ``processing`` row moves, so a settled message is never
        re-labelled; the outcome's CHECK refuses anything else.
        """
        async with self.session_factory() as session, session.begin():
            await session.execute(
                sa.update(_messages)
                .where(
                    _messages.c.channel == channel,
                    _messages.c.external_message_id == message_id,
                    _messages.c.outcome == "processing",
                )
                .values(outcome=outcome)
            )


@dataclass(frozen=True)
class SqlChannelInboundRetention:
    """Implements ``dw_worker.consumers.retention.RetentionPrunePort``.

    Deletes ids older than ``INBOUND_MESSAGE_RETENTION``, whatever their outcome.
    """

    session_factory: async_sessionmaker[AsyncSession]

    async def prune(self) -> None:
        async with self.session_factory() as session, session.begin():
            await session.execute(
                sa.delete(_messages).where(
                    _messages.c.received_at < sa.func.now() - INBOUND_MESSAGE_RETENTION
                )
            )
