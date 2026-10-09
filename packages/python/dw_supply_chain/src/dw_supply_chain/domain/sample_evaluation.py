"""A sample round judged criterion by criterion (steps 3-5; ticket
ai-automation/09).

R&D types what it measured; code compares; the model only words. What this
module owns:

- **The round's results** (`judge`): the newest value of each criterion of
  THIS round, compared by the criterion itself (`SampleCriterion.verdict`), an
  unmeasured criterion marked so, never passed.
- **The previous round's request, item by item** (`check_previous`): an item
  whose criterion now passes is fixed, one that still fails is open, one with
  no measurement this round (or no criterion of that name) cannot be checked.
- **What the model may cite** (`evidence`): the case, each criterion with its
  standard, value and verdict, each previous item with its status, the lab's
  report as read. Never a price.
- **What the model writes** (`EvaluationWriting`): a few notes for the record
  and one requirement per failed criterion for the revision request, each
  citing its evidence; code keeps what checks out (`domain.grounded_writing`)
  and a requirement only under the criterion it cites.
- **The drafts' fields**: the record's criteria table and the request's items
  are code's rows; a requirement nobody wrote that checks out stays an empty
  cell, which the draft names as a gap.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dw_supply_chain.domain.extraction import normalize
from dw_supply_chain.domain.grounded_writing import (
    CitedSentence,
    EvidenceItem,
    KeptSentence,
    ground_sentences,
)
from dw_supply_chain.domain.sample_criteria import SampleCriterion, Verdict

VERDICT_WORDS: Mapping[Verdict, str] = {
    Verdict.PASS: "Đạt",
    Verdict.FAIL: "Không đạt",
    Verdict.UNMEASURED: "Chưa đo",
}
MAX_NOTES = 5


@dataclass(frozen=True, slots=True)
class Measurement:
    id: uuid.UUID
    sample_round: int
    criterion: str
    value: str
    note: str | None
    entered_by: uuid.UUID
    entered_at: datetime


@dataclass(frozen=True, slots=True)
class CriterionResult:
    criterion: SampleCriterion
    value: str | None
    note: str | None
    verdict: Verdict

    @property
    def key(self) -> str:
        return f"criterion:{self.criterion.key}"

    @property
    def measured(self) -> str:
        if self.value is None:
            return ""
        if self.value in ("pass", "fail"):
            return VERDICT_WORDS[Verdict(self.value)]
        unit = f" {self.criterion.unit}" if self.criterion.unit else ""
        return f"{self.value}{unit}"


def judge(
    criteria: Sequence[SampleCriterion], measurements: Iterable[Measurement], sample_round: int
) -> list[CriterionResult]:
    newest: dict[str, Measurement] = {}
    for m in measurements:
        if m.sample_round != sample_round:
            continue
        if m.criterion not in newest or m.entered_at >= newest[m.criterion].entered_at:
            newest[m.criterion] = m
    out: list[CriterionResult] = []
    for criterion in criteria:
        found = newest.get(criterion.key)
        value = None if found is None else found.value
        out.append(
            CriterionResult(
                criterion=criterion,
                value=value,
                note=None if found is None else found.note,
                verdict=criterion.verdict(value),
            )
        )
    return out


def measurements_digest(results: Iterable[CriterionResult]) -> str:
    """What the round's results are, for a proposal's subject: a value
    entered after the proposal moves the subject."""
    return ";".join(f"{r.criterion.key}={r.value or ''}" for r in results)


class ItemStatus(StrEnum):
    FIXED = "fixed"
    OPEN = "open"
    UNCHECKED = "unchecked"


ITEM_WORDS: Mapping[ItemStatus, str] = {
    ItemStatus.FIXED: "đã sửa",
    ItemStatus.OPEN: "chưa sửa",
    ItemStatus.UNCHECKED: "không kiểm được",
}


@dataclass(frozen=True, slots=True)
class PreviousItem:
    index: int
    criterion: str
    requirement: str
    status: ItemStatus

    @property
    def key(self) -> str:
        return f"previous:{self.index}"


def check_previous(
    items: Sequence[Mapping[str, Any]], results: Sequence[CriterionResult]
) -> list[PreviousItem]:
    """Each item of the last request, against this round's results: matched
    by the criterion's label or key, as the request printed it."""
    by_name: dict[str, CriterionResult] = {}
    for result in results:
        by_name[normalize(result.criterion.label)] = result
        by_name[normalize(result.criterion.key)] = result
    out: list[PreviousItem] = []
    for index, item in enumerate(items):
        name = str(item.get("criterion") or "")
        match = by_name.get(normalize(name))
        if match is None or match.verdict is Verdict.UNMEASURED:
            status = ItemStatus.UNCHECKED
        elif match.verdict is Verdict.PASS:
            status = ItemStatus.FIXED
        else:
            status = ItemStatus.OPEN
        out.append(
            PreviousItem(
                index=index,
                criterion=name,
                requirement=str(item.get("requirement") or ""),
                status=status,
            )
        )
    return out


# ---------------------------------------------------------------- writing --


class RequirementWriting(BaseModel):
    """What the supplier is asked to change for one failed criterion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    criterion: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=600)
    cites: list[str] = Field(default_factory=list, max_length=6)


class EvaluationWriting(BaseModel):
    """The model's words for the record and the request, as it claims them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    notes: list[CitedSentence] = Field(default_factory=list, max_length=MAX_NOTES)
    requirements: list[RequirementWriting] = Field(default_factory=list, max_length=50)


@dataclass(frozen=True, slots=True)
class GroundedEvaluation:
    notes: tuple[KeptSentence, ...]
    # The kept requirement of each failed criterion, by criterion key.
    requirements: Mapping[str, KeptSentence]
    dropped: int


def ground_evaluation(
    writing: EvaluationWriting,
    evidence: Sequence[EvidenceItem],
    results: Sequence[CriterionResult],
) -> GroundedEvaluation:
    """The notes that check out, and for each FAILED criterion the first
    requirement that cites that criterion and checks out."""
    notes = ground_sentences(writing.notes, evidence)
    failed = {r.criterion.key for r in results if r.verdict is Verdict.FAIL}
    kept: dict[str, KeptSentence] = {}
    dropped = len(notes.dropped)
    for requirement in writing.requirements:
        key = requirement.criterion.removeprefix("criterion:")
        grounded = ground_sentences(
            [CitedSentence(text=requirement.text, cites=requirement.cites)], evidence
        )
        if (
            key not in failed
            or key in kept
            or not grounded.kept
            or f"criterion:{key}" not in grounded.kept[0].cites
        ):
            dropped += 1
            continue
        kept[key] = grounded.kept[0]
    return GroundedEvaluation(notes=notes.kept, requirements=kept, dropped=dropped)


def evidence(
    case_facts: str,
    results: Sequence[CriterionResult],
    previous: Sequence[PreviousItem],
    report: Iterable[tuple[uuid.UUID, Mapping[str, Any]]] = (),
) -> list[EvidenceItem]:
    """Everything the model may cite: the case, each criterion, each item of
    the last request, and the lab's report as the extraction lane read it
    (its kept fields only)."""
    items = [EvidenceItem("case", "Hồ sơ", case_facts)]
    for r in results:
        note = f"; ghi chú: {r.note}" if r.note else ""
        items.append(
            EvidenceItem(
                r.key,
                r.criterion.label,
                f"{r.criterion.label}: chuẩn {r.criterion.standard}; đo được "
                f"{r.measured or 'chưa đo'}; {VERDICT_WORDS[r.verdict]}{note}",
            )
        )
    for p in previous:
        items.append(
            EvidenceItem(
                p.key,
                "Yêu cầu vòng trước",
                f"{p.criterion}: {p.requirement} -> {ITEM_WORDS[p.status]}",
            )
        )
    for document_id, fields in report:
        parts = [
            f"{name}: {entry['value']}"
            for name, entry in fields.items()
            if isinstance(entry, Mapping) and isinstance(entry.get("value"), str)
        ]
        if parts:
            items.append(EvidenceItem(f"doc:{document_id}", "Báo cáo test", "; ".join(parts)))
    return items


def criteria_rows(results: Sequence[CriterionResult]) -> list[dict[str, str | None]]:
    """The record's criteria table: code's rows."""
    return [
        {
            "criterion": r.criterion.label,
            "standard": r.criterion.standard,
            "measured": r.measured or None,
            "result": None if r.verdict is Verdict.UNMEASURED else VERDICT_WORDS[r.verdict],
        }
        for r in results
    ]


def revision_rows(
    results: Sequence[CriterionResult], requirements: Mapping[str, KeptSentence]
) -> list[dict[str, str | None]]:
    """The request's items: one per failed criterion; the requirement only
    when the model wrote one that checks out, an empty cell otherwise."""
    return [
        {
            "criterion": r.criterion.label,
            "finding": f"Đo được {r.measured}, chuẩn {r.criterion.standard}",
            "requirement": None
            if r.criterion.key not in requirements
            else requirements[r.criterion.key].text,
        }
        for r in results
        if r.verdict is Verdict.FAIL
    ]


def notes_text(notes: Sequence[KeptSentence]) -> str | None:
    return " ".join(n.text for n in notes) or None


def suggested_outcome(results: Sequence[CriterionResult]) -> str | None:
    """What code's comparison suggests beside the empty conclusion: `revise`
    when a criterion fails, `pass` when every one passed, nothing while one
    is unmeasured. A suggestion only, never the value."""
    if any(r.verdict is Verdict.FAIL for r in results):
        return "revise"
    if results and all(r.verdict is Verdict.PASS for r in results):
        return "pass"
    return None


def iso(day: date) -> str:
    return day.isoformat()


__all__ = [
    "ITEM_WORDS",
    "VERDICT_WORDS",
    "CriterionResult",
    "EvaluationWriting",
    "GroundedEvaluation",
    "ItemStatus",
    "Measurement",
    "PreviousItem",
    "RequirementWriting",
    "check_previous",
    "criteria_rows",
    "evidence",
    "ground_evaluation",
    "judge",
    "measurements_digest",
    "notes_text",
    "revision_rows",
    "suggested_outcome",
]
