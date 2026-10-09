"""A message to a supplier: AI drafts it, a person sends it (ADR 0029, E18;
ticket ai-automation/07).

What this module owns:

- **Why a message is drafted** (`MessagePurpose`): asking for a sample,
  reminding a supplier who is late, confirming the agreed product. Each is a
  template of `supply_chain_supplier_messages` (subject and reply window).
- **What the model writes**: body paragraphs only, each citing the evidence it
  rests on (`SupplierMessageWriting`). The subject, the greeting, the reply-by
  date and the closing are code's, from the template and the case; the model
  never chooses a recipient, an attachment or a date.
- **The message as a person copies it** (`compose`): the kept paragraphs
  between the template's greeting and closing, and its `content_sha256`, which
  "Đã gửi" records so a person says which text they sent.
- **Which follow-ups are the supplier's to answer** (`SUPPLIER_SIDE_*`): a PO
  supplier's silence, and a product case late at the sample a supplier sends.

No price reaches a message drafted without `supply_chain.commercial.read`:
the evidence the model is shown carries none (the application decides), and
a number not in the evidence is dropped here (`domain.grounded_writing`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from dw_supply_chain.domain.follow_up import FollowUpKind
from dw_supply_chain.domain.grounded_writing import CitedSentence, KeptSentence

MAX_PARAGRAPHS = 6


class MessagePurpose(StrEnum):
    SAMPLE_REQUEST = "sample_request"
    SUPPLIER_REMINDER = "supplier_reminder"
    SUPPLIER_CONFIRMATION = "supplier_confirmation"
    # The approved revision request, sent to the supplier (ticket
    # ai-automation/09).
    SAMPLE_REVISION_REQUEST = "sample_revision_request"
    # The weekly chase of a PO case in production (ticket ai-automation/17),
    # from the supplier's own schedule.
    PRODUCTION_PROGRESS = "production_progress"


class MessageStatus(StrEnum):
    # A body a person can copy.
    DRAFTED = "drafted"
    # The model's answer did not fit the schema, or no paragraph of it
    # checked out against the evidence: a person writes this one.
    REFUSED = "refused"
    # The call failed after the gateway's own retries.
    FAILED = "failed"


# A follow-up is the supplier's to answer: a PO supplier's silence, or a
# product case late at the milestone the supplier's sample closes.
SUPPLIER_SIDE_KINDS = frozenset({FollowUpKind.UPDATE_REMINDER, FollowUpKind.UPDATE_ESCALATION})
SUPPLIER_SIDE_MILESTONES = frozenset({"sample_collection"})


class SupplierMessageWriting(BaseModel):
    """The model's body, as it claims it: nothing is shown until grounded."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    paragraphs: list[CitedSentence] = Field(default_factory=list, max_length=MAX_PARAGRAPHS)


def reply_by(today: date, within_days: int) -> date:
    return today + timedelta(days=within_days)


def vn_date(day: date) -> str:
    return f"{day.day:02d}/{day.month:02d}/{day.year}"


@dataclass(frozen=True, slots=True)
class ComposedMessage:
    subject: str
    body: str
    content_sha256: str


def content_hash(subject: str, body: str, attachments: Sequence[str]) -> str:
    canonical = json.dumps(
        {"subject": subject, "body": body, "attachments": sorted(attachments)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compose(
    *,
    subject: str,
    greeting: str,
    paragraphs: Sequence[KeptSentence],
    reply_line: str,
    closing: str,
    attachments: Sequence[str] = (),
) -> ComposedMessage:
    """The message a person copies: code's greeting, the model's kept
    paragraphs, code's reply-by line and closing. No kept paragraph is no
    body: there is nothing AI wrote that checks out."""
    if not paragraphs:
        return ComposedMessage(subject, "", content_hash(subject, "", attachments))
    body = "\n\n".join([greeting, *(p.text for p in paragraphs), reply_line, closing])
    return ComposedMessage(subject, body, content_hash(subject, body, attachments))


__all__ = [
    "MAX_PARAGRAPHS",
    "SUPPLIER_SIDE_KINDS",
    "SUPPLIER_SIDE_MILESTONES",
    "ComposedMessage",
    "MessagePurpose",
    "MessageStatus",
    "SupplierMessageWriting",
    "compose",
    "content_hash",
    "reply_by",
    "vn_date",
]
