"""Natural-language case query — what a model may claim about one
question, and how code decides what that claim is worth.

Two kinds of case are asked about: PO cases (list, open by PO number) and,
since stage-1 ticket 08, product-development cases (list by state, PIC or
Category; open by proposal code). Each kind of answer applies only its own
fields (`_APPLIES`); a field of the other kind is reported as unusable and
the reading refused, never quietly dropped.

The model interprets, code decides — split literally:

- The model turns the question into `CaseQueryIntent` and nothing else. It
  sees only the question — never a supplier list, a case, or anything else
  tenant-specific.
- Code grounds every field in the question (`ground`): a field whose quote
  cannot be found verbatim in the question is dropped and reported, never
  trusted. A model asked to fill a slot fills it even when the question named
  nothing; the quote is what makes that visible.
- Code resolves the grounded mentions against the tenant's REAL data
  (`resolve_name` for a supplier or a person of the workspace,
  `product_proposal.resolve_category` for a Category, and the repository's
  own lookup for a PO reference or a proposal code). Zero or several matches
  is reported back to the user, never guessed.

One rule runs through all of it: an answer is never broader than the
question. A supplier that does not resolve, a field that could not be
grounded, a grounded field the chosen kind of answer cannot apply — each one
stops the query instead of letting it run without that narrowing, because a
filter that silently falls away returns MORE than was asked for while reading
as if it had not. That is the fail-open shape.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dw_supply_chain.domain.evidence import fold, is_verbatim, names_whole_words
from dw_supply_chain.domain.po_case import CaseState
from dw_supply_chain.domain.product_development_case import ProductDevState
from dw_supply_chain.domain.product_proposal import CategoryOption, resolve_category

_QUOTE = Field(default=None, min_length=1, max_length=200)


class CaseQueryKind(StrEnum):
    LIST_CASES = "list_cases"
    OPEN_CASE = "open_case"
    # Product-development cases (Hồ sơ phát triển sản phẩm), stage 1.
    LIST_PRODUCT_CASES = "list_product_cases"
    OPEN_PRODUCT_CASE = "open_product_case"
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
    # Product-development cases. A state of the product case's own enum,
    # never a PO state: the two lists share words ("Đã hủy") but not meaning.
    proposal_code_mention: str | None = _QUOTE
    product_state: ProductDevState | None = None
    product_state_quote: str | None = _QUOTE
    category_mention: str | None = _QUOTE
    # A person named as the PIC, as typed; or the asker's own words for
    # "mine" ("của tôi"), which code turns into the asker, never a name.
    pic_mention: str | None = _QUOTE
    mine_quote: str | None = _QUOTE

    @field_validator(
        "supplier_mention",
        "po_reference_mention",
        "state",
        "state_quote",
        "active_only_quote",
        "proposal_code_mention",
        "product_state",
        "product_state_quote",
        "category_mention",
        "pic_mention",
        "mine_quote",
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
    PROPOSAL_CODE = "proposal_code"
    PRODUCT_STATE = "product_state"
    CATEGORY = "category"
    PIC = "pic"
    MINE = "mine"


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
    proposal_code_mention: str | None = None
    product_state: ProductDevState | None = None
    category_mention: str | None = None
    pic_mention: str | None = None
    mine: bool = False

    def present(self) -> frozenset[GroundedField]:
        """The fields this reading kept."""
        values = {
            GroundedField.SUPPLIER: self.supplier_mention is not None,
            GroundedField.PO_REFERENCE: self.po_reference_mention is not None,
            GroundedField.STATE: self.state is not None,
            GroundedField.ACTIVE_ONLY: self.active_only,
            GroundedField.PROPOSAL_CODE: self.proposal_code_mention is not None,
            GroundedField.PRODUCT_STATE: self.product_state is not None,
            GroundedField.CATEGORY: self.category_mention is not None,
            GroundedField.PIC: self.pic_mention is not None,
            GroundedField.MINE: self.mine,
        }
        return frozenset(field for field, kept in values.items() if kept)


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
            names_whole_words(quote, question) if whole_words else is_verbatim(quote, question)
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
    code = intent.proposal_code_mention
    keep_code = grounded(
        code, GroundedField.PROPOSAL_CODE, claimed=code is not None, whole_words=True
    )
    keep_product_state = grounded(
        intent.product_state_quote if intent.product_state is not None else None,
        GroundedField.PRODUCT_STATE,
        claimed=intent.product_state is not None or intent.product_state_quote is not None,
    )
    category = intent.category_mention
    keep_category = grounded(
        category, GroundedField.CATEGORY, claimed=category is not None, whole_words=True
    )
    pic = intent.pic_mention
    keep_pic = grounded(pic, GroundedField.PIC, claimed=pic is not None, whole_words=True)
    mine = grounded(intent.mine_quote, GroundedField.MINE, claimed=intent.mine_quote is not None)
    return GroundedQuery(
        kind=intent.kind,
        supplier_mention=supplier if keep_supplier else None,
        po_reference_mention=po_reference if keep_po else None,
        state=intent.state if keep_state else None,
        active_only=active_only,
        citations=tuple(citations),
        dropped=tuple(dropped),
        proposal_code_mention=code if keep_code else None,
        product_state=intent.product_state if keep_product_state else None,
        category_mention=category if keep_category else None,
        pic_mention=pic if keep_pic else None,
        mine=mine,
    )


@dataclass(frozen=True, slots=True)
class NameResolution:
    """`name` is the one stored name (a supplier's, a person's) the mention
    resolved to, or `None`; then `candidates` tells the two failures apart —
    empty means nothing matched, several means the mention was ambiguous."""

    name: str | None
    candidates: tuple[str, ...]


def resolve_name(mention: str, known_names: Iterable[str]) -> NameResolution:
    """One stored name for `mention`, or the reason there is not one.

    An equal name (after `fold`) wins outright; failing that, a name that
    contains the mention as whole words ("sunhouse" in "Sunhouse Co.", never
    "sun" in "Sunhouse"). Exactly one match resolves; zero or several are
    returned as candidates for the user to see, never narrowed by a guess. A
    mention with no letter or digit names nothing, whatever it contains.
    """
    folded = fold(mention)
    names = sorted(set(known_names))
    if not any(ch.isalnum() for ch in folded):
        return NameResolution(name=None, candidates=())
    equal = [name for name in names if fold(name) == folded]
    if equal:
        return (
            NameResolution(name=equal[0], candidates=())
            if len(equal) == 1
            else NameResolution(name=None, candidates=tuple(equal))
        )
    whole_words = re.compile(rf"(?<!\w){re.escape(folded)}(?!\w)")
    containing = [name for name in names if whole_words.search(fold(name))]
    if len(containing) == 1:
        return NameResolution(name=containing[0], candidates=())
    return NameResolution(name=None, candidates=tuple(containing))


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
    # Product-development cases. The two lookups run; the rest refuse.
    PRODUCT_LIST = "product_list"
    PRODUCT_OPEN = "product_open"
    PROPOSAL_CODE_MISSING = "proposal_code_missing"
    CATEGORY_NOT_FOUND = "category_not_found"
    CATEGORY_AMBIGUOUS = "category_ambiguous"
    PIC_NOT_FOUND = "pic_not_found"
    PIC_AMBIGUOUS = "pic_ambiguous"
    # Decided by the handler after its lookup, as for a PO reference.
    PRODUCT_NOT_FOUND = "product_not_found"
    PRODUCT_AMBIGUOUS = "product_ambiguous"


@dataclass(frozen=True, slots=True)
class CaseQueryPlan:
    """What code decided to do with one grounded question. `supplier_name`
    is always a STORED name (resolved), never the model's mention; so are
    `category` (a key of the tenant's list) and `pic_user_id` (a member of
    the workspace, or the asker). `state`/`active_only` travel with a
    supplier refusal too, so a person picking among the candidates keeps the
    rest of what they asked."""

    outcome: CaseQueryOutcome
    state: CaseState | None = None
    supplier_name: str | None = None
    active_only: bool = False
    po_reference: str | None = None
    candidates: tuple[str, ...] = ()
    # Grounded fields this kind of answer could not apply — reported, and
    # the reason the reading was refused.
    unused: tuple[GroundedField, ...] = ()
    product_state: ProductDevState | None = None
    category: str | None = None
    pic_user_id: uuid.UUID | None = None
    proposal_code: str | None = None


# Which fields each kind of answer applies. A grounded field outside its
# kind's set is one the answer cannot honour: a PO list cannot narrow by a
# Category, opening one case applies no list filter. Answering anyway would
# present it as understood.
_APPLIES: dict[CaseQueryKind, frozenset[GroundedField]] = {
    CaseQueryKind.LIST_CASES: frozenset(
        {GroundedField.SUPPLIER, GroundedField.STATE, GroundedField.ACTIVE_ONLY}
    ),
    CaseQueryKind.OPEN_CASE: frozenset({GroundedField.PO_REFERENCE}),
    CaseQueryKind.LIST_PRODUCT_CASES: frozenset(
        {
            GroundedField.PRODUCT_STATE,
            GroundedField.CATEGORY,
            GroundedField.PIC,
            GroundedField.MINE,
        }
    ),
    CaseQueryKind.OPEN_PRODUCT_CASE: frozenset({GroundedField.PROPOSAL_CODE}),
}

PRODUCT_KINDS = frozenset({CaseQueryKind.LIST_PRODUCT_CASES, CaseQueryKind.OPEN_PRODUCT_CASE})


def _unused_by(grounded: GroundedQuery) -> tuple[GroundedField, ...]:
    """Fields the question grounded that the chosen kind cannot apply, in
    `GroundedField` order. A person named AND "mine" is two PICs: neither is
    applied."""
    present = grounded.present()
    unused = present - _APPLIES.get(grounded.kind, frozenset())
    if {GroundedField.PIC, GroundedField.MINE} <= present:
        unused |= {GroundedField.PIC, GroundedField.MINE}
    return tuple(field for field in GroundedField if field in unused)


def _resolve_category(
    mention: str, categories: Sequence[CategoryOption]
) -> tuple[str | None, tuple[str, ...]]:
    found = resolve_category(mention, categories)
    if len(found) == 1:
        return found[0].key, ()
    return None, tuple(option.label for option in found)


def _resolve_member(
    mention: str, members: Sequence[tuple[uuid.UUID, str]]
) -> tuple[uuid.UUID | None, tuple[str, ...]]:
    """A person of the workspace by display name: one name, held by exactly
    one member. Two members sharing the name are as ambiguous as two names."""
    resolution = resolve_name(mention, (name for _, name in members))
    if resolution.name is None:
        return None, resolution.candidates
    holders = [user_id for user_id, name in members if name == resolution.name]
    if len(holders) == 1:
        return holders[0], ()
    return None, (resolution.name,)


def _plan_product_list(
    grounded: GroundedQuery,
    categories: Sequence[CategoryOption],
    members: Sequence[tuple[uuid.UUID, str]],
    caller: uuid.UUID | None,
) -> CaseQueryPlan:
    """A product-case list narrowed by every field the question named, each
    resolved against the caller's own data; one that does not resolve stops
    the list, as an unresolved supplier stops a PO list."""
    category: str | None = None
    if grounded.category_mention is not None:
        category, candidates = _resolve_category(grounded.category_mention, categories)
        if category is None:
            return CaseQueryPlan(
                outcome=(
                    CaseQueryOutcome.CATEGORY_AMBIGUOUS
                    if candidates
                    else CaseQueryOutcome.CATEGORY_NOT_FOUND
                ),
                product_state=grounded.product_state,
                candidates=candidates,
            )
    pic: uuid.UUID | None = None
    if grounded.pic_mention is not None:
        pic, candidates = _resolve_member(grounded.pic_mention, members)
        if pic is None:
            return CaseQueryPlan(
                outcome=(
                    CaseQueryOutcome.PIC_AMBIGUOUS if candidates else CaseQueryOutcome.PIC_NOT_FOUND
                ),
                product_state=grounded.product_state,
                category=category,
                candidates=candidates,
            )
    elif grounded.mine:
        if caller is None:
            # "Mine" with nobody asking narrows to nobody; never to everyone.
            return CaseQueryPlan(outcome=CaseQueryOutcome.NOT_UNDERSTOOD)
        pic = caller
    return CaseQueryPlan(
        outcome=CaseQueryOutcome.PRODUCT_LIST,
        product_state=grounded.product_state,
        category=category,
        pic_user_id=pic,
    )


def plan_case_query(
    grounded: GroundedQuery,
    known_suppliers: Iterable[str],
    *,
    categories: Sequence[CategoryOption] = (),
    members: Sequence[tuple[uuid.UUID, str]] = (),
    caller: uuid.UUID | None = None,
) -> CaseQueryPlan:
    """The one decision both the handler and the eval graders run.

    `known_suppliers` is the caller's own tenant's stored names — the only
    names a supplier mention may become; `categories` the tenant's own
    Category list, `members` the people of the caller's workspace (id,
    display name) and `caller` the asker, for "mine". A mention that resolves
    to none of them, or to several, stops the query: running it without that
    filter would answer MORE than was asked.
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
    if grounded.kind is CaseQueryKind.OPEN_PRODUCT_CASE and grounded.proposal_code_mention is None:
        return CaseQueryPlan(outcome=CaseQueryOutcome.PROPOSAL_CODE_MISSING)
    unused = _unused_by(grounded)
    if unused:
        return CaseQueryPlan(outcome=CaseQueryOutcome.NOT_UNDERSTOOD, unused=unused)
    if grounded.kind is CaseQueryKind.OPEN_CASE:
        return CaseQueryPlan(
            outcome=CaseQueryOutcome.OPEN, po_reference=grounded.po_reference_mention
        )
    if grounded.kind is CaseQueryKind.OPEN_PRODUCT_CASE:
        return CaseQueryPlan(
            outcome=CaseQueryOutcome.PRODUCT_OPEN, proposal_code=grounded.proposal_code_mention
        )
    if grounded.kind is CaseQueryKind.LIST_PRODUCT_CASES:
        return _plan_product_list(grounded, categories, members, caller)

    supplier_name: str | None = None
    if grounded.supplier_mention is not None:
        resolution = resolve_name(grounded.supplier_mention, known_suppliers)
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
