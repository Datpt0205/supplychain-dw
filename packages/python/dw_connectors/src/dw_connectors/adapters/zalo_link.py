"""Shared Zalo self-link flow: issue a one-time token, parse a bot update, act, reply.

A signed-in user asks the app for a ``/start <token>`` line (``ZaloLinking``),
sends it to the bot, and the bot's update is handled here (``handle_update``)
whichever way it arrives — the worker's long-poll today, the API webhook once a
public host exists. Both callers parse the same update shape and write the same
row, so the parse, the token codec and the branch on ``text`` live here once;
each caller supplies only a store and a sender.

The token is single-use. Its HMAC proves the app minted it for this user and
when it expires; its ``jti`` names a nonce row the store consumes in the same
transaction that writes the link, with one conditional UPDATE (see
``SqlZaloLink.redeem``). A token seen by someone else — a screenshot, a shared
screen — therefore links at most once, and the second ``/start`` is refused.

This module owns the two link commands, ``/start <token>`` and ``/stop``; given
anything else, ``handle_update`` answers with ``link_help`` and changes nothing.
Since Z4 every other text from a chat goes elsewhere: ``adapters.zalo_inbound``
sends ``/start`` and ``/stop`` here and the rest to the inbound router
(``dw_connectors.inbound``). There an unlinked chat gets ``link_help`` too, and a
linked person's message reaches the commands registered at the composition
root, each acting with a context built from that person's own membership and
cut to the command's ceiling — never from the chat. So free text can act through
a registered command a product context plugs in, but no free-text word unlinks
anyone, and an approval is decided only under ADR 0007's rule (Z5). This module
builds no context at all.
"""

from __future__ import annotations

import base64
import contextlib
import hmac
import secrets
import struct
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, Protocol

from dw_connectors.inbound import ChannelIdentityPort
from dw_connectors.ports import ChatSenderPort
from dw_kernel.ports import UtcClock

TOKEN_TTL = timedelta(minutes=15)

_START = "/start"
_STOP = "/stop"
_JTI_BYTES = 8
_SIG_BYTES = 8
# 16-byte user id + 4-byte expiry (unix seconds) + jti; the HMAC tag follows.
_BODY_BYTES = 16 + 4 + _JTI_BYTES


# ---- signed one-time connect token -------------------------------------------


@dataclass(frozen=True, slots=True)
class ConnectToken:
    """What a verified token claims. The nonce row decides whether it is still good."""

    user_id: uuid.UUID
    jti: str
    expires_at: datetime


def _sign(body: bytes, secret: str) -> bytes:
    return hmac.new(secret.encode(), body, sha256).digest()[:_SIG_BYTES]


def make_connect_token(
    user_id: uuid.UUID, secret: str, *, now: datetime, ttl: timedelta = TOKEN_TTL
) -> tuple[str, ConnectToken]:
    """Mint a token for ``user_id``; returns the text to send and its claims.

    The expiry is truncated to whole seconds once, here, and the same value goes
    into the token and (through the caller) into the nonce row, so the two can
    never disagree about when the token lapses.
    """
    expires = int((now + ttl).timestamp())
    jti = secrets.token_bytes(_JTI_BYTES)
    body = user_id.bytes + struct.pack(">I", expires) + jti
    text = base64.urlsafe_b64encode(body + _sign(body, secret)).decode().rstrip("=")
    return text, ConnectToken(user_id, jti.hex(), datetime.fromtimestamp(expires, tz=UTC))


def verify_connect_token(token: str, secret: str, *, now: datetime) -> ConnectToken | None:
    """The token's claims when its signature holds and it has not expired, else None.

    A valid signature is not enough to link: the caller must still consume the
    nonce, which is what refuses a token used once already.
    """
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except ValueError:
        return None
    if len(raw) != _BODY_BYTES + _SIG_BYTES:
        return None
    body, sig = raw[:_BODY_BYTES], raw[_BODY_BYTES:]
    if not hmac.compare_digest(sig, _sign(body, secret)):
        return None
    expires_at = datetime.fromtimestamp(struct.unpack(">I", body[16:20])[0], tz=UTC)
    if now >= expires_at:
        return None
    return ConnectToken(uuid.UUID(bytes=body[:16]), body[20:].hex(), expires_at)


# ---- the store each side is handed -------------------------------------------


class ZaloLinkStore(ChannelIdentityPort, Protocol):
    """What the bot side reads and writes — see ``dw_platform...zalo_link_repo.SqlZaloLink``.

    Injected so this module stays free of any database dependency. The
    inherited ``user_id_for`` is the chat-to-person step the inbound router
    starts every message from.
    """

    async def redeem(self, token: ConnectToken, zalo_id: str) -> bool:
        """Consume the token's nonce and link, in one transaction.

        False — and nothing written — when the nonce is unknown, belongs to
        another user, was used already or has expired, or when the user holds
        no membership to record the link in. A link that lands is audited and
        announced to the user in the same transaction.
        """
        ...

    async def unlink_by_zalo(self, zalo_id: str) -> bool:
        """Remove this chat's link; False when it was not linked."""
        ...


class ZaloAccountStore(Protocol):
    """What the signed-in user's side reads and writes — the settings page."""

    async def zalo_id_for(self, user_id: uuid.UUID) -> str | None: ...

    async def issue_nonce(self, token: ConnectToken) -> None: ...

    async def unlink_by_user(self, user_id: uuid.UUID) -> None: ...


# ---- the signed-in user's side ------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConnectOffer:
    code: str
    deep_link: str | None
    expires_at: datetime


@dataclass(frozen=True)
class ZaloLinking:
    """Status, connect and disconnect for one verified user.

    Takes a user id and nothing else about the caller: the route hands it the
    principal from the verified access context, never a value from the request.
    """

    store: ZaloAccountStore
    link_secret: str
    clock: UtcClock
    bot_link: str = ""

    async def is_linked(self, user_id: uuid.UUID) -> bool:
        return await self.store.zalo_id_for(user_id) is not None

    async def connect(self, user_id: uuid.UUID) -> ConnectOffer:
        code, token = make_connect_token(user_id, self.link_secret, now=self.clock.now())
        await self.store.issue_nonce(token)
        base = self.bot_link.strip()
        deep_link = f"{base}{'&' if '?' in base else '?'}start={code}" if base else None
        return ConnectOffer(code=code, deep_link=deep_link, expires_at=token.expires_at)

    async def disconnect(self, user_id: uuid.UUID) -> None:
        await self.store.unlink_by_user(user_id)


# ---- the bot's side -----------------------------------------------------------


def _message(update: dict[str, Any]) -> dict[str, Any]:
    message: dict[str, Any] = (update.get("result") or update).get("message") or {}
    return message


def message_id_of(update: dict[str, Any]) -> str:
    """The channel's id for the message, or "" when the update carries none.

    Read from ``message.message_id`` as Zalo's Bot Platform documents it; the
    poll fixture captured in Z1 predates any use of it, and a live run against
    the real bot is where it gets measured. Empty means the message cannot
    be deduplicated, and the caller must not act on it.
    """
    return str(_message(update).get("message_id") or "")


def parse_update(update: dict[str, Any]) -> tuple[str, str]:
    """Return ``(zalo_id, text)`` from a bot update; either may be empty.

    Zalo wraps the message under ``result`` on the poll path and delivers it at
    the top level on the webhook path; the chat id is under ``chat`` (private
    chat) or ``from``. Both shapes are normalised here.
    """
    message = _message(update)
    text = (message.get("text") or "").strip()
    chat = message.get("chat") or message.get("from") or {}
    return str(chat.get("id") or ""), text


LINK_COMMANDS = (_START, _STOP)


def link_help(product_name: str) -> str:
    """The one sentence on how to link: an unlinked chat's answer to anything,
    and the answer to a link command this module cannot read."""
    return (
        f"Để kết nối Zalo với tài khoản {product_name}, lấy mã ở trang Cài đặt cá nhân "
        "rồi gửi /start <mã>; gửi /stop để ngắt kết nối."
    )


async def handle_update(
    update: dict[str, Any],
    *,
    link_secret: str,
    store: ZaloLinkStore,
    sender: ChatSenderPort | None,
    clock: UtcClock,
    product_name: str,
) -> None:
    """Act on one update: ``/start <token>`` links, ``/stop`` unlinks, anything else is help."""
    zalo_id, text = parse_update(update)
    if not zalo_id or not text:
        return

    async def reply(msg: str) -> None:
        if sender is not None:
            # Best effort: the link is already written (or refused); a reply
            # that fails to send must not undo or repeat it.
            with contextlib.suppress(RuntimeError):
                await sender.send_message(zalo_id, msg)

    words = text.split()
    if words[0] == _START and len(words) == 2:
        token = verify_connect_token(words[1], link_secret, now=clock.now())
        if token is None or not await store.redeem(token, zalo_id):
            await reply(
                "Mã kết nối không hợp lệ, đã được dùng hoặc đã hết hạn. "
                f"Vào Cài đặt cá nhân trong {product_name} và bấm “Kết nối Zalo” để lấy mã mới."
            )
            return
        await reply(
            f"Đã kết nối Zalo với tài khoản {product_name} của bạn. "
            "Thông báo sẽ được gửi tới đây. Gõ /stop để ngắt kết nối."
        )
    elif text == _STOP:
        if await store.unlink_by_zalo(zalo_id):
            await reply(
                "Đã ngắt kết nối Zalo. Muốn nhận lại thông báo, vào Cài đặt cá nhân "
                f"trong {product_name} và bấm “Kết nối Zalo”."
            )
        else:
            await reply("Zalo này chưa kết nối với tài khoản nào.")
    else:
        await reply(link_help(product_name))
