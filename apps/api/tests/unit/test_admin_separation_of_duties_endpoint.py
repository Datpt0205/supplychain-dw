"""The separation-of-duties admin routes, over the real service with a fake
repository: the route's scope checks, body validation and error shapes.

What the database decides (a floor cannot be waived, a waiver in use cannot
be revoked, the audit row lands with the write) is covered against a real
database by `dw_platform`'s `test_sod_waivers.py`.
"""

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from asgi_lifespan import LifespanManager

from dw_api.bootstrap import ApiContainer
from dw_api.health import CheckState, HealthService
from dw_api.main import create_app
from dw_api.settings import ApiSettings
from dw_kernel.errors import ConflictError
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.adapters.identity.dev_token import DevTokenVerifier
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.entitlement import DEFAULT_PLANS, PlanEntitlementService
from dw_platform.application.identity import DbAccessContextFactory, MembershipAccess
from dw_platform.application.separation_of_duties import (
    SeparationOfDutiesService,
    SodRuleStatus,
    SodWaiver,
)
from dw_platform.domain.audit import AuditEvent

pytestmark = pytest.mark.unit

SECRET = "unit-test-secret-0123456789abcdef"
TENANT = uuid.uuid4()
WORKSPACE = uuid.uuid4()
PRINCIPAL = uuid.uuid4()
RULE = "sod_sc_ordering_vs_payment"
READ = frozenset({"platform.roles.read"})
DECIDE = READ | {"platform.sod_waivers.write"}


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
            roles=frozenset({"org_admin"}),
            scopes=self._scopes,
            groups=frozenset(),
            clearance="internal",
            plan_id="professional",
            feature_flags=frozenset(),
        )


class FakeRepo:
    """Answers like the SQL repository and records what it was asked to write."""

    def __init__(
        self,
        *,
        open_waiver: bool = False,
        in_use: int = 0,
        proposed_by: uuid.UUID | None = None,
        confirmed: bool = False,
    ) -> None:
        self.open_waiver = open_waiver
        self.in_use = in_use
        # Who proposed the open waiver: the database refuses their confirmation.
        self.proposed_by = proposed_by or uuid.uuid4()
        self.confirmed = confirmed
        self.written: list[tuple[str, str, str]] = []

    async def list_rules(self, context: AccessContext) -> list[SodRuleStatus]:
        waiver = (
            SodWaiver(reason="ba người", granted_by=PRINCIPAL, granted_at=datetime.now(UTC))
            if self.open_waiver
            else None
        )
        return [
            SodRuleStatus(
                key=RULE,
                description="ordering vs payment",
                left_scopes=("purchasing.duty.ordering",),
                right_scopes=("purchasing.duty.finance",),
                waivable=True,
                waiver=waiver,
            )
        ]

    async def waive(
        self,
        context: AccessContext,
        *,
        waiver_id: uuid.UUID,
        rule_key: str,
        reason: str,
        audit: AuditEvent,
    ) -> None:
        self.written.append(("waive", rule_key, reason))

    async def revoke(
        self, context: AccessContext, *, rule_key: str, reason: str, audit: AuditEvent
    ) -> bool:
        if self.in_use:
            raise ConflictError(
                "memberships still hold both sides of this rule",
                details={"rule_key": rule_key, "memberships": self.in_use},
            )
        if not self.open_waiver:
            return False
        self.written.append(("revoke", rule_key, reason))
        return True

    async def confirm(
        self, context: AccessContext, *, rule_key: str, reason: str, audit: AuditEvent
    ) -> bool:
        if not self.open_waiver or self.confirmed:
            return False
        if context.principal_id == self.proposed_by:
            raise ConflictError(
                "a waiver is confirmed by a second person, not by who proposed it",
                details={"rule_key": rule_key},
            )
        self.confirmed = True
        self.written.append(("confirm", rule_key, reason))
        return True


def make_container(repo: FakeRepo, scopes: frozenset[str]) -> ApiContainer:
    async def ok_probe() -> CheckState:
        return "ok"

    authz = ScopeAuthorizationService()
    return ApiContainer(
        settings=ApiSettings(profile="test", dev_secret=SECRET),
        engine=None,
        health_service=HealthService(probes={"database": ok_probe}),
        token_verifier=DevTokenVerifier(SECRET),
        access_context_factory=DbAccessContextFactory(FakeMembershipLookup(scopes)),
        identity_bootstrap=None,
        uow_factory=None,
        authorization=authz,
        entitlement=PlanEntitlementService(DEFAULT_PLANS),
        separation_of_duties=SeparationOfDutiesService(
            repo, authz, SystemClock(), Uuid4Generator()
        ),
    )


def headers() -> dict[str, str]:
    token = DevTokenVerifier(SECRET).issue("dev|admin", email="admin@fpt.com")
    return {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": str(TENANT),
        "X-Workspace-Id": str(WORKSPACE),
    }


async def _call(
    container: ApiContainer, method: str, path: str, body: object = None
) -> httpx.Response:
    app = create_app(container)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(
                method, f"/api/v1/admin/separation-of-duties{path}", json=body, headers=headers()
            )


async def test_the_rules_are_listed_with_this_tenants_waiver() -> None:
    response = await _call(make_container(FakeRepo(open_waiver=True), READ), "GET", "")

    assert response.status_code == 200
    (rule,) = response.json()
    assert rule["key"] == RULE and rule["waivable"] is True
    assert rule["waiver"]["reason"] == "ba người"


async def test_listing_needs_the_role_catalog_scope() -> None:
    response = await _call(make_container(FakeRepo(), frozenset()), "GET", "")
    assert response.status_code == 403


async def test_a_waiver_is_recorded_with_its_reason() -> None:
    repo = FakeRepo()
    response = await _call(
        make_container(repo, DECIDE), "POST", f"/{RULE}/waiver", {"reason": "  ba người  "}
    )

    assert response.status_code == 204
    assert repo.written == [("waive", RULE, "ba người")]


async def test_reading_the_rules_does_not_let_a_caller_waive_one() -> None:
    repo = FakeRepo()
    response = await _call(make_container(repo, READ), "POST", f"/{RULE}/waiver", {"reason": "x"})

    assert response.status_code == 403
    assert repo.written == []


@pytest.mark.parametrize("body", [{}, {"reason": ""}, {"reason": "   "}])
async def test_a_waiver_without_a_reason_is_refused(body: dict[str, str]) -> None:
    repo = FakeRepo()
    response = await _call(make_container(repo, DECIDE), "POST", f"/{RULE}/waiver", body)

    assert response.status_code == 422
    assert repo.written == []


async def test_revoking_a_waiver_in_use_is_a_conflict_naming_how_many_rely_on_it() -> None:
    response = await _call(
        make_container(FakeRepo(open_waiver=True, in_use=2), DECIDE),
        "POST",
        f"/{RULE}/waiver/revoke",
        {"reason": "đã tuyển thêm người"},
    )

    assert response.status_code == 409
    # The error contract carries details as strings.
    assert response.json()["details"]["memberships"] == "2"


async def test_revoking_without_an_open_waiver_is_not_found() -> None:
    response = await _call(
        make_container(FakeRepo(), DECIDE), "POST", f"/{RULE}/waiver/revoke", {"reason": "x"}
    )
    assert response.status_code == 404


async def test_a_second_admin_confirms_a_waiver_with_a_reason() -> None:
    repo = FakeRepo(open_waiver=True)
    response = await _call(
        make_container(repo, DECIDE), "POST", f"/{RULE}/waiver/confirm", {"reason": " đồng ý "}
    )

    assert response.status_code == 204
    assert repo.written == [("confirm", RULE, "đồng ý")]


async def test_the_proposer_confirming_their_own_waiver_is_a_conflict() -> None:
    repo = FakeRepo(open_waiver=True, proposed_by=PRINCIPAL)
    response = await _call(
        make_container(repo, DECIDE), "POST", f"/{RULE}/waiver/confirm", {"reason": "ok"}
    )

    assert response.status_code == 409
    assert repo.written == []


@pytest.mark.parametrize(
    ("repo", "scopes", "body", "status"),
    [
        (FakeRepo(open_waiver=True), READ, {"reason": "ok"}, 403),
        (FakeRepo(open_waiver=True), DECIDE, {"reason": "  "}, 422),
        (FakeRepo(), DECIDE, {"reason": "ok"}, 404),
        (FakeRepo(open_waiver=True, confirmed=True), DECIDE, {"reason": "ok"}, 404),
    ],
    ids=["no-scope", "no-reason", "no-waiver", "already-confirmed"],
)
async def test_a_confirmation_is_refused_without_scope_reason_or_waiting_waiver(
    repo: FakeRepo, scopes: frozenset[str], body: dict[str, str], status: int
) -> None:
    response = await _call(make_container(repo, scopes), "POST", f"/{RULE}/waiver/confirm", body)

    assert response.status_code == status
    assert repo.written == []


async def test_the_listing_says_whether_a_waiver_is_confirmed() -> None:
    response = await _call(make_container(FakeRepo(open_waiver=True), READ), "GET", "")

    (rule,) = response.json()
    assert rule["waiver"]["confirmed_by"] is None
    assert rule["waiver"]["confirmed_at"] is None
