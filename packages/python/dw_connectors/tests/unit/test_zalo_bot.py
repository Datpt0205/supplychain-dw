"""ZaloBotClient: dialect quirks (single-object result, 408 idle timeout), the
1900-character split, and the bot token kept out of errors and logs."""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from dw_connectors.adapters.zalo_bot import ZaloBotClient, _split_for_zalo
from dw_connectors.ports import ChatRecipientUnreachableError

pytestmark = pytest.mark.unit

TOKEN = "bot-test-token"


def _patch_client(monkeypatch, handler) -> None:
    real_init = httpx.AsyncClient.__init__

    def fake_init(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", fake_init)


async def test_get_updates_treats_408_timeout_as_empty(monkeypatch) -> None:
    # An idle long-poll ends 200-with-ok:false-408; that is the steady state,
    # not a failure, so the loop must see an empty batch rather than an error.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error_code": 408, "description": "timeout"})

    _patch_client(monkeypatch, handler)
    assert await ZaloBotClient(bot_token=TOKEN).get_updates(offset=0) == []


async def test_get_updates_normalises_single_object_result(monkeypatch) -> None:
    # Zalo returns result as one object, not a Telegram-style array.
    one = {"message": {"chat": {"id": "z1"}, "text": "hi"}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": one})

    _patch_client(monkeypatch, handler)
    assert await ZaloBotClient(bot_token=TOKEN).get_updates(offset=0) == [one]


async def test_get_updates_raises_on_non_408_failure(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"ok": False, "error_code": 401, "description": "bad token"}
        )

    _patch_client(monkeypatch, handler)
    with pytest.raises(RuntimeError):
        await ZaloBotClient(bot_token=TOKEN).get_updates(offset=0)


# ---- message length -------------------------------------------------------------


def test_a_4000_character_message_becomes_three_parts_under_the_cap() -> None:
    lines = [f"Dòng {i:03d}: " + "x" * 30 for i in range(4000 // 40)]
    text = "\n".join(lines)[:4000]
    parts = _split_for_zalo(text)
    assert len(parts) == 3
    assert all(len(part) <= 1900 for part in parts)
    assert "\n".join(parts) == text


def test_a_single_line_longer_than_the_cap_is_cut_by_length() -> None:
    parts = _split_for_zalo("y" * 4000)
    assert [len(p) for p in parts] == [1900, 1900, 200]


async def test_send_message_sends_every_part_and_returns_the_last_id(monkeypatch) -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True, "result": {"message_id": f"m{len(sent)}"}})

    _patch_client(monkeypatch, handler)
    message_id = await ZaloBotClient(bot_token=TOKEN).send_message("chat-1", "z" * 4000)
    assert len(sent) == 3
    assert message_id == "m3"


# ---- a chat that will never be reached, told apart from a passing failure ---------


@pytest.mark.parametrize("code", [400, 403, 404])
async def test_a_refused_chat_is_unreachable_in_the_body_or_the_status(monkeypatch, code) -> None:
    """The outbox stops retrying on this (channels Z2): the chat does
    not exist or blocked the bot. Both shapes, the dialect's ``ok: false`` body
    and a bare HTTP status, and the token is scrubbed from the description."""

    def in_body(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"ok": False, "error_code": code, "description": f"no chat {TOKEN}"}
        )

    def in_status(request: httpx.Request) -> httpx.Response:
        return httpx.Response(code, json={"ok": False})

    for handler in (in_body, in_status):
        _patch_client(monkeypatch, handler)
        with pytest.raises(ChatRecipientUnreachableError) as caught:
            await ZaloBotClient(bot_token=TOKEN).send_message("chat-1", "hi")
        assert TOKEN not in str(caught.value)


@pytest.mark.parametrize("code", [401, 429, 500, 502])
async def test_any_other_failure_stays_retryable(monkeypatch, code) -> None:
    """401 is this deployment's token, not the person's chat; 429 and 5xx pass."""

    def in_body(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error_code": code, "description": "x"})

    def in_status(request: httpx.Request) -> httpx.Response:
        return httpx.Response(code, text="nope")

    for handler in (in_body, in_status):
        _patch_client(monkeypatch, handler)
        with pytest.raises(RuntimeError):
            await ZaloBotClient(bot_token=TOKEN).send_message("chat-1", "hi")


# ---- the token never leaves in an error or a log line (SEC-20) -------------------


async def test_a_transport_error_does_not_carry_the_token(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    _patch_client(monkeypatch, handler)
    with pytest.raises(RuntimeError) as caught:
        await ZaloBotClient(bot_token=TOKEN).send_message("chat-1", "hi")
    assert TOKEN not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


async def test_an_http_status_error_does_not_carry_the_token(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    _patch_client(monkeypatch, handler)
    with pytest.raises(RuntimeError) as caught:
        await ZaloBotClient(bot_token=TOKEN).get_updates(offset=0)
    assert TOKEN not in str(caught.value)
    assert "bot***" in str(caught.value)


async def test_a_failure_description_echoing_the_token_is_scrubbed(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"ok": False, "error_code": 401, "description": f"bad token {TOKEN}"}
        )

    _patch_client(monkeypatch, handler)
    with pytest.raises(RuntimeError) as caught:
        await ZaloBotClient(bot_token=TOKEN).send_message("chat-1", "hi")
    assert TOKEN not in str(caught.value)


async def test_the_httpx_request_log_line_does_not_carry_the_token(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {"message_id": "m1"}})

    _patch_client(monkeypatch, handler)
    with caplog.at_level(logging.INFO, logger="httpx"):
        await ZaloBotClient(bot_token=TOKEN).send_message("chat-1", "hi")
    assert caplog.records, "httpx logs each request at INFO; the filter must see it"
    assert all(TOKEN not in record.getMessage() for record in caplog.records)


def test_repr_does_not_print_the_token() -> None:
    assert TOKEN not in repr(ZaloBotClient(bot_token=TOKEN))


async def test_set_webhook_registers_the_url_with_the_secret_token(monkeypatch) -> None:
    """Zalo sends ``secret_token`` back in ``X-Bot-Api-Secret-Token`` on every
    webhook call; the URL itself carries no secret (channels Z3)."""
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/setWebhook")
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": True})

    _patch_client(monkeypatch, handler)
    await ZaloBotClient(bot_token=TOKEN).set_webhook(
        "https://api.example.com/api/v1/zalo/webhook", secret_token="s" * 32
    )
    assert seen == [
        {"url": "https://api.example.com/api/v1/zalo/webhook", "secret_token": "s" * 32}
    ]


async def test_set_webhook_failure_names_neither_the_token_nor_the_secret(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "description": f"bad {TOKEN} {'s' * 32}"})

    _patch_client(monkeypatch, handler)
    with pytest.raises(RuntimeError) as raised:
        await ZaloBotClient(bot_token=TOKEN).set_webhook(
            "https://api.example.com/api/v1/zalo/webhook", secret_token="s" * 32
        )
    assert TOKEN not in str(raised.value)
    assert "s" * 32 not in str(raised.value)
