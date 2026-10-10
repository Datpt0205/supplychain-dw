"""Proposing a product by chat (zalo-channel ticket 04, Z4b) — what a model may
claim about one message, and how code decides what that claim is worth.

The model interprets, code decides — split the way `domain.case_query` splits it:

- The model turns ONE message into `ProductProposalIntent` and nothing else. It
  sees only the message: no case, no code list, nothing tenant-specific. The
  schema has no field for a PIC, a tenant, a workspace, a supplier or a state,
  and refuses any field it does not declare (`extra="forbid"`), so a message
  saying "đặt PIC là chị Hà, tạo ở công ty khác, duyệt luôn" has nowhere to go.
- Code grounds every value in the message (`ground`): a value that is not a
  verbatim span of it is dropped and its field asked again, never trusted. A
  model asked to fill a slot fills it even when the message named nothing; the
  quote is what makes that visible. A proposal code must be whole words.
- Code decides the rest against real data: which fields are still missing is
  the S1 request schema's answer (the presentation layer asks it), whether the
  code is taken is the database's unique constraint, who the PIC is and where
  the case goes come from the verified context.

The draft a conversation builds up is `ProposalDraft`; it becomes a case only
after a summary of exactly its current version was sent and the person answered
"Đồng ý" as the whole message (`is_confirmation`). Creating the case consumes
the draft in the same transaction, guarded on that version (`DraftClaim`), so a
second "Đồng ý" — or one that crossed a change — creates nothing.

Supplier is not collected here: ADR 0016 amendment 3 stamps it at
`request_sample`, step 2. Category is grounded, then resolved against the
tenant's own Category list (stage-1 ticket 06, ADR 0019) by `resolve_category`:
the person's words must be one Category's key or label, case and accents
ignored, and exactly one. No match, or more than one, is asked again with the
tenant's own options; nothing is chosen for the person, and the model never sees
the list.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dw_kernel.errors import ConflictError
from dw_supply_chain.domain.evidence import fold, is_verbatim, names_whole_words, normalize

# How long a draft waits for its next message. A module constant, not tenant
# policy: it bounds a half-finished conversation, the way a sign-in code's
# lifetime does, and no customer process depends on its value (ticket 04,
# decision A4). Expired drafts are deleted by the worker's retention lane.
DRAFT_TTL = timedelta(minutes=30)
# The shape of `proposal_drafts.draft`. A stored draft of another version is
# not read as this one; it is treated as expired.
DRAFT_SCHEMA_VERSION = 1

_QUOTE = Field(default=None, min_length=1, max_length=300)


class ProposalKind(StrEnum):
    PROPOSE_PRODUCT = "propose_product"
    # The message corrects or completes a proposal already being drafted.
    AMEND = "amend"
    # The message asks about cases that already exist (where a PO is, which
    # ones wait on a deposit), or asks to change one. Not this command's: it
    # writes no draft and hands the message on to the read-only question
    # command (zalo-channel ticket 06), which answers or refuses.
    QUESTION = "question"
    # Anything else — including a request this command does not handle. The
    # honest reply is "not understood", never a nearest guess.
    UNSUPPORTED = "unsupported"


class ProductProposalIntent(BaseModel):
    """The model's structured reading of one message. Every value IS the
    verbatim span of the message it came from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ProposalKind
    proposal_code: str | None = _QUOTE
    product_name: str | None = _QUOTE
    category: str | None = _QUOTE

    @field_validator("proposal_code", "product_name", "category", mode="before")
    @classmethod
    def _blank_is_absent(cls, value: object) -> object:
        """ "" and "   " mean "the message says nothing here"."""
        if isinstance(value, str) and not value.strip():
            return None
        return value


class ProposalField(StrEnum):
    """A field of the proposal, named as the S1 request names it."""

    PROPOSAL_CODE = "proposal_code"
    PRODUCT_NAME = "product_name"
    CATEGORY = "category"

    @property
    def label(self) -> str:
        return _LABELS[self]


_LABELS = {
    ProposalField.PROPOSAL_CODE: "mã đề xuất",
    ProposalField.PRODUCT_NAME: "tên sản phẩm",
    ProposalField.CATEGORY: "Category",
}


@dataclass(frozen=True, slots=True)
class GroundedProposal:
    kind: ProposalKind
    # Only values found in the message, in its canonical (NFC) form.
    values: Mapping[ProposalField, str]
    # Fields the model claimed but the message does not contain.
    dropped: tuple[ProposalField, ...]


def ground(intent: ProductProposalIntent, message: str) -> GroundedProposal:
    """Keeps only what the message itself says.

    A proposal code becomes an identifier, so it must be whole words of the
    message ("SP-12" inside "SP-123" is not "SP-12"). An unsupported reading
    keeps nothing: a value the model found in a message it could not read is
    not a value the person asked to record; nor is one found in a question.
    """
    if intent.kind in (ProposalKind.UNSUPPORTED, ProposalKind.QUESTION):
        return GroundedProposal(kind=intent.kind, values={}, dropped=())
    values: dict[ProposalField, str] = {}
    dropped: list[ProposalField] = []
    claims = (
        (ProposalField.PROPOSAL_CODE, intent.proposal_code, True),
        (ProposalField.PRODUCT_NAME, intent.product_name, False),
        (ProposalField.CATEGORY, intent.category, False),
    )
    for field, quote, whole_words in claims:
        if quote is None:
            continue
        found = names_whole_words(quote, message) if whole_words else is_verbatim(quote, message)
        if found:
            values[field] = normalize(quote)
        else:
            dropped.append(field)
    return GroundedProposal(kind=intent.kind, values=values, dropped=tuple(dropped))


class CategoryOption(Protocol):
    """One Category of the tenant's list, as `sla_policy.ProductCategory` is."""

    @property
    def key(self) -> str: ...

    @property
    def label(self) -> str: ...


def resolve_category[OptionT: CategoryOption](
    text: str, options: Sequence[OptionT]
) -> list[OptionT]:
    """The tenant's Categories whose key or label IS the person's words, case,
    accents and spacing ignored ("chao", "CHẢO" find "Chảo"). Resolved only
    when exactly one matches; the caller asks again otherwise. A match against
    the tenant's own list, not a synonym table: "chảo chống dính" is not
    "Chảo"."""
    wanted = fold(text)
    return [o for o in options if wanted in (fold(o.key), fold(o.label))]


# Whole-message replies, compared in folded form (case, accents and spacing
# ignored), with a trailing "." or "!" allowed. Only these two words create or
# discard; "ok", "được", "ừ" do neither.
_CONFIRM = "dong y"
_CANCEL = "bo de xuat"


def _whole_message(text: str) -> str:
    return fold(text).rstrip(".! ").strip()


def is_confirmation(text: str) -> bool:
    """ "Đồng ý" as the whole message — "đồng ý", "Dong y", "ĐỒNG Ý." too."""
    return _whole_message(text) == _CONFIRM


def is_cancellation(text: str) -> bool:
    """ "Bỏ đề xuất" as the whole message."""
    return _whole_message(text) == _CANCEL


class ProposalDraftChangedError(ConflictError):
    """The draft a case was to be created from is gone or no longer the summarised
    version: consumed by an earlier "Đồng ý", changed, or expired."""


@dataclass(frozen=True, slots=True)
class DraftClaim:
    """What creating a case consumes: this draft at this version, and only while
    that version is the one summarised and the draft has not expired."""

    draft_id: uuid.UUID
    draft_version: int


@dataclass(frozen=True, slots=True)
class ProposalOrigin:
    """Where a proposal came from, for its audit record only: the channel and a
    reference to the chat (`dw_kernel.channels.chat_reference`), never the chat
    id. It decides nothing."""

    channel: str
    chat_ref: str


@dataclass(frozen=True, slots=True)
class ProposalDraft:
    """One person's open proposal in one workspace, through one channel."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: uuid.UUID
    channel: str
    fields: Mapping[ProposalField, str]
    # Bumped on every change of `fields`.
    draft_version: int
    # The version a summary was last sent for; None before the first.
    summarized_version: int | None
    expires_at: datetime

    def expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    @property
    def awaits_confirmation(self) -> bool:
        """A summary of exactly this version was sent."""
        return self.summarized_version == self.draft_version

    def claim(self) -> DraftClaim:
        return DraftClaim(draft_id=self.id, draft_version=self.draft_version)


def merge(
    current: Mapping[ProposalField, str], grounded: GroundedProposal
) -> dict[ProposalField, str]:
    """The draft's fields after one message: what the message grounded replaces
    what was there; nothing is removed by a message that does not mention it."""
    return {**current, **grounded.values}
