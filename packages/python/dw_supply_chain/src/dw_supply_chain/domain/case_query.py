"""Natural-language case query — what a model may claim about one
question, and how code decides what that claim is worth.

The model interprets, code decides — split literally:

- The model turns the question into `CaseQueryIntent` and nothing else. It
  sees only the question — never a supplier list, a case, or anything else
  tenant-specific.
- Code grounds every field in the question (`ground`): a field whose quote
  cannot be found verbatim in the question is dropped and reported, never
  trusted. A model asked to fill a slot fills it even when the question named
  nothing; the quote is what makes that visible.
- Code resolves the grounded mentions against the tenant's REAL data
  (`resolve_supplier`, and the repository's own lookup for a PO reference).
  Zero or several matches is reported back to the user, never guessed.

One rule runs through all of it: an answer is never broader than the
question. A supplier that does not resolve, a field that could not be
grounded, a grounded field the chosen kind of answer cannot apply — each one
stops the query instead of letting it run without that narrowing, because a
filter that silently falls away returns MORE than was asked for while reading
as if it had not. That is the fail-open shape.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dw_supply_chain.domain.evidence import is_verbatim, normalize
from dw_supply_chain.domain.po_case import CaseState

_QUOTE = Field(default=None, min_length=1, max_length=200)


class CaseQueryKind(StrEnum):
    LIST_CASES = "list_cases"
    OPEN_CASE = "open_case"
    # Anything else — including a question this feature does not answer yet.
    # The honest reply is "not understood", never a nearest guess.
    UNSUPPORTED = "unsupported"


class CaseQueryIntent(BaseModel):
    """The model's structured reading of one question. Every value is paired
    with (or is itself) the verbatim span of the question it came from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: CaseQueryKind
    supplier_mention: str | None = _QUOTE
    po_reference_mention: str | None = _QUOTE
    state: CaseState | None = None
    state_quote: str | None = _QUOTE
    active_only_quote: str | None = _QUOTE

    @field_validator(
        "supplier_mention",
        "po_reference_mention",
        "state",
        "state_quote",
        "active_only_quote",
        mode="before",
    )
    @classmethod
    def _blank_is_absent(cls, value: object) -> object:
        """ "" and "   " mean "the question says nothing here" — a model told
        to leave a field blank may send either instead of null, and refusing
        the whole reading over that would be refusing a correct answer."""
        if isinstance(value, str) and not value.strip():
            return None
        return value


class GroundedField(StrEnum):
    """One field of a reading, named. It keys a citation (the question's own
    words a field was grounded in, kept whatever the outcome) and a refusal's
    reason: a field that could not be grounded, or one that was grounded but
    that the chosen kind of answer cannot apply."""

    SUPPLIER = "supplier"
    PO_REFERENCE = "po_reference"
    STATE = "state"
    ACTIVE_ONLY = "active_only"


@dataclass(frozen=True, slots=True)
class GroundedQuery:
    kind: CaseQueryKind
    supplier_mention: str | None
    po_reference_mention: str | None
    state: CaseState | None
    active_only: bool
    # The question span behind each field that was kept — what the reply
    # shows as "understood from", so a person can see why a filter applied.
    citations: tuple[tuple[GroundedField, str], ...]
    dropped: tuple[GroundedField, ...]


def _names_whole_words(mention: str, question: str) -> bool:
    """A mention that becomes an identifier has to be whole words of the
    question, not part of one. A letter or digit next to it extends it
    ("PO-12" inside "PO-1234"), and so does a code separator ("-", "/", ".")
    followed by one ("PO-12" inside "PO-12-A", "PO-12/1", "PO-12.5"):
    resolving either would open a PO the question never named. A separator
    that ends the sentence ("PO-12.") does not extend it."""
    if not is_verbatim(mention, question):
        return False
    pattern = r"(?<!\w)(?<!\w[-/.])" + re.escape(normalize(mention)) + r"(?!\w)(?![-/.]\w)"
    return re.search(pattern, normalize(question)) is not None


def ground(intent: CaseQueryIntent, question: str) -> GroundedQuery:
    """Keeps only what the question itself says.

    A `state` with no quote, or a quote not in the question, is dropped
    exactly like a made-up supplier — the enum being closed only proves the
    value exists, not that it was asked for. A `state_quote` with no `state`
    is dropped too: the model saw the question narrow by a status it could
    not name, and answering without that narrowing would answer more.
    """
    dropped: list[GroundedField] = []
    citations: list[tuple[GroundedField, str]] = []

    def grounded(
        quote: str | None, field: GroundedField, *, claimed: bool, whole_words: bool = False
    ) -> bool:
        if not claimed:
            return False
        found = quote is not None and (
            _names_whole_words(quote, question) if whole_words else is_verbatim(quote, question)
        )
        if found and quote is not None:
            citations.append((field, quote))
            return True
        dropped.append(field)
        return False

    supplier = intent.supplier_mention
    po_reference = intent.po_reference_mention
    keep_supplier = grounded(
        supplier, GroundedField.SUPPLIER, claimed=supplier is not None, whole_words=True
    )
    keep_po = grounded(
        po_reference,
        GroundedField.PO_REFERENCE,
        claimed=po_reference is not None,
        whole_words=True,
    )
    state_claimed = intent.state is not None or intent.state_quote is not None
    keep_state = grounded(
        intent.state_quote if intent.state is not None else None,
        GroundedField.STATE,
        claimed=state_claimed,
    )
    active_only = grounded(
        intent.active_only_quote,
        GroundedField.ACTIVE_ONLY,
        claimed=intent.active_only_quote is not None,
    )
    return GroundedQuery(
        kind=intent.kind,
        supplier_mention=supplier if keep_supplier else None,
        po_reference_mention=po_reference if keep_po else None,
        state=intent.state if keep_state else None,
        active_only=active_only,
        citations=tuple(citations),
        dropped=tuple(dropped),
    )


def _fold(text: str) -> str:
    """Case-, accent- and spacing-insensitive form, for MATCHING only —
    "dong nai" finds "Đồng Nai". Canonical decomposition (NFD), not the
    compatibility kind: NFKD would turn "Sunhouse™" into "sunhousetm" and
    move the word boundary a whole-word match depends on. What gets used
    afterwards is always the stored name, never this form."""
    decomposed = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    unaccented = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(unaccented.casefold().split())


@dataclass(frozen=True, slots=True)
class SupplierResolution:
    """`name` is the one stored supplier name the mention resolved to, or
    `None`; then `candidates` tells the two failures apart — empty means no
    supplier matched, several means the mention was ambiguous."""

    name: str | None
    candidates: tuple[str, ...]


def resolve_supplier(mention: str, known_names: Iterable[str]) -> SupplierResolution:
    """One stored name for `mention`, or the reason there is not one.

    An equal name (after `_fold`) wins outright; failing that, a name that
    contains the mention as whole words ("sunhouse" in "Sunhouse Co.", never
    "sun" in "Sunhouse"). Exactly one match resolves; zero or several are
    returned as candidates for the user to see, never narrowed by a guess. A
    mention with no letter or digit names nothing, whatever it contains.
    """
    folded = _fold(mention)
    names = sorted(set(known_names))
    if not any(ch.isalnum() for ch in folded):
        return SupplierResolution(name=None, candidates=())
    equal = [name for name in names if _fold(name) == folded]
    if equal:
        return (
            SupplierResolution(name=equal[0], candidates=())
            if len(equal) == 1
            else SupplierResolution(name=None, candidates=tuple(equal))
        )
    whole_words = re.compile(rf"(?<!\w){re.escape(folded)}(?!\w)")
    containing = [name for name in names if whole_words.search(_fold(name))]
    if len(containing) == 1:
        return SupplierResolution(name=containing[0], candidates=())
    return SupplierResolution(name=None, candidates=tuple(containing))


class CaseQueryOutcome(StrEnum):
    """What the reply to one question turned out to be. The first two run a
    lookup; every other one is a refusal that says why, never a widened or
    guessed answer."""

    LIST = "list"
    OPEN = "open"
    NOT_UNDERSTOOD = "not_understood"
    SUPPLIER_NOT_FOUND = "supplier_not_found"
    SUPPLIER_AMBIGUOUS = "supplier_ambiguous"
    PO_REFERENCE_MISSING = "po_reference_missing"
    # Decided by the handler after its repository lookup, not by
    # `plan_case_query` — a PO reference resolves against rows, not a list.
    PO_NOT_FOUND = "po_not_found"
    PO_AMBIGUOUS = "po_ambiguous"


@dataclass(frozen=True, slots=True)
class CaseQueryPlan:
    """What code decided to do with one grounded question. `supplier_name`
    is always a STORED name (resolved), never the model's mention. `state`/
    `active_only` travel with a supplier refusal too, so a person picking
    among the candidates keeps the rest of what they asked."""

    outcome: CaseQueryOutcome
    state: CaseState | None = None
    supplier_name: str | None = None
    active_only: bool = False
    po_reference: str | None = None
    candidates: tuple[str, ...] = ()
    # Grounded fields this kind of answer could not apply — reported, and
    # the reason the reading was refused.
    unused: tuple[GroundedField, ...] = ()


def _unused_by(grounded: GroundedQuery) -> tuple[GroundedField, ...]:
    """Fields the question grounded that the chosen kind cannot apply. A
    list cannot narrow by one PO reference; opening one case applies no
    list filter. Answering anyway would present them as understood."""
    if grounded.kind is CaseQueryKind.LIST_CASES:
        return (GroundedField.PO_REFERENCE,) if grounded.po_reference_mention else ()
    present = (
        (GroundedField.SUPPLIER, grounded.supplier_mention is not None),
        (GroundedField.STATE, grounded.state is not None),
        (GroundedField.ACTIVE_ONLY, grounded.active_only),
    )
    return tuple(field for field, is_present in present if is_present)


def plan_case_query(grounded: GroundedQuery, known_suppliers: Iterable[str]) -> CaseQueryPlan:
    """The one decision both the handler and the eval graders run.

    `known_suppliers` is the caller's own tenant's stored names — the only
    names a supplier mention may become. A mention that resolves to none of
    them, or to several, stops the query: running it without that filter
    would answer MORE than was asked.
    """
    if grounded.kind is CaseQueryKind.UNSUPPORTED:
        return CaseQueryPlan(outcome=CaseQueryOutcome.NOT_UNDERSTOOD)
    # A field the model claimed but could not ground is one it believed the
    # question narrowed by. Answering without it would answer MORE than was
    # asked while reading as if it had not — so the whole reading is refused,
    # with `dropped` saying which part — a model's nonsense degrades to
    # "not understood", never to a broader guess.
    if grounded.dropped:
        return CaseQueryPlan(outcome=CaseQueryOutcome.NOT_UNDERSTOOD)
    if grounded.kind is CaseQueryKind.OPEN_CASE and grounded.po_reference_mention is None:
        return CaseQueryPlan(outcome=CaseQueryOutcome.PO_REFERENCE_MISSING)
    unused = _unused_by(grounded)
    if unused:
        return CaseQueryPlan(outcome=CaseQueryOutcome.NOT_UNDERSTOOD, unused=unused)
    if grounded.kind is CaseQueryKind.OPEN_CASE:
        return CaseQueryPlan(
            outcome=CaseQueryOutcome.OPEN, po_reference=grounded.po_reference_mention
        )

    supplier_name: str | None = None
    if grounded.supplier_mention is not None:
        resolution = resolve_supplier(grounded.supplier_mention, known_suppliers)
        if resolution.name is None:
            return CaseQueryPlan(
                outcome=(
                    CaseQueryOutcome.SUPPLIER_AMBIGUOUS
                    if resolution.candidates
                    else CaseQueryOutcome.SUPPLIER_NOT_FOUND
                ),
                state=grounded.state,
                active_only=grounded.active_only,
                candidates=resolution.candidates,
            )
        supplier_name = resolution.name
    return CaseQueryPlan(
        outcome=CaseQueryOutcome.LIST,
        state=grounded.state,
        supplier_name=supplier_name,
        active_only=grounded.active_only,
    )


def ignored_fields(grounded: GroundedQuery) -> tuple[GroundedField, ...]:
    """Fields the model claimed that the question gave no grounds for — one
    of a refusal's two reasons; `CaseQueryPlan.unused` (grounded, but not
    applicable to the chosen kind) is the other, and is reported apart.
    Nothing for an unsupported question: it is refused for being
    unsupported, and the fields it happened to carry are not the reason."""
    if grounded.kind is CaseQueryKind.UNSUPPORTED:
        return ()
    return grounded.dropped
