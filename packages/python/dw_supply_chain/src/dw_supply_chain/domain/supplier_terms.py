"""The supplier's confirmation compared with the BM04, term by term (step 8;
ticket ai-automation/12).

The reply is the extraction lane's reading of the email the supplier sent (each
term with its quote); the BM04 is the case's latest `product_profiles` version,
the one owner of the agreed product. Code decides each term:

- `match`: both say the same (a number by value, a text normalised; the
  specification matches when every part the BM04 states, its material and its
  dimensions, is in the reply's specification);
- `differ`: both say something, and not the same;
- `not_stated`: the BM04 states it, the reply does not. A sentence like "we
  agree to all terms" states no term: what is compared is what the reading kept,
  so a reply agreeing to everything with another price still DIFFERS on price;
- `not_in_bm04`: the reply states a term the BM04 leaves empty.

A price is compared like any term, but never printed: a row's values and a
finding's words name no price (`PRICE_FIELDS`), and any price number either
side wrote is masked in the other rows' words, because an approval's payload is
read without the commercial scope and its notification goes to Zalo.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from dw_supply_chain.domain.bm04_prefill import mask_prices
from dw_supply_chain.domain.commercial import PRICE_FIELDS, ProductProfile
from dw_supply_chain.domain.extraction import normalize, parse_quantity


class TermStatus(StrEnum):
    MATCH = "match"
    DIFFER = "differ"
    NOT_STATED = "not_stated"
    NOT_IN_BM04 = "not_in_bm04"


class TermKind(StrEnum):
    NUMBER = "number"
    TEXT = "text"
    SPECIFICATION = "specification"


@dataclass(frozen=True, slots=True)
class Term:
    field: str
    label: str
    kind: TermKind


# The terms compared, in the order a person reads them.
TERMS: tuple[Term, ...] = (
    Term("unit_price", "Đơn giá", TermKind.NUMBER),
    Term("currency", "Tiền tệ", TermKind.TEXT),
    Term("moq", "MOQ", TermKind.NUMBER),
    Term("lead_time_days", "Thời gian sản xuất (ngày)", TermKind.NUMBER),
    Term("specification", "Quy cách", TermKind.SPECIFICATION),
    Term("packaging", "Bao bì", TermKind.TEXT),
)
# What the BM04's specification is made of.
SPECIFICATION_PARTS: tuple[str, ...] = ("material", "dimensions")


@dataclass(frozen=True, slots=True)
class Bm04Terms:
    """The terms a BM04 version states, as text code compares."""

    profile_id: str
    values: Mapping[str, str | None]
    specification: tuple[str, ...]


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def bm04_terms(profile: ProductProfile) -> Bm04Terms:
    c, a = profile.commercial, profile.attributes
    return Bm04Terms(
        profile_id=str(profile.id),
        values={
            "unit_price": None if c.unit_price is None else str(c.unit_price),
            "currency": c.currency,
            "moq": None if c.moq is None else str(c.moq),
            "lead_time_days": None if c.lead_time_days is None else str(c.lead_time_days),
            "packaging": _text(a.get("packaging")),
        },
        specification=tuple(p for k in SPECIFICATION_PARTS if (p := _text(a.get(k)))),
    )


@dataclass(frozen=True, slots=True)
class TermRow:
    field: str
    label: str
    status: TermStatus
    bm04: str | None
    reply: str | None
    quote: str | None

    def as_json(self, prices: Sequence[Decimal]) -> dict[str, Any]:
        """The row as a payload carries it: a price's values left out, and
        any price number masked in another row's words (a currency quoted
        from the price line carries the price)."""
        hidden = self.field in PRICE_FIELDS

        def shown(text: str | None) -> str | None:
            return None if hidden or text is None else mask_prices(text, prices)

        return {
            "field": self.field,
            "label": self.label,
            "status": self.status.value,
            "bm04": shown(self.bm04),
            "reply": shown(self.reply),
            "quote": shown(self.quote),
        }


def _reply(fields: Mapping[str, Any], name: str) -> tuple[str | None, str | None]:
    entry = fields.get(name)
    if isinstance(entry, Mapping) and isinstance(entry.get("value"), str) and entry["value"]:
        quote = entry.get("quote")
        return entry["value"], quote if isinstance(quote, str) else None
    return None, None


def _same(term: Term, bm04: str, reply: str, specification: Sequence[str]) -> bool:
    if term.kind is TermKind.NUMBER:
        x, y = parse_quantity(bm04), parse_quantity(reply)
        return x is not None and x == y
    if term.kind is TermKind.SPECIFICATION:
        return all(normalize(part) in normalize(reply) for part in specification)
    return normalize(bm04) == normalize(reply)


def compare_terms(reply: Mapping[str, Any], bm04: Bm04Terms) -> list[TermRow]:
    rows: list[TermRow] = []
    for term in TERMS:
        theirs, quote = _reply(reply, term.field)
        ours = (
            "; ".join(bm04.specification) or None
            if term.kind is TermKind.SPECIFICATION
            else bm04.values.get(term.field)
        )
        if ours is None and theirs is None:
            continue
        if ours is None:
            status = TermStatus.NOT_IN_BM04
        elif theirs is None:
            status = TermStatus.NOT_STATED
        elif _same(term, ours, theirs, bm04.specification):
            status = TermStatus.MATCH
        else:
            status = TermStatus.DIFFER
        rows.append(TermRow(term.field, term.label, status, ours, theirs, quote))
    return rows


def prices_of(rows: Iterable[TermRow]) -> list[Decimal]:
    """Every price either side wrote: masked wherever words are shown."""
    found: list[Decimal] = []
    for row in rows:
        if row.field in PRICE_FIELDS:
            found.extend(p for v in (row.bm04, row.reply) if v and (p := parse_quantity(v)))
    return found


def term_message(row: TermRow, prices: Sequence[Decimal]) -> str:
    """What a person reads about a term that is not a match."""
    if row.status is TermStatus.NOT_STATED:
        return f"Thư NCC chưa nêu {row.label}"
    if row.field in PRICE_FIELDS:
        if row.status is TermStatus.NOT_IN_BM04:
            return f"{row.label}: thư NCC có nêu, BM04 chưa có; giá chỉ xem trên chứng từ"
        return f"{row.label}: thư NCC khác BM04; giá chỉ xem trên chứng từ"
    said = mask_prices(row.quote or row.reply or "", prices)
    if row.status is TermStatus.NOT_IN_BM04:
        return f"{row.label}: thư NCC ghi “{said}”, BM04 chưa có"
    return f"{row.label}: BM04 ghi “{mask_prices(row.bm04 or '', prices)}”, thư NCC ghi “{said}”"


__all__ = [
    "SPECIFICATION_PARTS",
    "TERMS",
    "Bm04Terms",
    "Term",
    "TermKind",
    "TermRow",
    "TermStatus",
    "bm04_terms",
    "compare_terms",
    "prices_of",
    "term_message",
]
