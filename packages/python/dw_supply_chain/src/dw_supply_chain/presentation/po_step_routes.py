"""HTTP surface of a PO case's steps 11-17 prepared by code (tickets
ai-automation/15-18). Decisions live in `application.po_steps` and
`application.po_papers`.

`GET /po-cases/{id}/step-proposal` — the enabled step of the case's state: its
draft (id, version, status, content hash; its fields are read through the
drafts API, prices hidden per scope), what code finds now (no amount, no
account number in any of it), AI's suggestion beside each result a person
types (an amount redacted without the commercial scope), whether AI proposes
the step, and whether the caller may approve (and why not).
`POST /po-cases/{id}/step-proposal/approval` — the person whose duty the step
is approves the draft they saw with the results they typed.
`GET|PUT /po-documents-policy` — the paper each PO step needs, per tenant.

No `from __future__ import annotations`, for the reason `routes.py` gives.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from dw_platform.application.access_context import AccessContext
from dw_supply_chain.application.po_papers import (
    GetPODocumentsPolicy,
    SetPODocumentsPolicyOverride,
)
from dw_supply_chain.application.po_steps import ApprovePOStep, GetPOStepProposal, POStepProposal
from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.document_draft import DraftStatus
from dw_supply_chain.domain.po_case import CaseAction, POCaseId
from dw_supply_chain.domain.po_step import POStepKind, ResultKind
from dw_supply_chain.po_documents_policy import SupplyChainPODocuments
from dw_supply_chain.presentation.routes import SupportsIdempotentRecord

AccessContextResolver = Callable[..., object]
IdempotencyResolver = Callable[..., object]
_NO_NUL = r"^[^\x00]*$"


class POStepFindingView(BaseModel):
    code: str
    subject: str
    message: str


class POStepSuggestionView(BaseModel):
    value: str | None
    quote: str | None
    document_id: uuid.UUID | None


class POStepResultView(BaseModel):
    name: str
    kind: ResultKind
    label: str
    required: bool
    # A choice's options (QC's verdict); empty for any other kind.
    options: list[str]
    # Required only when the outcome is this choice (a reason for a fail).
    required_for: str | None
    # AI's reading beside the empty field; null: none.
    suggestion: POStepSuggestionView | None
    # The suggestion is an amount the caller may not read.
    redacted: bool


class POStepProposalView(BaseModel):
    # null: the case's current state has no step AI prepares for this tenant.
    step: POStepKind | None
    action: CaseAction | None
    draft_doc_type: DocumentType | None
    draft_id: uuid.UUID | None
    draft_version: int | None
    draft_status: DraftStatus | None
    content_sha256: str | None
    findings: list[POStepFindingView]
    results: list[POStepResultView]
    proposed: bool
    can_approve: bool
    blocked_reason: str | None
    missing_paper: DocumentType | None


class ApprovePOStepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: POStepKind
    draft_id: uuid.UUID | None = None
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    results: dict[str, Annotated[str, Field(max_length=100, pattern=_NO_NUL)]] = Field(
        default_factory=dict, max_length=10
    )


def _view(found: POStepProposal) -> POStepProposalView:
    spec, draft = found.spec, found.draft
    return POStepProposalView(
        step=None if spec is None else spec.kind,
        action=None if spec is None else spec.action,
        draft_doc_type=None if spec is None else spec.draft,
        draft_id=None if draft is None else draft.id,
        draft_version=None if draft is None else draft.version,
        draft_status=None if draft is None else draft.status,
        content_sha256=None if draft is None else draft.content_sha256,
        findings=[
            POStepFindingView(code=f.code, subject=f.subject, message=f.message)
            for f in found.findings
        ],
        results=[
            POStepResultView(
                name=r.field.name,
                kind=r.field.kind,
                label=r.field.label,
                required=r.field.required,
                options=list(r.field.options),
                required_for=r.field.required_for,
                suggestion=None
                if r.suggestion is None
                else POStepSuggestionView(
                    value=r.suggestion.value,
                    quote=r.suggestion.quote,
                    document_id=r.suggestion.document_id,
                ),
                redacted=r.redacted,
            )
            for r in found.results
        ],
        proposed=found.proposed,
        can_approve=found.can_approve,
        blocked_reason=found.blocked,
        missing_paper=found.missing_paper,
    )


@dataclass(frozen=True)
class POStepHandlers:
    get: GetPOStepProposal
    approve: ApprovePOStep
    get_papers_policy: GetPODocumentsPolicy
    set_papers_policy: SetPODocumentsPolicyOverride


def build_po_step_router(
    handlers: POStepHandlers,
    *,
    resolve_access_context: AccessContextResolver,
    resolve_idempotency: IdempotencyResolver,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/supply-chain", tags=["supply_chain"])
    require_access_context = Annotated[AccessContext, Depends(resolve_access_context)]
    require_idempotency = Annotated[SupportsIdempotentRecord, Depends(resolve_idempotency)]
    h = handlers

    @router.get("/po-cases/{case_id}/step-proposal", response_model=POStepProposalView)
    async def get_po_step_proposal(
        case_id: uuid.UUID, context: require_access_context
    ) -> POStepProposalView:
        return _view(await h.get.handle(context, POCaseId(case_id)))

    @router.post("/po-cases/{case_id}/step-proposal/approval", response_model=POStepProposalView)
    async def approve_po_step(
        case_id: uuid.UUID,
        body: ApprovePOStepRequest,
        context: require_access_context,
        idempotency: require_idempotency,
    ) -> POStepProposalView:
        await h.approve.handle(
            context,
            POCaseId(case_id),
            kind=body.step,
            draft_id=body.draft_id,
            content_sha256=body.content_sha256,
            results=body.results,
        )
        view = _view(await h.get.handle(context, POCaseId(case_id)))
        recorded: POStepProposalView = await idempotency.record(view)
        return recorded

    @router.get("/po-documents-policy", response_model=SupplyChainPODocuments)
    async def get_po_documents_policy(context: require_access_context) -> SupplyChainPODocuments:
        return await h.get_papers_policy.handle(context)

    @router.put("/po-documents-policy", response_model=SupplyChainPODocuments)
    async def put_po_documents_policy(
        body: SupplyChainPODocuments, context: require_access_context
    ) -> SupplyChainPODocuments:
        await h.set_papers_policy.handle(context, body)
        return body

    return router
