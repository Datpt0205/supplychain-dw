"""Steps 13-15 checked by code (ticket ai-automation/17; ADR 0025).

The model reads the supplier's and the carrier's papers into cited fields;
this module decides what they are worth against the PO case:

- **QC by the numbers** (`qc_suggestion`): for each class of defect the report
  states both the count found and its acceptance number (Ac), a count above
  Ac suggests a fail; a pass is suggested only when every class the report
  counts is within its Ac. A report whose own words say PASS while a count
  exceeds its Ac is named: the suggestion follows the numbers, never the
  words. A report without the numbers suggests nothing (QC decides). The
  acceptance number is the report's; code does not look up the ISO 2859 table.
- **The shipment against the PO** (`line_findings` of `payment_check`, by
  SKU, quantities only) and the container a packing list names against the
  bill of lading's.
- **The customs file** (`CUSTOMS_FILE`): which of its papers are on the case.
  The list is what skill `supply_chain.customs_file` explains; a test holds
  each item to a line of the skill.
- **Dates**: an ETD later than the PO's expected delivery is named.

Findings carry no price.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.payment_check import SourceRead
from dw_supply_chain.domain.step_proposal import Finding

# Defect classes: (found field, accept field, words).
_CLASSES: tuple[tuple[str, str, str], ...] = (
    ("critical_found", "critical_accept", "lỗi nghiêm trọng"),
    ("major_found", "major_accept", "lỗi nặng"),
    ("minor_found", "minor_accept", "lỗi nhẹ"),
)

# The customs file of an imported shipment (skill `supply_chain.customs_file`):
# (paper, words, required). The C/O is needed only for a preferential tariff.
CUSTOMS_FILE: tuple[tuple[DocumentType, str, bool], ...] = (
    (DocumentType.PURCHASE_ORDER, "đơn đặt hàng", True),
    (DocumentType.COMMERCIAL_INVOICE, "hóa đơn thương mại", True),
    (DocumentType.PACKING_LIST, "packing list", True),
    (DocumentType.BILL_OF_LADING, "vận đơn", True),
    (DocumentType.ARRIVAL_NOTICE, "giấy báo hàng đến", True),
    (DocumentType.CERTIFICATE_OF_ORIGIN, "giấy chứng nhận xuất xứ", False),
)


@dataclass(frozen=True, slots=True)
class QcSuggestion:
    """What the numbers suggest (`pass`/`fail`, or None: they do not say),
    and what code found reading them."""

    verdict: str | None
    findings: tuple[Finding, ...]


def qc_suggestion(report: SourceRead) -> QcSuggestion:
    if not report.extracted:
        return QcSuggestion(None, ())
    found: list[Finding] = []
    over: list[str] = []
    counted = 0
    for found_name, accept_name, words in _CLASSES:
        count, accept = report.number(found_name), report.number(accept_name)
        if count is None or accept is None:
            continue
        counted += 1
        if count > accept:
            over.append(words)
            found.append(
                Finding(
                    "qc_over_aql",
                    found_name,
                    f"Số {words} vượt số chấp nhận (Ac) báo cáo ghi: gợi ý không đạt",
                )
            )
    if over:
        if report.value("stated_result") == "pass":
            found.append(
                Finding(
                    "qc_stated_differs",
                    "stated_result",
                    "Báo cáo ghi ĐẠT nhưng số lỗi vượt AQL: gợi ý theo số, không theo chữ",
                )
            )
        return QcSuggestion("fail", tuple(found))
    if counted == 0:
        return QcSuggestion(
            None,
            (
                Finding(
                    "qc_numbers_missing",
                    "qc_report",
                    "Báo cáo không ghi đủ số lỗi và số chấp nhận (Ac): hệ thống không gợi ý,"
                    " QC quyết",
                ),
            ),
        )
    return QcSuggestion("pass", ())


def container_findings(packing: SourceRead, *others: SourceRead) -> list[Finding]:
    """The packing list's container against the ones a carrier's paper names."""
    if not packing.extracted:
        return []
    container = _code(packing.value("container_number"))
    if container is None:
        return []
    found: list[Finding] = []
    for other in others:
        if not other.extracted:
            continue
        rows = other.fields.get("container_numbers")
        named = {
            c
            for c in (
                _code(r.get("value"))
                for r in (rows if isinstance(rows, list) else [])
                if isinstance(r, Mapping)
            )
            if c is not None
        }
        if named and container not in named:
            found.append(
                Finding(
                    "container_differs",
                    other.doc_type.value,
                    "Số container trên packing list không có trên chứng từ của hãng tàu",
                )
            )
    return found


def customs_findings(on_case: Iterable[DocumentType]) -> list[Finding]:
    """Each paper of the customs file not on the case."""
    present = set(on_case)
    return [
        Finding(
            "customs_missing" if required else "customs_optional_missing",
            doc_type.value,
            f"Hồ sơ hải quan chưa có {words}"
            + ("" if required else " (chỉ cần khi hưởng thuế suất ưu đãi)"),
        )
        for doc_type, words, required in CUSTOMS_FILE
        if doc_type not in present
    ]


def etd_findings(schedule: SourceRead, expected_delivery: date | None) -> list[Finding]:
    if not schedule.extracted or expected_delivery is None:
        return []
    raw = schedule.value("etd")
    if not isinstance(raw, str):
        return []
    try:
        etd = date.fromisoformat(raw)
    except ValueError:
        return []
    if etd > expected_delivery:
        return [
            Finding(
                "etd_late",
                "etd",
                "Ngày xuất hàng (ETD) trên lịch sản xuất muộn hơn ngày giao dự kiến của PO",
            )
        ]
    return []


def _code(raw: Any) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    return "".join(raw.split()).upper()


__all__ = [
    "CUSTOMS_FILE",
    "QcSuggestion",
    "container_findings",
    "customs_findings",
    "etd_findings",
    "qc_suggestion",
]
