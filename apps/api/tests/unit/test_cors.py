import httpx
import pytest
from asgi_lifespan import LifespanManager

from dw_api.bootstrap import ApiContainer
from dw_api.health import HealthService
from dw_api.main import create_app
from dw_api.settings import ApiSettings
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.entitlement import DEFAULT_PLANS, PlanEntitlementService

pytestmark = pytest.mark.unit

WEB_ORIGIN = "http://localhost:3000"
# CORSMiddleware answers the preflight before routing, so any path exercises
# the method allow-list; this one is a real PUT route (sales intel).
PREFLIGHT_PATH = "/api/v1/intel/accounts/00000000-0000-5000-8000-000000000001/tenders/pref"


def make_container(settings: ApiSettings | None = None) -> ApiContainer:
    return ApiContainer(
        # public_web_url pinned: `make` exports .env, and a developer's own web
        # port must not decide what this test expects.
        settings=settings or ApiSettings(profile="test", public_web_url=WEB_ORIGIN),
        engine=None,
        health_service=HealthService(probes={}),
        token_verifier=None,
        access_context_factory=None,
        identity_bootstrap=None,
        uow_factory=None,
        authorization=ScopeAuthorizationService(),
        entitlement=PlanEntitlementService(DEFAULT_PLANS),
    )


async def preflight(method: str) -> httpx.Response:
    app = create_app(make_container())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.options(
                PREFLIGHT_PATH,
                headers={
                    "Origin": WEB_ORIGIN,
                    "Access-Control-Request-Method": method,
                    "Access-Control-Request-Headers": "content-type,authorization",
                },
            )


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
async def test_preflight_allows_every_method_the_routes_use(method: str) -> None:
    response = await preflight(method)
    assert response.status_code == 200
    assert method in response.headers["access-control-allow-methods"]


async def test_preflight_still_rejects_a_method_no_route_uses() -> None:
    response = await preflight("TRACE")
    assert response.status_code == 400


async def preflight_from(origin: str, settings: ApiSettings) -> httpx.Response:
    app = create_app(make_container(settings))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.options(
                PREFLIGHT_PATH,
                headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
            )


async def test_the_local_origin_follows_the_public_web_url() -> None:
    """A web app moved to another port is still let in, and the old port is not."""
    settings = ApiSettings(profile="test", public_web_url="http://localhost:3200")
    for origin in ("http://localhost:3200", "http://127.0.0.1:3200"):
        allowed = await preflight_from(origin, settings)
        assert allowed.status_code == 200, origin
    refused = await preflight_from("http://localhost:3000", settings)
    assert refused.status_code == 400
