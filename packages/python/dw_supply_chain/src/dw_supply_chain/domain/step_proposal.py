"""A step proposal: AI prepares a step, a person approves the move (ADR 0025;
ticket ai-automation/05).

When a case enters a step the tenant's `supply_chain_step_preparation` policy
lists, a run prepares it (drafts, the readings of its source documents, code's
findings) and raises ONE approval: "move to step X with documents Y". The case
moves only when a person holding the duty of that step approves. What this
module owns:

- **Which steps can be proposed at all** (`PREPARABLE_ACTIONS`): forward steps
  a person takes whose only input is a document. A step that needs a reason, a
  supplier name or a code needs something a proposal cannot carry yet, and is
  refused when the policy loads, not discovered at the first decision.
- **The closed sets a policy names** (`DraftRecipe`, `StepCheck`): code that
  fills a draft and code that checks a step. The application holds one
  implementation per member and asserts it has them all.
- **What a preparation came to** (`PreparationOutcome`), one row per change in
  `step_preparations`: proposed, not prepared (with its reason), superseded,
  rejected, applied.
- **The subject version** an approval is stamped with and a decision must still
  find (`proposal_subject_version`): the case's version, each draft still the
  open latest version of its lineage with the same content, and each source
  still the newest document of its type. Anything that changes one of those is
  a new subject, so a decision on the old one is refused (409) and the proposal
  superseded.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.product_development_case import (
    COMMAND_ONLY_ACTIONS,
    GRAPH_ONLY_ACTIONS,
    PRODUCT_REASON_REQUIRED_ACTIONS,
    ProductAction,
    forward_step,
)

# Every approval a step proposal raises starts with this; the composition root
# makes it strict (a decision needs a comment, the requester cannot decide) and
# registers the subject-version port under it.
STEP_PROPOSAL_PREFIX = "supply_chain.step_proposal."
# The payload key naming the case: the one the product approvals use, so the
# brief, the case page and the subject port find a proposal the same way.
PROPOSAL_CASE_KEY = "product_dev_case_id"

# Forward steps whose only input is a document: what a proposal can move.
PREPARABLE_ACTIONS = frozenset(
    action
    for action in ProductAction
    if forward_step(action) is not None
    and action not in GRAPH_ONLY_ACTIONS
    and action not in COMMAND_ONLY_ACTIONS
    and action not in PRODUCT_REASON_REQUIRED_ACTIONS
    and action is not ProductAction.REQUEST_SAMPLE
)


def proposal_type(action: ProductAction) -> str:
    return f"{STEP_PROPOSAL_PREFIX}{action.value}"


class DraftRecipe(StrEnum):
    """How a draft's fields are filled. `case_facts`: the case's own fields
    (code, name, Category, supplier, round) and the cited readings of the
    step's source documents whose field has the template field's name; a
    result field a person types is never filled."""

    CASE_FACTS = "case_facts"


class StepCheck(StrEnum):
    """Code's checks of a step, each a finding when it fails."""

    # Every source document type the step names is on the case.
    SOURCES_PRESENT = "sources_present"
    # Every source was read into fields (not unreadable, refused or failed).
    SOURCES_READ = "sources_read"
    # The drafts have no gap besides the result a person types.
    DRAFTS_COMPLETE = "drafts_complete"


class PreparationOutcome(StrEnum):
    PROPOSED = "proposed"
    NOT_PREPARED = "not_prepared"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    APPLIED = "applied"


# The outcomes after which the lane prepares the same step entry again: a
# reason that may have passed, or a proposal that no longer stands. Never after
# a person said no, and never after the step was taken.
PREPARE_AGAIN_AFTER = frozenset(
    {PreparationOutcome.NOT_PREPARED, PreparationOutcome.SUPERSEDED, PreparationOutcome.PROPOSED}
)


class NotPreparedReason(StrEnum):
    """Why a step has no proposal, each a sentence on the case page."""

    CASE_MOVED = "case_moved"
    ACTION_DOCUMENT_MISSING = "action_document_missing"
    SOURCE_NOT_READ_YET = "source_not_read_yet"
    RESULT_FIELD_UNKNOWN = "result_field_unknown"
    RUN_REFUSED = "run_refused"
    RUN_FAILED = "run_failed"


@dataclass(frozen=True, slots=True)
class Finding:
    """What code found while preparing: a named code and the words a person
    reads. `subject` names the document type or field it is about."""

    code: str
    subject: str
    message: str

    def as_json(self) -> dict[str, str]:
        return {"code": self.code, "subject": self.subject, "message": self.message}


@dataclass(frozen=True, slots=True)
class SubjectDraft:
    """A draft as a proposal names it, and whether it is still that."""

    draft_id: uuid.UUID
    content_sha256: str
    # Still the open latest version of its lineage.
    open_latest: bool


@dataclass(frozen=True, slots=True)
class SubjectSource:
    """A source document type as a proposal read it: the newest document of
    that type on the case then (None: there was none)."""

    doc_type: DocumentType
    document_id: uuid.UUID | None


def proposal_subject_version(
    case_version: int, drafts: Iterable[SubjectDraft], sources: Iterable[SubjectSource]
) -> str:
    """The version an approval binds to. The same case, drafts and sources hash
    the same however they are listed; a draft edited (a new version), decided,
    or a source with a newer document hashes differently."""
    canonical = json.dumps(
        {
            "case_version": case_version,
            "drafts": sorted(
                [str(d.draft_id), d.content_sha256 if d.open_latest else "closed"] for d in drafts
            ),
            "sources": sorted(
                [s.doc_type.value, None if s.document_id is None else str(s.document_id)]
                for s in sources
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def newest_by_type(
    documents: Iterable[tuple[DocumentType, uuid.UUID, int]],
) -> Mapping[DocumentType, uuid.UUID]:
    """The newest document of each type, by version: (type, id, version) rows."""
    newest: dict[DocumentType, tuple[int, uuid.UUID]] = {}
    for doc_type, document_id, version in documents:
        if doc_type not in newest or version > newest[doc_type][0]:
            newest[doc_type] = (version, document_id)
    return {doc_type: pair[1] for doc_type, pair in newest.items()}


__all__ = [
    "PREPARABLE_ACTIONS",
    "PREPARE_AGAIN_AFTER",
    "PROPOSAL_CASE_KEY",
    "STEP_PROPOSAL_PREFIX",
    "DraftRecipe",
    "Finding",
    "NotPreparedReason",
    "PreparationOutcome",
    "StepCheck",
    "SubjectDraft",
    "SubjectSource",
    "newest_by_type",
    "proposal_subject_version",
    "proposal_type",
]
