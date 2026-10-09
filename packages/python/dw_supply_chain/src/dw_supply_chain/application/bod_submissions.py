"""The tờ trình BGĐ: AI drafts it before BGĐ's review is raised (step 6;
ticket ai-automation/10).

`PrepareBodSubmission` is what `EnsureProductApproval` asks, in the worker's
reconcile lane, before it raises BGĐ's review of a passed sample, so the
approval names the draft and BGĐ is told only after it exists. What it does,
and what each step refuses:

1. **Once per round.** An open `bod_submission` draft prepared since the
   round opened is reused: no second call.
2. **The case's own evidence, read under the caller's tenant and workspace**:
   the case's facts, its history (each row a key the model may cite), the
   approved sample evaluation record of this round (its criteria, conclusion
   and notes), and the supplier's quotation as the extraction lane read it
   with every price left out. A case not in the caller's workspace drafts
   nothing and calls nothing.
3. **One structured call** (`draft_bod_submission@1.0.0`, one-call gateway):
   the model writes the summary, the risks and the recommendation, each
   sentence citing the evidence it rests on; code keeps the sentences that
   check out (`domain.grounded_writing`), so a sentence citing another case's
   paper (a key this evidence does not hold) is dropped.
4. **Code fills the facts**: code, name, Category, supplier, the evaluation's
   result as the approved record states it, and the quotation's unit price,
   currency and MOQ with their quotes. A price is a field of the draft, so a
   reader without `supply_chain.commercial.read` sees it redacted, in the
   reading and in the preview alike.

A failed call, a spent day or no plan is no tờ trình: the review is raised all
the same and says "chưa có tờ trình". Nothing here decides or moves the case.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.model.budget import BudgetExceededError
from dw_agent_runtime.ports import ModelGateway, ModelOutputInvalidError
from dw_kernel.errors import InfrastructureError, QuotaExceededError
from dw_kernel.pagination import PageQuery, page_request
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import PolicyOverridePort
from dw_supply_chain.application.document_drafts import FieldInput, PrepareDocumentDraft
from dw_supply_chain.application.ports import TenantPlanPort
from dw_supply_chain.application.step_preparation import (
    CaseDocumentListPort,
    CaseDraftsPort,
    CaseHistoryPort,
    ExtractionReadingsPort,
    resolve_step_preparation,
)
from dw_supply_chain.domain.case_document import CaseKind, DocumentType
from dw_supply_chain.domain.commercial import PRICE_FIELDS
from dw_supply_chain.domain.document_draft import DocumentDraft, DraftStatus, field_value
from dw_supply_chain.domain.extraction import EXTRACTION_SPECS, ExtractionStatus
from dw_supply_chain.domain.grounded_writing import (
    CitedSentence,
    EvidenceItem,
    KeptSentence,
    ground_sentences,
)
from dw_supply_chain.domain.product_development_case import (
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
)
from dw_supply_chain.step_preparation_policy import SupplyChainStepPreparation
from dw_supply_chain.workflows.grounded_writing import WritingPrompt, write_with_evidence

logger = logging.getLogger(__name__)

SUBMISSION_PROMPT = WritingPrompt("supply_chain.draft_bod_submission", "1.0.0")
# The history rows the model is shown, newest first.
_HISTORY = 30
# What the model is never shown of a quotation: prices and the lines that carry them.
_HIDDEN = PRICE_FIELDS | {"total", "lines"}


class BodSubmissionWriting(BaseModel):
    """The model's words for the tờ trình, as it claims them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: list[CitedSentence] = Field(default_factory=list, max_length=5)
    risks: list[CitedSentence] = Field(default_factory=list, max_length=5)
    recommendation: list[CitedSentence] = Field(default_factory=list, max_length=2)


@dataclass(frozen=True, slots=True)
class SubmissionRef:
    draft_id: uuid.UUID
    content_sha256: str
    gaps: tuple[str, ...]

    def as_json(self) -> dict[str, Any]:
        return {
            "draft_id": str(self.draft_id),
            "content_sha256": self.content_sha256,
            "gaps": list(self.gaps),
        }


def _joined(kept: Sequence[KeptSentence], prefix: str = "") -> FieldInput | None:
    if not kept:
        return None
    return FieldInput(
        value=prefix + " ".join(k.text for k in kept),
        cites=tuple(dict.fromkeys(c for k in kept for c in k.cites)),
    )


@dataclass(frozen=True)
class PrepareBodSubmission:
    cases: CaseHistoryPort
    documents: CaseDocumentListPort
    readings: ExtractionReadingsPort
    drafts: CaseDraftsPort
    prepare_draft: PrepareDocumentDraft
    plans: TenantPlanPort
    gateway: ModelGateway
    ids: IdGenerator
    clock: UtcClock
    worker_id: str
    worker_version: str
    # Whether the tenant asked for a tờ trình at all: its step preparation
    # policy (`bod_submission`); the platform drafts none.
    policy_override_repo: PolicyOverridePort
    platform_default_policy: SupplyChainStepPreparation
    model_profile: str | None = None

    async def prepare(
        self, context: AccessContext, case: ProductDevelopmentCase
    ) -> SubmissionRef | None:
        policy = await resolve_step_preparation(
            context, self.policy_override_repo, self.platform_default_policy
        )
        if not policy.bod_submission:
            return None
        current = await self.cases.get(context, ProductDevelopmentCaseId(case.id.value))
        if current is None or (current.tenant_id.value, current.workspace_id.value) != (
            context.tenant_id,
            context.workspace_id,
        ):
            return None
        drafts = await self.drafts.latest_for_case(context, CaseKind.PRODUCT, case.id.value)
        reusable = [
            d
            for d in drafts
            if d.doc_type is DocumentType.BOD_SUBMISSION
            and d.status is DraftStatus.OPEN
            and (current.round_opened_at is None or d.created_at >= current.round_opened_at)
        ]
        if reusable:
            newest = max(reusable, key=lambda d: d.created_at)
            return SubmissionRef(newest.id, newest.content_sha256, newest.gaps)
        plan = await self.plans.plan_of(context.tenant_id)
        if plan is None:
            return None
        evidence, facts = await self._evidence(context, current, drafts)
        writing = await self._write(context, current, plan, evidence)
        if writing is None:
            return None
        values = dict(facts)
        summary = ground_sentences(writing.summary, evidence).kept
        risks = ground_sentences(writing.risks, evidence).kept
        recommendation = ground_sentences(writing.recommendation, evidence).kept
        for name, field in (
            ("summary", _joined([*summary, *risks])),
            ("recommendation", _joined(recommendation)),
        ):
            if field is not None:
                values[name] = field
        values["submitted_on"] = FieldInput(value=self.clock.now().date().isoformat())
        draft = await self.prepare_draft.handle(
            context,
            case_kind=CaseKind.PRODUCT,
            case_id=current.id.value,
            doc_type=DocumentType.BOD_SUBMISSION,
            values=values,
            prompt=SUBMISSION_PROMPT.ref,
        )
        return SubmissionRef(draft.id, draft.content_sha256, draft.gaps)

    async def _write(
        self,
        context: AccessContext,
        case: ProductDevelopmentCase,
        plan: str,
        evidence: Sequence[EvidenceItem],
    ) -> BodSubmissionWriting | None:
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
                    subject_ref=f"product_dev_case:{case.id}",
                ),
                SUBMISSION_PROMPT,
                BodSubmissionWriting,
                task={"purpose": "bod_submission"},
                evidence=evidence,
                model_profile=self.model_profile,
            )
        except (
            QuotaExceededError,
            ModelOutputInvalidError,
            InfrastructureError,
            BudgetExceededError,
        ) as exc:
            logger.warning("bod submission: no model writing (%s)", type(exc).__name__)
            return None

    async def _evidence(
        self,
        context: AccessContext,
        case: ProductDevelopmentCase,
        drafts: Sequence[DocumentDraft],
    ) -> tuple[list[EvidenceItem], dict[str, FieldInput]]:
        facts: dict[str, FieldInput] = {
            "proposal_code": FieldInput(value=case.proposal_code),
            "product_name": FieldInput(value=case.product_name),
            "category": FieldInput(value=case.category),
        }
        if case.supplier_name:
            facts["supplier_name"] = FieldInput(value=case.supplier_name)
        items = [
            EvidenceItem(
                "case",
                "Hồ sơ",
                f"Mã đề xuất: {case.proposal_code}; Sản phẩm: {case.product_name}; "
                f"Nhóm: {case.category}; NCC: {case.supplier_name or 'chưa có'}; "
                f"Vòng mẫu: {case.sample_round}",
            )
        ]
        page = await self.cases.list_transitions(
            context,
            case.id,
            page_request(
                limit=_HISTORY,
                cursor=None,
                query=PageQuery(
                    key="supply_chain.product_case_transitions", filters={"case": case.id}
                ),
            ),
        )
        for row in page.items:
            if row.id is None:
                continue
            reason = f"; lý do: {row.reason}" if row.reason else ""
            items.append(
                EvidenceItem(
                    f"history:{row.id}",
                    "Lịch sử hồ sơ",
                    f"{row.occurred_at.date().isoformat()}: {row.action.value} "
                    f"({row.from_state.value if row.from_state else '-'} -> "
                    f"{row.to_state.value}){reason}",
                )
            )
        record = self._approved_record(case, drafts)
        if record is not None:
            conclusion = field_value(record.fields, "conclusion")
            rows = field_value(record.fields, "criteria")
            notes = field_value(record.fields, "notes")
            lines = [
                f"{r.get('criterion')}: chuẩn {r.get('standard')}, đo {r.get('measured')}, "
                f"{r.get('result')}"
                for r in (rows if isinstance(rows, list) else [])
                if isinstance(r, Mapping)
            ]
            items.append(
                EvidenceItem(
                    f"draft:{record.id}",
                    "Biên bản đánh giá mẫu đã duyệt",
                    f"Kết luận: {conclusion}; "
                    + "; ".join(lines)
                    + (f"; {notes}" if notes else ""),
                )
            )
            if isinstance(conclusion, str):
                facts["evaluation_result"] = FieldInput(
                    value=f"{conclusion} (vòng {case.sample_round})"
                )
        await self._quotation(context, case, items, facts)
        return items, facts

    @staticmethod
    def _approved_record(
        case: ProductDevelopmentCase, drafts: Sequence[DocumentDraft]
    ) -> DocumentDraft | None:
        confirmed = [
            d
            for d in drafts
            if d.doc_type is DocumentType.SAMPLE_EVALUATION and d.status is DraftStatus.CONFIRMED
        ]
        return max(confirmed, key=lambda d: d.created_at) if confirmed else None

    async def _quotation(
        self,
        context: AccessContext,
        case: ProductDevelopmentCase,
        items: list[EvidenceItem],
        facts: dict[str, FieldInput],
    ) -> None:
        """The quotation as read: its facts without a price for the model; its
        unit price, currency and MOQ, with their quotes, for code to fill."""
        quotes = [
            d
            for d in await self.documents.list_for_case(context, CaseKind.PRODUCT, case.id.value)
            if d.doc_type is DocumentType.SUPPLIER_QUOTATION
        ]
        if not quotes:
            return
        document = max(quotes, key=lambda d: d.version)
        spec = EXTRACTION_SPECS[DocumentType.SUPPLIER_QUOTATION]
        reading = next(
            (
                r
                for r in await self.readings.readings(context, [document.id.value])
                if r.sha256 == document.sha256
                and (r.prompt_id, r.prompt_version) == (spec.prompt_id, spec.prompt_version)
                and r.status is ExtractionStatus.EXTRACTED
            ),
            None,
        )
        if reading is None:
            return
        shown = [
            f"{name}: {entry['value']}"
            for name, entry in reading.fields.items()
            if name not in _HIDDEN
            and isinstance(entry, Mapping)
            and isinstance(entry.get("value"), str)
        ]
        if shown:
            items.append(
                EvidenceItem(f"doc:{document.id}", "Báo giá của NCC (không giá)", "; ".join(shown))
            )
        lines = reading.fields.get("lines")
        first = (
            lines[0] if isinstance(lines, list) and lines and isinstance(lines[0], Mapping) else {}
        )
        for name, entry in (
            ("unit_price", first.get("unit_price")),
            ("currency", reading.fields.get("currency")),
            ("moq", reading.fields.get("moq")),
        ):
            if isinstance(entry, Mapping) and isinstance(entry.get("value"), str):
                facts[name] = FieldInput(
                    value=entry["value"],
                    document_id=document.id.value,
                    quote=entry.get("quote") if isinstance(entry.get("quote"), str) else None,
                )


__all__ = ["SUBMISSION_PROMPT", "BodSubmissionWriting", "PrepareBodSubmission", "SubmissionRef"]
