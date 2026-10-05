"""The product-case routes, through the real app wiring (stage-1 ticket 01).

The case records, the document records and the bucket are faked; auth, the
access context, the request models, the idempotency dependency, the routes
and the handlers run for real. The container carries only the product-case
and document handlers, which is itself one assertion: the product router
mounts on its own guard.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from asgi_lifespan import LifespanManager

from dw_api.bootstrap import ApiContainer
from dw_api.health import CheckState, HealthService
from dw_api.main import create_app
from dw_api.settings import ApiSettings
from dw_kernel.errors import ConflictError, NotFoundError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.pagination import Page, PageRequest
from dw_kernel.ports import FixedClock, SystemClock, Uuid4Generator
from dw_platform.adapters.identity.dev_token import DevTokenVerifier
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.entitlement import DEFAULT_PLANS, PlanEntitlementService
from dw_platform.application.idempotency import (
    HttpIdempotency,
    RequestFingerprint,
    ReservedKey,
    StoredResponse,
)
from dw_platform.application.identity import DbAccessContextFactory, MembershipAccess
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.action_duties import CaseDuty
from dw_supply_chain.application.case_documents import (
    DownloadCaseDocument,
    ListCaseDocuments,
    UploadCaseDocument,
)
from dw_supply_chain.application.handlers import (
    ACTION_DUTIES_READ,
    DOCUMENT_READ,
    DOCUMENT_WRITE,
    PRODUCT_CASE_READ,
    PRODUCT_CASE_WRITE,
    GetProductActionDuties,
    SetProductActionDutiesOverride,
    duty_scope,
)
from dw_supply_chain.application.ports import NewCaseDocument, ProductCaseListFilter
from dw_supply_chain.application.product_cases import (
    AdvanceProductCase,
    GetProductCase,
    ListProductCases,
    ListProductCaseTransitions,
    ProposeProductCase,
)
from dw_supply_chain.domain.case_document import (
    CaseDocument,
    CaseDocumentId,
    CaseKind,
    DocumentType,
)
from dw_supply_chain.domain.product_development_case import (
    ProductCaseTransition,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    SampleRound,
)
from dw_supply_chain.presentation.product_case_routes import ProposeProductCaseRequest
from dw_supply_chain.product_action_duties import (
    PRODUCT_ACTION_DUTIES_POLICY_ID,
    SupplyChainProductActionDuties,
)

pytestmark = pytest.mark.unit

SECRET = "unit-test-secret-0123456789abcdef"
TENANT, WORKSPACE, OTHER_TENANT, OTHER_WORKSPACE = (uuid.uuid4() for _ in range(4))
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
PDF = b"%PDF-1.7\n" + b"x" * 64
BASE = "/api/v1/supply-chain"

_READS = {PRODUCT_CASE_READ, DOCUMENT_READ, ACTION_DUTIES_READ}
# As the role catalogue grants them (7c422b849fe9): the operator opens product
# cases as it opens PO cases; R&D tests samples and holds no other duty.
SC_OPERATOR = frozenset(
    {
        *_READS,
        DOCUMENT_WRITE,
        PRODUCT_CASE_WRITE,
        duty_scope(CaseDuty.ORDERING),
        duty_scope(CaseDuty.EXCEPTIONS),
    }
)
SC_RND = frozenset({*_READS, DOCUMENT_WRITE, duty_scope(CaseDuty.RND)})

DUTIES = SupplyChainProductActionDuties.model_validate(
    {
        "schema_version": "1.0",
        "policy_id": PRODUCT_ACTION_DUTIES_POLICY_ID,
        "policy_version": "1.0.0",
        "action_duties": {
            "propose": "ordering",
            "request_sample": "ordering",
            "receive_sample": "rnd",
            "pass_sample": "rnd",
            "request_revision": "rnd",
            "receive_revised_sample": "rnd",
            "reject_sample": "rnd",
            "wait_for_external": "exceptions",
            "flag_blocked": "exceptions",
            "flag_manual_review": "exceptions",
            "resume": "exceptions",
            "cancel": "ordering",
        },
    }
)


class FakeMembershipLookup:
    def __init__(self, scopes: frozenset[str], principal: uuid.UUID) -> None:
        self._scopes, self._principal = scopes, principal

    async def find_access(
        self, subject: str, issuer: str, tenant_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> MembershipAccess | None:
        if tenant_id != TENANT or workspace_id != WORKSPACE:
            return None
        return MembershipAccess(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            principal_id=self._principal,
            roles=frozenset({"member"}),
            scopes=self._scopes,
            groups=frozenset(),
            clearance="internal",
            plan_id="professional",
            feature_flags=frozenset(),
        )


@dataclass
class FakeCases:
    """Narrows by tenant AND workspace as the table's RLS does; one proposal
    code per tenant; optimistic on version. Writes each pending step's history
    row and the round it opens or closes (once), and reads the current round's
    opening time back, as `SqlProductCaseRepository` does."""

    rows: dict[uuid.UUID, ProductDevelopmentCase] = field(default_factory=dict)
    history: dict[uuid.UUID, list[ProductCaseTransition]] = field(default_factory=dict)
    rounds: dict[uuid.UUID, list[SampleRound]] = field(default_factory=dict)

    def _visible(self, context: AccessContext, case: ProductDevelopmentCase) -> bool:
        return (case.tenant_id.value, case.workspace_id.value) == (
            context.tenant_id,
            context.workspace_id,
        )

    def put(self, case: ProductDevelopmentCase) -> ProductDevelopmentCase:
        rounds = self.rounds.setdefault(case.id.value, [])
        for step in case.pop_pending_steps():
            self.history.setdefault(case.id.value, []).append(
                ProductCaseTransition(
                    action=step.action,
                    from_state=step.from_state,
                    to_state=step.to_state,
                    reason=step.reason,
                    actor_id=step.actor_id,
                    occurred_at=NOW,
                )
            )
            if step.opens_round is not None:
                rounds.append(
                    SampleRound(
                        round_no=step.opens_round,
                        opened_at=NOW,
                        opened_by=step.actor_id,
                        result=None,
                        evaluation_document_id=None,
                        closed_at=None,
                        closed_by=None,
                        revision_document_id=None,
                        requested_changes=None,
                    )
                )
            closure = step.closes_round
            if closure is not None:
                at = next(
                    (
                        i
                        for i, r in enumerate(rounds)
                        if r.round_no == closure.round_no and r.result is None
                    ),
                    None,
                )
                if at is None:
                    raise ConflictError("this sample round is already closed")
                rounds[at] = replace(
                    rounds[at],
                    result=closure.result,
                    evaluation_document_id=closure.evaluation_document_id,
                    closed_at=NOW,
                    closed_by=step.actor_id,
                    revision_document_id=closure.revision_document_id,
                    requested_changes=step.reason if closure.revision_document_id else None,
                )
        case.created_at = case.created_at or NOW
        current = next((r for r in rounds if r.round_no == case.sample_round), None)
        case.round_opened_at = current.opened_at if current else None
        self.rows[case.id.value] = case
        return case

    async def add(
        self, context: AccessContext, case: ProductDevelopmentCase, *, audit: AuditEvent
    ) -> None:
        if any(
            c.tenant_id == case.tenant_id and c.proposal_code == case.proposal_code
            for c in self.rows.values()
        ):
            raise ConflictError(
                "mã đề xuất này đã có trong tenant",
                details={"constraint": "uq_product_dev_cases_tenant_id_proposal_code"},
            )
        self.put(replace(case))

    async def get(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> ProductDevelopmentCase | None:
        case = self.rows.get(case_id.value)
        return replace(case) if case is not None and self._visible(context, case) else None

    async def case_workspace(self, context: AccessContext, case_id: uuid.UUID) -> uuid.UUID | None:
        case = self.rows.get(case_id)
        return case.workspace_id.value if case and self._visible(context, case) else None

    async def save(
        self, context: AccessContext, case: ProductDevelopmentCase, *, audit: AuditEvent
    ) -> None:
        stored = self.rows.get(case.id.value)
        if stored is None or not self._visible(context, stored):
            raise ConflictError("product case was modified concurrently")
        if stored.version != case.version - 1:
            raise ConflictError("product case was modified concurrently")
        self.put(replace(case))

    async def list_page(
        self, context: AccessContext, request: PageRequest, case_filter: ProductCaseListFilter
    ) -> Page[ProductDevelopmentCase]:
        items = [
            c
            for c in self.rows.values()
            if self._visible(context, c)
            and (case_filter.pic_user_id is None or c.pic_user_id == case_filter.pic_user_id)
            and (case_filter.state is None or c.state is case_filter.state)
        ]
        return Page(items=tuple(items), next_cursor=None)

    def _readable(self, context: AccessContext, case_id: ProductDevelopmentCaseId) -> bool:
        case = self.rows.get(case_id.value)
        return case is not None and self._visible(context, case)

    async def list_transitions(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> list[ProductCaseTransition]:
        if not self._readable(context, case_id):
            return []
        return list(self.history.get(case_id.value, []))

    async def list_rounds(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> list[SampleRound]:
        if not self._readable(context, case_id):
            return []
        return list(self.rounds.get(case_id.value, []))


@dataclass
class FakeDocuments:
    rows: list[CaseDocument] = field(default_factory=list)

    def _visible(self, context: AccessContext, row: CaseDocument) -> bool:
        return (row.tenant_id, row.workspace_id) == (context.tenant_id, context.workspace_id)

    async def add(
        self, context: AccessContext, document: NewCaseDocument, *, audit: AuditEvent
    ) -> CaseDocument:
        row = CaseDocument(
            id=document.id,
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            case_kind=document.case_kind,
            case_id=document.case_id,
            doc_type=document.doc_type,
            object_key=document.object_key,
            filename=document.filename,
            content_type=document.content_type,
            size_bytes=document.size_bytes,
            sha256=document.sha256,
            version=1,
            uploaded_by=context.principal_id,
            uploaded_at=NOW + timedelta(hours=1),
        )
        self.rows.append(row)
        return row

    async def list_for_case(
        self, context: AccessContext, case_kind: CaseKind, case_id: uuid.UUID
    ) -> list[CaseDocument]:
        return [
            r
            for r in self.rows
            if self._visible(context, r) and r.case_kind is case_kind and r.case_id == case_id
        ]

    async def get(self, context: AccessContext, document_id: CaseDocumentId) -> CaseDocument | None:
        return next(
            (r for r in self.rows if r.id == document_id and self._visible(context, r)), None
        )


@dataclass
class FakeBucket:
    objects: dict[str, bytes] = field(default_factory=dict)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = data

    async def get(self, key: str) -> bytes:
        if key not in self.objects:
            raise NotFoundError("object not found")
        return self.objects[key]

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)


@dataclass
class FakePolicies:
    stored: dict[str, Mapping[str, object]] = field(default_factory=dict)

    async def get(self, context: AccessContext, policy_id: str) -> dict[str, object] | None:
        found = self.stored.get(policy_id)
        return dict(found) if found is not None else None

    async def put(
        self,
        context: AccessContext,
        policy_id: str,
        content: Mapping[str, object],
        *,
        audit: AuditEvent,
    ) -> None:
        self.stored[policy_id] = content


class MemoryStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[uuid.UUID, str], ReservedKey] = {}

    async def reserve(
        self, context: AccessContext, *, key: str, fingerprint: RequestFingerprint
    ) -> ReservedKey | None:
        existing = self.rows.get((context.tenant_id, key))
        if existing is not None:
            return existing
        self.rows[(context.tenant_id, key)] = ReservedKey(fingerprint=fingerprint, response=None)
        return None

    async def take_over(self, context: AccessContext, *, key: str, older_than: object) -> bool:
        return False

    async def complete(self, context: AccessContext, *, key: str, response: StoredResponse) -> None:
        row = self.rows[(context.tenant_id, key)]
        self.rows[(context.tenant_id, key)] = ReservedKey(
            fingerprint=row.fingerprint, response=response
        )

    async def release(self, context: AccessContext, *, key: str) -> None:
        row = self.rows.get((context.tenant_id, key))
        if row is not None and row.response is None:
            del self.rows[(context.tenant_id, key)]


@dataclass
class World:
    cases: FakeCases = field(default_factory=FakeCases)
    documents: FakeDocuments = field(default_factory=FakeDocuments)
    bucket: FakeBucket = field(default_factory=FakeBucket)
    policies: FakePolicies = field(default_factory=FakePolicies)
    store: MemoryStore = field(default_factory=MemoryStore)
    principal: uuid.UUID = field(default_factory=uuid.uuid4)

    def container(self, scopes: frozenset[str], *, mount: bool = True) -> ApiContainer:
        async def ok_probe() -> CheckState:
            return "ok"

        authz = ScopeAuthorizationService()
        container = ApiContainer(
            settings=ApiSettings(profile="test", dev_secret=SECRET),
            engine=None,
            health_service=HealthService(probes={"database": ok_probe}),
            token_verifier=DevTokenVerifier(SECRET),
            access_context_factory=DbAccessContextFactory(
                FakeMembershipLookup(scopes, self.principal)
            ),
            identity_bootstrap=None,
            uow_factory=None,
            authorization=authz,
            entitlement=PlanEntitlementService(DEFAULT_PLANS),
            idempotency=HttpIdempotency(store=self.store, clock=SystemClock()),
        )
        lookups = {CaseKind.PRODUCT: self.cases}
        container.supply_chain_upload_case_document = UploadCaseDocument(
            cases=lookups,
            documents=self.documents,
            storage=self.bucket,
            authz=authz,
            ids=Uuid4Generator(),
            clock=FixedClock(NOW),
            max_bytes=4096,
        )
        container.supply_chain_list_case_documents = ListCaseDocuments(
            cases=lookups, documents=self.documents, authz=authz
        )
        container.supply_chain_download_case_document = DownloadCaseDocument(
            documents=self.documents, storage=self.bucket, authz=authz
        )
        if mount:
            common = {
                "authz": authz,
                "policy_override_repo": self.policies,
                "platform_default_duties": DUTIES,
                "ids": Uuid4Generator(),
                "clock": FixedClock(NOW),
            }
            container.supply_chain_propose_product_case = ProposeProductCase(
                repo=self.cases, **common
            )
            container.supply_chain_advance_product_case = AdvanceProductCase(
                repo=self.cases, documents=self.documents, **common
            )
            container.supply_chain_get_product_case = GetProductCase(
                repo=self.cases,
                authz=authz,
                policy_override_repo=self.policies,
                platform_default_duties=DUTIES,
            )
            container.supply_chain_list_product_cases = ListProductCases(
                repo=self.cases, authz=authz
            )
            container.supply_chain_list_product_case_transitions = ListProductCaseTransitions(
                repo=self.cases, authz=authz
            )
            container.supply_chain_get_product_action_duties = GetProductActionDuties(
                policy_override_repo=self.policies, platform_default_duties=DUTIES, authz=authz
            )
            container.supply_chain_set_product_action_duties_override = (
                SetProductActionDutiesOverride(
                    policy_override_repo=self.policies,
                    authz=authz,
                    ids=Uuid4Generator(),
                    clock=FixedClock(NOW),
                )
            )
        return container

    def case(
        self,
        *,
        tenant: uuid.UUID = TENANT,
        workspace: uuid.UUID = WORKSPACE,
        testing: bool = False,
    ) -> ProductDevelopmentCase:
        case = ProductDevelopmentCase.propose(
            id=ProductDevelopmentCaseId(uuid.uuid4()),
            tenant_id=TenantId(tenant),
            workspace_id=WorkspaceId(workspace),
            proposal_code=f"DX-{uuid.uuid4().hex[:6]}",
            product_name="Nồi 24cm",
            category="Nồi",
            actor_id=uuid.uuid4(),
        )
        if testing:
            case.request_sample(actor_id=uuid.uuid4(), supplier_name="NCC")
            case.receive_sample(actor_id=uuid.uuid4())
        return self.cases.put(case)

    def document(self, case: ProductDevelopmentCase, doc_type: DocumentType) -> CaseDocument:
        document_id = uuid.uuid4()
        row = CaseDocument(
            id=CaseDocumentId(document_id),
            tenant_id=case.tenant_id.value,
            workspace_id=case.workspace_id.value,
            case_kind=CaseKind.PRODUCT,
            case_id=case.id.value,
            doc_type=doc_type,
            object_key=f"k/{document_id}",
            filename="bien-ban.pdf",
            content_type="application/pdf",
            size_bytes=10,
            sha256="0" * 64,
            version=1,
            uploaded_by=uuid.uuid4(),
            uploaded_at=NOW + timedelta(hours=1),
        )
        self.documents.rows.append(row)
        return row


def _headers(key: str | None = None) -> dict[str, str]:
    token = DevTokenVerifier(SECRET).issue("dev|member", email="member@fpt.com")
    sent = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": str(TENANT),
        "X-Workspace-Id": str(WORKSPACE),
    }
    if key is not None:
        sent["Idempotency-Key"] = key
    return sent


async def _send(
    container: ApiContainer, requests: list[tuple[str, str, dict[str, object]]]
) -> list[httpx.Response]:
    app = create_app(container)
    responses = []
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for method, path, kw in requests:
                responses.append(await client.request(method, path, **kw))  # type: ignore[arg-type]
    return responses


def _post(path: str, body: dict[str, object]) -> tuple[str, str, dict[str, object]]:
    return ("POST", f"{BASE}{path}", {"json": body, "headers": _headers(str(uuid.uuid4()))})


def _get(path: str) -> tuple[str, str, dict[str, object]]:
    return ("GET", f"{BASE}{path}", {"headers": _headers()})


def _upload(case_id: uuid.UUID) -> tuple[str, str, dict[str, object]]:
    return (
        "POST",
        f"{BASE}/product-cases/{case_id}/documents",
        {
            "files": {"file": ("anh.pdf", PDF, "application/pdf")},
            "data": {"doc_type": "product_image"},
            "headers": _headers(str(uuid.uuid4())),
        },
    )


_PROPOSAL = {"proposal_code": "DX-2026-001", "product_name": "Nồi 24cm", "category": "Nồi"}


# --- propose and the PIC ------------------------------------------------------------


async def test_propose_is_200_and_the_caller_is_the_pic() -> None:
    world = World()
    (response,) = await _send(world.container(SC_OPERATOR), [_post("/product-cases", _PROPOSAL)])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pic_user_id"] == str(world.principal)
    assert body["created_by"] == str(world.principal)
    assert body["state"] == "proposed"
    assert "pic_user_id" not in ProposeProductCaseRequest.model_fields


async def test_the_detail_offers_each_step_with_what_it_takes_and_its_scope() -> None:
    """What the page draws its buttons and forms from: the steps the state
    accepts, each with the scope of its duty under the tenant's policy."""
    world = World()
    case = world.case(testing=True)
    override = DUTIES.model_dump(mode="json")
    override["action_duties"]["reject_sample"] = "qc"
    world.policies.stored[PRODUCT_ACTION_DUTIES_POLICY_ID] = override

    (response,) = await _send(world.container(SC_RND), [_get(f"/product-cases/{case.id}")])

    assert response.status_code == 200, response.text
    options = {o["action"]: o for o in response.json()["actions"]}
    assert list(options) == [
        "pass_sample",
        "request_revision",
        "reject_sample",
        "wait_for_external",
        "flag_blocked",
        "flag_manual_review",
        "cancel",
    ]
    assert options["pass_sample"] | {"action": None} == {
        "action": None,
        "required_scope": "supply_chain.duty.rnd",
        "reason_required": False,
        "takes_supplier": False,
        "document_type": "sample_evaluation",
        "document_required": True,
    }
    assert options["request_revision"]["reason_required"] is True
    assert options["request_revision"]["document_type"] == "sample_revision_request"
    assert options["reject_sample"]["document_required"] is False
    assert options["reject_sample"]["required_scope"] == "supply_chain.duty.qc"
    assert options["cancel"]["required_scope"] == "supply_chain.duty.ordering"


@pytest.mark.parametrize("extra", [{"pic_user_id": str(uuid.uuid4())}, {"pic": "someone"}])
async def test_a_body_naming_a_pic_is_422(extra: dict[str, str]) -> None:
    world = World()
    (response,) = await _send(
        world.container(SC_OPERATOR), [_post("/product-cases", _PROPOSAL | extra)]
    )
    assert response.status_code == 422
    assert world.cases.rows == {}


async def test_rnd_cannot_propose_403() -> None:
    world = World()
    (response,) = await _send(world.container(SC_RND), [_post("/product-cases", _PROPOSAL)])
    assert response.status_code == 403
    assert world.cases.rows == {}


async def test_proposing_with_the_duty_and_without_the_write_is_403() -> None:
    """Lead decision 9: the ordering duty alone does not open a case; the
    route refuses a caller without `supply_chain.product_case.write`."""
    world = World()
    (response,) = await _send(
        world.container(SC_OPERATOR - {PRODUCT_CASE_WRITE}), [_post("/product-cases", _PROPOSAL)]
    )
    assert response.status_code == 403
    assert world.cases.rows == {}


async def test_a_duplicate_proposal_code_is_409_by_its_constraint() -> None:
    world = World()
    first, second = await _send(
        world.container(SC_OPERATOR),
        [_post("/product-cases", _PROPOSAL), _post("/product-cases", _PROPOSAL)],
    )
    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["details"]["constraint"] == "uq_product_dev_cases_tenant_id_proposal_code"


# --- duties and paper ------------------------------------------------------------------


async def test_the_operator_cannot_pass_a_sample_403() -> None:
    world = World()
    case = world.case(testing=True)
    evaluation = world.document(case, DocumentType.SAMPLE_EVALUATION)
    (response,) = await _send(
        world.container(SC_OPERATOR),
        [
            _post(
                f"/product-cases/{case.id}/transitions",
                {"action": "pass_sample", "document_id": str(evaluation.id)},
            )
        ],
    )
    assert response.status_code == 403


async def test_pass_without_an_evaluation_or_on_another_cases_is_409_naming_the_type() -> None:
    world = World()
    case = world.case(testing=True)
    other = world.case()
    foreign = world.document(other, DocumentType.SAMPLE_EVALUATION)

    missing, borrowed = await _send(
        world.container(SC_RND),
        [
            _post(f"/product-cases/{case.id}/transitions", {"action": "pass_sample"}),
            _post(
                f"/product-cases/{case.id}/transitions",
                {"action": "pass_sample", "document_id": str(foreign.id)},
            ),
        ],
    )
    for response in (missing, borrowed):
        assert response.status_code == 409, response.text
        assert response.json()["details"]["missing_document_type"] == "sample_evaluation"
    assert world.cases.rows[case.id.value].state.value == "sample_testing"


async def test_rnd_passes_a_sample_on_this_rounds_evaluation() -> None:
    world = World()
    case = world.case(testing=True)
    evaluation = world.document(case, DocumentType.SAMPLE_EVALUATION)
    (response,) = await _send(
        world.container(SC_RND),
        [
            _post(
                f"/product-cases/{case.id}/transitions",
                {"action": "pass_sample", "document_id": str(evaluation.id)},
            )
        ],
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "pending_bod_review"


async def test_an_unknown_field_on_a_step_is_422() -> None:
    world = World()
    case = world.case()
    (response,) = await _send(
        world.container(SC_OPERATOR),
        [
            _post(
                f"/product-cases/{case.id}/transitions",
                {"action": "request_sample", "supplier_name": "NCC", "pic_user_id": "x"},
            )
        ],
    )
    assert response.status_code == 422


# --- another tenant's or workspace's case -------------------------------------------


@pytest.mark.parametrize("owner", [(OTHER_TENANT, WORKSPACE), (TENANT, OTHER_WORKSPACE)])
async def test_another_tenants_or_workspaces_case_is_404_on_every_route(
    owner: tuple[uuid.UUID, uuid.UUID],
) -> None:
    world = World()
    case = world.case(tenant=owner[0], workspace=owner[1])
    everything = SC_OPERATOR | SC_RND

    responses = await _send(
        world.container(everything),
        [
            _get(f"/product-cases/{case.id}"),
            _get(f"/product-cases/{case.id}/transitions"),
            _post(
                f"/product-cases/{case.id}/transitions",
                {"action": "request_sample", "supplier_name": "NCC"},
            ),
            _get(f"/product-cases/{case.id}/documents"),
            _upload(case.id.value),
        ],
    )
    assert [r.status_code for r in responses] == [404] * 5, [r.text for r in responses]
    assert world.bucket.objects == {} and world.documents.rows == []
    assert world.cases.rows[case.id.value].state.value == "proposed"


# --- documents of a product case --------------------------------------------------------


async def test_a_product_image_lands_under_the_product_prefix() -> None:
    world = World()
    case = world.case()
    upload, listed = await _send(
        world.container(SC_OPERATOR),
        [_upload(case.id.value), _get(f"/product-cases/{case.id}/documents")],
    )
    assert upload.status_code == 201, upload.text
    body = upload.json()
    assert (body["case_kind"], body["case_id"]) == ("product", str(case.id))
    (key,) = world.bucket.objects
    assert key == f"supply_chain/{TENANT}/{WORKSPACE}/product/{case.id}/{body['id']}"
    assert [d["id"] for d in listed.json()] == [body["id"]]


# --- listing, duties, mounting ---------------------------------------------------------------


async def test_the_list_filters_by_pic() -> None:
    world = World()
    mine = world.case()
    world.cases.rows[mine.id.value].pic_user_id = world.principal
    world.case()

    everyone, narrowed = await _send(
        world.container(frozenset({PRODUCT_CASE_READ})),
        [_get("/product-cases"), _get(f"/product-cases?pic_user_id={world.principal}")],
    )
    assert len(everyone.json()["items"]) == 2
    assert [c["id"] for c in narrowed.json()["items"]] == [str(mine.id)]


async def test_the_product_duties_read_and_a_po_shaped_override_is_refused() -> None:
    world = World()
    got, put = await _send(
        world.container(SC_OPERATOR | {"supply_chain.action_duties.write"}),
        [
            _get("/product-action-duties"),
            (
                "PUT",
                f"{BASE}/product-action-duties",
                {
                    "json": {
                        "schema_version": "1.0",
                        "policy_id": PRODUCT_ACTION_DUTIES_POLICY_ID,
                        "policy_version": "1.0.1",
                        "action_duties": {"request_deposit": "ordering"},
                    },
                    "headers": _headers(),
                },
            ),
        ],
    )
    assert got.status_code == 200
    assert got.json()["action_duties"]["pass_sample"] == "rnd"
    assert put.status_code == 422
    assert world.policies.stored == {}


async def test_every_read_refuses_a_caller_holding_only_duties_403() -> None:
    """Same tenant, same workspace, every step duty and the document write,
    no read scope: each read route answers 403 (the handler's own check)."""
    world = World()
    case = world.case()
    duties_only = frozenset(
        {DOCUMENT_WRITE, *(duty_scope(d) for d in (CaseDuty.ORDERING, CaseDuty.RND))}
    )
    responses = await _send(
        world.container(duties_only),
        [
            _get("/product-cases"),
            _get(f"/product-cases/{case.id}"),
            _get(f"/product-cases/{case.id}/transitions"),
            _get("/product-action-duties"),
        ],
    )
    assert [r.status_code for r in responses] == [403] * 4, [r.text for r in responses]


async def test_setting_the_product_duties_without_the_write_is_403() -> None:
    """A valid mapping, from a caller who may read the duties and run every
    step but not set who takes them: refused, and nothing stored."""
    world = World()
    (put,) = await _send(
        world.container(SC_OPERATOR | SC_RND),
        [
            (
                "PUT",
                f"{BASE}/product-action-duties",
                {"json": DUTIES.model_dump(mode="json"), "headers": _headers()},
            )
        ],
    )
    assert put.status_code == 403, put.text
    assert world.policies.stored == {}


async def test_the_history_and_rounds_are_the_cases_own() -> None:
    """The history and the rounds a visible case answers with: each step
    with its action, states, reason and actor; a round opened and then
    closed on this round's evaluation."""
    world = World()
    case = world.case(testing=True)
    evaluation = world.document(case, DocumentType.SAMPLE_EVALUATION)
    world.case(testing=True)  # another case's history must not appear

    before, step, after, history = await _send(
        world.container(SC_RND),
        [
            _get(f"/product-cases/{case.id}"),
            _post(
                f"/product-cases/{case.id}/transitions",
                {"action": "pass_sample", "document_id": str(evaluation.id)},
            ),
            _get(f"/product-cases/{case.id}"),
            _get(f"/product-cases/{case.id}/transitions"),
        ],
    )
    assert step.status_code == 200, step.text
    (opened,) = before.json()["rounds"]
    assert (opened["round_no"], opened["result"], opened["closed_by"]) == (1, None, None)
    (closed,) = after.json()["rounds"]
    assert (closed["result"], closed["evaluation_document_id"], closed["closed_by"]) == (
        "passed",
        str(evaluation.id),
        str(world.principal),
    )
    body = history.json()
    assert [(t["action"], t["from_state"], t["to_state"]) for t in body] == [
        ("propose", None, "proposed"),
        ("request_sample", "proposed", "sample_requested"),
        ("receive_sample", "sample_requested", "sample_testing"),
        ("pass_sample", "sample_testing", "pending_bod_review"),
    ]
    assert body[-1]["actor_id"] == str(world.principal)
    assert body[-1]["reason"] is None


async def test_a_reason_on_a_step_that_takes_none_is_refused_not_dropped() -> None:
    world = World()
    case = world.case(testing=True)
    evaluation = world.document(case, DocumentType.SAMPLE_EVALUATION)
    (response,) = await _send(
        world.container(SC_RND),
        [
            _post(
                f"/product-cases/{case.id}/transitions",
                {"action": "pass_sample", "document_id": str(evaluation.id), "reason": "ok"},
            )
        ],
    )
    assert response.status_code == 422, response.text
    assert world.cases.rows[case.id.value].state.value == "sample_testing"


async def test_the_product_router_mounts_on_its_own_guard() -> None:
    world = World()
    (unmounted,) = await _send(world.container(SC_OPERATOR, mount=False), [_get("/product-cases")])
    assert unmounted.status_code == 404
