"""Zalo updates the API's webhook queued: take -> ZaloInbound.handle.

The hosted path (ADR 0008). Zalo POSTs each update to
``/api/v1/zalo/webhook``; the API checks the secret, the size and the shape,
queues the update in ``platform.channel_inbound_updates`` and answers at once.
This lane takes what was queued and hands each update, unchanged, to
``ZaloInbound.handle`` — the same object, built by the same
``build_zalo_inbound``, that the poll lane feeds. So everything after the door
is one code path: ``/start`` and ``/stop`` link and unlink, any other text goes
to the inbound router, which resolves the chat to its linked person and claims
the message id once in ``channel_inbound_messages`` — the dedupe that makes a
redelivered update act once.

Registered only in ``ZALO_UPDATES_MODE=webhook``, where the poll lane is not:
one bot, one reader. Taking deletes, so like ``getUpdates`` an update that fails
is not handed out again; the router records it as failed and tells the person.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from dw_connectors.adapters.zalo_inbound import CHANNEL
from dw_worker.consumers.zalo_poll import ZaloUpdateHandler

logger = logging.getLogger("dw_worker.zalo_webhook")

# Updates taken per tick. A tick is a cheap query when the queue is empty, and
# each update may cost a model call, so a small batch keeps one tick short.
BATCH = 20
# How often the lane looks. A person waits this long, at most, for the bot to
# start answering.
INTERVAL_SECONDS = 2.0


class ZaloUpdateQueuePort(Protocol):
    """``dw_platform...channel_inbound.SqlChannelUpdateQueue``."""

    async def take(self, channel: str, limit: int, /) -> list[dict[str, Any]]: ...


def build_zalo_webhook_consumer(
    queue: ZaloUpdateQueuePort, inbound: ZaloUpdateHandler
) -> Callable[[], Awaitable[None]]:
    async def consume() -> None:
        for update in await queue.take(CHANNEL, BATCH):
            try:
                await inbound.handle(update)
            except Exception:
                # One bad update must not drop the rest of the batch or stop
                # the lane. Logged without the update: it carries a chat id
                # and the person's words.
                logger.exception("zalo webhook: failed to handle an update")

    return consume
