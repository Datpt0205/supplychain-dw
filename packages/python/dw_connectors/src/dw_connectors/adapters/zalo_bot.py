"""Zalo Bot Platform client (bot.zaloplatforms.com) — Telegram-style dialect.

Token rides in the URL; ``getUpdates`` long-polls (no public webhook needed
for local dev); ``sendMessage`` posts plain text. Responses wrap payloads in
{"ok": bool, "result": ..., "description": ...} exactly like Telegram.

The token in the URL is a credential (SEC-20): whoever reads it can read and
send as the bot. httpx puts the full URL in its exception text and logs every
request URL at INFO, so this module scrubs the token from both — an error
raised from here and the ``httpx`` log line name the endpoint, never the token.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from dw_connectors.ports import ChatRecipientUnreachableError

_BASE = "https://bot-api.zaloplatforms.com"
_REDACTED = "bot***"

# Zalo hard-caps one message at 2000 characters; stay under it with room to
# spare. Without the split a long message is refused and never delivered.
_MAX_CHARS = 1900

# Codes on which ``sendMessage`` will never succeed for this chat, whether they
# come as the HTTP status or as ``error_code`` in an ``ok: false`` body: the
# Telegram dialect answers 400 "chat not found" and 403 "blocked by the user".
# PROVISIONAL (channels Z2): taken from the dialect, not measured
# against Zalo; a live run confirms or corrects it. Everything
# else - 401 included, which is this deployment's token and not the person's
# chat - is retried, up to the outbox's ceiling.
_UNREACHABLE = frozenset({400, 403, 404})


def _split_for_zalo(text: str) -> list[str]:
    """Whole message when it fits, else consecutive parts split on line breaks.

    Splitting on lines keeps a list's bullets intact; a single line longer than
    the cap is cut by length as a last resort.
    """
    if len(text) <= _MAX_CHARS:
        return [text]
    parts: list[str] = []
    current = ""
    for line in text.splitlines():
        while len(line) > _MAX_CHARS:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:_MAX_CHARS])
            line = line[_MAX_CHARS:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > _MAX_CHARS:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


class _RedactTokens(logging.Filter):
    """Rewrites any registered bot token out of a log record before it is emitted.

    Installed on the ``httpx`` logger, whose INFO line carries the request URL.
    Formatting the record here and dropping its args is what makes the scrub
    total: the token may sit in an arg (the URL) rather than in the format string.
    """

    def __init__(self) -> None:
        super().__init__()
        self.tokens: set[str] = set()

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        scrubbed = message
        for token in self.tokens:
            scrubbed = scrubbed.replace(f"bot{token}", _REDACTED).replace(token, "***")
        if scrubbed != message:
            record.msg, record.args = scrubbed, None
        return True


_HTTPX_REDACTOR = _RedactTokens()
logging.getLogger("httpx").addFilter(_HTTPX_REDACTOR)


@dataclass(frozen=True)
class ZaloBotClient:
    bot_token: str
    poll_timeout: int = 25

    def __post_init__(self) -> None:
        if self.bot_token:
            _HTTPX_REDACTOR.tokens.add(self.bot_token)

    def __repr__(self) -> str:
        # The dataclass default would print the token into any log or traceback
        # that formats this object.
        return f"ZaloBotClient(bot_token={_REDACTED!r}, poll_timeout={self.poll_timeout})"

    def _scrub(self, text: str) -> str:
        return text.replace(f"bot{self.bot_token}", _REDACTED).replace(self.bot_token, "***")

    @asynccontextmanager
    async def _client(self, timeout: float) -> AsyncIterator[httpx.AsyncClient]:
        """An httpx client whose failures come out without the token in them.

        ``from None``: the original exception's text holds the URL, and a chained
        cause is printed in full by every traceback formatter.
        """
        try:
            async with httpx.AsyncClient(
                base_url=f"{_BASE}/bot{self.bot_token}", timeout=timeout
            ) as client:
                yield client
        except httpx.HTTPError as exc:
            raise RuntimeError(f"zalo request failed: {self._scrub(str(exc))}") from None

    async def get_updates(self, offset: int) -> list[dict[str, Any]]:
        async with self._client(self.poll_timeout + 10) as client:
            response = await client.get(
                "/getUpdates",
                params={"offset": offset, "timeout": self.poll_timeout},
            )
            response.raise_for_status()
            data = response.json()
        if not data.get("ok"):
            # A long-poll that saw no message ends with 408 "Request timeout".
            # That is the idle steady state, not a failure — a caller looping on
            # getUpdates would otherwise crash on every quiet poll.
            if data.get("error_code") == 408:
                return []
            raise RuntimeError(
                f"zalo getUpdates failed: {self._scrub(str(data.get('description')))}"
            )
        # Zalo returns ``result`` as a single update object (not a Telegram-style
        # array). Normalise both shapes so callers always see a list.
        result = data.get("result")
        if result is None:
            return []
        return result if isinstance(result, list) else [result]

    async def send_message(self, chat_id: str, text: str) -> str:
        """Send plain text, split into parts Zalo accepts; returns the last part's id."""
        message_id = ""
        for part in _split_for_zalo(text):
            message_id = await self._send_one(chat_id, part)
        return message_id

    async def _send_one(self, chat_id: str, text: str) -> str:
        async with self._client(15) as client:
            response = await client.post("/sendMessage", json={"chat_id": chat_id, "text": text})
            if response.status_code in _UNREACHABLE:
                raise ChatRecipientUnreachableError(
                    f"zalo sendMessage refused: HTTP {response.status_code}"
                )
            response.raise_for_status()
            data = response.json()
        if not data.get("ok"):
            if data.get("error_code") in _UNREACHABLE:
                raise ChatRecipientUnreachableError(
                    f"zalo sendMessage refused: {data.get('error_code')} "
                    f"{self._scrub(str(data.get('description')))}"
                )
            raise RuntimeError(
                f"zalo sendMessage failed: {self._scrub(str(data.get('description')))}"
            )
        result = data.get("result") or {}
        return str(result.get("message_id", ""))

    async def set_webhook(self, url: str, *, secret_token: str) -> None:
        """Point the bot at ``url`` so updates are POSTed there instead of polled.

        Zalo sends ``secret_token`` back in the ``X-Bot-Api-Secret-Token``
        header of every call, which is how the API tells Zalo from anyone else
        (ADR 0008); the URL carries no secret, so none lands in an
        access log. A failure's text has both the token and the secret scrubbed.
        """
        async with self._client(15) as client:
            response = await client.post(
                "/setWebhook", json={"url": url, "secret_token": secret_token}
            )
            response.raise_for_status()
            data = response.json()
        if not data.get("ok"):
            description = self._scrub(str(data.get("description"))).replace(secret_token, "***")
            raise RuntimeError(f"zalo setWebhook failed: {description}")

    async def delete_webhook(self) -> None:
        """Remove the webhook so ``getUpdates`` long-polling works again."""
        async with self._client(15) as client:
            response = await client.get("/deleteWebhook")
            response.raise_for_status()

    async def get_webhook_info(self) -> dict[str, Any]:
        """The current webhook target (empty ``url`` means none is set)."""
        async with self._client(15) as client:
            response = await client.get("/getWebhookInfo")
            response.raise_for_status()
            data = response.json()
        return dict(data.get("result") or {})
