"""Delay Impact Analysis — which milestones, and what the model adds.

Split the same way supplier updates are: `impacted_milestones` and
`propagated_estimates` below are domain rules a human can audit without a
model call — CaseState's own declaration order already says what "downstream
of this case" means, so finding it is a fact, not a judgment. `DelayImpactExtraction` (the model's
part) is deliberately narrower than the doc's own listed output (impacted
milestones; estimated effect; assumptions; source evidence; mitigation
options): WHICH milestones and a first-pass day estimate come from here, not
the model, and "source evidence" is the triggering `SupplierUpdate` itself,
already on record — nothing for the model to newly assert. What is left for
the model is exactly the judgment the strategy doc says not to make
unilaterally: assumptions worth naming and mitigation OPTIONS, plural,
never a chosen one ("Không tự chọn phương án nếu cần business decision").

Uniform propagation (every downstream milestone shifts by the same
`delay_days` the update reported) is a stated simplification for this first
slice, not a hidden one — a real delay does not always propagate evenly (a
buffered step can absorb it, a tightly-coupled one cannot), but modelling
that needs real throughput data this repo does not have yet. Revisit once
real cases exist to measure against, same discipline as `sla_policy.py`'s
reference numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from dw_kernel.ids import EntityId, TenantId, WorkspaceId
from dw_supply_chain.domain.po_case import CaseState, POCase, POCaseId
from dw_supply_chain.domain.supplier_update import SupplierUpdateId

# Declaration order in `po_case.CaseState` IS this sequence — repeated here
# as an explicit tuple (rather than re-deriving it from the enum's own
# member order, which is an implementation detail of Python's Enum, not a
# contract) so a reordering of CaseState's declaration cannot silently
# change what "downstream" means here without this file's own tests noticing.
_HAPPY_PATH = (
    CaseState.PO_CREATED,
    CaseState.WAITING_DEPOSIT,
    CaseState.DEPOSIT_CONFIRMED,
    CaseState.PRE_PRODUCTION,
    CaseState.PRODUCTION,
    CaseState.QC,
    CaseState.IN_TRANSIT,
    CaseState.ARRIVED_PORT,
    CaseState.WAITING_PAYMENT,
    CaseState.PAYMENT_COMPLETED,
    CaseState.WAREHOUSE_RECEIVING,
    CaseState.COMPLETED,
)


class ImpactedMilestoneEstimate(BaseModel):
    """One downstream milestone and the first-pass day estimate for it —
    both code-computed (`impacted_milestones`/`propagated_estimates` below),
    never asked of the model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    milestone: CaseState
    estimated_delay_days: int = Field(ge=0)


class MitigationOption(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    description: str = Field(min_length=1, max_length=500)
    # The strategy doc does not name this field, but "mitigation options" with no
    # stated cost is exactly what invites picking one without a business
    # decision — the thing the doc explicitly forbids the model from doing.
    tradeoff: str = Field(min_length=1, max_length=500)


class DelayImpactExtraction(BaseModel):
    """The model's typed claim: assumptions and mitigation options only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assumptions: list[str] = Field(min_length=1, max_length=10)
    mitigation_options: list[MitigationOption] = Field(min_length=1, max_length=5)


@dataclass(frozen=True, slots=True)
class DelayImpactAnalysisId(EntityId):
    """Identifies one delay impact analysis record."""


@dataclass(frozen=True, slots=True)
class DelayImpactAnalysis:
    """One analysis, as kept. Immutable — a record of what was computed and
    asked at the time, same shape as `SupplierUpdate` for the same reason.

    `supplier_update_id` is the "source evidence" the analysis must cite: the
    triggering update, already on record with its own verified `source_ref`
    — nothing new for this record to assert.
    """

    id: DelayImpactAnalysisId
    tenant_id: TenantId
    workspace_id: WorkspaceId
    po_case_id: POCaseId
    supplier_update_id: SupplierUpdateId
    delay_days: int
    impacted_milestones: tuple[ImpactedMilestoneEstimate, ...]
    extraction: DelayImpactExtraction
    created_at: datetime | None = None


def impacted_milestones(case: POCase) -> list[CaseState]:
    """Every happy-path milestone still ahead of this case.

    Mid-interrupt (`WAITING_EXTERNAL`/`BLOCKED`/`MANUAL_REVIEW`), "ahead"
    means ahead of the state the case will resume to, not the interrupt
    state itself — the interrupt is not a place in the process, it is a
    pause on one. `REWORK` resumes into `PRODUCTION` specifically (see
    `POCase.resume_from_rework`), not to `interrupted_state`, which `REWORK`
    does not set. `CANCELLED` and `COMPLETED` both correctly return an empty
    list without a special case: `COMPLETED` is the last element of the
    happy path, and `CANCELLED` never appears in it at all.
    """
    if case.state is CaseState.REWORK:
        reference = CaseState.PRODUCTION
    elif case.interrupted_state is not None:
        reference = case.interrupted_state
    else:
        reference = case.state

    if reference not in _HAPPY_PATH:
        return []
    return list(_HAPPY_PATH[_HAPPY_PATH.index(reference) + 1 :])


def propagated_estimates(case: POCase, *, delay_days: int) -> list[ImpactedMilestoneEstimate]:
    """`delay_days` applied uniformly to every impacted milestone — see this
    module's own docstring for why uniform propagation is this slice's
    stated simplification, not an accident."""
    return [
        ImpactedMilestoneEstimate(milestone=milestone, estimated_delay_days=delay_days)
        for milestone in impacted_milestones(case)
    ]
