"""Unit: the case assistant's routes (ticket ai-automation/19).

`POST /po-cases/{id}/questions` and `/product-cases/{id}/questions` over the
real `AskAboutCase` (the in-memory world of `testing.case_assistant`, the
shipped prompt): an answer with the label of what each sentence cites, a
request naming anything but the question refused (422), a caller without the
read of the case's kind refused (403) before the model is asked, another
workspace's case not found (404).
"""

from __future__ import annotations

import uuid
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
from dw_supply_chain.application.handlers import DOCUMENT_READ, PO_CASE_READ, PRODUCT_CASE_READ
from dw_supply_chain.domain.case_answer import CaseAnswerWriting
from dw_supply_chain.domain.grounded_writing import CitedSentence
from dw_supply_chain.testing.case_assistant import AssistantWorld
from dw_supply_chain.testing.extraction import ScriptedGateway

pytestmark = pytest.mark.unit

BASE = "/api/v1/supply-chain"
REPO_ROOT = Path(__file__).resolve().parents[4]
PROMPTS = load_shipped_prompts(REPO_ROOT / "configs")
READER = frozenset({PRODUCT_CASE_READ, PO_CASE_READ, DOCUMENT_READ})
MOQ = CaseAnswerWriting(sentences=[CitedSentence(text="BM04 ghi MOQ là 500 cái.", cites=["bm04"])])


def _world() -> AssistantWorld:
    world = AssistantWorld(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        gateway=ScriptedGateway(PROMPTS, answer=MOQ),  # type: ignore[arg-type]
    )
    return world


def _container(world: AssistantWorld, scopes: frozenset[str]) -> ApiContainer:
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
        supply_chain_ask_about_case=world.ask(),
    )


async def _post(container: ApiContainer, path: str, body: object) -> httpx.Response:
    app = create_app(container)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(f"{BASE}{path}", json=body, headers=_headers())


async def test_a_question_is_answered_with_the_label_of_what_it_cites() -> None:
    world = _world()
    case = world.add_product()
    world.add_profile(case, {"material": "Inox 304"}, moq=500)
    response = await _post(
        _container(world, READER),
        f"/product-cases/{case.id}/questions",
        {"question": "BM04 ghi MOQ bao nhiêu?"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answered"] is True
    assert body["sentences"] == [
        {
            "text": "BM04 ghi MOQ là 500 cái.",
            "cites": [{"key": "bm04", "label": "BM04 phiên bản 1"}],
        }
    ]


@pytest.mark.parametrize(
    "body",
    [
        {"question": "MOQ?", "channel": "web"},
        {"question": "MOQ?", "tenant_id": str(uuid.uuid4())},
        {"question": ""},
        {"question": "a\u0000b"},
    ],
    ids=["names-a-channel", "names-a-tenant", "empty", "nul"],
)
async def test_a_request_naming_anything_but_the_question_is_422(body: dict[str, object]) -> None:
    world = _world()
    case = world.add_product()
    response = await _post(_container(world, READER), f"/product-cases/{case.id}/questions", body)
    assert response.status_code == 422
    assert world.gateway.sent == []


async def test_without_the_read_of_its_kind_the_model_is_not_asked() -> None:
    world = _world()
    case = world.add_po_case()
    response = await _post(
        _container(world, frozenset({PRODUCT_CASE_READ})),
        f"/po-cases/{case.id}/questions",
        {"question": "ETD khi nào?"},
    )
    assert response.status_code == 403
    assert world.gateway.sent == []


async def test_another_workspaces_case_is_not_found() -> None:
    world = _world()
    case = world.add_po_case(workspace=uuid.uuid4())
    response = await _post(
        _container(world, READER), f"/po-cases/{case.id}/questions", {"question": "ETD?"}
    )
    assert response.status_code == 404
    assert world.gateway.sent == []
