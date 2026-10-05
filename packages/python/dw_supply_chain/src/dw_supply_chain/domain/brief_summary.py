"""The daily brief's AI summary — what a model may say about a brief, and
which of its sentences a person gets to read.

The model interprets, code decides — split literally:
- The model reads the brief's groups (as data) and writes a few sentences for
  a manager, each citing the keys of the groups it is about
  (`BriefSummaryDraft`, a closed schema).
- Code keeps a sentence only if it checks out against the brief it cites:
  - every cited key is a group of THIS brief;
  - every number in the sentence is a count or a day figure of a cited
    group, or a digit inside one of its cases' PO reference or supplier name;
  - every PO reference or supplier name of the brief the sentence mentions
    belongs to a cited group.

A sentence that fails a check is dropped, never repaired; the reader is told
how many were. Words without figures ("đáng lo nhất", "nên xử lý trước") are
beyond what this can check, which is why the page shows every kept sentence
as AI-written, next to the groups it cites.

Pure computation, no I/O and no model call — `workflows.brief_summary` asks,
this module decides.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from dw_supply_chain.domain.daily_brief import (
    CHANGE_WINDOW_HOURS,
    BriefGroup,
    BriefSignal,
    DailyBrief,
)
from dw_supply_chain.domain.evidence import normalize

# A manager's morning read, not an essay.
MAX_SENTENCES = 5
_MAX_CITED_GROUPS = 4

_NUMBER = re.compile(r"\d+")


class BriefSummarySentenceDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1, max_length=300)
    group_keys: list[str] = Field(min_length=1, max_length=_MAX_CITED_GROUPS)


class BriefSummaryDraft(BaseModel):
    """The model's summary as it claims it — nothing here is shown until
    `ground_summary` has checked it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sentences: list[BriefSummarySentenceDraft] = Field(max_length=MAX_SENTENCES)


class BriefSummaryStatus(StrEnum):
    # At least one sentence survived.
    WRITTEN = "written"
    # The model wrote sentences and none survived.
    NOTHING_KEPT = "nothing_kept"
    # The model returned no sentence at all.
    NOTHING_WRITTEN = "nothing_written"
    # The brief had no group, so no model was asked.
    NOTHING_TO_SUMMARIZE = "nothing_to_summarize"
    # Every attempt came back outside the schema.
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class BriefSummarySentence:
    text: str
    group_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BriefSummary:
    status: BriefSummaryStatus
    sentences: tuple[BriefSummarySentence, ...] = ()
    # How many sentences the model wrote that failed a check.
    dropped: int = 0


def _mentions(text: str, name: str) -> bool:
    """`name` appears in `text` as whole words, ignoring case — "PO-12"
    is not mentioned by "PO-123"."""
    wanted = normalize(name).casefold()
    if not wanted:
        return False
    pattern = r"(?<!\w)" + re.escape(wanted) + r"(?!\w)"
    return re.search(pattern, text.casefold()) is not None


def _names(groups: Iterable[BriefGroup]) -> set[str]:
    return {
        name
        for group in groups
        for entry in group.entries
        for name in (entry.case.po_reference, entry.case.supplier_name)
    }


def _allowed_numbers(groups: Sequence[BriefGroup]) -> set[str]:
    allowed: set[str] = set()
    for group in groups:
        allowed.add(str(group.total))
        if group.signal is BriefSignal.CHANGED_RECENTLY:
            # "trong 24 giờ qua" is what that group means.
            allowed.add(str(CHANGE_WINDOW_HOURS))
        for entry in group.entries:
            allowed.update(str(v) for v in (entry.days, entry.limit_days) if v is not None)
            allowed.update(_NUMBER.findall(entry.case.po_reference))
            allowed.update(_NUMBER.findall(entry.case.supplier_name))
    return allowed


def _checks_out(
    sentence: BriefSummarySentenceDraft, brief: DailyBrief, every_name: set[str]
) -> bool:
    cited: list[BriefGroup] = []
    for key in dict.fromkeys(sentence.group_keys):
        group = brief.group(key)
        if group is None:
            return False
        cited.append(group)
    text = normalize(sentence.text)
    if not set(_NUMBER.findall(text)) <= _allowed_numbers(cited):
        return False
    # A name of the brief that none of the cited groups holds is a sentence
    # talking about one group while citing another.
    return not any(_mentions(text, name) for name in every_name - _names(cited))


def ground_summary(draft: BriefSummaryDraft, brief: DailyBrief) -> BriefSummary:
    """Keeps the sentences that check out against `brief`, in the model's
    order, and counts the ones that do not."""
    if not draft.sentences:
        return BriefSummary(status=BriefSummaryStatus.NOTHING_WRITTEN)
    every_name = _names(brief.groups)
    kept = tuple(
        BriefSummarySentence(
            text=normalize(sentence.text),
            group_keys=tuple(dict.fromkeys(sentence.group_keys)),
        )
        for sentence in draft.sentences
        if _checks_out(sentence, brief, every_name)
    )
    return BriefSummary(
        status=BriefSummaryStatus.WRITTEN if kept else BriefSummaryStatus.NOTHING_KEPT,
        sentences=kept,
        dropped=len(draft.sentences) - len(kept),
    )
