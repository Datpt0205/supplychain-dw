"""Zalo updates over long-poll: getUpdates -> ZaloInbound.handle -> reply.

The local path (ADR 0008): with no public HTTPS host, Zalo cannot POST to the
API, so this lane asks for updates instead. ``getUpdates`` is a destructive
auto-ack read — each update comes back once and idle polls come back empty — so
no offset is tracked: each tick asks for the next batch. The ~25 s long-poll
inside ``get_updates`` is the real pacing; the registered interval only spaces
out retries after an error.

One bot answers one reader. Two processes polling the same bot (another
checkout, another product using the same token) each receive a share of the
updates and neither sees them all, so a ``/start`` lands in whichever process
asked first.

This lane only fetches and hands over. Each update goes to
``ZaloInbound.handle`` (``dw_connectors.adapters.zalo_inbound``), the same entry
the webhook will call: ``/start`` and ``/stop`` link and unlink a chat, and any
other text goes to the inbound router, which resolves the chat to its linked
person, claims the message id once, and runs the commands registered at the
composition root, each with a context built from that person's own membership
(ADR 0005 condition 2). Nothing here reads a context, a scope or a tenant out
of an update. Because ``getUpdates`` acknowledges on read, an update that fails
is not fetched again: the router records it as failed and tells the person.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from dw_connectors.ports import ChatSenderPort

logger = logging.getLogger("dw_worker.zalo_poll")


class ZaloUpdatesPort(ChatSenderPort, Protocol):
    """What this lane needs of the bot: read the next batch, and reply."""

    async def get_updates(self, offset: int) -> list[dict[str, Any]]: ...


class ZaloUpdateHandler(Protocol):
    """``dw_connectors.adapters.zalo_inbound.ZaloInbound``."""

    async def handle(self, update: dict[str, Any]) -> None: ...


def build_zalo_poll_consumer(
    bot: ZaloUpdatesPort, inbound: ZaloUpdateHandler
) -> Callable[[], Awaitable[None]]:
    async def consume() -> None:
        updates = await bot.get_updates(offset=0)
        for update in updates:
            try:
                await inbound.handle(update)
            except Exception:
                # One bad update (unparseable, a transient DB error) must not
                # drop the rest of the batch or stop the lane; it is auto-acked
                # already, so the user simply sends it again. Logged without
                # the update: it carries a chat id and the user's text.
                logger.exception("zalo poll: failed to handle an update")

    return consume
