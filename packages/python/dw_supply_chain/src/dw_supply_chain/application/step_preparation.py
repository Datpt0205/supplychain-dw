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
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.doc_templates import DocTemplateSpec, TemplateFieldKind
from dw_kernel.errors import ConflictError, QuotaExceededError
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
from dw_supply_chain.domain.case_document import CaseDocument, CaseKind, DocumentType
from dw_supply_chain.domain.document_draft import (
    DRAFT_TEMPLATES,
    DocumentDraft,
    DraftSource,
    DraftStatus,
)
from dw_supply_chain.domain.extraction import EXTRACTION_SPECS, ExtractionStatus
from dw_supply_chain.domain.product_development_case import (
    ProductCaseTransition,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
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
from dw_supply_chain.product_action_duties import SupplyChainProductActionDuties
from dw_supply_chain.step_preparation_policy import (
    STEP_PREPARATION_POLICY_ID,
    PreparedStep,
    SupplyChainStepPreparation,
)

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


def case_facts_recipe(
    case: ProductDevelopmentCase,
    spec: DocTemplateSpec,
    readings: Sequence[SourceReading],
    skip: frozenset[str],
) -> dict[str, FieldInput]:
    """The case's own fields, then the cited readings' fields of the same name
    (with their document and quote); never a field in `skip` (the result a
    person types), never a table (a model's rows come with a drafting prompt)."""
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


Recipe = Callable[
    [ProductDevelopmentCase, DocTemplateSpec, Sequence[SourceReading], frozenset[str]],
    dict[str, FieldInput],
]
DRAFT_RECIPES: Mapping[DraftRecipe, Recipe] = {DraftRecipe.CASE_FACTS: case_facts_recipe}
assert set(DRAFT_RECIPES) == set(DraftRecipe)  # every recipe a policy may name is implemented


def suggestions_for(
    result_fields: Sequence[str], readings: Sequence[SourceReading]
) -> dict[str, dict[str, str]]:
    """AI's suggestion for each result field a reading says something about:
    the value in words, the quote and the document. Shown beside the field."""
    out: dict[str, dict[str, str]] = {}
    for name in result_fields:
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
    result = set(given.step.result_fields)
    found: list[Finding] = []
    for draft in given.drafts:
        gaps = [g for g in draft.gaps if g.split("[", 1)[0] not in result]
        if gaps:
            found.append(
                Finding(
                    "draft_gaps", draft.doc_type.value, "Bản nháp còn thiếu: " + ", ".join(gaps)
                )
            )
    return found


STEP_CHECKS: Mapping[StepCheck, Callable[[CheckInput], list[Finding]]] = {
    StepCheck.SOURCES_PRESENT: _sources_present,
    StepCheck.SOURCES_READ: _sources_read,
    StepCheck.DRAFTS_COMPLETE: _drafts_complete,
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
        newest = newest_by_type((d.doc_type, d.id.value, d.version) for d in documents)

        paper_from_source = self._paper_from_source(case, step, newest, by_id)
        readings = await self._readings(context, step, newest, by_id)
        drafts = await self._drafts(context, case, step, readings, previous)
        suggestions = suggestions_for(step.result_fields, readings) if step.physical else {}

        given = CheckInput(step=step, newest=newest, readings=readings, drafts=drafts)
        findings = [finding for check in step.checks for finding in STEP_CHECKS[check](given)]
        sources = [SubjectSource(t, newest.get(t)) for t in step.sources]
        target = (case.state, step.action)
        subject_version = proposal_subject_version(
            case.version,
            [SubjectDraft(d.id, d.content_sha256, open_latest=True) for d in drafts],
            sources,
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

    async def _drafts(
        self,
        context: AccessContext,
        case: ProductDevelopmentCase,
        step: PreparedStep,
        readings: Sequence[SourceReading],
        previous: PreparationRecord | None,
    ) -> list[DocumentDraft]:
        """Each draft the step names: the open latest version of the lineage
        an earlier attempt of THIS step entry prepared (a person's edits are
        kept), else a new one filled by its recipe."""
        reusable: dict[DocumentType, DocumentDraft] = {}
        if previous is not None and previous.draft_lineages:
            lineages = set(previous.draft_lineages)
            for draft in await self.drafts.latest_for_case(
                context, CaseKind.PRODUCT, case.id.value
            ):
                if draft.lineage_id in lineages and draft.status is DraftStatus.OPEN:
                    reusable[draft.doc_type] = draft
        result = frozenset(step.result_fields)
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
            values = DRAFT_RECIPES[planned.recipe](case, template.spec, readings, result)
            drafts.append(
                await self.prepare_draft.handle(
                    context,
                    case_kind=CaseKind.PRODUCT,
                    case_id=case.id.value,
                    doc_type=planned.doc_type,
                    values=values,
                    sources=[_draft_source(r) for r in readings],
                )
            )
        return drafts


def _draft_source(source: SourceReading) -> DraftSource:
    return DraftSource(
        document_id=source.document_id,
        sha256=source.reading.sha256,
        extraction_id=source.reading.id,
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
                    duty_scope(duties.duty_for(step.action)),
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
    "PREPARATION_LANE",
    "STEP_CHECKS",
    "CaseDocumentListPort",
    "CaseDraftsPort",
    "ExtractionReadingsPort",
    "NewPreparationRecord",
    "PreparationRecord",
    "PreparationRecordsPort",
    "PreparationRequest",
    "PrepareStep",
    "PrepareSteps",
    "Prepared",
    "StoredReading",
    "case_facts_recipe",
    "entered_current_state",
    "lane_context",
    "preparation_input",
    "preparation_thread_id",
    "record_audit",
    "record_once",
    "resolve_step_preparation",
    "suggestions_for",
]
