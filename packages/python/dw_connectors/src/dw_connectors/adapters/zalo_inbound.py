"""The one entry for a Zalo bot update, whichever way it arrived.

The worker's poll lane calls ``ZaloInbound.handle`` for each update; in webhook
mode (channels Z3) the worker's drain lane calls the same method with
the update the API's webhook queued, unchanged. Both paths therefore split
updates one way:

- ``/start`` and ``/stop`` (the first word) go to the link flow,
  ``zalo_link.handle_update``, exactly as before Z4;
- any other text goes to the inbound router as an ``InboundMessage`` keyed by
  Zalo's message id, where the chat is resolved to a linked person, the id is
  claimed once, and the registered commands are asked (``dw_connectors.inbound``);
- an update from a chat with no text (a photo, a sticker) gets one fixed
  sentence, ``NO_PHOTOS``, and nothing is stored or downloaded: the shape of a
  Bot Platform photo update has not been measured yet (failure-modes #4;
  zalo-channel ticket 04b), so no code reads one. Images are uploaded on the
  case's page after it is created;
- an update with no chat is left alone.

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
NO_PHOTOS = "Mình chưa nhận ảnh qua Zalo; anh/chị tải ảnh ở trang hồ sơ sau khi tạo."


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
        if not zalo_id:
            return
        if not text:
            await self._reply(zalo_id, NO_PHOTOS)
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
            await self._reply(zalo_id, NOT_HANDLED)
            return
        await self.router.route(
            InboundMessage(channel=CHANNEL, message_id=message_id, chat_id=zalo_id, text=text)
        )

    async def _reply(self, zalo_id: str, text: str) -> None:
        if self.sender is not None:
            with contextlib.suppress(RuntimeError):
                await self.sender.send_message(zalo_id, text)
