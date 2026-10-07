"""The shared Zalo self-link flow: one-time token codec, the connect offer, and
update handling on the bot's side.

The single-use half of the token (the nonce consumed by one conditional UPDATE)
is the store's job and is tested against real Postgres in
``dw_platform/tests/integration/test_zalo_link_repo.py``. The fake store here
honours the same contract — a nonce redeems once, for its own user, before it
expires — so a handler that skipped ``redeem`` would go red here too.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from dw_connectors.adapters.zalo_link import (
    TOKEN_TTL,
    ConnectToken,
    ZaloLinking,
    handle_update,
    make_connect_token,
    parse_update,
    verify_connect_token,
)

pytestmark = pytest.mark.unit

_SECRET = "s3cr3t-link-signing-key"
_USER = uuid.UUID("450fbfd4-307f-447b-ae14-389aeb2f84ab")
_OTHER = uuid.UUID("0c6f3a52-8f0e-4d55-9d4f-6f0a5d1b2c3e")
_ZALO_ID = "733245f6b0b959e700a8"
_NOW = datetime(2026, 10, 5, 8, 0, tzinfo=UTC)
_PRODUCT = "Cổng thử"


class _Clock:
    def __init__(self, now: datetime = _NOW) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current


class _FakeStore:
    """Honours ``ZaloLinkStore`` and ``ZaloAccountStore`` the way ``SqlZaloLink`` does.

    A nonce must have been issued, belong to the token's user, be unused and
    unexpired to redeem; redeeming marks it used. Linking clears both sides
    first: one Zalo per user, one user per Zalo.
    """

    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.nonces: dict[str, tuple[uuid.UUID, datetime, bool]] = {}
        self.links: dict[uuid.UUID, str] = {}

    async def issue_nonce(self, token: ConnectToken) -> None:
        self.nonces[token.jti] = (token.user_id, token.expires_at, False)

    async def redeem(self, token: ConnectToken, zalo_id: str) -> bool:
        nonce = self.nonces.get(token.jti)
        if nonce is None:
            return False
        user_id, expires_at, used = nonce
        if used or user_id != token.user_id or expires_at <= self.clock.now():
            return False
        self.nonces[token.jti] = (user_id, expires_at, True)
        self.links = {u: z for u, z in self.links.items() if u != user_id and z != zalo_id}
        self.links[user_id] = zalo_id
        return True

    async def unlink_by_zalo(self, zalo_id: str) -> bool:
        before = len(self.links)
        self.links = {u: z for u, z in self.links.items() if z != zalo_id}
        return len(self.links) < before

    async def zalo_id_for(self, user_id: uuid.UUID) -> str | None:
        return self.links.get(user_id)

    async def user_id_for(self, zalo_id: str) -> uuid.UUID | None:
        return next((u for u, z in self.links.items() if z == zalo_id), None)

    async def unlink_by_user(self, user_id: uuid.UUID) -> None:
        self.links.pop(user_id, None)


class _FakeSender:
    """Satisfies ``ChatSenderPort`` structurally — no base class, no network."""

    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail = fail

    async def send_message(self, conversation_id: str, text: str) -> str:
        if self.fail:
            raise RuntimeError("zalo sendMessage failed")
        self.sent.append((conversation_id, text))
        return "msg-1"


def _poll_update(text: str, zalo_id: str = _ZALO_ID) -> dict[str, Any]:
    # The exact poll-path shape observed from getUpdates (result-wrapped).
    return {
        "result": {
            "message": {
                "chat": {"id": zalo_id, "chat_type": "PRIVATE"},
                "text": text,
                "from": {"id": zalo_id, "display_name": "Đạt"},
            },
            "event_name": "message.text.received",
        }
    }


def _webhook_update(text: str, zalo_id: str = _ZALO_ID) -> dict[str, Any]:
    # The webhook path delivers the message at the top level.
    return {"message": {"chat": {"id": zalo_id}, "text": text}}


async def _handle(
    update: dict[str, Any],
    store: _FakeStore,
    sender: _FakeSender | None,
    clock: _Clock,
) -> None:
    await handle_update(
        update,
        link_secret=_SECRET,
        store=store,
        sender=sender,
        clock=clock,
        product_name=_PRODUCT,
    )


async def _issued(store: _FakeStore, user: uuid.UUID = _USER) -> str:
    offer = await ZaloLinking(store=store, link_secret=_SECRET, clock=store.clock).connect(user)
    return offer.code


# ---- token codec ---------------------------------------------------------------


def test_token_round_trips_with_its_claims() -> None:
    text, claims = make_connect_token(_USER, _SECRET, now=_NOW)
    verified = verify_connect_token(text, _SECRET, now=_NOW)
    assert verified == claims
    assert verified.user_id == _USER
    assert verified.expires_at == _NOW + TOKEN_TTL


def test_two_tokens_for_one_user_have_different_jtis() -> None:
    first = make_connect_token(_USER, _SECRET, now=_NOW)[1]
    second = make_connect_token(_USER, _SECRET, now=_NOW)[1]
    assert first.jti != second.jti


def test_token_rejects_wrong_secret() -> None:
    text, _ = make_connect_token(_USER, _SECRET, now=_NOW)
    assert verify_connect_token(text, "different-secret", now=_NOW) is None


def test_token_rejects_tampering_with_the_user() -> None:
    text, _ = make_connect_token(_USER, _SECRET, now=_NOW)
    raw = bytearray(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)))
    raw[0] ^= 0x01  # flip a bit of the user id; the tag no longer matches
    forged = base64.urlsafe_b64encode(bytes(raw)).decode().rstrip("=")
    assert verify_connect_token(forged, _SECRET, now=_NOW) is None


def test_token_rejects_a_forged_tag_over_another_users_body() -> None:
    text, _ = make_connect_token(_USER, _SECRET, now=_NOW)
    raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    forged_body = _OTHER.bytes + raw[16:]
    forged = base64.urlsafe_b64encode(forged_body).decode().rstrip("=")
    assert verify_connect_token(forged, _SECRET, now=_NOW) is None


def test_token_expires_after_fifteen_minutes() -> None:
    text, _ = make_connect_token(_USER, _SECRET, now=_NOW)
    just_before = _NOW + TOKEN_TTL - timedelta(seconds=1)
    assert verify_connect_token(text, _SECRET, now=just_before) is not None
    assert verify_connect_token(text, _SECRET, now=_NOW + TOKEN_TTL) is None


@pytest.mark.parametrize("garbage", ["", "not-a-real-token", "%%%", "QUJD"])
def test_token_rejects_garbage(garbage: str) -> None:
    assert verify_connect_token(garbage, _SECRET, now=_NOW) is None


# ---- the connect offer (settings page) -----------------------------------------


async def test_connect_issues_a_nonce_and_returns_the_code_and_expiry() -> None:
    clock = _Clock()
    store = _FakeStore(clock)
    offer = await ZaloLinking(store=store, link_secret=_SECRET, clock=clock).connect(_USER)

    claims = verify_connect_token(offer.code, _SECRET, now=_NOW)
    assert claims is not None and claims.user_id == _USER
    assert store.nonces[claims.jti] == (_USER, _NOW + TOKEN_TTL, False)
    assert offer.expires_at == _NOW + TOKEN_TTL
    assert offer.deep_link is None


@pytest.mark.parametrize(
    ("bot_link", "joiner"),
    [("https://zalo.me/s/bot123", "?"), ("https://zalo.me/s/bot123?src=app", "&")],
)
async def test_connect_builds_the_deep_link_when_configured(bot_link: str, joiner: str) -> None:
    clock = _Clock()
    linking = ZaloLinking(
        store=_FakeStore(clock), link_secret=_SECRET, clock=clock, bot_link=bot_link
    )
    offer = await linking.connect(_USER)
    assert offer.deep_link == f"{bot_link}{joiner}start={offer.code}"


async def test_status_and_disconnect_are_per_user() -> None:
    clock = _Clock()
    store = _FakeStore(clock)
    store.links = {_USER: _ZALO_ID, _OTHER: "other-chat"}
    linking = ZaloLinking(store=store, link_secret=_SECRET, clock=clock)

    await linking.disconnect(_USER)

    assert await linking.is_linked(_USER) is False
    assert await linking.is_linked(_OTHER) is True


# ---- parse (both delivery shapes) ----------------------------------------------


def test_parse_poll_shape_with_result_wrapper() -> None:
    assert parse_update(_poll_update("hi")) == (_ZALO_ID, "hi")


def test_parse_webhook_shape_without_result_wrapper() -> None:
    assert parse_update(_webhook_update("hi")) == (_ZALO_ID, "hi")


def test_parse_falls_back_to_from_when_no_chat() -> None:
    update = {"result": {"message": {"from": {"id": _ZALO_ID}, "text": "hi"}}}
    assert parse_update(update) == (_ZALO_ID, "hi")


# ---- handle_update -------------------------------------------------------------


@pytest.mark.parametrize("shape", [_poll_update, _webhook_update])
async def test_start_with_a_valid_token_links_and_confirms(shape: Any) -> None:
    clock = _Clock()
    store, sender = _FakeStore(clock), _FakeSender()
    code = await _issued(store)

    await _handle(shape(f"/start {code}"), store, sender, clock)

    assert store.links == {_USER: _ZALO_ID}
    assert len(sender.sent) == 1
    assert sender.sent[0][0] == _ZALO_ID
    assert "Đã kết nối Zalo" in sender.sent[0][1] and _PRODUCT in sender.sent[0][1]


async def test_the_same_token_sent_twice_links_once() -> None:
    clock = _Clock()
    store, sender = _FakeStore(clock), _FakeSender()
    code = await _issued(store)
    await _handle(_poll_update(f"/start {code}"), store, sender, clock)

    # Someone who saw the code over a shoulder tries it from their own Zalo.
    await _handle(_poll_update(f"/start {code}", zalo_id="intruder-chat"), store, sender, clock)

    assert store.links == {_USER: _ZALO_ID}
    assert "đã được dùng" in sender.sent[-1][1]


async def test_a_signed_token_that_was_never_issued_does_not_link() -> None:
    clock = _Clock()
    store, sender = _FakeStore(clock), _FakeSender()
    text, _ = make_connect_token(_USER, _SECRET, now=_NOW)  # no nonce written

    await _handle(_poll_update(f"/start {text}"), store, sender, clock)

    assert store.links == {}
    assert "không hợp lệ" in sender.sent[0][1]


async def test_an_expired_token_does_not_link() -> None:
    clock = _Clock()
    store, sender = _FakeStore(clock), _FakeSender()
    code = await _issued(store)
    clock.current = _NOW + TOKEN_TTL + timedelta(seconds=1)

    await _handle(_poll_update(f"/start {code}"), store, sender, clock)

    assert store.links == {}
    assert "hết hạn" in sender.sent[0][1]


async def test_a_forged_token_does_not_link() -> None:
    clock = _Clock()
    store, sender = _FakeStore(clock), _FakeSender()
    await _handle(_poll_update("/start bogus"), store, sender, clock)
    assert store.links == {}
    assert "không hợp lệ" in sender.sent[0][1]


async def test_stop_unlinks_and_says_so() -> None:
    clock = _Clock()
    store, sender = _FakeStore(clock), _FakeSender()
    store.links = {_USER: _ZALO_ID}

    await _handle(_poll_update("/stop"), store, sender, clock)

    assert store.links == {}
    assert "Đã ngắt kết nối Zalo" in sender.sent[0][1]


async def test_stop_from_an_unlinked_chat_says_it_was_not_linked() -> None:
    clock = _Clock()
    store, sender = _FakeStore(clock), _FakeSender()
    store.links = {_USER: "someone-else"}

    await _handle(_poll_update("/stop"), store, sender, clock)

    assert store.links == {_USER: "someone-else"}
    assert "chưa kết nối" in sender.sent[0][1]


@pytest.mark.parametrize(
    "text",
    [
        "hủy",
        "huỷ",
        "stop",
        "/huy",
        "/hủy",
        "STOP",
        "/stop now",
        "duyệt",
        "đồng ý",
        "ok",
        "approve 7f3a",
        "/start",
        "xin chào",
    ],
)
async def test_free_text_changes_nothing_and_gets_one_sentence_of_help(text: str) -> None:
    clock = _Clock()
    store, sender = _FakeStore(clock), _FakeSender()
    store.links = {_USER: _ZALO_ID}
    code = await _issued(store)
    nonces_before = dict(store.nonces)

    await _handle(_poll_update(text), store, sender, clock)

    assert store.links == {_USER: _ZALO_ID}
    assert store.nonces == nonces_before
    assert len(sender.sent) == 1
    assert "/start <mã>" in sender.sent[0][1] and "/stop" in sender.sent[0][1]
    assert code not in sender.sent[0][1]


async def test_empty_message_is_noop() -> None:
    clock = _Clock()
    store, sender = _FakeStore(clock), _FakeSender()
    await _handle({"result": {"message": {}}}, store, sender, clock)
    assert store.links == {} and sender.sent == []


async def test_link_works_without_a_sender() -> None:
    clock = _Clock()
    store = _FakeStore(clock)
    code = await _issued(store)
    await _handle(_poll_update(f"/start {code}"), store, None, clock)
    assert store.links == {_USER: _ZALO_ID}


async def test_a_reply_that_fails_to_send_keeps_the_link() -> None:
    clock = _Clock()
    store = _FakeStore(clock)
    code = await _issued(store)
    await _handle(_poll_update(f"/start {code}"), store, _FakeSender(fail=True), clock)
    assert store.links == {_USER: _ZALO_ID}


def test_handle_update_cannot_reach_an_approval_decision() -> None:
    """No free text decides anything (ADR 0007): the bot module imports nothing
    that could. Z5 adds a separate, code-gated path and keeps this test."""
    from pathlib import Path

    import dw_connectors.adapters.zalo_link as module

    source = Path(module.__file__ or "").read_text(encoding="utf-8")
    for forbidden in ("approval_flow", "ApproveAndResumeService", "access_context", "decide("):
        assert forbidden not in source
