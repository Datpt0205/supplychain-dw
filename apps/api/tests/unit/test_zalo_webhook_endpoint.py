"""``POST /api/v1/zalo/webhook``: the hosted way a Zalo update reaches the platform.

ADR 0008: the route checks the ``X-Bot-Api-Secret-Token`` header
in constant time, caps the body at 64 KB, validates the envelope, hands the
update to the inbox the worker drains through the same ``ZaloInbound.handle``
the poll lane calls, and answers 200 at once. What is pinned here:

* off unless ``ZALO_UPDATES_MODE=webhook`` AND a secret is set: 404, even with
  the right secret — the setting guard that keeps poll and webhook from both
  reading one bot;
* a missing or wrong secret is refused (403) before the body is read, and
  nothing is queued;
* a body over 64 KB is 413, a body that is not a bot update is 422, and the
  error never echoes what was sent;
* an accepted update is queued unchanged.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager

from dw_api.bootstrap import ApiContainer
from dw_api.health import CheckState, HealthService
from dw_api.main import create_app
from dw_api.routes.v1.zalo import MAX_WEBHOOK_BYTES, WEBHOOK_SECRET_HEADER
from dw_api.settings import ApiSettings
from dw_platform.adapters.identity.dev_token import DevTokenVerifier
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.entitlement import DEFAULT_PLANS, PlanEntitlementService
from dw_platform.application.identity import DbAccessContextFactory

pytestmark = pytest.mark.unit

DEV_SECRET = "unit-test-secret-0123456789abcdef"
HOOK_SECRET = "webhook-secret-0123456789abcdef-0123456789"
PATH = "/api/v1/zalo/webhook"
CANARY = "canary-<script>-7f3a"


class FakeInbox:
    def __init__(self) -> None:
        self.queued: list[tuple[str, dict[str, Any]]] = []

    async def enqueue(self, channel: str, update: dict[str, Any]) -> None:
        self.queued.append((channel, update))


class NoMemberships:
    async def find_access(self, *_: object) -> None:
        return None


def make_container(
    inbox: FakeInbox, *, mode: str = "webhook", secret: str = HOOK_SECRET
) -> ApiContainer:
    async def ok_probe() -> CheckState:
        return "ok"

    return ApiContainer(
        settings=ApiSettings(
            profile="test",
            dev_secret=DEV_SECRET,
            zalo_updates_mode=mode,  # type: ignore[arg-type]
            zalo_webhook_secret=secret,  # type: ignore[arg-type]
        ),
        engine=None,
        health_service=HealthService(probes={"database": ok_probe}),
        token_verifier=DevTokenVerifier(DEV_SECRET),
        access_context_factory=DbAccessContextFactory(NoMemberships()),  # type: ignore[arg-type]
        identity_bootstrap=None,
        uow_factory=None,
        authorization=ScopeAuthorizationService(),
        entitlement=PlanEntitlementService(DEFAULT_PLANS),
        zalo_webhook_inbox=inbox,
    )


def update(text: str = "/start abc", message_id: str = "m-1") -> dict[str, Any]:
    # The webhook path delivers the message at the top level (survey §4).
    return {
        "event_name": "message.text.received",
        "message": {"chat": {"id": "chat-1"}, "text": text, "message_id": message_id},
    }


async def post(
    container: ApiContainer,
    *,
    secret: str | None = HOOK_SECRET,
    content: bytes | None = None,
    body: object | None = None,
) -> httpx.Response:
    headers = {"Content-Type": "application/json"}
    if secret is not None:
        headers[WEBHOOK_SECRET_HEADER] = secret
    if content is None:
        content = json.dumps(body if body is not None else update()).encode()
    app = create_app(container)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(PATH, headers=headers, content=content)


async def test_an_update_with_the_right_secret_is_queued_unchanged_and_answered_200() -> None:
    inbox = FakeInbox()
    sent = update("/start tok-123", "m-42")

    response = await post(make_container(inbox), body=sent)

    assert response.status_code == 200
    assert inbox.queued == [("zalo", sent)]


async def test_the_poll_shape_is_accepted_too() -> None:
    """``parse_update`` reads both envelopes; the route must not refuse the one
    it has not seen yet (the webhook's shape is measured in a live run)."""
    inbox = FakeInbox()
    sent = {"ok": True, "result": update()}

    response = await post(make_container(inbox), body=sent)

    assert response.status_code == 200
    assert inbox.queued == [("zalo", sent)]


@pytest.mark.parametrize(
    "presented",
    [None, "", "wrong", HOOK_SECRET[:-1], HOOK_SECRET + "x", HOOK_SECRET.upper()],
    ids=["missing", "empty", "wrong", "prefix", "longer", "case"],
)
async def test_a_missing_or_wrong_secret_is_refused_and_nothing_is_queued(
    presented: str | None,
) -> None:
    inbox = FakeInbox()

    response = await post(make_container(inbox), secret=presented, body=update(CANARY))

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"
    assert CANARY not in response.text
    assert inbox.queued == []


async def test_a_wrong_secret_is_refused_before_the_body_is_read() -> None:
    """An oversized body with a wrong secret is 403, not 413: who may post is
    decided before anything about what they posted."""
    inbox = FakeInbox()

    response = await post(make_container(inbox), secret="wrong", content=b"x" * (70 * 1024))

    assert response.status_code == 403
    assert inbox.queued == []


@pytest.mark.parametrize("secret", ["", HOOK_SECRET])
async def test_poll_mode_has_no_webhook_even_with_the_right_secret(secret: str) -> None:
    """The setting guard: one bot is read one way. In poll mode the worker polls,
    and a webhook accepting updates as well would split them between two readers."""
    inbox = FakeInbox()

    response = await post(make_container(inbox, mode="poll", secret=secret), secret=secret)

    assert response.status_code == 404
    assert inbox.queued == []


async def test_webhook_mode_without_a_secret_has_no_webhook() -> None:
    """Fail closed: an unset secret never means "anyone may post"."""
    inbox = FakeInbox()

    response = await post(make_container(inbox, secret=""), secret="")

    assert response.status_code == 404
    assert inbox.queued == []


async def test_a_body_over_64_kb_is_413_and_nothing_is_queued() -> None:
    inbox = FakeInbox()
    filler = "x" * (65 * 1024)
    body = json.dumps(update(filler)).encode()
    assert len(body) > MAX_WEBHOOK_BYTES

    response = await post(make_container(inbox), content=body)

    assert response.status_code == 413
    assert inbox.queued == []


async def test_a_body_of_exactly_64_kb_is_read() -> None:
    inbox = FakeInbox()
    shell = json.dumps(update("")).encode()
    text = "x" * (MAX_WEBHOOK_BYTES - len(shell))
    body = json.dumps(update(text)).encode()
    assert len(body) == MAX_WEBHOOK_BYTES

    response = await post(make_container(inbox), content=body)

    assert response.status_code == 200
    assert len(inbox.queued) == 1


@pytest.mark.parametrize(
    "content",
    [
        b"not json " + CANARY.encode(),
        json.dumps([CANARY]).encode(),
        json.dumps({"message": CANARY}).encode(),
        json.dumps({"result": CANARY}).encode(),
        json.dumps({"message": {"text": CANARY}, "result": {"message": [CANARY]}}).encode(),
        json.dumps({"event_name": CANARY}).encode(),
    ],
    ids=["not-json", "array", "message-not-object", "result-not-object", "result-message", "none"],
)
async def test_a_body_that_is_not_a_bot_update_is_422_without_echoing_it(content: bytes) -> None:
    inbox = FakeInbox()

    response = await post(make_container(inbox), content=content)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"
    assert CANARY not in response.text
    assert inbox.queued == []


async def test_the_same_update_twice_is_queued_twice_and_deduplicated_downstream() -> None:
    """The route does not dedupe: the inbound router claims the message id in
    ``channel_inbound_messages`` when the worker drains it, so a replay is acted
    on once (``apps/worker`` ``test_zalo_webhook_db.py``)."""
    inbox = FakeInbox()
    container = make_container(inbox)

    first = await post(container)
    second = await post(container)

    assert (first.status_code, second.status_code) == (200, 200)
    assert len(inbox.queued) == 2


# ---- settings: what a deployed webhook needs before the API starts -----------


def deployed(**overrides: object) -> ApiSettings:
    """Settings that satisfy every other deployed guard, so one is tested at a time."""
    values: dict[str, object] = {
        "profile": "production",
        "auth_mode": "oidc",
        "oidc_issuer_url": "https://idp.example/realms/dw",
        "model_provider": "openai_compatible",
        "openai_api_key": "unit-test-key",
        "openai_base_url": "https://api.example.com/v1",
        "embedding_provider": "openai_compatible",
        "qdrant_url": "https://qdrant.example:6333",
        "cors_origins": ["https://app.example.com"],
        "task_connector": "none",
        "database_url": "postgresql+asyncpg://u:p@db/dw",
        "zalo_updates_mode": "webhook",
        "zalo_webhook_secret": "s" * 32,
        "public_base_url": "https://api.example.com",
    }
    values.update(overrides)
    return ApiSettings(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("profile", ["uat", "production"])
def test_a_deployed_webhook_with_a_long_secret_and_https_starts(profile: str) -> None:
    deployed(profile=profile).validate_for_profile()


@pytest.mark.parametrize("profile", ["uat", "production"])
def test_a_deployed_webhook_refuses_a_secret_shorter_than_32(profile: str) -> None:
    with pytest.raises(RuntimeError, match="ZALO_WEBHOOK_SECRET"):
        deployed(profile=profile, zalo_webhook_secret="s" * 16).validate_for_profile()
    with pytest.raises(RuntimeError, match="ZALO_WEBHOOK_SECRET"):
        deployed(profile=profile, zalo_webhook_secret="s" * 31).validate_for_profile()


@pytest.mark.parametrize("base", ["http://api.example.com", "", "api.example.com"])
def test_a_deployed_webhook_refuses_a_public_base_url_that_is_not_https(base: str) -> None:
    with pytest.raises(RuntimeError, match="DW_API_PUBLIC_BASE_URL"):
        deployed(public_base_url=base).validate_for_profile()


def test_a_deployed_poll_needs_no_webhook_secret() -> None:
    deployed(zalo_updates_mode="poll", zalo_webhook_secret="").validate_for_profile()


def test_local_webhook_needs_neither() -> None:
    ApiSettings(
        profile="local",
        zalo_updates_mode="webhook",  # type: ignore[arg-type]
        zalo_webhook_secret="short",  # type: ignore[arg-type]
        public_base_url="http://localhost:8200",
    ).validate_for_profile()


def test_the_secret_never_prints() -> None:
    settings = deployed()
    assert "s" * 32 not in repr(settings)
    assert "s" * 32 not in str(settings.model_dump())


async def test_a_chunked_body_over_64_kb_without_a_length_is_413() -> None:
    """The declared length is a shortcut, not the guard: a sender that declares
    none is cut off by what it actually sends."""
    inbox = FakeInbox()

    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(80):
            yield b"x" * 1024

    app = create_app(make_container(inbox))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                PATH, headers={WEBHOOK_SECRET_HEADER: HOOK_SECRET}, content=chunks()
            )

    assert response.status_code == 413
    assert inbox.queued == []


async def test_a_browser_cannot_send_the_secret_header_cross_origin() -> None:
    """Zalo calls server to server; no web origin is ever allowed to send the
    secret header, so a page cannot drive the webhook from a person's browser."""
    app = create_app(make_container(FakeInbox()))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.options(
                PATH,
                headers={
                    "Origin": "http://localhost:3000",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": WEBHOOK_SECRET_HEADER.lower(),
                },
            )

    allowed = response.headers.get("access-control-allow-headers", "").lower()
    assert WEBHOOK_SECRET_HEADER.lower() not in allowed
    assert response.status_code == 400
