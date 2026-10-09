"""Preparing a step and raising its proposal (ADR 0025; ticket ai-automation/05).

Two parts:

- **`PrepareStep`**, what the preparation graph's first node calls: reads the
  case, its source documents and their readings (the extraction lane's cited
  fields), fills the step's drafts by code (`DRAFT_RECIPES`), runs its checks
  (`STEP_CHECKS`), and returns the approval's payload, or why it could not:
  the case left the step, the step's paper is not on the case, a source is not
  read yet. Every outcome is a row of `step_preparations`, so the case page says
  what happened. Nothing here changes the case, confirms a document or sends
  anything: the only writes are drafts (which satisfy no step) and that row.
- **`PrepareSteps`**, the worker lane `supply_chain_step_preparation`: for each
  workspace whose tenant's policy lists steps, each case in a listed state is
  prepared once per (history row, policy version), on the thread
  `uuid5(case, history row, policy version)`, which `uq_worker_runs_active_thread`
  lets one unfinished run hold. A pending proposal whose subject changed is
  superseded by its requester, the lane, through the platform
  (`supersede_stale`) and prepared again; one still current has its deciders
  told (once each). `not_prepared` is tried again after `retry_after`;
  `rejected` and `applied` never. Requested by the lane itself
  (`system:supply_chain_step_preparation`, ADR 0011): no person's authority
  and no scopes; who may decide is the duty scope of the step, resolved from
  the tenant's duty policy here, when the run starts, and stamped.

A tenant whose policy lists nothing (the platform default) costs one policy
read per workspace per tick and starts nothing.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.doc_templates import DocTemplateSpec, TemplateFieldKind
from dw_agent_runtime.model.budget import BudgetExceededError
from dw_agent_runtime.ports import ModelGateway, ModelOutputInvalidError
from dw_kernel.errors import ConflictError, InfrastructureError, QuotaExceededError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.pagination import MAX_PAGE_SIZE, Page, PageQuery, PageRequest, page_request
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import PolicyOverridePort
from dw_platform.domain.approval import (
    APPROVALS_DECIDE,
    REQUIRED_INPUT_KEY,
    SUBJECT_VERSION_KEY,
    approval_link,
)
from dw_platform.domain.audit import AuditEvent, lane_audit_event, system_actor
from dw_supply_chain.application.bm04_prefill import (
    BM04_PROMPT,
    Bm04Facts,
    Bm04Preparation,
    Bm04Reading,
    approved_evaluation,
)
from dw_supply_chain.application.document_drafts import (
    DraftTemplatesPort,
    FieldInput,
    PrepareDocumentDraft,
)
from dw_supply_chain.application.handlers import (
    duty_scope,
    product_case_link,
    resolve_product_action_duties,
)
from dw_supply_chain.application.ports import (
    PendingApprovalRecord,
    ProductCaseListFilter,
    RaisedApprovalsPort,
    ReviewNotifierPort,
    ReviewRunStarterPort,
    ScopeHoldersPort,
    TenantPlanPort,
)
from dw_supply_chain.domain.bm04_prefill import conflict_message
from dw_supply_chain.domain.case_document import CaseDocument, CaseKind, DocumentType
from dw_supply_chain.domain.document_draft import (
    DRAFT_TEMPLATES,
    DocumentDraft,
    DraftSource,
    DraftStatus,
    field_value,
)
from dw_supply_chain.domain.extraction import EXTRACTION_SPECS, ExtractionStatus
from dw_supply_chain.domain.grounded_writing import EvidenceItem
from dw_supply_chain.domain.product_development_case import (
    ACTION_DOCUMENT_TYPE,
    ProductCaseTransition,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
)
from dw_supply_chain.domain.sample_criteria import Verdict
from dw_supply_chain.domain.sample_evaluation import (
    ITEM_WORDS,
    CriterionResult,
    EvaluationWriting,
    GroundedEvaluation,
    ItemStatus,
    Measurement,
    PreviousItem,
    check_previous,
    criteria_rows,
    evidence,
    ground_evaluation,
    judge,
    measurements_digest,
    notes_text,
    revision_rows,
    suggested_outcome,
)
from dw_supply_chain.domain.step_proposal import (
    PREPARE_AGAIN_AFTER,
    PROPOSAL_CASE_KEY,
    DraftRecipe,
    Finding,
    NotPreparedReason,
    PreparationOutcome,
    StepCheck,
    SubjectDraft,
    SubjectSource,
    newest_by_type,
    proposal_subject_version,
    proposal_type,
)
from dw_supply_chain.domain.supplier_terms import (
    TermRow,
    TermStatus,
    bm04_terms,
    compare_terms,
    prices_of,
    term_message,
)
from dw_supply_chain.product_action_duties import SupplyChainProductActionDuties
from dw_supply_chain.sample_criteria_policy import (
    SupplyChainSampleCriteria,
    resolve_sample_criteria,
)
from dw_supply_chain.step_preparation_policy import (
    STEP_PREPARATION_POLICY_ID,
    PreparedStep,
    SupplyChainStepPreparation,
)
from dw_supply_chain.workflows.grounded_writing import WritingPrompt, write_with_evidence

logger = logging.getLogger(__name__)

PREPARATION_LANE = "supply_chain_step_preparation"
PREPARATION_RECORDED = "supply_chain.step_preparation.recorded"
RECONCILE_BATCH = 20
RETRY_AFTER = timedelta(minutes=5)
_THREAD_NAMESPACE = uuid.UUID("4f7d2c1a-0b5e-4e8a-9c3d-6a1b2c3d4e05")


def lane_actor() -> uuid.UUID:
    return system_actor(PREPARATION_LANE).value


def lane_context(tenant_id: uuid.UUID, workspace_id: uuid.UUID) -> AccessContext:
    """The lane in one workspace: its own actor, no role, no scope."""
    return AccessContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        principal_id=lane_actor(),
        roles=frozenset(),
        scopes=frozenset(),
        plan_id="system",
    )


def preparation_thread_id(
    case_id: uuid.UUID, transition_id: uuid.UUID, policy_version: str
) -> uuid.UUID:
    """One thread per step entry and policy version: every attempt meets at
    `uq_worker_runs_active_thread`."""
    return uuid.uuid5(_THREAD_NAMESPACE, f"{case_id}:{transition_id}:{policy_version}")


async def resolve_step_preparation(
    context: AccessContext,
    policy_override_repo: PolicyOverridePort,
    platform_default: SupplyChainStepPreparation,
) -> SupplyChainStepPreparation:
    """The tenant's own policy if it set one (re-validated whole), the
    platform's (nothing prepared) otherwise."""
    override = await policy_override_repo.get(context, STEP_PREPARATION_POLICY_ID)
    if override is None:
        return platform_default
    return SupplyChainStepPreparation.model_validate(override)


# ------------------------------------------------------------------ ports --


class CaseReadPort(Protocol):
    """One product case under the caller's tenant and workspace."""

    async def get(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> ProductDevelopmentCase | None: ...


class CaseHistoryPort(CaseReadPort, Protocol):
    """A case and its history, newest first."""

    async def list_transitions(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId, request: PageRequest
    ) -> Page[ProductCaseTransition]: ...


class CaseListingPort(CaseHistoryPort, Protocol):
    """The workspace's cases in one state, and each one's history."""

    async def list_page(
        self, context: AccessContext, request: PageRequest, case_filter: ProductCaseListFilter
    ) -> Page[ProductDevelopmentCase]: ...


@dataclass(frozen=True, slots=True)
class StoredReading:
    """One `document_extractions` row, as preparation reads it."""

    id: uuid.UUID
    document_id: uuid.UUID
    sha256: str
    prompt_id: str
    prompt_version: str
    status: ExtractionStatus
    fields: Mapping[str, Any]
    gaps: Sequence[Mapping[str, str]]


class ExtractionReadingsPort(Protocol):
    async def readings(
        self, context: AccessContext, document_ids: Sequence[uuid.UUID]
    ) -> list[StoredReading]: ...


class CaseDocumentListPort(Protocol):
    async def list_for_case(
        self, context: AccessContext, case_kind: CaseKind, case_id: uuid.UUID
    ) -> list[CaseDocument]: ...


class CaseDraftsPort(Protocol):
    async def get(self, context: AccessContext, draft_id: uuid.UUID) -> DocumentDraft | None: ...

    async def latest_for_case(
        self, context: AccessContext, case_kind: CaseKind, case_id: uuid.UUID
    ) -> list[DocumentDraft]: ...


class MeasurementsPort(Protocol):
    """What R&D measured on a case's round, under the caller's tenant and
    workspace (RLS)."""

    async def for_round(
        self, context: AccessContext, case_id: uuid.UUID, sample_round: int
    ) -> list[Measurement]: ...


class EvaluationWriterPort(Protocol):
    """The model's words for a sample round's record and request, or None
    when there are none to be had (no plan, a spent day, a failed call)."""

    async def write(
        self, context: AccessContext, *, case_id: uuid.UUID, evidence: Sequence[EvidenceItem]
    ) -> EvaluationWriting | None: ...


EVALUATION_PROMPT = WritingPrompt("supply_chain.draft_sample_evaluation", "1.0.0")


@dataclass(frozen=True)
class EvaluationWriter:
    """Implements `EvaluationWriterPort`: one structured call through the
    process's one-call gateway, as the preparation lane itself. A refused or
    failed call is no writing: the drafts carry code's rows and gaps."""

    gateway: ModelGateway
    plans: TenantPlanPort
    ids: IdGenerator
    worker_id: str
    worker_version: str
    model_profile: str | None = None

    async def write(
        self, context: AccessContext, *, case_id: uuid.UUID, evidence: Sequence[EvidenceItem]
    ) -> EvaluationWriting | None:
        plan = await self.plans.plan_of(context.tenant_id)
        if plan is None:
            return None
        run_id = self.ids.new_uuid()
        try:
            return await write_with_evidence(
                self.gateway,
                RunContext(
                    run_id=run_id,
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    actor_id=context.principal_id,
                    worker_id=self.worker_id,
                    worker_version=self.worker_version,
                    channel="worker",
                    plan_id=plan,
                    roles=frozenset(),
                    scopes=frozenset(),
                    trace_id=str(run_id),
                    subject_ref=f"product_dev_case:{case_id}",
                ),
                EVALUATION_PROMPT,
                EvaluationWriting,
                task={"purpose": "sample_evaluation"},
                evidence=evidence,
                model_profile=self.model_profile,
            )
        except (
            QuotaExceededError,
            ModelOutputInvalidError,
            InfrastructureError,
            BudgetExceededError,
        ) as exc:
            logger.warning("sample evaluation: no model writing (%s)", type(exc).__name__)
            return None


@dataclass(frozen=True)
class SamplePreparation:
    """What a sample round's recipes and checks read: the tenant's criteria
    for the case's Category, R&D's measurements of the round, and the model
    that words the record and the request."""

    measurements: MeasurementsPort
    policy_override_repo: PolicyOverridePort
    platform_default_criteria: SupplyChainSampleCriteria
    writer: EvaluationWriterPort | None = None

    async def results(
        self, context: AccessContext, case: ProductDevelopmentCase
    ) -> list[CriterionResult]:
        criteria = await resolve_sample_criteria(
            context, self.policy_override_repo, self.platform_default_criteria
        )
        return judge(
            criteria.for_category(case.category),
            await self.measurements.for_round(context, case.id.value, case.sample_round),
            case.sample_round,
        )


@dataclass(frozen=True, slots=True)
class PreparationRecord:
    id: uuid.UUID
    case_id: uuid.UUID
    transition_id: uuid.UUID
    policy_version: str
    action: str
    outcome: PreparationOutcome
    reason: str | None
    run_id: uuid.UUID | None
    draft_lineages: tuple[uuid.UUID, ...]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NewPreparationRecord:
    id: uuid.UUID
    case_id: uuid.UUID
    transition_id: uuid.UUID
    policy_version: str
    action: str
    outcome: PreparationOutcome
    reason: str | None = None
    run_id: uuid.UUID | None = None
    draft_lineages: tuple[uuid.UUID, ...] = ()


class PreparationRecordsPort(Protocol):
    async def latest(
        self, context: AccessContext, transition_id: uuid.UUID, policy_version: str
    ) -> PreparationRecord | None: ...

    async def add(
        self, context: AccessContext, record: NewPreparationRecord, *, audit: AuditEvent
    ) -> None: ...


def record_audit(
    context: AccessContext, ids: IdGenerator, clock: UtcClock, record: NewPreparationRecord
) -> AuditEvent:
    """A row of `step_preparations`, audited by whoever wrote it: the lane as
    itself (ADR 0011), a person deciding as themselves."""
    details: dict[str, object] = {
        "record_id": str(record.id),
        "transition_id": str(record.transition_id),
        "policy_version": record.policy_version,
        "action": record.action,
        "outcome": record.outcome.value,
        "reason": record.reason,
    }
    if context.principal_id == lane_actor():
        return lane_audit_event(
            lane=PREPARATION_LANE,
            event_id=ids.new_uuid(),
            tenant_id=TenantId(context.tenant_id),
            workspace_id=WorkspaceId(context.workspace_id),
            action=PREPARATION_RECORDED,
            resource_type="product_dev_case",
            resource_id=str(record.case_id),
            occurred_at=clock.now(),
            details=details,
        )
    return AuditEvent(
        id=ids.new_uuid(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        actor_id=UserId(context.principal_id),
        action=PREPARATION_RECORDED,
        resource_type="product_dev_case",
        resource_id=str(record.case_id),
        occurred_at=clock.now(),
        details=details,
    )


# --------------------------------------------------------------- recipes --

# A reading's field that suggests a result field of another name, and the
# words its choices print as. Shown beside the empty field, never put in it.
_SUGGESTION_ALIASES: Mapping[str, tuple[str, ...]] = {"conclusion": ("result",)}
_CHOICE_WORDS: Mapping[str, str] = {
    "pass": "Đạt",
    "fail": "Không đạt",
    "revise": "Cần chỉnh sửa",
    "yes": "Có",
    "no": "Không",
    "partly": "Một phần",
}


@dataclass(frozen=True, slots=True)
class SourceReading:
    """A source document's current reading: its document and kept fields."""

    document_id: uuid.UUID
    doc_type: DocumentType
    reading: StoredReading


def _case_facts(case: ProductDevelopmentCase) -> dict[str, str | None]:
    return {
        "proposal_code": case.proposal_code,
        "product_name": case.product_name,
        "category": case.category,
        "supplier_name": case.supplier_name,
        "sample_round": str(case.sample_round) if case.sample_round else None,
    }


@dataclass(frozen=True, slots=True)
class SampleFacts:
    """A sample round as the recipes and checks read it: code's results,
    the last request's items checked, and the model's words that checked out
    (None: no model writing was asked for, or none was had)."""

    results: tuple[CriterionResult, ...]
    previous: tuple[PreviousItem, ...]
    previous_structured: bool
    grounded: GroundedEvaluation | None
    today: date


@dataclass(frozen=True, slots=True)
class RecipeInput:
    case: ProductDevelopmentCase
    spec: DocTemplateSpec
    readings: Sequence[SourceReading]
    # The result fields a person types: never filled.
    skip: frozenset[str]
    sample: SampleFacts | None = None
    bm04: Bm04Facts | None = None


def case_facts_recipe(given: RecipeInput) -> dict[str, FieldInput]:
    """The case's own fields, then the cited readings' fields of the same name
    (with their document and quote); never a field in `skip` (the result a
    person types), never a table (a model's rows come with a drafting prompt)."""
    case, spec, readings, skip = given.case, given.spec, given.readings, given.skip
    facts = _case_facts(case)
    values: dict[str, FieldInput] = {}
    for f in spec.fields:
        if f.name in skip or f.kind is TemplateFieldKind.TABLE:
            continue
        fact = facts.get(f.name)
        if fact:
            values[f.name] = FieldInput(value=fact)
            continue
        for source in readings:
            entry = source.reading.fields.get(f.name)
            if isinstance(entry, Mapping) and isinstance(entry.get("value"), str):
                quote = entry.get("quote")
                values[f.name] = FieldInput(
                    value=entry["value"],
                    document_id=source.document_id,
                    quote=quote if isinstance(quote, str) else None,
                )
                break
    return values


def _sample(given: RecipeInput) -> SampleFacts:
    if given.sample is None:
        raise _NotPreparedError(NotPreparedReason.SAMPLE_FACTS_UNAVAILABLE)
    return given.sample


def sample_evaluation_recipe(given: RecipeInput) -> dict[str, FieldInput]:
    """The record: the case's facts, the round's criteria table (code's rows
    from R&D's measurements) and the notes the model wrote that checked out,
    each with what it cites. The conclusion and the date stay a person's."""
    sample = _sample(given)
    values = case_facts_recipe(given)
    values["criteria"] = FieldInput(value=criteria_rows(sample.results))
    notes = () if sample.grounded is None else sample.grounded.notes
    text = notes_text(notes)
    if text is not None and "notes" not in given.skip:
        values["notes"] = FieldInput(
            value=text, cites=tuple(dict.fromkeys(c for n in notes for c in n.cites))
        )
    return values


def revision_request_recipe(given: RecipeInput) -> dict[str, FieldInput]:
    """The request: one item per failed criterion (code's finding), its
    requirement only when the model wrote one that cites it and checks out."""
    sample = _sample(given)
    values = case_facts_recipe(given)
    requirements = {} if sample.grounded is None else sample.grounded.requirements
    values["items"] = FieldInput(
        value=revision_rows(sample.results, requirements),
        cites=tuple(dict.fromkeys(c for r in requirements.values() for c in r.cites)),
    )
    values["requested_on"] = FieldInput(value=sample.today.isoformat())
    return values


def bm04_recipe(given: RecipeInput) -> dict[str, FieldInput]:
    """The BM04: each value code kept or a model read and code checked, with
    its source; a conflicting field and a field nobody proves stay empty."""
    if given.bm04 is None:
        raise _NotPreparedError(NotPreparedReason.BM04_FACTS_UNAVAILABLE)
    names = {f.name for f in given.spec.fields if f.kind is not TemplateFieldKind.TABLE}
    values: dict[str, FieldInput] = {}
    for name, kept in given.bm04.reconciled.kept.items():
        if name not in names or name in given.skip:
            continue
        values[name] = FieldInput(
            value=kept.value,
            document_id=kept.source.document_id,
            quote=kept.quote,
            cites=(kept.source.key,) if kept.ai_written else (),
        )
    return values


Recipe = Callable[[RecipeInput], dict[str, FieldInput]]
DRAFT_RECIPES: Mapping[DraftRecipe, Recipe] = {
    DraftRecipe.CASE_FACTS: case_facts_recipe,
    DraftRecipe.SAMPLE_EVALUATION: sample_evaluation_recipe,
    DraftRecipe.REVISION_REQUEST: revision_request_recipe,
    DraftRecipe.BM04: bm04_recipe,
}
# The recipes and checks that read a sample round (`SampleFacts`).
SAMPLE_RECIPES = frozenset({DraftRecipe.SAMPLE_EVALUATION, DraftRecipe.REVISION_REQUEST})
SAMPLE_CHECKS = frozenset({StepCheck.CRITERIA_MEASURED, StepCheck.REVISION_CHECKED})


def fills_a_bm04(step: PreparedStep) -> bool:
    return any(d.recipe is DraftRecipe.BM04 for d in step.drafts) or (
        StepCheck.BM04_SOURCES in step.checks
    )


def compares_terms(step: PreparedStep) -> bool:
    return StepCheck.TERMS_MATCH_BM04 in step.checks


@dataclass(frozen=True, slots=True)
class TermsFacts:
    """The supplier's reply against the BM04 (ticket ai-automation/12): the
    BM04 version compared (None: the case has none) and each term's row."""

    profile_id: str | None
    rows: tuple[TermRow, ...]


def bound_facts(sample_digest: str, terms: TermsFacts | None) -> str:
    """What else a proposal's drafts and findings were made from, bound into
    its subject: a round's measurements, the BM04 version compared."""
    if terms is None:
        return sample_digest
    return f"{sample_digest}|bm04:{terms.profile_id or ''}"


def reads_a_round(step: PreparedStep) -> bool:
    return bool({d.recipe for d in step.drafts} & SAMPLE_RECIPES) or bool(
        set(step.checks) & SAMPLE_CHECKS
    )


assert set(DRAFT_RECIPES) == set(DraftRecipe)  # every recipe a policy may name is implemented


def suggestions_for(
    result_fields: Sequence[str],
    readings: Sequence[SourceReading],
    step: PreparedStep | None = None,
    sample: SampleFacts | None = None,
) -> dict[str, dict[str, str]]:
    """AI's suggestion for each result field a reading says something about:
    the value in words, the quote and the document. Shown beside the field.
    For a step whose outcome is chosen, code's comparison of the round's
    measurements suggests it first (a failed criterion: revise)."""
    out: dict[str, dict[str, str]] = {}
    if step is not None and sample is not None and step.outcome_field is not None:
        choice = suggested_outcome(sample.results)
        if choice is not None and choice in step.outcomes:
            failed = [r.criterion.label for r in sample.results if r.verdict is Verdict.FAIL]
            out[step.outcome_field] = {
                "value": step.outcomes[choice].label,
                "quote": "Tiêu chí không đạt: " + ", ".join(failed)
                if failed
                else "Mọi tiêu chí đạt theo số đo của R&D",
                "document_id": "",
            }
    for name in result_fields:
        if name in out:
            continue
        for source in readings:
            for key in (name, *_SUGGESTION_ALIASES.get(name, ())):
                entry = source.reading.fields.get(key)
                if not (isinstance(entry, Mapping) and isinstance(entry.get("value"), str)):
                    continue
                value = entry["value"]
                out[name] = {
                    "value": _CHOICE_WORDS.get(value, value),
                    "quote": str(entry.get("quote") or ""),
                    "document_id": str(source.document_id),
                }
                break
            if name in out:
                break
    return out


# ---------------------------------------------------------------- checks --


@dataclass(frozen=True, slots=True)
class CheckInput:
    step: PreparedStep
    newest: Mapping[DocumentType, uuid.UUID]
    readings: Sequence[SourceReading]
    drafts: Sequence[DocumentDraft]
    sample: SampleFacts | None = None
    bm04: Bm04Facts | None = None
    terms: TermsFacts | None = None


def _sources_present(given: CheckInput) -> list[Finding]:
    return [
        Finding("source_missing", t.value, "Chưa có chứng từ này trên hồ sơ")
        for t in given.step.sources
        if t not in given.newest
    ]


def _sources_read(given: CheckInput) -> list[Finding]:
    found: list[Finding] = []
    for source in given.readings:
        if source.reading.status is not ExtractionStatus.EXTRACTED:
            found.append(
                Finding(
                    "source_unreadable",
                    source.doc_type.value,
                    "Máy không đọc được chứng từ này; người kiểm cần mở file",
                )
            )
            continue
        missing = sorted({str(g.get("field", "")) for g in source.reading.gaps if g.get("field")})
        if missing:
            found.append(
                Finding(
                    "source_gaps",
                    source.doc_type.value,
                    "Máy không tìm thấy trong file: " + ", ".join(missing),
                )
            )
    return found


def _drafts_complete(given: CheckInput) -> list[Finding]:
    """Gaps of the drafts the step's own action takes. The paper of another
    outcome (the revision request of a round that may still pass) is shown,
    not reported: it is needed only if a person chooses that outcome."""
    result = set(given.step.result_fields)
    alternatives = {
        ACTION_DOCUMENT_TYPE[a]
        for a in given.step.outcome_actions
        if a is not given.step.action and a in ACTION_DOCUMENT_TYPE
    } - {given.step.action_document}
    found: list[Finding] = []
    for draft in given.drafts:
        if draft.doc_type in alternatives:
            continue
        gaps = [g for g in draft.gaps if g.split("[", 1)[0] not in result]
        if gaps:
            found.append(
                Finding(
                    "draft_gaps", draft.doc_type.value, "Bản nháp còn thiếu: " + ", ".join(gaps)
                )
            )
    return found


def _criteria_measured(given: CheckInput) -> list[Finding]:
    if given.sample is None:
        return []
    return [
        Finding("criterion_unmeasured", r.criterion.key, f"Chưa nhập số đo: {r.criterion.label}")
        for r in given.sample.results
        if r.verdict is Verdict.UNMEASURED
    ]


def _revision_checked(given: CheckInput) -> list[Finding]:
    if given.sample is None:
        return []
    found: list[Finding] = []
    if not given.sample.previous_structured:
        found.append(
            Finding(
                "previous_request_unstructured",
                "sample_revision_request",
                "Phiếu vòng trước là file tải lên; người kiểm cần đối chiếu từng mục",
            )
        )
    for item in given.sample.previous:
        if item.status is not ItemStatus.FIXED:
            found.append(
                Finding(
                    f"revision_item_{item.status.value}",
                    item.criterion,
                    f"Mục vòng trước {ITEM_WORDS[item.status]}: {item.criterion}",
                )
            )
    return found


def _bm04_sources(given: CheckInput) -> list[Finding]:
    """Each conflict with both its sources; a price line code could not
    choose; each field the tenant's BM04 schema requires that the BM04 draft
    leaves empty (a source said nothing, or two disagreed)."""
    facts = given.bm04
    if facts is None:
        return []
    found = [
        Finding(
            "bm04_conflict",
            c.field,
            conflict_message(c, facts.labels.get(c.field, c.field), facts.prices),
        )
        for c in facts.reconciled.conflicts
    ]
    if facts.lines_ambiguous:
        found.append(
            Finding(
                "quotation_lines_ambiguous",
                "unit_price",
                "Báo giá có nhiều dòng giá, không dòng nào ghi đúng tên sản phẩm; máy không chọn",
            )
        )
    bm04 = next((d for d in given.drafts if d.doc_type is DocumentType.PRODUCT_PROFILE_BM04), None)
    filled = {} if bm04 is None else bm04.fields
    for spec in facts.schema.fields:
        if spec.required and not field_value(filled, spec.key):
            found.append(
                Finding(
                    "bm04_required_missing",
                    spec.key,
                    f"Biểu mẫu BM04 của công ty bắt buộc ô này, chưa có nguồn: {spec.label}",
                )
            )
    return found


_TERM_CODES = {
    TermStatus.DIFFER: "terms_differ",
    TermStatus.NOT_STATED: "term_not_stated",
    TermStatus.NOT_IN_BM04: "term_not_in_bm04",
}


def _terms_match_bm04(given: CheckInput) -> list[Finding]:
    """Each term of the reply that is not the BM04's, in words that carry no
    price; no BM04 in the application is a finding of its own."""
    terms = given.terms
    if terms is None:
        return []
    if terms.profile_id is None:
        return [
            Finding(
                "bm04_missing",
                DocumentType.PRODUCT_PROFILE_BM04.value,
                "Hồ sơ chưa có BM04 trong ứng dụng để so; người kiểm đối chiếu thư với file BM04",
            )
        ]
    prices = prices_of(terms.rows)
    return [
        Finding(_TERM_CODES[row.status], row.field, term_message(row, prices))
        for row in terms.rows
        if row.status is not TermStatus.MATCH
    ]


STEP_CHECKS: Mapping[StepCheck, Callable[[CheckInput], list[Finding]]] = {
    StepCheck.SOURCES_PRESENT: _sources_present,
    StepCheck.SOURCES_READ: _sources_read,
    StepCheck.DRAFTS_COMPLETE: _drafts_complete,
    StepCheck.CRITERIA_MEASURED: _criteria_measured,
    StepCheck.REVISION_CHECKED: _revision_checked,
    StepCheck.BM04_SOURCES: _bm04_sources,
    StepCheck.TERMS_MATCH_BM04: _terms_match_bm04,
}
assert set(STEP_CHECKS) == set(StepCheck)  # every check a policy may name is implemented


# ------------------------------------------------------------ preparing --


@dataclass(frozen=True, slots=True)
class PreparationRequest:
    """What the run was started with (the graph's input)."""

    case_id: uuid.UUID
    transition_id: uuid.UUID
    policy_version: str
    step: PreparedStep
    required_scope: str
    run_id: uuid.UUID | None

    @classmethod
    def of(cls, state: Mapping[str, Any], run_id: uuid.UUID | None) -> PreparationRequest:
        return cls(
            case_id=uuid.UUID(str(state[PROPOSAL_CASE_KEY])),
            transition_id=uuid.UUID(str(state["transition_id"])),
            policy_version=str(state["policy_version"]),
            step=PreparedStep.model_validate(state["step"]),
            required_scope=str(state["required_scope"]),
            run_id=run_id,
        )


@dataclass(frozen=True, slots=True)
class Prepared:
    """What preparing came to: the approval's payload, or why there is none."""

    outcome: PreparationOutcome
    payload: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None


class _NotPreparedError(Exception):
    def __init__(self, reason: NotPreparedReason, subject: str = "") -> None:
        super().__init__(reason.value)
        self.reason = reason
        self.subject = subject

    @property
    def text(self) -> str:
        return f"{self.reason.value}:{self.subject}" if self.subject else self.reason.value


@dataclass(frozen=True)
class PrepareStep:
    """Implements the preparation graph's `StepPreparerPort`."""

    cases: CaseReadPort
    documents: CaseDocumentListPort
    readings: ExtractionReadingsPort
    drafts: CaseDraftsPort
    prepare_draft: PrepareDocumentDraft
    templates: DraftTemplatesPort
    records: PreparationRecordsPort
    ids: IdGenerator
    clock: UtcClock
    # A sample round's criteria, measurements and model (ticket
    # ai-automation/09); a host that prepares no such step needs none.
    sample: SamplePreparation | None = None
    # The BM04's schema and model (ticket ai-automation/11), likewise.
    bm04: Bm04Preparation | None = None

    async def prepare(self, context: AccessContext, request: PreparationRequest) -> Prepared:
        previous = await self.records.latest(context, request.transition_id, request.policy_version)
        try:
            payload, lineages = await self._prepare(context, request, previous)
        except _NotPreparedError as refusal:
            await record_once(
                self.records,
                context,
                self.ids,
                self.clock,
                previous,
                NewPreparationRecord(
                    id=self.ids.new_uuid(),
                    case_id=request.case_id,
                    transition_id=request.transition_id,
                    policy_version=request.policy_version,
                    action=request.step.action.value,
                    outcome=PreparationOutcome.NOT_PREPARED,
                    reason=refusal.text,
                    run_id=request.run_id,
                ),
            )
            return Prepared(PreparationOutcome.NOT_PREPARED, reason=refusal.text)
        record = NewPreparationRecord(
            id=self.ids.new_uuid(),
            case_id=request.case_id,
            transition_id=request.transition_id,
            policy_version=request.policy_version,
            action=request.step.action.value,
            outcome=PreparationOutcome.PROPOSED,
            run_id=request.run_id,
            draft_lineages=lineages,
        )
        await self.records.add(
            context, record, audit=record_audit(context, self.ids, self.clock, record)
        )
        return Prepared(PreparationOutcome.PROPOSED, payload=payload)

    async def _prepare(
        self,
        context: AccessContext,
        request: PreparationRequest,
        previous: PreparationRecord | None,
    ) -> tuple[dict[str, Any], tuple[uuid.UUID, ...]]:
        step = request.step
        case = await self.cases.get(context, ProductDevelopmentCaseId(request.case_id))
        if case is None or case.state is not step.state:
            raise _NotPreparedError(NotPreparedReason.CASE_MOVED)
        documents = await self.documents.list_for_case(context, CaseKind.PRODUCT, case.id.value)
        by_id = {d.id.value: d for d in documents}
        newest = step_documents(case, step, documents)

        paper_from_source = self._paper_from_source(case, step, newest, by_id)
        readings = await self._readings(context, step, newest, by_id)
        sample = await self._sample_facts(context, case, step) if reads_a_round(step) else None
        bm04 = await self._bm04_facts(context, case, readings) if fills_a_bm04(step) else None
        terms = await self._terms(context, case, readings) if compares_terms(step) else None
        drafts = await self._drafts(context, case, step, readings, previous, sample, bm04)
        suggestions = (
            suggestions_for(step.result_fields, readings, step, sample) if step.physical else {}
        )

        given = CheckInput(
            step=step,
            newest=newest,
            readings=readings,
            drafts=drafts,
            sample=sample,
            bm04=bm04,
            terms=terms,
        )
        findings = [finding for check in step.checks for finding in STEP_CHECKS[check](given)]
        sources = [SubjectSource(t, newest.get(t)) for t in step.sources]
        target = (case.state, step.action)
        subject_version = proposal_subject_version(
            case.version,
            [SubjectDraft(d.id, d.content_sha256, open_latest=True) for d in drafts],
            sources,
            bound_facts("" if sample is None else measurements_digest(sample.results), terms),
        )
        payload: dict[str, Any] = {
            PROPOSAL_CASE_KEY: str(case.id),
            "proposal_code": case.proposal_code,
            "product_name": case.product_name,
            "case_version": case.version,
            "transition_id": str(request.transition_id),
            "policy_version": request.policy_version,
            "step": step.model_dump(mode="json"),
            "from_state": target[0].value,
            "action": step.action.value,
            "drafts": [
                {
                    "draft_id": str(d.id),
                    "lineage_id": str(d.lineage_id),
                    "version": d.version,
                    "doc_type": d.doc_type.value,
                    "content_sha256": d.content_sha256,
                    "gaps": list(d.gaps),
                }
                for d in drafts
            ],
            "sources": [
                {
                    "doc_type": s.doc_type.value,
                    "document_id": None if s.document_id is None else str(s.document_id),
                    "sha256": None if s.document_id is None else by_id[s.document_id].sha256,
                }
                for s in sources
            ],
            "action_document_id": None if paper_from_source is None else str(paper_from_source),
            "findings": [f.as_json() for f in findings],
            "physical": step.physical,
            "suggestions": suggestions,
            SUBJECT_VERSION_KEY: subject_version,
        }
        if terms is not None:
            # No price value in a row: the payload is read without the
            # commercial scope (`TermRow.as_json`).
            payload["comparison"] = {
                "profile_id": terms.profile_id,
                "rows": [row.as_json(prices_of(terms.rows)) for row in terms.rows],
            }
        if step.physical:
            payload[REQUIRED_INPUT_KEY] = list(step.result_fields)
        return payload, tuple(d.lineage_id for d in drafts)

    def _paper_from_source(
        self,
        case: ProductDevelopmentCase,
        step: PreparedStep,
        newest: Mapping[DocumentType, uuid.UUID],
        by_id: Mapping[uuid.UUID, CaseDocument],
    ) -> uuid.UUID | None:
        """The step's paper when a person uploaded it (not drafted): the newest
        of its type, and only if the step would take it (uploaded since the
        round opened or the case reached the step: the domain's own bound)."""
        paper = step.action_document
        if paper is None or paper in step.drafted_types:
            return None
        option = next((o for o in case.action_options() if o.action is step.action), None)
        document = by_id.get(newest[paper]) if paper in newest else None
        since = None if option is None else option.documents_since
        if document is None or since is None or document.uploaded_at < since:
            raise _NotPreparedError(NotPreparedReason.ACTION_DOCUMENT_MISSING, paper.value)
        return document.id.value

    async def _readings(
        self,
        context: AccessContext,
        step: PreparedStep,
        newest: Mapping[DocumentType, uuid.UUID],
        by_id: Mapping[uuid.UUID, CaseDocument],
    ) -> list[SourceReading]:
        """The current reading of each source the lane reads: of this file
        (its hash) under the prompt version the lane reads it with now. One not
        read yet stops the preparation until it is (the lane retries)."""
        wanted = [by_id[newest[t]] for t in step.sources if t in newest and t in EXTRACTION_SPECS]
        stored = await self.readings.readings(context, [d.id.value for d in wanted])
        found: list[SourceReading] = []
        for document in wanted:
            spec = EXTRACTION_SPECS[document.doc_type]
            reading = next(
                (
                    r
                    for r in stored
                    if r.document_id == document.id.value
                    and r.sha256 == document.sha256
                    and (r.prompt_id, r.prompt_version) == (spec.prompt_id, spec.prompt_version)
                ),
                None,
            )
            if reading is None:
                raise _NotPreparedError(
                    NotPreparedReason.SOURCE_NOT_READ_YET, document.doc_type.value
                )
            found.append(SourceReading(document.id.value, document.doc_type, reading))
        return found

    async def _bm04_facts(
        self,
        context: AccessContext,
        case: ProductDevelopmentCase,
        readings: Sequence[SourceReading],
    ) -> Bm04Facts:
        """Code's BM04 candidates reconciled; the model is asked only when a
        new draft needs its words (`_drafts`)."""
        if self.bm04 is None:
            raise _NotPreparedError(NotPreparedReason.BM04_FACTS_UNAVAILABLE)
        template = await self.templates.resolve(
            context, *DRAFT_TEMPLATES[DocumentType.PRODUCT_PROFILE_BM04]
        )
        return await self.bm04.facts(context, case, template.spec, _bm04_readings(readings))

    async def _terms(
        self,
        context: AccessContext,
        case: ProductDevelopmentCase,
        readings: Sequence[SourceReading],
    ) -> TermsFacts:
        """The newest read confirmation against the case's latest BM04
        version, both under the lane's tenant and workspace."""
        if self.bm04 is None or self.bm04.profiles is None:
            raise _NotPreparedError(NotPreparedReason.BM04_FACTS_UNAVAILABLE)
        return await terms_facts(self.bm04, context, case, readings)

    async def _sample_facts(
        self, context: AccessContext, case: ProductDevelopmentCase, step: PreparedStep
    ) -> SampleFacts:
        """The round's results by code, and the last request's items checked
        against them. No model writing yet: that is asked for only when a
        draft needs it."""
        if self.sample is None:
            raise _NotPreparedError(NotPreparedReason.SAMPLE_FACTS_UNAVAILABLE)
        results = tuple(await self.sample.results(context, case))
        last = await self._last_request(context, case)
        previous = () if last is None else tuple(check_previous(last, results))
        return SampleFacts(
            results=results,
            previous=previous,
            previous_structured=case.sample_round <= 1 or last is not None,
            grounded=None,
            today=self.clock.now().date(),
        )

    async def _last_request(
        self, context: AccessContext, case: ProductDevelopmentCase
    ) -> list[Mapping[str, Any]] | None:
        """The items of the newest revision request a person approved (an
        AI-prepared one; an uploaded file has no items to check)."""
        confirmed = [
            d
            for d in await self.drafts.latest_for_case(context, CaseKind.PRODUCT, case.id.value)
            if d.doc_type is DocumentType.SAMPLE_REVISION_REQUEST
            and d.status is DraftStatus.CONFIRMED
        ]
        if not confirmed:
            return None
        newest = max(confirmed, key=lambda d: d.created_at)
        items = field_value(newest.fields, "items")
        return [i for i in items if isinstance(i, Mapping)] if isinstance(items, list) else []

    async def _written(
        self,
        context: AccessContext,
        case: ProductDevelopmentCase,
        sample: SampleFacts,
        readings: Sequence[SourceReading],
    ) -> SampleFacts:
        """The sample facts with the model's words that checked out, or with
        none when no writer is wired or the model gave nothing."""
        if self.sample is None or self.sample.writer is None:
            return sample
        items = evidence(
            "; ".join(f"{k}: {v}" for k, v in _case_facts(case).items() if v),
            sample.results,
            sample.previous,
            [
                (r.document_id, r.reading.fields)
                for r in readings
                if r.doc_type is DocumentType.SAMPLE_EVALUATION
            ],
        )
        writing = await self.sample.writer.write(context, case_id=case.id.value, evidence=items)
        if writing is None:
            return sample
        return replace(sample, grounded=ground_evaluation(writing, items, sample.results))

    async def _drafts(
        self,
        context: AccessContext,
        case: ProductDevelopmentCase,
        step: PreparedStep,
        readings: Sequence[SourceReading],
        previous: PreparationRecord | None,
        sample: SampleFacts | None = None,
        bm04: Bm04Facts | None = None,
    ) -> list[DocumentDraft]:
        """Each draft the step names: the open latest version of the lineage
        an earlier attempt of THIS step entry prepared (a person's edits are
        kept), else a new one filled by its recipe. The model is asked once,
        and only when a new draft needs its words."""
        reusable: dict[DocumentType, DocumentDraft] = {}
        if previous is not None and previous.draft_lineages:
            lineages = set(previous.draft_lineages)
            for draft in await self.drafts.latest_for_case(
                context, CaseKind.PRODUCT, case.id.value
            ):
                if draft.lineage_id in lineages and draft.status is DraftStatus.OPEN:
                    reusable[draft.doc_type] = draft
        result = frozenset(step.result_fields)
        if sample is not None and any(
            p.recipe in SAMPLE_RECIPES and p.doc_type not in reusable for p in step.drafts
        ):
            sample = await self._written(context, case, sample, readings)
        drafts: list[DocumentDraft] = []
        for planned in step.drafts:
            template = await self.templates.resolve(context, *DRAFT_TEMPLATES[planned.doc_type])
            if planned.doc_type == step.action_document:
                unknown = sorted(n for n in result if template.spec.field(n) is None)
                if unknown:
                    raise _NotPreparedError(
                        NotPreparedReason.RESULT_FIELD_UNKNOWN, ",".join(unknown)
                    )
            if planned.doc_type in reusable:
                drafts.append(reusable[planned.doc_type])
                continue
            if planned.recipe is DraftRecipe.BM04 and bm04 is not None and self.bm04 is not None:
                bm04 = await self.bm04.written(
                    context,
                    case,
                    template.spec,
                    _bm04_readings(readings),
                    approved_evaluation(
                        await self.drafts.latest_for_case(context, CaseKind.PRODUCT, case.id.value)
                    ),
                    bm04,
                )
            values = DRAFT_RECIPES[planned.recipe](
                RecipeInput(case, template.spec, readings, result, sample, bm04)
            )
            drafts.append(
                await self.prepare_draft.handle(
                    context,
                    case_kind=CaseKind.PRODUCT,
                    case_id=case.id.value,
                    doc_type=planned.doc_type,
                    values=values,
                    sources=[_draft_source(r) for r in readings],
                    prompt=_prompt_of(planned.recipe, sample, bm04),
                )
            )
        return drafts


async def terms_facts(
    bm04: Bm04Preparation,
    context: AccessContext,
    case: ProductDevelopmentCase,
    readings: Sequence[SourceReading],
) -> TermsFacts:
    """The comparison a proposal and its subject both read."""
    profile = None if bm04.profiles is None else await bm04.profiles.latest(context, case.id.value)
    if profile is None:
        return TermsFacts(profile_id=None, rows=())
    reply = next(
        (
            r.reading.fields
            for r in readings
            if r.doc_type is DocumentType.SUPPLIER_CONFIRMATION_EMAIL
            and r.reading.status is ExtractionStatus.EXTRACTED
        ),
        {},
    )
    return TermsFacts(str(profile.id), tuple(compare_terms(reply, bm04_terms(profile))))


def _prompt_of(
    recipe: DraftRecipe, sample: SampleFacts | None, bm04: Bm04Facts | None
) -> tuple[str, str] | None:
    """The prompt whose words a new draft holds, if any did."""
    if recipe in SAMPLE_RECIPES and sample is not None and sample.grounded is not None:
        return EVALUATION_PROMPT.ref
    if recipe is DraftRecipe.BM04 and bm04 is not None and bm04.written:
        return BM04_PROMPT.ref
    return None


def _bm04_readings(readings: Sequence[SourceReading]) -> list[Bm04Reading]:
    return [
        Bm04Reading(r.document_id, r.doc_type, r.reading.fields)
        for r in readings
        if r.reading.status is ExtractionStatus.EXTRACTED
    ]


def _draft_source(source: SourceReading) -> DraftSource:
    return DraftSource(
        document_id=source.document_id,
        sha256=source.reading.sha256,
        extraction_id=source.reading.id,
    )


def step_documents(
    case: ProductDevelopmentCase, step: PreparedStep, documents: Sequence[CaseDocument]
) -> Mapping[DocumentType, uuid.UUID]:
    """The newest document of each type, as a step reads its sources. A type
    the step both reads and drafts (a sample round's record) counts only when
    uploaded since the step's paper bound (the round opened): last round's
    record, approved into a document, is not this round's lab report. The
    preparation and the proposal's subject read sources through here, so the
    two never disagree about which document a proposal was made from."""
    option = next((o for o in case.action_options() if o.action is step.action), None)
    since = None if option is None else option.documents_since
    both = set(step.sources) & step.drafted_types
    return newest_by_type(
        (d.doc_type, d.id.value, d.version)
        for d in documents
        if d.doc_type not in both or (since is not None and d.uploaded_at >= since)
    )


async def record_once(
    records: PreparationRecordsPort,
    context: AccessContext,
    ids: IdGenerator,
    clock: UtcClock,
    previous: PreparationRecord | None,
    record: NewPreparationRecord,
) -> None:
    """Write `record` unless the entry's latest row already says the same:
    a step that cannot be prepared for the same reason tick after tick adds
    one row, not one per tick."""
    if (
        previous is not None
        and previous.outcome is record.outcome
        and previous.reason == record.reason
    ):
        return
    await records.add(context, record, audit=record_audit(context, ids, clock, record))


# ------------------------------------------------------------------ lane --


class WorkspacesWithCasesPort(Protocol):
    async def workspaces(self) -> list[tuple[uuid.UUID, uuid.UUID]]: ...


class ProposalSupersedePort(Protocol):
    """The platform's `ApproveAndResumeService.supersede_stale`."""

    async def supersede_stale(self, *, approval_id: uuid.UUID, context: AccessContext) -> bool: ...


@dataclass(slots=True)
class PreparationOutcomeCount:
    started: int = 0
    superseded: int = 0
    not_started: int = 0
    notified: int = 0
    failed_workspaces: int = 0


async def entered_current_state(
    cases: CaseHistoryPort, context: AccessContext, case: ProductDevelopmentCase
) -> ProductCaseTransition | None:
    """The history row that brought the case into its current state: the
    newest transition into it, read newest first a page at a time."""
    query = PageQuery(key="supply_chain.product_case_transitions", filters={"case": case.id})
    cursor: str | None = None
    while True:
        page = await cases.list_transitions(
            context, case.id, page_request(limit=MAX_PAGE_SIZE, cursor=cursor, query=query)
        )
        entered = next((t for t in page.items if t.to_state is case.state), None)
        if entered is not None or page.next_cursor is None:
            return entered
        cursor = page.next_cursor


@dataclass(frozen=True)
class PrepareSteps:
    """The worker lane `supply_chain_step_preparation` (module docstring)."""

    workspaces: WorkspacesWithCasesPort
    cases: CaseListingPort
    policy_override_repo: PolicyOverridePort
    platform_default_policy: SupplyChainStepPreparation
    platform_default_duties: SupplyChainProductActionDuties
    plans: TenantPlanPort
    runner: ReviewRunStarterPort
    approvals: RaisedApprovalsPort
    supersede: ProposalSupersedePort
    records: PreparationRecordsPort
    holders: ScopeHoldersPort
    notifier: ReviewNotifierPort
    ids: IdGenerator
    clock: UtcClock
    worker_id: str
    worker_version: str
    batch: int = RECONCILE_BATCH
    retry_after: timedelta = RETRY_AFTER

    async def run(self) -> PreparationOutcomeCount:
        count = PreparationOutcomeCount()
        ended: set[uuid.UUID] = set()
        for tenant_id, workspace_id in await self.workspaces.workspaces():
            if count.started + count.not_started >= self.batch:
                break
            if tenant_id in ended:
                continue
            try:
                if await self._workspace(lane_context(tenant_id, workspace_id), count):
                    ended.add(tenant_id)
            except Exception:
                logger.exception(
                    "step preparation failed for a workspace",
                    extra={"tenant_id": str(tenant_id), "workspace_id": str(workspace_id)},
                )
                count.failed_workspaces += 1
        return count

    async def _workspace(self, context: AccessContext, count: PreparationOutcomeCount) -> bool:
        """True when the tenant's turn is over (a run was refused)."""
        policy = await resolve_step_preparation(
            context, self.policy_override_repo, self.platform_default_policy
        )
        if not policy.steps:
            return False
        plan = await self.plans.plan_of(context.tenant_id)
        if plan is None:
            return False
        for step in policy.steps:
            case_filter = ProductCaseListFilter(state=step.state)
            cursor: str | None = None
            while True:
                page = await self.cases.list_page(
                    context,
                    page_request(
                        limit=MAX_PAGE_SIZE,
                        cursor=cursor,
                        query=case_filter.page_query(context.tenant_id, context.workspace_id),
                    ),
                    case_filter,
                )
                for case in page.items:
                    if count.started + count.not_started >= self.batch:
                        return False
                    if await self._case(context, policy, step, plan, case, count):
                        return True
                if page.next_cursor is None:
                    break
                cursor = page.next_cursor
        return False

    async def _case(
        self,
        context: AccessContext,
        policy: SupplyChainStepPreparation,
        step: PreparedStep,
        plan: str,
        case: ProductDevelopmentCase,
        count: PreparationOutcomeCount,
    ) -> bool:
        entered = await entered_current_state(self.cases, context, case)
        if entered is None or entered.id is None:
            return False
        previous = await self.records.latest(context, entered.id, policy.policy_version)
        pending = await self.approvals.raised_by_payload(
            context,
            approval_type=proposal_type(step.action),
            key=PROPOSAL_CASE_KEY,
            value=str(case.id),
        )
        if pending is not None:
            if not await self.supersede.supersede_stale(approval_id=pending.id, context=context):
                await self.notify(context, case, step, pending)
                return False
            count.superseded += 1
            superseded = NewPreparationRecord(
                id=self.ids.new_uuid(),
                case_id=case.id.value,
                transition_id=entered.id,
                policy_version=policy.policy_version,
                action=step.action.value,
                outcome=PreparationOutcome.SUPERSEDED,
                reason="subject_changed",
            )
            await self.records.add(
                context, superseded, audit=record_audit(context, self.ids, self.clock, superseded)
            )
        elif previous is not None:
            if previous.outcome not in PREPARE_AGAIN_AFTER:
                return False
            if (
                previous.outcome is PreparationOutcome.NOT_PREPARED
                and self.clock.now() - previous.created_at < self.retry_after
            ):
                return False
        return await self._start(context, policy, step, plan, case, entered.id, previous, count)

    async def _start(
        self,
        context: AccessContext,
        policy: SupplyChainStepPreparation,
        step: PreparedStep,
        plan: str,
        case: ProductDevelopmentCase,
        transition_id: uuid.UUID,
        previous: PreparationRecord | None,
        count: PreparationOutcomeCount,
    ) -> bool:
        duties = await resolve_product_action_duties(
            context, self.policy_override_repo, self.platform_default_duties
        )
        scopes = {duty_scope(duties.duty_for(action)) for action in step.outcome_actions}
        if len(scopes) != 1:
            # One approval carries one stamped scope: outcomes of different
            # duties are refused, never stamped with one of them.
            return await self._not_started(
                context,
                case,
                step,
                transition_id,
                policy,
                previous,
                NotPreparedReason.OUTCOME_DUTIES_DIFFER,
                count,
            )
        thread_id = preparation_thread_id(case.id.value, transition_id, policy.policy_version)
        run_id = self.ids.new_uuid()
        try:
            await self.runner.start(
                run_context=RunContext(
                    run_id=run_id,
                    thread_id=thread_id,
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    actor_id=lane_actor(),
                    worker_id=self.worker_id,
                    worker_version=self.worker_version,
                    channel="worker",
                    plan_id=plan,
                    roles=frozenset(),
                    scopes=frozenset(),
                    trace_id=str(run_id),
                    subject_ref=f"product_dev_case:{case.id}",
                ),
                input_payload=preparation_input(
                    case,
                    transition_id,
                    policy.policy_version,
                    step,
                    next(iter(scopes)),
                ),
            )
        except ConflictError as exc:
            if exc.details.get("thread_id") == str(thread_id):
                return False  # another attempt holds the thread
            return await self._not_started(
                context,
                case,
                step,
                transition_id,
                policy,
                previous,
                NotPreparedReason.RUN_FAILED,
                count,
            )
        except QuotaExceededError:
            return await self._not_started(
                context,
                case,
                step,
                transition_id,
                policy,
                previous,
                NotPreparedReason.RUN_REFUSED,
                count,
            )
        except Exception:
            logger.exception("step preparation run failed", extra={"case_id": str(case.id)})
            return await self._not_started(
                context,
                case,
                step,
                transition_id,
                policy,
                previous,
                NotPreparedReason.RUN_FAILED,
                count,
            )
        count.started += 1
        raised = await self.approvals.raised_by_payload(
            context,
            approval_type=proposal_type(step.action),
            key=PROPOSAL_CASE_KEY,
            value=str(case.id),
        )
        if raised is not None:
            await self.notify(context, case, step, raised)
        return False

    async def _not_started(
        self,
        context: AccessContext,
        case: ProductDevelopmentCase,
        step: PreparedStep,
        transition_id: uuid.UUID,
        policy: SupplyChainStepPreparation,
        previous: PreparationRecord | None,
        reason: NotPreparedReason,
        count: PreparationOutcomeCount,
    ) -> bool:
        """A run refused or failed: recorded (once per reason), and the
        tenant's turn ends for this tick."""
        count.not_started += 1
        latest = await self.records.latest(context, transition_id, policy.policy_version)
        await record_once(
            self.records,
            context,
            self.ids,
            self.clock,
            latest if latest is not None else previous,
            NewPreparationRecord(
                id=self.ids.new_uuid(),
                case_id=case.id.value,
                transition_id=transition_id,
                policy_version=policy.policy_version,
                action=step.action.value,
                outcome=PreparationOutcome.NOT_PREPARED,
                reason=reason.value,
            ),
        )
        return True

    async def notify(
        self,
        context: AccessContext,
        case: ProductDevelopmentCase,
        step: PreparedStep,
        approval: PendingApprovalRecord,
    ) -> int:
        """Tell who may decide (the stamped scope and `approvals.decide`),
        once per approval and person. No price and no file content: the case's
        code and name, and where to decide (the case page for a step whose
        result is typed, the approval page otherwise)."""
        if approval.required_scope is None:
            return 0
        workspace = case.workspace_id.value
        try:
            stamped = set(
                await self.holders.holding(
                    context.tenant_id, workspace, frozenset({approval.required_scope})
                )
            )
            deciders = set(
                await self.holders.holding(
                    context.tenant_id, workspace, frozenset({APPROVALS_DECIDE})
                )
            )
            recipients = sorted(stamped & deciders - {lane_actor()})
            await self.notifier.deliver(
                context,
                recipients=recipients,
                source_key=f"supply_chain.step_proposal:{approval.id}",
                title=f"AI đã chuẩn bị bước tiếp theo: {case.proposal_code}",
                body=(
                    f"{case.product_name}: AI đề xuất chuyển bước kèm chứng từ đã soạn."
                    + (
                        " Nhập kết quả rồi duyệt trên trang hồ sơ."
                        if step.physical
                        else " Mở để kiểm và duyệt."
                    )
                ),
                link=product_case_link(case.id.value)
                if step.physical
                else approval_link(approval.id, workspace),
            )
            return len(recipients)
        except Exception:
            logger.exception(
                "step proposal raised but its notification failed",
                extra={"case_id": str(case.id), "approval_id": str(approval.id)},
            )
            return 0


def preparation_input(
    case: ProductDevelopmentCase,
    transition_id: uuid.UUID,
    policy_version: str,
    step: PreparedStep,
    required_scope: str,
) -> dict[str, Any]:
    """The run's input: every key the graph reads, so a new pass on a thread
    that ran before starts from nothing the last pass left."""
    return {
        PROPOSAL_CASE_KEY: str(case.id),
        "transition_id": str(transition_id),
        "policy_version": policy_version,
        "step": step.model_dump(mode="json"),
        "required_scope": required_scope,
        "outcome": "",
        "reason": "",
        "proposal": {},
        "approved": False,
        "approver_comment": "",
        "decided_by": "",
        "typed_input": {},
    }


__all__ = [
    "DRAFT_RECIPES",
    "EVALUATION_PROMPT",
    "PREPARATION_LANE",
    "STEP_CHECKS",
    "CaseDocumentListPort",
    "CaseDraftsPort",
    "EvaluationWriter",
    "ExtractionReadingsPort",
    "MeasurementsPort",
    "NewPreparationRecord",
    "PreparationRecord",
    "PreparationRecordsPort",
    "PreparationRequest",
    "PrepareStep",
    "PrepareSteps",
    "Prepared",
    "RecipeInput",
    "SampleFacts",
    "SamplePreparation",
    "StoredReading",
    "case_facts_recipe",
    "entered_current_state",
    "lane_context",
    "preparation_input",
    "preparation_thread_id",
    "reads_a_round",
    "record_audit",
    "record_once",
    "resolve_step_preparation",
    "step_documents",
    "suggestions_for",
]
