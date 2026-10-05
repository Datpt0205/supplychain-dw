"""The Zalo self-link poll lane: when it is registered, and what one tick does.

Registration is all-or-nothing: a database, a bot token, a link secret and
``ZALO_UPDATES_MODE=poll``. Without the token it could neither poll nor reply;
without the secret it could not verify a ``/start``; in webhook mode the API
receives updates and a poll would steal them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from test_worker import bare_settings

from dw_connectors.adapters.zalo_link import ConnectToken, make_connect_token
from dw_worker.consumers.zalo_poll import build_zalo_poll_consumer
from dw_worker.main import build_registry

pytestmark = pytest.mark.unit

_DB = "postgresql+asyncpg://dw:dw@localhost/dw"
_SECRET = "poll-link-secret"
_NOW = datetime(2026, 10, 5, 8, 0, tzinfo=UTC)
_USER = uuid.UUID("9d3c3a4e-2b1f-4c55-8f2a-0e6b7c8d9a10")


@pytest.mark.parametrize(
    ("overrides", "registered"),
    [
        ({"zalo_bot_token": "tok", "zalo_link_secret": "sec"}, True),
        ({"zalo_bot_token": "", "zalo_link_secret": "sec"}, False),
        ({"zalo_bot_token": "tok", "zalo_link_secret": ""}, False),
        (
            {"zalo_bot_token": "tok", "zalo_link_secret": "sec", "zalo_updates_mode": "webhook"},
            False,
        ),
    ],
    ids=["poll-configured", "no-bot-token", "no-link-secret", "webhook-mode"],
)
def test_the_poll_lane_is_registered_only_when_fully_configured(
    overrides: dict[str, object], registered: bool
) -> None:
    lanes = build_registry(bare_settings(database_url=_DB, **overrides)).all()
    assert ("zalo_link_poll" in lanes) is registered


def test_no_database_no_poll_lane() -> None:
    lanes = build_registry(bare_settings(zalo_bot_token="tok", zalo_link_secret="sec")).all()
    assert "zalo_link_poll" not in lanes


def test_the_settings_never_print_the_bot_token() -> None:
    settings = bare_settings(zalo_bot_token="tok-123456789", zalo_link_secret="sec-987654321")
    assert "tok-123456789" not in repr(settings)
    assert "sec-987654321" not in repr(settings)


class _Clock:
    def now(self) -> datetime:
        return _NOW


class _FakeBot:
    def __init__(self, updates: list[dict[str, Any]]) -> None:
        self.updates = updates
        self.sent: list[tuple[str, str]] = []

    async def get_updates(self, offset: int) -> list[dict[str, Any]]:
        batch, self.updates = self.updates, []
        return batch

    async def send_message(self, conversation_id: str, text: str) -> str:
        self.sent.append((conversation_id, text))
        return "m"


class _FakeStore:
    """Redeems any issued jti once; ``boom`` chat ids raise like a DB fault."""

    def __init__(self, issued: set[str]) -> None:
        self.issued = issued
        self.links: dict[str, uuid.UUID] = {}

    async def redeem(self, token: ConnectToken, zalo_id: str) -> bool:
        if zalo_id == "boom":
            raise RuntimeError("database went away")
        if token.jti not in self.issued:
            return False
        self.issued.discard(token.jti)
        self.links[zalo_id] = token.user_id
        return True

    async def unlink_by_zalo(self, zalo_id: str) -> bool:
        return self.links.pop(zalo_id, None) is not None


def _consumer(bot: _FakeBot, store: _FakeStore) -> Any:
    return build_zalo_poll_consumer(
        bot, store, link_secret=_SECRET, clock=_Clock(), product_name="Cổng thử"
    )


async def test_one_tick_handles_both_payload_shapes_and_survives_a_bad_update() -> None:
    first, first_claims = make_connect_token(_USER, _SECRET, now=_NOW)
    second, second_claims = make_connect_token(_USER, _SECRET, now=_NOW)
    bot = _FakeBot(
        [
            # Poll shape: the message under `result`.
            {"result": {"message": {"chat": {"id": "boom"}, "text": f"/start {first}"}}},
            # A message at the top level, as getUpdates returns each list item.
            {"message": {"chat": {"id": "chat-ok"}, "text": f"/start {second}"}},
        ]
    )
    store = _FakeStore({first_claims.jti, second_claims.jti})

    await _consumer(bot, store)()

    assert store.links == {"chat-ok": _USER}
    assert [chat for chat, _ in bot.sent] == ["chat-ok"]
    assert "Đã kết nối Zalo" in bot.sent[0][1]


async def test_stop_in_a_poll_tick_unlinks() -> None:
    bot = _FakeBot([{"result": {"message": {"chat": {"id": "chat-1"}, "text": "/stop"}}}])
    store = _FakeStore(set())
    store.links = {"chat-1": _USER}

    await _consumer(bot, store)()

    assert store.links == {}
    assert "Đã ngắt kết nối Zalo" in bot.sent[0][1]


async def test_an_idle_tick_does_nothing() -> None:
    bot = _FakeBot([])
    await _consumer(bot, _FakeStore(set()))()
    assert bot.sent == []


def test_the_lane_never_builds_an_access_context_from_zalo_data() -> None:
    """ADR 0012 condition 2: inbound Zalo data never reaches the access-context
    factory. Z4-Z6 build their context by a separate path keyed by user_id.
    The API route has the same check in ``test_zalo_endpoint.py``."""
    from pathlib import Path

    import dw_connectors.adapters.zalo_link as link
    import dw_worker.consumers.zalo_poll as lane

    for module in (link, lane):
        source = Path(module.__file__ or "").read_text(encoding="utf-8")
        assert "access_context_factory" not in source, module.__name__
