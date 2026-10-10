"""Step 10's purchase order drafted by code (ticket ai-automation/14; ADR 0017,
ADR 0026): which value each field of the PO takes, and the totals, computed
here and nowhere else.

- **Which value** (`po_terms`): what a person already set on the PO case wins
  (its terms, a line's price); otherwise the product's latest BM04 version,
  unless the supplier's confirmation (step 8's reading, compared term by
  term) states another value, which is a conflict and an empty field; a term
  only the confirmation states is taken from it. Payment terms, the deposit
  and the delivery date are a person's (or the case's): never guessed.
- **Totals** (`po_totals`): each line's quantity times its unit price, the
  order total, and the deposit from `deposit_percent`; unknown (None) when
  any input is, never zero. `stated_totals_differ` recomputes them from a
  draft's fields: a total anyone wrote that is not code's is named, and the
  approval refuses it.
- **What is missing** (`missing_terms`): each term and each line cell the
  PO still lacks, by name, so a person fills them before approving.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from dw_supply_chain.domain.supplier_terms import TermRow, TermStatus

# The terms a PO carries beside its lines, by draft field.
PO_TERMS: tuple[str, ...] = (
    "currency",
    "incoterm",
    "payment_terms",
    "deposit_percent",
    "delivery_date",
)
# The terms whose absence a PO is drafted with and a person fills.
REQUIRED_TERMS: tuple[str, ...] = ("currency", "payment_terms", "deposit_percent", "delivery_date")
TERM_WORDS: Mapping[str, str] = {
    "currency": "tiền tệ",
    "incoterm": "Incoterm",
    "payment_terms": "điều khoản thanh toán",
    "deposit_percent": "% đặt cọc",
    "delivery_date": "ngày giao dự kiến",
    "unit_price": "đơn giá",
    "quantity": "số lượng",
}


def decimal_text(value: Decimal) -> str:
    """A number as a draft holds it: no exponent, no trailing zeros, except
    one added to a value like `2.125`, which a document's number reader
    refuses as ambiguous with a thousands separator (`parse_number`)."""
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if re.fullmatch(r"-?\d{1,3}\.\d{3}", text):
        text += "0"
    return text


def to_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


@dataclass(frozen=True, slots=True)
class TermChoice:
    """One term's value and where it came from, or why it is empty."""

    value: str | None
    source: str  # "po_case" | "bm04" | "confirmation" | "conflict" | "none"


def po_terms(
    case_terms: Mapping[str, str | None],
    bm04: Mapping[str, str | None],
    reply: Sequence[TermRow],
) -> dict[str, TermChoice]:
    """Each PO term and the unit price, as `po_terms`' rule says: the case's
    own value; else the BM04's unless the confirmation differs (a conflict,
    empty); else the confirmation's own; else nothing."""
    rows = {row.field: row for row in reply}
    out: dict[str, TermChoice] = {}
    for name in (*PO_TERMS, "unit_price"):
        own = case_terms.get(name)
        if own not in (None, ""):
            out[name] = TermChoice(own, "po_case")
            continue
        row = rows.get(name)
        if row is not None and row.status is TermStatus.DIFFER:
            out[name] = TermChoice(None, "conflict")
            continue
        from_bm04 = bm04.get(name)
        if from_bm04 not in (None, ""):
            out[name] = TermChoice(from_bm04, "bm04")
            continue
        if row is not None and row.status is TermStatus.NOT_IN_BM04 and row.reply:
            out[name] = TermChoice(row.reply, "confirmation")
            continue
        out[name] = TermChoice(None, "none")
    return out


@dataclass(frozen=True, slots=True)
class PoTotals:
    line_totals: tuple[Decimal | None, ...]
    order_total: Decimal | None
    deposit_amount: Decimal | None


def po_totals(
    lines: Iterable[tuple[int | None, Decimal | None]], deposit_percent: Decimal | None
) -> PoTotals:
    """(quantity, unit price) per line, and the deposit %: every total code
    computes; a line missing either makes its total and the order's unknown."""
    line_totals = tuple(
        None if quantity is None or price is None else price * quantity for quantity, price in lines
    )
    known = [total for total in line_totals if total is not None]
    order_total = sum(known, Decimal(0)) if line_totals and len(known) == len(line_totals) else None
    deposit = (
        None
        if order_total is None or deposit_percent is None
        else order_total * deposit_percent / Decimal(100)
    )
    return PoTotals(line_totals, order_total, deposit)


def _value(fields: Mapping[str, Any], name: str) -> Any:
    entry = fields.get(name)
    return entry.get("value") if isinstance(entry, Mapping) else None


def _rows(fields: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = _value(fields, "lines")
    return [r for r in rows if isinstance(r, Mapping)] if isinstance(rows, list) else []


def _quantity(raw: Any) -> int | None:
    number = to_decimal(raw)
    if number is None or number != number.to_integral_value() or number < 1:
        return None
    return int(number)


def draft_totals(fields: Mapping[str, Any]) -> PoTotals:
    """The totals code computes from a draft's lines and deposit %."""
    return po_totals(
        ((_quantity(r.get("quantity")), to_decimal(r.get("unit_price"))) for r in _rows(fields)),
        to_decimal(_value(fields, "deposit_percent")),
    )


def stated_totals_differ(fields: Mapping[str, Any]) -> list[str]:
    """Each total a draft states that is not the one code computes from its
    own lines (a line's, the order's, the deposit), by field name. A total
    stated where code cannot compute one is a difference too."""
    totals = draft_totals(fields)
    differ: list[str] = []
    for index, (row, computed) in enumerate(zip(_rows(fields), totals.line_totals, strict=True)):
        stated = to_decimal(row.get("line_total"))
        if row.get("line_total") not in (None, "") and stated != computed:
            differ.append(f"lines[{index}].line_total")
    for name, computed in (
        ("order_total", totals.order_total),
        ("deposit_amount", totals.deposit_amount),
    ):
        raw = _value(fields, name)
        if raw not in (None, "") and to_decimal(raw) != computed:
            differ.append(name)
    return differ


def missing_terms(fields: Mapping[str, Any]) -> list[str]:
    """What the PO still lacks, by field: each required term, then each
    line's quantity and unit price."""
    missing = [name for name in REQUIRED_TERMS if _value(fields, name) in (None, "")]
    rows = _rows(fields)
    if not rows:
        missing.append("lines")
    for index, row in enumerate(rows):
        if _quantity(row.get("quantity")) is None:
            missing.append(f"lines[{index}].quantity")
        if to_decimal(row.get("unit_price")) is None:
            missing.append(f"lines[{index}].unit_price")
    return missing


def missing_words(name: str) -> str:
    base, _, rest = name.partition("[")
    if not rest:
        return TERM_WORDS.get(base, base)
    index, _, cell = rest.partition("].")
    return f"{TERM_WORDS.get(cell, cell)} dòng {int(index) + 1}"


__all__ = [
    "PO_TERMS",
    "REQUIRED_TERMS",
    "TERM_WORDS",
    "PoTotals",
    "TermChoice",
    "decimal_text",
    "draft_totals",
    "missing_terms",
    "missing_words",
    "po_terms",
    "po_totals",
    "stated_totals_differ",
    "to_decimal",
]
