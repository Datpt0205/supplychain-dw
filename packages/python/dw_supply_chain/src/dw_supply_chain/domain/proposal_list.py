"""A list of proposed products, read into one row per product (step 1; ticket
ai-automation/08).

The model reads; code decides what each row is worth, the way the extraction
lane does (`domain.extraction`):

- **The fields** (product name, proposal code, supplier, item code, picture
  reference) are `Cited`: kept only when the quote is in the list's text and
  the value in the quote, else a named gap the PIC fills.
- **The Category** is a suggestion the model makes from the product, kept only
  when it is a KEY of the tenant's own list, exactly; anything else is no
  suggestion and a finding (`category_unknown`), never a near match.
- **The priority** is a suggestion with a reason until Elmich's scale exists
  (QE-13): one of three words, its reason dropped if it writes a number the
  list does not or anything like an account number.
- **The findings** are code's: a proposal code already taken in the workspace
  or repeated in the list, an item code or SKU already issued, a product name a
  case already carries. A finding warns; the database still refuses a taken
  code when the PIC proposes the row.

Pure computation, no I/O and no model call.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.extraction import (
    Cited,
    ExtractionSpec,
    ground,
    normalize,
    numbers_in,
    redact_identifiers,
)

MAX_ROWS = 200
PRIORITIES = frozenset({"high", "normal", "low"})
# The fields a proposal takes, in the order the review table shows them.
ROW_FIELDS = ("product_name", "proposal_code", "supplier_name", "item_code", "image_ref")


class Suggestion(BaseModel):
    """What the model suggests and why. Nothing here is a fact read from the
    list; code keeps the value only when it is one the tenant offers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: str | None = Field(default=None, max_length=100)
    reason: str | None = Field(default=None, max_length=300)


class ProposalRowReading(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    product_name: Cited = Cited()
    proposal_code: Cited = Cited()
    supplier_name: Cited = Cited()
    item_code: Cited = Cited()
    image_ref: Cited = Cited()
    category: Suggestion = Suggestion()
    priority: Suggestion = Suggestion()


class ProposalListReading(BaseModel):
    """The model's reading of the whole list, as it claims it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rows: list[ProposalRowReading] = Field(default_factory=list, max_length=MAX_ROWS)


PROPOSAL_LIST_SPEC = ExtractionSpec(
    doc_type=DocumentType.PROPOSAL_LIST,
    prompt_id="supply_chain.extract_proposal_list",
    prompt_version="1.0.0",
    reading=ProposalListReading,
    kinds={},
)


class RowFinding(StrEnum):
    PROPOSAL_CODE_TAKEN = "proposal_code_taken"
    PROPOSAL_CODE_REPEATED = "proposal_code_repeated"
    ITEM_CODE_TAKEN = "item_code_taken"
    PRODUCT_SEEN = "product_seen"
    CATEGORY_UNKNOWN = "category_unknown"
    NO_PRODUCT_NAME = "no_product_name"


FINDING_WORDS: Mapping[RowFinding, str] = {
    RowFinding.PROPOSAL_CODE_TAKEN: "Mã đề xuất đã có hồ sơ trong workspace",
    RowFinding.PROPOSAL_CODE_REPEATED: "Mã đề xuất lặp lại trong danh sách",
    RowFinding.ITEM_CODE_TAKEN: "Mã hàng hoặc SKU đã cấp",
    RowFinding.PRODUCT_SEEN: "Tên sản phẩm trùng một hồ sơ đã có",
    RowFinding.CATEGORY_UNKNOWN: "AI gợi ý nhóm sản phẩm không có trong danh sách của công ty",
    RowFinding.NO_PRODUCT_NAME: "Máy không tìm thấy tên sản phẩm trong file",
}


@dataclass(frozen=True, slots=True)
class TakenCodes:
    """What the workspace already holds, for the codes and names a list names."""

    proposal_codes: frozenset[str] = frozenset()
    item_codes: frozenset[str] = frozenset()
    product_names: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ReviewedRow:
    index: int
    fields: Mapping[str, Mapping[str, str] | None]
    gaps: tuple[str, ...]
    category: str | None
    category_reason: str | None
    priority: str | None
    priority_reason: str | None
    findings: tuple[RowFinding, ...] = field(default=())

    def value(self, name: str) -> str | None:
        entry = self.fields.get(name)
        return None if entry is None else entry.get("value")

    def as_json(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "fields": {k: (None if v is None else dict(v)) for k, v in self.fields.items()},
            "gaps": list(self.gaps),
            "category": self.category,
            "category_reason": self.category_reason,
            "priority": self.priority,
            "priority_reason": self.priority_reason,
            "findings": [f.value for f in self.findings],
        }


def _code_key(code: str) -> str:
    return normalize(code)


def _reason(reason: str | None, text_numbers: set[Decimal]) -> str | None:
    """A suggestion's reason is shown only when it invents no number and
    holds nothing like an account number."""
    if reason is None or not reason.strip():
        return None
    if redact_identifiers(reason).count:
        return None
    if not {abs(n) for n in numbers_in(reason)} <= text_numbers:
        return None
    return reason.strip()


def wanted_codes(rows: Iterable[ReviewedRow]) -> tuple[list[str], list[str], list[str]]:
    """The proposal codes, item codes and product names a list's rows name:
    what the workspace is asked about, and nothing else."""
    proposal, item, names = [], [], []
    for row in rows:
        if (v := row.value("proposal_code")) is not None:
            proposal.append(v)
        if (v := row.value("item_code")) is not None:
            item.append(v)
        if (v := row.value("product_name")) is not None:
            names.append(v)
    return proposal, item, names


def ground_rows(
    reading: ProposalListReading, text: str, categories: Sequence[str]
) -> list[ReviewedRow]:
    """Each row grounded against the list's text, its suggestions checked
    against the tenant's Category keys and the three priorities."""
    grounded = ground(reading, PROPOSAL_LIST_SPEC, text)
    kept_rows: list[Any] = grounded.fields.get("rows", [])
    text_numbers = {abs(n) for n in numbers_in(normalize(text))}
    allowed = frozenset(categories)
    out: list[ReviewedRow] = []
    for index, row in enumerate(reading.rows):
        kept = kept_rows[index] if index < len(kept_rows) else {}
        fields: dict[str, Mapping[str, str] | None] = {
            name: kept.get(name) if isinstance(kept.get(name), Mapping) else None
            for name in ROW_FIELDS
        }
        prefix = f"rows[{index}]."
        gaps = tuple(
            sorted(
                {
                    gap.field[len(prefix) :]
                    for gap in grounded.gaps
                    if gap.field.startswith(prefix) and row_has_value(row, gap.field[len(prefix) :])
                }
            )
        )
        findings: list[RowFinding] = []
        category = (row.category.value or "").strip() or None
        if category is not None and category not in allowed:
            findings.append(RowFinding.CATEGORY_UNKNOWN)
            category = None
        priority = (row.priority.value or "").strip() or None
        if priority not in PRIORITIES:
            priority = None
        if fields["product_name"] is None:
            findings.append(RowFinding.NO_PRODUCT_NAME)
        out.append(
            ReviewedRow(
                index=index,
                fields=fields,
                gaps=gaps,
                category=category,
                category_reason=_reason(row.category.reason, text_numbers) if category else None,
                priority=priority,
                priority_reason=_reason(row.priority.reason, text_numbers) if priority else None,
                findings=tuple(findings),
            )
        )
    return out


def row_has_value(row: ProposalRowReading, name: str) -> bool:
    """A gap worth naming is a field the model said something about that the
    text did not prove; a field the list does not have is just empty."""
    cited = getattr(row, name, None)
    return isinstance(cited, Cited) and bool((cited.value or "").strip())


def with_findings(rows: Sequence[ReviewedRow], taken: TakenCodes) -> list[ReviewedRow]:
    """Each row with what the workspace and the list itself already hold."""
    seen: dict[str, int] = {}
    for row in rows:
        if (code := row.value("proposal_code")) is not None:
            seen[_code_key(code)] = seen.get(_code_key(code), 0) + 1
    taken_codes = {_code_key(c) for c in taken.proposal_codes}
    taken_items = {_code_key(c) for c in taken.item_codes}
    taken_names = {normalize(n) for n in taken.product_names}
    out: list[ReviewedRow] = []
    for row in rows:
        found = list(row.findings)
        code = row.value("proposal_code")
        if code is not None and _code_key(code) in taken_codes:
            found.append(RowFinding.PROPOSAL_CODE_TAKEN)
        if code is not None and seen.get(_code_key(code), 0) > 1:
            found.append(RowFinding.PROPOSAL_CODE_REPEATED)
        item = row.value("item_code")
        if item is not None and _code_key(item) in taken_items:
            found.append(RowFinding.ITEM_CODE_TAKEN)
        name = row.value("product_name")
        if name is not None and normalize(name) in taken_names:
            found.append(RowFinding.PRODUCT_SEEN)
        out.append(
            ReviewedRow(
                index=row.index,
                fields=row.fields,
                gaps=row.gaps,
                category=row.category,
                category_reason=row.category_reason,
                priority=row.priority,
                priority_reason=row.priority_reason,
                findings=tuple(dict.fromkeys(found)),
            )
        )
    return out


__all__ = [
    "FINDING_WORDS",
    "MAX_ROWS",
    "PRIORITIES",
    "PROPOSAL_LIST_SPEC",
    "ROW_FIELDS",
    "ProposalListReading",
    "ProposalRowReading",
    "ReviewedRow",
    "RowFinding",
    "Suggestion",
    "TakenCodes",
    "ground_rows",
    "wanted_codes",
    "with_findings",
]
