"""The one entry for a Zalo bot update, whichever way it arrived.

The worker's poll lane calls ``ZaloInbound.handle`` for each update today; the
API webhook (zalo-channel ticket 03) calls the same method with the update it
was POSTed, unchanged. Both paths therefore split updates one way:

- ``/start`` and ``/stop`` (the first word) go to the link flow,
  ``zalo_link.handle_update``, exactly as before Z4;
- any other text goes to the inbound router as an ``InboundMessage`` keyed by
  Zalo's message id, where the chat is resolved to a linked person, the id is
  claimed once, and the registered commands are asked (``dw_connectors.inbound``);
- an update with no chat or no text (a sticker, a photo) is left alone.

A text message without a message id cannot be deduplicated, so it is not
routed: it is logged as dropped and the chat is told it was not handled.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from dw_connectors.adapters.zalo_link import (
    LINK_COMMANDS,
    ZaloLinkStore,
    handle_update,
    message_id_of,
    parse_update,
)
from dw_connectors.inbound import NOT_HANDLED, InboundMessage
from dw_connectors.ports import ChatSenderPort
from dw_kernel.ports import UtcClock

logger = logging.getLogger("dw_connectors.zalo_inbound")

CHANNEL = "zalo"


class InboundRoutePort(Protocol):
    async def route(self, message: InboundMessage) -> None: ...


@dataclass(frozen=True)
class ZaloInbound:
    link_secret: str
    store: ZaloLinkStore
    sender: ChatSenderPort | None
    clock: UtcClock
    product_name: str
    router: InboundRoutePort

    async def handle(self, update: dict[str, Any]) -> None:
        zalo_id, text = parse_update(update)
        if not zalo_id or not text:
            return
        if text.split()[0] in LINK_COMMANDS:
            await handle_update(
                update,
                link_secret=self.link_secret,
                store=self.store,
                sender=self.sender,
                clock=self.clock,
                product_name=self.product_name,
            )
            return
        message_id = message_id_of(update)
        if not message_id:
            logger.warning("zalo inbound: dropped a text message that carries no message id")
            if self.sender is not None:
                with contextlib.suppress(RuntimeError):
                    await self.sender.send_message(zalo_id, NOT_HANDLED)
            return
        await self.router.route(
            InboundMessage(channel=CHANNEL, message_id=message_id, chat_id=zalo_id, text=text)
        )
