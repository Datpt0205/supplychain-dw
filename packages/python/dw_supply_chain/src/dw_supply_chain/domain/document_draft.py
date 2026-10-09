"""A draft of a case document (ADR 0025 point 4; ticket ai-automation/03).

A draft is fields on a template, each with where its value came from, and the
gaps nobody has filled. It is NOT a document: a step's paper is read from
`case_documents` only, and a draft reaches that table only when a person
approves it (ticket 05), as a new document that names its draft. So nothing
here can satisfy a step, by construction rather than by a flag a reader could
forget to check (ADR 0025, "Phương án đã cân nhắc").

What this module owns:

- **Which template a draft of a type renders with** (`DRAFT_TEMPLATES`).
- **The shape of a draft's fields:** `{name: {"value": ..., "source": ...}}`, a
  value a string as it will be printed (a number or a date in the canonical form
  code wrote), a table a list of rows; a source the document and quote it was
  read from, or the person who typed it.
- **Versions and decisions.** An edit is a new version of the same lineage; a
  version that has a decision, or is not the latest, cannot be edited or
  decided again.
- **The content hash** an approval binds to: the type, the template and the
  fields, canonically serialised.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from dw_kernel.errors import ConflictError, DomainError
from dw_supply_chain.domain.case_document import CaseKind, DocumentType


class DraftDecision(StrEnum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class DraftStatus(StrEnum):
    """What a version is, read from its decision and its place in the lineage."""

    OPEN = "open"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


# The template a draft of each type renders with. One owner: a step's
# preparation (ticket 05) and the preview both read it here.
DRAFT_TEMPLATES: Mapping[DocumentType, tuple[str, str]] = {
    DocumentType.SAMPLE_REVISION_REQUEST: ("supply_chain.sample_revision_request", "1.0.0"),
    DocumentType.SAMPLE_EVALUATION: ("supply_chain.sample_evaluation", "1.0.0"),
    DocumentType.BOD_SUBMISSION: ("supply_chain.bod_submission", "1.0.0"),
    DocumentType.PRODUCT_PROFILE_BM04: ("supply_chain.product_profile_bm04", "1.0.0"),
    # 1.1.0 (ticket ai-automation/14): the deposit beside the order total.
    DocumentType.PURCHASE_ORDER: ("supply_chain.purchase_order", "1.1.0"),
    DocumentType.OFFICIAL_ITEM_CODE: ("supply_chain.official_item_code", "1.0.0"),
    # Steps 11 and 16 (ticket ai-automation/15): the deposit and final payment
    # requests, which become the case's deposit and payment papers.
    DocumentType.DEPOSIT_DOCS: ("supply_chain.deposit_request", "1.0.0"),
    DocumentType.PAYMENT_DOCS: ("supply_chain.payment_request", "1.0.0"),
    # Step 12 (ticket ai-automation/16): MKT's skeletons from the BM04, and
    # Cung ứng's revision requests to the supplier.
    DocumentType.PACKAGING_CONTENT: ("supply_chain.packaging_content", "1.0.0"),
    DocumentType.USER_MANUAL: ("supply_chain.user_manual", "1.0.0"),
    DocumentType.COLOUR_REVISION_REQUEST: ("supply_chain.colour_revision_request", "1.0.0"),
    DocumentType.DESIGN_REVISION_REQUEST: ("supply_chain.design_revision_request", "1.0.0"),
    # Step 14 (ticket ai-automation/17): QC failed by the numbers.
    DocumentType.REWORK_REQUEST: ("supply_chain.rework_request", "1.0.0"),
    DocumentType.WAREHOUSE_RECEIPT: ("supply_chain.warehouse_receipt", "1.0.0"),
    DocumentType.DISCREPANCY_REPORT: ("supply_chain.discrepancy_report", "1.0.0"),
}

_MAX_REASON = 1000


@dataclass(frozen=True, slots=True)
class DraftSource:
    """A document a draft was prepared from, as it was when read."""

    document_id: uuid.UUID
    sha256: str
    extraction_id: uuid.UUID | None = None

    def as_json(self) -> dict[str, str | None]:
        return {
            "document_id": str(self.document_id),
            "sha256": self.sha256,
            "extraction_id": None if self.extraction_id is None else str(self.extraction_id),
        }


@dataclass(frozen=True, slots=True)
class DocumentDraft:
    id: uuid.UUID
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    lineage_id: uuid.UUID
    version: int
    case_kind: CaseKind
    case_id: uuid.UUID
    doc_type: DocumentType
    template_id: str
    template_version: str
    prompt_id: str | None
    prompt_version: str | None
    fields: dict[str, Any]
    gaps: tuple[str, ...]
    sources: tuple[DraftSource, ...]
    content_sha256: str
    created_by: uuid.UUID
    created_at: datetime
    decision: DraftDecision | None = None
    decision_reason: str | None = None
    # The newest version of this draft's lineage, as read with it.
    latest_version: int | None = None

    @property
    def status(self) -> DraftStatus:
        if self.decision is not None:
            return DraftStatus(self.decision.value)
        if self.latest_version is not None and self.latest_version > self.version:
            return DraftStatus.SUPERSEDED
        return DraftStatus.OPEN

    def require_open(self) -> None:
        """A version is edited or decided only while it is the open latest one."""
        status = self.status
        if status is not DraftStatus.OPEN:
            raise ConflictError(
                "bản nháp này không còn mở để sửa hay quyết",
                details={"draft_id": str(self.id), "status": status.value},
            )


def content_sha256(doc_type: DocumentType, template_ref: str, fields: Mapping[str, Any]) -> str:
    """What an approval binds to: the same type, template and fields hash the
    same, however the JSON was written."""
    canonical = json.dumps(
        {"doc_type": doc_type.value, "template": template_ref, "fields": fields},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def rejection_reason(reason: str | None) -> str:
    text = (reason or "").strip()
    if not text:
        raise DomainError("từ chối bản nháp cần lý do", details={"field": "reason"})
    if len(text) > _MAX_REASON:
        raise DomainError(f"lý do tối đa {_MAX_REASON} ký tự", details={"field": "reason"})
    return text


def field_value(fields: Mapping[str, Any], name: str) -> Any:
    entry = fields.get(name)
    return entry.get("value") if isinstance(entry, Mapping) else None
