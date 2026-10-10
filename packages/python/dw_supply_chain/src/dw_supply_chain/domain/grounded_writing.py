"""What a model may write in a draft, and which of its sentences a person reads
(ADR 0025 point 2; tickets ai-automation/07-10).

The model writes; code keeps. Split literally, as the daily brief's summary is
(`domain.brief_summary`):

- Code assembles the **evidence**: numbered items, each a key the model may
  cite (`case`, `doc:<id>`, `history:<id>`, `follow_up:<id>`, `criterion:<key>`
  ...), a label, and the text the model is shown. Only what the caller may
  read, of THIS case, under its tenant and workspace, ever becomes an item; a
  price only when the drafter may read prices. The whole set goes to the model
  as one untrusted `<input>` variable.
- The model writes sentences, each citing the keys it rests on
  (`CitedSentence`, a closed schema).
- Code keeps a sentence only when:
  - it cites at least one key, and every key it cites is an item of THIS
    evidence (a sentence citing another case's document cites a key that does
    not exist here);
  - every number it writes is a number one of its cited items writes (a date's
    parts count: `15/10/2026` is 15, 10 and 2026), so a total, a price or a
    deadline the evidence does not hold is never shown;
  - it holds nothing that reads as a bank account (`redact_identifiers` finds
    nothing to mask).

A sentence that fails is dropped, never repaired, and counted. Words without
figures are beyond what this can check, which is why every kept sentence is
shown as AI-written beside the items it cites.

Pure computation, no I/O and no model call.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from dw_supply_chain.domain.extraction import numbers_in, redact_identifiers

MAX_CITES = 6


class CitedSentence(BaseModel):
    """One sentence as the model claims it, with the evidence keys it rests on."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1, max_length=1200)
    cites: list[str] = Field(default_factory=list, max_length=MAX_CITES)


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """One thing the model is shown and may cite."""

    key: str
    label: str
    text: str

    def as_json(self) -> dict[str, str]:
        return {"key": self.key, "label": self.label, "text": self.text}


class DropReason(StrEnum):
    NO_CITATION = "no_citation"
    UNKNOWN_CITATION = "unknown_citation"
    NUMBER_NOT_IN_EVIDENCE = "number_not_in_evidence"
    ACCOUNT_NUMBER = "account_number"


@dataclass(frozen=True, slots=True)
class KeptSentence:
    text: str
    cites: tuple[str, ...]

    def as_json(self) -> dict[str, object]:
        return {"text": self.text, "cites": list(self.cites)}


@dataclass(frozen=True, slots=True)
class Dropped:
    index: int
    reason: DropReason

    def as_json(self) -> dict[str, object]:
        return {"index": self.index, "reason": self.reason.value}


@dataclass(frozen=True, slots=True)
class GroundedWriting:
    kept: tuple[KeptSentence, ...]
    dropped: tuple[Dropped, ...]


def _numbers(text: str) -> set[Decimal]:
    # A date's separators read as signs (`2026-10-15`): compare magnitudes.
    return {abs(n) for n in numbers_in(text)}


def _reason(sentence: CitedSentence, evidence: Mapping[str, EvidenceItem]) -> DropReason | None:
    cites = list(dict.fromkeys(sentence.cites))
    if not cites:
        return DropReason.NO_CITATION
    if any(key not in evidence for key in cites):
        return DropReason.UNKNOWN_CITATION
    if redact_identifiers(sentence.text).count:
        return DropReason.ACCOUNT_NUMBER
    allowed: set[Decimal] = set()
    for key in cites:
        allowed |= _numbers(evidence[key].text)
    if not _numbers(sentence.text) <= allowed:
        return DropReason.NUMBER_NOT_IN_EVIDENCE
    return None


def ground_sentences(
    sentences: Sequence[CitedSentence], evidence: Iterable[EvidenceItem]
) -> GroundedWriting:
    """Keeps the sentences that check out against `evidence`, in the model's
    order, and names why each other one was dropped."""
    by_key = {item.key: item for item in evidence}
    kept: list[KeptSentence] = []
    dropped: list[Dropped] = []
    for index, sentence in enumerate(sentences):
        reason = _reason(sentence, by_key)
        if reason is None:
            kept.append(
                KeptSentence(text=sentence.text.strip(), cites=tuple(dict.fromkeys(sentence.cites)))
            )
        else:
            dropped.append(Dropped(index, reason))
    return GroundedWriting(kept=tuple(kept), dropped=tuple(dropped))


def evidence_json(task: Mapping[str, object], items: Iterable[EvidenceItem]) -> str:
    """The task and the evidence as the prompt's one untrusted variable. The
    registry wraps and escapes it (ADR 0010): no value can close the block."""
    return json.dumps(
        {"task": dict(task), "evidence": [item.as_json() for item in items]},
        ensure_ascii=False,
    )


__all__ = [
    "MAX_CITES",
    "CitedSentence",
    "DropReason",
    "Dropped",
    "EvidenceItem",
    "GroundedWriting",
    "KeptSentence",
    "evidence_json",
    "ground_sentences",
]
