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
from datetime import date, datetime
from typing import Annotated, Literal, Protocol, TypeVar

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from dw_kernel.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.action_duties import SupplyChainActionDuties
from dw_supply_chain.application.case_query import AnswerCaseQuery, CaseQueryAnswer
from dw_supply_chain.application.handlers import (
    AdvancePOCase,
    AdvancePOCaseResult,
    AnalyzeDelayImpact,
    AttentionItem,
    CaseActionApplied,
    CloseFollowUp,
    CreatePO,
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
    ListPOCaseApprovals,
    ListPOCases,
    ListSupplierUpdates,
    ReassignPOCasePic,
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
from dw_supply_chain.domain.case_document import CaseKind
from dw_supply_chain.domain.case_query import CaseQueryKind, CaseQueryOutcome, GroundedField
from dw_supply_chain.domain.daily_brief import (
    ENTRIES_SHOWN,
    BriefEntry,
    BriefGroup,
    BriefSignal,
    DailyBrief,
    ProductBriefEntry,
)
from dw_supply_chain.domain.delay_impact import DelayImpactAnalysis
from dw_supply_chain.domain.follow_up import FollowUpKind
from dw_supply_chain.domain.missing_update import MissingUpdateAssessment, MissingUpdateStatus
from dw_supply_chain.domain.po_case import (
    CaseAction,
    CaseState,
    CaseTransition,
    OrderKind,
    POCase,
    POCaseId,
)
from dw_supply_chain.domain.portfolio import PortfolioSummary
from dw_supply_chain.domain.product_development_case import (
    ProductDevelopmentCase,
    ProductDevState,
    SampleResult,
)
from dw_supply_chain.domain.sla_evaluation import SLAEvaluation, SLAEvaluationStatus
from dw_supply_chain.domain.supplier_update import (
    SupplierEventType,
    SupplierUpdate,
    SupplierUpdateId,
)
from dw_supply_chain.follow_up_policy import SupplyChainFollowUpPolicy
from dw_supply_chain.sla_policy import SupplyChainSLAPolicy
from dw_supply_chain.workflows.advance_case_graph import APPROVAL_TYPE_PREFIX

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
    """A case opened without stage 1 (a reorder, ADR 0017). No PIC field: the
    caller is the PIC, and an unknown field is a 422. `category`, optional, is
    a key of the tenant's Category list (`GET /product-categories`); a key not
    in it is refused."""

    model_config = ConfigDict(extra="forbid")

    po_reference: str = Field(min_length=1, max_length=200)
    supplier_name: str = Field(min_length=1, max_length=_SUPPLIER_NAME_MAX_LENGTH, pattern=_NO_NUL)
    order_kind: OrderKind
    category: str | None = Field(default=None, min_length=1, max_length=100, pattern=_NO_NUL)


class ReassignPicRequest(BaseModel):
    """Hands a case to another PIC (ticket 06): who, and why. The new PIC
    must be a member of the case's workspace; the reason is required."""

    model_config = ConfigDict(extra="forbid")

    pic_user_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=2000, pattern=_NO_NUL)


class POCaseView(BaseModel):
    id: uuid.UUID
    # Null while the case awaits its PO (`order_requested`, ADR 0017).
    po_reference: str | None
    supplier_name: str
    state: CaseState
    interrupted_state: CaseState | None
    created_at: datetime | None
    version: int
    order_kind: OrderKind
    # The product case ĐẶT HÀNG opened this case from, and the PIC and
    # Category stamped from it; null on a case opened otherwise (no PIC on
    # one opened before ticket 05).
    product_dev_case_id: uuid.UUID | None
    pic_user_id: uuid.UUID | None
    category: str | None


class ProductCaseRefView(BaseModel):
    """A product-development case where it is named in passing (a brief
    group, a command-bar answer): what identifies it and where it stands.
    The case page's own view (`product_case_routes.ProductCaseView`) carries
    the rest."""

    id: uuid.UUID
    proposal_code: str
    product_name: str
    # The Category key it was stamped with; the label is the tenant's list's.
    category: str
    pic_user_id: uuid.UUID
    state: ProductDevState


def _product_ref(case: ProductDevelopmentCase) -> ProductCaseRefView:
    return ProductCaseRefView(
        id=case.id.value,
        proposal_code=case.proposal_code,
        product_name=case.product_name,
        category=case.category,
        pic_user_id=case.pic_user_id,
        state=case.state,
    )


def _view(case: POCase) -> POCaseView:
    return POCaseView(
        id=case.id.value,
        po_reference=case.po_reference,
        supplier_name=case.supplier_name,
        state=case.state,
        interrupted_state=case.interrupted_state,
        created_at=case.created_at,
        version=case.version,
        order_kind=case.order_kind,
        product_dev_case_id=case.product_dev_case_id,
        pic_user_id=case.pic_user_id,
        category=case.category,
    )


class POCaseLineView(BaseModel):
    """A planned line: a SKU of the product and how many (null until step
    10 sets it, when the SKU's planned quantity was open)."""

    sku_id: uuid.UUID
    sku_code: str | None
    variant_label: str | None
    quantity: int | None


class POCaseDetailView(POCaseView):
    lines: list[POCaseLineView]
    # Steps 13-15 (ticket ai-automation/17): null until the step that learns it.
    etd: date | None
    eta: date | None
    container_number: str | None


def _detail_view(case: POCase) -> POCaseDetailView:
    return POCaseDetailView.model_validate(
        {
            **_view(case).model_dump(),
            "lines": [
                POCaseLineView(
                    sku_id=line.sku_id,
                    sku_code=line.sku_code,
                    variant_label=line.variant_label,
                    quantity=line.quantity,
                )
                for line in case.lines
            ],
            "etd": case.shipping.etd,
            "eta": case.shipping.eta,
            "container_number": case.shipping.container_number,
        }
    )


class POLineQuantity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku_id: uuid.UUID
    quantity: int = Field(gt=0, le=10_000_000)


class CreatePORequest(BaseModel):
    """Step 10: the PO's reference and kind, and the quantity of any line
    still open or to correct."""

    model_config = ConfigDict(extra="forbid")

    po_reference: str = Field(min_length=1, max_length=200, pattern=_NO_NUL)
    order_kind: OrderKind
    lines: list[POLineQuantity] = Field(default_factory=list, max_length=500)


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

    @field_validator("action")
    @classmethod
    def _a_plain_step(cls, action: CaseAction) -> CaseAction:
        if action is CaseAction.CREATE_PO:
            raise ValueError("create_po is taken at /po-cases/{case_id}/create-po")
        return action


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
    # null only for an imported case's first row (ticket onboarding/02).
    from_state: CaseState | None
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


class CaseApprovalView(BaseModel):
    id: uuid.UUID
    # The step waiting on the decision (`approval_type` without its prefix).
    action: str
    requested_at: datetime | None


class CaseApprovalsView(BaseModel):
    # False when the caller may not read the approval inbox: not looked at,
    # which is not the same as none pending.
    visible: bool
    # Every pending approval naming the case; `items` is the newest few.
    total: int
    items: list[CaseApprovalView]


class SLAEvaluationView(BaseModel):
    status: SLAEvaluationStatus
    milestone: str | None
    entered_current_state_at: datetime
    age_days: int
    threshold_days: int | None


def sla_evaluation_view(evaluation: SLAEvaluation) -> SLAEvaluationView:
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
        sla=sla_evaluation_view(item.sla) if item.sla is not None else None,
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


class ProductBriefEntryView(BaseModel):
    case: ProductCaseRefView
    # Days in its state (with `limit_days`, the stage-1 SLA it overran).
    days: int | None
    limit_days: int | None
    # A sample group's round, closed today: its number and result. What R&D
    # wrote on it stays on the case page.
    round_no: int | None
    sample_result: SampleResult | None


class BriefGroupView(BaseModel):
    # Stable within one brief: `signal`, or `signal:qualifier`.
    key: str
    signal: BriefSignal
    # The SLA milestone for `sla_breached` and `product_sla_breached`, the
    # state for `waiting_on_us`, the result for `sample_evaluated_today`.
    qualifier: str | None
    # The one state every case in the group is in, when a state defines the
    # group — what the PO case list can be filtered to for the whole group.
    state: CaseState | None
    # The same for a stage-1 group, for the product case list.
    product_state: ProductDevState | None
    total: int
    # Longest-standing first, at most ten; `total` counts every case. A
    # stage-1 group's cases are `product_entries`, and `entries` is empty.
    entries: list[BriefEntryView]
    product_entries: list[ProductBriefEntryView]


class DailyBriefView(BaseModel):
    generated_at: datetime
    active_case_count: int
    flagged_case_count: int
    # False when the caller may not read the approval inbox: pending
    # approvals were not looked at, which is not the same as "none pending".
    approvals_visible: bool
    # The same for product-development cases (stage 1); the two counts are
    # 0 when they were not looked at.
    product_cases_visible: bool
    active_product_case_count: int
    flagged_product_case_count: int
    # In the tenant's own `signal_order`; a client renders them as given.
    groups: list[BriefGroupView]
    # How many cases a group lists at most. A group's `total` counts every
    # one; for pending approvals and recent changes only the newest this
    # many were read at all.
    entries_shown: int


def _product_brief_entry_view(entry: ProductBriefEntry) -> ProductBriefEntryView:
    sample_round = entry.sample_round
    return ProductBriefEntryView(
        case=_product_ref(entry.case),
        days=entry.days,
        limit_days=entry.limit_days,
        round_no=sample_round.round_no if sample_round is not None else None,
        sample_result=sample_round.result if sample_round is not None else None,
    )


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
        product_state=group.product_state,
        total=group.total,
        entries=[_brief_entry_view(entry) for entry in group.shown_entries],
        product_entries=[_product_brief_entry_view(entry) for entry in group.shown_product_entries],
    )


def _daily_brief_view(brief: DailyBrief) -> DailyBriefView:
    return DailyBriefView(
        generated_at=brief.generated_at,
        active_case_count=brief.active_case_count,
        flagged_case_count=brief.flagged_case_count,
        approvals_visible=brief.approvals_visible,
        product_cases_visible=brief.product_cases_visible,
        active_product_case_count=brief.active_product_case_count,
        flagged_product_case_count=brief.flagged_product_case_count,
        groups=[_brief_group_view(group) for group in brief.groups],
        entries_shown=ENTRIES_SHOWN,
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
    always a stored name, never the model's mention; a PO reference or a
    proposal code is the stored one once the case was found (the question's
    own spelling when it was not); a Category is a key of the tenant's list
    and a PIC a person of the workspace (or the asker, for "mine")."""

    state: CaseState | None
    supplier_name: str | None
    active_only: bool
    po_reference: str | None
    product_state: ProductDevState | None
    category: str | None
    pic_user_id: uuid.UUID | None
    proposal_code: str | None


class CaseTableDataView(BaseModel):
    type: Literal["case_table"]
    rows: list[POCaseView]
    # More matched than the answer carries — the client offers the full,
    # paged list for the same `understood` filter.
    has_more: bool


class CaseLinkDataView(BaseModel):
    type: Literal["case_link"]
    case: POCaseView


class ProductCaseTableDataView(BaseModel):
    type: Literal["product_case_table"]
    rows: list[ProductCaseRefView]
    # More matched than the answer carries — the client offers the product
    # case list for the same `understood` filter.
    has_more: bool


class ProductCaseLinkDataView(BaseModel):
    type: Literal["product_case_link"]
    case: ProductCaseRefView


_DataView = Annotated[
    CaseTableDataView | CaseLinkDataView | ProductCaseTableDataView | ProductCaseLinkDataView,
    Field(discriminator="type"),
]


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
    # Supplier names, PO references, Category labels, people's names or
    # proposal codes the question matched more than one of — shown to the
    # person, never picked from.
    candidates: list[str]
    data_view: _DataView | None


def _ai_work_response_view(answer: CaseQueryAnswer) -> AIWorkResponseView:
    plan = answer.plan
    data_view: (
        CaseTableDataView
        | CaseLinkDataView
        | ProductCaseTableDataView
        | ProductCaseLinkDataView
        | None
    ) = None
    if answer.opened is not None:
        data_view = CaseLinkDataView(type="case_link", case=_view(answer.opened))
    elif answer.opened_product is not None:
        data_view = ProductCaseLinkDataView(
            type="product_case_link", case=_product_ref(answer.opened_product)
        )
    elif plan.outcome in (CaseQueryOutcome.LIST, CaseQueryOutcome.PO_AMBIGUOUS):
        data_view = CaseTableDataView(
            type="case_table",
            rows=[_view(case) for case in answer.cases],
            has_more=answer.has_more,
        )
    elif plan.outcome in (CaseQueryOutcome.PRODUCT_LIST, CaseQueryOutcome.PRODUCT_AMBIGUOUS):
        data_view = ProductCaseTableDataView(
            type="product_case_table",
            rows=[_product_ref(case) for case in answer.product_cases],
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
            product_state=plan.product_state,
            category=plan.category,
            pic_user_id=plan.pic_user_id,
            proposal_code=plan.proposal_code,
        ),
        citations=[CitationView(field=field, quote=quote) for field, quote in answer.citations],
        ignored_fields=list(answer.ignored),
        unusable_fields=list(answer.unusable),
        candidates=list(plan.candidates),
        data_view=data_view,
    )


class FollowUpItemView(BaseModel):
    """An open follow-up. `mine`: it was handed to the caller (a stamped scope
    they hold, or they are its stamped PIC), so the caller is expected to act,
    and may close it. `case_kind` says which page `case_id` opens; `reference`
    is the PO reference (null while the case awaits its PO) or the product
    case's proposal code."""

    id: uuid.UUID
    case_kind: CaseKind
    case_id: uuid.UUID
    reference: str | None
    supplier_name: str | None
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
        case_kind=record.case_kind,
        case_id=record.case_id,
        reference=record.reference,
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
    create_po: CreatePO,
    reassign_pic: ReassignPOCasePic,
    list_case_approvals: ListPOCaseApprovals,
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
            context,
            po_reference=body.po_reference,
            supplier_name=body.supplier_name,
            order_kind=body.order_kind,
            category=body.category,
        )
        return await idempotency.record(_view(case))

    @router.post("/po-cases/{case_id}/pic", response_model=POCaseView)
    async def reassign_po_case_pic(
        case_id: uuid.UUID,
        body: ReassignPicRequest,
        context: require_access_context,
        idempotency: require_idempotency,
    ) -> POCaseView:
        """Hands the case to another PIC (TP Cung ứng's duty, ticket 06)."""
        case = await reassign_pic.handle(
            context, po_case_id=POCaseId(case_id), new_pic=body.pic_user_id, reason=body.reason
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

    @router.get("/po-cases/{case_id}", response_model=POCaseDetailView)
    async def get_po_case(case_id: uuid.UUID, context: require_access_context) -> POCaseDetailView:
        case = await get.handle(context, POCaseId(case_id))
        return _detail_view(case)

    @router.post("/po-cases/{case_id}/create-po", response_model=POCaseDetailView)
    async def create_po_route(
        case_id: uuid.UUID,
        body: CreatePORequest,
        context: require_access_context,
        idempotency: require_idempotency,
    ) -> POCaseDetailView:
        """Step 10 on a case awaiting its PO. A reference already taken in
        the tenant is a 409 naming it; a line still without a quantity, 409."""
        case = await create_po.handle(
            context,
            po_case_id=POCaseId(case_id),
            po_reference=body.po_reference,
            order_kind=body.order_kind,
            quantities={line.sku_id: line.quantity for line in body.lines},
        )
        return await idempotency.record(_detail_view(case))

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
        response_model=Page[CaseTransitionView],
    )
    async def get_case_transitions(
        case_id: uuid.UUID,
        context: require_access_context,
        limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
        cursor: str | None = Query(default=None, description="Opaque cursor from a previous page."),
    ) -> Page[CaseTransitionView]:
        """The case's timeline, newest first, a page at a time."""
        page = await list_case_transitions.handle(
            context, POCaseId(case_id), limit=limit, cursor=cursor
        )
        return page.map_items(_case_transition_view)

    @router.get("/po-cases/{case_id}/approvals", response_model=CaseApprovalsView)
    async def get_case_approvals(
        case_id: uuid.UUID, context: require_access_context
    ) -> CaseApprovalsView:
        """The pending approvals that name this case, filtered here rather
        than by the client: the newest few, and how many there are."""
        found = await list_case_approvals.handle(context, POCaseId(case_id))
        return CaseApprovalsView(
            visible=found.visible,
            total=found.total,
            items=[
                CaseApprovalView(
                    id=approval.id,
                    action=approval.approval_type.removeprefix(APPROVAL_TYPE_PREFIX),
                    requested_at=approval.created_at,
                )
                for approval in found.newest
            ],
        )

    @router.get("/po-cases/{case_id}/sla-evaluation", response_model=SLAEvaluationView)
    async def get_sla_evaluation_route(
        case_id: uuid.UUID, context: require_access_context
    ) -> SLAEvaluationView:
        evaluation = await get_sla_evaluation.handle(context, POCaseId(case_id))
        return sla_evaluation_view(evaluation)

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
