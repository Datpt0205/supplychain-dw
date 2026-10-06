"""Unit: ProposeProductCase / GetProductCase / ListProductCases /
ListProductCaseTransitions / AdvanceProductCase (stage-1 tickets 01 and 02).

Fakes stand in for the case records, the document records and the policy
store; each honours its port the way the database does (tenant AND workspace
narrowing, one proposal code per tenant, optimistic version, a round closed
once). Under test are the handlers' own decisions: who may take a step, which
case, which paper, and that the PIC is the proposer and nobody else.
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from dw_kernel.errors import (
    ConflictError,
    DomainError,
    NotFoundError,
    PermissionDeniedError,
    QuotaExceededError,
)
from dw_kernel.pagination import Page, PageRequest
from dw_kernel.ports import FixedClock, Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.action_duties import CaseDuty
from dw_supply_chain.application.handlers import (
    ACTION_DUTIES_READ,
    PRODUCT_CASE_READ,
    PRODUCT_CASE_WRITE,
    GetProductActionDuties,
    SetProductActionDutiesOverride,
    duty_scope,
)
from dw_supply_chain.application.ports import (
    PendingApprovalRecord,
    ProductCaseListFilter,
    ReviewRaise,
    ReviewRequester,
)
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
    GRAPH_ONLY_ACTIONS,
    ProductAction,
    ProductCaseStep,
    ProductCaseTransition,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
    SampleRound,
)
from dw_supply_chain.domain.product_proposal import DraftClaim
from dw_supply_chain.product_action_duties import (
    PRODUCT_ACTION_DUTIES_POLICY_ID,
    SupplyChainProductActionDuties,
)

pytestmark = pytest.mark.unit

TENANT, WORKSPACE, OTHER_TENANT, OTHER_WORKSPACE = (uuid.uuid4() for _ in range(4))
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)

ORDERING = duty_scope(CaseDuty.ORDERING)
RND = duty_scope(CaseDuty.RND)
EXCEPTIONS = duty_scope(CaseDuty.EXCEPTIONS)
# The scopes the role catalogue grants (migration 7c422b849fe9): sc_operator
# opens product cases (`product_case.write`, as it opens PO cases), orders and
# handles exceptions; sc_rnd tests samples and nothing else (lead decision 8).
SC_OPERATOR = frozenset({PRODUCT_CASE_READ, PRODUCT_CASE_WRITE, ORDERING, EXCEPTIONS})
SC_RND = frozenset({PRODUCT_CASE_READ, RND})

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


def _context(
    scopes: frozenset[str],
    *,
    tenant: uuid.UUID = TENANT,
    workspace: uuid.UUID = WORKSPACE,
    principal: uuid.UUID | None = None,
) -> AccessContext:
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=principal or uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=scopes,
        plan_id="professional",
    )


@dataclass
class FakeCases:
    """Narrows by tenant AND workspace as the table's RLS does; one proposal
    code per tenant; `save` optimistic on version; a round closes once."""

    rows: dict[uuid.UUID, ProductDevelopmentCase] = field(default_factory=dict)
    steps: list[ProductCaseStep] = field(default_factory=list)
    audits: list[AuditEvent] = field(default_factory=list)
    open_rounds: dict[uuid.UUID, int] = field(default_factory=dict)

    def _visible(self, context: AccessContext, case: ProductDevelopmentCase) -> bool:
        return (case.tenant_id.value, case.workspace_id.value) == (
            context.tenant_id,
            context.workspace_id,
        )

    def _drain(self, case: ProductDevelopmentCase) -> None:
        for step in case.pop_pending_steps():
            if step.closes_round is not None:
                if self.open_rounds.get(case.id.value) != step.closes_round.round_no:
                    raise ConflictError("this sample round is already closed")
                del self.open_rounds[case.id.value]
            if step.opens_round is not None:
                self.open_rounds[case.id.value] = step.opens_round
            self.steps.append(step)

    async def add(
        self,
        context: AccessContext,
        case: ProductDevelopmentCase,
        *,
        audit: AuditEvent,
        consume: DraftClaim | None = None,
    ) -> None:
        if consume is not None:
            raise NotImplementedError("not exercised here: test_zalo_proposal.py holds drafts")
        if any(
            c.tenant_id == case.tenant_id and c.proposal_code == case.proposal_code
            for c in self.rows.values()
        ):
            raise ConflictError("mã đề xuất này đã có trong tenant")
        case.created_at = NOW + timedelta(seconds=len(self.rows))
        self._drain(case)
        self.audits.append(audit)
        self.rows[case.id.value] = replace(case, _pending_steps=[])

    async def get(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> ProductDevelopmentCase | None:
        case = self.rows.get(case_id.value)
        if case is None or not self._visible(context, case):
            return None
        return replace(case, _pending_steps=[])

    async def save(
        self, context: AccessContext, case: ProductDevelopmentCase, *, audit: AuditEvent
    ) -> None:
        stored = self.rows.get(case.id.value)
        if stored is None or not self._visible(context, stored):
            raise ConflictError("product case was modified concurrently")
        if stored.version != case.version - 1:
            raise ConflictError("product case was modified concurrently")
        self._drain(case)
        self.audits.append(audit)
        # A round opened by this save is read back with its opening time.
        opened = case.round_opened_at or (NOW if case.sample_round else None)
        self.rows[case.id.value] = replace(case, _pending_steps=[], round_opened_at=opened)

    async def list_page(
        self, context: AccessContext, request: PageRequest, case_filter: ProductCaseListFilter
    ) -> Page[ProductDevelopmentCase]:
        items = [
            c
            for c in self.rows.values()
            if self._visible(context, c)
            and (case_filter.state is None or c.state is case_filter.state)
            and (case_filter.pic_user_id is None or c.pic_user_id == case_filter.pic_user_id)
        ]
        items.sort(key=lambda c: c.created_at or NOW, reverse=True)
        return Page(items=tuple(items[: request.limit]), next_cursor=None)

    async def list_transitions(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> list[ProductCaseTransition]:
        raise NotImplementedError("not exercised by these handler tests beyond the 404 path")

    async def list_rounds(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> list[SampleRound]:
        return []


@dataclass
class FakeDocuments:
    rows: dict[uuid.UUID, CaseDocument] = field(default_factory=dict)

    async def get(self, context: AccessContext, document_id: CaseDocumentId) -> CaseDocument | None:
        row = self.rows.get(document_id.value)
        if row is None or (row.tenant_id, row.workspace_id) != (
            context.tenant_id,
            context.workspace_id,
        ):
            return None
        return row

    def add(
        self,
        case: ProductDevelopmentCase,
        doc_type: DocumentType,
        *,
        uploaded_at: datetime | None = None,
        tenant: uuid.UUID | None = None,
    ) -> CaseDocument:
        document_id = uuid.uuid4()
        row = CaseDocument(
            id=CaseDocumentId(document_id),
            tenant_id=tenant or case.tenant_id.value,
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
            uploaded_at=uploaded_at or NOW + timedelta(hours=1),
        )
        self.rows[document_id] = row
        return row


@dataclass
class FakePolicies:
    """`PolicyOverridePort`: the tenant's stored document per policy id."""

    stored: dict[tuple[uuid.UUID, str], Mapping[str, object]] = field(default_factory=dict)

    async def get(self, context: AccessContext, policy_id: str) -> dict[str, object] | None:
        found = self.stored.get((context.tenant_id, policy_id))
        return dict(found) if found is not None else None

    async def put(
        self,
        context: AccessContext,
        policy_id: str,
        content: Mapping[str, object],
        *,
        audit: AuditEvent,
    ) -> None:
        self.stored[(context.tenant_id, policy_id)] = content


@dataclass
class FakeReviews:
    """`BodReviewPort`: records each request for BGĐ's review and answers
    with `outcome`, or raises `error` as a refused start would."""

    outcome: ReviewRaise = ReviewRaise.RAISED
    error: Exception | None = None
    asked: list[tuple[AccessContext, ProductDevelopmentCase, ReviewRequester]] = field(
        default_factory=list
    )

    async def ensure(
        self, context: AccessContext, case: ProductDevelopmentCase, requester: ReviewRequester
    ) -> ReviewRaise:
        self.asked.append((context, case, requester))
        if self.error is not None:
            raise self.error
        return self.outcome


@dataclass(frozen=True)
class PendingReview:
    id: uuid.UUID
    approval_type: str
    payload: Mapping[str, object]
    created_at: datetime | None
    required_scope: str | None


@dataclass
class FakeApprovals:
    """`PendingApprovalsPort`, narrowed to the caller's workspace as the
    inbox is: holds pending reviews keyed by (workspace, type, case id)."""

    pending: dict[tuple[uuid.UUID, str, str], PendingReview] = field(default_factory=dict)

    async def list_pending_by_type_prefix(
        self, context: AccessContext, *, prefix: str, limit: int
    ) -> tuple[int, Sequence[PendingApprovalRecord]]:
        raise NotImplementedError("not exercised by the product-case handlers")

    async def pending_by_payload(
        self, context: AccessContext, *, approval_type: str, key: str, value: str
    ) -> PendingApprovalRecord | None:
        assert key == "product_dev_case_id"
        return self.pending.get((context.workspace_id, approval_type, value))


@dataclass
class Stack:
    cases: FakeCases = field(default_factory=FakeCases)
    documents: FakeDocuments = field(default_factory=FakeDocuments)
    policies: FakePolicies = field(default_factory=FakePolicies)
    reviews: FakeReviews = field(default_factory=FakeReviews)
    approvals: FakeApprovals = field(default_factory=FakeApprovals)

    def propose(self) -> ProposeProductCase:
        return ProposeProductCase(
            repo=self.cases,
            authz=ScopeAuthorizationService(),
            policy_override_repo=self.policies,
            platform_default_duties=DUTIES,
            ids=Uuid4Generator(),
            clock=FixedClock(NOW),
        )

    def get(self) -> GetProductCase:
        return GetProductCase(
            self.cases, ScopeAuthorizationService(), self.policies, DUTIES, self.approvals
        )

    def advance(self) -> AdvanceProductCase:
        return AdvanceProductCase(
            repo=self.cases,
            documents=self.documents,
            authz=ScopeAuthorizationService(),
            policy_override_repo=self.policies,
            platform_default_duties=DUTIES,
            reviews=self.reviews,
            ids=Uuid4Generator(),
            clock=FixedClock(NOW),
        )

    async def proposed(self, *, code: str = "DX-001") -> ProductDevelopmentCase:
        return await self.propose().handle(
            _context(SC_OPERATOR), proposal_code=code, product_name="Nồi 24cm", category="Nồi"
        )

    async def testing(self) -> ProductDevelopmentCase:
        case = await self.proposed()
        await self.advance().handle(
            _context(SC_OPERATOR),
            case_id=case.id,
            action=ProductAction.REQUEST_SAMPLE,
            supplier_name="NCC Minh Long",
        )
        result = await self.advance().handle(
            _context(SC_RND), case_id=case.id, action=ProductAction.RECEIVE_SAMPLE
        )
        return result.case

    async def passed(self, *, rnd: AccessContext | None = None) -> ProductDevelopmentCase:
        case = await self.testing()
        evaluation = self.documents.add(case, DocumentType.SAMPLE_EVALUATION)
        result = await self.advance().handle(
            rnd or _context(SC_RND),
            case_id=case.id,
            action=ProductAction.PASS_SAMPLE,
            document_id=evaluation.id.value,
        )
        return result.case


# --- propose and the PIC ---------------------------------------------------------


async def test_the_proposer_is_the_pic_and_the_proposal_is_audited() -> None:
    stack = Stack()
    proposer = uuid.uuid4()

    case = await stack.propose().handle(
        _context(SC_OPERATOR, principal=proposer),
        proposal_code="DX-001",
        product_name="Nồi 24cm",
        category="Nồi",
    )

    assert case.pic_user_id == proposer
    assert stack.cases.rows[case.id.value].pic_user_id == proposer
    (audit,) = stack.cases.audits
    assert (audit.action, audit.actor_id.value) == ("supply_chain.product_case.propose", proposer)
    assert [s.action for s in stack.cases.steps] == [ProductAction.PROPOSE]


def test_the_propose_command_takes_no_pic_at_all() -> None:
    """Not only the route model: the command itself has nowhere to put one, so
    no other caller (Zalo, a tool) can name a PIC either. The chat's two extra
    arguments name where it came from (audit only) and the draft it consumes,
    never a person, tenant or workspace."""
    parameters = inspect.signature(ProposeProductCase.handle).parameters
    assert not any("pic" in name for name in parameters)
    assert set(parameters) == {
        "self",
        "context",
        "proposal_code",
        "product_name",
        "category",
        "origin",
        "consume",
    }


async def test_rnd_cannot_propose() -> None:
    with pytest.raises(PermissionDeniedError):
        await (
            Stack()
            .propose()
            .handle(_context(SC_RND), proposal_code="DX-1", product_name="Nồi", category="Nồi")
        )


@pytest.mark.parametrize(
    "scopes",
    [
        # The duty the policy gives `propose`, without the write: refused.
        pytest.param(SC_OPERATOR - {PRODUCT_CASE_WRITE}, id="duty-without-write"),
        # The write, without that duty: refused too.
        pytest.param(SC_OPERATOR - {ORDERING}, id="write-without-duty"),
    ],
)
async def test_proposing_needs_the_product_case_write_and_the_proposing_duty(
    scopes: frozenset[str],
) -> None:
    """Lead decision 9: opening a case is gated as `CreatePOCase` gates it, by
    the context's write (`supply_chain.product_case.write`); `propose` is also
    a step of the policy (decision 7), so its duty is asked as well."""
    stack = Stack()
    with pytest.raises(PermissionDeniedError):
        await stack.propose().handle(
            _context(scopes), proposal_code="DX-1", product_name="Nồi", category="Nồi"
        )
    assert stack.cases.rows == {}


async def test_a_duplicate_proposal_code_in_the_tenant_is_a_conflict() -> None:
    stack = Stack()
    await stack.proposed(code="DX-001")
    with pytest.raises(ConflictError):
        await stack.proposed(code="DX-001")


# --- duties ----------------------------------------------------------------------


async def test_the_operator_cannot_pass_a_sample() -> None:
    stack = Stack()
    case = await stack.testing()
    evaluation = stack.documents.add(case, DocumentType.SAMPLE_EVALUATION)

    with pytest.raises(PermissionDeniedError):
        await stack.advance().handle(
            _context(SC_OPERATOR),
            case_id=case.id,
            action=ProductAction.PASS_SAMPLE,
            document_id=evaluation.id.value,
        )
    assert stack.cases.rows[case.id.value].state is ProductDevState.SAMPLE_TESTING


async def test_rnd_cannot_request_a_sample() -> None:
    stack = Stack()
    case = await stack.proposed()
    with pytest.raises(PermissionDeniedError):
        await stack.advance().handle(
            _context(SC_RND),
            case_id=case.id,
            action=ProductAction.REQUEST_SAMPLE,
            supplier_name="NCC",
        )


async def test_a_tenant_override_moves_a_step_and_a_po_override_does_not() -> None:
    """The product policy is its own document: a PO override that gives
    `cancel` to finance changes nothing here, the product override does."""
    stack = Stack()
    case = await stack.proposed()
    finance = frozenset({PRODUCT_CASE_READ, duty_scope(CaseDuty.FINANCE)})
    stack.policies.stored[(TENANT, "supply_chain_action_duties")] = {"cancel": "finance"}

    with pytest.raises(PermissionDeniedError):
        await stack.advance().handle(
            _context(finance), case_id=case.id, action=ProductAction.CANCEL, reason="x"
        )

    override = DUTIES.model_dump(mode="json")
    override["action_duties"]["cancel"] = "finance"
    stack.policies.stored[(TENANT, PRODUCT_ACTION_DUTIES_POLICY_ID)] = override
    cancelled = await stack.advance().handle(
        _context(finance), case_id=case.id, action=ProductAction.CANCEL, reason="x"
    )
    assert cancelled.case.state is ProductDevState.CANCELLED


# --- the paper -------------------------------------------------------------------


async def test_pass_without_an_evaluation_is_409_naming_the_type() -> None:
    stack = Stack()
    case = await stack.testing()
    with pytest.raises(ConflictError) as raised:
        await stack.advance().handle(
            _context(SC_RND), case_id=case.id, action=ProductAction.PASS_SAMPLE
        )
    assert raised.value.details["missing_document_type"] == "sample_evaluation"


async def test_pass_on_another_cases_evaluation_in_the_same_tenant_is_409() -> None:
    stack = Stack()
    case = await stack.testing()
    other = await stack.proposed(code="DX-002")
    foreign = stack.documents.add(other, DocumentType.SAMPLE_EVALUATION)

    with pytest.raises(ConflictError) as raised:
        await stack.advance().handle(
            _context(SC_RND),
            case_id=case.id,
            action=ProductAction.PASS_SAMPLE,
            document_id=foreign.id.value,
        )
    assert raised.value.details["missing_document_type"] == "sample_evaluation"
    assert stack.cases.rows[case.id.value].state is ProductDevState.SAMPLE_TESTING


async def test_pass_on_a_document_the_caller_cannot_read_is_the_same_409() -> None:
    stack = Stack()
    case = await stack.testing()
    unreadable = stack.documents.add(case, DocumentType.SAMPLE_EVALUATION, tenant=OTHER_TENANT)

    with pytest.raises(ConflictError) as raised:
        await stack.advance().handle(
            _context(SC_RND),
            case_id=case.id,
            action=ProductAction.PASS_SAMPLE,
            document_id=unreadable.id.value,
        )
    assert raised.value.details["missing_document_type"] == "sample_evaluation"


async def test_reject_naming_a_document_the_caller_cannot_read_is_refused() -> None:
    # The evaluation is optional on a reject, so the domain would accept the
    # step without one: only the handler's refusal stops an id from another
    # tenant being silently dropped instead of answered.
    stack = Stack()
    case = await stack.testing()
    unreadable = stack.documents.add(case, DocumentType.SAMPLE_EVALUATION, tenant=OTHER_TENANT)

    with pytest.raises(ConflictError):
        await stack.advance().handle(
            _context(SC_RND),
            case_id=case.id,
            action=ProductAction.REJECT_SAMPLE,
            reason="mẫu lỗi",
            document_id=unreadable.id.value,
        )
    assert stack.cases.rows[case.id.value].state is ProductDevState.SAMPLE_TESTING


async def test_a_step_naming_a_document_the_caller_cannot_read_is_refused() -> None:
    stack = Stack()
    case = await stack.proposed()
    unreadable = stack.documents.add(case, DocumentType.SAMPLE_PHOTO, tenant=OTHER_TENANT)

    # The same refusal a readable document gets on this step, so it says
    # nothing about whether the id exists elsewhere.
    with pytest.raises(DomainError, match="document"):
        await stack.advance().handle(
            _context(SC_OPERATOR),
            case_id=case.id,
            action=ProductAction.REQUEST_SAMPLE,
            supplier_name="NCC",
            document_id=unreadable.id.value,
        )
    assert stack.cases.rows[case.id.value].state is ProductDevState.PROPOSED


async def test_a_document_on_a_step_that_takes_none_is_refused() -> None:
    stack = Stack()
    case = await stack.proposed()
    photo = stack.documents.add(case, DocumentType.SAMPLE_PHOTO)
    with pytest.raises(DomainError, match="document"):
        await stack.advance().handle(
            _context(SC_OPERATOR),
            case_id=case.id,
            action=ProductAction.REQUEST_SAMPLE,
            supplier_name="NCC",
            document_id=photo.id.value,
        )


async def test_steps_one_to_five_through_the_handlers() -> None:
    stack = Stack()
    case = await stack.testing()
    revision = stack.documents.add(case, DocumentType.SAMPLE_REVISION_REQUEST)
    await stack.advance().handle(
        _context(SC_RND),
        case_id=case.id,
        action=ProductAction.REQUEST_REVISION,
        reason="Tay cầm lỏng",
        document_id=revision.id.value,
    )
    await stack.advance().handle(
        _context(SC_RND), case_id=case.id, action=ProductAction.RECEIVE_REVISED_SAMPLE
    )
    round_one_report = stack.documents.add(
        case, DocumentType.SAMPLE_EVALUATION, uploaded_at=NOW - timedelta(minutes=1)
    )
    with pytest.raises(ConflictError):
        await stack.advance().handle(
            _context(SC_RND),
            case_id=case.id,
            action=ProductAction.PASS_SAMPLE,
            document_id=round_one_report.id.value,
        )
    evaluation = stack.documents.add(case, DocumentType.SAMPLE_EVALUATION)
    passed = await stack.advance().handle(
        _context(SC_RND),
        case_id=case.id,
        action=ProductAction.PASS_SAMPLE,
        document_id=evaluation.id.value,
    )

    assert (passed.case.state, passed.case.sample_round) == (ProductDevState.PENDING_BOD_REVIEW, 2)
    assert [a.action for a in stack.cases.audits][-1] == "supply_chain.product_case.pass_sample"
    assert stack.cases.audits[-1].details["document_id"] == str(evaluation.id)


# --- another tenant's or workspace's case ----------------------------------------


@pytest.mark.parametrize("owner", [(OTHER_TENANT, WORKSPACE), (TENANT, OTHER_WORKSPACE)])
async def test_another_tenants_or_workspaces_case_is_not_found(
    owner: tuple[uuid.UUID, uuid.UUID],
) -> None:
    stack = Stack()
    case = await stack.proposed()
    tenant, workspace = owner
    everything = SC_OPERATOR | SC_RND
    caller = _context(everything, tenant=tenant, workspace=workspace)

    with pytest.raises(NotFoundError):
        await GetProductCase(
            stack.cases, ScopeAuthorizationService(), stack.policies, DUTIES, stack.approvals
        ).handle(caller, case.id)
    with pytest.raises(NotFoundError):
        await ListProductCaseTransitions(stack.cases, ScopeAuthorizationService()).handle(
            caller, case.id
        )
    with pytest.raises(NotFoundError):
        await stack.advance().handle(
            caller, case_id=case.id, action=ProductAction.REQUEST_SAMPLE, supplier_name="NCC"
        )
    assert stack.cases.rows[case.id.value].state is ProductDevState.PROPOSED


@dataclass
class LeakyCases(FakeCases):
    """A repository that forgot to narrow `get` by workspace: what the
    handlers' own workspace check is there for. Every other method keeps
    `FakeCases`' narrowing, so only that check stands between the caller and
    another workspace's case."""

    async def get(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> ProductDevelopmentCase | None:
        case = self.rows.get(case_id.value)
        return None if case is None else replace(case, _pending_steps=[])


async def test_the_handlers_refuse_another_workspaces_case_a_repository_let_through() -> None:
    """Defence in depth behind RLS and the repository's own filter: a case
    from another workspace that reaches the handler anyway is not found."""
    stack = Stack(cases=LeakyCases())
    case = await stack.proposed()
    caller = _context(SC_OPERATOR | SC_RND, workspace=OTHER_WORKSPACE)

    with pytest.raises(NotFoundError):
        await stack.get().handle(caller, case.id)
    with pytest.raises(NotFoundError):
        await ListProductCaseTransitions(stack.cases, ScopeAuthorizationService()).handle(
            caller, case.id
        )
    with pytest.raises(NotFoundError):
        await stack.advance().handle(
            caller, case_id=case.id, action=ProductAction.REQUEST_SAMPLE, supplier_name="NCC"
        )
    assert stack.cases.rows[case.id.value].state is ProductDevState.PROPOSED


@pytest.mark.parametrize("read", ["get", "list", "transitions", "duties"])
async def test_reading_needs_the_read_scope(read: str) -> None:
    """Every read refuses a caller of the same tenant and workspace who holds
    every step duty and no read scope, at the handler itself."""
    stack = Stack()
    case = await stack.proposed()
    caller = _context(frozenset({ORDERING, RND, EXCEPTIONS}))
    authz = ScopeAuthorizationService()
    with pytest.raises(PermissionDeniedError):
        if read == "get":
            await GetProductCase(
                stack.cases, authz, stack.policies, DUTIES, stack.approvals
            ).handle(caller, case.id)
        elif read == "list":
            await ListProductCases(stack.cases, authz).handle(
                caller, ProductCaseListFilter(), limit=10, cursor=None
            )
        elif read == "transitions":
            await ListProductCaseTransitions(stack.cases, authz).handle(caller, case.id)
        else:
            await GetProductActionDuties(stack.policies, DUTIES, authz).handle(caller)


async def test_setting_the_product_duties_needs_the_action_duties_write() -> None:
    """Who may take every product step is changed only by a holder of
    `supply_chain.action_duties.write`; reading it is not enough."""
    stack = Stack()
    caller = _context(SC_OPERATOR | {ACTION_DUTIES_READ})
    with pytest.raises(PermissionDeniedError):
        await SetProductActionDutiesOverride(
            stack.policies, ScopeAuthorizationService(), Uuid4Generator(), FixedClock(NOW)
        ).handle(caller, DUTIES)
    assert stack.policies.stored == {}


async def test_the_pic_filter_narrows_and_everyone_with_read_sees_every_case() -> None:
    stack = Stack()
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    for code, principal in (("DX-1", mine), ("DX-2", theirs)):
        await stack.propose().handle(
            _context(SC_OPERATOR, principal=principal),
            proposal_code=code,
            product_name="Nồi",
            category="Nồi",
        )
    listing = ListProductCases(stack.cases, ScopeAuthorizationService())
    reader = _context(frozenset({PRODUCT_CASE_READ}))

    everyone = await listing.handle(reader, ProductCaseListFilter(), limit=10, cursor=None)
    narrowed = await listing.handle(
        reader, ProductCaseListFilter(pic_user_id=mine), limit=10, cursor=None
    )

    assert {c.proposal_code for c in everyone.items} == {"DX-1", "DX-2"}
    assert [c.proposal_code for c in narrowed.items] == ["DX-1"]


def test_a_cursor_is_bound_to_its_filter_and_workspace() -> None:
    plain = ProductCaseListFilter().page_query(TENANT, WORKSPACE)
    assert plain != ProductCaseListFilter(pic_user_id=uuid.uuid4()).page_query(TENANT, WORKSPACE)
    assert plain != ProductCaseListFilter().page_query(TENANT, OTHER_WORKSPACE)
    assert plain != ProductCaseListFilter(state=ProductDevState.PROPOSED).page_query(
        TENANT, WORKSPACE
    )


async def test_a_broken_tenant_override_refuses_rather_than_falling_back() -> None:
    """Fail closed (reviewing-feature-security §6): a stored product override
    that no longer validates refuses every step; it never quietly reads as the
    platform default, which may grant a duty the tenant took away."""
    stack = Stack()
    case = await stack.proposed()
    stack.policies.stored[(TENANT, PRODUCT_ACTION_DUTIES_POLICY_ID)] = {
        "schema_version": "1.0",
        "policy_id": PRODUCT_ACTION_DUTIES_POLICY_ID,
        "policy_version": "1.0.0",
        "action_duties": {"propose": "ordering"},
    }
    with pytest.raises(ValidationError):
        await stack.advance().handle(
            _context(SC_OPERATOR),
            case_id=case.id,
            action=ProductAction.REQUEST_SAMPLE,
            supplier_name="NCC",
        )
    assert stack.cases.rows[case.id.value].state is ProductDevState.PROPOSED


# --- step 6: BGĐ's review ----------------------------------------------------------

BOD_REVIEW = "supply_chain.product_action.bod_review"


@pytest.mark.parametrize("action", sorted(GRAPH_ONLY_ACTIONS))
async def test_bgds_outcomes_are_refused_as_a_step_before_anything_is_read(
    action: ProductAction,
) -> None:
    """Only the review graph applies them. Refused before the duty policy is
    resolved (it has no duty for them) and before the case is read: even a
    caller holding every scope, approve.bod included, cannot take them."""
    stack = Stack()
    case = await stack.passed()
    everything = SC_OPERATOR | SC_RND | {"supply_chain.approve.bod", "approvals.decide"}

    with pytest.raises(DomainError, match="approval"):
        await stack.advance().handle(
            _context(everything),
            case_id=case.id,
            action=action,
            reason="BGĐ đồng ý" if action is ProductAction.BOD_REJECT else None,
        )
    assert stack.cases.rows[case.id.value].state is ProductDevState.PENDING_BOD_REVIEW
    # Not even the case was looked up: a caller with no scope at all gets the
    # same refusal, not a 403 or a 404.
    with pytest.raises(DomainError):
        await stack.advance().handle(
            _context(frozenset(), tenant=OTHER_TENANT),
            case_id=ProductDevelopmentCaseId(uuid.uuid4()),
            action=action,
        )


async def test_passing_a_sample_asks_for_bgds_review_once_as_the_rnd_tester() -> None:
    stack = Stack()
    tester = uuid.uuid4()
    rnd = _context(SC_RND, principal=tester)
    case = await stack.testing()
    evaluation = stack.documents.add(case, DocumentType.SAMPLE_EVALUATION)

    result = await stack.advance().handle(
        rnd, case_id=case.id, action=ProductAction.PASS_SAMPLE, document_id=evaluation.id.value
    )

    assert result.review is ReviewRaise.RAISED
    ((context, asked_case, requester),) = stack.reviews.asked
    assert context is rnd
    assert (asked_case.id, asked_case.state) == (case.id, ProductDevState.PENDING_BOD_REVIEW)
    assert requester == ReviewRequester(
        principal_id=tester,
        roles=rnd.roles,
        scopes=rnd.scopes,
        plan_id="professional",
        channel="web",
    )


async def test_a_refused_review_leaves_the_step_recorded_and_says_so() -> None:
    """The plan's run quota (or any failed start) does not undo the pass: the
    case waits for BGĐ, the answer says the review is not raised yet, and the
    reconcile lane raises it later."""
    stack = Stack(reviews=FakeReviews(error=QuotaExceededError("hết lượt chạy")))
    case = await stack.testing()
    evaluation = stack.documents.add(case, DocumentType.SAMPLE_EVALUATION)

    result = await stack.advance().handle(
        _context(SC_RND),
        case_id=case.id,
        action=ProductAction.PASS_SAMPLE,
        document_id=evaluation.id.value,
    )

    assert result.review is ReviewRaise.NOT_RAISED
    assert stack.cases.rows[case.id.value].state is ProductDevState.PENDING_BOD_REVIEW
    assert stack.cases.audits[-1].action == "supply_chain.product_case.pass_sample"


async def test_only_a_step_that_leaves_the_case_waiting_asks_for_the_review() -> None:
    stack = Stack()
    case = await stack.testing()
    revision = stack.documents.add(case, DocumentType.SAMPLE_REVISION_REQUEST)

    result = await stack.advance().handle(
        _context(SC_RND),
        case_id=case.id,
        action=ProductAction.REQUEST_REVISION,
        reason="Tay cầm lỏng",
        document_id=revision.id.value,
    )

    assert result.review is None
    assert stack.reviews.asked == []


async def test_resuming_back_into_waiting_for_bgd_asks_for_the_review() -> None:
    """A case paused while waiting for BGĐ before S2 (S1 allowed it) comes
    back to `pending_bod_review` with no review: resuming it raises one."""
    stack = Stack()
    case = await stack.passed()
    stored = stack.cases.rows[case.id.value]
    stack.cases.rows[case.id.value] = replace(
        stored,
        state=ProductDevState.BLOCKED,
        interrupted_state=ProductDevState.PENDING_BOD_REVIEW,
    )
    stack.reviews.asked.clear()

    result = await stack.advance().handle(
        _context(SC_OPERATOR), case_id=case.id, action=ProductAction.RESUME
    )

    assert result.case.state is ProductDevState.PENDING_BOD_REVIEW
    assert result.review is ReviewRaise.RAISED
    assert len(stack.reviews.asked) == 1


async def test_the_only_step_on_a_case_waiting_for_bgd_is_cancel() -> None:
    stack = Stack()
    case = await stack.passed()

    with pytest.raises(ConflictError):
        await stack.advance().handle(
            _context(SC_OPERATOR), case_id=case.id, action=ProductAction.FLAG_BLOCKED, reason="x"
        )
    cancelled = await stack.advance().handle(
        _context(SC_OPERATOR), case_id=case.id, action=ProductAction.CANCEL, reason="Dừng dự án"
    )
    assert cancelled.case.state is ProductDevState.CANCELLED
    assert cancelled.review is None


async def test_the_case_page_shows_the_review_it_waits_on_and_its_stamped_scope() -> None:
    stack = Stack()
    case = await stack.passed()
    review = PendingReview(
        id=uuid.uuid4(),
        approval_type=BOD_REVIEW,
        payload={"product_dev_case_id": str(case.id)},
        created_at=NOW,
        required_scope="supply_chain.approve.bod",
    )
    stack.approvals.pending[(WORKSPACE, BOD_REVIEW, str(case.id))] = review

    detail = await stack.get().handle(_context(frozenset({PRODUCT_CASE_READ})), case.id)

    assert detail.pending_review is review
    assert [o.action for o in detail.case.action_options()] == [ProductAction.CANCEL]


async def test_the_case_page_shows_no_review_outside_waiting_for_bgd() -> None:
    stack = Stack()
    case = await stack.passed()
    stack.approvals.pending[(WORKSPACE, BOD_REVIEW, str(case.id))] = PendingReview(
        id=uuid.uuid4(),
        approval_type=BOD_REVIEW,
        payload={},
        created_at=NOW,
        required_scope="supply_chain.approve.bod",
    )
    await stack.advance().handle(
        _context(SC_OPERATOR), case_id=case.id, action=ProductAction.CANCEL, reason="Dừng"
    )

    detail = await stack.get().handle(_context(frozenset({PRODUCT_CASE_READ})), case.id)

    assert detail.pending_review is None
