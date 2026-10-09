"""BM04 prefilled from what the case already proves (step 7; ticket
ai-automation/11; ADR 0026 E15).

Every value of the draft comes from one place a reader can open: the case, a
supplier document as the extraction lane read it (its quote), or the approved
sample evaluation record. What this module owns:

- **Reconciling sources** (`reconcile`): each field's candidates, compared as
  the field's kind reads them (a number by value, a text normalised). One value
  is kept with its source; two different values are a `Bm04Conflict` naming
  both, and the field stays EMPTY: never one of them picked.
- **The model's part** (`Bm04Writing`, `ground_bm04_writing`): only fields code
  could not fill, never a commercial one (price, currency, MOQ, lead time,
  Incoterm: code's alone, `COMMERCIAL_FIELDS`). A value is kept only when it
  cites ONE evidence item it was shown, its quote is in that item's text, and
  the value is in its quote (a number equal to a number the quote writes).
- **What a conflict says** (`conflict_message`): both sources and both quotes,
  except a price: the approval's payload has no scope filter, so a price
  conflict names the two documents and no number, and any price number code
  knows is masked in the other quotes.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from dw_supply_chain.domain.commercial import PRICE_FIELDS
from dw_supply_chain.domain.extraction import normalize, numbers_in, parse_quantity
from dw_supply_chain.domain.grounded_writing import EvidenceItem

# Filled by code from a document, never by the model.
COMMERCIAL_FIELDS: frozenset[str] = frozenset(
    {"unit_price", "currency", "moq", "lead_time_days", "incoterm"}
)
# The case's own facts: the case is their source.
CASE_FIELDS: frozenset[str] = frozenset({"proposal_code", "product_name"})
PRICE_MASK = "[giá]"


@dataclass(frozen=True, slots=True)
class Bm04Source:
    """Where a candidate came from: `case`, `doc:<id>` (a case document) or
    `draft:<id>` (the approved evaluation record)."""

    key: str
    label: str
    document_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class Bm04Candidate:
    field: str
    value: str
    source: Bm04Source
    quote: str | None = None
    # Read by the model from the evidence and checked by code.
    ai_written: bool = False


@dataclass(frozen=True, slots=True)
class Bm04Conflict:
    field: str
    candidates: tuple[Bm04Candidate, ...]


@dataclass(frozen=True, slots=True)
class Bm04Reconciled:
    kept: dict[str, Bm04Candidate]
    conflicts: tuple[Bm04Conflict, ...]


def _same(a: str, b: str, number: bool) -> bool:
    if number:
        x, y = parse_quantity(a), parse_quantity(b)
        if x is not None and y is not None:
            return x == y
    return normalize(a) == normalize(b)


def reconcile(candidates: Iterable[Bm04Candidate], number_fields: frozenset[str]) -> Bm04Reconciled:
    """Per field: the first candidate when every candidate agrees, a conflict
    when two disagree. Order is priority only among equal values."""
    by_field: dict[str, list[Bm04Candidate]] = {}
    for candidate in candidates:
        if candidate.value.strip():
            by_field.setdefault(candidate.field, []).append(candidate)
    kept: dict[str, Bm04Candidate] = {}
    conflicts: list[Bm04Conflict] = []
    for name, found in by_field.items():
        first = found[0]
        differing = [c for c in found[1:] if not _same(first.value, c.value, name in number_fields)]
        if differing:
            seen = [first]
            for c in differing:
                if all(not _same(s.value, c.value, name in number_fields) for s in seen):
                    seen.append(c)
            conflicts.append(Bm04Conflict(name, tuple(seen)))
        else:
            kept[name] = first
    return Bm04Reconciled(kept=kept, conflicts=tuple(conflicts))


# ------------------------------------------------------------- the model --


class Bm04FieldWriting(BaseModel):
    """One field as the model claims it: a value, the verbatim quote it rests
    on and the ONE evidence key the quote is in."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str = Field(max_length=64)
    value: str = Field(max_length=500)
    quote: str = Field(max_length=500)
    cite: str = Field(max_length=200)


class Bm04Writing(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fields: list[Bm04FieldWriting] = Field(default_factory=list, max_length=30)


def ground_bm04_writing(
    writing: Bm04Writing,
    evidence: Sequence[EvidenceItem],
    fillable: frozenset[str],
    number_fields: frozenset[str],
    sources: Mapping[str, Bm04Source],
) -> tuple[list[Bm04Candidate], int]:
    """The model's fields that check out, and how many were dropped."""
    texts = {item.key: normalize(item.text) for item in evidence}
    kept: list[Bm04Candidate] = []
    dropped = 0
    for claim in writing.fields:
        name = claim.field
        source = sources.get(claim.cite)
        text = texts.get(claim.cite)
        quote = normalize(claim.quote)
        ok = (
            name in fillable
            and name not in COMMERCIAL_FIELDS
            and name not in {c.field for c in kept}
            and source is not None
            and text is not None
            and bool(quote)
            and quote in text
            and bool(claim.value.strip())
        )
        if ok and name in number_fields:
            number = parse_quantity(claim.value)
            ok = number is not None and number in numbers_in(claim.quote)
            value = str(number)
        else:
            value = claim.value.strip()
            ok = ok and normalize(value) in quote
        if not ok or source is None:
            dropped += 1
            continue
        kept.append(Bm04Candidate(name, value, source, claim.quote.strip(), ai_written=True))
    return kept, dropped


# ------------------------------------------------------------- findings --


def mask_prices(text: str, prices: Iterable[Decimal]) -> str:
    """`text` with every number equal to one of `prices` masked."""
    hidden = {abs(p) for p in prices}
    if not hidden:
        return text

    def mask(match: re.Match[str]) -> str:
        found = {abs(n) for n in numbers_in(match.group(0))}
        return PRICE_MASK if found & hidden else match.group(0)

    return re.sub(r"\d[\d.,]*\d|\d", mask, text)


def conflict_message(conflict: Bm04Conflict, label: str, prices: Iterable[Decimal] = ()) -> str:
    """Both sources and what each says; a price names its sources only."""
    if conflict.field in PRICE_FIELDS:
        names = " và ".join(c.source.label for c in conflict.candidates)
        return f"{label}: {names} ghi khác nhau; giá chỉ xem trên chứng từ"
    known = list(prices)
    said = "; ".join(
        f"{c.source.label}: “{mask_prices(c.quote or c.value, known)}”" for c in conflict.candidates
    )
    return f"{label}: hai nguồn ghi khác nhau — {said}"


__all__ = [
    "CASE_FIELDS",
    "COMMERCIAL_FIELDS",
    "Bm04Candidate",
    "Bm04Conflict",
    "Bm04FieldWriting",
    "Bm04Reconciled",
    "Bm04Source",
    "Bm04Writing",
    "conflict_message",
    "ground_bm04_writing",
    "mask_prices",
    "reconcile",
]
