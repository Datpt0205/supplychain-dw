"""Register, remove or show this deployment's Zalo webhook (ADR 0008, channels Z3).

Usage:
  uv run python scripts/zalo_webhook.py set     # ZALO_UPDATES_MODE=webhook only
  uv run python scripts/zalo_webhook.py delete  # before going back to poll
  uv run python scripts/zalo_webhook.py info

Reads the API's own settings (``ApiSettings``: ``ZALO_BOT_TOKEN``,
``ZALO_WEBHOOK_SECRET``, ``ZALO_UPDATES_MODE``, ``DW_API_PUBLIC_BASE_URL``), so
the URL and secret registered with Zalo are exactly what the API checks. The
webhook URL is ``<DW_API_PUBLIC_BASE_URL>/api/v1/zalo/webhook``; the secret goes
to Zalo as ``secret_token`` and comes back in a header, never in the URL.

Prints neither the bot token nor the secret. ``set`` refuses outside webhook
mode: a webhook registered while the worker polls makes ``getUpdates`` fail,
and the order to switch is ``set`` with ``ZALO_UPDATES_MODE=webhook`` on both
api and worker, or ``delete`` before setting it back to ``poll``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable
from typing import Any, Protocol

from dw_api.settings import ApiSettings
from dw_connectors.adapters.zalo_bot import ZaloBotClient

WEBHOOK_PATH = "/api/v1/zalo/webhook"


class WebhookAdmin(Protocol):
    async def set_webhook(self, url: str, *, secret_token: str) -> None: ...

    async def delete_webhook(self) -> None: ...

    async def get_webhook_info(self) -> dict[str, Any]: ...


def webhook_url(settings: ApiSettings) -> str:
    return settings.public_base_url.rstrip("/") + WEBHOOK_PATH


def _refusal_to_set(settings: ApiSettings) -> str | None:
    if settings.zalo_updates_mode != "webhook":
        return "ZALO_UPDATES_MODE is not 'webhook'; the worker would still poll this bot"
    if not settings.zalo_webhook_secret.get_secret_value():
        return "ZALO_WEBHOOK_SECRET is not set"
    if not settings.public_base_url.startswith("https://"):
        return "DW_API_PUBLIC_BASE_URL must start with https://"
    return None


async def run(
    command: str,
    settings: ApiSettings,
    bot_for: Callable[[str], WebhookAdmin],
    out: Callable[[str], None],
) -> int:
    token = settings.zalo_bot_token.get_secret_value()
    if not token:
        out("ERROR: ZALO_BOT_TOKEN is not set")
        return 2
    bot = bot_for(token)
    if command == "set":
        refusal = _refusal_to_set(settings)
        if refusal is not None:
            out(f"ERROR: {refusal}")
            return 2
        await bot.set_webhook(
            webhook_url(settings), secret_token=settings.zalo_webhook_secret.get_secret_value()
        )
        out(f"webhook set: {webhook_url(settings)}")
        return 0
    if command == "delete":
        await bot.delete_webhook()
        out("webhook deleted; the bot can be polled again")
        return 0
    info = await bot.get_webhook_info()
    # Only the URL: whatever else Zalo returns is not ours to print unread.
    current = str(info.get("url") or "")
    out(f"webhook: {current or '(none)'}")
    if current and settings.public_base_url and current != webhook_url(settings):
        out(f"note: this deployment's webhook URL would be {webhook_url(settings)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["set", "delete", "info"])
    args = parser.parse_args(argv)
    return asyncio.run(
        run(
            args.command,
            ApiSettings(),
            lambda token: ZaloBotClient(bot_token=token),
            lambda line: print(line, file=sys.stderr if line.startswith("ERROR") else sys.stdout),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
