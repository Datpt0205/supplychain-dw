"""The /zalo routes over the real ``ZaloLinking`` with an in-memory store.

What these pin down: every route acts on the verified principal and nothing the
request carries; a request without a bearer token is refused before anything
runs; status names no chat id; and a deployment missing the bot token or the
link secret has no route at all. The single-use token itself is the store's
job, tested against Postgres in ``dw_platform``'s ``test_zalo_link_repo.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from asgi_lifespan import LifespanManager

from dw_api.bootstrap import ApiContainer, build_container
from dw_api.health import CheckState, HealthService
from dw_api.main import create_app
from dw_api.settings import ApiSettings
from dw_connectors.adapters.zalo_link import (
    TOKEN_TTL,
    ConnectToken,
    ZaloLinking,
    verify_connect_token,
)
from dw_platform.adapters.identity.dev_token import DevTokenVerifier
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.entitlement import DEFAULT_PLANS, PlanEntitlementService
from dw_platform.application.identity import DbAccessContextFactory, MembershipAccess

pytestmark = pytest.mark.unit

SECRET = "unit-test-secret-0123456789abcdef"
LINK_SECRET = "unit-test-link-secret"
NOW = datetime(2026, 10, 5, 8, 0, tzinfo=UTC)
TENANT = uuid.uuid4()
WORKSPACE = uuid.uuid4()
SECOND_TENANT = uuid.uuid4()
SECOND_WORKSPACE = uuid.uuid4()
ALICE = uuid.uuid4()
BOB = uuid.uuid4()
SUBJECTS = {"dev|alice": ALICE, "dev|bob": BOB}


class FixedClock:
    def now(self) -> datetime:
        return NOW


class FakeMembershipLookup:
    """Alice is a member of two tenants; Bob of the first only."""

    async def find_access(
        self, subject: str, issuer: str, tenant_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> MembershipAccess | None:
        principal = SUBJECTS.get(subject)
        allowed = {(TENANT, WORKSPACE)}
        if principal == ALICE:
            allowed.add((SECOND_TENANT, SECOND_WORKSPACE))
        if principal is None or (tenant_id, workspace_id) not in allowed:
            return None
        return MembershipAccess(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            principal_id=principal,
            roles=frozenset({"member"}),
            scopes=frozenset(),
            groups=frozenset(),
            clearance="internal",
            plan_id="professional",
            feature_flags=frozenset(),
        )


class FakeAccountStore:
    """``ZaloAccountStore`` in memory, keyed by user like ``SqlZaloLink``."""

    def __init__(self) -> None:
        self.links: dict[uuid.UUID, str] = {}
        self.nonces: list[ConnectToken] = []

    async def zalo_id_for(self, user_id: uuid.UUID) -> str | None:
        return self.links.get(user_id)

    async def issue_nonce(self, token: ConnectToken) -> None:
        self.nonces.append(token)

    async def unlink_by_user(self, user_id: uuid.UUID) -> None:
        self.links.pop(user_id, None)


class FakePreferences:
    """``ChannelPreferencesPort`` in memory: memberships as ``FakeMembershipLookup`` has them."""

    def __init__(self) -> None:
        self.held = {
            ALICE: [(TENANT, WORKSPACE), (SECOND_TENANT, SECOND_WORKSPACE)],
            BOB: [(TENANT, WORKSPACE)],
        }
        self.choices: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID]] = {}

    async def chosen(self, user_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID] | None:
        return self.choices.get(user_id)

    async def memberships(self, user_id: uuid.UUID) -> list[tuple[uuid.UUID, uuid.UUID]]:
        return list(self.held.get(user_id, []))

    async def choose(
        self, user_id: uuid.UUID, tenant_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> bool:
        # The SQL store confirms the membership under the caller's principal; so must this.
        if (tenant_id, workspace_id) not in self.held.get(user_id, []):
            return False
        self.choices[user_id] = (tenant_id, workspace_id)
        return True


def make_container(
    store: FakeAccountStore | None, preferences: FakePreferences | None = None
) -> ApiContainer:
    async def ok_probe() -> CheckState:
        return "ok"

    return ApiContainer(
        settings=ApiSettings(profile="test", dev_secret=SECRET),
        engine=None,
        health_service=HealthService(probes={"database": ok_probe}),
        token_verifier=DevTokenVerifier(SECRET),
        access_context_factory=DbAccessContextFactory(FakeMembershipLookup()),
        identity_bootstrap=None,
        uow_factory=None,
        authorization=ScopeAuthorizationService(),
        entitlement=PlanEntitlementService(DEFAULT_PLANS),
        zalo_linking=(
            ZaloLinking(
                store=store,
                link_secret=LINK_SECRET,
                clock=FixedClock(),
                bot_link="https://zalo.me/s/bot-test",
            )
            if store is not None
            else None
        ),
        channel_preferences=(preferences or FakePreferences()) if store is not None else None,
    )


async def _call(
    container: ApiContainer,
    method: str,
    path: str,
    *,
    subject: str | None = "dev|alice",
    tenant: uuid.UUID = TENANT,
    workspace: uuid.UUID = WORKSPACE,
    json: object | None = None,
) -> httpx.Response:
    headers = {"X-Tenant-Id": str(tenant), "X-Workspace-Id": str(workspace)}
    if subject is not None:
        token = DevTokenVerifier(SECRET).issue(subject, email=f"{subject[4:]}@example.com")
        headers["Authorization"] = f"Bearer {token}"
    app = create_app(container)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, f"/api/v1/zalo{path}", headers=headers, json=json)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/status"),
        ("POST", "/connect"),
        ("POST", "/disconnect"),
        ("GET", "/workspace"),
        ("PUT", "/workspace"),
    ],
)
async def test_without_a_bearer_token_every_route_is_refused(method: str, path: str) -> None:
    """The platform answers a missing bearer token with 403 ``permission_denied``
    (``dw_kernel.http_auth.bearer_token``), on these routes as on every other."""
    store = FakeAccountStore()
    store.links[ALICE] = "alice-chat"

    response = await _call(make_container(store), method, path, subject=None)

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"
    assert store.links == {ALICE: "alice-chat"} and store.nonces == []


async def test_status_says_linked_and_names_no_chat_id() -> None:
    store = FakeAccountStore()
    store.links = {ALICE: "alice-chat", BOB: "bob-chat"}

    response = await _call(make_container(store), "GET", "/status")

    assert response.status_code == 200
    assert response.json() == {"linked": True}
    assert "chat" not in response.text


async def test_status_is_the_callers_own() -> None:
    store = FakeAccountStore()
    store.links = {ALICE: "alice-chat"}

    response = await _call(make_container(store), "GET", "/status", subject="dev|bob")

    assert response.json() == {"linked": False}


async def test_a_link_belongs_to_the_person_so_a_second_tenant_reads_linked_too() -> None:
    """By design (ADR 0005): the link is keyed by user, not by tenant."""
    store = FakeAccountStore()
    store.links = {ALICE: "alice-chat"}

    response = await _call(
        make_container(store),
        "GET",
        "/status",
        tenant=SECOND_TENANT,
        workspace=SECOND_WORKSPACE,
    )

    assert response.json() == {"linked": True}


async def test_connect_mints_a_code_for_the_caller_and_ignores_a_user_in_the_body() -> None:
    store = FakeAccountStore()

    response = await _call(
        make_container(store),
        "POST",
        "/connect",
        json={"user_id": str(BOB), "principal_id": str(BOB)},
    )

    assert response.status_code == 200
    body = response.json()
    claims = verify_connect_token(body["code"], LINK_SECRET, now=NOW)
    assert claims is not None and claims.user_id == ALICE
    assert [n.user_id for n in store.nonces] == [ALICE]
    assert body["deep_link"] == f"https://zalo.me/s/bot-test?start={body['code']}"
    assert datetime.fromisoformat(body["expires_at"]) == NOW + TOKEN_TTL


async def test_disconnect_removes_only_the_callers_link() -> None:
    store = FakeAccountStore()
    store.links = {ALICE: "alice-chat", BOB: "bob-chat"}

    response = await _call(make_container(store), "POST", "/disconnect", json={"user_id": str(BOB)})

    assert response.status_code == 204
    assert store.links == {BOB: "bob-chat"}


async def test_a_non_member_of_the_workspace_is_refused() -> None:
    store = FakeAccountStore()
    response = await _call(
        make_container(store),
        "POST",
        "/connect",
        subject="dev|bob",
        tenant=SECOND_TENANT,
        workspace=SECOND_WORKSPACE,
    )
    assert response.status_code in (403, 404)
    assert store.nonces == []


# ---- the workspace Zalo commands act in -----------------------------------------


async def test_the_workspace_is_unset_until_chosen() -> None:
    response = await _call(make_container(FakeAccountStore()), "GET", "/workspace")
    assert response.status_code == 200
    assert response.json() == {"tenant_id": None, "workspace_id": None}


async def test_choosing_one_of_my_workspaces_is_kept_for_me_only() -> None:
    preferences = FakePreferences()
    container = make_container(FakeAccountStore(), preferences)
    choice = {"tenant_id": str(SECOND_TENANT), "workspace_id": str(SECOND_WORKSPACE)}

    put = await _call(container, "PUT", "/workspace", json=choice)
    mine = await _call(container, "GET", "/workspace")
    bobs = await _call(container, "GET", "/workspace", subject="dev|bob")

    assert put.status_code == 200 and put.json() == choice
    assert mine.json() == choice
    assert bobs.json() == {"tenant_id": None, "workspace_id": None}
    assert preferences.choices == {ALICE: (SECOND_TENANT, SECOND_WORKSPACE)}


async def test_a_workspace_i_do_not_belong_to_answers_404_and_changes_nothing() -> None:
    """Bob is no member of the second tenant: the same 404 as for a workspace
    that does not exist, so the answer says nothing about it."""
    preferences = FakePreferences()
    container = make_container(FakeAccountStore(), preferences)

    for choice in (
        {"tenant_id": str(SECOND_TENANT), "workspace_id": str(SECOND_WORKSPACE)},
        {"tenant_id": str(uuid.uuid4()), "workspace_id": str(uuid.uuid4())},
    ):
        response = await _call(container, "PUT", "/workspace", subject="dev|bob", json=choice)
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"
    assert preferences.choices == {}


async def test_the_choice_cannot_name_whose_it_is() -> None:
    preferences = FakePreferences()
    response = await _call(
        make_container(FakeAccountStore(), preferences),
        "PUT",
        "/workspace",
        json={"tenant_id": str(TENANT), "workspace_id": str(WORKSPACE), "user_id": str(BOB)},
    )
    assert response.status_code == 422
    assert preferences.choices == {}


# ---- no configuration, no door -------------------------------------------------

_WIRING = {
    "profile": "test",
    "database_url": "postgresql+asyncpg://wiring:wiring@localhost:5432/wiring",
    "auth_mode": "dev",
    "dev_secret": SECRET,
    "model_provider": "mock",
}


@pytest.mark.parametrize(
    ("token", "secret", "mounted"),
    [("bot-token", "link-secret", True), ("", "link-secret", False), ("bot-token", "", False)],
    ids=["both-set", "no-bot-token", "no-link-secret"],
)
async def test_the_routes_exist_only_with_both_the_bot_token_and_the_link_secret(
    token: str, secret: str, mounted: bool
) -> None:
    settings = ApiSettings(**_WIRING, zalo_bot_token=token, zalo_link_secret=secret)  # type: ignore[arg-type]
    container = build_container(settings)
    assert (container.zalo_linking is not None) is mounted

    for method, path in (("GET", "/status"), ("POST", "/connect"), ("POST", "/disconnect")):
        response = await _call(container, method, path, subject=None)
        # Mounted: refused for want of a token. Not mounted: no such route.
        assert response.status_code == (403 if mounted else 404), (method, path)


async def test_an_unwired_container_answers_404() -> None:
    response = await _call(make_container(None), "GET", "/status")
    assert response.status_code == 404


def test_the_bot_token_and_link_secret_never_print_in_the_settings_repr() -> None:
    settings = ApiSettings(**_WIRING, zalo_bot_token="tok-123456", zalo_link_secret="sec-987654")  # type: ignore[arg-type]
    assert "tok-123456" not in repr(settings) and "sec-987654" not in repr(settings)


def test_the_route_never_builds_an_access_context_from_zalo_data() -> None:
    """ADR 0005 condition 2: the routes resolve the caller from the bearer token
    through the ordinary dependency; nothing Zalo sent reaches the factory.

    The import side is import-linter's ("The Zalo settings routes take neither
    approvals nor the access-context factory"). What an import check cannot see
    is the container the route is handed, so this reads the route's syntax tree:
    the only things it takes from the container are the link service, the
    workspace choice and, for the webhook (channels Z3), the inbox it queues to
    and the settings that hold the webhook's mode and secret."""
    import ast
    from pathlib import Path

    import dw_api.routes.v1.zalo as route

    tree = ast.parse(Path(route.__file__ or "").read_text(encoding="utf-8"))
    taken = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "container"
    }
    assert taken == {"zalo_linking", "channel_preferences", "zalo_webhook_inbox", "settings"}
