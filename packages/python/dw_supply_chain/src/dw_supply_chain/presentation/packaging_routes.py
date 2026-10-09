"""Step 12's colour, packaging and pre-production sub-flow on a PO case (slice PK).

`GET /po-cases/{id}/packaging-design` — the sub-flow's state, each step with
whose duty it is and whether the caller may take it (the step's own check),
the tenant's step-13 rule, and the history.
`POST /po-cases/{id}/packaging-design/steps` — take one step; replayed under
the same `Idempotency-Key` it returns the first answer.
`GET|PUT /packaging-policy` — the tenant's step-13 rule.
`GET /po-cases/{id}/packaging-proof` — the newest packaging design proof and
what code finds in it against the label rules, the BM04 and the PO's SKUs
(ticket ai-automation/16). No price.

The request names a step from the closed `PackagingAction` set, a reason and a
document id; never a case state, a duty, a tenant or a person.
"""

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from dw_platform.application.access_context import AccessContext
from dw_supply_chain.action_duties import CaseDuty
from dw_supply_chain.application.packaging_designs import (
    GetPackagingDesign,
    GetPackagingPolicy,
    PackagingDesignDetail,
    SetPackagingPolicyOverride,
    TakePackagingStep,
)
from dw_supply_chain.application.packaging_papers import GetPackagingProof
from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.extraction import ExtractionStatus
from dw_supply_chain.domain.packaging_design import (
    PackagingAction,
    PackagingDesign,
    PreProductionTest,
    ReviewStatus,
)
from dw_supply_chain.domain.po_case import CaseState, POCaseId
from dw_supply_chain.packaging_policy import SupplyChainPackagingPolicy
from dw_supply_chain.presentation.routes import SupportsIdempotentRecord

AccessContextResolver = Callable[..., object]
IdempotencyResolver = Callable[..., object]


class PackagingStepOptionView(BaseModel):
    action: PackagingAction
    duty: CaseDuty
    # The caller holds the step's duty: the same check the step runs.
    allowed: bool
    requires_reason: bool
    requires_document: bool


class PackagingEventView(BaseModel):
    action: PackagingAction
    reason: str | None
    note: str | None
    document_id: uuid.UUID | None
    actor_id: uuid.UUID
    occurred_at: datetime


class PackagingStateView(BaseModel):
    po_case_id: uuid.UUID
    colour_status: ReviewStatus
    design_status: ReviewStatus
    pre_production_sample_received_at: datetime | None
    pre_production_test: PreProductionTest
    # MKT's two steps (ticket ai-automation/16).
    mkt_pack_sent_at: datetime | None
    packaging_content_submitted_at: datetime | None
    version: int


class PackItemView(BaseModel):
    doc_type: DocumentType
    # null: no document of that type yet.
    document_id: uuid.UUID | None


class PackagingDesignView(PackagingStateView):
    case_state: CaseState
    # The tenant's rule: production waits for a passed test.
    require_pre_production_test: bool
    # The tenant's rule: MKT's steps stand between the colour and the design.
    require_packaging_content: bool
    # What MKT receives (empty where MKT is not a user).
    pack: list[PackItemView]
    steps: list[PackagingStepOptionView]
    history: list[PackagingEventView]


class PackagingFindingView(BaseModel):
    code: str
    subject: str
    message: str


class PackagingProofView(BaseModel):
    # null: no proof on the case, or the tenant does not prepare step 12.
    document_id: uuid.UUID | None
    # null: not read yet (or none).
    status: ExtractionStatus | None
    findings: list[PackagingFindingView]


class TakePackagingStepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: PackagingAction
    # No NUL: PostgreSQL text cannot hold one (a 422, not a 500).
    reason: str | None = Field(default=None, max_length=2000, pattern=r"^[^\x00]*$")
    # The pre-production test report the test step is taken on.
    document_id: uuid.UUID | None = None


def _state(design: PackagingDesign) -> dict[str, object]:
    return {
        "po_case_id": design.po_case_id,
        "colour_status": design.colour_status,
        "design_status": design.design_status,
        "pre_production_sample_received_at": design.pre_production_sample_received_at,
        "pre_production_test": design.pre_production_test,
        "mkt_pack_sent_at": design.mkt_pack_sent_at,
        "packaging_content_submitted_at": design.packaging_content_submitted_at,
        "version": design.version,
    }


def _detail_view(detail: PackagingDesignDetail) -> PackagingDesignView:
    return PackagingDesignView.model_validate(
        {
            **_state(detail.design),
            "case_state": detail.case_state,
            "require_pre_production_test": detail.require_pre_production_test,
            "require_packaging_content": detail.require_packaging_content,
            "pack": [
                PackItemView(doc_type=item.doc_type, document_id=item.document_id)
                for item in detail.pack
            ],
            "steps": [
                PackagingStepOptionView(
                    action=s.action,
                    duty=s.duty,
                    allowed=s.allowed,
                    requires_reason=s.requires_reason,
                    requires_document=s.requires_document,
                )
                for s in detail.steps
            ],
            "history": [
                PackagingEventView(
                    action=e.action,
                    reason=e.reason,
                    note=e.note,
                    document_id=e.document_id,
                    actor_id=e.actor_id,
                    occurred_at=e.occurred_at,
                )
                for e in detail.history
            ],
        }
    )


def build_packaging_router(
    get_design: GetPackagingDesign,
    take_step: TakePackagingStep,
    get_policy: GetPackagingPolicy,
    set_policy: SetPackagingPolicyOverride,
    *,
    get_proof: GetPackagingProof,
    resolve_access_context: AccessContextResolver,
    resolve_idempotency: IdempotencyResolver,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/supply-chain", tags=["supply_chain"])
    require_access_context = Annotated[AccessContext, Depends(resolve_access_context)]
    require_idempotency = Annotated[SupportsIdempotentRecord, Depends(resolve_idempotency)]

    @router.get("/po-cases/{case_id}/packaging-design", response_model=PackagingDesignView)
    async def get_packaging_design(
        case_id: uuid.UUID, context: require_access_context
    ) -> PackagingDesignView:
        return _detail_view(await get_design.handle(context, POCaseId(case_id)))

    @router.post("/po-cases/{case_id}/packaging-design/steps", response_model=PackagingStateView)
    async def take_packaging_step(
        case_id: uuid.UUID,
        body: TakePackagingStepRequest,
        context: require_access_context,
        idempotency: require_idempotency,
    ) -> PackagingStateView:
        design = await take_step.handle(
            context,
            po_case_id=POCaseId(case_id),
            action=body.action,
            reason=body.reason,
            document_id=body.document_id,
        )
        return await idempotency.record(PackagingStateView.model_validate(_state(design)))

    @router.get("/po-cases/{case_id}/packaging-proof", response_model=PackagingProofView)
    async def get_packaging_proof(
        case_id: uuid.UUID, context: require_access_context
    ) -> PackagingProofView:
        found = await get_proof.handle(context, POCaseId(case_id))
        proof = found.proof
        return PackagingProofView(
            document_id=None if proof is None else proof.document_id,
            status=None if proof is None else proof.status,
            findings=[
                PackagingFindingView(code=f.code, subject=f.subject, message=f.message)
                for f in found.findings
            ],
        )

    @router.get("/packaging-policy", response_model=SupplyChainPackagingPolicy)
    async def get_packaging_policy(context: require_access_context) -> SupplyChainPackagingPolicy:
        """Whether the tenant's PO cases need a passed pre-production test to
        enter production."""
        return await get_policy.handle(context)

    @router.put("/packaging-policy", response_model=SupplyChainPackagingPolicy)
    async def set_packaging_policy(
        body: SupplyChainPackagingPolicy, context: require_access_context
    ) -> SupplyChainPackagingPolicy:
        """Replaces the tenant's own step-13 rule, whole. No `Idempotency-Key`,
        as for the other policies' `PUT`."""
        await set_policy.handle(context, body)
        return body

    return router
