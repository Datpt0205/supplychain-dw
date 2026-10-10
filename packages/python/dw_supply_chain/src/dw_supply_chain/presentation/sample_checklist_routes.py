"""HTTP surface of a sample round's checklist (steps 3-5; ticket
ai-automation/09). Decisions live in `application.sample_checklist`.

`GET /product-cases/{id}/sample-checklist` — the criteria of the case's
Category, the newest value of each this round, and code's verdict.
`POST /product-cases/{id}/sample-measurements` — R&D enters one value.
`GET|PUT /sample-criteria-policy` — the tenant's criteria.

No `from __future__ import annotations`, for the reason `routes.py` gives.
"""

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from dw_platform.application.access_context import AccessContext
from dw_supply_chain.application.sample_checklist import (
    GetSampleChecklist,
    GetSampleCriteriaPolicy,
    RecordMeasurement,
    SampleChecklist,
    SetSampleCriteriaPolicyOverride,
)
from dw_supply_chain.domain.sample_criteria import CriterionKind, Verdict
from dw_supply_chain.domain.sample_evaluation import CriterionResult
from dw_supply_chain.presentation.routes import SupportsIdempotentRecord
from dw_supply_chain.sample_criteria_policy import SupplyChainSampleCriteria

AccessContextResolver = Callable[..., object]
IdempotencyResolver = Callable[..., object]
_NO_NUL = r"^[^\x00]*$"


class ChecklistRowView(BaseModel):
    key: str
    label: str
    kind: CriterionKind
    unit: str | None
    min: Decimal | None
    max: Decimal | None
    # What the record prints as the standard.
    standard: str
    # The newest value this round, as code canonicalised it; null: unmeasured.
    value: str | None
    note: str | None
    verdict: Verdict


class SampleChecklistView(BaseModel):
    case_id: uuid.UUID
    sample_round: int
    can_record: bool
    rows: list[ChecklistRowView]


class MeasurementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    # A number in either convention, or pass / fail.
    value: str = Field(min_length=1, max_length=40, pattern=_NO_NUL)
    note: str | None = Field(default=None, max_length=500, pattern=_NO_NUL)


def checklist_rows(results: Sequence[CriterionResult]) -> list[ChecklistRowView]:
    """A sample round's or a pre-production test's rows, one shape."""
    return [
        ChecklistRowView(
            key=r.criterion.key,
            label=r.criterion.label,
            kind=r.criterion.kind,
            unit=r.criterion.unit,
            min=r.criterion.min,
            max=r.criterion.max,
            standard=r.criterion.standard,
            value=r.value,
            note=r.note,
            verdict=r.verdict,
        )
        for r in results
    ]


def _view(checklist: SampleChecklist) -> SampleChecklistView:
    return SampleChecklistView(
        case_id=checklist.case.id.value,
        sample_round=checklist.case.sample_round,
        can_record=checklist.can_record,
        rows=checklist_rows(checklist.results),
    )


@dataclass(frozen=True)
class SampleChecklistHandlers:
    get_checklist: GetSampleChecklist
    record: RecordMeasurement
    get_policy: GetSampleCriteriaPolicy
    set_policy: SetSampleCriteriaPolicyOverride


def build_sample_checklist_router(
    handlers: SampleChecklistHandlers,
    *,
    resolve_access_context: AccessContextResolver,
    resolve_idempotency: IdempotencyResolver,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/supply-chain", tags=["supply_chain"])
    require_access_context = Annotated[AccessContext, Depends(resolve_access_context)]
    require_idempotency = Annotated[SupportsIdempotentRecord, Depends(resolve_idempotency)]
    h = handlers

    @router.get("/product-cases/{case_id}/sample-checklist", response_model=SampleChecklistView)
    async def get_sample_checklist(
        case_id: uuid.UUID, context: require_access_context
    ) -> SampleChecklistView:
        return _view(await h.get_checklist.handle(context, case_id))

    @router.post(
        "/product-cases/{case_id}/sample-measurements",
        response_model=SampleChecklistView,
        status_code=201,
    )
    async def record_sample_measurement(
        case_id: uuid.UUID,
        body: MeasurementRequest,
        context: require_access_context,
        idempotency: require_idempotency,
    ) -> SampleChecklistView:
        await h.record.handle(
            context, case_id, criterion=body.criterion, value=body.value, note=body.note
        )
        view = _view(await h.get_checklist.handle(context, case_id))
        return await idempotency.record(view, status_code=201)

    @router.get("/sample-criteria-policy", response_model=SupplyChainSampleCriteria)
    async def get_sample_criteria_policy(
        context: require_access_context,
    ) -> SupplyChainSampleCriteria:
        return await h.get_policy.handle(context)

    @router.put("/sample-criteria-policy", response_model=SupplyChainSampleCriteria)
    async def set_sample_criteria_policy(
        body: SupplyChainSampleCriteria,
        context: require_access_context,
        idempotency: require_idempotency,
    ) -> SupplyChainSampleCriteria:
        await h.set_policy.handle(context, body)
        return await idempotency.record(body)

    return router
