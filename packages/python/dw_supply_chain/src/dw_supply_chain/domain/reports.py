"""The weekly report for BGĐ, the supplier scorecard and the AI acceptance
metrics (ticket ai-automation/20).

Every number here is code's, counted from records this context keeps, and
each weekly figure carries the cases it counts, so a reader can open them.
The model only words a summary of the week, and `domain.grounded_writing`
keeps a sentence only when every number in it is one the figures it cites
write. Nothing here is a price.

- **The week** (`week_of`): Monday 00:00 to the next Monday 00:00, Vietnamese
  time, the week a day falls in.
- **The weekly figures** (`weekly_figures`): what moved in the week, from the
  history of both kinds of case, and what is open at the end.
- **The supplier scorecard** (`scorecard`): per supplier, its PO cases (all,
  open, completed), how often QC sent goods back, how its sample rounds
  closed, and how many lines the warehouse counted differently.
- **AI acceptance** (`acceptance`): per document type, how the drafts the
  lanes wrote (each lineage's version 1) ended: approved as written,
  approved after a person edited them, rejected, or still open; and an
  estimate of the minutes saved, from the tenant's own minutes per draft.

Pure computation, no I/O.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from dw_supply_chain.domain.daily_brief import VIETNAM
from dw_supply_chain.domain.grounded_writing import CitedSentence, EvidenceItem

# How many cases a figure names: enough to open, never the list itself.
CASES_SHOWN = 20
MAX_SUMMARY_SENTENCES = 5


def week_of(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day - timedelta(days=day.weekday()), datetime.min.time(), VIETNAM)
    return start, start + timedelta(days=7)


@dataclass(frozen=True, slots=True)
class CaseMove:
    """One history row of the week: the case's reference (a proposal code or a
    PO number), the action (product cases) and the state it moved to."""

    reference: str
    to_state: str
    action: str | None = None


@dataclass(frozen=True, slots=True)
class Figure:
    key: str
    label: str
    value: int
    cases: tuple[str, ...] = ()

    def evidence(self) -> EvidenceItem:
        named = f" ({', '.join(self.cases)})" if self.cases else ""
        return EvidenceItem(f"figure:{self.key}", self.label, f"{self.label}: {self.value}{named}")


def _figure(key: str, label: str, references: Iterable[str]) -> Figure:
    unique = tuple(dict.fromkeys(references))
    return Figure(key, label, len(unique), unique[:CASES_SHOWN])


# What a week's product history counts, by action.
_PRODUCT_ACTIONS: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("proposed", "Hồ sơ SP mới (đề xuất hoặc nạp)", frozenset({"propose", "import"})),
    ("samples_passed", "Mẫu đạt", frozenset({"pass_sample"})),
    ("revisions", "Mẫu cần chỉnh sửa", frozenset({"request_revision"})),
    ("samples_rejected", "Mẫu bị hủy", frozenset({"reject_sample"})),
    ("bod_approved", "BGĐ duyệt", frozenset({"bod_approve"})),
    ("ordered", "Đã đặt hàng", frozenset({"place_order"})),
)
# What a week's PO history counts, by the state a case moved to.
_PO_STATES: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("production_started", "PO vào sản xuất", frozenset({"production"})),
    ("qc_reworks", "QC trả hàng làm lại", frozenset({"rework"})),
    ("shipped", "PO đã xuất hàng", frozenset({"in_transit"})),
    ("received", "PO đã nhập kho xong", frozenset({"completed"})),
)


def weekly_figures(
    product_moves: Sequence[CaseMove],
    po_moves: Sequence[CaseMove],
    po_created: Sequence[str],
    open_products: Mapping[str, int],
    open_pos: Mapping[str, int],
) -> list[Figure]:
    figures = [
        _figure(key, label, (m.reference for m in product_moves if m.action in actions))
        for key, label, actions in _PRODUCT_ACTIONS
    ]
    figures.append(_figure("po_created", "PO mới", po_created))
    figures += [
        _figure(key, label, (m.reference for m in po_moves if m.to_state in states))
        for key, label, states in _PO_STATES
    ]
    figures.append(Figure("open_products", "Hồ sơ SP đang mở", sum(open_products.values())))
    figures.append(Figure("open_pos", "Hồ sơ PO đang mở", sum(open_pos.values())))
    return figures


# ------------------------------------------------------------- scorecard --


@dataclass(frozen=True, slots=True)
class SupplierRecord:
    """What the records say of one supplier, counted by the adapter."""

    supplier: str
    po_total: int = 0
    po_open: int = 0
    po_completed: int = 0
    qc_reworks: int = 0
    rounds_passed: int = 0
    rounds_revised: int = 0
    rounds_rejected: int = 0
    lines_differing: int = 0


@dataclass(frozen=True, slots=True)
class SupplierScore:
    record: SupplierRecord

    @property
    def sample_pass_rate(self) -> float | None:
        """Rounds passed of rounds closed; None before any closed."""
        r = self.record
        closed = r.rounds_passed + r.rounds_revised + r.rounds_rejected
        return None if closed == 0 else r.rounds_passed / closed


def scorecard(records: Iterable[SupplierRecord]) -> list[SupplierScore]:
    """Most POs first, then by name: an order, never a ranking by score."""
    return [
        SupplierScore(r) for r in sorted(records, key=lambda r: (-r.po_total, r.supplier.lower()))
    ]


# ------------------------------------------------------------ acceptance --


@dataclass(frozen=True, slots=True)
class DraftVersion:
    """One version of a draft and its decision, if any."""

    doc_type: str
    lineage_id: str
    version: int
    decision: str | None


@dataclass(frozen=True, slots=True)
class Acceptance:
    doc_type: str
    drafted: int
    as_is: int
    edited: int
    rejected: int
    open: int
    minutes_saved: int


def acceptance(
    versions: Iterable[DraftVersion],
    minutes_per_draft: Mapping[str, int],
    edited_credit: float,
) -> list[Acceptance]:
    """One row per document type the lanes drafted: each lineage counted once,
    by how it ended. Minutes saved: a draft approved as written saves the
    tenant's minutes for its type, one edited first saves `edited_credit` of
    them, a rejected or open one saves nothing."""
    lineages: dict[tuple[str, str], list[DraftVersion]] = defaultdict(list)
    for v in versions:
        lineages[(v.doc_type, v.lineage_id)].append(v)
    outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    for (doc_type, _), rows in lineages.items():
        confirmed = [r for r in rows if r.decision == "confirmed"]
        if confirmed:
            outcome = "as_is" if min(r.version for r in confirmed) == 1 else "edited"
        elif any(r.decision == "rejected" for r in rows):
            outcome = "rejected"
        else:
            outcome = "open"
        outcomes[doc_type][outcome] += 1
    out = []
    for doc_type in sorted(outcomes):
        c = outcomes[doc_type]
        minutes = minutes_per_draft.get(doc_type, 0)
        out.append(
            Acceptance(
                doc_type=doc_type,
                drafted=sum(c.values()),
                as_is=c["as_is"],
                edited=c["edited"],
                rejected=c["rejected"],
                open=c["open"],
                minutes_saved=round(minutes * (c["as_is"] + edited_credit * c["edited"])),
            )
        )
    return out


# --------------------------------------------------------------- summary --


class WeeklySummaryWriting(BaseModel):
    """The model's summary of the week as it claims it; nothing is shown
    until grounded against the figures it cites."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sentences: list[CitedSentence] = Field(default_factory=list, max_length=MAX_SUMMARY_SENTENCES)


__all__ = [
    "CASES_SHOWN",
    "Acceptance",
    "CaseMove",
    "DraftVersion",
    "Figure",
    "SupplierRecord",
    "SupplierScore",
    "WeeklySummaryWriting",
    "acceptance",
    "scorecard",
    "week_of",
    "weekly_figures",
]
