"""HTTP surface of step proposals (ADR 0025; ticket ai-automation/05).
Decisions live in `application.step_proposals`.

`GET /product-cases/{id}/step-proposal` — the case page's "AI đã chuẩn bị"
block: whether the tenant prepares the case's step, what the latest preparation
came to (and why not, when it could not), and the pending proposal: its drafts,
sources, findings, who may decide (the stamped scope), and for a physical step
its result fields, each EMPTY with AI's suggestion beside it.
`POST /product-cases/{id}/step-proposal/decision` — approve or not, with the
typed result a physical step needs; the platform decides (scope, comment,
subject still current) and the run resumes.
`GET|PUT /step-preparation-policy` — the tenant's policy.

No `from __future__ import annotations`, for the reason `routes.py` gives.
"""

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from dw_platform.application.access_context import AccessContext
from dw_supply_chain.application.step_proposals import (
    DecideStepProposal,
    GetStepPreparationPolicy,
    GetStepProposal,
    SetStepPreparationPolicyOverride,
    StepProposalReading,
)
from dw_supply_chain.domain.product_development_case import ProductDevelopmentCaseId
from dw_supply_chain.presentation.routes import SupportsIdempotentRecord
from dw_supply_chain.step_preparation_policy import SupplyChainStepPreparation

AccessContextResolver = Callable[..., object]
IdempotencyResolver = Callable[..., object]

ProposalStatus = Literal["none", "proposed", "not_prepared", "superseded", "rejected", "applied"]


class FindingView(BaseModel):
    code: str
    subject: str
    message: str


class ProposalDraftView(BaseModel):
    draft_id: uuid.UUID
    doc_type: str
    version: int
    gaps: list[str]


class ProposalSourceView(BaseModel):
    doc_type: str
    document_id: uuid.UUID | None


class SuggestionView(BaseModel):
    """What AI read for a result field: shown beside it, never its value."""

    value: str
    quote: str
    document_id: uuid.UUID | None


class ResultFieldView(BaseModel):
    name: str
    label: str
    kind: str
    suggestion: SuggestionView | None


class StepProposalView(BaseModel):
    # The tenant's policy prepares the case's current step.
    prepared: bool
    action: str | None
    status: ProposalStatus
    reason: str | None
    recorded_at: datetime | None
    approval_id: uuid.UUID | None
    required_scope: str | None
    # The case, a draft or a source changed since it was raised: a decision is
    # refused and the lane prepares it again.
    stale: bool
    can_decide: bool
    physical: bool
    drafts: list[ProposalDraftView]
    sources: list[ProposalSourceView]
    action_document_id: uuid.UUID | None
    findings: list[FindingView]
    result_fields: list[ResultFieldView]


def _uuid(raw: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(raw)) if raw else None
    except ValueError:
        return None


def _list(payload: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    raw = payload.get(key)
    return [item for item in raw if isinstance(item, Mapping)] if isinstance(raw, list) else []


def _view(reading: StepProposalReading) -> StepProposalView:
    approval = reading.approval
    payload: Mapping[str, Any] = {} if approval is None else approval.payload
    record = reading.record
    status: ProposalStatus = (
        "proposed" if approval is not None else ("none" if record is None else record.outcome.value)
    )
    return StepProposalView(
        prepared=reading.prepared,
        action=reading.action,
        status=status,
        reason=None if record is None or approval is not None else record.reason,
        recorded_at=None if record is None else record.created_at,
        approval_id=None if approval is None else approval.id,
        required_scope=None if approval is None else approval.required_scope,
        stale=reading.stale,
        can_decide=reading.can_decide and not reading.stale,
        physical=bool(payload.get("physical")),
        drafts=[
            ProposalDraftView(
                draft_id=uuid.UUID(str(d["draft_id"])),
                doc_type=str(d.get("doc_type")),
                version=int(d.get("version", 1)),
                gaps=[str(g) for g in d.get("gaps") or []],
            )
            for d in _list(payload, "drafts")
        ],
        sources=[
            ProposalSourceView(
                doc_type=str(s.get("doc_type")), document_id=_uuid(s.get("document_id"))
            )
            for s in _list(payload, "sources")
        ],
        action_document_id=_uuid(payload.get("action_document_id")),
        findings=[
            FindingView(
                code=str(f.get("code")),
                subject=str(f.get("subject")),
                message=str(f.get("message")),
            )
            for f in _list(payload, "findings")
        ],
        result_fields=[
            ResultFieldView(
                name=f.name,
                label=f.label,
                kind=f.kind,
                suggestion=None
                if f.suggestion is None
                else SuggestionView(
                    value=str(f.suggestion.get("value", "")),
                    quote=str(f.suggestion.get("quote", "")),
                    document_id=_uuid(f.suggestion.get("document_id")),
                ),
            )
            for f in reading.result_fields
        ],
    )


class StepDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: uuid.UUID
    approve: bool
    comment: str = Field(default="", max_length=2000)
    # A physical step's result, by field name; nothing for any other step.
    result: dict[str, str] = Field(default_factory=dict, max_length=10)


@dataclass(frozen=True)
class StepProposalHandlers:
    get_proposal: GetStepProposal
    decide: DecideStepProposal
    get_policy: GetStepPreparationPolicy
    set_policy: SetStepPreparationPolicyOverride


def build_step_proposal_router(
    handlers: StepProposalHandlers,
    *,
    resolve_access_context: AccessContextResolver,
    resolve_idempotency: IdempotencyResolver,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/supply-chain", tags=["supply_chain"])
    require_access_context = Annotated[AccessContext, Depends(resolve_access_context)]
    require_idempotency = Annotated[SupportsIdempotentRecord, Depends(resolve_idempotency)]
    h = handlers

    @router.get("/product-cases/{case_id}/step-proposal", response_model=StepProposalView)
    async def get_step_proposal(
        case_id: uuid.UUID, context: require_access_context
    ) -> StepProposalView:
        return _view(await h.get_proposal.handle(context, ProductDevelopmentCaseId(case_id)))

    @router.post("/product-cases/{case_id}/step-proposal/decision", response_model=StepProposalView)
    async def decide_step_proposal(
        case_id: uuid.UUID,
        body: StepDecisionRequest,
        context: require_access_context,
        idempotency: require_idempotency,
    ) -> StepProposalView:
        """A decision resumes the run that applies it, so a retry after a
        timeout must not decide twice: `Idempotency-Key` is honoured."""
        await h.decide.handle(
            context,
            ProductDevelopmentCaseId(case_id),
            approval_id=body.approval_id,
            approve=body.approve,
            comment=body.comment,
            result=body.result,
        )
        view = _view(await h.get_proposal.handle(context, ProductDevelopmentCaseId(case_id)))
        return await idempotency.record(view)

    @router.get("/step-preparation-policy", response_model=SupplyChainStepPreparation)
    async def get_step_preparation_policy(
        context: require_access_context,
    ) -> SupplyChainStepPreparation:
        return await h.get_policy.handle(context)

    @router.put("/step-preparation-policy", response_model=SupplyChainStepPreparation)
    async def set_step_preparation_policy(
        body: SupplyChainStepPreparation, context: require_access_context
    ) -> SupplyChainStepPreparation:
        """Replaces the tenant's own policy, whole."""
        await h.set_policy.handle(context, body)
        return body

    return router
