"""Unit: CreatePOCase / GetPOCase, without infrastructure.

`FakePOCaseRepository` is an in-memory dict — the point here is the
handler's own decisions (authorization, tenant/workspace sourced from the
verified context rather than caller input, not-found mapping), not
persistence, which `test_po_case_repository.py` (integration) already
covers against a real database.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.ports import ModelOutputInvalidError, ModelRequest, OutputT
from dw_kernel.errors import ConflictError, DomainError, NotFoundError, PermissionDeniedError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.pagination import CursorPosition, Page, PageRequest, build_page
from dw_kernel.ports import FixedClock, Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.domain.approval import ApprovalRequest
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.action_duties import (
    CaseDuty,
    SupplyChainActionDuties,
    load_supply_chain_action_duties,
)
from dw_supply_chain.application.handlers import (
    AdvancePOCase,
    AnalyzeDelayImpact,
    AnswerCaseQuery,
    CaseActionApplied,
    CaseActionPendingApproval,
    CreatePOCase,
    GetActionDuties,
    GetApprovalMatrix,
    GetAttentionQueue,
    GetBriefPolicy,
    GetDailyBrief,
    GetMissingUpdateStatus,
    GetPOCase,
    GetPortfolioSummary,
    GetProductActionDuties,
    GetSLAEvaluation,
    GetSLAPolicy,
    ListCaseTransitions,
    ListDelayImpactAnalyses,
    ListPOCases,
    ListSupplierUpdates,
    SetActionDutiesOverride,
    SetApprovalMatrixOverride,
    SetBriefPolicyOverride,
    SetProductActionDutiesOverride,
    SetSLAPolicyOverride,
    SubmitSupplierUpdate,
    SummarizeDailyBrief,
)
from dw_supply_chain.application.ports import PO_REFERENCE_PADDING, POCaseListFilter
from dw_supply_chain.approval_matrix import SupplyChainApprovalMatrix
from dw_supply_chain.brief_policy import SupplyChainBriefPolicy
from dw_supply_chain.domain.brief_summary import BriefSummaryDraft, BriefSummaryStatus
from dw_supply_chain.domain.case_query import CaseQueryOutcome, GroundedField
from dw_supply_chain.domain.daily_brief import BriefSignal
from dw_supply_chain.domain.delay_impact import (
    DelayImpactAnalysis,
    DelayImpactExtraction,
    MitigationOption,
)
from dw_supply_chain.domain.missing_update import MissingUpdateStatus
from dw_supply_chain.domain.po_case import (
    TERMINAL_STATES,
    CaseAction,
    CaseState,
    CaseTransition,
    POCase,
    POCaseId,
)
from dw_supply_chain.domain.sla_evaluation import SLAEvaluationStatus
from dw_supply_chain.domain.supplier_update import (
    SupplierEventType,
    SupplierUpdate,
    SupplierUpdateExtraction,
    SupplierUpdateId,
)
from dw_supply_chain.product_action_duties import load_supply_chain_product_action_duties
from dw_supply_chain.sla_policy import (
    SLAConfirmationStatus,
    SLAMilestone,
    SupplierUpdateCadence,
    SupplyChainSLAPolicy,
)

pytestmark = pytest.mark.unit

TENANT = uuid.uuid4()
WORKSPACE = uuid.uuid4()
# Every step's duty: the tests below that are not about duties take any
# step, the way a small company's one coordinator would.
_DUTY_SCOPES = frozenset(f"supply_chain.duty.{duty.value}" for duty in CaseDuty)
_BOTH_SCOPES = frozenset({"supply_chain.po_case.read", "supply_chain.po_case.write"}) | _DUTY_SCOPES
_SHIPPED_ACTION_DUTIES = (
    Path(__file__).resolve().parents[5]
    / "configs"
    / "policies"
    / "supply_chain_action_duties@1.0.0.yaml"
)
_SHIPPED_PRODUCT_ACTION_DUTIES = (
    Path(__file__).resolve().parents[5]
    / "configs"
    / "policies"
    / "supply_chain_product_action_duties@1.0.0.yaml"
)
_SUPPLIER_UPDATE_SCOPES = frozenset(
    {"supply_chain.supplier_update.read", "supply_chain.supplier_update.write"}
)
_DELAY_IMPACT_SCOPES = frozenset(
    {"supply_chain.delay_impact.read", "supply_chain.delay_impact.write"}
)


class FakePOCaseRepository:
    def __init__(self) -> None:
        self.by_id: dict[uuid.UUID, POCase] = {}
        # Test-controlled directly rather than derived from a simulated
        # transition history — the handler tests care about "what does the
        # evaluator see", not about reproducing the repository's own write
        # path, which test_po_case_repository.py already covers for real.
        self.current_state_entered_at: dict[uuid.UUID, datetime] = {}
        self.transitions: dict[uuid.UUID, list[CaseTransition]] = {}

    async def add(self, context: AccessContext, case: POCase) -> None:
        self.by_id[case.id.value] = case

    async def get(self, context: AccessContext, case_id: POCaseId) -> POCase | None:
        case = self.by_id.get(case_id.value)
        if case is None or case.tenant_id.value != context.tenant_id:
            return None
        return case

    async def save(self, context: AccessContext, case: POCase) -> None:
        self.by_id[case.id.value] = case

    async def get_current_state_entered_at(
        self, context: AccessContext, case_id: POCaseId
    ) -> datetime | None:
        return self.current_state_entered_at.get(case_id.value)

    async def list_page(
        self, context: AccessContext, request: PageRequest, case_filter: POCaseListFilter
    ) -> Page[POCase]:
        # Honors the same exact-match contract `SqlPOCaseRepository.list_page`
        # promises, not only what one assertion needs.
        cases = [
            c
            for c in self.by_id.values()
            if c.tenant_id.value == context.tenant_id
            and (case_filter.state is None or c.state is case_filter.state)
            and (case_filter.supplier_name is None or c.supplier_name == case_filter.supplier_name)
            and not (case_filter.active_only and c.state in TERMINAL_STATES)
        ]
        cases.sort(key=lambda c: (c.created_at, c.id.value), reverse=True)
        if request.after is not None:
            after = request.after
            cases = [
                c
                for c in cases
                if (c.created_at, c.id.value) < (after.sort_value, after.tiebreaker)
            ]
        return build_page(cases[: request.fetch_limit], request=request, position_of=_case_position)

    async def list_transitions(
        self, context: AccessContext, case_id: POCaseId
    ) -> list[CaseTransition]:
        return self.transitions.get(case_id.value, [])

    async def list_active(self, context: AccessContext) -> list[POCase]:
        return [
            c
            for c in self.by_id.values()
            if c.tenant_id.value == context.tenant_id and c.state not in TERMINAL_STATES
        ]

    async def list_supplier_names(self, context: AccessContext) -> list[str]:
        return sorted(
            {c.supplier_name for c in self.by_id.values() if c.tenant_id.value == context.tenant_id}
        )

    async def find_by_reference(self, context: AccessContext, po_reference: str) -> list[POCase]:
        wanted = po_reference.strip(PO_REFERENCE_PADDING).lower()
        return sorted(
            (
                c
                for c in self.by_id.values()
                if c.tenant_id.value == context.tenant_id
                and c.po_reference.strip(PO_REFERENCE_PADDING).lower() == wanted
            ),
            key=lambda c: c.po_reference,
        )

    async def bulk_current_state_entered_at(
        self, context: AccessContext, case_ids: list[POCaseId]
    ) -> dict[uuid.UUID, datetime]:
        return {
            case_id.value: self.current_state_entered_at[case_id.value]
            for case_id in case_ids
            if case_id.value in self.current_state_entered_at
        }

    async def get_many(self, context: AccessContext, case_ids: list[POCaseId]) -> list[POCase]:
        return [
            case
            for case_id in case_ids
            if (case := self.by_id.get(case_id.value)) is not None
            and case.tenant_id.value == context.tenant_id
        ]

    async def list_latest_transitions_since(
        self, context: AccessContext, since: datetime
    ) -> list[tuple[POCaseId, CaseTransition]]:
        # Honors the real contract: the caller's tenant only, each case's
        # latest transition inside the window, one per case.
        latest: list[tuple[POCaseId, CaseTransition]] = []
        for case_id, history in self.transitions.items():
            case = self.by_id.get(case_id)
            if case is None or case.tenant_id.value != context.tenant_id:
                continue
            inside = [t for t in history if t.occurred_at >= since]
            if inside:
                latest.append((case.id, max(inside, key=lambda t: t.occurred_at)))
        return latest


def _case_position(case: POCase) -> CursorPosition:
    assert case.created_at is not None
    return CursorPosition(sort_value=case.created_at, tiebreaker=case.id.value)


class _FixedIdGenerator:
    def __init__(self, fixed: uuid.UUID) -> None:
        self.fixed = fixed

    def new_uuid(self) -> uuid.UUID:
        return self.fixed


def _context(*, scopes: frozenset[str] = _BOTH_SCOPES) -> AccessContext:
    return AccessContext(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=scopes,
        plan_id="professional",
    )


async def test_create_opens_a_case_with_tenant_and_workspace_from_context() -> None:
    repo = FakePOCaseRepository()
    case_id = uuid.uuid4()
    handler = CreatePOCase(
        repo=repo, authz=ScopeAuthorizationService(), ids=_FixedIdGenerator(case_id)
    )

    case = await handler.handle(_context(), po_reference="PO-0001", supplier_name="Elmich Co.")

    assert case.id.value == case_id
    assert case.tenant_id.value == TENANT
    assert case.workspace_id.value == WORKSPACE
    assert repo.by_id[case_id] is case


async def test_create_refuses_without_the_write_scope() -> None:
    repo = FakePOCaseRepository()
    handler = CreatePOCase(
        repo=repo, authz=ScopeAuthorizationService(), ids=_FixedIdGenerator(uuid.uuid4())
    )
    context = _context(scopes=frozenset({"supply_chain.po_case.read"}))

    with pytest.raises(PermissionDeniedError):
        await handler.handle(context, po_reference="PO-0001", supplier_name="Elmich Co.")
    assert repo.by_id == {}


async def test_get_returns_the_case_it_was_created_with() -> None:
    repo = FakePOCaseRepository()
    context = _context()
    created = await CreatePOCase(
        repo=repo, authz=ScopeAuthorizationService(), ids=_FixedIdGenerator(uuid.uuid4())
    ).handle(context, po_reference="PO-0001", supplier_name="Elmich Co.")

    fetched = await GetPOCase(repo=repo, authz=ScopeAuthorizationService()).handle(
        context, created.id
    )
    assert fetched is created


async def test_get_raises_not_found_for_an_unknown_case() -> None:
    repo = FakePOCaseRepository()
    handler = GetPOCase(repo=repo, authz=ScopeAuthorizationService())
    with pytest.raises(NotFoundError):
        await handler.handle(_context(), POCaseId(uuid.uuid4()))


async def test_get_refuses_without_the_read_scope() -> None:
    repo = FakePOCaseRepository()
    handler = GetPOCase(repo=repo, authz=ScopeAuthorizationService())
    context = _context(scopes=frozenset())
    with pytest.raises(PermissionDeniedError):
        await handler.handle(context, POCaseId(uuid.uuid4()))


# -- ListPOCases --------------------------------------------------------------


async def test_list_returns_cases_newest_first() -> None:
    repo = FakePOCaseRepository()
    context = _context()
    older = await CreatePOCase(
        repo=repo, authz=ScopeAuthorizationService(), ids=_FixedIdGenerator(uuid.uuid4())
    ).handle(context, po_reference="PO-0001", supplier_name="Elmich Co.")
    older.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    newer = await CreatePOCase(
        repo=repo, authz=ScopeAuthorizationService(), ids=_FixedIdGenerator(uuid.uuid4())
    ).handle(context, po_reference="PO-0002", supplier_name="Elmich Co.")
    newer.created_at = datetime(2026, 2, 1, tzinfo=UTC)

    page = await ListPOCases(repo=repo, authz=ScopeAuthorizationService()).handle(
        context, POCaseListFilter(), limit=50, cursor=None
    )

    assert [c.id for c in page.items] == [newer.id, older.id]


async def test_list_only_returns_the_callers_tenant() -> None:
    repo = FakePOCaseRepository()
    mine = _context()
    other = AccessContext(
        tenant_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=_BOTH_SCOPES,
        plan_id="professional",
    )
    case = await CreatePOCase(
        repo=repo, authz=ScopeAuthorizationService(), ids=_FixedIdGenerator(uuid.uuid4())
    ).handle(mine, po_reference="PO-0001", supplier_name="Elmich Co.")
    case.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    await CreatePOCase(
        repo=repo, authz=ScopeAuthorizationService(), ids=_FixedIdGenerator(uuid.uuid4())
    ).handle(other, po_reference="PO-9999", supplier_name="Someone Else Co.")

    page = await ListPOCases(repo=repo, authz=ScopeAuthorizationService()).handle(
        mine, POCaseListFilter(), limit=50, cursor=None
    )

    assert [c.id for c in page.items] == [case.id]


async def test_list_refuses_without_the_read_scope() -> None:
    repo = FakePOCaseRepository()
    handler = ListPOCases(repo=repo, authz=ScopeAuthorizationService())
    context = _context(scopes=frozenset())
    with pytest.raises(PermissionDeniedError):
        await handler.handle(context, POCaseListFilter(), limit=50, cursor=None)


async def test_list_passes_the_filter_through_to_the_repository() -> None:
    repo = FakePOCaseRepository()
    context = _context()
    wanted = _case_with_created_at(context, created_at=_NOW, state=CaseState.WAITING_DEPOSIT)
    await repo.add(context, wanted)
    await repo.add(context, _case_with_created_at(context, created_at=_NOW))

    page = await ListPOCases(repo=repo, authz=ScopeAuthorizationService()).handle(
        context, POCaseListFilter(state=CaseState.WAITING_DEPOSIT), limit=50, cursor=None
    )

    assert [c.id for c in page.items] == [wanted.id]


class _NeverQueriedRepository(FakePOCaseRepository):
    async def list_page(
        self, context: AccessContext, request: PageRequest, case_filter: POCaseListFilter
    ) -> Page[POCase]:
        raise AssertionError("a filter that matches nothing must not reach the database")


@pytest.mark.parametrize("terminal", sorted(TERMINAL_STATES))
async def test_list_answers_a_contradictory_filter_without_querying(terminal: CaseState) -> None:
    """Active AND terminal is empty by definition — answered here, because
    the database would walk every terminal row to find that out."""
    handler = ListPOCases(repo=_NeverQueriedRepository(), authz=ScopeAuthorizationService())

    page = await handler.handle(
        _context(), POCaseListFilter(state=terminal, active_only=True), limit=50, cursor=None
    )

    assert page.items == ()
    assert page.next_cursor is None


async def test_list_checks_authorization_before_answering_a_contradictory_filter() -> None:
    handler = ListPOCases(repo=_NeverQueriedRepository(), authz=ScopeAuthorizationService())
    with pytest.raises(PermissionDeniedError):
        await handler.handle(
            _context(scopes=frozenset()),
            POCaseListFilter(state=CaseState.COMPLETED, active_only=True),
            limit=50,
            cursor=None,
        )


async def test_list_refuses_a_cursor_minted_under_a_different_filter() -> None:
    """The handler decodes the cursor against the filter it was given, so a
    caller cannot page one filter's SQL with another filter's position."""
    from dw_kernel.pagination import InvalidCursorError, encode_cursor

    minted_under = POCaseListFilter(supplier_name="Elmich Co.")
    cursor = encode_cursor(
        CursorPosition(sort_value=_NOW, tiebreaker=uuid.uuid4()),
        minted_under.page_query(TENANT),
    )
    handler = ListPOCases(repo=FakePOCaseRepository(), authz=ScopeAuthorizationService())

    with pytest.raises(InvalidCursorError):
        await handler.handle(
            _context(), POCaseListFilter(supplier_name="Other Co."), limit=50, cursor=cursor
        )


def test_an_unnarrowed_filter_keeps_the_cursor_identity_filters_never_had() -> None:
    """So a cursor minted before filters existed still resumes the plain
    listing, and "no filter" is never spelled as a value a caller could
    also send."""
    from dw_kernel.pagination import PageQuery

    assert POCaseListFilter().page_query(TENANT) == PageQuery(
        key="supply_chain.po_cases", filters={"tenant": TENANT}
    )


def test_every_narrowing_field_joins_the_cursor_identity() -> None:
    case_filter = POCaseListFilter(state=CaseState.QC, supplier_name="Elmich Co.", active_only=True)
    assert case_filter.page_query(TENANT).filters == {
        "tenant": TENANT,
        "state": CaseState.QC,
        "supplier_name": "Elmich Co.",
        "active_only": True,
    }


def test_a_supplier_literally_named_none_does_not_fingerprint_like_no_filter() -> None:
    def fingerprint(case_filter: POCaseListFilter) -> str:
        return case_filter.page_query(TENANT).fingerprint

    assert fingerprint(POCaseListFilter(supplier_name="None")) != fingerprint(POCaseListFilter())


# -- SubmitSupplierUpdate / ListSupplierUpdates ------------------------------


class FakeSupplierUpdateRepository:
    def __init__(self) -> None:
        self.by_case: dict[uuid.UUID, list[SupplierUpdate]] = {}

    async def add(self, context: AccessContext, update: SupplierUpdate) -> None:
        self.by_case.setdefault(update.po_case_id.value, []).append(update)

    async def get(
        self, context: AccessContext, update_id: SupplierUpdateId
    ) -> SupplierUpdate | None:
        for updates in self.by_case.values():
            for update in updates:
                if update.id.value == update_id.value:
                    return update
        return None

    async def list_for_case(
        self, context: AccessContext, po_case_id: POCaseId
    ) -> list[SupplierUpdate]:
        return list(reversed(self.by_case.get(po_case_id.value, [])))

    async def bulk_latest(
        self, context: AccessContext, case_ids: list[POCaseId]
    ) -> dict[uuid.UUID, SupplierUpdate]:
        result: dict[uuid.UUID, SupplierUpdate] = {}
        for case_id in case_ids:
            stamped = [u for u in self.by_case.get(case_id.value, []) if u.created_at is not None]
            if stamped:
                result[case_id.value] = max(stamped, key=lambda u: (u.created_at, u.id.value))
        return result


class FakeModelGateway:
    """Returns a fixed extraction regardless of the request — the point of
    these tests is the handler's own decisions (does it check the case
    exists first, does it compute requires_confirmation, does authorization
    gate it), not the model call itself, which
    test_supplier_update_understanding.py/test_delay_impact_analysis.py
    already cover for real.

    Generic in `output_type` only to satisfy `ModelGateway`'s own generic
    Protocol shape structurally; `extraction`'s declared type (`object`,
    narrowed by whichever handler actually calls this) is intentionally
    loose so the same fake serves both `SupplierUpdateExtraction` and
    `DelayImpactExtraction` callers — the cast back to `OutputT` is safe in
    practice even though mypy cannot prove it for an arbitrary caller."""

    def __init__(self, extraction: object) -> None:
        self.extraction = extraction
        self.calls: list[tuple[ModelRequest, RunContext]] = []

    async def generate_structured(
        self, request: ModelRequest, output_type: type[OutputT], *, run_context: RunContext
    ) -> OutputT:
        self.calls.append((request, run_context))
        return self.extraction  # type: ignore[return-value]


def _extraction(**overrides: object) -> SupplierUpdateExtraction:
    defaults: dict[str, object] = {
        "event_type": SupplierEventType.PRODUCTION_DELAY,
        "reason": "component shortage",
        "proposed_action": "split shipment",
        "confidence": 0.95,
        "source_ref": "delayed by 7 days",
    }
    defaults.update(overrides)
    return SupplierUpdateExtraction(**defaults)


async def _seeded_case(repo: FakePOCaseRepository, context: AccessContext) -> POCase:
    case = await CreatePOCase(
        repo=repo, authz=ScopeAuthorizationService(), ids=Uuid4Generator()
    ).handle(context, po_reference="PO-0001", supplier_name="Elmich Co.")
    return case


async def test_submit_reads_the_case_first_and_persists_the_extraction() -> None:
    po_case_repo = FakePOCaseRepository()
    context = _context(scopes=_BOTH_SCOPES | _SUPPLIER_UPDATE_SCOPES)
    case = await _seeded_case(po_case_repo, context)

    supplier_update_repo = FakeSupplierUpdateRepository()
    gateway = FakeModelGateway(_extraction())
    handler = SubmitSupplierUpdate(
        po_case_repo=po_case_repo,
        supplier_update_repo=supplier_update_repo,
        gateway=gateway,
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
    )

    update = await handler.handle(
        context, po_case_id=case.id, raw_text="we will be delayed by 7 days"
    )

    assert update.po_case_id == case.id
    assert update.extraction.event_type is SupplierEventType.PRODUCTION_DELAY
    assert not update.requires_confirmation
    assert supplier_update_repo.by_case[case.id.value] == [update]
    assert len(gateway.calls) == 1


async def test_submit_computes_requires_confirmation_from_the_real_domain_rule() -> None:
    """Not re-implemented in the handler — a low-confidence extraction must
    come back flagged, proving the handler actually calls
    `domain.supplier_update.requires_confirmation` rather than always
    returning `False`."""
    po_case_repo = FakePOCaseRepository()
    context = _context(scopes=_BOTH_SCOPES | _SUPPLIER_UPDATE_SCOPES)
    case = await _seeded_case(po_case_repo, context)

    supplier_update_repo = FakeSupplierUpdateRepository()
    gateway = FakeModelGateway(_extraction(confidence=0.1))
    handler = SubmitSupplierUpdate(
        po_case_repo=po_case_repo,
        supplier_update_repo=supplier_update_repo,
        gateway=gateway,
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
    )

    update = await handler.handle(context, po_case_id=case.id, raw_text="delayed by 7 days")
    assert update.requires_confirmation


async def test_submit_refuses_for_an_unknown_case_without_calling_the_model() -> None:
    po_case_repo = FakePOCaseRepository()
    supplier_update_repo = FakeSupplierUpdateRepository()
    gateway = FakeModelGateway(_extraction())
    handler = SubmitSupplierUpdate(
        po_case_repo=po_case_repo,
        supplier_update_repo=supplier_update_repo,
        gateway=gateway,
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
    )

    with pytest.raises(NotFoundError):
        await handler.handle(
            _context(scopes=_SUPPLIER_UPDATE_SCOPES),
            po_case_id=POCaseId(uuid.uuid4()),
            raw_text="irrelevant",
        )
    # The expensive/external call never happens for a case that does not exist.
    assert gateway.calls == []
    assert supplier_update_repo.by_case == {}


async def test_submit_refuses_without_the_write_scope() -> None:
    po_case_repo = FakePOCaseRepository()
    context = _context(scopes=_BOTH_SCOPES | _SUPPLIER_UPDATE_SCOPES)
    case = await _seeded_case(po_case_repo, context)

    gateway = FakeModelGateway(_extraction())
    handler = SubmitSupplierUpdate(
        po_case_repo=po_case_repo,
        supplier_update_repo=FakeSupplierUpdateRepository(),
        gateway=gateway,
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
    )

    denied_context = _context(scopes=_BOTH_SCOPES)  # no supplier_update.write
    with pytest.raises(PermissionDeniedError):
        await handler.handle(denied_context, po_case_id=case.id, raw_text="irrelevant")
    assert gateway.calls == []


async def test_list_returns_updates_newest_first_via_the_repository() -> None:
    po_case_repo = FakePOCaseRepository()
    context = _context(scopes=_BOTH_SCOPES | _SUPPLIER_UPDATE_SCOPES)
    case = await _seeded_case(po_case_repo, context)

    supplier_update_repo = FakeSupplierUpdateRepository()
    submit = SubmitSupplierUpdate(
        po_case_repo=po_case_repo,
        supplier_update_repo=supplier_update_repo,
        gateway=FakeModelGateway(_extraction()),
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
    )
    first = await submit.handle(context, po_case_id=case.id, raw_text="update one")
    second = await submit.handle(context, po_case_id=case.id, raw_text="update two")

    listed = await ListSupplierUpdates(
        po_case_repo=po_case_repo,
        supplier_update_repo=supplier_update_repo,
        authz=ScopeAuthorizationService(),
    ).handle(context, case.id)

    assert [update.id for update in listed] == [second.id, first.id]


async def test_list_refuses_for_an_unknown_case() -> None:
    po_case_repo = FakePOCaseRepository()
    handler = ListSupplierUpdates(
        po_case_repo=po_case_repo,
        supplier_update_repo=FakeSupplierUpdateRepository(),
        authz=ScopeAuthorizationService(),
    )
    with pytest.raises(NotFoundError):
        await handler.handle(_context(scopes=_SUPPLIER_UPDATE_SCOPES), POCaseId(uuid.uuid4()))


# -- AnalyzeDelayImpact / ListDelayImpactAnalyses ----------------------------


class FakeDelayImpactAnalysisRepository:
    def __init__(self) -> None:
        self.by_case: dict[uuid.UUID, list[DelayImpactAnalysis]] = {}

    async def add(self, context: AccessContext, analysis: DelayImpactAnalysis) -> None:
        self.by_case.setdefault(analysis.po_case_id.value, []).append(analysis)

    async def list_for_case(
        self, context: AccessContext, po_case_id: POCaseId
    ) -> list[DelayImpactAnalysis]:
        return list(reversed(self.by_case.get(po_case_id.value, [])))


def _delay_extraction() -> DelayImpactExtraction:
    return DelayImpactExtraction(
        assumptions=["no further change to the production schedule"],
        mitigation_options=[
            MitigationOption(description="split shipment", tradeoff="higher freight cost")
        ],
    )


# (method name, state reached) in happy-path order — used to advance a case
# to a target state through its own real guarded transitions, never by
# setting `.state` directly, even in test setup.
_HAPPY_PATH_METHODS: list[tuple[str, CaseState]] = [
    ("request_deposit", CaseState.WAITING_DEPOSIT),
    ("confirm_deposit", CaseState.DEPOSIT_CONFIRMED),
    ("start_pre_production", CaseState.PRE_PRODUCTION),
    ("start_production", CaseState.PRODUCTION),
    ("send_to_qc", CaseState.QC),
    ("pass_qc", CaseState.IN_TRANSIT),
    ("arrive_at_port", CaseState.ARRIVED_PORT),
    ("request_final_payment", CaseState.WAITING_PAYMENT),
    ("confirm_payment", CaseState.PAYMENT_COMPLETED),
    ("start_warehouse_receiving", CaseState.WAREHOUSE_RECEIVING),
    ("complete", CaseState.COMPLETED),
]


def _advance_case_to(case: POCase, target: CaseState) -> None:
    if target is CaseState.PO_CREATED:
        return
    for method_name, reached in _HAPPY_PATH_METHODS:
        getattr(case, method_name)()
        if reached is target:
            return
    raise AssertionError(f"{target} is not reachable via the happy path")


async def _seeded_case_with_delay_update(
    po_case_repo: FakePOCaseRepository,
    supplier_update_repo: FakeSupplierUpdateRepository,
    context: AccessContext,
    *,
    state: CaseState = CaseState.PRODUCTION,
    delay_days: int | None = 7,
) -> tuple[POCase, SupplierUpdate]:
    case = await CreatePOCase(
        repo=po_case_repo, authz=ScopeAuthorizationService(), ids=Uuid4Generator()
    ).handle(context, po_reference="PO-0001", supplier_name="Elmich Co.")
    _advance_case_to(case, state)
    await po_case_repo.save(context, case)
    gateway = FakeModelGateway(_extraction(delay_days=delay_days))
    update = await SubmitSupplierUpdate(
        po_case_repo=po_case_repo,
        supplier_update_repo=supplier_update_repo,
        gateway=gateway,
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
    ).handle(context, po_case_id=case.id, raw_text="we will be delayed by 7 days")
    return case, update


async def test_analyze_persists_impacted_milestones_and_the_models_extraction() -> None:
    po_case_repo, supplier_update_repo = FakePOCaseRepository(), FakeSupplierUpdateRepository()
    context = _context(scopes=_BOTH_SCOPES | _SUPPLIER_UPDATE_SCOPES | _DELAY_IMPACT_SCOPES)
    case, update = await _seeded_case_with_delay_update(po_case_repo, supplier_update_repo, context)

    delay_impact_repo = FakeDelayImpactAnalysisRepository()
    handler = AnalyzeDelayImpact(
        po_case_repo=po_case_repo,
        supplier_update_repo=supplier_update_repo,
        delay_impact_repo=delay_impact_repo,
        gateway=FakeModelGateway(_delay_extraction()),
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
    )

    analysis = await handler.handle(context, po_case_id=case.id, supplier_update_id=update.id)

    assert analysis.delay_days == 7
    assert [e.milestone for e in analysis.impacted_milestones] == [
        CaseState.QC,
        CaseState.IN_TRANSIT,
        CaseState.ARRIVED_PORT,
        CaseState.WAITING_PAYMENT,
        CaseState.PAYMENT_COMPLETED,
        CaseState.WAREHOUSE_RECEIVING,
        CaseState.COMPLETED,
    ]
    assert all(e.estimated_delay_days == 7 for e in analysis.impacted_milestones)
    assert analysis.extraction.assumptions == _delay_extraction().assumptions
    assert delay_impact_repo.by_case[case.id.value] == [analysis]


async def test_analyze_refuses_for_an_unknown_case() -> None:
    handler = AnalyzeDelayImpact(
        po_case_repo=FakePOCaseRepository(),
        supplier_update_repo=FakeSupplierUpdateRepository(),
        delay_impact_repo=FakeDelayImpactAnalysisRepository(),
        gateway=FakeModelGateway(_delay_extraction()),
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
    )
    with pytest.raises(NotFoundError):
        await handler.handle(
            _context(scopes=_DELAY_IMPACT_SCOPES),
            po_case_id=POCaseId(uuid.uuid4()),
            supplier_update_id=SupplierUpdateId(uuid.uuid4()),
        )


async def test_analyze_refuses_when_the_update_belongs_to_a_different_case() -> None:
    po_case_repo, supplier_update_repo = FakePOCaseRepository(), FakeSupplierUpdateRepository()
    context = _context(scopes=_BOTH_SCOPES | _SUPPLIER_UPDATE_SCOPES | _DELAY_IMPACT_SCOPES)
    _, update = await _seeded_case_with_delay_update(po_case_repo, supplier_update_repo, context)
    other_case = await CreatePOCase(
        repo=po_case_repo, authz=ScopeAuthorizationService(), ids=Uuid4Generator()
    ).handle(context, po_reference="PO-0002", supplier_name="Elmich Co.")

    handler = AnalyzeDelayImpact(
        po_case_repo=po_case_repo,
        supplier_update_repo=supplier_update_repo,
        delay_impact_repo=FakeDelayImpactAnalysisRepository(),
        gateway=FakeModelGateway(_delay_extraction()),
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
    )
    with pytest.raises(NotFoundError):
        # update belongs to the FIRST case, named against other_case here.
        await handler.handle(context, po_case_id=other_case.id, supplier_update_id=update.id)


async def test_analyze_refuses_an_update_with_no_delay_to_analyze() -> None:
    po_case_repo, supplier_update_repo = FakePOCaseRepository(), FakeSupplierUpdateRepository()
    context = _context(scopes=_BOTH_SCOPES | _SUPPLIER_UPDATE_SCOPES | _DELAY_IMPACT_SCOPES)
    case, update = await _seeded_case_with_delay_update(
        po_case_repo, supplier_update_repo, context, delay_days=None
    )

    handler = AnalyzeDelayImpact(
        po_case_repo=po_case_repo,
        supplier_update_repo=supplier_update_repo,
        delay_impact_repo=FakeDelayImpactAnalysisRepository(),
        gateway=FakeModelGateway(_delay_extraction()),
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
    )
    with pytest.raises(DomainError, match="no delay"):
        await handler.handle(context, po_case_id=case.id, supplier_update_id=update.id)


async def test_analyze_refuses_a_case_with_no_downstream_milestones_left() -> None:
    po_case_repo, supplier_update_repo = FakePOCaseRepository(), FakeSupplierUpdateRepository()
    context = _context(scopes=_BOTH_SCOPES | _SUPPLIER_UPDATE_SCOPES | _DELAY_IMPACT_SCOPES)
    case, update = await _seeded_case_with_delay_update(
        po_case_repo, supplier_update_repo, context, state=CaseState.COMPLETED
    )

    handler = AnalyzeDelayImpact(
        po_case_repo=po_case_repo,
        supplier_update_repo=supplier_update_repo,
        delay_impact_repo=FakeDelayImpactAnalysisRepository(),
        gateway=FakeModelGateway(_delay_extraction()),
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
    )
    with pytest.raises(DomainError, match="no downstream milestones"):
        await handler.handle(context, po_case_id=case.id, supplier_update_id=update.id)


async def test_analyze_refuses_without_the_write_scope() -> None:
    po_case_repo, supplier_update_repo = FakePOCaseRepository(), FakeSupplierUpdateRepository()
    context = _context(scopes=_BOTH_SCOPES | _SUPPLIER_UPDATE_SCOPES | _DELAY_IMPACT_SCOPES)
    case, update = await _seeded_case_with_delay_update(po_case_repo, supplier_update_repo, context)

    gateway = FakeModelGateway(_delay_extraction())
    handler = AnalyzeDelayImpact(
        po_case_repo=po_case_repo,
        supplier_update_repo=supplier_update_repo,
        delay_impact_repo=FakeDelayImpactAnalysisRepository(),
        gateway=gateway,
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
    )
    denied_context = _context(
        scopes=_BOTH_SCOPES | _SUPPLIER_UPDATE_SCOPES
    )  # no delay_impact.write
    with pytest.raises(PermissionDeniedError):
        await handler.handle(denied_context, po_case_id=case.id, supplier_update_id=update.id)
    assert gateway.calls == []


async def test_list_delay_impact_analyses_newest_first() -> None:
    po_case_repo, supplier_update_repo = FakePOCaseRepository(), FakeSupplierUpdateRepository()
    context = _context(scopes=_BOTH_SCOPES | _SUPPLIER_UPDATE_SCOPES | _DELAY_IMPACT_SCOPES)
    case, update = await _seeded_case_with_delay_update(po_case_repo, supplier_update_repo, context)

    delay_impact_repo = FakeDelayImpactAnalysisRepository()
    analyze = AnalyzeDelayImpact(
        po_case_repo=po_case_repo,
        supplier_update_repo=supplier_update_repo,
        delay_impact_repo=delay_impact_repo,
        gateway=FakeModelGateway(_delay_extraction()),
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
    )
    first = await analyze.handle(context, po_case_id=case.id, supplier_update_id=update.id)
    second = await analyze.handle(context, po_case_id=case.id, supplier_update_id=update.id)

    listed = await ListDelayImpactAnalyses(
        po_case_repo=po_case_repo,
        delay_impact_repo=delay_impact_repo,
        authz=ScopeAuthorizationService(),
    ).handle(context, case.id)

    assert [a.id for a in listed] == [second.id, first.id]


async def test_list_delay_impact_analyses_refuses_for_an_unknown_case() -> None:
    handler = ListDelayImpactAnalyses(
        po_case_repo=FakePOCaseRepository(),
        delay_impact_repo=FakeDelayImpactAnalysisRepository(),
        authz=ScopeAuthorizationService(),
    )
    with pytest.raises(NotFoundError):
        await handler.handle(_context(scopes=_DELAY_IMPACT_SCOPES), POCaseId(uuid.uuid4()))


# -- GetMissingUpdateStatus ---------------------------------------------------

_NOW = datetime(2026, 9, 24, tzinfo=UTC)


def _case_with_created_at(
    context: AccessContext, *, created_at: datetime, state: CaseState = CaseState.PO_CREATED
) -> POCase:
    # Built directly, not through CreatePOCase: the fakes above don't
    # simulate a DB-assigned created_at the way the real SqlPOCaseRepository
    # does, and this handler's own behaviour is what's under test here.
    case = POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        po_reference="PO-0001",
        supplier_name="Elmich Co.",
        created_at=created_at,
    )
    _advance_case_to(case, state)
    return case


def _supplier_update_at(case: POCase, *, created_at: datetime) -> SupplierUpdate:
    return SupplierUpdate(
        id=SupplierUpdateId(uuid.uuid4()),
        tenant_id=case.tenant_id,
        workspace_id=case.workspace_id,
        po_case_id=case.id,
        raw_text="all quiet, on schedule",
        extraction=_extraction(),
        requires_confirmation=False,
        created_at=created_at,
    )


async def test_missing_update_status_uses_the_latest_supplier_update_as_reference() -> None:
    po_case_repo, supplier_update_repo = FakePOCaseRepository(), FakeSupplierUpdateRepository()
    context = _context(scopes=_BOTH_SCOPES | _SUPPLIER_UPDATE_SCOPES)
    case = _case_with_created_at(
        context, created_at=_NOW - timedelta(days=30), state=CaseState.PRODUCTION
    )
    await po_case_repo.add(context, case)
    # Older update first, then a fresher one — the handler must pick the
    # fresher one as the reference, not merely "the first one it sees".
    older = _supplier_update_at(case, created_at=_NOW - timedelta(days=20))
    newer = _supplier_update_at(case, created_at=_NOW - timedelta(days=3))
    await supplier_update_repo.add(context, older)
    await supplier_update_repo.add(context, newer)

    handler = GetMissingUpdateStatus(
        po_case_repo=po_case_repo,
        supplier_update_repo=supplier_update_repo,
        policy_override_repo=FakePolicyOverrideRepository(),
        platform_default_policy=_sla_policy(),
        authz=ScopeAuthorizationService(),
        clock=FixedClock(_NOW),
    )
    assessment = await handler.handle(context, case.id)

    assert assessment.status is MissingUpdateStatus.ON_TRACK
    assert assessment.age_days == 3
    assert assessment.reference_at == _NOW - timedelta(days=3)


async def test_missing_update_status_falls_back_to_case_creation_when_no_updates_exist() -> None:
    po_case_repo, supplier_update_repo = FakePOCaseRepository(), FakeSupplierUpdateRepository()
    context = _context(scopes=_BOTH_SCOPES | _SUPPLIER_UPDATE_SCOPES)
    case = _case_with_created_at(
        context, created_at=_NOW - timedelta(days=15), state=CaseState.PRODUCTION
    )
    await po_case_repo.add(context, case)

    handler = GetMissingUpdateStatus(
        po_case_repo=po_case_repo,
        supplier_update_repo=supplier_update_repo,
        policy_override_repo=FakePolicyOverrideRepository(),
        platform_default_policy=_sla_policy(),
        authz=ScopeAuthorizationService(),
        clock=FixedClock(_NOW),
    )
    assessment = await handler.handle(context, case.id)

    assert assessment.status is MissingUpdateStatus.ESCALATION_DUE
    assert assessment.age_days == 15
    assert assessment.reference_at == case.created_at


async def test_missing_update_status_reads_the_tenants_own_cadence() -> None:
    """A case two days quiet is on track under the default (5d/10d) and due
    for escalation under a tenant that escalates after one day."""
    po_case_repo, supplier_update_repo = FakePOCaseRepository(), FakeSupplierUpdateRepository()
    context = _context(scopes=_BOTH_SCOPES | _SUPPLIER_UPDATE_SCOPES)
    case = _case_with_created_at(
        context, created_at=_NOW - timedelta(days=2), state=CaseState.PRODUCTION
    )
    await po_case_repo.add(context, case)
    overrides = FakePolicyOverrideRepository()
    handler = GetMissingUpdateStatus(
        po_case_repo=po_case_repo,
        supplier_update_repo=supplier_update_repo,
        policy_override_repo=overrides,
        platform_default_policy=_sla_policy(),
        authz=ScopeAuthorizationService(),
        clock=FixedClock(_NOW),
    )
    assert (await handler.handle(context, case.id)).status is MissingUpdateStatus.ON_TRACK

    tenant_policy = _sla_policy().model_copy(
        update={
            "supplier_update": SupplierUpdateCadence(reminder_after="0d", escalation_after="1d")
        }
    )
    await overrides.put(
        context,
        "supply_chain_sla",
        tenant_policy.model_dump(mode="json"),
        audit=_audit_event(context),
    )
    assert (await handler.handle(context, case.id)).status is MissingUpdateStatus.ESCALATION_DUE


async def test_missing_update_status_refuses_for_an_unknown_case() -> None:
    handler = GetMissingUpdateStatus(
        po_case_repo=FakePOCaseRepository(),
        supplier_update_repo=FakeSupplierUpdateRepository(),
        policy_override_repo=FakePolicyOverrideRepository(),
        platform_default_policy=_sla_policy(),
        authz=ScopeAuthorizationService(),
        clock=FixedClock(_NOW),
    )
    with pytest.raises(NotFoundError):
        await handler.handle(_context(scopes=_BOTH_SCOPES), POCaseId(uuid.uuid4()))


async def test_missing_update_status_refuses_without_the_read_scope() -> None:
    po_case_repo, supplier_update_repo = FakePOCaseRepository(), FakeSupplierUpdateRepository()
    context = _context(scopes=_BOTH_SCOPES | _SUPPLIER_UPDATE_SCOPES)
    case = _case_with_created_at(context, created_at=_NOW)
    await po_case_repo.add(context, case)

    handler = GetMissingUpdateStatus(
        po_case_repo=po_case_repo,
        supplier_update_repo=supplier_update_repo,
        policy_override_repo=FakePolicyOverrideRepository(),
        platform_default_policy=_sla_policy(),
        authz=ScopeAuthorizationService(),
        clock=FixedClock(_NOW),
    )
    with pytest.raises(PermissionDeniedError):
        await handler.handle(_context(scopes=frozenset()), case.id)


# -- AdvancePOCase ------------------------------------------------------------


class FakeWorkflowRunnerPort:
    """`AdvancePOCase` only ever calls `.start()` — an approval-gated action
    starts a run and returns, it never resumes one itself (that is
    `ApproveAndResumeService`'s job, exercised by the real integration test
    against `workflows/advance_case_graph.py`, not by this fake)."""

    def __init__(self) -> None:
        self.started: list[tuple[RunContext, dict[str, object]]] = []

    def hosts(self, *, worker_id: str, worker_version: str, graph_version: str) -> bool:
        return True

    async def start(
        self, *, run_context: RunContext, input_payload: dict[str, object]
    ) -> uuid.UUID:
        self.started.append((run_context, input_payload))
        return run_context.run_id

    async def resume(
        self, *, run_context: RunContext, run_id: uuid.UUID, resume_payload: dict[str, object]
    ) -> None:
        raise NotImplementedError("not exercised by AdvancePOCase")


def _approval_matrix(**overrides: object) -> SupplyChainApprovalMatrix:
    defaults: dict[str, object] = {
        "schema_version": "1.0",
        "policy_id": "supply_chain_approval_matrix",
        "policy_version": "1.0.0",
    }
    defaults.update(overrides)
    return SupplyChainApprovalMatrix(**defaults)


def _advance_po_case_handler(
    po_case_repo: FakePOCaseRepository,
    *,
    policy_override_repo: FakePolicyOverrideRepository | None = None,
    platform_default_approval_matrix: SupplyChainApprovalMatrix | None = None,
    platform_default_action_duties: SupplyChainActionDuties | None = None,
    runner: FakeWorkflowRunnerPort | None = None,
) -> AdvancePOCase:
    return AdvancePOCase(
        repo=po_case_repo,
        authz=ScopeAuthorizationService(),
        policy_override_repo=policy_override_repo or FakePolicyOverrideRepository(),
        platform_default_approval_matrix=platform_default_approval_matrix or _approval_matrix(),
        platform_default_action_duties=platform_default_action_duties
        or load_supply_chain_action_duties(_SHIPPED_ACTION_DUTIES),
        runner=runner or FakeWorkflowRunnerPort(),
        ids=Uuid4Generator(),
    )


async def test_advance_dispatches_a_no_reason_action_and_persists_it() -> None:
    po_case_repo = FakePOCaseRepository()
    context = _context()
    case = await _seeded_case(po_case_repo, context)

    handler = _advance_po_case_handler(po_case_repo)
    result = await handler.handle(context, po_case_id=case.id, action=CaseAction.REQUEST_DEPOSIT)

    assert isinstance(result, CaseActionApplied)
    assert result.case.state is CaseState.WAITING_DEPOSIT
    assert po_case_repo.by_id[case.id.value].state is CaseState.WAITING_DEPOSIT


async def test_advance_dispatches_a_reason_required_action() -> None:
    po_case_repo = FakePOCaseRepository()
    context = _context()
    case = await _seeded_case(po_case_repo, context)

    handler = _advance_po_case_handler(po_case_repo)
    result = await handler.handle(
        context, po_case_id=case.id, action=CaseAction.CANCEL, reason="customer walked away"
    )

    assert isinstance(result, CaseActionApplied)
    assert result.case.state is CaseState.CANCELLED


@pytest.mark.parametrize("reason", [None, "", "   "])
async def test_advance_refuses_a_reason_required_action_without_a_real_reason(
    reason: str | None,
) -> None:
    po_case_repo = FakePOCaseRepository()
    context = _context()
    case = await _seeded_case(po_case_repo, context)

    handler = _advance_po_case_handler(po_case_repo)
    with pytest.raises(DomainError, match="reason"):
        await handler.handle(context, po_case_id=case.id, action=CaseAction.CANCEL, reason=reason)
    # Refused before dispatch: the case must be untouched.
    assert po_case_repo.by_id[case.id.value].state is CaseState.PO_CREATED


async def test_advance_refuses_an_illegal_transition() -> None:
    po_case_repo = FakePOCaseRepository()
    context = _context()
    case = await _seeded_case(po_case_repo, context)  # still PO_CREATED

    handler = _advance_po_case_handler(po_case_repo)
    with pytest.raises(ConflictError):
        # cannot confirm a deposit that was never requested
        await handler.handle(context, po_case_id=case.id, action=CaseAction.CONFIRM_DEPOSIT)


async def test_advance_refuses_for_an_unknown_case() -> None:
    handler = _advance_po_case_handler(FakePOCaseRepository())
    with pytest.raises(NotFoundError):
        await handler.handle(
            _context(), po_case_id=POCaseId(uuid.uuid4()), action=CaseAction.REQUEST_DEPOSIT
        )


async def test_advance_starts_a_run_instead_of_applying_when_approval_is_required() -> None:
    po_case_repo = FakePOCaseRepository()
    context = _context()
    case = await _seeded_case(po_case_repo, context)
    runner = FakeWorkflowRunnerPort()
    matrix = _approval_matrix(approval_required_actions=frozenset({CaseAction.CANCEL}))

    handler = _advance_po_case_handler(
        po_case_repo, platform_default_approval_matrix=matrix, runner=runner
    )
    result = await handler.handle(
        context, po_case_id=case.id, action=CaseAction.CANCEL, reason="customer walked away"
    )

    assert isinstance(result, CaseActionPendingApproval)
    assert result.run_id is not None
    # Nothing applied — the case is exactly as it was, not cancelled yet.
    assert po_case_repo.by_id[case.id.value].state is CaseState.PO_CREATED
    assert len(runner.started) == 1
    started_context, payload = runner.started[0]
    assert started_context.run_id == result.run_id
    assert payload == {
        "po_case_id": str(case.id),
        "action": "cancel",
        "reason": "customer walked away",
    }


async def test_advance_uses_the_tenants_own_approval_matrix_override() -> None:
    """The platform default requires nothing; the tenant's own override adds
    CANCEL. If the handler used the platform default instead of resolving
    the override, this would apply directly instead of starting a run."""
    po_case_repo = FakePOCaseRepository()
    context = _context()
    case = await _seeded_case(po_case_repo, context)
    policy_override_repo = FakePolicyOverrideRepository()
    tenant_matrix = _approval_matrix(approval_required_actions=frozenset({CaseAction.CANCEL}))
    await policy_override_repo.put(
        context,
        "supply_chain_approval_matrix",
        tenant_matrix.model_dump(mode="json"),
        audit=_audit_event(context),
    )
    runner = FakeWorkflowRunnerPort()

    handler = _advance_po_case_handler(
        po_case_repo, policy_override_repo=policy_override_repo, runner=runner
    )
    result = await handler.handle(
        context, po_case_id=case.id, action=CaseAction.CANCEL, reason="customer walked away"
    )

    assert isinstance(result, CaseActionPendingApproval)
    assert len(runner.started) == 1


async def test_advance_refuses_without_the_write_scope() -> None:
    po_case_repo = FakePOCaseRepository()
    context = _context()
    case = await _seeded_case(po_case_repo, context)

    handler = _advance_po_case_handler(po_case_repo)
    denied_context = _context(scopes=frozenset({"supply_chain.po_case.read"}))
    with pytest.raises(PermissionDeniedError):
        await handler.handle(denied_context, po_case_id=case.id, action=CaseAction.REQUEST_DEPOSIT)
    assert po_case_repo.by_id[case.id.value].state is CaseState.PO_CREATED


# -- ListCaseTransitions --------------------------------------------------------


async def test_list_transitions_returns_the_cases_own_history() -> None:
    po_case_repo = FakePOCaseRepository()
    context = _context()
    case = await _seeded_case(po_case_repo, context)
    expected = [
        CaseTransition(
            from_state=CaseState.PO_CREATED,
            to_state=CaseState.WAITING_DEPOSIT,
            reason=None,
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        CaseTransition(
            from_state=CaseState.WAITING_DEPOSIT,
            to_state=CaseState.DEPOSIT_CONFIRMED,
            reason=None,
            occurred_at=datetime(2026, 1, 2, tzinfo=UTC),
        ),
    ]
    po_case_repo.transitions[case.id.value] = expected

    result = await ListCaseTransitions(repo=po_case_repo, authz=ScopeAuthorizationService()).handle(
        context, case.id
    )

    assert result == expected


async def test_list_transitions_raises_not_found_for_an_unknown_case() -> None:
    po_case_repo = FakePOCaseRepository()
    handler = ListCaseTransitions(repo=po_case_repo, authz=ScopeAuthorizationService())
    with pytest.raises(NotFoundError):
        await handler.handle(_context(), POCaseId(uuid.uuid4()))


async def test_list_transitions_refuses_without_the_read_scope() -> None:
    po_case_repo = FakePOCaseRepository()
    context = _context()
    case = await _seeded_case(po_case_repo, context)

    handler = ListCaseTransitions(repo=po_case_repo, authz=ScopeAuthorizationService())
    denied_context = _context(scopes=frozenset())
    with pytest.raises(PermissionDeniedError):
        await handler.handle(denied_context, case.id)


# -- GetSLAPolicy / SetSLAPolicyOverride / GetSLAEvaluation -----------------


class FakePolicyOverrideRepository:
    def __init__(self) -> None:
        self.by_tenant_and_policy: dict[tuple[uuid.UUID, str], dict[str, object]] = {}
        self.audit_events: list[AuditEvent] = []

    async def get(self, context: AccessContext, policy_id: str) -> dict[str, object] | None:
        return self.by_tenant_and_policy.get((context.tenant_id, policy_id))

    async def put(
        self,
        context: AccessContext,
        policy_id: str,
        content: Mapping[str, object],
        *,
        audit: AuditEvent,
    ) -> None:
        self.by_tenant_and_policy[(context.tenant_id, policy_id)] = dict(content)
        self.audit_events.append(audit)


def _sla_policy(**milestones: SLAMilestone) -> SupplyChainSLAPolicy:
    return SupplyChainSLAPolicy(
        schema_version="1.0",
        policy_id="supply_chain_sla",
        policy_version="1.0.0",
        sla=milestones,
        supplier_update=SupplierUpdateCadence(reminder_after="5d", escalation_after="10d"),
    )


def _confirmed_milestone(duration_days: int) -> SLAMilestone:
    return SLAMilestone(duration=f"{duration_days}d", status=SLAConfirmationStatus.CONFIRMED)


def _audit_event(context: AccessContext) -> AuditEvent:
    """A minimal, valid `AuditEvent` for tests seeding a `policy_override_
    repo` directly rather than through `SetSLAPolicyOverride` — the write
    path's own audit-recording is covered separately, this is only test
    setup."""
    return AuditEvent(
        id=uuid.uuid4(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        actor_id=UserId(context.principal_id),
        action="supply_chain.sla_policy.override_set",
        resource_type="sla_policy",
        resource_id="supply_chain_sla",
        occurred_at=_NOW,
    )


async def test_sla_evaluation_is_not_applicable_for_an_unmapped_state() -> None:
    po_case_repo = FakePOCaseRepository()
    context = _context()
    case = _case_with_created_at(context, created_at=_NOW, state=CaseState.PRODUCTION)
    await po_case_repo.add(context, case)

    handler = GetSLAEvaluation(
        po_case_repo=po_case_repo,
        policy_override_repo=FakePolicyOverrideRepository(),
        platform_default_policy=_sla_policy(),
        authz=ScopeAuthorizationService(),
        clock=FixedClock(_NOW),
    )
    evaluation = await handler.handle(context, case.id)
    assert evaluation.status is SLAEvaluationStatus.NOT_APPLICABLE


async def test_sla_evaluation_uses_the_repositorys_current_state_entered_at() -> None:
    po_case_repo = FakePOCaseRepository()
    context = _context()
    case = _case_with_created_at(
        context, created_at=_NOW - timedelta(days=30), state=CaseState.WAITING_DEPOSIT
    )
    await po_case_repo.add(context, case)
    po_case_repo.current_state_entered_at[case.id.value] = _NOW - timedelta(days=3)

    handler = GetSLAEvaluation(
        po_case_repo=po_case_repo,
        policy_override_repo=FakePolicyOverrideRepository(),
        platform_default_policy=_sla_policy(deposit=_confirmed_milestone(10)),
        authz=ScopeAuthorizationService(),
        clock=FixedClock(_NOW),
    )
    evaluation = await handler.handle(context, case.id)

    assert evaluation.age_days == 3
    assert evaluation.status is SLAEvaluationStatus.ON_TRACK


async def test_sla_evaluation_falls_back_to_case_created_at_with_no_transition_yet() -> None:
    po_case_repo = FakePOCaseRepository()
    context = _context()
    case = _case_with_created_at(
        context, created_at=_NOW - timedelta(days=15), state=CaseState.WAITING_DEPOSIT
    )
    await po_case_repo.add(context, case)
    # current_state_entered_at deliberately left empty on the fake.

    handler = GetSLAEvaluation(
        po_case_repo=po_case_repo,
        policy_override_repo=FakePolicyOverrideRepository(),
        platform_default_policy=_sla_policy(deposit=_confirmed_milestone(10)),
        authz=ScopeAuthorizationService(),
        clock=FixedClock(_NOW),
    )
    evaluation = await handler.handle(context, case.id)

    assert evaluation.age_days == 15
    assert evaluation.status is SLAEvaluationStatus.BREACHED


async def test_sla_evaluation_refuses_for_an_unknown_case() -> None:
    handler = GetSLAEvaluation(
        po_case_repo=FakePOCaseRepository(),
        policy_override_repo=FakePolicyOverrideRepository(),
        platform_default_policy=_sla_policy(),
        authz=ScopeAuthorizationService(),
        clock=FixedClock(_NOW),
    )
    with pytest.raises(NotFoundError):
        await handler.handle(_context(), POCaseId(uuid.uuid4()))


async def test_sla_evaluation_refuses_without_the_read_scope() -> None:
    po_case_repo = FakePOCaseRepository()
    context = _context()
    case = _case_with_created_at(context, created_at=_NOW)
    await po_case_repo.add(context, case)

    handler = GetSLAEvaluation(
        po_case_repo=po_case_repo,
        policy_override_repo=FakePolicyOverrideRepository(),
        platform_default_policy=_sla_policy(),
        authz=ScopeAuthorizationService(),
        clock=FixedClock(_NOW),
    )
    with pytest.raises(PermissionDeniedError):
        await handler.handle(_context(scopes=frozenset()), case.id)


async def test_sla_evaluation_uses_the_tenants_own_override_over_the_platform_default() -> None:
    """The platform default marks `deposit` pending (30 days, not evaluable);
    the tenant's own override confirms a MUCH shorter 2-day threshold. If the
    handler used the platform default instead of resolving the override, this
    case (3 days in WAITING_DEPOSIT) would read NOT_EVALUABLE, not BREACHED."""
    po_case_repo = FakePOCaseRepository()
    policy_override_repo = FakePolicyOverrideRepository()
    context = _context()
    case = _case_with_created_at(
        context, created_at=_NOW - timedelta(days=30), state=CaseState.WAITING_DEPOSIT
    )
    await po_case_repo.add(context, case)
    po_case_repo.current_state_entered_at[case.id.value] = _NOW - timedelta(days=3)
    tenant_override = _sla_policy(deposit=_confirmed_milestone(2))
    await policy_override_repo.put(
        context,
        "supply_chain_sla",
        tenant_override.model_dump(mode="json"),
        audit=_audit_event(context),
    )

    pending_deposit = SLAMilestone(
        duration="30d", status=SLAConfirmationStatus.PENDING_BUSINESS_CONFIRMATION
    )
    handler = GetSLAEvaluation(
        po_case_repo=po_case_repo,
        policy_override_repo=policy_override_repo,
        platform_default_policy=_sla_policy(deposit=pending_deposit),
        authz=ScopeAuthorizationService(),
        clock=FixedClock(_NOW),
    )
    evaluation = await handler.handle(context, case.id)

    assert evaluation.status is SLAEvaluationStatus.BREACHED
    assert evaluation.threshold_days == 2


# -- GetAttentionQueue ---------------------------------------------------------


def _attention_queue_handler(
    po_case_repo: FakePOCaseRepository,
    supplier_update_repo: FakeSupplierUpdateRepository,
    *,
    policy_override_repo: FakePolicyOverrideRepository | None = None,
    platform_default_policy: SupplyChainSLAPolicy | None = None,
) -> GetAttentionQueue:
    return GetAttentionQueue(
        po_case_repo=po_case_repo,
        supplier_update_repo=supplier_update_repo,
        policy_override_repo=policy_override_repo or FakePolicyOverrideRepository(),
        platform_default_policy=platform_default_policy or _sla_policy(),
        authz=ScopeAuthorizationService(),
        clock=FixedClock(_NOW),
    )


async def test_attention_queue_flags_a_case_with_breached_sla() -> None:
    po_case_repo, supplier_update_repo = FakePOCaseRepository(), FakeSupplierUpdateRepository()
    context = _context()
    case = _case_with_created_at(
        context, created_at=_NOW - timedelta(days=3), state=CaseState.WAITING_DEPOSIT
    )
    await po_case_repo.add(context, case)
    po_case_repo.current_state_entered_at[case.id.value] = _NOW - timedelta(days=30)

    handler = _attention_queue_handler(
        po_case_repo,
        supplier_update_repo,
        platform_default_policy=_sla_policy(deposit=_confirmed_milestone(10)),
    )
    items = await handler.handle(context)

    assert len(items) == 1
    assert items[0].case.id == case.id
    assert items[0].sla is not None
    assert items[0].sla.status is SLAEvaluationStatus.BREACHED
    assert items[0].missing_update is None


async def test_attention_queue_flags_a_case_with_a_missing_update_reminder() -> None:
    po_case_repo, supplier_update_repo = FakePOCaseRepository(), FakeSupplierUpdateRepository()
    context = _context()
    case = _case_with_created_at(
        context, created_at=_NOW - timedelta(days=7), state=CaseState.PRODUCTION
    )
    await po_case_repo.add(context, case)
    # PRODUCTION has no SLA milestone mapped (NOT_APPLICABLE), so only the
    # missing-update signal can flag this one.

    handler = _attention_queue_handler(po_case_repo, supplier_update_repo)
    items = await handler.handle(context)

    assert len(items) == 1
    assert items[0].sla is None
    assert items[0].missing_update is not None
    assert items[0].missing_update.status is MissingUpdateStatus.REMINDER_DUE


async def test_attention_queue_flags_a_case_with_both_signals_at_once() -> None:
    po_case_repo, supplier_update_repo = FakePOCaseRepository(), FakeSupplierUpdateRepository()
    context = _context()
    case = _case_with_created_at(
        context, created_at=_NOW - timedelta(days=30), state=CaseState.WAITING_DEPOSIT
    )
    await po_case_repo.add(context, case)
    po_case_repo.current_state_entered_at[case.id.value] = _NOW - timedelta(days=30)

    handler = _attention_queue_handler(
        po_case_repo,
        supplier_update_repo,
        platform_default_policy=_sla_policy(deposit=_confirmed_milestone(10)),
    )
    items = await handler.handle(context)

    assert len(items) == 1
    assert items[0].sla is not None and items[0].sla.status is SLAEvaluationStatus.BREACHED
    assert (
        items[0].missing_update is not None
        and items[0].missing_update.status is MissingUpdateStatus.ESCALATION_DUE
    )


async def test_attention_queue_excludes_a_healthy_case() -> None:
    po_case_repo, supplier_update_repo = FakePOCaseRepository(), FakeSupplierUpdateRepository()
    context = _context()
    case = _case_with_created_at(
        context, created_at=_NOW - timedelta(days=1), state=CaseState.WAITING_DEPOSIT
    )
    await po_case_repo.add(context, case)
    po_case_repo.current_state_entered_at[case.id.value] = _NOW - timedelta(days=1)

    handler = _attention_queue_handler(
        po_case_repo,
        supplier_update_repo,
        platform_default_policy=_sla_policy(deposit=_confirmed_milestone(10)),
    )
    items = await handler.handle(context)

    assert items == []


async def test_attention_queue_excludes_terminal_cases() -> None:
    po_case_repo, supplier_update_repo = FakePOCaseRepository(), FakeSupplierUpdateRepository()
    context = _context()
    # Ancient enough that a non-terminal case in this state would flag on
    # missing-update alone — a terminal one must not, regardless of age.
    case = _case_with_created_at(
        context, created_at=_NOW - timedelta(days=365), state=CaseState.COMPLETED
    )
    await po_case_repo.add(context, case)

    handler = _attention_queue_handler(po_case_repo, supplier_update_repo)
    items = await handler.handle(context)

    assert items == []


async def test_attention_queue_refuses_without_the_read_scope() -> None:
    po_case_repo, supplier_update_repo = FakePOCaseRepository(), FakeSupplierUpdateRepository()
    handler = _attention_queue_handler(po_case_repo, supplier_update_repo)
    with pytest.raises(PermissionDeniedError):
        await handler.handle(_context(scopes=frozenset()))


async def test_attention_queue_uses_the_tenants_own_supplier_update_cadence() -> None:
    """One day quiet is on track under the default (5d/10d); a tenant that
    reminds at once and escalates after a day sees it due for escalation.
    The same assessment feeds the Control Tower and the daily brief."""
    po_case_repo, supplier_update_repo = FakePOCaseRepository(), FakeSupplierUpdateRepository()
    policy_override_repo = FakePolicyOverrideRepository()
    context = _context()
    case = _case_with_created_at(
        context, created_at=_NOW - timedelta(days=1), state=CaseState.PRODUCTION
    )
    await po_case_repo.add(context, case)
    handler = _attention_queue_handler(
        po_case_repo, supplier_update_repo, policy_override_repo=policy_override_repo
    )
    assert await handler.handle(context) == []

    tenant_policy = _sla_policy().model_copy(
        update={
            "supplier_update": SupplierUpdateCadence(reminder_after="0d", escalation_after="1d")
        }
    )
    await policy_override_repo.put(
        context,
        "supply_chain_sla",
        tenant_policy.model_dump(mode="json"),
        audit=_audit_event(context),
    )
    (item,) = await handler.handle(context)
    assert item.missing_update is not None
    assert item.missing_update.status is MissingUpdateStatus.ESCALATION_DUE


async def test_attention_queue_uses_the_tenants_own_sla_policy_override() -> None:
    """The platform default marks `deposit` pending (never breachable); the
    tenant's own override confirms a real threshold. If the handler used the
    platform default instead of resolving the override, this case would
    never flag on SLA."""
    po_case_repo, supplier_update_repo = FakePOCaseRepository(), FakeSupplierUpdateRepository()
    policy_override_repo = FakePolicyOverrideRepository()
    context = _context()
    case = _case_with_created_at(
        context, created_at=_NOW - timedelta(days=3), state=CaseState.WAITING_DEPOSIT
    )
    await po_case_repo.add(context, case)
    po_case_repo.current_state_entered_at[case.id.value] = _NOW - timedelta(days=3)
    tenant_override = _sla_policy(deposit=_confirmed_milestone(2))
    await policy_override_repo.put(
        context,
        "supply_chain_sla",
        tenant_override.model_dump(mode="json"),
        audit=_audit_event(context),
    )
    pending_deposit = SLAMilestone(
        duration="30d", status=SLAConfirmationStatus.PENDING_BUSINESS_CONFIRMATION
    )

    handler = _attention_queue_handler(
        po_case_repo,
        supplier_update_repo,
        policy_override_repo=policy_override_repo,
        platform_default_policy=_sla_policy(deposit=pending_deposit),
    )
    items = await handler.handle(context)

    assert len(items) == 1
    assert items[0].sla is not None and items[0].sla.status is SLAEvaluationStatus.BREACHED


# -- GetPortfolioSummary -------------------------------------------------------


def _portfolio_summary_handler(
    po_case_repo: FakePOCaseRepository,
    supplier_update_repo: FakeSupplierUpdateRepository,
    *,
    platform_default_policy: SupplyChainSLAPolicy | None = None,
) -> GetPortfolioSummary:
    return GetPortfolioSummary(
        po_case_repo=po_case_repo,
        supplier_update_repo=supplier_update_repo,
        policy_override_repo=FakePolicyOverrideRepository(),
        platform_default_policy=platform_default_policy or _sla_policy(),
        authz=ScopeAuthorizationService(),
        clock=FixedClock(_NOW),
    )


async def test_portfolio_summary_counts_active_cases_by_state_and_supplier() -> None:
    po_case_repo, supplier_update_repo = FakePOCaseRepository(), FakeSupplierUpdateRepository()
    context = _context()
    # Breached deposit SLA, heard from yesterday.
    breached = _case_with_created_at(
        context, created_at=_NOW - timedelta(days=20), state=CaseState.WAITING_DEPOSIT
    )
    await po_case_repo.add(context, breached)
    po_case_repo.current_state_entered_at[breached.id.value] = _NOW - timedelta(days=12)
    await supplier_update_repo.add(
        context, _supplier_update_at(breached, created_at=_NOW - timedelta(days=1))
    )
    # Healthy on both signals, same state and supplier.
    healthy = _case_with_created_at(
        context, created_at=_NOW - timedelta(days=2), state=CaseState.WAITING_DEPOSIT
    )
    await po_case_repo.add(context, healthy)
    po_case_repo.current_state_entered_at[healthy.id.value] = _NOW - timedelta(days=2)
    # A different supplier gone quiet in PRODUCTION (no SLA mapped there).
    quiet = _case_with_created_at(
        context, created_at=_NOW - timedelta(days=11), state=CaseState.PRODUCTION
    )
    quiet.supplier_name = "Quiet Co."
    await po_case_repo.add(context, quiet)
    # Terminal: never counted, however old.
    done = _case_with_created_at(
        context, created_at=_NOW - timedelta(days=365), state=CaseState.COMPLETED
    )
    await po_case_repo.add(context, done)

    summary = await _portfolio_summary_handler(
        po_case_repo,
        supplier_update_repo,
        platform_default_policy=_sla_policy(deposit=_confirmed_milestone(10)),
    ).handle(context)

    assert summary.active_case_count == 3
    assert summary.sla_breached_count == 1
    assert summary.update_overdue_count == 1

    deposit, production = summary.by_state
    assert (deposit.state, deposit.case_count, deposit.sla_breached_count) == (
        CaseState.WAITING_DEPOSIT,
        2,
        1,
    )
    assert deposit.oldest_in_state_days == 12
    assert (production.state, production.case_count, production.update_overdue_count) == (
        CaseState.PRODUCTION,
        1,
        1,
    )

    quiet_row, elmich_row = summary.by_supplier
    assert (quiet_row.supplier_name, quiet_row.escalation_due_count) == ("Quiet Co.", 1)
    assert quiet_row.longest_silence_days == 11
    assert (elmich_row.supplier_name, elmich_row.case_count) == ("Elmich Co.", 2)
    assert elmich_row.update_overdue_count == 0


# Tenant isolation is deliberately NOT tested here: against a fake that
# scopes by tenant because it was written to, no change to the handler could
# make such a test fail. `tests/integration/test_portfolio_summary.py` is
# the guard — it runs this handler over the real repositories under RLS.


async def test_portfolio_summary_refuses_without_the_read_scope() -> None:
    handler = _portfolio_summary_handler(FakePOCaseRepository(), FakeSupplierUpdateRepository())
    with pytest.raises(PermissionDeniedError):
        await handler.handle(_context(scopes=frozenset()))


# -- GetSLAPolicy / SetSLAPolicyOverride --------------------------------------


async def test_get_sla_policy_returns_the_platform_default_when_no_override_exists() -> None:
    handler = GetSLAPolicy(
        policy_override_repo=FakePolicyOverrideRepository(),
        platform_default_policy=_sla_policy(deposit=_confirmed_milestone(10)),
        authz=ScopeAuthorizationService(),
    )
    context = _context(scopes=frozenset({"supply_chain.sla_policy.read"}))

    policy = await handler.handle(context)
    assert policy.sla["deposit"].duration_days == 10


async def test_get_sla_policy_returns_the_tenants_own_override_when_set() -> None:
    policy_override_repo = FakePolicyOverrideRepository()
    context = _context(scopes=frozenset({"supply_chain.sla_policy.read"}))
    override = _sla_policy(deposit=_confirmed_milestone(3))
    await policy_override_repo.put(
        context, "supply_chain_sla", override.model_dump(mode="json"), audit=_audit_event(context)
    )

    handler = GetSLAPolicy(
        policy_override_repo=policy_override_repo,
        platform_default_policy=_sla_policy(deposit=_confirmed_milestone(10)),
        authz=ScopeAuthorizationService(),
    )
    policy = await handler.handle(context)
    assert policy.sla["deposit"].duration_days == 3


async def test_get_sla_policy_refuses_without_the_read_scope() -> None:
    handler = GetSLAPolicy(
        policy_override_repo=FakePolicyOverrideRepository(),
        platform_default_policy=_sla_policy(),
        authz=ScopeAuthorizationService(),
    )
    with pytest.raises(PermissionDeniedError):
        await handler.handle(_context(scopes=frozenset()))


async def test_set_sla_policy_override_persists_it_for_the_callers_own_tenant() -> None:
    policy_override_repo = FakePolicyOverrideRepository()
    context = _context(scopes=frozenset({"supply_chain.sla_policy.write"}))
    submitted = _sla_policy(deposit=_confirmed_milestone(7))

    handler = SetSLAPolicyOverride(
        policy_override_repo=policy_override_repo,
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
        clock=FixedClock(_NOW),
    )
    await handler.handle(context, submitted)

    stored = policy_override_repo.by_tenant_and_policy[(TENANT, "supply_chain_sla")]
    assert SupplyChainSLAPolicy.model_validate(stored).sla["deposit"].duration_days == 7

    (audit,) = policy_override_repo.audit_events
    assert audit.tenant_id.value == TENANT
    assert audit.actor_id.value == context.principal_id
    assert audit.action == "supply_chain.sla_policy.override_set"


async def test_set_sla_policy_override_refuses_without_the_write_scope() -> None:
    policy_override_repo = FakePolicyOverrideRepository()
    context = _context(scopes=frozenset({"supply_chain.sla_policy.read"}))  # read, not write

    handler = SetSLAPolicyOverride(
        policy_override_repo=policy_override_repo,
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
        clock=FixedClock(_NOW),
    )
    with pytest.raises(PermissionDeniedError):
        await handler.handle(context, _sla_policy())
    assert policy_override_repo.by_tenant_and_policy == {}


async def test_a_written_override_is_immediately_visible_to_get_sla_policy() -> None:
    """End-to-end within the fakes: SetSLAPolicyOverride's write is what
    GetSLAPolicy's read resolves — not two disconnected mechanisms that
    happen to share a name."""
    policy_override_repo = FakePolicyOverrideRepository()
    context = _context(
        scopes=frozenset({"supply_chain.sla_policy.read", "supply_chain.sla_policy.write"})
    )
    set_handler = SetSLAPolicyOverride(
        policy_override_repo=policy_override_repo,
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
        clock=FixedClock(_NOW),
    )
    get_handler = GetSLAPolicy(
        policy_override_repo=policy_override_repo,
        platform_default_policy=_sla_policy(),
        authz=ScopeAuthorizationService(),
    )

    await set_handler.handle(context, _sla_policy(deposit=_confirmed_milestone(9)))
    policy = await get_handler.handle(context)

    assert policy.sla["deposit"].duration_days == 9


# -- GetApprovalMatrix / SetApprovalMatrixOverride ---------------------------


async def test_get_approval_matrix_returns_the_platform_default_when_no_override_exists() -> None:
    handler = GetApprovalMatrix(
        policy_override_repo=FakePolicyOverrideRepository(),
        platform_default_matrix=_approval_matrix(
            approval_required_actions=frozenset({CaseAction.CANCEL})
        ),
        authz=ScopeAuthorizationService(),
    )
    context = _context(scopes=frozenset({"supply_chain.approval_matrix.read"}))

    matrix = await handler.handle(context)
    assert matrix.approval_required_actions == frozenset({CaseAction.CANCEL})


async def test_get_approval_matrix_returns_the_tenants_own_override_when_set() -> None:
    policy_override_repo = FakePolicyOverrideRepository()
    context = _context(scopes=frozenset({"supply_chain.approval_matrix.read"}))
    override = _approval_matrix(approval_required_actions=frozenset({CaseAction.FLAG_BLOCKED}))
    await policy_override_repo.put(
        context,
        "supply_chain_approval_matrix",
        override.model_dump(mode="json"),
        audit=_audit_event(context),
    )

    handler = GetApprovalMatrix(
        policy_override_repo=policy_override_repo,
        platform_default_matrix=_approval_matrix(),
        authz=ScopeAuthorizationService(),
    )
    matrix = await handler.handle(context)
    assert matrix.approval_required_actions == frozenset({CaseAction.FLAG_BLOCKED})


async def test_get_approval_matrix_refuses_without_the_read_scope() -> None:
    handler = GetApprovalMatrix(
        policy_override_repo=FakePolicyOverrideRepository(),
        platform_default_matrix=_approval_matrix(),
        authz=ScopeAuthorizationService(),
    )
    with pytest.raises(PermissionDeniedError):
        await handler.handle(_context(scopes=frozenset()))


async def test_set_approval_matrix_override_persists_it_for_the_callers_own_tenant() -> None:
    policy_override_repo = FakePolicyOverrideRepository()
    context = _context(scopes=frozenset({"supply_chain.approval_matrix.write"}))
    submitted = _approval_matrix(approval_required_actions=frozenset({CaseAction.CANCEL}))

    handler = SetApprovalMatrixOverride(
        policy_override_repo=policy_override_repo,
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
        clock=FixedClock(_NOW),
    )
    await handler.handle(context, submitted)

    stored = policy_override_repo.by_tenant_and_policy[(TENANT, "supply_chain_approval_matrix")]
    reloaded = SupplyChainApprovalMatrix.model_validate(stored)
    assert reloaded.approval_required_actions == frozenset({CaseAction.CANCEL})

    (audit,) = policy_override_repo.audit_events
    assert audit.tenant_id.value == TENANT
    assert audit.actor_id.value == context.principal_id
    assert audit.action == "supply_chain.approval_matrix.override_set"


async def test_set_approval_matrix_override_refuses_without_the_write_scope() -> None:
    policy_override_repo = FakePolicyOverrideRepository()
    context = _context(scopes=frozenset({"supply_chain.approval_matrix.read"}))  # read, not write

    handler = SetApprovalMatrixOverride(
        policy_override_repo=policy_override_repo,
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
        clock=FixedClock(_NOW),
    )
    with pytest.raises(PermissionDeniedError):
        await handler.handle(context, _approval_matrix())
    assert policy_override_repo.by_tenant_and_policy == {}


async def test_a_written_approval_matrix_override_is_immediately_visible_to_get() -> None:
    policy_override_repo = FakePolicyOverrideRepository()
    context = _context(
        scopes=frozenset(
            {"supply_chain.approval_matrix.read", "supply_chain.approval_matrix.write"}
        )
    )
    set_handler = SetApprovalMatrixOverride(
        policy_override_repo=policy_override_repo,
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
        clock=FixedClock(_NOW),
    )
    get_handler = GetApprovalMatrix(
        policy_override_repo=policy_override_repo,
        platform_default_matrix=_approval_matrix(),
        authz=ScopeAuthorizationService(),
    )

    await set_handler.handle(
        context, _approval_matrix(approval_required_actions=frozenset({CaseAction.CANCEL}))
    )
    matrix = await get_handler.handle(context)

    assert matrix.approval_required_actions == frozenset({CaseAction.CANCEL})


# -- AnswerCaseQuery -----------------------------------------------------------


class _IntentGateway:
    """Answers with a fixed payload, validated against the requested schema
    exactly as the real gateway does: an out-of-schema payload raises
    `ModelOutputInvalidError`, never comes back half-trusted. A fake that
    returned any object regardless of `output_type` would let a wrong schema
    pass every handler test."""

    def __init__(self, answer: Mapping[str, object] | Exception) -> None:
        self.answer = answer
        self.calls: list[RunContext] = []

    async def generate_structured(
        self, request: ModelRequest, output_type: type[OutputT], *, run_context: RunContext
    ) -> OutputT:
        self.calls.append(run_context)
        if isinstance(self.answer, Exception):
            raise self.answer
        try:
            return output_type.model_validate(dict(self.answer))
        except ValidationError as exc:
            raise ModelOutputInvalidError("model output failed schema validation") from exc


def _answer_case_query(repo: FakePOCaseRepository, gateway: _IntentGateway) -> AnswerCaseQuery:
    authz = ScopeAuthorizationService()
    return AnswerCaseQuery(
        po_case_repo=repo,
        list_cases=ListPOCases(repo=repo, authz=authz),
        gateway=gateway,
        authz=authz,
        ids=Uuid4Generator(),
    )


async def _seed(
    repo: FakePOCaseRepository,
    context: AccessContext,
    *,
    reference: str,
    supplier: str,
    state: CaseState = CaseState.PO_CREATED,
) -> POCase:
    case = _case_with_created_at(context, created_at=_NOW, state=state)
    case.po_reference = reference
    case.supplier_name = supplier
    await repo.add(context, case)
    return case


async def test_a_question_lists_only_the_cases_its_resolved_filter_matches() -> None:
    repo = FakePOCaseRepository()
    context = _context()
    wanted = await _seed(
        repo, context, reference="PO-1", supplier="Sunhouse Co.", state=CaseState.WAITING_DEPOSIT
    )
    await _seed(repo, context, reference="PO-2", supplier="Sunhouse Co.")
    await _seed(
        repo, context, reference="PO-3", supplier="Elmich Co.", state=CaseState.WAITING_DEPOSIT
    )
    gateway = _IntentGateway(
        {
            "kind": "list_cases",
            "supplier_mention": "sunhouse",
            "state": "waiting_deposit",
            "state_quote": "chờ đặt cọc",
        }
    )

    answer = await _answer_case_query(repo, gateway).handle(
        context, "PO của sunhouse đang chờ đặt cọc"
    )

    assert answer.plan.outcome is CaseQueryOutcome.LIST
    # The STORED name, resolved by code — not the model's "sunhouse".
    assert answer.plan.supplier_name == "Sunhouse Co."
    assert [c.id for c in answer.cases] == [wanted.id]
    assert answer.has_more is False


async def test_an_unknown_supplier_answers_nothing_rather_than_everything() -> None:
    """Dropping the supplier filter would list every case — the fail-open
    answer to a question about one supplier."""
    repo = FakePOCaseRepository()
    context = _context()
    await _seed(repo, context, reference="PO-1", supplier="Sunhouse Co.")
    gateway = _IntentGateway({"kind": "list_cases", "supplier_mention": "Toshiba"})

    answer = await _answer_case_query(repo, gateway).handle(context, "PO của Toshiba")

    assert answer.plan.outcome is CaseQueryOutcome.SUPPLIER_NOT_FOUND
    assert answer.cases == ()


async def test_a_state_the_question_never_said_refuses_rather_than_lists_all() -> None:
    """Listing every case would read as if filtered by the state the model
    thought it saw — the reply says which part was not understood instead."""
    repo = FakePOCaseRepository()
    context = _context()
    await _seed(repo, context, reference="PO-1", supplier="Sunhouse Co.")
    gateway = _IntentGateway({"kind": "list_cases", "state": "qc", "state_quote": "đang QC"})

    answer = await _answer_case_query(repo, gateway).handle(context, "Cho tôi các PO")

    assert answer.plan.outcome is CaseQueryOutcome.NOT_UNDERSTOOD
    assert answer.ignored == (GroundedField.STATE,)
    assert answer.cases == ()


async def test_more_rows_than_an_answer_carries_offer_the_full_list() -> None:
    repo = FakePOCaseRepository()
    context = _context()
    for index in range(21):
        await _seed(repo, context, reference=f"PO-{index}", supplier="Sunhouse Co.")
    gateway = _IntentGateway({"kind": "list_cases"})

    answer = await _answer_case_query(repo, gateway).handle(context, "Cho tôi các PO")

    assert len(answer.cases) == 20
    assert answer.has_more is True


async def test_a_named_po_opens_that_one_case() -> None:
    repo = FakePOCaseRepository()
    context = _context()
    case = await _seed(repo, context, reference="PO-123", supplier="Sunhouse Co.")
    gateway = _IntentGateway({"kind": "open_case", "po_reference_mention": "po-123"})

    answer = await _answer_case_query(repo, gateway).handle(context, "Case po-123 đang vướng gì?")

    assert answer.plan.outcome is CaseQueryOutcome.OPEN
    assert answer.opened is not None and answer.opened.id == case.id


async def test_an_unknown_po_is_not_found_and_two_spellings_are_ambiguous() -> None:
    repo = FakePOCaseRepository()
    context = _context()
    await _seed(repo, context, reference="PO-7", supplier="Sunhouse Co.")
    await _seed(repo, context, reference="po-7", supplier="Elmich Co.")

    unknown = await _answer_case_query(
        repo, _IntentGateway({"kind": "open_case", "po_reference_mention": "PO-9"})
    ).handle(context, "PO-9 thế nào?")
    ambiguous = await _answer_case_query(
        repo, _IntentGateway({"kind": "open_case", "po_reference_mention": "PO-7"})
    ).handle(context, "PO-7 thế nào?")

    assert unknown.plan.outcome is CaseQueryOutcome.PO_NOT_FOUND
    assert unknown.opened is None
    assert ambiguous.plan.outcome is CaseQueryOutcome.PO_AMBIGUOUS
    assert ambiguous.opened is None
    assert set(ambiguous.plan.candidates) == {"PO-7", "po-7"}


async def test_an_answer_the_schema_refuses_degrades_to_not_understood() -> None:
    gateway = _IntentGateway({"kind": "list_cases", "tenant_id": "someone-else"})

    answer = await _answer_case_query(FakePOCaseRepository(), gateway).handle(
        _context(), "PO của tenant khác"
    )

    assert answer.plan.outcome is CaseQueryOutcome.NOT_UNDERSTOOD
    assert answer.cases == ()


async def test_a_budget_refusal_is_raised_not_dressed_up_as_a_misunderstanding() -> None:
    gateway = _IntentGateway(DomainError("run reached its cost ceiling"))

    with pytest.raises(DomainError, match="cost ceiling"):
        await _answer_case_query(FakePOCaseRepository(), gateway).handle(_context(), "PO?")


async def test_no_token_is_spent_for_a_caller_who_may_not_read_cases() -> None:
    gateway = _IntentGateway({"kind": "list_cases"})

    with pytest.raises(PermissionDeniedError):
        await _answer_case_query(FakePOCaseRepository(), gateway).handle(
            _context(scopes=frozenset()), "Cho tôi các PO"
        )
    assert gateway.calls == []


async def test_the_model_call_runs_as_the_verified_caller() -> None:
    gateway = _IntentGateway({"kind": "unsupported"})
    context = _context()

    await _answer_case_query(FakePOCaseRepository(), gateway).handle(context, "Thời tiết?")

    (run_context,) = gateway.calls
    assert (run_context.tenant_id, run_context.workspace_id) == (
        context.tenant_id,
        context.workspace_id,
    )
    assert run_context.actor_id == context.principal_id


async def test_exactly_a_full_answer_of_rows_does_not_claim_more_exist() -> None:
    """`has_more` is the list's own next cursor, not "the answer is full" —
    twenty matches are all there is."""
    repo = FakePOCaseRepository()
    context = _context()
    for index in range(20):
        await _seed(repo, context, reference=f"PO-{index}", supplier="Sunhouse Co.")

    answer = await _answer_case_query(repo, _IntentGateway({"kind": "list_cases"})).handle(
        context, "Cho tôi các PO"
    )

    assert len(answer.cases) == 20
    assert answer.has_more is False


async def test_an_ambiguous_po_answer_carries_every_match_to_pick_from() -> None:
    repo = FakePOCaseRepository()
    context = _context()
    upper = await _seed(repo, context, reference="PO-7", supplier="Sunhouse Co.")
    lower = await _seed(repo, context, reference="po-7", supplier="Elmich Co.")

    answer = await _answer_case_query(
        repo, _IntentGateway({"kind": "open_case", "po_reference_mention": "PO-7"})
    ).handle(context, "PO-7 thế nào?")

    assert {c.id for c in answer.cases} == {upper.id, lower.id}


async def test_an_opened_case_is_reported_by_its_stored_reference() -> None:
    repo = FakePOCaseRepository()
    context = _context()
    await _seed(repo, context, reference="PO-123", supplier="Sunhouse Co.")

    answer = await _answer_case_query(
        repo, _IntentGateway({"kind": "open_case", "po_reference_mention": "po-123"})
    ).handle(context, "po-123 đang vướng gì?")

    assert answer.plan.po_reference == "PO-123"


async def test_a_list_reading_that_names_a_po_answers_nothing_rather_than_everything() -> None:
    repo = FakePOCaseRepository()
    context = _context()
    await _seed(repo, context, reference="PO-123", supplier="Sunhouse Co.")
    await _seed(repo, context, reference="PO-456", supplier="Sunhouse Co.")

    answer = await _answer_case_query(
        repo, _IntentGateway({"kind": "list_cases", "po_reference_mention": "PO-123"})
    ).handle(context, "Cho tôi xem PO-123")

    assert answer.plan.outcome is CaseQueryOutcome.NOT_UNDERSTOOD
    assert answer.ignored == ()
    assert answer.unusable == (GroundedField.PO_REFERENCE,)
    assert answer.cases == ()


async def test_an_unsupported_question_reports_no_ignored_field() -> None:
    answer = await _answer_case_query(
        FakePOCaseRepository(),
        # "Toshiba" is not in the question: a dropped field an unsupported
        # reading must still not report as its reason.
        _IntentGateway({"kind": "unsupported", "supplier_mention": "Toshiba"}),
    ).handle(_context(), "Dự báo PO của sunhouse tháng sau")

    assert answer.plan.outcome is CaseQueryOutcome.NOT_UNDERSTOOD
    assert answer.ignored == ()


# -- GetDailyBrief / GetBriefPolicy / SetBriefPolicyOverride -----------------

_BRIEF_READ = frozenset({"supply_chain.po_case.read"})
_BRIEF_AND_APPROVALS_READ = _BRIEF_READ | {"approvals.read"}


def _brief_policy(order: tuple[BriefSignal, ...] = tuple(BriefSignal)) -> SupplyChainBriefPolicy:
    return SupplyChainBriefPolicy(
        schema_version="1.0",
        policy_id="supply_chain_brief",
        policy_version="1.0.0",
        signal_order=order,
    )


class FakePendingApprovals:
    """Honors the port's contract (tenant, pending, literal prefix, newest
    first, `limit` after `total`) and records whether it was asked at all."""

    def __init__(self, approvals: list[ApprovalRequest] | None = None) -> None:
        self.approvals = approvals or []
        self.calls = 0

    async def list_pending_by_type_prefix(
        self, context: AccessContext, *, prefix: str, limit: int
    ) -> tuple[int, list[ApprovalRequest]]:
        self.calls += 1
        matching = sorted(
            (
                a
                for a in self.approvals
                if a.tenant_id.value == context.tenant_id and a.approval_type.startswith(prefix)
            ),
            key=lambda a: (a.created_at, a.id),
            reverse=True,
        )
        return len(matching), matching[:limit]


def _approval(
    context: AccessContext, *, po_case_id: object, requested_days_ago: int = 2
) -> ApprovalRequest:
    return ApprovalRequest(
        id=uuid.uuid4(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        approval_type="supply_chain.case_action.cancel",
        requested_by=UserId(context.principal_id),
        reason="cancel needs a second pair of eyes",
        payload={"po_case_id": str(po_case_id)},
        created_at=_NOW - timedelta(days=requested_days_ago),
    )


def _daily_brief_handler(
    po_case_repo: FakePOCaseRepository,
    *,
    supplier_update_repo: FakeSupplierUpdateRepository | None = None,
    policy_override_repo: FakePolicyOverrideRepository | None = None,
    pending: FakePendingApprovals | None = None,
) -> GetDailyBrief:
    return GetDailyBrief(
        po_case_repo=po_case_repo,
        supplier_update_repo=supplier_update_repo or FakeSupplierUpdateRepository(),
        policy_override_repo=policy_override_repo or FakePolicyOverrideRepository(),
        platform_default_policy=_sla_policy(),
        platform_default_brief_policy=_brief_policy(),
        pending_approvals=pending or FakePendingApprovals(),
        authz=ScopeAuthorizationService(),
        clock=FixedClock(_NOW),
    )


async def test_daily_brief_refuses_without_the_read_scope_before_reading_anything() -> None:
    pending = FakePendingApprovals()
    handler = _daily_brief_handler(FakePOCaseRepository(), pending=pending)
    with pytest.raises(PermissionDeniedError):
        await handler.handle(_context(scopes=frozenset()))
    assert pending.calls == 0


def _blocked_case(context: AccessContext) -> POCase:
    """A case flagged blocked a day after it was created — an exception state
    the happy-path walker cannot reach on its own."""
    case = _case_with_created_at(
        context, created_at=_NOW - timedelta(days=1), state=CaseState.PRODUCTION
    )
    case.flag_blocked(reason="mould broke")
    return case


async def test_daily_brief_groups_active_cases_by_their_signals() -> None:
    repo = FakePOCaseRepository()
    context = _context(scopes=_BRIEF_READ)
    blocked = _blocked_case(context)
    quiet = _case_with_created_at(
        context, created_at=_NOW - timedelta(days=30), state=CaseState.PRODUCTION
    )
    for case in (blocked, quiet):
        await repo.add(context, case)

    brief = await _daily_brief_handler(repo).handle(context)

    assert brief.active_case_count == 2
    assert [g.key for g in brief.groups] == ["update_escalation_due", "case_blocked"]
    assert brief.group("case_blocked").entries[0].case.id == blocked.id  # type: ignore[union-attr]


async def test_daily_brief_reads_the_suppliers_latest_word_for_a_reported_delay() -> None:
    repo, updates = FakePOCaseRepository(), FakeSupplierUpdateRepository()
    context = _context(scopes=_BRIEF_READ)
    case = _case_with_created_at(
        context, created_at=_NOW - timedelta(days=2), state=CaseState.PRODUCTION
    )
    await repo.add(context, case)
    earlier = _supplier_update_at(case, created_at=_NOW - timedelta(days=2))
    delayed = replace(
        _supplier_update_at(case, created_at=_NOW - timedelta(days=1)),
        extraction=_extraction(delay_days=9),
    )
    await updates.add(context, earlier)
    await updates.add(context, delayed)

    brief = await _daily_brief_handler(repo, supplier_update_repo=updates).handle(context)
    group = brief.group("supplier_reported_delay")
    assert group is not None
    assert group.entries[0].days == 9


async def test_daily_brief_hides_approvals_from_a_caller_who_cannot_open_the_inbox() -> None:
    repo = FakePOCaseRepository()
    context = _context(scopes=_BRIEF_READ)
    case = _case_with_created_at(
        context, created_at=_NOW - timedelta(days=1), state=CaseState.PRODUCTION
    )
    await repo.add(context, case)
    pending = FakePendingApprovals([_approval(context, po_case_id=case.id.value)])

    brief = await _daily_brief_handler(repo, pending=pending).handle(context)

    assert brief.approvals_visible is False
    assert brief.group("approval_pending") is None
    # Not looked at, rather than looked at and discarded.
    assert pending.calls == 0


async def test_daily_brief_shows_pending_approvals_resolved_to_the_callers_cases() -> None:
    repo = FakePOCaseRepository()
    context = _context(scopes=_BRIEF_AND_APPROVALS_READ)
    case = _case_with_created_at(
        context, created_at=_NOW - timedelta(days=1), state=CaseState.PRODUCTION
    )
    await repo.add(context, case)
    pending = FakePendingApprovals(
        [
            _approval(context, po_case_id=case.id.value, requested_days_ago=3),
            # Names no readable case: counted, never guessed at.
            _approval(context, po_case_id="not-a-uuid"),
            _approval(context, po_case_id=uuid.uuid4()),
        ]
    )

    brief = await _daily_brief_handler(repo, pending=pending).handle(context)

    assert brief.approvals_visible is True
    group = brief.group("approval_pending")
    assert group is not None
    assert group.total == 3
    assert [(e.case.id, e.days, e.approval_action) for e in group.entries] == [
        (case.id, 3, "cancel")
    ]


async def test_daily_brief_shows_a_case_that_moved_even_after_it_left_the_active_set() -> None:
    repo = FakePOCaseRepository()
    context = _context(scopes=_BRIEF_READ)
    done = _case_with_created_at(
        context, created_at=_NOW - timedelta(days=40), state=CaseState.COMPLETED
    )
    await repo.add(context, done)
    moved = CaseTransition(
        from_state=CaseState.WAREHOUSE_RECEIVING,
        to_state=CaseState.COMPLETED,
        reason=None,
        occurred_at=_NOW - timedelta(hours=3),
    )
    stale = CaseTransition(
        from_state=CaseState.PAYMENT_COMPLETED,
        to_state=CaseState.WAREHOUSE_RECEIVING,
        reason=None,
        occurred_at=_NOW - timedelta(days=5),
    )
    repo.transitions[done.id.value] = [stale, moved]

    brief = await _daily_brief_handler(repo).handle(context)

    group = brief.group("changed_recently")
    assert group is not None
    assert [(e.case.id, e.transition) for e in group.entries] == [(done.id, moved)]
    assert brief.active_case_count == 0


async def test_daily_brief_follows_the_tenants_own_brief_order() -> None:
    repo, overrides = FakePOCaseRepository(), FakePolicyOverrideRepository()
    context = _context(scopes=_BRIEF_READ)
    blocked = _blocked_case(context)
    quiet = _case_with_created_at(
        context, created_at=_NOW - timedelta(days=30), state=CaseState.PRODUCTION
    )
    for case in (blocked, quiet):
        await repo.add(context, case)
    await overrides.put(
        context,
        "supply_chain_brief",
        _brief_policy(tuple(reversed(BriefSignal))).model_dump(mode="json"),
        audit=_audit_event(context),
    )

    brief = await _daily_brief_handler(repo, policy_override_repo=overrides).handle(context)
    assert [g.key for g in brief.groups] == ["case_blocked", "update_escalation_due"]


async def test_get_brief_policy_returns_the_platform_default_then_the_tenants_own() -> None:
    overrides = FakePolicyOverrideRepository()
    context = _context(
        scopes=frozenset({"supply_chain.brief_policy.read", "supply_chain.brief_policy.write"})
    )
    get_handler = GetBriefPolicy(
        policy_override_repo=overrides,
        platform_default_policy=_brief_policy(),
        authz=ScopeAuthorizationService(),
    )
    set_handler = SetBriefPolicyOverride(
        policy_override_repo=overrides,
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
        clock=FixedClock(_NOW),
    )

    assert (await get_handler.handle(context)).signal_order == tuple(BriefSignal)
    reordered = _brief_policy(tuple(reversed(BriefSignal)))
    await set_handler.handle(context, reordered)
    assert (await get_handler.handle(context)).signal_order == tuple(reversed(BriefSignal))

    (audit,) = overrides.audit_events
    assert audit.action == "supply_chain.brief_policy.override_set"
    assert audit.resource_id == "supply_chain_brief"
    assert audit.actor_id.value == context.principal_id


async def test_brief_policy_handlers_refuse_without_their_scopes() -> None:
    overrides = FakePolicyOverrideRepository()
    get_handler = GetBriefPolicy(
        policy_override_repo=overrides,
        platform_default_policy=_brief_policy(),
        authz=ScopeAuthorizationService(),
    )
    set_handler = SetBriefPolicyOverride(
        policy_override_repo=overrides,
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
        clock=FixedClock(_NOW),
    )
    with pytest.raises(PermissionDeniedError):
        await get_handler.handle(_context(scopes=frozenset()))
    with pytest.raises(PermissionDeniedError):
        await set_handler.handle(
            _context(scopes=frozenset({"supply_chain.brief_policy.read"})), _brief_policy()
        )
    assert overrides.by_tenant_and_policy == {}


async def test_a_brief_policy_row_edited_past_the_handler_is_refused_on_read() -> None:
    """The override is re-validated on read: a row written straight into the
    database that drops a signal is refused loudly, never silently obeyed."""
    overrides = FakePolicyOverrideRepository()
    context = _context(scopes=frozenset({"supply_chain.brief_policy.read"}))
    document = _brief_policy().model_dump(mode="json")
    document["signal_order"] = [s for s in document["signal_order"] if s != "sla_breached"]
    overrides.by_tenant_and_policy[(TENANT, "supply_chain_brief")] = document

    handler = GetBriefPolicy(
        policy_override_repo=overrides,
        platform_default_policy=_brief_policy(),
        authz=ScopeAuthorizationService(),
    )
    with pytest.raises(ValidationError):
        await handler.handle(context)


# -- SummarizeDailyBrief -------------------------------------------------------


class _SummaryGateway:
    """Answers the summary prompt with `draft`, or raises `error` — and
    records every call, so a test can prove no token was spent."""

    def __init__(
        self, draft: BriefSummaryDraft | None = None, *, error: Exception | None = None
    ) -> None:
        self.draft = draft or BriefSummaryDraft(sentences=[])
        self.error = error
        self.calls: list[RunContext] = []

    async def generate_structured(
        self, request: ModelRequest, output_type: type[OutputT], *, run_context: RunContext
    ) -> OutputT:
        self.calls.append(run_context)
        if self.error is not None:
            raise self.error
        assert output_type is BriefSummaryDraft
        return self.draft  # type: ignore[return-value]


def _summarizer(repo: FakePOCaseRepository, gateway: _SummaryGateway) -> SummarizeDailyBrief:
    return SummarizeDailyBrief(
        get_daily_brief=_daily_brief_handler(repo), gateway=gateway, ids=Uuid4Generator()
    )


async def _repo_with_a_blocked_case(context: AccessContext) -> FakePOCaseRepository:
    repo = FakePOCaseRepository()
    await repo.add(context, _blocked_case(context))
    return repo


def _draft(*sentences: tuple[str, list[str]]) -> BriefSummaryDraft:
    return BriefSummaryDraft.model_validate(
        {"sentences": [{"text": text, "group_keys": keys} for text, keys in sentences]}
    )


async def test_summary_refuses_without_the_read_scope_before_a_token_is_spent() -> None:
    context = _context(scopes=_BRIEF_READ)
    gateway = _SummaryGateway()
    summarizer = _summarizer(await _repo_with_a_blocked_case(context), gateway)
    with pytest.raises(PermissionDeniedError):
        await summarizer.handle(_context(scopes=frozenset()))
    assert gateway.calls == []


async def test_an_empty_brief_is_answered_without_asking_a_model() -> None:
    gateway = _SummaryGateway()
    result = await _summarizer(FakePOCaseRepository(), gateway).handle(_context(scopes=_BRIEF_READ))
    assert result.summary.status is BriefSummaryStatus.NOTHING_TO_SUMMARIZE
    assert gateway.calls == []


async def test_only_sentences_that_check_out_against_the_brief_are_returned() -> None:
    context = _context(scopes=_BRIEF_READ)
    gateway = _SummaryGateway(
        _draft(
            ("1 case đang bị chặn.", ["case_blocked"]),
            ("40 case đang bị chặn.", ["case_blocked"]),
            ("Mọi PO thanh toán đã xong.", ["waiting_on_us:waiting_payment"]),
        )
    )
    result = await _summarizer(await _repo_with_a_blocked_case(context), gateway).handle(context)

    assert result.summary.status is BriefSummaryStatus.WRITTEN
    assert [s.text for s in result.summary.sentences] == ["1 case đang bị chặn."]
    assert result.summary.dropped == 2
    # Checked against the brief it arrived with: every cited key resolves.
    assert all(
        result.brief.group(key) is not None
        for sentence in result.summary.sentences
        for key in sentence.group_keys
    )
    (run_context,) = gateway.calls
    assert run_context.tenant_id == context.tenant_id
    assert run_context.worker_id == "supply_chain.daily_brief_summary"


async def test_an_answer_outside_the_schema_degrades_to_unavailable() -> None:
    context = _context(scopes=_BRIEF_READ)
    gateway = _SummaryGateway(error=ModelOutputInvalidError("off-schema every attempt"))
    result = await _summarizer(await _repo_with_a_blocked_case(context), gateway).handle(context)
    assert result.summary.status is BriefSummaryStatus.UNAVAILABLE
    assert result.summary.sentences == ()
    assert [g.key for g in result.brief.groups] == ["case_blocked"]


async def test_a_refusal_other_than_the_schema_is_not_swallowed() -> None:
    """A budget refusal or an outage stays what it is — "unavailable" would
    hide that the tenant is out of budget."""
    context = _context(scopes=_BRIEF_READ)
    gateway = _SummaryGateway(error=DomainError("run budget exceeded"))
    with pytest.raises(DomainError, match="budget"):
        await _summarizer(await _repo_with_a_blocked_case(context), gateway).handle(context)


# -- Per-step duties ------------------------------------------------------------


def _duty_context(*duties: CaseDuty, extra: frozenset[str] = frozenset()) -> AccessContext:
    return _context(
        scopes=frozenset({"supply_chain.po_case.read"})
        | frozenset(f"supply_chain.duty.{duty.value}" for duty in duties)
        | extra
    )


async def _case_waiting_for_its_deposit(
    repo: FakePOCaseRepository,
) -> POCase:
    case = await _seeded_case(repo, _context())
    case.request_deposit()
    await repo.save(_context(), case)
    return case


async def test_finance_confirms_a_deposit_it_did_not_request() -> None:
    repo = FakePOCaseRepository()
    case = await _case_waiting_for_its_deposit(repo)

    result = await _advance_po_case_handler(repo).handle(
        _duty_context(CaseDuty.FINANCE), po_case_id=case.id, action=CaseAction.CONFIRM_DEPOSIT
    )

    assert isinstance(result, CaseActionApplied)
    assert result.case.state is CaseState.DEPOSIT_CONFIRMED


async def test_a_step_outside_the_callers_duties_is_refused_and_nothing_moves() -> None:
    repo = FakePOCaseRepository()
    case = await _case_waiting_for_its_deposit(repo)

    with pytest.raises(PermissionDeniedError) as refused:
        await _advance_po_case_handler(repo).handle(
            _duty_context(CaseDuty.ORDERING),
            po_case_id=case.id,
            action=CaseAction.CONFIRM_DEPOSIT,
        )

    assert refused.value.details["action"] == "supply_chain.duty.finance"
    assert repo.by_id[case.id.value].state is CaseState.WAITING_DEPOSIT


async def test_a_general_case_write_takes_no_step_on_its_own() -> None:
    """Opening cases is one authority; taking a step is another."""
    repo = FakePOCaseRepository()
    case = await _seeded_case(repo, _context())

    with pytest.raises(PermissionDeniedError):
        await _advance_po_case_handler(repo).handle(
            _duty_context(extra=frozenset({"supply_chain.po_case.write"})),
            po_case_id=case.id,
            action=CaseAction.REQUEST_DEPOSIT,
        )


async def test_the_tenants_own_mapping_decides_which_duty_a_step_needs() -> None:
    repo, overrides = FakePOCaseRepository(), FakePolicyOverrideRepository()
    case = await _case_waiting_for_its_deposit(repo)
    shipped = load_supply_chain_action_duties(_SHIPPED_ACTION_DUTIES)
    # This company lets purchasing record the deposit itself.
    moved = shipped.model_copy(
        update={
            "action_duties": {
                **shipped.action_duties,
                CaseAction.CONFIRM_DEPOSIT: CaseDuty.ORDERING,
            }
        }
    )
    await overrides.put(
        _context(),
        "supply_chain_action_duties",
        moved.model_dump(mode="json"),
        audit=_audit_event(_context()),
    )
    handler = _advance_po_case_handler(repo, policy_override_repo=overrides)

    with pytest.raises(PermissionDeniedError):
        await handler.handle(
            _duty_context(CaseDuty.FINANCE), po_case_id=case.id, action=CaseAction.CONFIRM_DEPOSIT
        )
    result = await handler.handle(
        _duty_context(CaseDuty.ORDERING), po_case_id=case.id, action=CaseAction.CONFIRM_DEPOSIT
    )
    assert isinstance(result, CaseActionApplied)


async def test_a_step_that_needs_approval_starts_no_run_for_a_caller_without_its_duty() -> None:
    """The approval path starts only after the duty check: a request nobody
    permitted never reaches an approver's inbox."""
    repo = FakePOCaseRepository()
    case = await _seeded_case(repo, _context())
    runner = FakeWorkflowRunnerPort()
    matrix = _approval_matrix(approval_required_actions=frozenset({CaseAction.CANCEL}))

    with pytest.raises(PermissionDeniedError):
        await _advance_po_case_handler(
            repo, platform_default_approval_matrix=matrix, runner=runner
        ).handle(
            _duty_context(CaseDuty.FINANCE),
            po_case_id=case.id,
            action=CaseAction.CANCEL,
            reason="not ours to cancel",
        )
    assert runner.started == []


async def test_action_duties_read_back_the_platform_default_then_the_tenants_own() -> None:
    overrides = FakePolicyOverrideRepository()
    context = _context(
        scopes=frozenset({"supply_chain.action_duties.read", "supply_chain.action_duties.write"})
    )
    shipped = load_supply_chain_action_duties(_SHIPPED_ACTION_DUTIES)
    get_handler = GetActionDuties(
        policy_override_repo=overrides,
        platform_default_duties=shipped,
        authz=ScopeAuthorizationService(),
    )
    set_handler = SetActionDutiesOverride(
        policy_override_repo=overrides,
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
        clock=FixedClock(_NOW),
    )

    assert (await get_handler.handle(context)).duty_for(CaseAction.CONFIRM_PAYMENT) is (
        CaseDuty.FINANCE
    )
    moved = shipped.model_copy(
        update={"action_duties": {**shipped.action_duties, CaseAction.COMPLETE: CaseDuty.LOGISTICS}}
    )
    await set_handler.handle(context, moved)
    assert (await get_handler.handle(context)).duty_for(CaseAction.COMPLETE) is CaseDuty.LOGISTICS
    (audit,) = overrides.audit_events
    assert audit.action == "supply_chain.action_duties.override_set"


async def test_action_duties_handlers_refuse_without_their_scopes() -> None:
    overrides = FakePolicyOverrideRepository()
    shipped = load_supply_chain_action_duties(_SHIPPED_ACTION_DUTIES)
    with pytest.raises(PermissionDeniedError):
        await GetActionDuties(
            policy_override_repo=overrides,
            platform_default_duties=shipped,
            authz=ScopeAuthorizationService(),
        ).handle(_context(scopes=frozenset()))
    with pytest.raises(PermissionDeniedError):
        await SetActionDutiesOverride(
            policy_override_repo=overrides,
            authz=ScopeAuthorizationService(),
            ids=Uuid4Generator(),
            clock=FixedClock(_NOW),
        ).handle(_context(scopes=frozenset({"supply_chain.action_duties.read"})), shipped)
    assert overrides.by_tenant_and_policy == {}


async def test_product_action_duties_handlers_refuse_without_their_scopes() -> None:
    # Setting this mapping decides who may take every product step, so the
    # refusal is pinned where the write happens, not only at the route.
    overrides = FakePolicyOverrideRepository()
    shipped = load_supply_chain_product_action_duties(_SHIPPED_PRODUCT_ACTION_DUTIES)
    with pytest.raises(PermissionDeniedError):
        await GetProductActionDuties(
            policy_override_repo=overrides,
            platform_default_duties=shipped,
            authz=ScopeAuthorizationService(),
        ).handle(_context(scopes=frozenset()))
    with pytest.raises(PermissionDeniedError):
        await SetProductActionDutiesOverride(
            policy_override_repo=overrides,
            authz=ScopeAuthorizationService(),
            ids=Uuid4Generator(),
            clock=FixedClock(_NOW),
        ).handle(_context(scopes=frozenset({"supply_chain.action_duties.read"})), shipped)
    assert overrides.by_tenant_and_policy == {}
