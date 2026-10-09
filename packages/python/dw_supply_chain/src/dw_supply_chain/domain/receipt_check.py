"""Step 17 checked by code (ticket ai-automation/18; ADR 0025).

The warehouse counts what arrived, a person types each count; code compares
it, line by line, with what the supplier says it shipped (the packing list as
read) and with what the PO ordered. A count is never suggested into its field:
the shipped quantity sits beside the empty field, as every other physical
step's suggestion does.

One function answers "which lines differ, and by how much": the approval's
audit, the discrepancy report and the claim letter to the supplier all read
`discrepancies`, so the three can never disagree about a line.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from dw_kernel.errors import DomainError

# A count above this is a typing slip, not a delivery (no PO line here is
# near it); the table's CHECK holds the same bound.
MAX_COUNT = 10_000_000


@dataclass(frozen=True, slots=True)
class LineCount:
    """One PO line at the warehouse: what was ordered, what the packing list
    says was shipped (None: not read), what the warehouse counted."""

    sku_id: uuid.UUID
    sku_code: str
    ordered: int | None
    shipped: int | None
    counted: int

    @property
    def expected(self) -> int | None:
        """What the count is held to: the shipped quantity when the packing
        list states it, the ordered one otherwise."""
        return self.shipped if self.shipped is not None else self.ordered


@dataclass(frozen=True, slots=True)
class LineDiscrepancy:
    sku_code: str
    ordered: int | None
    shipped: int | None
    counted: int

    @property
    def difference(self) -> int:
        """Counted less expected: negative is short, positive is over."""
        held = self.shipped if self.shipped is not None else self.ordered
        return self.counted - (held or 0)

    def words(self) -> str:
        """The line as a person and a supplier read it (no price)."""
        parts = [f"SKU {self.sku_code}: PO {self.ordered if self.ordered is not None else '?'}"]
        parts.append(
            f"NCC giao {self.shipped}" if self.shipped is not None else "packing list không ghi"
        )
        parts.append(f"kho đếm {self.counted}")
        diff = self.difference
        parts.append(f"thiếu {-diff}" if diff < 0 else f"thừa {diff}")
        return ", ".join(parts)


@dataclass(frozen=True, slots=True)
class NewLineReceipt:
    """One PO line's count as the step records it (`po_case_line_receipts`,
    append-only): the SKU code, what was ordered and shipped stamped beside
    the count, so a later reader never re-derives them from a PO or a packing
    list that has since changed; `document_id` is the goods-received note
    filed with the step."""

    id: uuid.UUID
    po_case_id: uuid.UUID
    line: LineCount
    document_id: uuid.UUID | None


def counted_lines(
    lines: Sequence[tuple[uuid.UUID, str, int | None]],
    shipped: Mapping[str, int],
    counts: Mapping[uuid.UUID, int],
) -> list[LineCount]:
    """Every PO line with its count: a line without one, a count for no line
    of the PO, or a count out of range is refused (an empty count does not
    approve, ticket 18)."""
    known = {sku_id for sku_id, _, _ in lines}
    stray = sorted(str(s) for s in counts if s not in known)
    if stray:
        raise DomainError("số đếm cho dòng không có trong PO", details={"sku_ids": stray})
    missing = sorted(code for sku_id, code, _ in lines if sku_id not in counts)
    if missing:
        raise DomainError(
            "cần nhập số đếm cho mọi dòng của PO", details={"field": "counts", "skus": missing}
        )
    bad = sorted(code for sku_id, code, _ in lines if not 0 <= counts[sku_id] <= MAX_COUNT)
    if bad:
        raise DomainError("số đếm không hợp lệ", details={"field": "counts", "skus": bad})
    return [
        LineCount(
            sku_id=sku_id,
            sku_code=code,
            ordered=ordered,
            shipped=shipped.get(code),
            counted=counts[sku_id],
        )
        for sku_id, code, ordered in lines
    ]


def discrepancies(lines: Sequence[LineCount]) -> list[LineDiscrepancy]:
    """Each line whose count is not what was shipped (or, the packing list
    silent, what was ordered)."""
    return [
        LineDiscrepancy(line.sku_code, line.ordered, line.shipped, line.counted)
        for line in lines
        if line.counted != line.expected
    ]


__all__ = [
    "MAX_COUNT",
    "LineCount",
    "LineDiscrepancy",
    "NewLineReceipt",
    "counted_lines",
    "discrepancies",
]
