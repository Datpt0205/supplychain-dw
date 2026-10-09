"""Step 12's sub-flow routes through the real app wiring (slice PK).

The PO case and the sub-flow's records are faked; auth, the access context,
the request models, the idempotency dependency, the routes and the handlers run
for real. The container carries only the packaging handlers: the router mounts
on its own guard.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import httpx
import pytest
from asgi_lifespan import LifespanManager
from test_product_case_endpoints import (
    SECRET,
    TENANT,
    WORKSPACE,
    FakeDocuments,
    FakeHolders,
    FakeMembershipLookup,
    FakeNotifier,
    FakePolicies,
    MemoryStore,
    _headers,
)

from dw_api.bootstrap import ApiContainer
from dw_api.bootstrap.paths import (
    SUPPLY_CHAIN_ACTION_DUTIES,
    SUPPLY_CHAIN_PACKAGING_POLICY,
    SUPPLY_CHAIN_STEP_PREPARATION_POLICY,
)
from dw_api.health import CheckState, HealthService
from dw_api.main import create_app
from dw_api.settings import ApiSettings
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.adapters.identity.dev_token import DevTokenVerifier
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.entitlement import DEFAULT_PLANS, PlanEntitlementService
from dw_platform.application.idempotency import HttpIdempotency
from dw_platform.application.identity import DbAccessContextFactory
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.action_duties import CaseDuty, load_supply_chain_action_duties
from dw_supply_chain.application.handlers import (
    ACTION_DUTIES_READ,
    ACTION_DUTIES_WRITE,
    PO_CASE_READ,
    duty_scope,
)
from dw_supply_chain.application.packaging_designs import (
    GetPackagingDesign,
    GetPackagingPolicy,
    SetPackagingPolicyOverride,
    TakePackagingStep,
)
from dw_supply_chain.application.packaging_papers import GetPackagingProof, PackagingSources
from dw_supply_chain.application.step_preparation import StoredReading
from dw_supply_chain.domain.case_document import (
    CaseDocument,
    CaseDocumentId,
    CaseKind,
    DocumentType,
)
from dw_supply_chain.domain.extraction import EXTRACTION_SPECS, ExtractionStatus
from dw_supply_chain.domain.packaging_design import PackagingDesign, PackagingHistoryEntry
from dw_supply_chain.domain.po_case import CaseState, POCase, POCaseId
from dw_supply_chain.packaging_policy import load_supply_chain_packaging_policy
from dw_supply_chain.step_preparation_policy import load_supply_chain_step_preparation
from dw_supply_chain.testing.purchase_orders import ELMICH_PREPARATION
from dw_supply_chain.testing.step_preparation import (
    InMemoryDocuments,
    InMemoryProfiles,
    InMemoryReadings,
)

pytestmark = pytest.mark.unit

BASE = "/api/v1/supply-chain"
ORDERING = frozenset({PO_CASE_READ, duty_scope(CaseDuty.ORDERING)})
RND = frozenset({PO_CASE_READ, duty_scope(CaseDuty.RND)})


def _case(state: CaseState = CaseState.PRE_PRODUCTION) -> POCase:
    return POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(TENANT),
        workspace_id=WorkspaceId(WORKSPACE),
        po_reference="PO-PK-1",
        supplier_name="NCC",
        state=state,
    )


@dataclass
class FakePOCases:
    """`get` narrowed by tenant and workspace, as RLS narrows the table."""

    cases: list[POCase] = field(default_factory=list)

    async def get(self, context: AccessContext, case_id: POCaseId) -> POCase | None:
        for case in self.cases:
            if case.id.value == case_id.value and (
                case.tenant_id.value,
                case.workspace_id.value,
            ) == (context.tenant_id, context.workspace_id):
                return case
        return None


@dataclass
class FakeDesigns:
    rows: dict[uuid.UUID, PackagingDesign] = field(default_factory=dict)
    history_rows: list[PackagingHistoryEntry] = field(default_factory=list)

    async def get(self, context: AccessContext, po_case_id: uuid.UUID) -> PackagingDesign | None:
        return self.rows.get(po_case_id)

    async def history(
        self, context: AccessContext, po_case_id: uuid.UUID
    ) -> list[PackagingHistoryEntry]:
        return list(self.history_rows)

    async def save(
        self,
        context: AccessContext,
        design: PackagingDesign,
        *,
        actor_id: uuid.UUID,
        audit: AuditEvent,
    ) -> None:
        for event in design.pop_pending_events():
            self.history_rows.append(
                PackagingHistoryEntry(
                    action=event.action,
                    reason=event.reason,
                    document_id=event.document_id,
                    note=event.note,
                    actor_id=actor_id,
                    occurred_at=SystemClock().now(),
                )
            )
        self.rows[design.po_case_id] = design


@dataclass
class World:
    po_cases: FakePOCases = field(default_factory=FakePOCases)
    designs: FakeDesigns = field(default_factory=FakeDesigns)
    policies: FakePolicies = field(default_factory=FakePolicies)
    notifier: FakeNotifier = field(default_factory=FakeNotifier)
    store: MemoryStore = field(default_factory=MemoryStore)
    # Step 12's proof check (ticket ai-automation/16).
    documents: InMemoryDocuments = field(default_factory=InMemoryDocuments)
    readings: InMemoryReadings = field(default_factory=InMemoryReadings)
    profiles: InMemoryProfiles = field(default_factory=InMemoryProfiles)

    def container(self, scopes: frozenset[str]) -> ApiContainer:
        async def ok_probe() -> CheckState:
            return "ok"

        authz = ScopeAuthorizationService()
        duties = load_supply_chain_action_duties(SUPPLY_CHAIN_ACTION_DUTIES)
        packaging = load_supply_chain_packaging_policy(SUPPLY_CHAIN_PACKAGING_POLICY)
        common = {"authz": authz, "policy_override_repo": self.policies}
        return ApiContainer(
            settings=ApiSettings(profile="test", dev_secret=SECRET),
            engine=None,
            health_service=HealthService(probes={"database": ok_probe}),
            token_verifier=DevTokenVerifier(SECRET),
            access_context_factory=DbAccessContextFactory(
                FakeMembershipLookup(scopes, uuid.uuid4())
            ),
            identity_bootstrap=None,
            uow_factory=None,
            authorization=authz,
            entitlement=PlanEntitlementService(DEFAULT_PLANS),
            idempotency=HttpIdempotency(store=self.store, clock=SystemClock()),
            supply_chain_get_packaging_design=GetPackagingDesign(
                po_cases=self.po_cases,  # type: ignore[arg-type]
                designs=self.designs,
                platform_default_duties=duties,
                platform_default_policy=packaging,
                documents=FakeDocuments(),
                **common,  # type: ignore[arg-type]
            ),
            supply_chain_take_packaging_step=TakePackagingStep(
                po_cases=self.po_cases,  # type: ignore[arg-type]
                designs=self.designs,
                documents=FakeDocuments(),
                platform_default_duties=duties,
                platform_default_policy=packaging,
                holders=FakeHolders(),
                notifier=self.notifier,
                ids=Uuid4Generator(),
                clock=SystemClock(),
                **common,  # type: ignore[arg-type]
            ),
            supply_chain_get_packaging_policy=GetPackagingPolicy(
                platform_default_policy=packaging,
                **common,  # type: ignore[arg-type]
            ),
            supply_chain_set_packaging_policy_override=SetPackagingPolicyOverride(
                ids=Uuid4Generator(),
                clock=SystemClock(),
                **common,  # type: ignore[arg-type]
            ),
            supply_chain_get_packaging_proof=GetPackagingProof(
                cases=self.po_cases,  # type: ignore[arg-type]
                sources=PackagingSources(
                    profiles=self.profiles, documents=self.documents, readings=self.readings
                ),
                platform_default_policy=load_supply_chain_step_preparation(
                    SUPPLY_CHAIN_STEP_PREPARATION_POLICY
                ),
                **common,  # type: ignore[arg-type]
            ),
        )


async def _send(
    container: ApiContainer,
    method: str,
    path: str,
    body: object | None = None,
    *,
    auth: bool = True,
) -> httpx.Response:
    headers = _headers(str(uuid.uuid4())) if auth else {}
    app = create_app(container)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, f"{BASE}{path}", json=body, headers=headers)


async def test_a_step_needs_a_bearer_token_and_changes_nothing_without_one() -> None:
    world = World()
    case = _case()
    world.po_cases.cases.append(case)
    response = await _send(
        world.container(ORDERING),
        "POST",
        f"/po-cases/{case.id}/packaging-design/steps",
        {"action": "approve_colour"},
        auth=False,
    )
    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"
    assert world.designs.rows == {}


async def test_the_page_reads_the_sub_flow_and_who_may_take_each_step() -> None:
    world = World()
    case = _case()
    world.po_cases.cases.append(case)
    response = await _send(world.container(RND), "GET", f"/po-cases/{case.id}/packaging-design")
    assert response.status_code == 200
    body = response.json()
    assert (body["colour_status"], body["pre_production_test"], body["version"]) == (
        "pending",
        "pending",
        0,
    )
    assert body["require_pre_production_test"] is False
    allowed = {s["action"] for s in body["steps"] if s["allowed"]}
    assert allowed == {"pass_pre_production_test", "fail_pre_production_test"}
    by_action = {s["action"]: s for s in body["steps"]}
    assert by_action["pass_pre_production_test"]["requires_document"] is True
    assert by_action["request_colour_revision"]["requires_reason"] is True


async def test_cung_ung_approves_the_colour_and_the_history_carries_the_mkt_note() -> None:
    world = World()
    case = _case()
    world.po_cases.cases.append(case)
    container = world.container(ORDERING)
    taken = await _send(
        container,
        "POST",
        f"/po-cases/{case.id}/packaging-design/steps",
        {"action": "approve_colour"},
    )
    assert taken.status_code == 200, taken.text
    assert taken.json()["colour_status"] == "approved"
    page = (await _send(container, "GET", f"/po-cases/{case.id}/packaging-design")).json()
    assert [(e["action"], e["note"] is not None) for e in page["history"]] == [
        ("approve_colour", True)
    ]


@pytest.mark.parametrize(
    "body",
    [
        {"action": "approve_colour", "tenant_id": str(uuid.uuid4())},
        {"action": "approve_colour", "colour_status": "approved"},
        {"action": "start_production"},
        {"action": "approve_colour", "reason": "a\u0000b"},
    ],
    ids=["names-a-tenant", "names-a-state", "not-a-sub-step", "nul-in-reason"],
)
async def test_a_request_naming_anything_but_a_step_is_422(body: dict[str, object]) -> None:
    world = World()
    case = _case()
    world.po_cases.cases.append(case)
    response = await _send(
        world.container(ORDERING), "POST", f"/po-cases/{case.id}/packaging-design/steps", body
    )
    assert response.status_code == 422
    assert world.designs.rows == {}


async def test_a_case_of_another_workspace_is_not_found() -> None:
    world = World()
    elsewhere = _case()
    elsewhere.workspace_id = WorkspaceId(uuid.uuid4())
    world.po_cases.cases.append(elsewhere)
    container = world.container(ORDERING)
    assert (
        await _send(container, "GET", f"/po-cases/{elsewhere.id}/packaging-design")
    ).status_code == 404
    response = await _send(
        container,
        "POST",
        f"/po-cases/{elsewhere.id}/packaging-design/steps",
        {"action": "approve_colour"},
    )
    assert response.status_code == 404
    assert world.designs.rows == {}


async def test_the_packaging_policy_is_set_by_whoever_sets_duties_and_strictly_typed() -> None:
    world = World()
    document = {
        "schema_version": "1.0",
        "policy_id": "supply_chain_packaging",
        "policy_version": "1.0.0",
        "require_pre_production_test": True,
    }
    refused = await _send(world.container(ORDERING), "PUT", "/packaging-policy", document)
    assert refused.status_code == 403
    # A reader of the rule (the settings screen shows it locked) is refused the
    # write at the route, whatever the screen offers.
    reader = world.container(frozenset({ACTION_DUTIES_READ}))
    assert (await _send(reader, "GET", "/packaging-policy")).status_code == 200
    assert (await _send(reader, "PUT", "/packaging-policy", document)).status_code == 403
    admin = world.container(frozenset({ACTION_DUTIES_READ, ACTION_DUTIES_WRITE}))
    loose = await _send(
        admin, "PUT", "/packaging-policy", {**document, "require_pre_production_test": "yes"}
    )
    assert loose.status_code == 422
    assert world.policies.stored == {}
    assert (await _send(admin, "PUT", "/packaging-policy", document)).status_code == 200
    read = await _send(admin, "GET", "/packaging-policy")
    assert read.json()["require_pre_production_test"] is True


def _proof(world: World, case: POCase, fields: dict[str, object]) -> None:
    spec = EXTRACTION_SPECS[DocumentType.PACKAGING_DESIGN]
    document = CaseDocument(
        id=CaseDocumentId(uuid.uuid4()),
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        case_kind=CaseKind.PO,
        case_id=case.id.value,
        doc_type=DocumentType.PACKAGING_DESIGN,
        object_key="k",
        filename="ban-in.pdf",
        content_type="application/pdf",
        size_bytes=1,
        sha256="0" * 64,
        version=1,
        uploaded_by=uuid.uuid4(),
        uploaded_at=SystemClock().now(),
    )
    world.documents.rows.append(document)
    world.readings.rows.append(
        (
            TENANT,
            WORKSPACE,
            StoredReading(
                id=uuid.uuid4(),
                document_id=document.id.value,
                sha256=document.sha256,
                prompt_id=spec.prompt_id,
                prompt_version=spec.prompt_version,
                status=ExtractionStatus.EXTRACTED,
                fields={k: {"value": v, "quote": str(v)} for k, v in fields.items()},
                gaps=[],
            ),
        )
    )


async def test_the_proof_check_names_what_the_label_lacks_and_hides_another_workspaces_case() -> (
    None
):
    """Ticket ai-automation/16: the newest proof, read, against the label rules
    (and the BM04: none here, so that is named too); a PO case of another
    workspace is not found."""
    world = World()
    world.policies.stored["supply_chain_step_preparation"] = ELMICH_PREPARATION.model_dump(
        mode="json"
    )
    case = _case()
    world.po_cases.cases.append(case)
    _proof(world, case, {"product_name": "Nồi inox 24cm", "material": "Inox 304"})

    response = await _send(world.container(ORDERING), "GET", f"/po-cases/{case.id}/packaging-proof")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "extracted"
    codes = {(f["code"], f["subject"]) for f in body["findings"]}
    assert ("label_missing", "origin") in codes
    assert ("label_missing", "warnings") in codes
    assert ("bm04_missing", "product_profile_bm04") in codes

    other = _case()
    other.workspace_id = WorkspaceId(uuid.uuid4())
    world.po_cases.cases.append(other)
    missing = await _send(world.container(ORDERING), "GET", f"/po-cases/{other.id}/packaging-proof")
    assert missing.status_code == 404
