"""The case assistant's answer (ticket ai-automation/19): what a model may
cite to answer a question about ONE case, and which of its sentences a person
reads.

- **The evidence** is code's: the case's own facts, its history, the fields
  the extraction lane read from its documents, its BM04 and its sample
  measurements; each item a key the model may cite. What the asker may not
  read never becomes an item: a document's reading only for a holder of the
  document read, and a price (`PRICE_FIELDS`, the documents that print
  amounts, `PRICED_DOCUMENT_TYPES`) only where prices may be shown at all
  (the portal, to a holder of the commercial read; never a chat).
- **The writing** is the model's (`CaseAnswerWriting`: cited sentences), kept
  by `domain.grounded_writing`: every key cited is an item here, every number
  one its items write, no account number.
- **No answer** is an answer: nothing kept reads `NOT_ENOUGH_EVIDENCE`, never
  a guess.

Pure computation, no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dw_supply_chain.domain.case_document import CaseDocument
from dw_supply_chain.domain.commercial import PRICE_FIELDS, PRICED_DOCUMENT_TYPES, ProductProfile
from dw_supply_chain.domain.grounded_writing import CitedSentence, EvidenceItem
from dw_supply_chain.domain.po_case import CaseTransition, POCase
from dw_supply_chain.domain.product_development_case import (
    ProductCaseTransition,
    ProductDevelopmentCase,
)
from dw_supply_chain.domain.sample_evaluation import VERDICT_WORDS, CriterionResult

MAX_ANSWER_SENTENCES = 5
MAX_QUESTION = 500
NOT_ENOUGH_EVIDENCE = "Không đủ bằng chứng trong hồ sơ để trả lời câu này."
# A reading's total and its lines carry amounts beside quantities.
_PRICED_KEYS = PRICE_FIELDS | {"total"}


class CaseAnswerWriting(BaseModel):
    """The model's answer as it claims it; nothing is shown until grounded."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sentences: list[CitedSentence] = Field(default_factory=list, max_length=MAX_ANSWER_SENTENCES)


def po_case_item(case: POCase) -> EvidenceItem:
    lines = "; ".join(
        f"{line.sku_code or 'SKU'} x {line.quantity if line.quantity is not None else '?'}"
        for line in case.lines
    )
    shipping = case.shipping
    parts = [
        f"Số PO {case.po_reference or 'chưa có'}",
        f"NCC {case.supplier_name}",
        f"bước {case.state.value}",
        f"dòng hàng: {lines or 'chưa có'}",
    ]
    if shipping.etd is not None:
        parts.append(f"ETD {shipping.etd.isoformat()}")
    if shipping.eta is not None:
        parts.append(f"ETA {shipping.eta.isoformat()}")
    return EvidenceItem("case", "Hồ sơ PO", "; ".join(parts))


def product_case_item(case: ProductDevelopmentCase) -> EvidenceItem:
    parts = [
        f"Mã đề xuất {case.proposal_code}",
        f"sản phẩm {case.product_name}",
        f"nhóm {case.category}",
        f"bước {case.state.value}",
        f"NCC {case.supplier_name or 'chưa có'}",
        f"vòng mẫu {case.sample_round}",
    ]
    return EvidenceItem("case", "Hồ sơ phát triển sản phẩm", "; ".join(parts))


def history_items(
    transitions: Iterable[CaseTransition | ProductCaseTransition],
) -> list[EvidenceItem]:
    """Newest first, as the case page reads them."""
    items = []
    for index, t in enumerate(transitions):
        before = "bắt đầu" if t.from_state is None else t.from_state.value
        action = getattr(t, "action", None)
        step = f" ({action.value})" if action is not None else ""
        reason = f"; lý do: {t.reason}" if t.reason else ""
        items.append(
            EvidenceItem(
                f"history:{index}",
                "Lịch sử",
                f"{t.occurred_at.date().isoformat()}: {before} -> {t.to_state.value}{step}{reason}",
            )
        )
    return items


def _value_text(value: Any, prices: bool) -> str | None:
    if isinstance(value, Mapping):
        inner = value.get("value")
        return inner if isinstance(inner, str) and inner.strip() else None
    if isinstance(value, list):
        rows = []
        for row in value:
            if not isinstance(row, Mapping):
                continue
            cells = [
                f"{k}: {t}"
                for k, v in row.items()
                if (prices or k not in _PRICED_KEYS) and (t := _value_text(v, prices)) is not None
            ]
            if cells:
                rows.append(", ".join(cells))
        return " | ".join(rows) or None
    return None


def reading_item(
    document: CaseDocument, fields: Mapping[str, Any], *, prices: bool
) -> EvidenceItem | None:
    """A document's fields as the extraction lane kept them (quoted values
    only; code's own digests are never shown). A document that prints amounts
    is left out entirely where prices may not be shown."""
    if not prices and document.doc_type in PRICED_DOCUMENT_TYPES:
        return None
    parts = [
        f"{name}: {text}"
        for name, value in fields.items()
        if not name.startswith("_")
        and (prices or name not in _PRICED_KEYS)
        and (text := _value_text(value, prices)) is not None
    ]
    if not parts:
        return None
    return EvidenceItem(
        f"doc:{document.id}",
        f"{document.doc_type.value} v{document.version}",
        "; ".join(parts),
    )


def profile_item(profile: ProductProfile, *, prices: bool) -> EvidenceItem:
    shown = profile if prices else profile.without_prices()
    commercial = shown.commercial
    parts = [
        f"{name}: {value}"
        for name, value in sorted(shown.attributes.items())
        if isinstance(value, str | int | float) and str(value).strip()
    ]
    for name, value in (
        ("moq", commercial.moq),
        ("lead_time_days", commercial.lead_time_days),
        ("incoterm", commercial.incoterm),
        ("currency", commercial.currency),
        ("unit_price", commercial.unit_price),
    ):
        if value is not None:
            parts.append(f"{name}: {getattr(value, 'value', value)}")
    # The name is in the text too: a sentence saying "BM04 ghi ..." writes
    # its digits, which must be ones the item writes.
    return EvidenceItem(
        "bm04",
        f"BM04 phiên bản {profile.version}",
        f"BM04 phiên bản {profile.version}: " + "; ".join(parts),
    )


def criterion_items(results: Sequence[CriterionResult]) -> list[EvidenceItem]:
    return [
        EvidenceItem(
            r.key,
            r.criterion.label,
            f"{r.criterion.label}: chuẩn {r.criterion.standard}; đo được "
            f"{r.measured or 'chưa đo'}; {VERDICT_WORDS[r.verdict]}",
        )
        for r in results
    ]


__all__ = [
    "MAX_ANSWER_SENTENCES",
    "MAX_QUESTION",
    "NOT_ENOUGH_EVIDENCE",
    "CaseAnswerWriting",
    "criterion_items",
    "history_items",
    "po_case_item",
    "product_case_item",
    "profile_item",
    "reading_item",
]
