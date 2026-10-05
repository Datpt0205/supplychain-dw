"""HTTP surface. Authorization belongs where the mutation is (handlers.py).

No `from __future__ import annotations` here, on purpose: FastAPI resolves a
route's parameter types at call time, and postponed evaluation turns
`Annotated[AccessContext, Depends(resolve_access_context)]` into a string
FastAPI would have to re-evaluate against this module's globals — which does
not see `resolve_access_context`, a value closed over from `build_router`'s
own parameter, not a module-level name. Real objects in the annotation avoid
the whole question.
"""

import re
import unicodedata
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Literal, Protocol, TypeVar

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from dw_kernel.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.action_duties import SupplyChainActionDuties
from dw_supply_chain.application.handlers import (
    AdvancePOCase,
    AdvancePOCaseResult,
    AnalyzeDelayImpact,
    AnswerCaseQuery,
    AttentionItem,
    CaseActionApplied,
    CaseQueryAnswer,
    CloseFollowUp,
    CreatePOCase,
    FollowUpView,
    GetActionDuties,
    GetApprovalMatrix,
    GetAttentionQueue,
    GetBriefPolicy,
    GetDailyBrief,
    GetFollowUpPolicy,
    GetMissingUpdateStatus,
    GetPOCase,
    GetPortfolioSummary,
    GetSLAEvaluation,
    GetSLAPolicy,
    ListCaseTransitions,
    ListDelayImpactAnalyses,
    ListFollowUps,
    ListPOCases,
    ListSupplierUpdates,
    SetActionDutiesOverride,
    SetApprovalMatrixOverride,
    SetBriefPolicyOverride,
    SetFollowUpPolicyOverride,
    SetSLAPolicyOverride,
    SubmitSupplierUpdate,
    SummarizeDailyBrief,
)
from dw_supply_chain.application.ports import POCaseListFilter
from dw_supply_chain.approval_matrix import SupplyChainApprovalMatrix
from dw_supply_chain.brief_policy import SupplyChainBriefPolicy
from dw_supply_chain.domain.brief_summary import BriefSummary, BriefSummaryStatus
from dw_supply_chain.domain.case_query import CaseQueryKind, CaseQueryOutcome, GroundedField
from dw_supply_chain.domain.daily_brief import BriefEntry, BriefGroup, BriefSignal, DailyBrief
from dw_supply_chain.domain.delay_impact import DelayImpactAnalysis
from dw_supply_chain.domain.follow_up import FollowUpKind
from dw_supply_chain.domain.missing_update import MissingUpdateAssessment, MissingUpdateStatus
from dw_supply_chain.domain.po_case import CaseAction, CaseState, CaseTransition, POCase, POCaseId
from dw_supply_chain.domain.portfolio import PortfolioSummary
from dw_supply_chain.domain.sla_evaluation import SLAEvaluation, SLAEvaluationStatus
from dw_supply_chain.domain.supplier_update import (
    SupplierEventType,
    SupplierUpdate,
    SupplierUpdateId,
)
from dw_supply_chain.follow_up_policy import SupplyChainFollowUpPolicy
from dw_supply_chain.sla_policy import SupplyChainSLAPolicy

# Declared here, satisfied by the composition root: this context needs a way
# to resolve the caller's verified identity from a request, but must not
# import `dw_api` to get one — `dw_api` imports `dw_supply_chain`, not the
# other way, and the import-linter "Supply Chain is independent" contract
# enforces exactly this direction. `...` rather than a fixed parameter list:
# the real function (`dw_api`'s `get_access_context`) takes a second,
# `Depends`-satisfied parameter FastAPI resolves on its own, which this
# context has no reason to know the shape of.
AccessContextResolver = Callable[..., Awaitable[AccessContext]]

_ResultT = TypeVar("_ResultT", bound=BaseModel)


class SupportsIdempotentRecord(Protocol):
    """The one method a route needs from `dw_api`'s `IdempotentOperation` —
    declared narrowly here rather than importing that class, for the same
    import-direction reason `AccessContextResolver` exists. `claim`/
    `abandon_unless_recorded` stay inside the composition root's own
    dependency (`get_idempotent_operation`'s generator body), which runs
    before/after this whole request regardless of what the route does."""

    async def record(self, result: _ResultT, *, status_code: int = 200) -> _ResultT: ...


# Loose on purpose: the real value (`dw_api`'s `get_idempotent_operation`) is a
# generator-based FastAPI dependency with its own `Depends`-satisfied
# parameters, the same shape `AccessContextResolver` already declines to pin
# down precisely — FastAPI's own dependency resolution handles the mechanics
# either way.
IdempotencyResolver = Callable[..., object]


# No NUL byte in free text that reaches PostgreSQL: text cannot hold one,
# so it would otherwise reach asyncpg and come back as a 500 instead of the
# 422 it is.
_NO_NUL = r"^[^\x00]*$"
# One limit for a supplier name, on the way in and on the way back out as a
# list filter — a name the create path accepts must also be one the Control
# Tower's drill-down link can ask for, or that link is a 422.
_SUPPLIER_NAME_MAX_LENGTH = 200


class CreatePOCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    po_reference: str = Field(min_length=1, max_length=200)
    supplier_name: str = Field(min_length=1, max_length=_SUPPLIER_NAME_MAX_LENGTH, pattern=_NO_NUL)


class POCaseView(BaseModel):
    id: uuid.UUID
    po_reference: str
    supplier_name: str
    state: CaseState
    interrupted_state: CaseState | None
    created_at: datetime | None
    version: int


def _view(case: POCase) -> POCaseView:
    return POCaseView(
        id=case.id.value,
        po_reference=case.po_reference,
        supplier_name=case.supplier_name,
        state=case.state,
        interrupted_state=case.interrupted_state,
        created_at=case.created_at,
        version=case.version,
    )


class SubmitSupplierUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_text: str = Field(min_length=1, max_length=4000)


class SupplierUpdateView(BaseModel):
    id: uuid.UUID
    po_case_id: uuid.UUID
    raw_text: str
    event_type: SupplierEventType
    affected_po: str | None
    delay_days: int | None
    reason: str
    proposed_action: str
    confidence: float
    source_ref: str
    requires_confirmation: bool
    created_at: datetime | None


def _supplier_update_view(update: SupplierUpdate) -> SupplierUpdateView:
    return SupplierUpdateView(
        id=update.id.value,
        po_case_id=update.po_case_id.value,
        raw_text=update.raw_text,
        event_type=update.extraction.event_type,
        affected_po=update.extraction.affected_po,
        delay_days=update.extraction.delay_days,
        reason=update.extraction.reason,
        proposed_action=update.extraction.proposed_action,
        confidence=update.extraction.confidence,
        source_ref=update.extraction.source_ref,
        requires_confirmation=update.requires_confirmation,
        created_at=update.created_at,
    )


class ImpactedMilestoneView(BaseModel):
    milestone: CaseState
    estimated_delay_days: int


class MitigationOptionView(BaseModel):
    description: str
    tradeoff: str


class DelayImpactAnalysisView(BaseModel):
    id: uuid.UUID
    po_case_id: uuid.UUID
    supplier_update_id: uuid.UUID
    delay_days: int
    impacted_milestones: list[ImpactedMilestoneView]
    assumptions: list[str]
    mitigation_options: list[MitigationOptionView]
    created_at: datetime | None


def _delay_impact_view(analysis: DelayImpactAnalysis) -> DelayImpactAnalysisView:
    return DelayImpactAnalysisView(
        id=analysis.id.value,
        po_case_id=analysis.po_case_id.value,
        supplier_update_id=analysis.supplier_update_id.value,
        delay_days=analysis.delay_days,
        impacted_milestones=[
            ImpactedMilestoneView(
                milestone=estimate.milestone, estimated_delay_days=estimate.estimated_delay_days
            )
            for estimate in analysis.impacted_milestones
        ],
        assumptions=analysis.extraction.assumptions,
        mitigation_options=[
            MitigationOptionView(description=option.description, tradeoff=option.tradeoff)
            for option in analysis.extraction.mitigation_options
        ],
        created_at=analysis.created_at,
    )


class MissingUpdateStatusView(BaseModel):
    status: MissingUpdateStatus
    reference_at: datetime
    age_days: int


def _missing_update_view(assessment: MissingUpdateAssessment) -> MissingUpdateStatusView:
    return MissingUpdateStatusView(
        status=assessment.status,
        reference_at=assessment.reference_at,
        age_days=assessment.age_days,
    )


class AdvancePOCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # A closed choice from a UI's own context-sensitive buttons, not free
    # text — see `CaseAction`'s own docstring. An unrecognised action name
    # is a 422 from Pydantic before this ever reaches the handler.
    action: CaseAction
    reason: str | None = Field(default=None, max_length=2000)


class CaseActionResultView(BaseModel):
    status: Literal["applied", "pending_approval"]
    # Populated only when status == "applied".
    case: POCaseView | None = None
    # Populated only when status == "pending_approval" — the id
    # `GET /api/v1/runs/{run_id}` and `GET /api/v1/approvals` (both already
    # generic, no new route needed) resolve against.
    run_id: uuid.UUID | None = None


def _case_action_result_view(result: AdvancePOCaseResult) -> CaseActionResultView:
    if isinstance(result, CaseActionApplied):
        return CaseActionResultView(status="applied", case=_view(result.case))
    return CaseActionResultView(status="pending_approval", run_id=result.run_id)


class CaseTransitionView(BaseModel):
    from_state: CaseState
    to_state: CaseState
    reason: str | None
    occurred_at: datetime


def _case_transition_view(transition: CaseTransition) -> CaseTransitionView:
    return CaseTransitionView(
        from_state=transition.from_state,
        to_state=transition.to_state,
        reason=transition.reason,
        occurred_at=transition.occurred_at,
    )


class SLAEvaluationView(BaseModel):
    status: SLAEvaluationStatus
    milestone: str | None
    entered_current_state_at: datetime
    age_days: int
    threshold_days: int | None


def _sla_evaluation_view(evaluation: SLAEvaluation) -> SLAEvaluationView:
    return SLAEvaluationView(
        status=evaluation.status,
        milestone=evaluation.milestone,
        entered_current_state_at=evaluation.entered_current_state_at,
        age_days=evaluation.age_days,
        threshold_days=evaluation.threshold_days,
    )


class AttentionItemView(BaseModel):
    case: POCaseView
    # Populated only when that signal is the reason this case is flagged —
    # see `AttentionItem`'s own docstring.
    sla: SLAEvaluationView | None
    missing_update: MissingUpdateStatusView | None


def _attention_item_view(item: AttentionItem) -> AttentionItemView:
    return AttentionItemView(
        case=_view(item.case),
        sla=_sla_evaluation_view(item.sla) if item.sla is not None else None,
        missing_update=_missing_update_view(item.missing_update)
        if item.missing_update is not None
        else None,
    )


class StateSummaryView(BaseModel):
    state: CaseState
    case_count: int
    sla_breached_count: int
    update_overdue_count: int
    oldest_in_state_days: int


class SupplierSummaryView(BaseModel):
    supplier_name: str
    case_count: int
    update_overdue_count: int
    escalation_due_count: int
    sla_breached_count: int
    longest_silence_days: int


class PortfolioSummaryView(BaseModel):
    active_case_count: int
    sla_breached_count: int
    update_overdue_count: int
    # Order is the domain's own (`PortfolioSummary`'s field comments) — a
    # client renders it as given rather than re-sorting.
    by_state: list[StateSummaryView]
    by_supplier: list[SupplierSummaryView]


def _portfolio_summary_view(summary: PortfolioSummary) -> PortfolioSummaryView:
    return PortfolioSummaryView(
        active_case_count=summary.active_case_count,
        sla_breached_count=summary.sla_breached_count,
        update_overdue_count=summary.update_overdue_count,
        by_state=[
            StateSummaryView(
                state=row.state,
                case_count=row.case_count,
                sla_breached_count=row.sla_breached_count,
                update_overdue_count=row.update_overdue_count,
                oldest_in_state_days=row.oldest_in_state_days,
            )
            for row in summary.by_state
        ],
        by_supplier=[
            SupplierSummaryView(
                supplier_name=row.supplier_name,
                case_count=row.case_count,
                update_overdue_count=row.update_overdue_count,
                escalation_due_count=row.escalation_due_count,
                sla_breached_count=row.sla_breached_count,
                longest_silence_days=row.longest_silence_days,
            )
            for row in summary.by_supplier
        ],
    )


class BriefEntryView(BaseModel):
    case: POCaseView
    # The figure that put the case in its group — see `BriefEntry`.
    days: int | None
    limit_days: int | None
    transition: CaseTransitionView | None
    approval_action: str | None


class BriefGroupView(BaseModel):
    # Stable within one brief: `signal`, or `signal:qualifier`.
    key: str
    signal: BriefSignal
    # The SLA milestone for `sla_breached`, the state for `waiting_on_us`.
    qualifier: str | None
    # The one state every case in the group is in, when a state defines the
    # group — what the PO case list can be filtered to for the whole group.
    state: CaseState | None
    total: int
    # Longest-standing first, at most ten; `total` counts every case.
    entries: list[BriefEntryView]


class DailyBriefView(BaseModel):
    generated_at: datetime
    active_case_count: int
    flagged_case_count: int
    # False when the caller may not read the approval inbox: pending
    # approvals were not looked at, which is not the same as "none pending".
    approvals_visible: bool
    # In the tenant's own `signal_order`; a client renders them as given.
    groups: list[BriefGroupView]


def _brief_entry_view(entry: BriefEntry) -> BriefEntryView:
    return BriefEntryView(
        case=_view(entry.case),
        days=entry.days,
        limit_days=entry.limit_days,
        transition=_case_transition_view(entry.transition) if entry.transition else None,
        approval_action=entry.approval_action,
    )


def _brief_group_view(group: BriefGroup) -> BriefGroupView:
    return BriefGroupView(
        key=group.key,
        signal=group.signal,
        qualifier=group.qualifier,
        state=group.state,
        total=group.total,
        entries=[_brief_entry_view(entry) for entry in group.shown_entries],
    )


def _daily_brief_view(brief: DailyBrief) -> DailyBriefView:
    return DailyBriefView(
        generated_at=brief.generated_at,
        active_case_count=brief.active_case_count,
        flagged_case_count=brief.flagged_case_count,
        approvals_visible=brief.approvals_visible,
        groups=[_brief_group_view(group) for group in brief.groups],
    )


class BriefSummarySentenceView(BaseModel):
    text: str
    # The keys of the brief groups the sentence is about — every one a group
    # of the `brief` this summary arrived with.
    group_keys: list[str]


class BriefSummaryView(BaseModel):
    status: BriefSummaryStatus
    # Only sentences that checked out against the brief; written by a model.
    sentences: list[BriefSummarySentenceView]
    # How many sentences the model wrote that failed a check and were dropped.
    dropped: int


class DailyBriefSummaryView(BaseModel):
    # The brief the summary was written from and checked against — a client
    # renders this one, not an older copy, so every cited key resolves.
    brief: DailyBriefView
    summary: BriefSummaryView


def _brief_summary_view(summary: BriefSummary) -> BriefSummaryView:
    return BriefSummaryView(
        status=summary.status,
        sentences=[
            BriefSummarySentenceView(text=sentence.text, group_keys=list(sentence.group_keys))
            for sentence in summary.sentences
        ],
        dropped=summary.dropped,
    )


# The case-query prompt frames the question as data inside <input>...</input>.
# A question carrying either tag could close that block early and put its own
# text where the prompt's instructions live. Checked after NFKC folding with
# zero-width characters removed, so fullwidth angle brackets or a "<" + ZWSP +
# "/input" lookalike are caught too, and spacing inside the tag is allowed for.
_PROMPT_WRAPPER_TAG = re.compile(r"<\s*/?\s*input\b", re.IGNORECASE)
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"))


def _carries_prompt_wrapper_tag(question: str) -> bool:
    folded = unicodedata.normalize("NFKC", question).translate(_ZERO_WIDTH)
    return _PROMPT_WRAPPER_TAG.search(folded) is not None


class CaseQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=500, pattern=_NO_NUL)

    @field_validator("question")
    @classmethod
    def _answerable(cls, question: str) -> str:
        if not question.strip():
            # Nothing to read — refused here rather than spending a model
            # call to be told so.
            raise ValueError("question must not be blank")
        if _carries_prompt_wrapper_tag(question):
            raise ValueError("question must not contain an <input> tag")
        return question


class CitationView(BaseModel):
    """The question's own words one field was grounded in — reported
    whatever the outcome, so a refusal can show what WAS understood."""

    field: GroundedField
    quote: str


class UnderstoodView(BaseModel):
    """What the answer applied, each value decided by code: a supplier is
    always a stored name, never the model's mention; a PO reference is the
    stored one once the case was found (the question's own spelling when it
    was not)."""

    state: CaseState | None
    supplier_name: str | None
    active_only: bool
    po_reference: str | None


class CaseTableDataView(BaseModel):
    type: Literal["case_table"]
    rows: list[POCaseView]
    # More matched than the answer carries — the client offers the full,
    # paged list for the same `understood` filter.
    has_more: bool


class CaseLinkDataView(BaseModel):
    type: Literal["case_link"]
    case: POCaseView


class AIWorkResponseView(BaseModel):
    """A structured AI answer, the parts this feature fills.

    Structured data only, and `data_view` is a closed set of types a client
    renders with its own components — nothing here is markup, a link, or an
    action a model chose. No `narrative`: no model writes one yet, and the
    client composes its sentence from these fields. No `suggested_actions`:
    nothing here changes state yet (confirmation and execution come with
    the first action that does).
    """

    intent: CaseQueryKind
    outcome: CaseQueryOutcome
    understood: UnderstoodView
    citations: list[CitationView]
    # Fields the model claimed that the question gave no grounds for.
    ignored_fields: list[GroundedField]
    # Fields the question did state that this kind of answer cannot apply
    # (a list cannot narrow to one PO) — the other reason a reading is refused.
    unusable_fields: list[GroundedField]
    # Supplier names or PO references the question matched more than one
    # of — shown to the person, never picked from.
    candidates: list[str]
    data_view: Annotated[CaseTableDataView | CaseLinkDataView, Field(discriminator="type")] | None


def _ai_work_response_view(answer: CaseQueryAnswer) -> AIWorkResponseView:
    plan = answer.plan
    data_view: CaseTableDataView | CaseLinkDataView | None = None
    if answer.opened is not None:
        data_view = CaseLinkDataView(type="case_link", case=_view(answer.opened))
    elif plan.outcome in (CaseQueryOutcome.LIST, CaseQueryOutcome.PO_AMBIGUOUS):
        data_view = CaseTableDataView(
            type="case_table",
            rows=[_view(case) for case in answer.cases],
            has_more=answer.has_more,
        )
    return AIWorkResponseView(
        intent=answer.intent,
        outcome=plan.outcome,
        understood=UnderstoodView(
            state=plan.state,
            supplier_name=plan.supplier_name,
            active_only=plan.active_only,
            po_reference=plan.po_reference,
        ),
        citations=[CitationView(field=field, quote=quote) for field, quote in answer.citations],
        ignored_fields=list(answer.ignored),
        unusable_fields=list(answer.unusable),
        candidates=list(plan.candidates),
        data_view=data_view,
    )


class FollowUpItemView(BaseModel):
    """An open follow-up. `mine`: the caller holds a scope it was handed to,
    so the caller is expected to act, and may close it."""

    id: uuid.UUID
    po_case_id: uuid.UUID
    po_reference: str
    supplier_name: str
    kind: FollowUpKind
    milestone: str | None
    days: int
    limit_days: int | None
    opened_at: datetime
    notified_at: datetime | None
    mine: bool


class CloseFollowUpRequest(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


def _follow_up_view(item: FollowUpView) -> FollowUpItemView:
    record = item.record
    return FollowUpItemView(
        id=record.id,
        po_case_id=record.po_case_id,
        po_reference=record.po_reference,
        supplier_name=record.supplier_name,
        kind=record.kind,
        milestone=record.milestone,
        days=record.days,
        limit_days=record.limit_days,
        opened_at=record.opened_at,
        notified_at=record.notified_at,
        mine=item.mine,
    )


def build_router(
    create: CreatePOCase,
    get: GetPOCase,
    list_cases: ListPOCases,
    submit_supplier_update: SubmitSupplierUpdate,
    list_supplier_updates: ListSupplierUpdates,
    analyze_delay_impact: AnalyzeDelayImpact,
    list_delay_impact_analyses: ListDelayImpactAnalyses,
    get_missing_update_status: GetMissingUpdateStatus,
    advance_po_case: AdvancePOCase,
    list_case_transitions: ListCaseTransitions,
    get_sla_evaluation: GetSLAEvaluation,
    get_sla_policy: GetSLAPolicy,
    set_sla_policy_override: SetSLAPolicyOverride,
    get_approval_matrix: GetApprovalMatrix,
    set_approval_matrix_override: SetApprovalMatrixOverride,
    get_attention_queue: GetAttentionQueue,
    get_portfolio_summary: GetPortfolioSummary,
    answer_case_query: AnswerCaseQuery,
    get_daily_brief: GetDailyBrief,
    get_brief_policy: GetBriefPolicy,
    set_brief_policy_override: SetBriefPolicyOverride,
    summarize_daily_brief: SummarizeDailyBrief,
    get_action_duties: GetActionDuties,
    set_action_duties_override: SetActionDutiesOverride,
    list_follow_ups: ListFollowUps,
    close_follow_up: CloseFollowUp,
    get_follow_up_policy: GetFollowUpPolicy,
    set_follow_up_policy_override: SetFollowUpPolicyOverride,
    *,
    resolve_access_context: AccessContextResolver,
    resolve_idempotency: IdempotencyResolver,
) -> APIRouter:
    """Injected, never resolved from a global: the app owns the wiring.

    The router is created fresh here rather than reused from a module-level
    instance — a shared module-level `APIRouter` that this function then
    decorates onto accumulates a duplicate route registration (and FastAPI's
    "Duplicate Operation ID" warning) every time it is called, which the
    scaffold's own generated placeholder did.
    """
    router = APIRouter(prefix="/api/v1/supply-chain", tags=["supply_chain"])
    require_access_context = Annotated[AccessContext, Depends(resolve_access_context)]
    require_idempotency = Annotated[SupportsIdempotentRecord, Depends(resolve_idempotency)]

    @router.post("/po-cases", response_model=POCaseView)
    async def create_po_case(
        body: CreatePOCaseRequest,
        context: require_access_context,
        idempotency: require_idempotency,
    ) -> POCaseView:
        case = await create.handle(
            context, po_reference=body.po_reference, supplier_name=body.supplier_name
        )
        return await idempotency.record(_view(case))

    @router.get("/po-cases", response_model=Page[POCaseView])
    async def list_po_cases(
        context: require_access_context,
        limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
        cursor: str | None = Query(default=None, description="Opaque cursor from a previous page."),
        state: Annotated[CaseState | None, Query(description="Only cases in this state.")] = None,
        supplier_name: Annotated[
            str | None,
            Query(
                min_length=1,
                max_length=_SUPPLIER_NAME_MAX_LENGTH,
                pattern=_NO_NUL,
                description="Only this supplier's cases — an exact match on the stored name.",
            ),
        ] = None,
        active_only: Annotated[
            bool,
            Query(
                description="Only active cases — every state the server does not treat as"
                " terminal, the same set the Control Tower counts."
            ),
        ] = False,
    ) -> Page[POCaseView]:
        case_filter = POCaseListFilter(
            state=state, supplier_name=supplier_name, active_only=active_only
        )
        page = await list_cases.handle(context, case_filter, limit=limit, cursor=cursor)
        return page.map_items(_view)

    @router.get("/attention-queue", response_model=list[AttentionItemView])
    async def get_attention_queue_route(
        context: require_access_context,
    ) -> list[AttentionItemView]:
        items = await get_attention_queue.handle(context)
        return [_attention_item_view(item) for item in items]

    @router.get("/control-tower/summary", response_model=PortfolioSummaryView)
    async def get_portfolio_summary_route(
        context: require_access_context,
    ) -> PortfolioSummaryView:
        summary = await get_portfolio_summary.handle(context)
        return _portfolio_summary_view(summary)

    @router.get("/daily-brief", response_model=DailyBriefView)
    async def get_daily_brief_route(context: require_access_context) -> DailyBriefView:
        """What needs handling now, grouped by deterministic signal and
        ordered by the caller's tenant's own brief policy."""
        brief = await get_daily_brief.handle(context)
        return _daily_brief_view(brief)

    @router.post("/daily-brief/summary", response_model=DailyBriefSummaryView)
    async def summarize_daily_brief_route(
        context: require_access_context,
    ) -> DailyBriefSummaryView:
        """The brief, with a model's summary of it that code has checked.
        A POST because it spends a model call; asked for explicitly, never on
        page load. No `Idempotency-Key`: it changes nothing, and a stored
        reply replayed under a key would skip the handler's own
        authorization."""
        result = await summarize_daily_brief.handle(context)
        return DailyBriefSummaryView(
            brief=_daily_brief_view(result.brief), summary=_brief_summary_view(result.summary)
        )

    @router.get("/brief-policy", response_model=SupplyChainBriefPolicy)
    async def get_brief_policy_route(context: require_access_context) -> SupplyChainBriefPolicy:
        """The caller's own tenant's effective brief order — their own
        override if they have set one, the platform default otherwise."""
        return await get_brief_policy.handle(context)

    @router.put("/brief-policy", response_model=SupplyChainBriefPolicy)
    async def set_brief_policy_override_route(
        body: SupplyChainBriefPolicy, context: require_access_context
    ) -> SupplyChainBriefPolicy:
        """Replaces the caller's tenant's own brief order, whole. Every
        signal must be listed exactly once: an override reorders the brief,
        it cannot hide a group from it. No `Idempotency-Key`, same reasoning
        as `/sla-policy`'s own `PUT`."""
        await set_brief_policy_override.handle(context, body)
        return body

    @router.get("/follow-ups", response_model=list[FollowUpItemView])
    async def list_follow_ups_route(context: require_access_context) -> list[FollowUpItemView]:
        """The tenant's open follow-ups, newest first; the caller's own marked."""
        return [_follow_up_view(item) for item in await list_follow_ups.handle(context)]

    @router.post("/follow-ups/{follow_up_id}/done", status_code=204)
    async def close_follow_up_route(
        follow_up_id: uuid.UUID, body: CloseFollowUpRequest, context: require_access_context
    ) -> Response:
        """Mark a follow-up handled. Only someone it was handed to may."""
        await close_follow_up.handle(context, follow_up_id, body.note)
        return Response(status_code=204)

    @router.get("/follow-up-policy", response_model=SupplyChainFollowUpPolicy)
    async def get_follow_up_policy_route(
        context: require_access_context,
    ) -> SupplyChainFollowUpPolicy:
        return await get_follow_up_policy.handle(context)

    @router.put("/follow-up-policy", response_model=SupplyChainFollowUpPolicy)
    async def set_follow_up_policy_override_route(
        body: SupplyChainFollowUpPolicy, context: require_access_context
    ) -> SupplyChainFollowUpPolicy:
        """Replaces the tenant's own routing, whole; every kind must still
        reach someone. No `Idempotency-Key`, as for the other policies."""
        await set_follow_up_policy_override.handle(context, body)
        return body

    @router.post("/case-query", response_model=AIWorkResponseView)
    async def answer_case_query_route(
        body: CaseQueryRequest, context: require_access_context
    ) -> AIWorkResponseView:
        """A question about PO cases, answered as structured work.
        No `Idempotency-Key`: it changes nothing, and a stored reply replayed
        under a key would skip the handler's own authorization."""
        answer = await answer_case_query.handle(context, body.question)
        return _ai_work_response_view(answer)

    @router.get("/po-cases/{case_id}", response_model=POCaseView)
    async def get_po_case(case_id: uuid.UUID, context: require_access_context) -> POCaseView:
        case = await get.handle(context, POCaseId(case_id))
        return _view(case)

    @router.post(
        "/po-cases/{case_id}/supplier-updates",
        response_model=SupplierUpdateView,
    )
    async def create_supplier_update(
        case_id: uuid.UUID,
        body: SubmitSupplierUpdateRequest,
        context: require_access_context,
        idempotency: require_idempotency,
    ) -> SupplierUpdateView:
        update = await submit_supplier_update.handle(
            context, po_case_id=POCaseId(case_id), raw_text=body.raw_text
        )
        return await idempotency.record(_supplier_update_view(update))

    @router.get(
        "/po-cases/{case_id}/supplier-updates",
        response_model=list[SupplierUpdateView],
    )
    async def get_supplier_updates(
        case_id: uuid.UUID, context: require_access_context
    ) -> list[SupplierUpdateView]:
        updates = await list_supplier_updates.handle(context, POCaseId(case_id))
        return [_supplier_update_view(update) for update in updates]

    @router.post(
        "/po-cases/{case_id}/supplier-updates/{update_id}/delay-impact-analysis",
        response_model=DelayImpactAnalysisView,
    )
    async def create_delay_impact_analysis(
        case_id: uuid.UUID,
        update_id: uuid.UUID,
        context: require_access_context,
        idempotency: require_idempotency,
    ) -> DelayImpactAnalysisView:
        analysis = await analyze_delay_impact.handle(
            context, po_case_id=POCaseId(case_id), supplier_update_id=SupplierUpdateId(update_id)
        )
        return await idempotency.record(_delay_impact_view(analysis))

    @router.get(
        "/po-cases/{case_id}/delay-impact-analyses",
        response_model=list[DelayImpactAnalysisView],
    )
    async def get_delay_impact_analyses(
        case_id: uuid.UUID, context: require_access_context
    ) -> list[DelayImpactAnalysisView]:
        analyses = await list_delay_impact_analyses.handle(context, POCaseId(case_id))
        return [_delay_impact_view(analysis) for analysis in analyses]

    @router.get(
        "/po-cases/{case_id}/missing-update-status",
        response_model=MissingUpdateStatusView,
    )
    async def get_missing_update_status_route(
        case_id: uuid.UUID, context: require_access_context
    ) -> MissingUpdateStatusView:
        assessment = await get_missing_update_status.handle(context, POCaseId(case_id))
        return _missing_update_view(assessment)

    @router.post("/po-cases/{case_id}/transitions", response_model=CaseActionResultView)
    async def create_po_case_transition(
        case_id: uuid.UUID,
        body: AdvancePOCaseRequest,
        context: require_access_context,
        idempotency: require_idempotency,
        response: Response,
    ) -> CaseActionResultView:
        result = await advance_po_case.handle(
            context, po_case_id=POCaseId(case_id), action=body.action, reason=body.reason
        )
        view = _case_action_result_view(result)
        if view.status == "pending_approval":
            # Accepted, not yet applied — the caller polls GET /runs/{id} or
            # GET /approvals to learn when a human decides. A replayed
            # request under the SAME Idempotency-Key must not start a
            # second run: `status_code=` is passed through to `record` so a
            # later replay of this response also comes back 202, not the
            # 200 every other mutating route here defaults to.
            response.status_code = 202
            return await idempotency.record(view, status_code=202)
        return await idempotency.record(view)

    @router.get(
        "/po-cases/{case_id}/transitions",
        response_model=list[CaseTransitionView],
    )
    async def get_case_transitions(
        case_id: uuid.UUID, context: require_access_context
    ) -> list[CaseTransitionView]:
        transitions = await list_case_transitions.handle(context, POCaseId(case_id))
        return [_case_transition_view(transition) for transition in transitions]

    @router.get("/po-cases/{case_id}/sla-evaluation", response_model=SLAEvaluationView)
    async def get_sla_evaluation_route(
        case_id: uuid.UUID, context: require_access_context
    ) -> SLAEvaluationView:
        evaluation = await get_sla_evaluation.handle(context, POCaseId(case_id))
        return _sla_evaluation_view(evaluation)

    @router.get("/sla-policy", response_model=SupplyChainSLAPolicy)
    async def get_sla_policy_route(context: require_access_context) -> SupplyChainSLAPolicy:
        """The caller's own tenant's effective SLA policy — their own
        override if they have set one, the platform default otherwise."""
        return await get_sla_policy.handle(context)

    @router.put("/sla-policy", response_model=SupplyChainSLAPolicy)
    async def set_sla_policy_override_route(
        body: SupplyChainSLAPolicy, context: require_access_context
    ) -> SupplyChainSLAPolicy:
        """Replaces the caller's tenant's own SLA policy, whole. No
        `Idempotency-Key`: a `PUT` replacing the same key with the same body
        is already idempotent on its own, unlike the `POST`-create routes
        above where a retry could otherwise create a second resource."""
        await set_sla_policy_override.handle(context, body)
        return body

    @router.get("/action-duties", response_model=SupplyChainActionDuties)
    async def get_action_duties_route(context: require_access_context) -> SupplyChainActionDuties:
        """Which duty each case step belongs to for the caller's own tenant —
        their own override if they have set one, the platform default
        otherwise."""
        return await get_action_duties.handle(context)

    @router.put("/action-duties", response_model=SupplyChainActionDuties)
    async def set_action_duties_override_route(
        body: SupplyChainActionDuties, context: require_access_context
    ) -> SupplyChainActionDuties:
        """Replaces the caller's tenant's own step-to-duty mapping, whole.
        Every action must have a duty. No `Idempotency-Key`, same reasoning
        as `/sla-policy`'s own `PUT`."""
        await set_action_duties_override.handle(context, body)
        return body

    @router.get("/approval-matrix", response_model=SupplyChainApprovalMatrix)
    async def get_approval_matrix_route(
        context: require_access_context,
    ) -> SupplyChainApprovalMatrix:
        """The caller's own tenant's effective approval matrix — their own
        override if they have set one, the platform default (nothing
        gated) otherwise."""
        return await get_approval_matrix.handle(context)

    @router.put("/approval-matrix", response_model=SupplyChainApprovalMatrix)
    async def set_approval_matrix_override_route(
        body: SupplyChainApprovalMatrix, context: require_access_context
    ) -> SupplyChainApprovalMatrix:
        """Replaces the caller's tenant's own approval matrix, whole. No
        `Idempotency-Key`, same reasoning as `/sla-policy`'s own `PUT`."""
        await set_approval_matrix_override.handle(context, body)
        return body

    return router
