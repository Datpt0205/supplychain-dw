"""`GET /memory/candidates/{id}`: what a reviewer reads before deciding.

The route checks `memory.read`; workspace and clearance are the service's,
where the row is read, and are proven against Postgres in
`apps/worker/tests/integration/test_memory_review_db.py`. What is provable here
is that the route asks for the scope before it reads anything, and that it
passes the service's refusal through rather than serving the content anyway.
"""

import uuid
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import pytest
from asgi_lifespan import LifespanManager

from dw_api.bootstrap import ApiContainer
from dw_api.health import CheckState, HealthService
from dw_api.main import create_app
from dw_api.settings import ApiSettings
from dw_kernel.errors import PermissionDeniedError
from dw_memory.service import ReviewCandidate
from dw_platform.adapters.identity.dev_token import DevTokenVerifier
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.entitlement import DEFAULT_PLANS, PlanEntitlementService
from dw_platform.application.identity import DbAccessContextFactory, MembershipAccess

pytestmark = pytest.mark.unit

SECRET = "unit-test-secret-0123456789abcdef"
TENANT = uuid.uuid4()
WORKSPACE = uuid.uuid4()
PRINCIPAL = uuid.uuid4()
CANDIDATE = uuid.uuid4()


class FakeMembershipLookup:
    def __init__(self, scopes: frozenset[str]) -> None:
        self._scopes = scopes

    async def find_access(
        self, subject: str, issuer: str, tenant_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> MembershipAccess | None:
        if tenant_id != TENANT or workspace_id != WORKSPACE:
            return None
        return MembershipAccess(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            principal_id=PRINCIPAL,
            roles=frozenset({"approver"}),
            scopes=self._scopes,
            groups=frozenset(),
            clearance="internal",
            plan_id="professional",
            feature_flags=frozenset(),
        )


class FakeMemoryService:
    """Answers `get_candidate` the way the service does: the content, or the
    service's own refusal when the clearance does not cover it."""

    def __init__(self, refuse: bool = False) -> None:
        self.refuse = refuse
        self.calls: list[AccessContext] = []

    async def get_candidate(
        self, candidate_id: uuid.UUID, context: AccessContext
    ) -> ReviewCandidate:
        self.calls.append(context)
        if self.refuse:
            raise PermissionDeniedError("your clearance does not cover this memory candidate")
        return ReviewCandidate(
            candidate_id=candidate_id,
            worker_id="demo",
            memory_type="commitment",
            content="Anh An cam kết gửi hợp đồng trước thứ Sáu.",
            structured_facts={},
            subject_refs=("crm:account:1",),
            fact_key="contract_date",
            provenance_refs=(),
            classification="internal",
            confidence=0.6,
            decision="review",
            memory_id=None,
            created_by_run_id=uuid.uuid4(),
            created_at=datetime(2026, 10, 6, tzinfo=UTC),
        )


def make_container(service: FakeMemoryService, scopes: frozenset[str]) -> ApiContainer:
    async def ok_probe() -> CheckState:
        return "ok"

    return ApiContainer(
        settings=ApiSettings(profile="test", dev_secret=SECRET),
        engine=None,
        health_service=HealthService(probes={"database": ok_probe}),
        token_verifier=DevTokenVerifier(SECRET),
        access_context_factory=DbAccessContextFactory(FakeMembershipLookup(scopes)),
        identity_bootstrap=None,
        uow_factory=None,
        authorization=ScopeAuthorizationService(),
        entitlement=PlanEntitlementService(DEFAULT_PLANS),
        memory_service=cast(Any, service),
    )


async def _get(container: ApiContainer) -> httpx.Response:
    token = DevTokenVerifier(SECRET).issue("dev|reviewer", email="reviewer@fpt.com")
    app = create_app(container)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(
                f"/api/v1/memory/candidates/{CANDIDATE}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Tenant-Id": str(TENANT),
                    "X-Workspace-Id": str(WORKSPACE),
                },
            )


async def test_a_reader_with_the_scope_gets_the_content_in_their_own_tenancy() -> None:
    service = FakeMemoryService()

    response = await _get(make_container(service, frozenset({"memory.read"})))

    assert response.status_code == 200
    assert response.json()["content"].startswith("Anh An")
    [context] = service.calls
    assert (context.tenant_id, context.workspace_id) == (TENANT, WORKSPACE)


async def test_without_memory_read_nothing_is_read() -> None:
    service = FakeMemoryService()

    response = await _get(make_container(service, frozenset({"approvals.decide"})))

    assert response.status_code == 403
    assert service.calls == []


async def test_the_services_clearance_refusal_reaches_the_caller() -> None:
    response = await _get(
        make_container(FakeMemoryService(refuse=True), frozenset({"memory.read"}))
    )

    assert response.status_code == 403
    assert "Anh An" not in response.text
