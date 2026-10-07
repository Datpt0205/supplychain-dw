"""The Zalo webhook path end to end against real Postgres, as ``dw_app``.

What the API's route queues (``SqlChannelUpdateQueue.enqueue``, the API's own
adapter) this lane takes and hands to the worker's ``build_zalo_inbound`` — the
object the poll lane runs, unchanged (ADR 0008). Shown here:

* a ``/start <code>`` in the webhook's envelope links the chat, exactly as the
  poll path does (the Z1 flow, over the other door);
* the same update delivered twice — Zalo retrying, or a replay — is acted on
  once: the router claims the message id in ``channel_inbound_messages``;
* a taken update is gone from the queue, and two drains at once never hand one
  update to both;
* the queue is identity plane: no tenant, and nothing outside the platform's
  grants (asserted in ``dw_platform``'s ``test_privileges.py``).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from pg_test_db import DatabaseUrls
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from test_zalo_inbound_db import _PRODUCT, _SECRET, _Bot, _Command, _linking, _member, _user

from dw_connectors.inbound import ChannelCommandRegistry
from dw_kernel.ports import SystemClock
from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.channel_inbound import (
    SqlChannelInboundRetention,
    SqlChannelUpdateQueue,
)
from dw_platform.application.access_context import AccessContext
from dw_worker.consumers.zalo_webhook import build_zalo_webhook_consumer
from dw_worker.main import build_zalo_inbound
from dw_worker.settings import WorkerSettings

pytestmark = pytest.mark.integration


@pytest.fixture
async def migrator(worker_db: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(worker_db.migrator, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def sessions(worker_db: DatabaseUrls) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(worker_db.app, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _hook_update(chat: str, text: str, message_id: str) -> dict[str, Any]:
    """The webhook's envelope: the message at the top level (survey §4)."""
    return {
        "event_name": "message.text.received",
        "message": {"chat": {"id": chat}, "text": text, "message_id": message_id},
    }


def _drain(sessions: async_sessionmaker[AsyncSession], bot: _Bot, command: _Command) -> Any:
    commands = ChannelCommandRegistry[AccessContext]()
    commands.register("probe", command)
    inbound = build_zalo_inbound(
        WorkerSettings(
            zalo_link_secret=_SECRET,  # type: ignore[arg-type]
            product_name=_PRODUCT,
            public_web_url="https://portal.example",
            zalo_updates_mode="webhook",
        ),
        sessions,
        bot,
        SystemClock(),
        commands,
    )
    return build_zalo_webhook_consumer(SqlChannelUpdateQueue(sessions), inbound)


async def _queued(migrator: AsyncEngine) -> int:
    async with migrator.connect() as conn:
        return int(
            (
                await conn.execute(
                    sa.select(sa.func.count()).select_from(tables.channel_inbound_updates)
                )
            ).scalar_one()
        )


async def test_a_start_over_the_webhook_links_and_a_replayed_message_is_acted_on_once(
    migrator: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    user_id = await _user(migrator)
    await _member(migrator, user_id)
    queue = SqlChannelUpdateQueue(sessions)
    bot, command = _Bot(), _Command()
    chat = f"chat-{uuid.uuid4().hex[:8]}"

    offer = await _linking(sessions).connect(user_id)
    await queue.enqueue("zalo", _hook_update(chat, f"/start {offer.code}", uuid.uuid4().hex))
    await _drain(sessions, bot, command)()

    assert await _linking(sessions).is_linked(user_id)

    replayed = _hook_update(chat, "báo cáo hôm nay", uuid.uuid4().hex)
    await queue.enqueue("zalo", replayed)
    await queue.enqueue("zalo", replayed)
    await _drain(sessions, bot, command)()

    assert [m.text for m, _ in command.seen] == ["báo cáo hôm nay"]
    assert await _queued(migrator) == 0


async def test_two_drains_at_once_never_take_the_same_update(
    migrator: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    queue = SqlChannelUpdateQueue(sessions)
    marker = uuid.uuid4().hex
    for n in range(30):
        await queue.enqueue("zalo", {"message": {"text": f"{marker}-{n}"}})

    first, second = await asyncio.gather(queue.take("zalo", 50), queue.take("zalo", 50))

    texts = [u["message"]["text"] for u in [*first, *second]]
    mine = [t for t in texts if t.startswith(marker)]
    assert len(mine) == len(set(mine)) == 30
    # Oldest first within one take.
    order = [int(u["message"]["text"].rsplit("-", 1)[1]) for u in first if marker in str(u)]
    assert order == sorted(order)


async def test_an_update_no_worker_took_within_a_day_is_pruned(
    migrator: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """A queued update holds a person's words; one left behind (the lane was
    down) does not stay for ever (failure-modes #6)."""
    stale, fresh = uuid.uuid4(), uuid.uuid4()
    async with migrator.begin() as conn:
        await conn.execute(
            sa.insert(tables.channel_inbound_updates),
            [
                {
                    "id": stale,
                    "channel": "zalo",
                    "payload": {"message": {"text": "old"}},
                    "received_at": datetime.now(UTC) - timedelta(hours=25),
                },
                {
                    "id": fresh,
                    "channel": "zalo",
                    "payload": {"message": {"text": "new"}},
                    "received_at": datetime.now(UTC),
                },
            ],
        )

    await SqlChannelInboundRetention(session_factory=sessions).prune()

    async with migrator.connect() as conn:
        left = set(
            (
                await conn.execute(
                    sa.select(tables.channel_inbound_updates.c.id).where(
                        tables.channel_inbound_updates.c.id.in_([stale, fresh])
                    )
                )
            ).scalars()
        )
    assert left == {fresh}
