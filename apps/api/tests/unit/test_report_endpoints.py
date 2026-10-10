"""Unit: the report routes (ticket ai-automation/20) over the real handlers
and `testing.reports`: both case reads required (403 otherwise, nothing
read), a window outside 1..366 days refused (422), the figures and the
acceptance rows as the handlers count them.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager
from test_product_case_endpoints import SECRET, TENANT, WORKSPACE, FakeMembershipLookup, _headers

from dw_agent_runtime.model.prompts import load_shipped_prompts
from dw_api.bootstrap import ApiContainer
from dw_api.health import CheckState, HealthService
from dw_api.main import create_app
from dw_api.settings import ApiSettings
from dw_platform.adapters.identity.dev_token import DevTokenVerifier
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.entitlement import DEFAULT_PLANS, PlanEntitlementService
from dw_platform.application.identity import DbAccessContextFactory
from dw_supply_chain.application.handlers import PO_CASE_READ, PRODUCT_CASE_READ
from dw_supply_chain.domain.reports import CaseMove, DraftVersion, WeeklySummaryWriting
from dw_supply_chain.presentation.report_routes import ReportHandlers
from dw_supply_chain.testing.extraction import ScriptedGateway
from dw_supply_chain.testing.purchase_orders import NOW
from dw_supply_chain.testing.reports import ReportWorld

pytestmark = pytest.mark.unit

BASE = "/api/v1/supply-chain"
REPO_ROOT = Path(__file__).resolve().parents[4]
READS = frozenset({PO_CASE_READ, PRODUCT_CASE_READ})


def _world() -> ReportWorld:
    world = ReportWorld(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        gateway=ScriptedGateway(  # type: ignore[arg-type]
            load_shipped_prompts(REPO_ROOT / "configs"), answer=WeeklySummaryWriting()
        ),
    )
    world.reads.product.append(
        (world.scope, NOW - timedelta(days=1), CaseMove("DX-1", "proposed", "propose"))
    )
    world.reads.drafts.append(
        (world.scope, NOW, DraftVersion("purchase_order", str(uuid.uuid4()), 1, "confirmed"))
    )
    return world


def _container(world: ReportWorld, scopes: frozenset[str]) -> ApiContainer:
    async def ok_probe() -> CheckState:
        return "ok"

    return ApiContainer(
        settings=ApiSettings(profile="test", dev_secret=SECRET),
        engine=None,
        health_service=HealthService(probes={"database": ok_probe}),
        token_verifier=DevTokenVerifier(SECRET),
        access_context_factory=DbAccessContextFactory(FakeMembershipLookup(scopes, uuid.uuid4())),
        identity_bootstrap=None,
        uow_factory=None,
        authorization=ScopeAuthorizationService(),
        entitlement=PlanEntitlementService(DEFAULT_PLANS),
        supply_chain_reports=ReportHandlers(
            weekly=world.weekly(),
            summary=world.summary(),
            suppliers=world.suppliers(),
            acceptance=world.acceptance(),
        ),
    )


async def _send(container: ApiContainer, method: str, path: str) -> httpx.Response:
    app = create_app(container)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, f"{BASE}{path}", headers=_headers())


async def test_the_weekly_report_names_its_cases() -> None:
    response = await _send(_container(_world(), READS), "GET", "/reports/weekly?day=2026-10-09")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["week_start"], body["week_end"]) == ("2026-10-05", "2026-10-11")
    figures = {f["key"]: f for f in body["figures"]}
    assert (figures["proposed"]["value"], figures["proposed"]["cases"]) == (1, ["DX-1"])


async def test_the_acceptance_rows_and_the_window_rule() -> None:
    container = _container(_world(), READS)
    response = await _send(container, "GET", "/reports/ai-acceptance")
    assert response.status_code == 200, response.text
    (row,) = response.json()["rows"]
    assert (row["doc_type"], row["as_is"], row["minutes_saved"]) == ("purchase_order", 1, 30)
    for days in (0, 367):
        assert (
            await _send(container, "GET", f"/reports/ai-acceptance?days={days}")
        ).status_code == 422


@pytest.mark.parametrize("scopes", [frozenset({PO_CASE_READ}), frozenset({PRODUCT_CASE_READ})])
async def test_without_both_case_reads_every_report_is_403(scopes: frozenset[str]) -> None:
    world = _world()
    container = _container(world, scopes)
    for method, path in (
        ("GET", "/reports/weekly"),
        ("POST", "/reports/weekly/summary"),
        ("GET", "/reports/suppliers"),
        ("GET", "/reports/ai-acceptance"),
    ):
        assert (await _send(container, method, path)).status_code == 403, path
    assert world.gateway.sent == []
