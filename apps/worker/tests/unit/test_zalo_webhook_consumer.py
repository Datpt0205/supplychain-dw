"""The Zalo webhook drain lane: when it is registered, and what one tick does.

ADR 0015 amendment Z3: in webhook mode the API queues each update it was POSTed
and this lane hands it to the same ``ZaloInbound.handle`` the poll lane calls.
The two lanes are exclusive by ``ZALO_UPDATES_MODE``: one bot, one reader.
"""

from __future__ import annotations

from typing import Any

import pytest
from test_worker import bare_settings

from dw_worker.consumers.zalo_webhook import BATCH, build_zalo_webhook_consumer
from dw_worker.main import build_registry

pytestmark = pytest.mark.unit

_DB = "postgresql+asyncpg://dw:dw@localhost/dw"


@pytest.mark.parametrize(
    ("overrides", "drain", "poll"),
    [
        ({"zalo_updates_mode": "webhook"}, True, False),
        ({"zalo_updates_mode": "poll"}, False, True),
        ({"zalo_updates_mode": "webhook", "zalo_bot_token": ""}, False, False),
        ({"zalo_updates_mode": "webhook", "zalo_link_secret": ""}, False, False),
    ],
    ids=["webhook", "poll", "no-bot-token", "no-link-secret"],
)
def test_exactly_one_reader_per_mode(overrides: dict[str, object], drain: bool, poll: bool) -> None:
    settings = bare_settings(
        database_url=_DB, **{"zalo_bot_token": "tok", "zalo_link_secret": "sec", **overrides}
    )
    lanes = build_registry(settings).all()
    assert ("zalo_webhook_drain" in lanes) is drain
    assert ("zalo_link_poll" in lanes) is poll


def test_no_database_no_drain_lane() -> None:
    settings = bare_settings(
        zalo_bot_token="tok", zalo_link_secret="sec", zalo_updates_mode="webhook"
    )
    assert "zalo_webhook_drain" not in build_registry(settings).all()


class FakeQueue:
    def __init__(self, updates: list[dict[str, Any]]) -> None:
        self.updates = updates
        self.asked: list[tuple[str, int]] = []

    async def take(self, channel: str, limit: int) -> list[dict[str, Any]]:
        self.asked.append((channel, limit))
        batch, self.updates = self.updates[:limit], self.updates[limit:]
        return batch


class RecordingInbound:
    def __init__(self, fail_on: str | None = None) -> None:
        self.handled: list[dict[str, Any]] = []
        self.fail_on = fail_on

    async def handle(self, update: dict[str, Any]) -> None:
        if update.get("id") == self.fail_on:
            raise RuntimeError("boom")
        self.handled.append(update)


async def test_one_tick_hands_each_update_unchanged_and_a_failure_spares_the_rest() -> None:
    updates = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    inbound = RecordingInbound(fail_on="b")
    queue = FakeQueue(list(updates))

    await build_zalo_webhook_consumer(queue, inbound)()

    assert queue.asked == [("zalo", BATCH)]
    assert inbound.handled == [{"id": "a"}, {"id": "c"}]


async def test_an_empty_queue_is_a_quiet_tick() -> None:
    inbound = RecordingInbound()
    await build_zalo_webhook_consumer(FakeQueue([]), inbound)()
    assert inbound.handled == []


_DEPLOYED: dict[str, object] = {
    "profile": "production",
    "database_url": _DB,
    "embedding_provider": "openai_compatible",
    "qdrant_url": "http://qdrant:6333",
    "zalo_bot_token": "tok",
    "zalo_link_secret": "sec",
    "zalo_updates_mode": "webhook",
}


def test_a_deployed_drain_lane_refuses_the_fixture_model() -> None:
    """The drain reads chat messages with the model exactly as the poll lane does."""
    with pytest.raises(RuntimeError, match="mock model provider"):
        bare_settings(**_DEPLOYED, model_provider="mock").validate_for_profile()
    bare_settings(**_DEPLOYED, model_provider="openai_compatible").validate_for_profile()
