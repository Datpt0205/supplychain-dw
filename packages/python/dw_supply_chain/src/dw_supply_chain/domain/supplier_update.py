"""A supplier update: what the model extracted, and the record of it.

Split in two on purpose, per CLAUDE.md's "Understanding vs Deciding" —
`SupplierUpdateExtraction` is the model's typed claim (workflows/
supplier_update_understanding.py produces it, nothing more); `SupplierUpdate`
is what `requires_confirmation` below decided once it checked that claim
against the raw text — a domain rule, not the model's own opinion of itself,
and not application orchestration either, so it lives here rather than in a
handler.

`event_type`'s seven values are this context's own reasonable-but-provisional
reading of the strategy doc (which names only one, `PRODUCTION_DELAY`, plus
the explicit `NO_OFFICIAL_UPDATE` rule) — not something Elmich has
confirmed, same status as the SLA reference numbers in `sla_policy.py`. Widen
it when a real message does not fit rather than forcing one of these seven.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from dw_kernel.ids import EntityId, TenantId, WorkspaceId
from dw_supply_chain.domain.evidence import is_verbatim
from dw_supply_chain.domain.po_case import POCaseId


@dataclass(frozen=True, slots=True)
class SupplierUpdateId(EntityId):
    """Identifies one supplier update record."""


class SupplierEventType(StrEnum):
    PRODUCTION_DELAY = "production_delay"
    QC_ISSUE = "qc_issue"
    SHIPMENT_UPDATE = "shipment_update"
    DEPOSIT_CONFIRMATION = "deposit_confirmation"
    DOCUMENT_SUBMITTED = "document_submitted"
    # The doc's own rule: no source, no inferred percentage — this is
    # the value that means "nothing extractable", not a guess dressed as one.
    NO_OFFICIAL_UPDATE = "no_official_update"
    OTHER = "other"


class SupplierUpdateExtraction(BaseModel):
    """The model's structured claim about one raw supplier message.

    Produced by `workflows.supplier_update_understanding`, validated by the
    gateway's schema check alone — `source_ref` is not yet verified against
    the raw text at this point, and `confidence` is not yet compared against
    any threshold. Both are the application layer's job.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: SupplierEventType
    affected_po: str | None = None
    delay_days: int | None = Field(default=None, ge=0)
    reason: str = Field(min_length=1, max_length=2000)
    proposed_action: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)
    source_ref: str = Field(min_length=1, max_length=2000)


@dataclass(frozen=True, slots=True)
class SupplierUpdate:
    """One supplier update, as decided and kept.

    Immutable: a record of what was extracted and decided at the time, not an
    aggregate with its own lifecycle — matches `dw_platform.domain.approval.
    ApprovalDecision`'s shape for the same reason.
    """

    id: SupplierUpdateId
    tenant_id: TenantId
    workspace_id: WorkspaceId
    po_case_id: POCaseId
    raw_text: str
    extraction: SupplierUpdateExtraction
    # True when the confidence was below threshold or source_ref could not be
    # found verbatim in raw_text — a human has to look at this one, not the
    # model's own stated confidence.
    requires_confirmation: bool
    created_at: datetime | None = None


# Provisional — not measured against real supplier messages yet, same status
# as the alert thresholds in Ops hardening Phase 5 (a starting point, not a
# tuned number). Revisit once real messages have been seen.
CONFIDENCE_THRESHOLD = 0.7


def requires_confirmation(extraction: SupplierUpdateExtraction, raw_text: str) -> bool:
    """The strategy doc's rule for supplier updates, verbatim:
    "Phải yêu cầu confirm nếu confidence thấp hoặc source không đủ."
    `source_ref` failing the verbatim check counts as "không đủ" regardless
    of the confidence the model reported — a model asked how sure it is will
    answer, even when it should not be."""
    return extraction.confidence < CONFIDENCE_THRESHOLD or not is_verbatim(
        extraction.source_ref, raw_text
    )
