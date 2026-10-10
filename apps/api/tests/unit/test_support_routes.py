"""SA7: a support context reaches only the routes that opted in (ADR 0024).

Default deny is the real gate: a missing scope only stops a route that checks
one, and `GET /runs/{id}` checks none. So `RequireAccessContext` refuses the
support header outright, and the set of routes taking
`RequireAccessContextOrSupport` must equal `SUPPORT_ALLOWED_ROUTES` (empty on
the platform). A context opening a route adds it there with its own negative
test.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from test_support_endpoint import ENABLED, GRANTER, SECRET, TENANT, WORKSPACE, _container

from dw_api.bootstrap.wiring import SUPPORT_ALLOWED_ROUTES
from dw_api.dependencies.auth import (
    SUPPORT_GRANT_HEADER,
    RequireAccessContextOrSupport,
    get_access_context_or_support,
)
from dw_api.main import create_app
from dw_platform.adapters.identity.dev_token import DevTokenVerifier

pytestmark = pytest.mark.unit


def _calls(dependant: Dependant) -> Iterator[Callable[..., Any]]:
    for sub in dependant.dependencies:
        if sub.call is not None:
            yield sub.call
        yield from _calls(sub)


def support_routes(app: FastAPI) -> set[tuple[str, str]]:
    """Every (method, path) whose dependency tree builds a support context."""
    found: set[tuple[str, str]] = set()
    for route in app.routes:
        if isinstance(route, APIRoute) and get_access_context_or_support in set(
            _calls(route.dependant)
        ):
            found |= {(method, route.path) for method in route.methods}
    return found


def test_the_walker_sees_a_route_that_opts_in() -> None:
    # The guard below can fail: a route taking the dependency is found.
    app = FastAPI()

    @app.get("/probe/{item_id}")
    async def probe(item_id: str, context: RequireAccessContextOrSupport) -> None: ...

    assert support_routes(app) == {("GET", "/probe/{item_id}")}


def test_only_the_allowed_routes_take_a_support_context() -> None:
    app = create_app(_container(GRANTER, ENABLED))
    assert support_routes(app) == set(SUPPORT_ALLOWED_ROUTES)


@pytest.mark.parametrize(
    "path",
    [
        f"/runs/{uuid.uuid4()}",
        "/approvals",
        "/knowledge/documents",
        "/audit/events",
        "/admin/members",
        "/support/grants",
    ],
)
async def test_sa7_a_route_not_opened_to_support_refuses_the_header(path: str) -> None:
    token = DevTokenVerifier(SECRET).issue("dev|staff", amr=["pwd", "otp"])
    app = create_app(_container(GRANTER, ENABLED))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/api/v1{path}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Tenant-Id": str(TENANT),
                    "X-Workspace-Id": str(WORKSPACE),
                    SUPPORT_GRANT_HEADER: str(uuid.uuid4()),
                },
            )
    assert response.status_code == 403, path
    assert response.json()["details"]["reason_code"] == "support_context_not_allowed", path


async def test_the_dependency_itself_refuses_a_route_missing_from_the_list() -> None:
    # Belt and braces: a route that takes the dependency without being listed
    # still refuses support, before any grant is read.
    app = create_app(_container(GRANTER, ENABLED))

    @app.get("/api/v1/probe/{item_id}")
    async def probe(item_id: str, context: RequireAccessContextOrSupport) -> dict[str, str]:
        return {"tenant": str(context.tenant_id)}

    token = DevTokenVerifier(SECRET).issue("dev|staff", amr=["pwd", "otp"])
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/probe/1",
                headers={
                    "Authorization": f"Bearer {token}",
                    SUPPORT_GRANT_HEADER: str(uuid.uuid4()),
                },
            )
    assert response.status_code == 403
    assert response.json()["details"]["reason_code"] == "support_context_not_allowed"
