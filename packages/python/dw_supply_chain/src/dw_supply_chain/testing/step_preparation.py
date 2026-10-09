"""An in-memory world for step preparation (ticket ai-automation/05): its unit
tests and the `supply_chain_preparation` eval graders run the REAL `PrepareStep`,
`ApplyStepProposal` and `StepProposalSubject` over it, with the shipped
templates and the real DOCX renderer.

Every store keeps its port's promise: a reader sees only its own tenant AND
workspace (RLS), a draft version is decided once (the UNIQUE), a case is saved
only over the version it was read at, and `apply_approved` is all or nothing.
`leaky=True` on the documents or readings stands in for an adapter that forgot
the workspace, so a guard above it can be exercised.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from dw_agent_runtime.adapters.docx_templates import DocxRenderer, DocxTemplateInspector
from dw_agent_runtime.doc_templates import DocTemplateRegistry, LoadedDocTemplate
from dw_kernel.errors import ConflictError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.pagination import CursorPosition, Page, PageRequest, build_page
from dw_kernel.ports import FixedClock, Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.application.document_drafts import (
    NewDocumentDraft,
    NewDraftDecision,
    PrepareDocumentDraft,
)
from dw_supply_chain.application.ports import ProductCaseListFilter
from dw_supply_chain.application.sample_checklist import NewMeasurement
from dw_supply_chain.application.step_preparation import (
    EvaluationWriter,
    NewPreparationRecord,
    PreparationRecord,
    PreparationRequest,
    PrepareStep,
    SamplePreparation,
    StoredReading,
    lane_context,
)
from dw_supply_chain.application.step_proposals import (
    AiPreparedDocument,
    ApplyStepProposal,
    StepProposalSubject,
)
from dw_supply_chain.domain.case_document import (
    CaseDocument,
    CaseDocumentId,
    CaseKind,
    DocumentType,
)
from dw_supply_chain.domain.document_draft import DocumentDraft, DraftDecision, DraftSource
from dw_supply_chain.domain.extraction import EXTRACTION_SPECS, ExtractionStatus
from dw_supply_chain.domain.product_development_case import (
    ProductAction,
    ProductCaseTransition,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
)
from dw_supply_chain.domain.sample_evaluation import Measurement
from dw_supply_chain.sample_criteria_policy import (
    SupplyChainSampleCriteria,
    load_supply_chain_sample_criteria,
)
from dw_supply_chain.step_preparation_policy import (
    PreparedStep,
    load_supply_chain_step_preparation,
)

REPO_ROOT = Path(__file__).resolve().parents[6]
NOW = datetime(2026, 10, 9, 9, 0, tzinfo=UTC)
# The criteria Elmich's R&D measures against (provisional thresholds).
ELMICH_CRITERIA = load_supply_chain_sample_criteria(
    REPO_ROOT / "scripts" / "elmich_sample_criteria_override.yaml"
)


def _sees(context: AccessContext, tenant: uuid.UUID, workspace: uuid.UUID) -> bool:
    return (tenant, workspace) == (context.tenant_id, context.workspace_id)


def shipped_templates() -> DocTemplateRegistry:
    registry = DocTemplateRegistry(inspector=DocxTemplateInspector())
    registry.load_directory(REPO_ROOT / "configs" / "doc_templates")
    return registry


@dataclass
class PlatformTemplates:
    """`DraftTemplatesPort` over the shipped templates (no tenant override)."""

    registry: DocTemplateRegistry = field(default_factory=shipped_templates)

    async def resolve(
        self, context: AccessContext, template_id: str, version: str
    ) -> LoadedDocTemplate:
        return self.registry.resolve(template_id, version)


@dataclass
class InMemoryStepCases:
    cases: dict[uuid.UUID, ProductDevelopmentCase] = field(default_factory=dict)
    transitions: dict[uuid.UUID, list[ProductCaseTransition]] = field(default_factory=dict)

    def _visible(self, context: AccessContext, case_id: uuid.UUID) -> ProductDevelopmentCase | None:
        case = self.cases.get(case_id)
        if case is None or not _sees(context, case.tenant_id.value, case.workspace_id.value):
            return None
        return case

    async def get(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> ProductDevelopmentCase | None:
        case = self._visible(context, case_id.value)
        # A fresh copy, as a read from the database is: a caller mutating it
        # changes nothing until it is saved.
        return None if case is None else replace(case, _pending_steps=[])

    async def case_workspace(self, context: AccessContext, case_id: uuid.UUID) -> uuid.UUID | None:
        case = self._visible(context, case_id)
        return None if case is None else case.workspace_id.value

    async def list_page(
        self, context: AccessContext, request: PageRequest, case_filter: ProductCaseListFilter
    ) -> Page[ProductDevelopmentCase]:
        found = [
            replace(c, _pending_steps=[])
            for c in self.cases.values()
            if _sees(context, c.tenant_id.value, c.workspace_id.value)
            and (case_filter.state is None or c.state is case_filter.state)
        ]
        found.sort(key=lambda c: c.id.value)
        return build_page(
            found[: request.fetch_limit],
            request=request,
            position_of=lambda c: CursorPosition(sort_value=NOW, tiebreaker=c.id.value),
        )

    async def list_transitions(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId, request: PageRequest
    ) -> Page[ProductCaseTransition]:
        if self._visible(context, case_id.value) is None:
            return build_page(
                [], request=request, position_of=lambda t: CursorPosition(NOW, uuid.uuid4())
            )
        rows = sorted(
            self.transitions.get(case_id.value, []), key=lambda t: t.occurred_at, reverse=True
        )
        return build_page(
            rows[: request.fetch_limit],
            request=request,
            position_of=lambda t: CursorPosition(
                sort_value=t.occurred_at, tiebreaker=t.id or uuid.uuid4()
            ),
        )

    def enter(self, case: ProductDevelopmentCase, at: datetime) -> ProductCaseTransition:
        """Record that `case` entered its current state at `at`."""
        transition = ProductCaseTransition(
            action=ProductAction.RECEIVE_SAMPLE,
            from_state=None,
            to_state=case.state,
            reason=None,
            actor_id=uuid.uuid4(),
            occurred_at=at,
            id=uuid.uuid4(),
        )
        self.transitions.setdefault(case.id.value, []).append(transition)
        return transition


@dataclass
class InMemoryDocuments:
    rows: list[CaseDocument] = field(default_factory=list)
    leaky: bool = False

    def _seen(self, context: AccessContext, row: CaseDocument) -> bool:
        return self.leaky or _sees(context, row.tenant_id, row.workspace_id)

    async def list_for_case(
        self, context: AccessContext, case_kind: CaseKind, case_id: uuid.UUID
    ) -> list[CaseDocument]:
        return [r for r in self.rows if r.case_id == case_id and self._seen(context, r)]

    async def get(self, context: AccessContext, document_id: CaseDocumentId) -> CaseDocument | None:
        return next((r for r in self.rows if r.id == document_id and self._seen(context, r)), None)


@dataclass
class InMemoryReadings:
    rows: list[tuple[uuid.UUID, uuid.UUID, StoredReading]] = field(default_factory=list)
    leaky: bool = False

    async def readings(
        self, context: AccessContext, document_ids: Sequence[uuid.UUID]
    ) -> list[StoredReading]:
        return [
            r
            for tenant, workspace, r in self.rows
            if r.document_id in document_ids
            and (self.leaky or (tenant, workspace) == (context.tenant_id, context.workspace_id))
        ]


@dataclass
class InMemoryDrafts:
    rows: list[DocumentDraft] = field(default_factory=list)
    decisions: dict[uuid.UUID, tuple[DraftDecision, str | None, uuid.UUID]] = field(
        default_factory=dict
    )
    audits: list[AuditEvent] = field(default_factory=list)

    def _with_state(self, d: DocumentDraft) -> DocumentDraft:
        latest = max(r.version for r in self.rows if r.lineage_id == d.lineage_id)
        decision = self.decisions.get(d.id)
        return replace(
            d,
            latest_version=latest,
            decision=None if decision is None else decision[0],
            decision_reason=None if decision is None else decision[1],
        )

    def insert(self, context: AccessContext, draft: NewDocumentDraft) -> DocumentDraft:
        if any(r.lineage_id == draft.lineage_id and r.version == draft.version for r in self.rows):
            raise ConflictError("version taken")
        row = DocumentDraft(
            id=draft.id,
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            lineage_id=draft.lineage_id,
            version=draft.version,
            case_kind=draft.case_kind,
            case_id=draft.case_id,
            doc_type=draft.doc_type,
            template_id=draft.template_id,
            template_version=draft.template_version,
            prompt_id=draft.prompt_id,
            prompt_version=draft.prompt_version,
            fields=draft.fields,
            gaps=tuple(draft.gaps),
            sources=tuple(
                DraftSource(document_id=uuid.UUID(str(s["document_id"])), sha256=str(s["sha256"]))
                for s in draft.sources
            ),
            content_sha256=draft.content_sha256,
            created_by=context.principal_id,
            created_at=NOW,
        )
        self.rows.append(row)
        return row

    async def add(
        self, context: AccessContext, draft: NewDocumentDraft, *, audit: AuditEvent
    ) -> DocumentDraft:
        row = self.insert(context, draft)
        self.audits.append(audit)
        return self._with_state(row)

    async def get(self, context: AccessContext, draft_id: uuid.UUID) -> DocumentDraft | None:
        for row in self.rows:
            if row.id == draft_id and _sees(context, row.tenant_id, row.workspace_id):
                return self._with_state(row)
        return None

    async def latest_for_case(
        self, context: AccessContext, case_kind: CaseKind, case_id: uuid.UUID
    ) -> list[DocumentDraft]:
        mine = [
            self._with_state(r)
            for r in self.rows
            if _sees(context, r.tenant_id, r.workspace_id) and r.case_id == case_id
        ]
        return [r for r in mine if r.version == r.latest_version]

    async def decide(
        self, context: AccessContext, decision: NewDraftDecision, *, audit: AuditEvent
    ) -> None:
        self.record_decision(context, decision)
        self.audits.append(audit)

    def record_decision(self, context: AccessContext, decision: NewDraftDecision) -> None:
        if decision.draft_id in self.decisions:
            raise ConflictError("this draft version already has a decision")
        self.decisions[decision.draft_id] = (
            decision.decision,
            decision.reason,
            context.principal_id,
        )


@dataclass
class InMemoryRecords:
    rows: list[tuple[uuid.UUID, uuid.UUID, PreparationRecord, uuid.UUID]] = field(
        default_factory=list
    )
    audits: list[AuditEvent] = field(default_factory=list)
    clock: FixedClock = field(default_factory=lambda: FixedClock(NOW))

    def insert(self, context: AccessContext, record: NewPreparationRecord) -> None:
        self.rows.append(
            (
                context.tenant_id,
                context.workspace_id,
                PreparationRecord(
                    id=record.id,
                    case_id=record.case_id,
                    transition_id=record.transition_id,
                    policy_version=record.policy_version,
                    action=record.action,
                    outcome=record.outcome,
                    reason=record.reason,
                    run_id=record.run_id,
                    draft_lineages=record.draft_lineages,
                    created_at=self.clock.now(),
                ),
                context.principal_id,
            )
        )

    async def latest(
        self, context: AccessContext, transition_id: uuid.UUID, policy_version: str
    ) -> PreparationRecord | None:
        mine = [
            r
            for tenant, workspace, r, _ in self.rows
            if (tenant, workspace) == (context.tenant_id, context.workspace_id)
            and r.transition_id == transition_id
            and r.policy_version == policy_version
        ]
        return mine[-1] if mine else None

    async def add(
        self, context: AccessContext, record: NewPreparationRecord, *, audit: AuditEvent
    ) -> None:
        self.insert(context, record)
        self.audits.append(audit)

    def outcomes(self) -> list[str]:
        return [r.outcome.value for _, _, r, _ in self.rows]


@dataclass
class InMemoryStorage:
    objects: dict[str, tuple[bytes, str]] = field(default_factory=dict)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = (data, content_type)

    async def get(self, key: str) -> bytes:
        return self.objects[key][0]

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)


@dataclass
class InMemoryOutcomes:
    """`ProposalOutcomesPort`: all or nothing, as one transaction is."""

    cases: InMemoryStepCases
    drafts: InMemoryDrafts
    documents: InMemoryDocuments
    records: InMemoryRecords
    audits: list[AuditEvent] = field(default_factory=list)
    applied: int = 0

    async def apply_approved(
        self,
        context: AccessContext,
        *,
        case: ProductDevelopmentCase,
        new_drafts: Sequence[NewDocumentDraft],
        confirmations: Sequence[NewDraftDecision],
        documents: Sequence[AiPreparedDocument],
        record: NewPreparationRecord,
        audits: Sequence[AuditEvent],
    ) -> None:
        stored = self.cases.cases[case.id.value]
        if stored.version != case.version - 1:
            raise ConflictError("product case was modified concurrently")
        decided = set(self.drafts.decisions)
        if any(c.draft_id in decided for c in confirmations):
            raise ConflictError("this draft version already has a decision")
        for draft in new_drafts:
            self.drafts.insert(context, draft)
        for decision in confirmations:
            self.drafts.record_decision(context, decision)
        for prepared in documents:
            self.documents.rows.append(_stored(context, prepared))
        case.pop_pending_steps()
        self.cases.cases[case.id.value] = replace(case, _pending_steps=[])
        self.records.insert(context, record)
        self.audits.extend(audits)
        self.applied += 1

    async def reject(
        self,
        context: AccessContext,
        *,
        decisions: Sequence[NewDraftDecision],
        record: NewPreparationRecord,
        audits: Sequence[AuditEvent],
    ) -> None:
        if any(d.draft_id in self.drafts.decisions for d in decisions):
            raise ConflictError("this draft version already has a decision")
        for decision in decisions:
            self.drafts.record_decision(context, decision)
        self.records.insert(context, record)
        self.audits.extend(audits)


# Which draft each AI-prepared document came from, beside the stored row.
DOCUMENT_DRAFTS: dict[uuid.UUID, uuid.UUID] = {}


def _stored(context: AccessContext, prepared: AiPreparedDocument) -> CaseDocument:
    d = prepared.document
    DOCUMENT_DRAFTS[d.id.value] = prepared.draft_id
    return CaseDocument(
        id=d.id,
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        case_kind=d.case_kind,
        case_id=d.case_id,
        doc_type=d.doc_type,
        object_key=d.object_key,
        filename=d.filename,
        content_type=d.content_type,
        size_bytes=d.size_bytes,
        sha256=d.sha256,
        version=1,
        uploaded_by=context.principal_id,
        uploaded_at=NOW,
    )


@dataclass
class InMemoryMeasurements:
    """`MeasurementStorePort`: a reader sees its own tenant AND workspace."""

    rows: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID, Measurement]] = field(default_factory=list)
    leaky: bool = False

    async def for_round(
        self, context: AccessContext, case_id: uuid.UUID, sample_round: int
    ) -> list[Measurement]:
        return [
            m
            for tenant, workspace, case, m in self.rows
            if case == case_id
            and m.sample_round == sample_round
            and (self.leaky or (tenant, workspace) == (context.tenant_id, context.workspace_id))
        ]

    async def add(
        self, context: AccessContext, measurement: NewMeasurement, *, audit: AuditEvent
    ) -> None:
        self.rows.append(
            (
                context.tenant_id,
                context.workspace_id,
                measurement.case_id,
                Measurement(
                    id=measurement.id,
                    sample_round=measurement.sample_round,
                    criterion=measurement.criterion,
                    value=measurement.value,
                    note=measurement.note,
                    entered_by=context.principal_id,
                    entered_at=NOW + timedelta(seconds=len(self.rows)),
                ),
            )
        )


class _StaticPlans:
    async def plan_of(self, tenant_id: uuid.UUID) -> str | None:
        return "professional"


class _NoOverrides:
    async def get(self, context: AccessContext, policy_id: str) -> None:
        return None

    async def put(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("not exercised by step preparation")


@dataclass
class StepWorld:
    """The real preparer, applier and subject over in-memory stores."""

    tenant_id: uuid.UUID = field(default_factory=uuid.uuid4)
    workspace_id: uuid.UUID = field(default_factory=uuid.uuid4)
    cases: InMemoryStepCases = field(default_factory=InMemoryStepCases)
    documents: InMemoryDocuments = field(default_factory=InMemoryDocuments)
    readings: InMemoryReadings = field(default_factory=InMemoryReadings)
    drafts: InMemoryDrafts = field(default_factory=InMemoryDrafts)
    records: InMemoryRecords = field(default_factory=InMemoryRecords)
    storage: InMemoryStorage = field(default_factory=InMemoryStorage)
    templates: PlatformTemplates = field(default_factory=PlatformTemplates)
    clock: FixedClock = field(default_factory=lambda: FixedClock(NOW))
    measurements: InMemoryMeasurements = field(default_factory=InMemoryMeasurements)
    criteria: SupplyChainSampleCriteria = field(default_factory=lambda: ELMICH_CRITERIA)
    # The model that words a round (ticket ai-automation/09): a gateway whose
    # `generate_structured` answers; None wires no writer at all.
    gateway: Any = None
    model_profile: str | None = None

    def context(
        self,
        principal: uuid.UUID | None = None,
        *,
        tenant: uuid.UUID | None = None,
        workspace: uuid.UUID | None = None,
        scopes: frozenset[str] = frozenset(),
    ) -> AccessContext:
        return AccessContext(
            tenant_id=tenant or self.tenant_id,
            workspace_id=workspace or self.workspace_id,
            principal_id=principal or uuid.uuid4(),
            roles=frozenset(),
            scopes=scopes,
            plan_id="professional",
        )

    outcomes: InMemoryOutcomes = field(init=False)

    def __post_init__(self) -> None:
        self.outcomes = InMemoryOutcomes(self.cases, self.drafts, self.documents, self.records)

    def sample(self) -> SamplePreparation:
        return SamplePreparation(
            measurements=self.measurements,
            policy_override_repo=_NoOverrides(),
            platform_default_criteria=self.criteria,
            writer=None
            if self.gateway is None
            else EvaluationWriter(
                gateway=self.gateway,
                plans=_StaticPlans(),
                ids=Uuid4Generator(),
                worker_id="supply_chain_step_preparation",
                worker_version="1.0.0",
                model_profile=self.model_profile,
            ),
        )

    def subject(self) -> StepProposalSubject:
        return StepProposalSubject(
            cases=self.cases, drafts=self.drafts, documents=self.documents, sample=self.sample()
        )

    def preparer(self) -> PrepareStep:
        ids = Uuid4Generator()
        return PrepareStep(
            cases=self.cases,
            documents=self.documents,
            readings=self.readings,
            drafts=self.drafts,
            prepare_draft=PrepareDocumentDraft(
                cases={CaseKind.PRODUCT: self.cases},
                drafts=self.drafts,
                templates=self.templates,
                ids=ids,
                clock=self.clock,
            ),
            templates=self.templates,
            records=self.records,
            ids=ids,
            clock=self.clock,
            sample=self.sample(),
        )

    def applier(self) -> ApplyStepProposal:
        return ApplyStepProposal(
            cases=self.cases,
            drafts=self.drafts,
            documents=self.documents,
            templates=self.templates,
            renderer=DocxRenderer(),
            storage=self.storage,
            subject=self.subject(),
            outcomes=self.outcomes,
            ids=Uuid4Generator(),
            clock=self.clock,
        )

    # -- seeding -----------------------------------------------------------

    def add_case(
        self,
        state: ProductDevState,
        *,
        sample_round: int = 1,
        entered_at: datetime = NOW - timedelta(days=1),
        supplier_name: str | None = "Công ty Gia dụng Minh Phát",
        tenant: uuid.UUID | None = None,
        workspace: uuid.UUID | None = None,
        product_name: str = "Nồi inox 3 đáy 24cm",
        category: str = "kitchen",
    ) -> tuple[ProductDevelopmentCase, ProductCaseTransition]:
        case = ProductDevelopmentCase(
            id=ProductDevelopmentCaseId(uuid.uuid4()),
            tenant_id=TenantId(tenant or self.tenant_id),
            workspace_id=WorkspaceId(workspace or self.workspace_id),
            proposal_code="DX-2026-041",
            product_name=product_name,
            category=category,
            pic_user_id=uuid.uuid4(),
            created_by=uuid.uuid4(),
            supplier_name=supplier_name,
            state=state,
            sample_round=sample_round,
            round_opened_at=entered_at,
            stage_entered_at=entered_at,
            version=5,
            created_at=entered_at - timedelta(days=10),
        )
        self.cases.cases[case.id.value] = case
        return case, self.cases.enter(case, entered_at)

    def add_document(
        self,
        case: ProductDevelopmentCase,
        doc_type: DocumentType,
        *,
        text: str = "",
        uploaded_at: datetime = NOW - timedelta(hours=2),
        version: int = 1,
        tenant: uuid.UUID | None = None,
        workspace: uuid.UUID | None = None,
    ) -> CaseDocument:
        data = (text or doc_type.value).encode("utf-8")
        document = CaseDocument(
            id=CaseDocumentId(uuid.uuid4()),
            tenant_id=tenant or case.tenant_id.value,
            workspace_id=workspace or case.workspace_id.value,
            case_kind=CaseKind.PRODUCT,
            case_id=case.id.value,
            doc_type=doc_type,
            object_key=f"k/{uuid.uuid4()}",
            filename=f"{doc_type.value}.pdf",
            content_type="application/pdf",
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            version=version,
            uploaded_by=uuid.uuid4(),
            uploaded_at=uploaded_at,
        )
        self.documents.rows.append(document)
        return document

    def add_reading(
        self,
        document: CaseDocument,
        fields: Mapping[str, Any],
        *,
        status: ExtractionStatus = ExtractionStatus.EXTRACTED,
        gaps: Sequence[Mapping[str, str]] = (),
    ) -> StoredReading:
        spec = EXTRACTION_SPECS[document.doc_type]
        reading = StoredReading(
            id=uuid.uuid4(),
            document_id=document.id.value,
            sha256=document.sha256,
            prompt_id=spec.prompt_id,
            prompt_version=spec.prompt_version,
            status=status,
            fields=dict(fields),
            gaps=list(gaps),
        )
        self.readings.rows.append((document.tenant_id, document.workspace_id, reading))
        return reading

    def request(
        self,
        case: ProductDevelopmentCase,
        transition: ProductCaseTransition,
        step: PreparedStep,
        *,
        required_scope: str = "supply_chain.duty.rnd",
        policy_version: str = "1.0.0",
    ) -> PreparationRequest:
        assert transition.id is not None
        return PreparationRequest(
            case_id=case.id.value,
            transition_id=transition.id,
            policy_version=policy_version,
            step=step,
            required_scope=required_scope,
            run_id=uuid.uuid4(),
        )

    def lane_context(self, case: ProductDevelopmentCase) -> AccessContext:
        return lane_context(case.tenant_id.value, case.workspace_id.value)

    def measure(
        self, case: ProductDevelopmentCase, criterion: str, value: str, *, sample_round: int = 1
    ) -> None:
        self.measurements.rows.append(
            (
                case.tenant_id.value,
                case.workspace_id.value,
                case.id.value,
                Measurement(
                    id=uuid.uuid4(),
                    sample_round=sample_round,
                    criterion=criterion,
                    value=value,
                    note=None,
                    entered_by=uuid.uuid4(),
                    entered_at=NOW - timedelta(minutes=30 - len(self.measurements.rows)),
                ),
            )
        )


# Elmich's override (scripts/elmich_step_preparation_override.yaml) and the
# criteria it measures against (`ELMICH_CRITERIA`), read from the files
# themselves: one owner.
ELMICH_PREPARATION = load_supply_chain_step_preparation(
    REPO_ROOT / "scripts" / "elmich_step_preparation_override.yaml"
)


# Steps 3-5 as Elmich prepares them (ticket ai-automation/09): measured,
# worded by a model, three outcomes.
def _elmich_step(state: ProductDevState) -> PreparedStep:
    step = ELMICH_PREPARATION.step_for(CaseKind.PRODUCT, state)
    if step is None:
        raise LookupError(f"Elmich's override prepares no {state.value}")
    return step


SAMPLE_ROUND = _elmich_step(ProductDevState.SAMPLE_TESTING)

# AI-05's first preparation of steps 3-5 (the case's facts and the readings,
# one outcome), kept for the eval cases that grade what it guarantees.
SAMPLE_TESTING = PreparedStep.model_validate(
    {
        "case_kind": "product",
        "state": "sample_testing",
        "action": "pass_sample",
        "sources": ["sample_evaluation", "supplier_quotation"],
        "drafts": [{"doc_type": "sample_evaluation", "recipe": "case_facts"}],
        "checks": ["sources_read", "drafts_complete"],
        "physical": True,
        "result_fields": ["evaluated_on", "conclusion"],
    }
)
SUPPLIER_CONFIRMATION = PreparedStep.model_validate(
    {
        "case_kind": "product",
        "state": "supplier_confirmation",
        "action": "confirm_with_supplier",
        "sources": ["supplier_confirmation_email"],
        "checks": ["sources_present", "sources_read"],
    }
)
