"""The read-only case assistant (ticket ai-automation/19): a question about
ONE case, answered from that case's own records with citations, on the portal
(the case page) and in a linked chat (Zalo, after the case query opened one
case).

What each part decides, and where:

- **Who and which case.** The read of the case's kind (`po_case.read`,
  `product_case.read`) before anything is read; the case under the caller's
  tenant AND workspace (RLS), and checked again here: another's case is not
  found, never forbidden.
- **What may be cited** (`domain.case_answer`): the case, its history, its
  BM04 and sample measurements (product), and the documents' readings only for
  a holder of `supply_chain.document.read`. Prices only on the portal and only
  for a holder of `supply_chain.commercial.read`; never in a chat, whatever the
  asker holds. An answer is never broader than the asker.
- **One model call** (`answer_case_question@1.0.0`, task `draft.case_answer`),
  the question and the evidence as the prompt's one untrusted variable; the
  answer is schema-validated and grounded by code (`ground_sentences`).
- **Nothing kept, or a model answer that fits no schema, reads "không đủ bằng
  chứng"**; a provider or budget failure is raised as what it is.
- Nothing is written: no row, no step, no message.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.ports import ModelGateway, ModelOutputInvalidError
from dw_kernel.errors import DomainError, NotFoundError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.pagination import PageQuery, page_request
from dw_kernel.ports import IdGenerator
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import AuthorizationPort
from dw_supply_chain.application.bm04_prefill import Bm04ProfileReadPort
from dw_supply_chain.application.commercial import allows
from dw_supply_chain.application.handlers import (
    COMMERCIAL_READ,
    DOCUMENT_READ,
    PO_CASE_READ,
    PRODUCT_CASE_READ,
    WORKER_VERSION,
)
from dw_supply_chain.application.ports import POCaseRepositoryPort, ProductCaseRepositoryPort
from dw_supply_chain.application.step_preparation import (
    CaseDocumentListPort,
    ExtractionReadingsPort,
    SamplePreparation,
)
from dw_supply_chain.domain.case_answer import (
    MAX_QUESTION,
    NOT_ENOUGH_EVIDENCE,
    CaseAnswerWriting,
    criterion_items,
    history_items,
    po_case_item,
    product_case_item,
    profile_item,
    reading_item,
)
from dw_supply_chain.domain.case_document import CaseKind
from dw_supply_chain.domain.extraction import ExtractionStatus
from dw_supply_chain.domain.grounded_writing import EvidenceItem, KeptSentence, ground_sentences
from dw_supply_chain.domain.po_case import POCaseId
from dw_supply_chain.domain.product_development_case import ProductDevelopmentCaseId
from dw_supply_chain.workflows.grounded_writing import WritingPrompt, write_with_evidence

ANSWER_PROMPT = WritingPrompt("supply_chain.answer_case_question", "1.0.0")
_WORKER_ID = "supply_chain.case_assistant"
# How much of a case the model is shown: its newest history and documents.
_HISTORY = 30
_DOCUMENTS = 20


class AnswerChannel(StrEnum):
    WEB = "web"
    ZALO = "zalo"


@dataclass(frozen=True, slots=True)
class CitedAnswer:
    """The kept sentences, each with the labels of what it cites; `answered`
    False is "không đủ bằng chứng" (the text says so)."""

    answered: bool
    text: str
    sentences: tuple[KeptSentence, ...]
    # Every item the model was shown, by key: what a citation names.
    sources: dict[str, str]
    dropped: int


@dataclass(frozen=True)
class AskAboutCase:
    po_cases: POCaseRepositoryPort
    product_cases: ProductCaseRepositoryPort
    documents: CaseDocumentListPort
    readings: ExtractionReadingsPort
    profiles: Bm04ProfileReadPort
    sample: SamplePreparation
    gateway: ModelGateway
    authz: AuthorizationPort
    ids: IdGenerator
    model_profile: str | None = None

    async def handle(
        self,
        context: AccessContext,
        *,
        case_kind: CaseKind,
        case_id: uuid.UUID,
        question: str,
        channel: AnswerChannel,
    ) -> CitedAnswer:
        read = PO_CASE_READ if case_kind is CaseKind.PO else PRODUCT_CASE_READ
        await self.authz.require(
            context=context, action=read, resource_type=case_kind.value, resource_id=str(case_id)
        )
        text = question.strip()
        if not text or len(text) > MAX_QUESTION or "<input" in text.lower():
            raise DomainError(
                f"câu hỏi từ 1 tới {MAX_QUESTION} ký tự", details={"field": "question"}
            )
        prices = channel is AnswerChannel.WEB and await allows(
            self.authz, context, COMMERCIAL_READ, case_kind.value
        )
        documents = await allows(self.authz, context, DOCUMENT_READ, case_kind.value)
        evidence = await self._evidence(
            context, case_kind, case_id, prices=prices, documents=documents
        )
        try:
            writing = await self._write(context, case_kind, case_id, text, evidence, channel)
        except ModelOutputInvalidError:
            writing = CaseAnswerWriting()
        grounded = ground_sentences(writing.sentences, evidence)
        sources = {item.key: item.label for item in evidence}
        if not grounded.kept:
            return CitedAnswer(False, NOT_ENOUGH_EVIDENCE, (), sources, len(grounded.dropped))
        return CitedAnswer(
            True,
            " ".join(s.text for s in grounded.kept),
            grounded.kept,
            sources,
            len(grounded.dropped),
        )

    async def _evidence(
        self,
        context: AccessContext,
        case_kind: CaseKind,
        case_id: uuid.UUID,
        *,
        prices: bool,
        documents: bool,
    ) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        if case_kind is CaseKind.PO:
            po = await self.po_cases.get(context, POCaseId(case_id))
            if po is None or (po.tenant_id.value, po.workspace_id.value) != (
                context.tenant_id,
                context.workspace_id,
            ):
                raise NotFoundError("PO case not found", details={"case_id": str(case_id)})
            items.append(po_case_item(po))
            page = await self.po_cases.list_transitions(
                context,
                po.id,
                page_request(
                    limit=_HISTORY,
                    cursor=None,
                    query=PageQuery(
                        key="supply_chain.po_case_transitions", filters={"case": po.id}
                    ),
                ),
            )
            items += history_items(page.items)
        else:
            product = await self.product_cases.get(context, ProductDevelopmentCaseId(case_id))
            if product is None or (product.tenant_id, product.workspace_id) != (
                TenantId(context.tenant_id),
                WorkspaceId(context.workspace_id),
            ):
                raise NotFoundError("product case not found", details={"case_id": str(case_id)})
            items.append(product_case_item(product))
            product_page = await self.product_cases.list_transitions(
                context,
                product.id,
                page_request(
                    limit=_HISTORY,
                    cursor=None,
                    query=PageQuery(
                        key="supply_chain.product_case_transitions", filters={"case": product.id}
                    ),
                ),
            )
            items += history_items(product_page.items)
            profile = await self.profiles.latest(context, case_id)
            if profile is not None:
                items.append(profile_item(profile, prices=prices))
            items += criterion_items(await self.sample.results(context, product))
        if documents:
            items += await self._readings(context, case_kind, case_id, prices=prices)
        return items

    async def _readings(
        self, context: AccessContext, case_kind: CaseKind, case_id: uuid.UUID, *, prices: bool
    ) -> list[EvidenceItem]:
        mine = [
            d
            for d in await self.documents.list_for_case(context, case_kind, case_id)
            if d.case_id == case_id
            and (d.tenant_id, d.workspace_id) == (context.tenant_id, context.workspace_id)
        ]
        newest = sorted(mine, key=lambda d: d.uploaded_at, reverse=True)[:_DOCUMENTS]
        by_id = {d.id.value: d for d in newest}
        out: list[EvidenceItem] = []
        seen: set[uuid.UUID] = set()
        for reading in await self.readings.readings(context, list(by_id)):
            document = by_id.get(reading.document_id)
            if (
                document is None
                or reading.status is not ExtractionStatus.EXTRACTED
                or reading.sha256 != document.sha256
                or reading.document_id in seen
            ):
                continue
            seen.add(reading.document_id)
            item = reading_item(document, reading.fields, prices=prices)
            if item is not None:
                out.append(item)
        return out

    async def _write(
        self,
        context: AccessContext,
        case_kind: CaseKind,
        case_id: uuid.UUID,
        question: str,
        evidence: Sequence[EvidenceItem],
        channel: AnswerChannel,
    ) -> CaseAnswerWriting:
        run_id = self.ids.new_uuid()
        return await write_with_evidence(
            self.gateway,
            RunContext(
                run_id=run_id,
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                actor_id=context.principal_id,
                worker_id=_WORKER_ID,
                worker_version=WORKER_VERSION,
                channel=channel.value,
                plan_id=context.plan_id,
                roles=context.roles,
                scopes=context.scopes,
                trace_id=str(run_id),
                subject_ref=f"{case_kind.value}_case:{case_id}",
            ),
            ANSWER_PROMPT,
            CaseAnswerWriting,
            task={"purpose": "case_answer", "question": question},
            evidence=evidence,
            model_profile=self.model_profile,
        )


__all__ = ["ANSWER_PROMPT", "AnswerChannel", "AskAboutCase", "CitedAnswer"]
