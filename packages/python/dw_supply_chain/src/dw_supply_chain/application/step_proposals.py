"""A step proposal once it is raised: its subject, its decision, its page
(ADR 0025 points 3-7; ticket ai-automation/05).

- **The subject** (`StepProposalSubject`, the platform's
  `ApprovalSubjectVersionPort` under `supply_chain.step_proposal.`): the case's
  version, each named draft still the open latest version with its content,
  each source type's newest document. The approval is stamped with it when
  raised; a decision on another one is refused (409) before anything is
  written, a code issued for an older one admits nothing (ADR 0007), and the
  lane supersedes the proposal. Read under the caller's context, so another
  workspace's case gives no version.
- **The decision** (`ApplyStepProposal`, called by the graph after a person
  decided, as that person): approving makes each draft a `case_documents` row
  (`origin = ai_prepared`, the file rendered from its pinned template), confirms
  it, and takes the step through `apply_product_action` (the dispatch a click
  uses), all in ONE transaction with the audit events; for a physical step the
  typed result is a new version of the record first. Not approving rejects the
  drafts with the decider's comment and leaves the case where it is. A subject
  that moved between the decision and the resume applies nothing (`superseded`).
- **Typed results** (`check_result`): one check for the route that refuses a
  bad value before deciding and for the graph that writes it.
- **The page** (`GetStepProposal`) and the web decision (`DecideStepProposal`)
  of the case page's "AI đã chuẩn bị" block; the policy's GET and PUT.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from dw_agent_runtime.doc_templates import DocTemplateSpec, DocumentRendererPort
from dw_kernel.errors import DomainError, NotFoundError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import holds_stamped_scope
from dw_platform.application.ports import AuthorizationPort, PolicyOverridePort
from dw_platform.domain.approval import APPROVALS_DECIDE, SUBJECT_VERSION_KEY, ApprovalRequest
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.application.bm04_prefill import Bm04ProfileWriter
from dw_supply_chain.application.commercial import PROFILE_SAVED, allows
from dw_supply_chain.application.document_drafts import (
    DraftTemplatesPort,
    NewDocumentDraft,
    NewDraftDecision,
    check_values,
    compute_gaps,
    template_values,
)
from dw_supply_chain.application.handlers import (
    ACTION_DUTIES_READ,
    ACTION_DUTIES_WRITE,
    PRODUCT_CASE_READ,
    _put_policy_override,
)
from dw_supply_chain.application.ports import (
    CaseDocumentStoragePort,
    NewCaseDocument,
    PendingApprovalRecord,
)
from dw_supply_chain.application.product_case_audit import product_case_audit
from dw_supply_chain.application.step_preparation import (
    CaseDocumentListPort,
    CaseDraftsPort,
    CaseHistoryPort,
    NewPreparationRecord,
    PreparationRecord,
    PreparationRecordsPort,
    SamplePreparation,
    entered_current_state,
    reads_a_round,
    record_audit,
    resolve_step_preparation,
    step_documents,
)
from dw_supply_chain.domain.case_document import (
    CaseDocument,
    CaseDocumentId,
    CaseKind,
    DocumentType,
    ObjectKey,
)
from dw_supply_chain.domain.commercial import NewProductProfile
from dw_supply_chain.domain.document_draft import (
    DRAFT_TEMPLATES,
    DocumentDraft,
    DraftDecision,
    DraftStatus,
    content_sha256,
)
from dw_supply_chain.domain.product_development_case import (
    ACTION_DOCUMENT_TYPE,
    ProductActionInput,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    apply_product_action,
)
from dw_supply_chain.domain.sample_evaluation import measurements_digest
from dw_supply_chain.domain.step_proposal import (
    OUTCOME_REASON_ACTIONS,
    PROPOSAL_CASE_KEY,
    PreparationOutcome,
    SubjectDraft,
    SubjectSource,
    newest_by_type,
    proposal_subject_version,
    proposal_type,
)
from dw_supply_chain.step_preparation_policy import (
    STEP_PREPARATION_POLICY_ID,
    PreparedStep,
    SupplyChainStepPreparation,
)

PROPOSAL_APPLIED = "step_proposal_applied"
DRAFT_CONFIRMED = "supply_chain.document_draft.confirmed"
DRAFT_REJECTED = "supply_chain.document_draft.rejected"
AI_DOCUMENT_ADDED = "supply_chain.case_document.ai_prepared"
# A confirmed BM04 whose required fields the tenant's schema still lacks.
PROFILE_NOT_SAVED = "supply_chain.product_profile.not_saved"
_POLICY_RESOURCE = "step_preparation_policy"
_REJECTION_REASON_MAX = 1000


# ----------------------------------------------------------------- subject --


class ProposalSubjectPort(Protocol):
    """The current subject version of a proposal payload, or None."""

    async def current(self, context: AccessContext, payload: Mapping[str, Any]) -> str | None: ...


@dataclass(frozen=True)
class StepProposalSubject:
    """Implements `ApprovalSubjectVersionPort` for step proposals."""

    cases: CaseHistoryPort
    drafts: CaseDraftsPort
    documents: CaseDocumentListPort
    # A step that reads a sample round binds its measurements too.
    sample: SamplePreparation | None = None

    async def version_of(self, context: AccessContext, request: ApprovalRequest) -> str | None:
        return await self.current(context, request.payload)

    async def current(self, context: AccessContext, payload: Mapping[str, Any]) -> str | None:
        try:
            case_id = uuid.UUID(str(payload.get(PROPOSAL_CASE_KEY)))
            named = [
                (uuid.UUID(str(d["draft_id"])), str(d["content_sha256"]))
                for d in payload.get("drafts") or []
            ]
            types = [DocumentType(str(s["doc_type"])) for s in payload.get("sources") or []]
            step = PreparedStep.model_validate(payload["step"]) if "step" in payload else None
        except (KeyError, TypeError, ValueError):
            return None
        case = await self.cases.get(context, ProductDevelopmentCaseId(case_id))
        if case is None:
            return None
        drafts: list[SubjectDraft] = []
        for draft_id, sha in named:
            draft = await self.drafts.get(context, draft_id)
            still = (
                draft is not None
                and draft.status is DraftStatus.OPEN
                and draft.content_sha256 == sha
            )
            drafts.append(SubjectDraft(draft_id, sha, open_latest=still))
        documents = await self.documents.list_for_case(context, CaseKind.PRODUCT, case_id)
        newest = (
            newest_by_type((d.doc_type, d.id.value, d.version) for d in documents)
            if step is None
            else step_documents(case, step, documents)
        )
        facts = ""
        if step is not None and reads_a_round(step):
            if self.sample is None:
                return None
            facts = measurements_digest(await self.sample.results(context, case))
        return proposal_subject_version(
            case.version, drafts, [SubjectSource(t, newest.get(t)) for t in types], facts
        )


# ------------------------------------------------------------ typed result --


def check_result(
    spec: DocTemplateSpec, result_fields: Sequence[str], values: Mapping[str, Any]
) -> dict[str, str]:
    """A physical step's result as the record takes it: exactly its result
    fields, each filled and of its template kind (a date ISO, a number
    canonical), or a 422 naming what is wrong."""
    unknown = sorted(set(values) - set(result_fields))
    if unknown:
        raise DomainError("kết quả có ô không thuộc bước này", details={"unknown": unknown})
    clean = check_values(spec, {name: values.get(name) for name in result_fields})
    missing = sorted(n for n in result_fields if not isinstance(clean.get(n), str))
    if missing:
        raise DomainError("cần nhập kết quả trước khi duyệt", details={"missing": missing})
    return {name: str(clean[name]) for name in result_fields}


# ------------------------------------------------------------- applying ----


@dataclass(frozen=True, slots=True)
class AiPreparedDocument:
    """A case document made from a confirmed draft version."""

    document: NewCaseDocument
    draft_id: uuid.UUID


class ProposalOutcomesPort(Protocol):
    """The two ends of a proposal, each one transaction with its audit."""

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
        profile: NewProductProfile | None = None,
    ) -> None:
        """New draft versions, their confirmations, the documents, the case's
        step (optimistic on its version), the record and, for a BM04, the
        product profile version it makes: all or nothing. A confirmation of
        an already-decided version is a `ConflictError`."""
        ...

    async def reject(
        self,
        context: AccessContext,
        *,
        decisions: Sequence[NewDraftDecision],
        record: NewPreparationRecord,
        audits: Sequence[AuditEvent],
    ) -> None: ...


class CaseDocumentGetPort(Protocol):
    async def get(
        self, context: AccessContext, document_id: CaseDocumentId
    ) -> CaseDocument | None: ...


def _audit(
    context: AccessContext,
    ids: IdGenerator,
    clock: UtcClock,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID,
    details: dict[str, Any],
) -> AuditEvent:
    return AuditEvent(
        id=ids.new_uuid(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        actor_id=UserId(context.principal_id),
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id),
        occurred_at=clock.now(),
        details=details,
    )


@dataclass(frozen=True)
class ApplyStepProposal:
    """Implements the preparation graph's `StepProposalApplierPort`."""

    cases: CaseHistoryPort
    drafts: CaseDraftsPort
    documents: CaseDocumentGetPort
    templates: DraftTemplatesPort
    renderer: DocumentRendererPort
    storage: CaseDocumentStoragePort
    subject: ProposalSubjectPort
    outcomes: ProposalOutcomesPort
    ids: IdGenerator
    clock: UtcClock
    # Step 7 (ticket ai-automation/11): a confirmed BM04 becomes a
    # `product_profiles` version with the step. None: no BM04 step is applied
    # by this host (the BM04 still becomes a document).
    profiles: Bm04ProfileWriter | None = None

    def _record(
        self,
        payload: Mapping[str, Any],
        outcome: PreparationOutcome,
        reason: str | None,
        run_id: uuid.UUID | None,
    ) -> NewPreparationRecord:
        return NewPreparationRecord(
            id=self.ids.new_uuid(),
            case_id=uuid.UUID(str(payload[PROPOSAL_CASE_KEY])),
            transition_id=uuid.UUID(str(payload["transition_id"])),
            policy_version=str(payload["policy_version"]),
            action=str(payload["action"]),
            outcome=outcome,
            reason=reason,
            run_id=run_id,
        )

    async def apply(
        self,
        context: AccessContext,
        payload: Mapping[str, Any],
        *,
        approved: bool,
        comment: str,
        typed_input: Mapping[str, str],
        run_id: uuid.UUID | None,
    ) -> str:
        """`context` is the decider's, bounded by the run's tenant and
        workspace. Returns the outcome the run completes with."""
        step = PreparedStep.model_validate(payload["step"])
        case = await self.cases.get(
            context, ProductDevelopmentCaseId(uuid.UUID(str(payload[PROPOSAL_CASE_KEY])))
        )
        if case is None:
            raise NotFoundError("product case not found")
        if not approved:
            return await self._reject(context, payload, comment, run_id)
        current = await self.subject.current(context, payload)
        if current != payload.get(SUBJECT_VERSION_KEY) or case.state is not step.state:
            record = self._record(
                payload, PreparationOutcome.SUPERSEDED, "changed_after_decision", run_id
            )
            await self.outcomes.reject(
                context,
                decisions=[],
                record=record,
                audits=[record_audit(context, self.ids, self.clock, record)],
            )
            return PreparationOutcome.SUPERSEDED.value
        return await self._approve(context, payload, step, case, typed_input, comment, run_id)

    async def _reject(
        self,
        context: AccessContext,
        payload: Mapping[str, Any],
        comment: str,
        run_id: uuid.UUID | None,
    ) -> str:
        reason = (comment.strip() or "Không duyệt đề xuất của AI")[:_REJECTION_REASON_MAX]
        decisions: list[NewDraftDecision] = []
        audits: list[AuditEvent] = []
        for named in payload.get("drafts") or []:
            draft = await self.drafts.get(context, uuid.UUID(str(named["draft_id"])))
            if draft is None or draft.status is not DraftStatus.OPEN:
                continue
            decision = NewDraftDecision(
                id=self.ids.new_uuid(),
                draft_id=draft.id,
                decision=DraftDecision.REJECTED,
                reason=reason,
            )
            decisions.append(decision)
            audits.append(
                _audit(
                    context,
                    self.ids,
                    self.clock,
                    DRAFT_REJECTED,
                    "document_draft",
                    draft.id,
                    {"lineage_id": str(draft.lineage_id), "version": draft.version},
                )
            )
        record = self._record(payload, PreparationOutcome.REJECTED, reason, run_id)
        audits.append(record_audit(context, self.ids, self.clock, record))
        await self.outcomes.reject(context, decisions=decisions, record=record, audits=audits)
        return PreparationOutcome.REJECTED.value

    async def _approve(
        self,
        context: AccessContext,
        payload: Mapping[str, Any],
        step: PreparedStep,
        case: ProductDevelopmentCase,
        typed_input: Mapping[str, str],
        comment: str,
        run_id: uuid.UUID | None,
    ) -> str:
        # The step the person chose (one of a physical step's outcomes), or
        # the step's own; a choice it does not offer is refused here too.
        action = step.outcome_for(typed_input)
        chosen_paper = ACTION_DOCUMENT_TYPE.get(action)
        # Papers of the outcomes NOT chosen are closed, not confirmed; the
        # record that carries the result is confirmed whatever was chosen.
        unchosen = {
            ACTION_DOCUMENT_TYPE[other]
            for other in step.outcome_actions
            if other is not action and other in ACTION_DOCUMENT_TYPE
        } - {chosen_paper, step.action_document}
        new_drafts: list[NewDocumentDraft] = []
        confirmations: list[NewDraftDecision] = []
        documents: list[AiPreparedDocument] = []
        audits: list[AuditEvent] = []
        paper: CaseDocument | None = None
        bm04_fields: Mapping[str, Any] | None = None
        for named in payload.get("drafts") or []:
            draft = await self.drafts.get(context, uuid.UUID(str(named["draft_id"])))
            if draft is None:
                raise NotFoundError("draft not found", details={"draft_id": named["draft_id"]})
            if draft.doc_type in unchosen:
                confirmations.append(
                    NewDraftDecision(
                        id=self.ids.new_uuid(),
                        draft_id=draft.id,
                        decision=DraftDecision.REJECTED,
                        reason="Không dùng: người duyệt chọn kết luận khác",
                    )
                )
                continue
            template = await self.templates.resolve(
                context, draft.template_id, draft.template_version
            )
            confirmed, new_version = self._confirmed_version(
                context, step, draft, template.spec, typed_input
            )
            if new_version is not None:
                new_drafts.append(new_version)
            confirmations.append(
                NewDraftDecision(
                    id=self.ids.new_uuid(),
                    draft_id=confirmed[0],
                    decision=DraftDecision.CONFIRMED,
                    reason=None,
                )
            )
            rendered = self.renderer.render(
                template, template_values(confirmed[1], hide_prices=False)
            )
            document_id = self.ids.new_uuid()
            key = ObjectKey.build(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                case_kind=CaseKind.PRODUCT,
                case_id=case.id.value,
                document_id=document_id,
            ).value
            sha = hashlib.sha256(rendered.data).hexdigest()
            # Outside the transaction, as an upload is: a row that then fails
            # leaves an object the orphan sweep removes.
            await self.storage.put(key, rendered.data, rendered.content_type)
            new_document = NewCaseDocument(
                id=CaseDocumentId(document_id),
                case_kind=CaseKind.PRODUCT,
                case_id=case.id.value,
                doc_type=draft.doc_type,
                object_key=key,
                filename=f"{template.spec.title}.{rendered.extension}",
                content_type=rendered.content_type,
                size_bytes=len(rendered.data),
                sha256=sha,
            )
            documents.append(AiPreparedDocument(document=new_document, draft_id=confirmed[0]))
            audits.append(
                _audit(
                    context,
                    self.ids,
                    self.clock,
                    DRAFT_CONFIRMED,
                    "document_draft",
                    confirmed[0],
                    {"lineage_id": str(draft.lineage_id), "document_id": str(document_id)},
                )
            )
            audits.append(
                _audit(
                    context,
                    self.ids,
                    self.clock,
                    AI_DOCUMENT_ADDED,
                    "case_document",
                    document_id,
                    {
                        "case_kind": CaseKind.PRODUCT.value,
                        "case_id": str(case.id),
                        "doc_type": draft.doc_type.value,
                        "draft_id": str(confirmed[0]),
                        "sha256": sha,
                    },
                )
            )
            if draft.doc_type is DocumentType.PRODUCT_PROFILE_BM04:
                bm04_fields = confirmed[1]
            if draft.doc_type == chosen_paper:
                paper = CaseDocument(
                    id=new_document.id,
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    case_kind=CaseKind.PRODUCT,
                    case_id=case.id.value,
                    doc_type=draft.doc_type,
                    object_key=key,
                    filename=new_document.filename,
                    content_type=new_document.content_type,
                    size_bytes=new_document.size_bytes,
                    sha256=sha,
                    version=1,
                    uploaded_by=context.principal_id,
                    uploaded_at=self.clock.now(),
                )
        source_paper = payload.get("action_document_id")
        if paper is None and source_paper and action is step.action:
            paper = await self.documents.get(context, CaseDocumentId(uuid.UUID(str(source_paper))))
        before = case.state
        apply_product_action(
            case,
            action=action,
            given=ProductActionInput(
                actor_id=context.principal_id,
                document=paper,
                # An outcome that needs a reason takes the decider's comment.
                reason=comment.strip() or None if action in OUTCOME_REASON_ACTIONS else None,
            ),
        )
        audits.insert(
            0,
            product_case_audit(
                context,
                self.ids,
                self.clock,
                case,
                action.value,
                {
                    "from_state": before.value,
                    "to_state": case.state.value,
                    "sample_round": case.sample_round,
                    "via": PROPOSAL_APPLIED,
                    "run_id": None if run_id is None else str(run_id),
                    **({"document_id": str(paper.id)} if paper else {}),
                },
            ),
        )
        record = self._record(payload, PreparationOutcome.APPLIED, None, run_id)
        audits.append(record_audit(context, self.ids, self.clock, record))
        profile = await self._profile(context, case, bm04_fields, audits)
        # `confirmations` carries the papers of outcomes not chosen too, each
        # a REJECTED decision: every draft the proposal named is closed.
        await self.outcomes.apply_approved(
            context,
            case=case,
            new_drafts=new_drafts,
            confirmations=confirmations,
            documents=documents,
            record=record,
            audits=audits,
            profile=profile,
        )
        return action.value

    async def _profile(
        self,
        context: AccessContext,
        case: ProductDevelopmentCase,
        fields: Mapping[str, Any] | None,
        audits: list[AuditEvent],
    ) -> NewProductProfile | None:
        """The profile version a confirmed BM04 makes, with its audit; what
        the tenant's schema refused is named there, never guessed."""
        if fields is None or self.profiles is None:
            return None
        made = await self.profiles.profile_for(context, case.id.value, fields)
        if made.profile is None:
            audits.append(
                _audit(
                    context,
                    self.ids,
                    self.clock,
                    PROFILE_NOT_SAVED,
                    "product_profile",
                    case.id.value,
                    {"product_dev_case_id": str(case.id), "missing": list(made.skipped)},
                )
            )
            return None
        audits.append(
            _audit(
                context,
                self.ids,
                self.clock,
                PROFILE_SAVED,
                "product_profile",
                made.profile.id,
                {
                    "product_dev_case_id": str(case.id),
                    "schema_version": made.profile.schema_version,
                    "prices_set": made.prices_set,
                    "via": PROPOSAL_APPLIED,
                    "skipped": list(made.skipped),
                },
            )
        )
        return made.profile

    def _confirmed_version(
        self,
        context: AccessContext,
        step: PreparedStep,
        draft: DocumentDraft,
        spec: DocTemplateSpec,
        typed_input: Mapping[str, str],
    ) -> tuple[tuple[uuid.UUID, dict[str, Any]], NewDocumentDraft | None]:
        """The version confirmed and its fields: the bound one, or for the
        record of a physical step, its next version holding the typed result
        (by the decider)."""
        if not (step.physical and draft.doc_type == step.action_document):
            return (draft.id, dict(draft.fields)), None
        result = check_result(spec, step.result_fields, typed_input)
        typed_by = {"edited_by": str(context.principal_id)}
        fields = dict(draft.fields)
        for name, value in result.items():
            # The chosen outcome prints in words, as the record reads it.
            if name == step.outcome_field and value in step.outcomes:
                value = step.outcomes[value].label
            fields[name] = {"value": value, "source": typed_by}
        new_id = self.ids.new_uuid()
        return (new_id, fields), NewDocumentDraft(
            id=new_id,
            lineage_id=draft.lineage_id,
            version=draft.version + 1,
            case_kind=draft.case_kind,
            case_id=draft.case_id,
            doc_type=draft.doc_type,
            template_id=draft.template_id,
            template_version=draft.template_version,
            prompt_id=draft.prompt_id,
            prompt_version=draft.prompt_version,
            fields=fields,
            gaps=compute_gaps(spec, fields),
            sources=[s.as_json() for s in draft.sources],
            content_sha256=content_sha256(draft.doc_type, spec.ref, fields),
        )


# ------------------------------------------------------------------ page ---


class PendingProposalPort(Protocol):
    """The case's pending proposal, as the platform's inbox serves it to the
    caller (narrowed to who may see it: ADR 0004 amendment)."""

    async def pending_by_payload(
        self, context: AccessContext, *, approval_type: str, key: str, value: str
    ) -> PendingApprovalRecord | None: ...


@dataclass(frozen=True, slots=True)
class ResultField:
    name: str
    label: str
    kind: str
    # AI's reading, shown beside the empty field; never its value.
    suggestion: Mapping[str, str] | None
    # The outcomes a person chooses from, `(choice, words)`; empty for a
    # field typed freely.
    choices: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class StepProposalReading:
    """What the case page's "AI đã chuẩn bị" block shows."""

    prepared: bool
    action: str | None
    record: PreparationRecord | None
    approval: PendingApprovalRecord | None
    stale: bool
    can_decide: bool
    result_fields: tuple[ResultField, ...] = ()


async def _proposal_for(
    cases: CaseHistoryPort,
    policy_override_repo: PolicyOverridePort,
    platform_default: SupplyChainStepPreparation,
    context: AccessContext,
    case_id: ProductDevelopmentCaseId,
) -> tuple[ProductDevelopmentCase, SupplyChainStepPreparation, PreparedStep | None]:
    case = await cases.get(context, case_id)
    if case is None or case.workspace_id.value != context.workspace_id:
        raise NotFoundError("product case not found", details={"case_id": str(case_id)})
    policy = await resolve_step_preparation(context, policy_override_repo, platform_default)
    return case, policy, policy.step_for(CaseKind.PRODUCT, case.state)


@dataclass(frozen=True)
class GetStepProposal:
    cases: CaseHistoryPort
    records: PreparationRecordsPort
    approvals: PendingProposalPort
    subject: ProposalSubjectPort
    templates: DraftTemplatesPort
    policy_override_repo: PolicyOverridePort
    platform_default_policy: SupplyChainStepPreparation
    authz: AuthorizationPort

    async def handle(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> StepProposalReading:
        await self.authz.require(
            context=context,
            action=PRODUCT_CASE_READ,
            resource_type="product_dev_case",
            resource_id=str(case_id),
        )
        case, policy, step = await _proposal_for(
            self.cases, self.policy_override_repo, self.platform_default_policy, context, case_id
        )
        if step is None:
            return StepProposalReading(
                prepared=False,
                action=None,
                record=None,
                approval=None,
                stale=False,
                can_decide=False,
            )
        entered = await entered_current_state(self.cases, context, case)
        record = (
            None
            if entered is None or entered.id is None
            else await self.records.latest(context, entered.id, policy.policy_version)
        )
        approval = await self.approvals.pending_by_payload(
            context,
            approval_type=proposal_type(step.action),
            key=PROPOSAL_CASE_KEY,
            value=str(case.id),
        )
        stale = False
        can_decide = False
        fields: tuple[ResultField, ...] = ()
        if approval is not None:
            current = await self.subject.current(context, approval.payload)
            stale = current != approval.payload.get(SUBJECT_VERSION_KEY)
            can_decide = await allows(
                self.authz, context, APPROVALS_DECIDE, "approval_request"
            ) and holds_stamped_scope(context, approval.required_scope)
            fields = await self._result_fields(context, step, approval.payload)
        return StepProposalReading(
            prepared=True,
            action=step.action.value,
            record=record,
            approval=approval,
            stale=stale,
            can_decide=can_decide,
            result_fields=fields,
        )

    async def _result_fields(
        self, context: AccessContext, step: PreparedStep, payload: Mapping[str, Any]
    ) -> tuple[ResultField, ...]:
        paper = step.action_document
        if not step.physical or paper is None:
            return ()
        template = await self.templates.resolve(context, *DRAFT_TEMPLATES[paper])
        raw = payload.get("suggestions")
        suggestions = raw if isinstance(raw, Mapping) else {}
        out: list[ResultField] = []
        for name in step.result_fields:
            spec_field = template.spec.field(name)
            suggestion = suggestions.get(name)
            out.append(
                ResultField(
                    name=name,
                    label=name if spec_field is None else spec_field.label,
                    kind="text" if spec_field is None else spec_field.kind.value,
                    suggestion=suggestion if isinstance(suggestion, Mapping) else None,
                    choices=tuple((k, o.label) for k, o in step.outcomes.items())
                    if name == step.outcome_field
                    else (),
                )
            )
        return tuple(out)


class StepDecisionPort(Protocol):
    """The platform's decision on an approval (`ApproveAndResumeService.
    decide` with the deployment's authorization), as the caller."""

    async def decide(
        self,
        context: AccessContext,
        *,
        approval_id: uuid.UUID,
        approve: bool,
        comment: str,
        typed_input: Mapping[str, str] | None,
    ) -> None: ...


@dataclass(frozen=True)
class DecideStepProposal:
    """The web decision on a proposal, typed result and all. Who may decide
    is the platform's to check (`approvals.decide`, the stamped duty scope,
    the comment a strict type needs, the subject still current); this checks
    that the approval is this case's proposal and that a result is of its
    template's kinds, so a bad value is refused before anything is decided."""

    cases: CaseHistoryPort
    approvals: PendingProposalPort
    templates: DraftTemplatesPort
    decisions: StepDecisionPort
    policy_override_repo: PolicyOverridePort
    platform_default_policy: SupplyChainStepPreparation

    async def handle(
        self,
        context: AccessContext,
        case_id: ProductDevelopmentCaseId,
        *,
        approval_id: uuid.UUID,
        approve: bool,
        comment: str,
        result: Mapping[str, Any],
    ) -> None:
        case, _, step = await _proposal_for(
            self.cases, self.policy_override_repo, self.platform_default_policy, context, case_id
        )
        approval = (
            None
            if step is None
            else await self.approvals.pending_by_payload(
                context,
                approval_type=proposal_type(step.action),
                key=PROPOSAL_CASE_KEY,
                value=str(case.id),
            )
        )
        if step is None or approval is None or approval.id != approval_id:
            raise NotFoundError("no pending proposal by that id on this case")
        stamped = PreparedStep.model_validate(approval.payload["step"])
        typed: dict[str, str] | None = None
        if approve and stamped.physical and stamped.action_document is not None:
            template = await self.templates.resolve(
                context, *DRAFT_TEMPLATES[stamped.action_document]
            )
            typed = check_result(template.spec, stamped.result_fields, result)
            # A choice the step does not offer is refused before deciding.
            stamped.outcome_for(typed)
        elif result:
            raise DomainError("bước này không nhận kết quả nhập tay", details={})
        await self.decisions.decide(
            context,
            approval_id=approval_id,
            approve=approve,
            comment=comment,
            typed_input=typed,
        )


# ---------------------------------------------------------------- policy ---


@dataclass(frozen=True)
class GetStepPreparationPolicy:
    policy_override_repo: PolicyOverridePort
    platform_default_policy: SupplyChainStepPreparation
    authz: AuthorizationPort

    async def handle(self, context: AccessContext) -> SupplyChainStepPreparation:
        await self.authz.require(
            context=context, action=ACTION_DUTIES_READ, resource_type=_POLICY_RESOURCE
        )
        return await resolve_step_preparation(
            context, self.policy_override_repo, self.platform_default_policy
        )


@dataclass(frozen=True)
class SetStepPreparationPolicyOverride:
    policy_override_repo: PolicyOverridePort
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(self, context: AccessContext, policy: SupplyChainStepPreparation) -> None:
        await self.authz.require(
            context=context, action=ACTION_DUTIES_WRITE, resource_type=_POLICY_RESOURCE
        )
        await _put_policy_override(
            context,
            self.policy_override_repo,
            policy_id=STEP_PREPARATION_POLICY_ID,
            policy=policy,
            resource_type=_POLICY_RESOURCE,
            ids=self.ids,
            clock=self.clock,
        )


__all__ = [
    "AiPreparedDocument",
    "ApplyStepProposal",
    "DecideStepProposal",
    "GetStepPreparationPolicy",
    "GetStepProposal",
    "ProposalOutcomesPort",
    "ProposalSubjectPort",
    "ResultField",
    "SetStepPreparationPolicyOverride",
    "StepDecisionPort",
    "StepProposalReading",
    "StepProposalSubject",
    "check_result",
]
