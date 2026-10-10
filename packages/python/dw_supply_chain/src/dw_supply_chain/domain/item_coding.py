"""Step 9 prepared by code (ticket ai-automation/13; ADR 0018, ADR 0027): the
item code a tenant's rule gives next, the SKUs a BM04's variants make, and
whether any of those codes is already taken.

What this module owns:

- **The code rule** (`ItemCodeRule`): a prefix, a separator and a number of
  digits for the item code; a separator and digits for each SKU under it. A
  template, never a regular expression a tenant types (ADR 0018 amendment 2:
  a tenant's regex needs its own ReDoS guard). No rule, no item code: code
  never guesses one, and a model is never asked.
- **The next item code** (`next_item_code`): one above the highest number
  already used with this prefix, in the application or in the imported
  catalogue; None when the digits run out.
- **The variants** (`bm04_variants`): one per line of the BM04's `variants`,
  its planned quantity only when the line ends with one written as a number
  after `:` or `=` ("Đỏ 24cm: 500 cái"). "24cm" is not a quantity.
- **What is taken** (`TakenCodes`, `taken_findings`): a code the application
  or the catalogue already holds is named, each with where it was found.
  Whether a code is free when it is written stays the database's answer.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_MAX_VARIANTS = 50
_BULLET = re.compile(r"^\s*(?:[-•*+]|\d+[.)])\s+")
_QUANTITY = re.compile(
    r"^(?P<label>.+?)\s*[:=]\s*(?P<qty>\d{1,3}(?:[.,\s]\d{3})+|\d+)\s*"
    r"(?:pcs|pc|cái|chiếc|sp|bộ|units?)?\.?\s*$",
    re.IGNORECASE,
)


class ItemCodeRule(BaseModel):
    """How a tenant's codes are made: `EL-00042`, then `EL-00042-01` for its
    first SKU."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    prefix: str = Field(pattern=r"^[A-Z0-9]{1,10}$")
    separator: Literal["-", ".", "/", ""] = "-"
    digits: int = Field(ge=3, le=8)
    sku_separator: Literal["-", ".", "/", ""] = "-"
    sku_digits: int = Field(ge=1, le=4)

    def item_code(self, number: int) -> str | None:
        text = str(number)
        if number < 1 or len(text) > self.digits:
            return None
        return f"{self.prefix}{self.separator}{text.zfill(self.digits)}"

    def number_of(self, code: str) -> int | None:
        """The number a code of this rule carries, or None for any other code."""
        head = f"{self.prefix}{self.separator}"
        tail = code[len(head) :] if code.startswith(head) else ""
        if len(tail) != self.digits or not tail.isdigit():
            return None
        return int(tail)

    def sku_code(self, item_code: str, index: int) -> str | None:
        text = str(index)
        if index < 1 or len(text) > self.sku_digits:
            return None
        return f"{item_code}{self.sku_separator}{text.zfill(self.sku_digits)}"


def next_item_code(rule: ItemCodeRule, used: Iterable[str]) -> str | None:
    """One above the highest number of this rule among `used` (the
    application's codes and the catalogue's); the first when there is none."""
    numbers = [n for n in (rule.number_of(code) for code in used) if n is not None]
    return rule.item_code(max(numbers, default=0) + 1)


@dataclass(frozen=True, slots=True)
class Variant:
    label: str
    planned_quantity: int | None


def bm04_variants(text: str | None) -> list[Variant]:
    """The BM04's variants, one per non-empty line, bullets dropped; the same
    label twice (case aside) is one variant. A quantity is read only from a
    number that ends the line after `:` or `=`."""
    variants: list[Variant] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        line = _BULLET.sub("", raw).strip()
        if not line:
            continue
        label, quantity = line, None
        matched = _QUANTITY.match(line)
        if matched is not None:
            digits = re.sub(r"[.,\s]", "", matched.group("qty"))
            quantity = int(digits) if int(digits) > 0 else None
            label = matched.group("label").strip()
        key = label.casefold()
        if not label or key in seen:
            continue
        seen.add(key)
        variants.append(Variant(label[:200], quantity))
        if len(variants) == _MAX_VARIANTS:
            break
    return variants


class Holder:
    """Where a taken code was found."""

    APP = "app"
    CATALOGUE = "catalogue"


@dataclass(frozen=True, slots=True)
class TakenCodes:
    """Each code already held, by kind, with where: `{code: (holder, ...)}`."""

    item_codes: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    sku_codes: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def digest(self) -> str:
        """A canonical text of what is taken, bound into a proposal's subject."""
        parts = [f"i:{c}:{','.join(sorted(h))}" for c, h in sorted(self.item_codes.items())] + [
            f"s:{c}:{','.join(sorted(h))}" for c, h in sorted(self.sku_codes.items())
        ]
        return ";".join(parts)


_HOLDER_WORDS = {Holder.APP: "đã cấp trong ứng dụng", Holder.CATALOGUE: "có trong danh mục đã nạp"}


def taken_message(kind: str, code: str, holders: Sequence[str]) -> str:
    where = ", ".join(_HOLDER_WORDS.get(h, h) for h in holders)
    return f"{kind} {code} {where}; đổi mã trước khi duyệt"


__all__ = [
    "Holder",
    "ItemCodeRule",
    "TakenCodes",
    "Variant",
    "bm04_variants",
    "next_item_code",
    "taken_message",
]
