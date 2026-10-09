"""Unit: ProposeProductCase / GetProductCase / ListProductCases /
ListProductCaseTransitions / AdvanceProductCase (stage-1 tickets 01-03), and
the Category, the case's SLA and `ReassignProductCasePic` (ticket 06).

Fakes stand in for the case records, the document records and the policy
store; each honours its port the way the database does (tenant AND workspace
narrowing, one proposal code per tenant, optimistic version, a round closed
once). Under test are the handlers' own decisions: who may take a step, which
case, which paper, and that the PIC is the proposer and nobody else.
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
from dw_supply_chain.action_duties import CaseDuty, load_supply_chain_action_duties
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
    PlaceOrder,
    ProposeProductCase,
    ReassignProductCasePic,
)
from dw_supply_chain.domain.case_document import (
    CaseDocument,
    CaseDocumentId,
    CaseKind,
    DocumentType,
)
from dw_supply_chain.domain.po_case import POCase
from dw_supply_chain.domain.product_development_case import (
    CODING_ACTIONS,
    GRAPH_ONLY_ACTIONS,
    ItemCodeIssued,
    ProductAction,
    ProductCaseStep,
    ProductCaseTransition,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
    SampleRound,
    SkuAdded,
    SkuDraft,
)
from dw_supply_chain.domain.product_proposal import DraftClaim
from dw_supply_chain.domain.sla_evaluation import SLAEvaluationStatus
from dw_supply_chain.policy_files import SLA_POLICY_FILE
from dw_supply_chain.product_action_duties import (
    PRODUCT_ACTION_DUTIES_POLICY_ID,
    SupplyChainProductActionDuties,
)
from dw_supply_chain.sla_policy import load_supply_chain_sla_policy

pytestmark = pytest.mark.unit

# The shipped platform default: categories `noi`, `chao`; bm04 2 days.
SLA = load_supply_chain_sla_policy(
    Path(__file__).resolve().parents[5] / "configs" / "policies" / SLA_POLICY_FILE
)
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
SUPPLY_LEAD = duty_scope(CaseDuty.SUPPLY_LEAD)
# sc_supply_lead (TP Cung ứng, S3): confirms with the supplier and nothing else.
SC_SUPPLY_LEAD = frozenset({PRODUCT_CASE_READ, SUPPLY_LEAD})

DUTIES = SupplyChainProductActionDuties.model_validate(
    {
        "schema_version": "1.0",
        "policy_id": PRODUCT_ACTION_DUTIES_POLICY_ID,
        "policy_version": "1.3.0",
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
            "complete_profile": "rnd",
            "confirm_with_supplier": "supply_lead",
            "issue_item_code": "ordering",
            "add_sku": "ordering",
            "remove_sku": "ordering",
            "submit_for_signoff": "ordering",
            "place_order": "ordering",
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
    po_cases: dict[uuid.UUID, POCase] = field(default_factory=dict)
    # When each case last moved (its latest history row), as the SQL reads it.
    moved_at: dict[uuid.UUID, datetime] = field(default_factory=dict)

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
        self.moved_at[case.id.value] = case.created_at

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
        before = len(self.steps)
        self._drain(case)
        last = self.steps[before:]
        self.audits.append(audit)
        # A round opened by this save is read back with its opening time, and
        # a step reached by it with when it was reached (a resume reaches none).
        opened = case.round_opened_at or (NOW if case.sample_round else None)
        entered = (
            NOW if last and last[-1].action is not ProductAction.RESUME else case.stage_entered_at
        )
        if last:
            self.moved_at[case.id.value] = NOW
        self.rows[case.id.value] = replace(
            case, _pending_steps=[], round_opened_at=opened, stage_entered_at=entered
        )

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
        self, context: AccessContext, case_id: ProductDevelopmentCaseId, request: PageRequest
    ) -> Page[ProductCaseTransition]:
        raise NotImplementedError("not exercised by these handler tests beyond the 404 path")

    async def list_rounds(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> list[SampleRound]:
        return []

    async def place_order(
        self,
        context: AccessContext,
        case: ProductDevelopmentCase,
        po_case: POCase,
        *,
        audits: Sequence[AuditEvent],
    ) -> None:
        """As the SQL: moved only from the state its step left, at the version
        read, else a conflict and nothing written; then both rows and audits."""
        (step,) = case.pop_pending_steps()
        stored = self.rows.get(case.id.value)
        if (
            stored is None
            or not self._visible(context, stored)
            or stored.version != case.version - 1
            or stored.state is not step.from_state
        ):
            raise ConflictError("hồ sơ đã được đặt hàng hoặc vừa thay đổi")
        self.steps.append(step)
        self.audits.extend(audits)
        self.rows[case.id.value] = replace(case, _pending_steps=[])
        po_case.created_at = NOW
        self.po_cases[po_case.id.value] = po_case

    async def state_entered_at(
        self, context: AccessContext, case_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, datetime]:
        return {
            i: self.moved_at[i]
            for i in case_ids
            if i in self.moved_at and self._visible(context, self.rows[i])
        }

    async def po_case_of(self, context: AccessContext, case_id: uuid.UUID) -> uuid.UUID | None:
        return next(
            (
                po.id.value
                for po in self.po_cases.values()
                if po.product_dev_case_id == case_id
                and (po.tenant_id.value, po.workspace_id.value)
                == (context.tenant_id, context.workspace_id)
            ),
            None,
        )


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
    """`ProductApprovalPort`: records each request for BGĐ's review and answers
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
        self,
        context: AccessContext,
        *,
        prefix: str,
        limit: int,
        payload_match: tuple[str, str] | None = None,
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
            platform_default_sla_policy=SLA,
            ids=Uuid4Generator(),
            clock=FixedClock(NOW),
        )

    def get(self, now: datetime = NOW) -> GetProductCase:
        return GetProductCase(
            self.cases,
            ScopeAuthorizationService(),
            self.policies,
            DUTIES,
            SLA,
            self.approvals,
            FixedClock(now),
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
            _context(SC_OPERATOR), proposal_code=code, product_name="Nồi 24cm", category="noi"
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

    async def profiling(self) -> ProductDevelopmentCase:
        """BGĐ approved: what the review graph saves, as the repository would
        (the step command refuses `bod_approve`)."""
        case = await self.passed()
        stored = await self.cases.get(_context(SC_RND), case.id)
        assert stored is not None
        stored.bod_approve(actor_id=uuid.uuid4())
        await self.cases.save(_context(SC_RND), stored, audit=self.cases.audits[-1])
        return self.cases.rows[case.id.value]

    async def confirming(self) -> ProductDevelopmentCase:
        case = await self.profiling()
        bm04 = self.documents.add(case, DocumentType.PRODUCT_PROFILE_BM04)
        result = await self.advance().handle(
            _context(SC_RND),
            case_id=case.id,
            action=ProductAction.COMPLETE_PROFILE,
            document_id=bm04.id.value,
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
        category="noi",
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
            .handle(_context(SC_RND), proposal_code="DX-1", product_name="Nồi", category="noi")
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
            _context(scopes), proposal_code="DX-1", product_name="Nồi", category="noi"
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


# --- steps 7-8: BM04 and the supplier's confirmation ------------------------------


async def test_rnd_completes_the_bm04_and_the_supply_lead_confirms_with_the_supplier() -> None:
    stack = Stack()
    case = await stack.profiling()
    bm04 = stack.documents.add(case, DocumentType.PRODUCT_PROFILE_BM04)
    profiled = await stack.advance().handle(
        _context(SC_RND),
        case_id=case.id,
        action=ProductAction.COMPLETE_PROFILE,
        document_id=bm04.id.value,
    )
    assert profiled.case.state is ProductDevState.SUPPLIER_CONFIRMATION
    assert profiled.review is None
    assert stack.cases.steps[-1].document_id == bm04.id.value
    assert stack.cases.audits[-1].details["document_id"] == str(bm04.id)

    email = stack.documents.add(case, DocumentType.SUPPLIER_CONFIRMATION_EMAIL)
    confirmed = await stack.advance().handle(
        _context(SC_SUPPLY_LEAD),
        case_id=case.id,
        action=ProductAction.CONFIRM_WITH_SUPPLIER,
        document_id=email.id.value,
    )
    assert confirmed.case.state is ProductDevState.ITEM_CODING
    assert stack.cases.audits[-1].action == "supply_chain.product_case.confirm_with_supplier"
    assert stack.cases.audits[-1].details["document_id"] == str(email.id)
    assert stack.reviews.asked == [stack.reviews.asked[0]]  # only the pass asked BGĐ


async def test_rnd_cannot_confirm_with_the_supplier() -> None:
    stack = Stack()
    case = await stack.confirming()
    email = stack.documents.add(case, DocumentType.SUPPLIER_CONFIRMATION_EMAIL)
    with pytest.raises(PermissionDeniedError):
        await stack.advance().handle(
            _context(SC_RND),
            case_id=case.id,
            action=ProductAction.CONFIRM_WITH_SUPPLIER,
            document_id=email.id.value,
        )
    assert stack.cases.rows[case.id.value].state is ProductDevState.SUPPLIER_CONFIRMATION


async def test_the_supply_lead_cannot_complete_the_bm04() -> None:
    stack = Stack()
    case = await stack.profiling()
    bm04 = stack.documents.add(case, DocumentType.PRODUCT_PROFILE_BM04)
    with pytest.raises(PermissionDeniedError):
        await stack.advance().handle(
            _context(SC_SUPPLY_LEAD),
            case_id=case.id,
            action=ProductAction.COMPLETE_PROFILE,
            document_id=bm04.id.value,
        )
    assert stack.cases.rows[case.id.value].state is ProductDevState.PROFILE_IN_PROGRESS


async def test_a_bm04_draft_never_satisfies_step_7_only_a_case_document_does() -> None:
    """ADR 0025 point 4, ticket ai-automation/03: a step reads its paper from
    the case's documents and nowhere else. A draft lives in its own table and
    becomes a document only when a person approves it (ticket 05), so a draft's
    id offered as the step's paper is the same 409 as no paper at all. The
    step has no way to read drafts: it is given none."""
    assert not any("draft" in name for name in AdvanceProductCase.__dataclass_fields__)
    stack = Stack()
    case = await stack.profiling()
    draft_id = uuid.uuid4()  # the id a BM04 draft of this case would carry
    with pytest.raises(ConflictError) as raised:
        await stack.advance().handle(
            _context(SC_RND),
            case_id=case.id,
            action=ProductAction.COMPLETE_PROFILE,
            document_id=draft_id,
        )
    assert raised.value.details["missing_document_type"] == "product_profile_bm04"
    assert stack.cases.rows[case.id.value].state is ProductDevState.PROFILE_IN_PROGRESS


@pytest.mark.parametrize(
    ("action", "doc_type", "caller"),
    [
        (ProductAction.COMPLETE_PROFILE, DocumentType.PRODUCT_PROFILE_BM04, SC_RND),
        (
            ProductAction.CONFIRM_WITH_SUPPLIER,
            DocumentType.SUPPLIER_CONFIRMATION_EMAIL,
            SC_SUPPLY_LEAD,
        ),
    ],
)
@pytest.mark.parametrize("paper", ["none", "another_tenants", "another_cases", "older"])
async def test_steps_seven_and_eight_refuse_paper_that_is_not_theirs_with_409(
    action: ProductAction, doc_type: DocumentType, caller: frozenset[str], paper: str
) -> None:
    """Missing, another tenant's (unreadable to the caller under RLS),
    another case's, or uploaded before the case reached the step: one 409
    naming the missing type, and the case does not move."""
    stack = Stack()
    case = (
        await stack.profiling()
        if action is ProductAction.COMPLETE_PROFILE
        else await stack.confirming()
    )
    other = await stack.proposed(code="DX-OTHER")
    papers: dict[str, Callable[[], uuid.UUID | None]] = {
        "none": lambda: None,
        "another_tenants": lambda: (
            stack.documents.add(case, doc_type, tenant=OTHER_TENANT).id.value
        ),
        "another_cases": lambda: stack.documents.add(other, doc_type).id.value,
        "older": lambda: (
            stack.documents.add(case, doc_type, uploaded_at=NOW - timedelta(seconds=1)).id.value
        ),
    }
    document_id = papers[paper]()
    before = stack.cases.rows[case.id.value].state

    with pytest.raises(ConflictError) as raised:
        await stack.advance().handle(
            _context(caller), case_id=case.id, action=action, document_id=document_id
        )
    assert raised.value.details["missing_document_type"] == doc_type.value
    assert stack.cases.rows[case.id.value].state is before


@pytest.mark.parametrize("owner", [(OTHER_TENANT, WORKSPACE), (TENANT, OTHER_WORKSPACE)])
@pytest.mark.parametrize(
    ("action", "doc_type"),
    [
        (ProductAction.COMPLETE_PROFILE, DocumentType.PRODUCT_PROFILE_BM04),
        (ProductAction.CONFIRM_WITH_SUPPLIER, DocumentType.SUPPLIER_CONFIRMATION_EMAIL),
    ],
)
async def test_steps_seven_and_eight_on_another_tenants_or_workspaces_case_are_not_found(
    owner: tuple[uuid.UUID, uuid.UUID], action: ProductAction, doc_type: DocumentType
) -> None:
    stack = Stack()
    case = (
        await stack.profiling()
        if action is ProductAction.COMPLETE_PROFILE
        else await stack.confirming()
    )
    paper = stack.documents.add(case, doc_type)
    before = stack.cases.rows[case.id.value].state
    tenant, workspace = owner
    caller = _context(SC_RND | SC_SUPPLY_LEAD, tenant=tenant, workspace=workspace)

    with pytest.raises(NotFoundError):
        await stack.advance().handle(
            caller, case_id=case.id, action=action, document_id=paper.id.value
        )
    assert stack.cases.rows[case.id.value].state is before


async def test_an_override_stored_before_steps_seven_and_eight_still_lets_them_be_taken() -> None:
    """A tenant override written at 1.0.0 names neither step; the platform's
    duty applies to them and the tenant's own choices still hold."""
    stack = Stack()
    case = await stack.profiling()
    old = DUTIES.model_dump(mode="json")
    old["policy_version"] = "1.0.0"
    del old["action_duties"]["complete_profile"]
    del old["action_duties"]["confirm_with_supplier"]
    old["action_duties"]["cancel"] = "rnd"
    stack.policies.stored[(TENANT, PRODUCT_ACTION_DUTIES_POLICY_ID)] = old

    bm04 = stack.documents.add(case, DocumentType.PRODUCT_PROFILE_BM04)
    with pytest.raises(PermissionDeniedError):
        await stack.advance().handle(
            _context(SC_SUPPLY_LEAD),
            case_id=case.id,
            action=ProductAction.COMPLETE_PROFILE,
            document_id=bm04.id.value,
        )
    profiled = await stack.advance().handle(
        _context(SC_RND),
        case_id=case.id,
        action=ProductAction.COMPLETE_PROFILE,
        document_id=bm04.id.value,
    )
    assert profiled.case.state is ProductDevState.SUPPLIER_CONFIRMATION
    with pytest.raises(PermissionDeniedError):
        await stack.advance().handle(
            _context(SC_OPERATOR), case_id=case.id, action=ProductAction.CANCEL, reason="x"
        )
    duties = await GetProductActionDuties(
        stack.policies, DUTIES, ScopeAuthorizationService()
    ).handle(_context(frozenset({ACTION_DUTIES_READ})))
    assert duties.duty_for(ProductAction.CONFIRM_WITH_SUPPLIER) is CaseDuty.SUPPLY_LEAD
    assert duties.duty_for(ProductAction.CANCEL) is CaseDuty.RND


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
        await stack.get().handle(caller, case.id)
    with pytest.raises(NotFoundError):
        await ListProductCaseTransitions(stack.cases, ScopeAuthorizationService()).handle(
            caller, case.id, limit=10, cursor=None
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
            caller, case.id, limit=10, cursor=None
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
                stack.cases, authz, stack.policies, DUTIES, SLA, stack.approvals, FixedClock(NOW)
            ).handle(caller, case.id)
        elif read == "list":
            await ListProductCases(stack.cases, authz).handle(
                caller, ProductCaseListFilter(), limit=10, cursor=None
            )
        elif read == "transitions":
            await ListProductCaseTransitions(stack.cases, authz).handle(
                caller, case.id, limit=10, cursor=None
            )
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
            category="noi",
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


# --- step 9: item code, SKUs, sign-off (ticket 04) -------------------------------------

SIGNOFF = "supply_chain.product_action.signoff"


async def _coding(stack: Stack) -> ProductDevelopmentCase:
    case = await stack.confirming()
    email = stack.documents.add(case, DocumentType.SUPPLIER_CONFIRMATION_EMAIL)
    result = await stack.advance().handle(
        _context(SC_SUPPLY_LEAD),
        case_id=case.id,
        action=ProductAction.CONFIRM_WITH_SUPPLIER,
        document_id=email.id.value,
    )
    assert result.case.state is ProductDevState.ITEM_CODING
    return result.case


async def _coded(stack: Stack) -> ProductDevelopmentCase:
    case = await _coding(stack)
    await stack.advance().handle(
        _context(SC_OPERATOR),
        case_id=case.id,
        action=ProductAction.ISSUE_ITEM_CODE,
        item_code="MH-0001",
    )
    result = await stack.advance().handle(
        _context(SC_OPERATOR),
        case_id=case.id,
        action=ProductAction.ADD_SKU,
        sku=SkuDraft("MH-0001-RED", "Đỏ 24cm", 120),
    )
    return result.case


async def test_ordering_codes_the_product_with_minted_ids_and_audits_each_step() -> None:
    stack = Stack()
    case = await _coded(stack)

    issued, added = stack.cases.steps[-2:]
    assert isinstance(issued.coding, ItemCodeIssued)
    assert isinstance(added.coding, SkuAdded)
    assert issued.coding.item_code.code == "MH-0001"
    assert added.coding.item_code_id == issued.coding.item_code.id
    assert added.coding.sku.id != issued.coding.item_code.id
    named = [a.details.get("item_code") or a.details.get("sku_code") for a in stack.cases.audits]
    assert named[-2:] == ["MH-0001", "MH-0001-RED"]
    assert case.state is ProductDevState.ITEM_CODING
    assert stack.reviews.asked[1:] == []  # coding asks for no approval


@pytest.mark.parametrize("action", sorted(CODING_ACTIONS | {ProductAction.SUBMIT_FOR_SIGNOFF}))
@pytest.mark.parametrize("scopes", [SC_RND, SC_SUPPLY_LEAD, frozenset({PRODUCT_CASE_READ})])
async def test_step_nine_needs_the_ordering_duty(
    action: ProductAction, scopes: frozenset[str]
) -> None:
    """Refused before the case is read, as every step's duty is."""
    stack = Stack()
    case = await _coded(stack)
    before = len(stack.cases.steps)
    with pytest.raises(PermissionDeniedError):
        await stack.advance().handle(
            _context(scopes),
            case_id=case.id,
            action=action,
            item_code="MH-0002" if action is ProductAction.ISSUE_ITEM_CODE else None,
        )
    assert len(stack.cases.steps) == before


@pytest.mark.parametrize("action", [ProductAction.SIGNOFF_APPROVE, ProductAction.SIGNOFF_REJECT])
async def test_the_signoffs_outcome_is_never_a_step_even_for_the_ordering_duty(
    action: ProductAction,
) -> None:
    stack = Stack()
    case = await _coded(stack)
    await stack.advance().handle(
        _context(SC_OPERATOR), case_id=case.id, action=ProductAction.SUBMIT_FOR_SIGNOFF
    )
    with pytest.raises(DomainError, match="approval"):
        await stack.advance().handle(
            _context(SC_OPERATOR | {"supply_chain.approve.bod", "approvals.decide"}),
            case_id=case.id,
            action=action,
            reason="tự duyệt",
        )
    assert stack.cases.rows[case.id.value].state is ProductDevState.PENDING_SIGNOFF


async def test_submitting_without_codes_is_a_409_and_asks_for_no_signoff() -> None:
    stack = Stack()
    case = await _coding(stack)
    asked = len(stack.reviews.asked)
    with pytest.raises(ConflictError) as refused:
        await stack.advance().handle(
            _context(SC_OPERATOR), case_id=case.id, action=ProductAction.SUBMIT_FOR_SIGNOFF
        )
    assert refused.value.details["missing"] == "item_code,sku"
    assert len(stack.reviews.asked) == asked


async def test_a_sku_before_the_item_code_is_refused() -> None:
    stack = Stack()
    case = await _coding(stack)
    with pytest.raises(ConflictError, match="mã hàng"):
        await stack.advance().handle(
            _context(SC_OPERATOR),
            case_id=case.id,
            action=ProductAction.ADD_SKU,
            sku=SkuDraft("MH-0001-RED", "Đỏ"),
        )


async def test_submitting_asks_for_the_signoff_after_the_step_is_saved() -> None:
    stack = Stack()
    case = await _coded(stack)
    operator = _context(SC_OPERATOR)

    result = await stack.advance().handle(
        operator, case_id=case.id, action=ProductAction.SUBMIT_FOR_SIGNOFF
    )

    assert result.review is ReviewRaise.RAISED
    assert result.case.state is ProductDevState.PENDING_SIGNOFF
    assert result.case.signoff_round == 1
    _, asked_case, requester = stack.reviews.asked[-1]
    assert asked_case.state is ProductDevState.PENDING_SIGNOFF
    assert requester.principal_id == operator.principal_id
    assert stack.cases.rows[case.id.value].state is ProductDevState.PENDING_SIGNOFF


async def test_a_refused_signoff_start_keeps_the_submission() -> None:
    stack = Stack()
    case = await _coded(stack)
    stack.reviews.error = QuotaExceededError("hết lượt chạy")

    result = await stack.advance().handle(
        _context(SC_OPERATOR), case_id=case.id, action=ProductAction.SUBMIT_FOR_SIGNOFF
    )

    assert result.review is ReviewRaise.NOT_RAISED
    assert stack.cases.rows[case.id.value].state is ProductDevState.PENDING_SIGNOFF


@pytest.mark.parametrize("other", ["tenant", "workspace"])
async def test_another_tenant_or_workspace_cannot_code_the_case(other: str) -> None:
    stack = Stack()
    case = await _coding(stack)
    caller = _context(
        SC_OPERATOR,
        tenant=OTHER_TENANT if other == "tenant" else TENANT,
        workspace=OTHER_WORKSPACE,
    )
    with pytest.raises(NotFoundError):
        await stack.advance().handle(
            caller, case_id=case.id, action=ProductAction.ISSUE_ITEM_CODE, item_code="MH-0001"
        )
    assert stack.cases.rows[case.id.value].item_code is None


async def test_the_case_page_shows_the_pending_signoff_step() -> None:
    stack = Stack()
    case = await _coded(stack)
    await stack.advance().handle(
        _context(SC_OPERATOR), case_id=case.id, action=ProductAction.SUBMIT_FOR_SIGNOFF
    )
    signoff = PendingReview(
        id=uuid.uuid4(),
        approval_type=SIGNOFF,
        payload={"product_dev_case_id": str(case.id), "step": "bod", "step_no": 1},
        created_at=NOW,
        required_scope="supply_chain.approve.bod",
    )
    stack.approvals.pending[(WORKSPACE, SIGNOFF, str(case.id))] = signoff
    # A stale review of the same case is not what a sign-off waits on.
    stack.approvals.pending[(WORKSPACE, BOD_REVIEW, str(case.id))] = replace(
        signoff, id=uuid.uuid4(), approval_type=BOD_REVIEW
    )

    detail = await stack.get().handle(_context(frozenset({PRODUCT_CASE_READ})), case.id)

    assert detail.pending_review is signoff
    assert detail.case.item_code is not None and detail.case.item_code.code == "MH-0001"
    assert [s.sku_code for s in detail.case.skus] == ["MH-0001-RED"]


# --- ĐẶT HÀNG (ticket 05) ----------------------------------------------------------------

_PO_DUTIES = load_supply_chain_action_duties(
    Path(__file__).resolve().parents[5]
    / "configs"
    / "policies"
    / "supply_chain_action_duties@1.2.0.yaml"
)


@dataclass
class FakeHolders:
    by_scope: dict[str, list[uuid.UUID]] = field(default_factory=dict)
    asked: list[frozenset[str]] = field(default_factory=list)

    async def holding(
        self, tenant_id: uuid.UUID, workspace_id: uuid.UUID, scopes: frozenset[str]
    ) -> list[uuid.UUID]:
        self.asked.append(scopes)
        return sorted({p for scope in scopes for p in self.by_scope.get(scope, [])})


@dataclass
class FakeNotifier:
    """Once per (person, source_key), as the inbox is; `fails` breaks it."""

    sent: dict[tuple[uuid.UUID, str], tuple[str, str | None]] = field(default_factory=dict)
    fails: bool = False

    async def deliver(
        self,
        context: AccessContext,
        *,
        recipients: Sequence[uuid.UUID],
        source_key: str,
        title: str,
        body: str,
        link: str | None,
    ) -> None:
        if self.fails:
            raise RuntimeError("inbox down")
        for person in recipients:
            self.sent.setdefault((person, source_key), (title, link))


def _place_order(
    stack: Stack, holders: FakeHolders | None = None, notifier: FakeNotifier | None = None
) -> PlaceOrder:
    return PlaceOrder(
        repo=stack.cases,
        authz=ScopeAuthorizationService(),
        policy_override_repo=stack.policies,
        platform_default_duties=DUTIES,
        platform_default_action_duties=_PO_DUTIES,
        holders=holders or FakeHolders(),
        notifier=notifier or FakeNotifier(),
        ids=Uuid4Generator(),
        clock=FixedClock(NOW),
    )


async def _ready_to_order(stack: Stack) -> ProductDevelopmentCase:
    """Coded, submitted, and signed: what the sign-off graph saves."""
    case = await _coded(stack)
    await stack.advance().handle(
        _context(SC_OPERATOR), case_id=case.id, action=ProductAction.SUBMIT_FOR_SIGNOFF
    )
    stored = await stack.cases.get(_context(SC_OPERATOR), case.id)
    assert stored is not None
    stored.signoff_approve(actor_id=uuid.uuid4())
    await stack.cases.save(_context(SC_OPERATOR), stored, audit=stack.cases.audits[-1])
    return stack.cases.rows[case.id.value]


async def test_place_order_opens_the_po_case_with_the_cases_stamps_and_tells_ordering() -> None:
    stack = Stack()
    case = await _ready_to_order(stack)
    colleague, clicker_id = uuid.uuid4(), uuid.uuid4()
    holders = FakeHolders(by_scope={ORDERING: [colleague, clicker_id]})
    notifier = FakeNotifier()

    placed = await _place_order(stack, holders, notifier).handle(
        _context(SC_OPERATOR, principal=clicker_id), case_id=case.id
    )

    assert placed.case.state is ProductDevState.ORDERED
    po = stack.cases.po_cases[placed.po_case.id.value]
    assert po.pic_user_id == case.pic_user_id != clicker_id
    assert (po.category, po.supplier_name, po.product_dev_case_id) == (
        case.category,
        case.supplier_name,
        case.id.value,
    )
    assert [(line.sku_id, line.quantity) for line in po.lines] == [(case.skus[0].id, 120)]
    product_audit, po_audit = stack.cases.audits[-2:]
    assert (product_audit.action, product_audit.resource_id) == (
        "supply_chain.product_case.place_order",
        str(case.id),
    )
    assert (po_audit.action, po_audit.resource_type, po_audit.resource_id) == (
        "supply_chain.po_case.order_requested",
        "po_case",
        str(po.id),
    )
    assert {a.actor_id.value for a in (product_audit, po_audit)} == {clicker_id}
    # The duty of `create_po` is told, less the clicker.
    assert holders.asked == [frozenset({ORDERING})]
    key = f"supply_chain.order_requested:{po.id}"
    assert list(notifier.sent) == [(colleague, key)]
    assert notifier.sent[(colleague, key)][1] == f"/supply-chain/po-cases/{po.id}"


async def test_who_is_told_follows_the_tenants_duty_for_create_po() -> None:
    """One owner: the PO policy of the tenant says who creates the PO, and so
    who hears there is one to create."""
    stack = Stack()
    shipped = _PO_DUTIES.model_dump(mode="json")
    stack.policies.stored[(TENANT, "supply_chain_action_duties")] = {
        **shipped,
        "action_duties": {**shipped["action_duties"], "create_po": "exceptions"},
    }
    case = await _ready_to_order(stack)
    holders = FakeHolders()

    await _place_order(stack, holders).handle(_context(SC_OPERATOR), case_id=case.id)

    assert holders.asked == [frozenset({EXCEPTIONS})]


async def test_a_failed_notice_does_not_undo_the_order() -> None:
    stack = Stack()
    case = await _ready_to_order(stack)

    placed = await _place_order(stack, notifier=FakeNotifier(fails=True)).handle(
        _context(SC_OPERATOR), case_id=case.id
    )

    assert stack.cases.rows[case.id.value].state is ProductDevState.ORDERED
    assert placed.po_case.id.value in stack.cases.po_cases


@dataclass
class _NeverRead(FakeCases):
    async def get(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> ProductDevelopmentCase | None:
        raise AssertionError("read before the duty was checked")


@pytest.mark.parametrize(
    "scopes", [SC_RND, SC_SUPPLY_LEAD, frozenset({PRODUCT_CASE_READ, PRODUCT_CASE_WRITE})]
)
async def test_place_order_needs_its_duty_before_the_case_is_read(scopes: frozenset[str]) -> None:
    stack = Stack(cases=_NeverRead())

    with pytest.raises(PermissionDeniedError):
        await _place_order(stack).handle(
            _context(scopes), case_id=ProductDevelopmentCaseId(uuid.uuid4())
        )


@pytest.mark.parametrize(
    ("tenant", "workspace"), [(OTHER_TENANT, WORKSPACE), (TENANT, OTHER_WORKSPACE)]
)
async def test_another_tenant_or_workspace_cannot_order_the_case(
    tenant: uuid.UUID, workspace: uuid.UUID
) -> None:
    stack = Stack(cases=LeakyCases())
    case = await _ready_to_order(stack)

    with pytest.raises(NotFoundError):
        await _place_order(stack).handle(
            _context(SC_OPERATOR, tenant=tenant, workspace=workspace), case_id=case.id
        )
    assert stack.cases.po_cases == {}


async def test_ordering_a_case_not_ready_is_a_409_and_opens_nothing() -> None:
    stack = Stack()
    case = await _coded(stack)

    with pytest.raises(ConflictError):
        await _place_order(stack).handle(_context(SC_OPERATOR), case_id=case.id)
    assert stack.cases.po_cases == {}
    assert stack.cases.rows[case.id.value].state is ProductDevState.ITEM_CODING


async def test_a_second_click_is_a_409() -> None:
    stack = Stack()
    case = await _ready_to_order(stack)
    order = _place_order(stack)
    await order.handle(_context(SC_OPERATOR), case_id=case.id)

    with pytest.raises(ConflictError):
        await order.handle(_context(SC_OPERATOR), case_id=case.id)
    assert len(stack.cases.po_cases) == 1


async def test_the_step_command_refuses_place_order_before_reading_anything() -> None:
    stack = Stack(cases=_NeverRead())
    with pytest.raises(DomainError, match="place_order"):
        await stack.advance().handle(
            _context(SC_OPERATOR),
            case_id=ProductDevelopmentCaseId(uuid.uuid4()),
            action=ProductAction.PLACE_ORDER,
        )


def test_place_order_takes_no_pic_from_its_caller() -> None:
    """The command has no parameter that could name a PIC, a Category or a
    supplier: the PO case takes them from the case."""
    assert set(inspect.signature(PlaceOrder.handle).parameters) == {"self", "context", "case_id"}


async def test_an_ordered_case_shows_its_po_case() -> None:
    stack = Stack()
    case = await _ready_to_order(stack)
    placed = await _place_order(stack).handle(_context(SC_OPERATOR), case_id=case.id)

    detail = await stack.get().handle(_context(SC_OPERATOR), case.id)

    assert detail.po_case_id == placed.po_case.id.value
    assert detail.case.available_actions() == frozenset()


# ---- the Category (ticket 06, ADR 0019) -----------------------------------------


async def test_propose_stamps_a_category_of_the_tenants_list_as_its_key() -> None:
    stack = Stack()
    case = await stack.propose().handle(
        _context(SC_OPERATOR), proposal_code="DX-1", product_name="Chảo 28", category="chao"
    )
    assert stack.cases.rows[case.id.value].category == "chao"


@pytest.mark.parametrize("category", ["Chảo", "ấm", "CHAO", "noi "])
async def test_propose_refuses_a_category_not_in_the_tenants_list(category: str) -> None:
    """Exactly a key of the list (a trailing space aside, as every field is
    trimmed): the web form sends a key; the chat resolves words to one first."""
    stack = Stack()
    if category.strip() == "noi":
        case = await stack.propose().handle(
            _context(SC_OPERATOR), proposal_code="DX-1", product_name="Nồi", category=category
        )
        assert case.category == "noi"
        return
    with pytest.raises(DomainError, match="Category") as refused:
        await stack.propose().handle(
            _context(SC_OPERATOR), proposal_code="DX-1", product_name="X", category=category
        )
    assert refused.value.details == {"field": "category", "category": category}
    assert stack.cases.rows == {}


async def test_the_category_list_is_the_callers_tenants_never_another() -> None:
    """Tenant B's own list has `bep_tu`; tenant A's (the platform's) does not.
    A proposes with it and is refused; B is not."""
    stack = Stack()
    stack.policies.stored[(OTHER_TENANT, "supply_chain_sla")] = {
        **SLA.model_dump(mode="json"),
        "categories": [{"key": "bep_tu", "label": "Bếp từ"}],
        "by_category": {},
    }
    with pytest.raises(DomainError):
        await stack.propose().handle(
            _context(SC_OPERATOR), proposal_code="DX-1", product_name="Bếp", category="bep_tu"
        )
    case = await stack.propose().handle(
        _context(SC_OPERATOR, tenant=OTHER_TENANT, workspace=OTHER_WORKSPACE),
        proposal_code="DX-1",
        product_name="Bếp",
        category="bep_tu",
    )
    assert case.category == "bep_tu"
    assert [c.key for c in await stack.propose().categories(_context(SC_OPERATOR))] == [
        "noi",
        "chao",
    ]


async def test_propose_checks_the_category_after_who_may() -> None:
    """A caller who may not propose learns nothing about the list."""
    stack = Stack()
    with pytest.raises(PermissionDeniedError):
        await stack.propose().handle(
            _context(SC_RND), proposal_code="DX-1", product_name="X", category="not-a-category"
        )


# ---- the case's SLA (ticket 06) -------------------------------------------------


async def test_the_case_page_carries_its_steps_sla_under_its_category() -> None:
    stack = Stack()
    case = await stack.profiling()
    # bm04 is 2 days in the shipped default; the case reached it at NOW.

    on_time = await stack.get(NOW + timedelta(days=1)).handle(_context(SC_OPERATOR), case.id)
    late = await stack.get(NOW + timedelta(days=3)).handle(_context(SC_OPERATOR), case.id)

    assert (on_time.sla.milestone, on_time.sla.status) == ("bm04", SLAEvaluationStatus.ON_TRACK)
    assert (late.sla.status, late.sla.age_days, late.sla.threshold_days) == (
        SLAEvaluationStatus.BREACHED,
        3,
        2,
    )


async def test_a_proposed_case_has_no_milestone() -> None:
    stack = Stack()
    case = await stack.proposed()
    detail = await stack.get(NOW + timedelta(days=30)).handle(_context(SC_OPERATOR), case.id)
    assert detail.sla.status is SLAEvaluationStatus.NOT_APPLICABLE


# ---- reassign_pic (ticket 06) ---------------------------------------------------


@dataclass
class FakeMembers:
    """`WorkspaceMembersPort`: who belongs to which workspace."""

    of: dict[uuid.UUID, set[uuid.UUID]] = field(default_factory=dict)

    async def members(
        self, tenant_id: uuid.UUID, workspace_id: uuid.UUID, user_ids: frozenset[uuid.UUID]
    ) -> frozenset[uuid.UUID]:
        return frozenset(user_ids & self.of.get(workspace_id, set()))


def _reassign(stack: Stack, members: FakeMembers) -> ReassignProductCasePic:
    return ReassignProductCasePic(
        repo=stack.cases,
        members=members,
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
        clock=FixedClock(NOW),
    )


async def test_tp_cung_ung_hands_the_case_to_a_member_with_a_reason_and_it_is_audited() -> None:
    stack = Stack()
    case = await stack.proposed()
    new_pic, lead = uuid.uuid4(), uuid.uuid4()

    changed = await _reassign(stack, FakeMembers({WORKSPACE: {new_pic}})).handle(
        _context(SC_SUPPLY_LEAD, principal=lead),
        case_id=case.id,
        new_pic=new_pic,
        reason="Chị Hà nghỉ phép",
    )

    assert changed.pic_user_id == new_pic
    assert stack.cases.rows[case.id.value].pic_user_id == new_pic
    assert stack.cases.rows[case.id.value].state is ProductDevState.PROPOSED
    audit = stack.cases.audits[-1]
    assert (audit.action, audit.actor_id.value) == ("supply_chain.product_case.reassign_pic", lead)
    assert audit.details == {
        "from_pic_user_id": str(case.pic_user_id),
        "to_pic_user_id": str(new_pic),
        "reason": "Chị Hà nghỉ phép",
    }


async def test_reassigning_needs_the_supply_lead_duty() -> None:
    stack = Stack()
    case = await stack.proposed()
    new_pic = uuid.uuid4()
    with pytest.raises(PermissionDeniedError):
        await _reassign(stack, FakeMembers({WORKSPACE: {new_pic}})).handle(
            _context(SC_OPERATOR), case_id=case.id, new_pic=new_pic, reason="x"
        )
    assert stack.cases.rows[case.id.value].pic_user_id == case.pic_user_id


@pytest.mark.parametrize("reason", [None, "", "   "])
async def test_reassigning_needs_a_reason(reason: str | None) -> None:
    stack = Stack()
    case = await stack.proposed()
    new_pic = uuid.uuid4()
    with pytest.raises(DomainError, match="reason"):
        await _reassign(stack, FakeMembers({WORKSPACE: {new_pic}})).handle(
            _context(SC_SUPPLY_LEAD), case_id=case.id, new_pic=new_pic, reason=reason
        )


async def test_the_new_pic_must_be_a_member_of_the_cases_workspace() -> None:
    """A member of another workspace of the same tenant is not one."""
    stack = Stack()
    case = await stack.proposed()
    elsewhere = uuid.uuid4()
    with pytest.raises(DomainError, match="member"):
        await _reassign(stack, FakeMembers({OTHER_WORKSPACE: {elsewhere}})).handle(
            _context(SC_SUPPLY_LEAD), case_id=case.id, new_pic=elsewhere, reason="x"
        )
    assert stack.cases.rows[case.id.value].pic_user_id == case.pic_user_id


@pytest.mark.parametrize(
    ("tenant", "workspace"), [(OTHER_TENANT, WORKSPACE), (TENANT, OTHER_WORKSPACE)]
)
async def test_another_tenant_or_workspace_cannot_reassign_the_case(
    tenant: uuid.UUID, workspace: uuid.UUID
) -> None:
    stack = Stack(cases=LeakyCases())
    case = await stack.proposed()
    new_pic = uuid.uuid4()
    with pytest.raises(NotFoundError):
        await _reassign(stack, FakeMembers({workspace: {new_pic}})).handle(
            _context(SC_SUPPLY_LEAD, tenant=tenant, workspace=workspace),
            case_id=case.id,
            new_pic=new_pic,
            reason="x",
        )
    assert stack.cases.rows[case.id.value].pic_user_id == case.pic_user_id


async def test_an_ordered_case_refuses_reassign_its_pic_lives_on_the_po_case() -> None:
    stack = Stack()
    case = await _ready_to_order(stack)
    placed = await _place_order(stack).handle(_context(SC_OPERATOR), case_id=case.id)
    new_pic = uuid.uuid4()

    with pytest.raises(ConflictError, match="ordered"):
        await _reassign(stack, FakeMembers({WORKSPACE: {new_pic}})).handle(
            _context(SC_SUPPLY_LEAD), case_id=case.id, new_pic=new_pic, reason="x"
        )
    assert stack.cases.rows[case.id.value].pic_user_id == case.pic_user_id
    assert stack.cases.po_cases[placed.po_case.id.value].pic_user_id == case.pic_user_id


async def test_reassigning_to_the_pic_it_has_is_refused() -> None:
    stack = Stack()
    case = await stack.proposed()
    with pytest.raises(DomainError, match="already"):
        await _reassign(stack, FakeMembers({WORKSPACE: {case.pic_user_id}})).handle(
            _context(SC_SUPPLY_LEAD), case_id=case.id, new_pic=case.pic_user_id, reason="x"
        )
