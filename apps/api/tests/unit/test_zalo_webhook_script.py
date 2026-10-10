"""``scripts/zalo_webhook.py``: registers what the API checks, prints no secret.

Never calls Zalo: the bot is a recording fake.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from dw_api.settings import ApiSettings

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[4]
TOKEN = "bot-token-never-printed"
SECRET = "w" * 40


def _load_script() -> ModuleType:  # scripts/ is not on the path; load by file
    path = REPO_ROOT / "scripts" / "zalo_webhook.py"
    spec = importlib.util.spec_from_file_location("zalo_webhook_script", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = _load_script()


class FakeBot:
    def __init__(self, info: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.info = info or {}

    async def set_webhook(self, url: str, *, secret_token: str) -> None:
        self.calls.append(("set", url, secret_token))

    async def delete_webhook(self) -> None:
        self.calls.append(("delete",))

    async def get_webhook_info(self) -> dict[str, Any]:
        self.calls.append(("info",))
        return self.info


def settings(**overrides: object) -> ApiSettings:
    values: dict[str, object] = {
        "zalo_bot_token": TOKEN,
        "zalo_webhook_secret": SECRET,
        "zalo_updates_mode": "webhook",
        "public_base_url": "https://api.example.com/",
    }
    values.update(overrides)
    return ApiSettings(**values)  # type: ignore[arg-type]


async def _run(command: str, s: ApiSettings, bot: FakeBot) -> tuple[int, list[str]]:
    lines: list[str] = []
    tokens: list[str] = []

    def bot_for(token: str) -> FakeBot:
        tokens.append(token)
        return bot

    code = await script.run(command, s, bot_for, lines.append)
    assert set(tokens) <= {TOKEN}
    return code, lines


async def test_set_registers_the_route_url_with_the_api_secret_and_prints_neither() -> None:
    bot = FakeBot()
    code, lines = await _run("set", settings(), bot)

    assert code == 0
    assert bot.calls == [("set", "https://api.example.com/api/v1/zalo/webhook", SECRET)]
    assert not any(SECRET in line or TOKEN in line for line in lines)


@pytest.mark.parametrize(
    "overrides",
    [
        {"zalo_updates_mode": "poll"},
        {"zalo_webhook_secret": ""},
        {"public_base_url": "http://api.example.com"},
        {"public_base_url": ""},
    ],
    ids=["poll-mode", "no-secret", "http", "no-base-url"],
)
async def test_set_refuses_what_the_api_would_not_serve(overrides: dict[str, object]) -> None:
    bot = FakeBot()
    code, lines = await _run("set", settings(**overrides), bot)

    assert code == 2
    assert bot.calls == []
    assert lines and lines[0].startswith("ERROR")


async def test_no_bot_token_calls_nothing() -> None:
    bot = FakeBot()
    code, _ = await _run("info", settings(zalo_bot_token=""), bot)
    assert code == 2 and bot.calls == []


async def test_delete_and_info_print_only_the_url() -> None:
    bot = FakeBot(info={"url": "https://old.example/hook", "secret_token": SECRET})
    code, lines = await _run("info", settings(), bot)
    assert code == 0
    assert lines[0] == "webhook: https://old.example/hook"
    assert not any(SECRET in line for line in lines)

    code, lines = await _run("delete", settings(zalo_updates_mode="poll"), bot)
    assert code == 0 and bot.calls[-1] == ("delete",)


def test_the_path_is_the_route_the_api_mounts() -> None:
    from dw_api.routes.v1.zalo import webhook_router

    paths = {route.path for route in webhook_router.routes}  # type: ignore[attr-defined]
    assert "/api/v1" + next(iter(paths)) == script.WEBHOOK_PATH
