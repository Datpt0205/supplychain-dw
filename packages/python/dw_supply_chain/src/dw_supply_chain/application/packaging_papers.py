"""Step 12's papers prepared by code (ticket ai-automation/16; ADR 0028).

- **The lane** (`PreparePackagingPapers`, `supply_chain_packaging_papers`): for
  each workspace whose tenant's step preparation policy says `packaging`, each
  PO case in `pre_production` gets, once each:
    * MKT's skeletons (`packaging_content`, `user_manual`) from the product's
      latest BM04 once the colour is approved and the tenant's packaging policy
      requires the packaging content: what the BM04 says filled by code, what
      the label must carry and the BM04 does not say left a named gap for MKT;
    * a colour revision request for the newest colour sample while the colour
      is not approved (the BM04's colours beside an empty requirement: a
      colour is judged by eye, and a photo is not read);
    * a design revision request for the newest packaging design proof the
      extraction lane read while the design is not approved, one item per
      finding of the proof check, when there is any.
    * the pre-production test report (ticket ai-automation/17, item 2) once
      the sample is in, the test not passed and R&D has measured every
      criterion of this attempt: code's criteria table, and the notes the
      model wrote only where they check out against what they cite (the
      same prompt and grounding as a sample round's record, ticket
      ai-automation/09); without a model, or when it does not answer, the
      record carries the table alone. Drafted once per set of results.
  A request is drafted once per source document (its `sources` name it), and
  the holders of the step's duty are told; a rejected draft is a person's no.
  The model words only the test report's notes; reading the proof is the
  extraction lane's.
- **The proof check** (`GetPackagingProof`): the newest proof on the case, its
  reading, and what code finds against the label rules, the BM04 and the PO's
  SKUs (`domain.proof_check`). No price in any of it.

Approving the design stays Cung ứng's step (`approve_design`), sending a
revision back stays `request_*_revision` with its reason: a draft prepares
the paper, it never takes a step.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from dw_kernel.errors import NotFoundError
from dw_kernel.pagination import MAX_PAGE_SIZE, page_request
from dw_kernel.ports import UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import AuthorizationPort, PolicyOverridePort
from dw_platform.domain.audit import system_actor
from dw_supply_chain.action_duties import SupplyChainActionDuties
from dw_supply_chain.application.bm04_prefill import Bm04ProfileReadPort
from dw_supply_chain.application.document_drafts import FieldInput, PrepareDocumentDraft
from dw_supply_chain.application.handlers import (
    PO_CASE_READ,
    notify_duty_holders,
    po_case_link,
    resolve_action_duties,
)
from dw_supply_chain.application.po_steps import newest_reading, own_documents
from dw_supply_chain.application.ports import (
    PackagingDesignReaderPort,
    POCaseListFilter,
    ReviewNotifierPort,
    ScopeHoldersPort,
)
from dw_supply_chain.application.pre_production_test import (
    PreProductionResults,
    PreProductionTestSources,
    drafted_for,
    report_evidence,
    report_values,
)
from dw_supply_chain.application.production_gate import resolve_packaging_policy
from dw_supply_chain.application.purchase_orders import POCaseStorePort
from dw_supply_chain.application.step_preparation import (
    EVALUATION_PROMPT,
    CaseDocumentListPort,
    CaseDraftsPort,
    EvaluationWriterPort,
    ExtractionReadingsPort,
    WorkspacesWithCasesPort,
    resolve_step_preparation,
)
from dw_supply_chain.domain.case_document import CaseKind, DocumentType
from dw_supply_chain.domain.document_draft import DocumentDraft, DraftSource
from dw_supply_chain.domain.packaging_design import (
    PackagingAction,
    PackagingDesign,
    ReviewStatus,
)
from dw_supply_chain.domain.payment_check import SourceRead
from dw_supply_chain.domain.po_case import CaseState, POCase, POCaseId
from dw_supply_chain.domain.proof_check import proof_findings
from dw_supply_chain.domain.sample_evaluation import ground_evaluation
from dw_supply_chain.domain.step_proposal import Finding
from dw_supply_chain.packaging_policy import SupplyChainPackagingPolicy
from dw_supply_chain.step_preparation_policy import SupplyChainStepPreparation

logger = logging.getLogger(__name__)

PACKAGING_PAPERS_LANE = "supply_chain_packaging_papers"

# What a BM04 attribute fills in MKT's skeletons, by template field.
_FROM_BM04 = {
    "product_name": "product_name",
    "material": "material",
    "dimensions": "dimensions",
    "origin": "origin_country",
}


def packaging_lane_context(tenant_id: uuid.UUID, workspace_id: uuid.UUID) -> AccessContext:
    return AccessContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        principal_id=system_actor(PACKAGING_PAPERS_LANE).value,
        roles=frozenset(),
        scopes=frozenset(),
        plan_id="system",
    )


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


@dataclass(frozen=True)
class PackagingSources:
    """What step 12's papers are made from, under the caller's tenant and
    workspace: the product's BM04, the case's documents and their readings."""

    profiles: Bm04ProfileReadPort
    documents: CaseDocumentListPort
    readings: ExtractionReadingsPort

    async def attributes(self, context: AccessContext, case: POCase) -> Mapping[str, Any] | None:
        if case.product_dev_case_id is None:
            return None
        profile = await self.profiles.latest(context, case.product_dev_case_id)
        return None if profile is None else profile.attributes

    async def newest(
        self, context: AccessContext, case: POCase, doc_type: DocumentType
    ) -> SourceRead:
        documents = own_documents(
            context,
            await self.documents.list_for_case(context, CaseKind.PO, case.id.value),
            case.id.value,
        )
        return await newest_reading(self.readings, context, documents, doc_type)

    async def proof(self, context: AccessContext, case: POCase) -> tuple[SourceRead, list[Finding]]:
        proof = await self.newest(context, case, DocumentType.PACKAGING_DESIGN)
        attributes = await self.attributes(context, case)
        skus = [line.sku_code for line in case.lines if line.sku_code]
        return proof, proof_findings(proof, attributes, skus)


def skeleton_values(
    case: POCase, attributes: Mapping[str, Any] | None, doc_type: DocumentType
) -> dict[str, FieldInput]:
    """MKT's skeleton, every value code's: the BM04's own words and the PO's
    SKUs; what the BM04 does not say is left for MKT."""
    values: dict[str, FieldInput] = {}
    bm04 = attributes or {}
    for name, key in _FROM_BM04.items():
        if doc_type is DocumentType.USER_MANUAL and name == "origin":
            continue
        value = _text(bm04.get(key))
        if value is not None:
            values[name] = FieldInput(value=value)
    if doc_type is DocumentType.PACKAGING_CONTENT:
        if case.po_reference is not None:
            values["po_reference"] = FieldInput(value=case.po_reference)
        skus = ", ".join(line.sku_code for line in case.lines if line.sku_code)
        if skus:
            values["sku_codes"] = FieldInput(value=skus)
    return values


def colour_request_values(
    case: POCase, attributes: Mapping[str, Any] | None, today: date
) -> dict[str, FieldInput]:
    values = {
        "supplier_name": FieldInput(value=case.supplier_name),
        "requested_on": FieldInput(value=today.isoformat()),
    }
    if case.po_reference is not None:
        values["po_reference"] = FieldInput(value=case.po_reference)
    bm04 = attributes or {}
    for name, key in (("product_name", "product_name"), ("expected_colours", "colours")):
        value = _text(bm04.get(key))
        if value is not None:
            values[name] = FieldInput(value=value)
    return values


def design_request_values(
    case: POCase,
    attributes: Mapping[str, Any] | None,
    findings: Sequence[Finding],
    today: date,
) -> dict[str, FieldInput]:
    """One item per finding; the requirement is the BM04's own words where the
    BM04 has them, empty for a person otherwise."""
    bm04 = attributes or {}
    items = []
    for finding in findings:
        recorded = _text(bm04.get(_FROM_BM04.get(finding.subject, "")))
        items.append(
            {
                "subject": finding.subject,
                "finding": finding.message,
                "requirement": None if recorded is None else f"Theo BM04: {recorded}",
            }
        )
    values = colour_request_values(case, attributes, today)
    values.pop("expected_colours", None)
    values["items"] = FieldInput(value=items)
    return values


def _drafted_from(
    drafts: Sequence[DocumentDraft], doc_type: DocumentType, source: uuid.UUID
) -> bool:
    return any(
        d.doc_type is doc_type and any(s.document_id == source for s in d.sources) for d in drafts
    )


@dataclass(slots=True)
class PackagingPaperCount:
    drafted: int = 0
    failed_workspaces: int = 0


@dataclass(frozen=True)
class PreparePackagingPapers:
    """The worker lane `supply_chain_packaging_papers` (module docstring)."""

    workspaces: WorkspacesWithCasesPort
    cases: POCaseStorePort
    designs: PackagingDesignReaderPort
    sources: PackagingSources
    drafts: CaseDraftsPort
    prepare_draft: PrepareDocumentDraft
    policy_override_repo: PolicyOverridePort
    platform_default_policy: SupplyChainStepPreparation
    platform_default_packaging: SupplyChainPackagingPolicy
    platform_default_duties: SupplyChainActionDuties
    holders: ScopeHoldersPort
    notifier: ReviewNotifierPort
    clock: UtcClock
    # R&D's values of the pre-production test, and the model that words the
    # record's notes (None: the record carries code's table alone).
    test: PreProductionTestSources
    writer: EvaluationWriterPort | None = None

    async def run(self) -> PackagingPaperCount:
        count = PackagingPaperCount()
        for tenant_id, workspace_id in await self.workspaces.workspaces():
            context = packaging_lane_context(tenant_id, workspace_id)
            try:
                policy = await resolve_step_preparation(
                    context, self.policy_override_repo, self.platform_default_policy
                )
                if policy.packaging:
                    count.drafted += await self._workspace(context)
            except Exception:
                logger.exception(
                    "packaging papers failed for a workspace",
                    extra={"tenant_id": str(tenant_id), "workspace_id": str(workspace_id)},
                )
                count.failed_workspaces += 1
        return count

    async def _workspace(self, context: AccessContext) -> int:
        case_filter = POCaseListFilter(state=CaseState.PRE_PRODUCTION)
        cursor: str | None = None
        drafted = 0
        while True:
            page = await self.cases.list_page(
                context,
                page_request(
                    limit=MAX_PAGE_SIZE,
                    cursor=cursor,
                    query=case_filter.page_query(context.tenant_id),
                ),
                case_filter,
            )
            for listed in page.items:
                drafted += await self.prepare(context, listed.id)
            if page.next_cursor is None:
                return drafted
            cursor = page.next_cursor

    async def prepare(self, context: AccessContext, case_id: POCaseId) -> int:
        case = await self.cases.get(context, case_id)
        if case is None or case.state is not CaseState.PRE_PRODUCTION:
            return 0
        design = await self.designs.get(context, case.id.value) or PackagingDesign(
            po_case_id=case.id.value, tenant_id=case.tenant_id, workspace_id=case.workspace_id
        )
        packaging = await resolve_packaging_policy(
            context, self.policy_override_repo, self.platform_default_packaging
        )
        drafts = await self.drafts.latest_for_case(context, CaseKind.PO, case.id.value)
        attributes = await self.sources.attributes(context, case)
        today = self.clock.now().date()
        drafted = 0
        if packaging.require_packaging_content and design.colour_status is ReviewStatus.APPROVED:
            for doc_type in (DocumentType.PACKAGING_CONTENT, DocumentType.USER_MANUAL):
                if not any(d.doc_type is doc_type for d in drafts):
                    await self._draft(
                        context, case, doc_type, skeleton_values(case, attributes, doc_type), ()
                    )
                    drafted += 1
        if design.colour_status is not ReviewStatus.APPROVED:
            sample = await self.sources.newest(context, case, DocumentType.COLOUR_SAMPLE)
            if sample.document_id is not None and not _drafted_from(
                drafts, DocumentType.COLOUR_REVISION_REQUEST, sample.document_id
            ):
                await self._draft(
                    context,
                    case,
                    DocumentType.COLOUR_REVISION_REQUEST,
                    colour_request_values(case, attributes, today),
                    (DraftSource(sample.document_id, sample.sha256 or ""),),
                )
                drafted += 1
        if design.design_status is not ReviewStatus.APPROVED:
            proof, findings = await self.sources.proof(context, case)
            if (
                proof.extracted
                and proof.document_id is not None
                and findings
                and not _drafted_from(
                    drafts, DocumentType.DESIGN_REVISION_REQUEST, proof.document_id
                )
            ):
                await self._draft(
                    context,
                    case,
                    DocumentType.DESIGN_REVISION_REQUEST,
                    design_request_values(case, attributes, findings, today),
                    (DraftSource(proof.document_id, proof.sha256 or "", proof.extraction_id),),
                )
                await self._tell(context, case, PackagingAction.REQUEST_DESIGN_REVISION)
                drafted += 1
        if design.test_open:
            results = await self.test.results(context, case)
            if results.complete and not any(
                d.doc_type is DocumentType.PRE_PRODUCTION_TEST_REPORT and drafted_for(d, results)
                for d in drafts
            ):
                await self._test_report(context, case, attributes, results)
                drafted += 1
        return drafted

    async def _test_report(
        self,
        context: AccessContext,
        case: POCase,
        attributes: Mapping[str, Any] | None,
        results: PreProductionResults,
    ) -> None:
        notes: tuple[Any, ...] = ()
        if self.writer is not None:
            evidence = report_evidence(case, results)
            writing = await self.writer.write(context, case_id=case.id.value, evidence=evidence)
            if writing is not None:
                notes = ground_evaluation(writing, evidence, results.results).notes
        await self.prepare_draft.handle(
            context,
            case_kind=CaseKind.PO,
            case_id=case.id.value,
            doc_type=DocumentType.PRE_PRODUCTION_TEST_REPORT,
            values=report_values(case, results, attributes, notes),
            prompt=None
            if self.writer is None
            else (EVALUATION_PROMPT.prompt_id, EVALUATION_PROMPT.prompt_version),
        )
        duties = await resolve_action_duties(
            context, self.policy_override_repo, self.platform_default_duties
        )
        await notify_duty_holders(
            context,
            holders=self.holders,
            notifier=self.notifier,
            duty=duties.duty_for(PackagingAction.PASS_PRE_PRODUCTION_TEST),
            source_key=f"supply_chain.pre_production_report:{case.id}:{results.digest}",
            title=f"Biên bản test trước SX đã soạn: {case.supplier_name}",
            body=(
                "Hệ thống đã so số đo với chuẩn và soạn biên bản; R&D xem rồi chọn Đạt hoặc "
                "Không đạt với biên bản này."
            ),
            link=po_case_link(case.id.value),
        )

    async def _draft(
        self,
        context: AccessContext,
        case: POCase,
        doc_type: DocumentType,
        values: Mapping[str, FieldInput],
        sources: Sequence[DraftSource],
    ) -> None:
        await self.prepare_draft.handle(
            context,
            case_kind=CaseKind.PO,
            case_id=case.id.value,
            doc_type=doc_type,
            values=values,
            sources=sources,
        )

    async def _tell(self, context: AccessContext, case: POCase, step: PackagingAction) -> None:
        duties = await resolve_action_duties(
            context, self.policy_override_repo, self.platform_default_duties
        )
        await notify_duty_holders(
            context,
            holders=self.holders,
            notifier=self.notifier,
            duty=duties.duty_for(step),
            source_key=f"supply_chain.proof_checked:{case.id}:{step.value}",
            title=f"Bản in thiết kế có điểm cần sửa: {case.supplier_name}",
            body="AI đã đọc bản in, hệ thống so với BM04 và luật nhãn; yêu cầu sửa đã soạn.",
            link=po_case_link(case.id.value),
        )


# ------------------------------------------------------------------- page --


@dataclass(frozen=True, slots=True)
class PackagingProof:
    proof: SourceRead | None
    findings: tuple[Finding, ...]


@dataclass(frozen=True)
class GetPackagingProof:
    cases: POCaseStorePort
    sources: PackagingSources
    policy_override_repo: PolicyOverridePort
    platform_default_policy: SupplyChainStepPreparation
    authz: AuthorizationPort

    async def handle(self, context: AccessContext, case_id: POCaseId) -> PackagingProof:
        await self.authz.require(
            context=context, action=PO_CASE_READ, resource_type="po_case", resource_id=str(case_id)
        )
        case = await self.cases.get(context, case_id)
        if case is None or case.workspace_id.value != context.workspace_id:
            raise NotFoundError("PO case not found", details={"case_id": str(case_id)})
        policy = await resolve_step_preparation(
            context, self.policy_override_repo, self.platform_default_policy
        )
        if not policy.packaging:
            return PackagingProof(None, ())
        proof, findings = await self.sources.proof(context, case)
        return PackagingProof(proof, tuple(findings))


__all__ = [
    "PACKAGING_PAPERS_LANE",
    "GetPackagingProof",
    "PackagingPaperCount",
    "PackagingProof",
    "PackagingSources",
    "PreparePackagingPapers",
    "colour_request_values",
    "design_request_values",
    "packaging_lane_context",
    "skeleton_values",
]
