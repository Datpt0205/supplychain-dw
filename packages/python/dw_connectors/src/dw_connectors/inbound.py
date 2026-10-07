"""Inbound chat messages from linked people: who sent it, once, and which command acts.

A message that is not a link command (``/start``, ``/stop`` — those stay with the
link flow in ``adapters.zalo_link``) arrives here as an ``InboundMessage``, and
``InboundRouter.route`` decides, in this order:

1. **Who.** The chat's link row names the user (``ChannelIdentityPort``), read
   afresh for every message. No link: one sentence on how to link, nothing
   recorded, no command and no model call.
2. **Once.** The message id is claimed (``InboundLedgerPort.claim``) and
   committed before anything acts; an id seen before is skipped. After acting
   its outcome is settled — ``done``, ``ignored``, or ``failed`` when a command
   raised, in which case the person gets a short error. An id is never
   processed twice: a poll's ``getUpdates`` acknowledges on read and a webhook
   may redeliver, so the id is the one thing both paths share.
3. **Where.** The workspace the person chose on ``/settings``, else their only
   membership. Several and no choice: a link to ``/settings``. None: a refusal.
4. **What.** Each command registered at the composition root
   (``ChannelCommandRegistry``), in registration order, gets an access context
   built for that workspace from the person's own membership and cut to the
   command's declared ceiling (``LinkedAccessPort.access_for``); the first that
   handles the message ends the walk. A refused context (membership gone,
   tenant locked) ends it with a refusal. No command took it: "Mình chưa xử lý
   được tin này".

Nothing in the message text names the user, the tenant, the workspace or a
scope: each comes from a database read keyed by the step before it (ADR 0005
condition 2). The context type is a parameter so this package needs no
platform import; the composition root binds it to the platform's
``AccessContext`` (``dw_platform.application.channel_access``).
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from uuid import UUID

from dw_connectors.ports import ChatSenderPort

logger = logging.getLogger("dw_connectors.inbound")

Outcome = Literal["done", "ignored", "failed"]
Reply = Callable[[str], Awaitable[None]]

NOT_HANDLED = "Mình chưa xử lý được tin này."
FAILED = "Có lỗi khi xử lý tin này. Anh/chị gửi lại sau ít phút giúp mình."
NO_WORKSPACE = (
    "Tài khoản của anh/chị chưa thuộc workspace nào, nên chưa gửi lệnh qua chat được. "
    "Liên hệ quản trị viên để được thêm vào."
)
REFUSED = (
    "Anh/chị không còn dùng được workspace này (đã rời workspace hoặc workspace đang bị khóa). "
    "Kiểm tra lại ở trang Cài đặt cá nhân."
)


def choose_workspace_reply(settings_url: str) -> str:
    return (
        "Anh/chị thuộc nhiều workspace. Chọn “Workspace dùng cho Zalo” ở "
        f"{settings_url} rồi nhắn lại giúp mình."
    )


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """One text message from a chat, as the channel delivered it.

    ``message_id`` is the channel's own id for the message, the dedupe key;
    ``chat_id`` is where it came from and where replies go.
    """

    channel: str
    message_id: str
    chat_id: str
    text: str


# ---- what the router is handed ---------------------------------------------


class ChannelIdentityPort(Protocol):
    """Chat -> the person it is linked to; None when it is linked to nobody."""

    async def user_id_for(self, chat_id: str, /) -> UUID | None: ...


class InboundLedgerPort(Protocol):
    async def claim(self, channel: str, message_id: str, user_id: UUID, /) -> bool:
        """Record the id as in progress and commit; False when it was seen before."""
        ...

    async def settle(self, channel: str, message_id: str, outcome: Outcome, /) -> None: ...


class LinkedAccessPort[ContextT](Protocol):
    """The person's workspaces and the context a command may act with in one."""

    async def chosen_workspace(self, user_id: UUID, /) -> tuple[UUID, UUID] | None: ...

    async def workspaces_of(self, user_id: UUID, /) -> Sequence[tuple[UUID, UUID]]: ...

    async def access_for(
        self, user_id: UUID, tenant_id: UUID, workspace_id: UUID, ceiling: frozenset[str], /
    ) -> ContextT | None: ...


class ChannelCommand[ContextT](Protocol):
    """One thing a linked person can ask for through a chat.

    ``ceiling`` is every scope the command could need; the context it receives
    holds the membership's scopes cut to it, and no role. ``handle`` returns
    True when the message was this command's to act on (it has replied), False
    to let the next command look at it.
    """

    @property
    def ceiling(self) -> frozenset[str]: ...

    async def handle(self, message: InboundMessage, context: ContextT, reply: Reply) -> bool: ...


@dataclass
class ChannelCommandRegistry[ContextT]:
    """The commands a deployment answers, in the order they are asked.

    Filled at the composition root. Order is policy: a pending decision (Z5)
    before an open conversation before intent classification, so a reply to a
    question the bot asked is never re-read as a new request.
    """

    _commands: list[tuple[str, ChannelCommand[ContextT]]] = field(default_factory=list)

    def register(self, name: str, command: ChannelCommand[ContextT]) -> None:
        if not name.strip():
            raise ValueError("a channel command needs a name")
        if any(existing == name for existing, _ in self._commands):
            raise ValueError(f"channel command {name!r} is already registered")
        self._commands.append((name, command))

    def commands(self) -> list[tuple[str, ChannelCommand[ContextT]]]:
        return list(self._commands)


class InboundUpdateInboxPort(Protocol):
    """Where a webhook leaves an update it accepted, for the worker to handle.

    The API's side of the hand-over (ADR 0008): it only queues, so
    the webhook answers at once and every update is handled by the one inbound
    entry the worker runs for both paths.
    """

    async def enqueue(self, channel: str, update: dict[str, Any], /) -> None: ...


# ---- the router -------------------------------------------------------------


@dataclass(frozen=True)
class InboundRouter[ContextT]:
    identities: ChannelIdentityPort
    ledger: InboundLedgerPort
    access: LinkedAccessPort[ContextT]
    commands: ChannelCommandRegistry[ContextT]
    sender: ChatSenderPort | None
    # What an unlinked chat is told: one sentence on how to link.
    unlinked_reply: str
    # Where a person with several workspaces picks one.
    settings_url: str

    async def route(self, message: InboundMessage) -> None:
        async def reply(text: str) -> None:
            if self.sender is not None:
                # Best effort: what was decided stands whether or not the
                # reply reaches the chat.
                with contextlib.suppress(RuntimeError):
                    await self.sender.send_message(message.chat_id, text)

        user_id = await self.identities.user_id_for(message.chat_id)
        if user_id is None:
            await reply(self.unlinked_reply)
            return
        if not await self.ledger.claim(message.channel, message.message_id, user_id):
            logger.info("inbound %s: message already seen, skipped", message.channel)
            return
        try:
            outcome = await self._dispatch(message, user_id, reply)
        except Exception:
            # Logged without the message: it carries a chat id and the text.
            logger.exception("inbound %s: a command failed", message.channel)
            await self.ledger.settle(message.channel, message.message_id, "failed")
            await reply(FAILED)
            return
        await self.ledger.settle(message.channel, message.message_id, outcome)

    async def _dispatch(self, message: InboundMessage, user_id: UUID, reply: Reply) -> Outcome:
        target = await self.access.chosen_workspace(user_id)
        if target is None:
            workspaces = await self.access.workspaces_of(user_id)
            if not workspaces:
                await reply(NO_WORKSPACE)
                return "ignored"
            if len(workspaces) > 1:
                await reply(choose_workspace_reply(self.settings_url))
                return "ignored"
            target = workspaces[0]
        tenant_id, workspace_id = target
        for _name, command in self.commands.commands():
            context = await self.access.access_for(
                user_id, tenant_id, workspace_id, command.ceiling
            )
            if context is None:
                await reply(REFUSED)
                return "ignored"
            if await command.handle(message, context, reply):
                return "done"
        await reply(NOT_HANDLED)
        return "ignored"
