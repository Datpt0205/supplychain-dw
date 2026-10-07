"""Dev demo endpoints: one-click login works in dev, absent in production."""

from __future__ import annotations

import httpx
import pytest

from dw_api.bootstrap import build_container
from dw_api.main import create_app
from dw_api.settings import ApiSettings

pytestmark = pytest.mark.unit

DEV_SECRET = "unit-test-dev-secret-0123456789abcdef"


def make_app(auth_mode: str = "dev"):
    settings = ApiSettings(
        profile="test",
        auth_mode=auth_mode,  # type: ignore[arg-type]
        dev_secret=DEV_SECRET,
        oidc_issuer_url="https://keycloak.example/realms/dw" if auth_mode == "oidc" else None,
        rate_limit_per_minute=0,
    )
    return create_app(build_container(settings))


def deployed_settings(profile: str = "production", task_connector: str = "none") -> ApiSettings:
    """Settings that otherwise satisfy a deployed profile, so a test can
    exercise one guard at a time.

    Every field here is one of the deployed profile's requirements: a real
    identity provider, a real model provider, a durable vector store, meaningful
    embeddings, CORS origins listed rather than inferred, and every URL a
    browser or Zalo reaches over TLS.
    """
    return ApiSettings(
        profile=profile,  # type: ignore[arg-type]
        auth_mode="oidc",
        oidc_issuer_url="https://idp.example/realms/dw",
        model_provider="openai_compatible",
        openai_api_key="unit-test-key",
        openai_base_url="https://api.example.com/v1",
        embedding_provider="openai_compatible",
        qdrant_url="https://qdrant.example:6333",
        cors_origins=["https://app.example.com"],
        public_base_url="https://api.example.com",
        task_connector=task_connector,
        database_url="postgresql+asyncpg://u:p@db/dw",
        rate_limit_per_minute=0,
    )


def prod_settings(task_connector: str = "none") -> ApiSettings:
    return deployed_settings(task_connector=task_connector)


async def _get(app, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


async def _post(app, path: str, body: dict) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, json=body)


async def test_demo_users_listed_in_dev_mode() -> None:
    response = await _get(make_app(), "/api/v1/dev/demo-users")
    assert response.status_code == 200
    subjects = {user["subject"] for user in response.json()}
    assert {"dev|an.nguyen", "dev|binh.tran", "dev|bao.pham"} <= subjects


async def test_session_issues_verifiable_token_with_context() -> None:
    from dw_platform.adapters.identity.dev_token import DevTokenVerifier

    response = await _post(make_app(), "/api/v1/dev/session", {"subject": "dev|an.nguyen"})
    assert response.status_code == 200
    session = response.json()
    assert session["display_name"] == "Nguyễn Văn An"
    assert session["tenant_name"] == "Công ty Alpha"
    claims = await DevTokenVerifier(DEV_SECRET).verify(session["token"])
    assert claims.subject == "dev|an.nguyen"


async def test_unknown_subject_is_rejected() -> None:
    response = await _post(make_app(), "/api/v1/dev/session", {"subject": "dev|hacker"})
    assert response.status_code == 404


async def test_session_issuer_absent_outside_dev_auth_mode() -> None:
    # The dev-token issuer must never be a Keycloak bypass: gated to dev mode.
    app = make_app(auth_mode="oidc")
    response = await _post(app, "/api/v1/dev/session", {"subject": "dev|an.nguyen"})
    assert response.status_code == 404


async def test_sample_data_endpoints_available_under_oidc() -> None:
    # Sample-data endpoints use the normal auth context, so they stay mounted
    # (they just require a valid token) — only /session is dev-only.
    app = make_app(auth_mode="oidc")
    response = await _get(app, "/api/v1/dev/demo-users")
    assert response.status_code == 200


def test_none_connector_is_allowed_in_production() -> None:
    # The blocker for turning prod to profile=production: 'none' (no external
    # task system wired) is a legitimate production choice.
    prod_settings(task_connector="none").validate_for_profile()


def test_mock_connector_is_still_forbidden_in_production() -> None:
    with pytest.raises(RuntimeError, match="mock task connector is forbidden"):
        prod_settings(task_connector="mock").validate_for_profile()


def test_uat_is_held_to_the_same_rules_as_production() -> None:
    """UAT carries real people's data; the difference is blast radius, not rigour."""
    deployed_settings(profile="uat").validate_for_profile()
    with pytest.raises(RuntimeError, match="mock task connector is forbidden"):
        deployed_settings(profile="uat", task_connector="mock").validate_for_profile()


async def test_the_schema_stays_available_off_production() -> None:
    # Developers keep Swagger in local/test; only production hides it (the app
    # passes docs_url/openapi_url=None to FastAPI when profile == production).
    assert (await _get(make_app(auth_mode="oidc"), "/api/openapi.json")).status_code == 200


@pytest.mark.parametrize("profile", ["uat", "production"])
@pytest.mark.parametrize(
    ("field", "value", "named"),
    [
        ("oidc_issuer_url", "http://idp.example/realms/dw", "DW_API_OIDC_ISSUER_URL"),
        ("public_base_url", "http://api.example.com", "DW_API_PUBLIC_BASE_URL"),
        ("public_base_url", "", "DW_API_PUBLIC_BASE_URL"),
        ("cors_origins", ["http://app.example.com"], "DW_API_CORS_ORIGINS"),
        # One plain origin among https ones is still a plain origin.
        (
            "cors_origins",
            ["https://app.example.com", "http://app.example.com"],
            "DW_API_CORS_ORIGINS",
        ),
    ],
)
def test_a_deployed_profile_refuses_a_url_that_is_not_https(
    profile: str, field: str, value: object, named: str
) -> None:
    """ADR 0023: a deployment people sign into is reached over TLS only."""
    settings = deployed_settings(profile=profile).model_copy(update={field: value})
    with pytest.raises(RuntimeError, match=named):
        settings.validate_for_profile()


def test_local_does_not_ask_for_https() -> None:
    ApiSettings(
        profile="local",
        auth_mode="oidc",
        oidc_issuer_url="http://localhost:28686/realms/dw",
        cors_origins=["http://localhost:3200"],
        public_base_url="",
    ).validate_for_profile()
