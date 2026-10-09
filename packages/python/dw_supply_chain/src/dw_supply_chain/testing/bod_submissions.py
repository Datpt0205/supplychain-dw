"""An in-memory world for the tờ trình BGĐ (ticket ai-automation/10): its unit
tests and the `supply_chain.bod_submission` eval grader run the REAL
`PrepareBodSubmission` over the stores of `testing.step_preparation`, with the
shipped prompt, skill and template.

A case waiting for BGĐ, its approved sample evaluation record of the round, a
supplier quotation the extraction lane read (with prices), and its history.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from dw_kernel.ports import FixedClock, Uuid4Generator
from dw_supply_chain.application.bod_submissions import PrepareBodSubmission
from dw_supply_chain.application.document_drafts import (
    NewDocumentDraft,
    NewDraftDecision,
    PrepareDocumentDraft,
)
from dw_supply_chain.domain.case_document import CaseKind, DocumentType
from dw_supply_chain.domain.document_draft import DraftDecision
from dw_supply_chain.domain.product_development_case import (
    ProductAction,
    ProductCaseTransition,
    ProductDevelopmentCase,
    ProductDevState,
)
from dw_supply_chain.step_preparation_policy import SupplyChainStepPreparation
from dw_supply_chain.testing.step_preparation import NOW, StepWorld

QUOTATION_TEXT = (
    "BÁO GIÁ\nNồi inox 3 đáy 24cm, đơn giá 245.000 VND, số lượng 1.000 cái, "
    "thành tiền 245.000.000 VND\nMOQ: 500 cái\nGiao hàng: 45 ngày\n"
)
QUOTATION_READING: dict[str, Any] = {
    "currency": {"value": "VND", "quote": "245.000 VND"},
    "moq": {"value": "500", "quote": "MOQ: 500 cái"},
    "lead_time_days": {"value": "45", "quote": "Giao hàng: 45 ngày"},
    "lines": [
        {
            "description": {"value": "Nồi inox 3 đáy 24cm", "quote": "Nồi inox 3 đáy 24cm"},
            "unit_price": {"value": "245000", "quote": "đơn giá 245.000 VND"},
            "quantity": {"value": "1000", "quote": "số lượng 1.000 cái"},
            "line_total": {"value": "245000000", "quote": "thành tiền 245.000.000 VND"},
        }
    ],
    "total": {"value": "245000000", "quote": "thành tiền 245.000.000 VND"},
}
RECORD_ROWS = [
    {"criterion": "Độ dày đáy", "standard": "≥ 3 mm", "measured": "3.2 mm", "result": "Đạt"},
    {"criterion": "Ngoại quan", "standard": "Đạt", "measured": "Đạt", "result": "Đạt"},
]


@dataclass
class SubmissionWorld:
    gateway: Any
    world: StepWorld = field(default_factory=StepWorld)
    model_profile: str | None = None
    # The tenant's step preparation policy asks for a tờ trình (Elmich's does).
    enabled: bool = True

    def submitter(self) -> PrepareBodSubmission:
        w = self.world
        ids = Uuid4Generator()
        return PrepareBodSubmission(
            cases=w.cases,
            documents=w.documents,
            readings=w.readings,
            drafts=w.drafts,
            prepare_draft=PrepareDocumentDraft(
                cases={CaseKind.PRODUCT: w.cases},
                drafts=w.drafts,
                templates=w.templates,
                ids=ids,
                clock=w.clock,
            ),
            plans=_Plans(),
            gateway=self.gateway,
            ids=ids,
            clock=FixedClock(NOW),
            worker_id="supply_chain_advance_product_case",
            worker_version="1.0.0",
            policy_override_repo=_Overrides(self.enabled),
            platform_default_policy=SupplyChainStepPreparation.model_validate(
                {
                    "schema_version": "1.0",
                    "policy_id": "supply_chain_step_preparation",
                    "policy_version": "1.0.0",
                }
            ),
            model_profile=self.model_profile,
        )

    def case(
        self,
        *,
        tenant: uuid.UUID | None = None,
        workspace: uuid.UUID | None = None,
        product_name: str = "Nồi inox 3 đáy 24cm",
        history_reason: str | None = None,
        with_record: bool = True,
        with_quotation: bool = True,
    ) -> ProductDevelopmentCase:
        w = self.world
        case, _ = w.add_case(
            ProductDevState.PENDING_BOD_REVIEW,
            tenant=tenant,
            workspace=workspace,
            product_name=product_name,
            category="noi",
        )
        if history_reason is not None:
            w.cases.transitions.setdefault(case.id.value, []).append(
                ProductCaseTransition(
                    action=ProductAction.REQUEST_REVISION,
                    from_state=ProductDevState.SAMPLE_TESTING,
                    to_state=ProductDevState.REVISION_REQUESTED,
                    reason=history_reason,
                    actor_id=uuid.uuid4(),
                    occurred_at=NOW - timedelta(days=5),
                    id=uuid.uuid4(),
                )
            )
        context = w.context(tenant=case.tenant_id.value, workspace=case.workspace_id.value)
        if with_record:
            draft_id = uuid.uuid4()
            w.drafts.insert(
                context,
                NewDocumentDraft(
                    id=draft_id,
                    lineage_id=draft_id,
                    version=1,
                    case_kind=CaseKind.PRODUCT,
                    case_id=case.id.value,
                    doc_type=DocumentType.SAMPLE_EVALUATION,
                    template_id="supply_chain.sample_evaluation",
                    template_version="1.0.0",
                    prompt_id=None,
                    prompt_version=None,
                    fields={
                        "criteria": {"value": RECORD_ROWS, "source": None},
                        "conclusion": {"value": "Đạt", "source": None},
                    },
                    gaps=[],
                    sources=[],
                    content_sha256="b" * 64,
                ),
            )
            w.drafts.record_decision(
                context,
                NewDraftDecision(
                    id=uuid.uuid4(),
                    draft_id=draft_id,
                    decision=DraftDecision.CONFIRMED,
                    reason=None,
                ),
            )
        if with_quotation:
            document = w.add_document(case, DocumentType.SUPPLIER_QUOTATION, text=QUOTATION_TEXT)
            w.add_reading(document, QUOTATION_READING)
        return case

    def context(self, case: ProductDevelopmentCase) -> Any:
        return self.world.context(tenant=case.tenant_id.value, workspace=case.workspace_id.value)


@dataclass
class _Overrides:
    enabled: bool

    async def get(self, context: Any, policy_id: str) -> dict[str, Any] | None:
        if policy_id != "supply_chain_step_preparation" or not self.enabled:
            return None
        return {
            "schema_version": "1.0",
            "policy_id": "supply_chain_step_preparation",
            "policy_version": "1.3.0",
            "bod_submission": True,
        }

    async def put(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("not exercised")


class _Plans:
    async def plan_of(self, tenant_id: uuid.UUID) -> str | None:
        return "professional"


__all__ = ["QUOTATION_READING", "QUOTATION_TEXT", "RECORD_ROWS", "SubmissionWorld"]
