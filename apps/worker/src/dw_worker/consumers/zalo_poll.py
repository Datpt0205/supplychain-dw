"""Zalo self-link over long-poll: getUpdates -> handle_update -> reply.

The local path (ADR 0015): with no public HTTPS host, Zalo cannot POST to the
API, so this lane asks for updates instead. ``getUpdates`` is a destructive
auto-ack read — each update comes back once and idle polls come back empty — so
no offset is tracked: each tick asks for the next batch. The ~25 s long-poll
inside ``get_updates`` is the real pacing; the registered interval only spaces
out retries after an error.

One bot answers one reader. Two processes polling the same bot (another
checkout, another product using the same token) each receive a share of the
updates and neither sees them all, so a ``/start`` lands in whichever process
asked first.

Nothing here builds an access context from a chat id: this lane links and
unlinks a chat, and replies. Inbound commands that act for a user (Z4-Z6) build
their context by a separate path, from the linked user's own membership.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from dw_connectors.adapters.zalo_link import ZaloLinkStore, handle_update
from dw_connectors.ports import ChatSenderPort
from dw_kernel.ports import UtcClock

logger = logging.getLogger("dw_worker.zalo_poll")


class ZaloUpdatesPort(ChatSenderPort, Protocol):
    """What this lane needs of the bot: read the next batch, and reply."""

    async def get_updates(self, offset: int) -> list[dict[str, Any]]: ...


def build_zalo_poll_consumer(
    bot: ZaloUpdatesPort,
    store: ZaloLinkStore,
    *,
    link_secret: str,
    clock: UtcClock,
    product_name: str,
) -> Callable[[], Awaitable[None]]:
    async def consume() -> None:
        updates = await bot.get_updates(offset=0)
        for update in updates:
            try:
                await handle_update(
                    update,
                    link_secret=link_secret,
                    store=store,
                    sender=bot,
                    clock=clock,
                    product_name=product_name,
                )
            except Exception:
                # One bad update (unparseable, a transient DB error) must not
                # drop the rest of the batch or stop the lane; it is auto-acked
                # already, so the user simply sends /start again. Logged
                # without the update: it carries a chat id and the user's text.
                logger.exception("zalo poll: failed to handle an update")

    return consume
