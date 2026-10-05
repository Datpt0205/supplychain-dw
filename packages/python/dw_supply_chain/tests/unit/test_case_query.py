"""Unit: `domain.case_query` — what a model's reading of a question is worth
once code has checked it against the question and against real names."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dw_supply_chain.domain.case_query import (
    CaseQueryIntent,
    CaseQueryKind,
    CaseQueryOutcome,
    CaseQueryPlan,
    GroundedField,
    ground,
    ignored_fields,
    plan_case_query,
    resolve_supplier,
)
from dw_supply_chain.domain.evidence import is_verbatim
from dw_supply_chain.domain.po_case import CaseState

pytestmark = pytest.mark.unit

QUESTION = "Cho tôi các PO của NCC Sunhouse đang chờ đặt cọc, chỉ case đang chạy"


def _intent(**fields: object) -> CaseQueryIntent:
    return CaseQueryIntent.model_validate({"kind": "list_cases", **fields})


# -- evidence ------------------------------------------------------------------


def test_a_blank_quote_cites_nothing() -> None:
    """`"" in text` is always true — a blank quote must not pass as found."""
    assert not is_verbatim("", QUESTION)
    assert not is_verbatim("   ", QUESTION)


def test_a_quote_is_found_across_reflowed_whitespace() -> None:
    assert is_verbatim("NCC   Sunhouse\n", QUESTION)


# -- ground --------------------------------------------------------------------


def test_every_field_quoted_from_the_question_is_kept() -> None:
    grounded = ground(
        _intent(
            supplier_mention="Sunhouse",
            state="waiting_deposit",
            state_quote="chờ đặt cọc",
            active_only_quote="đang chạy",
        ),
        QUESTION,
    )
    assert grounded.supplier_mention == "Sunhouse"
    assert grounded.state is CaseState.WAITING_DEPOSIT
    assert grounded.active_only is True
    assert grounded.dropped == ()


def test_a_supplier_the_question_never_named_is_dropped_and_reported() -> None:
    grounded = ground(_intent(supplier_mention="Elmich"), QUESTION)
    assert grounded.supplier_mention is None
    assert grounded.dropped == (GroundedField.SUPPLIER,)


def test_a_state_without_a_quote_is_dropped_even_though_the_enum_is_closed() -> None:
    """A valid enum value proves the state exists, not that it was asked for."""
    grounded = ground(_intent(state="qc"), "Cho tôi các PO của NCC Sunhouse")
    assert grounded.state is None
    assert grounded.dropped == (GroundedField.STATE,)


def test_a_state_quote_not_in_the_question_is_dropped() -> None:
    grounded = ground(_intent(state="qc", state_quote="kiểm tra chất lượng"), QUESTION)
    assert grounded.state is None
    assert GroundedField.STATE in grounded.dropped


def test_active_only_needs_its_own_quote() -> None:
    assert ground(_intent(), QUESTION).active_only is False
    grounded = ground(_intent(active_only_quote="chưa hoàn tất"), QUESTION)
    assert grounded.active_only is False
    assert grounded.dropped == (GroundedField.ACTIVE_ONLY,)


def test_a_po_reference_is_grounded_like_any_other_mention() -> None:
    question = "Case PO-123 đang vướng gì?"
    kept = ground(_intent(kind="open_case", po_reference_mention="PO-123"), question)
    dropped = ground(_intent(kind="open_case", po_reference_mention="PO-999"), question)
    assert kept.po_reference_mention == "PO-123"
    assert dropped.po_reference_mention is None
    assert dropped.kind is CaseQueryKind.OPEN_CASE


def test_the_intent_schema_refuses_fields_it_does_not_define() -> None:
    """`extra="forbid"`: a model cannot smuggle a tenant or a raw filter in."""
    with pytest.raises(ValidationError):
        CaseQueryIntent.model_validate({"kind": "list_cases", "tenant_id": "other"})


# -- resolve_supplier ----------------------------------------------------------

NAMES = ["Sunhouse Co.", "Công ty TNHH Đồng Nai", "Elmich Co.", "Elmich Việt Nam"]


def test_an_equal_name_resolves_regardless_of_case_accents_and_spacing() -> None:
    resolution = resolve_supplier("cong ty  tnhh dong nai", NAMES)
    # The STORED name comes back, never the folded form used to match.
    assert resolution.name == "Công ty TNHH Đồng Nai"


def test_a_whole_word_part_of_one_name_resolves() -> None:
    assert resolve_supplier("sunhouse", NAMES).name == "Sunhouse Co."


def test_part_of_a_word_does_not_match() -> None:
    resolution = resolve_supplier("sun", NAMES)
    assert resolution.name is None
    assert resolution.candidates == ()


def test_an_ambiguous_mention_returns_every_candidate_and_picks_none() -> None:
    resolution = resolve_supplier("Elmich", NAMES)
    assert resolution.name is None
    assert resolution.candidates == ("Elmich Co.", "Elmich Việt Nam")


def test_two_stored_spellings_of_one_equal_name_are_ambiguous_not_merged() -> None:
    resolution = resolve_supplier("elmich co.", ["Elmich Co.", "elmich co."])
    assert resolution.name is None
    assert set(resolution.candidates) == {"Elmich Co.", "elmich co."}


def test_an_unknown_supplier_resolves_to_nothing() -> None:
    resolution = resolve_supplier("Toshiba", NAMES)
    assert resolution.name is None
    assert resolution.candidates == ()


# -- plan_case_query: the one decision the handler and the eval graders run --


def _plan(question: str, known: list[str] | None = None, **intent: object) -> CaseQueryPlan:
    return plan_case_query(ground(_intent(**intent), question), known or NAMES)


def test_a_fully_grounded_question_plans_a_list_with_the_stored_name() -> None:
    plan = _plan(
        QUESTION,
        supplier_mention="Sunhouse",
        state="waiting_deposit",
        state_quote="chờ đặt cọc",
        active_only_quote="đang chạy",
    )
    assert plan.outcome is CaseQueryOutcome.LIST
    assert plan.supplier_name == "Sunhouse Co."
    assert plan.state is CaseState.WAITING_DEPOSIT
    assert plan.active_only is True


def test_an_unknown_supplier_stops_the_query_instead_of_dropping_the_filter() -> None:
    """Running it without the supplier filter would answer MORE than asked."""
    plan = _plan("PO của NCC Toshiba", supplier_mention="Toshiba")
    assert plan.outcome is CaseQueryOutcome.SUPPLIER_NOT_FOUND
    assert plan.supplier_name is None


def test_an_ambiguous_supplier_stops_the_query_and_names_the_candidates() -> None:
    plan = _plan("PO của Elmich", supplier_mention="Elmich")
    assert plan.outcome is CaseQueryOutcome.SUPPLIER_AMBIGUOUS
    assert plan.candidates == ("Elmich Co.", "Elmich Việt Nam")


def test_a_supplier_only_another_tenant_has_is_simply_not_found() -> None:
    """`known` is the caller's own names; another tenant's are never offered."""
    plan = _plan(
        "PO của Their Secret Co.",
        known=["Sunhouse Co."],
        supplier_mention="Their Secret Co.",
    )
    assert plan.outcome is CaseQueryOutcome.SUPPLIER_NOT_FOUND


def test_a_question_with_no_filters_lists_everything_the_caller_may_read() -> None:
    plan = _plan("Cho tôi xem các PO")
    assert plan.outcome is CaseQueryOutcome.LIST
    assert (plan.state, plan.supplier_name, plan.active_only) == (None, None, False)


def test_open_case_needs_a_grounded_po_reference() -> None:
    kept = _plan("Case PO-123 đang vướng gì?", kind="open_case", po_reference_mention="PO-123")
    none_named = _plan("Case nào đang vướng?", kind="open_case")
    ungrounded = _plan("Case nào đang vướng?", kind="open_case", po_reference_mention="PO-123")
    assert (kept.outcome, kept.po_reference) == (CaseQueryOutcome.OPEN, "PO-123")
    assert none_named.outcome is CaseQueryOutcome.PO_REFERENCE_MISSING
    # A reference the question never contained is a claim, not a lookup key.
    assert ungrounded.outcome is CaseQueryOutcome.NOT_UNDERSTOOD


def test_an_unsupported_question_is_not_understood_whatever_else_was_filled() -> None:
    plan = _plan(QUESTION, kind="unsupported", supplier_mention="Sunhouse")
    assert plan.outcome is CaseQueryOutcome.NOT_UNDERSTOOD
    assert plan.supplier_name is None


def test_citations_name_the_question_span_behind_each_kept_field() -> None:
    grounded = ground(
        _intent(supplier_mention="Sunhouse", state="waiting_deposit", state_quote="chờ đặt cọc"),
        QUESTION,
    )
    assert grounded.citations == (
        (GroundedField.SUPPLIER, "Sunhouse"),
        (GroundedField.STATE, "chờ đặt cọc"),
    )


def test_a_claimed_but_ungrounded_field_refuses_rather_than_answering_broader() -> None:
    """The model believed the question named a supplier; its quote is not in
    the question. Listing every case instead would read as if filtered."""
    plan = _plan("PO của sunhouse", supplier_mention="Sunhouse")  # case-changed quote
    assert plan.outcome is CaseQueryOutcome.NOT_UNDERSTOOD
    assert plan.supplier_name is None


# -- review fixes: every way an answer could come back broader than asked ----


def test_a_mention_that_is_only_part_of_a_word_is_not_grounded() -> None:
    """ "PO-12" is a substring of "PO-1234"; resolving it would open a PO the
    question never named."""
    po = _plan("PO-1234 đang vướng gì?", kind="open_case", po_reference_mention="PO-12")
    supplier = _plan("PO của Sunhouse", supplier_mention="Sun")
    assert po.outcome is CaseQueryOutcome.NOT_UNDERSTOOD
    assert supplier.outcome is CaseQueryOutcome.NOT_UNDERSTOOD


def test_a_state_quote_the_model_could_not_name_refuses_rather_than_lists_all() -> None:
    """The model saw the question narrow by a status outside the closed set
    ("trễ" is not a state) — listing everything would ignore that."""
    grounded = ground(_intent(state_quote="trễ"), "PO nào trễ?")
    assert grounded.dropped == (GroundedField.STATE,)
    assert plan_case_query(grounded, NAMES).outcome is CaseQueryOutcome.NOT_UNDERSTOOD


def test_a_blank_quote_is_read_as_absent_not_as_a_broken_answer() -> None:
    intent = CaseQueryIntent.model_validate(
        {
            "kind": "list_cases",
            "supplier_mention": "Sunhouse",
            "state_quote": "",
            "active_only_quote": "  ",
        }
    )
    assert intent.state_quote is None
    assert intent.active_only_quote is None
    assert _plan("PO của Sunhouse", supplier_mention="Sunhouse", state_quote="").outcome is (
        CaseQueryOutcome.LIST
    )


def test_a_question_typed_decomposed_still_grounds_a_precomposed_quote() -> None:
    import unicodedata

    decomposed = unicodedata.normalize("NFD", "PO của Đồng Nai đang chờ đặt cọc")
    grounded = ground(
        _intent(supplier_mention="Đồng Nai", state="waiting_deposit", state_quote="chờ đặt cọc"),
        decomposed,
    )
    assert grounded.dropped == ()
    assert grounded.state is CaseState.WAITING_DEPOSIT


def test_a_list_reading_that_names_a_po_refuses_rather_than_lists_everything() -> None:
    plan = _plan("Cho tôi xem PO-123", po_reference_mention="PO-123")
    assert plan.outcome is CaseQueryOutcome.NOT_UNDERSTOOD
    assert plan.unused == (GroundedField.PO_REFERENCE,)


def test_an_open_reading_that_also_filters_refuses_rather_than_ignoring_it() -> None:
    plan = _plan(
        "PO-123 của Sunhouse thế nào?",
        kind="open_case",
        po_reference_mention="PO-123",
        supplier_mention="Sunhouse",
    )
    assert plan.outcome is CaseQueryOutcome.NOT_UNDERSTOOD
    assert plan.unused == (GroundedField.SUPPLIER,)


def test_an_ambiguous_supplier_keeps_the_rest_of_what_was_asked() -> None:
    plan = _plan(
        "PO của Elmich đang chờ đặt cọc, chưa hoàn tất",
        supplier_mention="Elmich",
        state="waiting_deposit",
        state_quote="chờ đặt cọc",
        active_only_quote="chưa hoàn tất",
    )
    assert plan.outcome is CaseQueryOutcome.SUPPLIER_AMBIGUOUS
    assert (plan.state, plan.active_only) == (CaseState.WAITING_DEPOSIT, True)


def test_an_unsupported_question_reports_no_field_as_its_reason() -> None:
    """Even a field that could not be grounded is not the reason an
    unsupported question is refused — and must not be reported as one."""
    grounded = ground(_intent(kind="unsupported", supplier_mention="Toshiba"), QUESTION)
    assert grounded.dropped == (GroundedField.SUPPLIER,)  # there IS something to misreport
    assert ignored_fields(grounded) == ()


def test_ungrounded_and_unusable_fields_are_reported_as_different_reasons() -> None:
    """A PO code the question states is not "no grounds for the PO code" —
    telling a person that about their own words would be false."""
    dropped = ground(_intent(state="qc", state_quote="đang QC"), "Cho tôi các PO")
    unusable = ground(_intent(po_reference_mention="PO-123"), "Cho tôi xem PO-123")
    assert ignored_fields(dropped) == (GroundedField.STATE,)
    assert plan_case_query(dropped, NAMES).unused == ()
    assert ignored_fields(unusable) == ()
    assert plan_case_query(unusable, NAMES).unused == (GroundedField.PO_REFERENCE,)


def test_an_equal_name_wins_over_a_longer_one_containing_it() -> None:
    resolution = resolve_supplier("Elmich", ["Elmich", "Elmich Việt Nam"])
    assert resolution.name == "Elmich"


def test_a_mention_with_no_letter_or_digit_names_nothing() -> None:
    # "A ... B" holds "..." as a whole word, so only the guard stops it.
    resolution = resolve_supplier("...", ["Sunhouse Co.", "A ... B"])
    assert resolution.name is None
    assert resolution.candidates == ()


def test_a_trademark_sign_does_not_move_the_word_boundary() -> None:
    """NFKD would fold "™" into "tm" and glue it onto the name."""
    assert resolve_supplier("Sunhouse", ["Sunhouse™ Co."]).name == "Sunhouse™ Co."


@pytest.mark.parametrize(
    ("question", "mention"),
    [
        ("PO-12-A đang vướng gì?", "PO-12"),
        ("PO-12/1 thế nào?", "PO-12"),
        ("PO-12.5 thế nào?", "PO-12"),
        ("PO-2026-001 đang vướng gì?", "PO-2026"),
        ("Case PO-12 thế nào?", "12"),
        ("PO-2026-001 đang vướng gì?", "2026-001"),
        ("HN/PO-7 thế nào?", "PO-7"),
        ("Lô A.PO-9 thế nào?", "PO-9"),
    ],
)
def test_a_reference_cut_at_a_code_separator_is_not_grounded(question: str, mention: str) -> None:
    """ "-", "/" and "." between letters or digits belong to the code: the
    front or back part of a longer reference would open a different, real
    PO."""
    plan = _plan(question, kind="open_case", po_reference_mention=mention)
    assert plan.outcome is CaseQueryOutcome.NOT_UNDERSTOOD


def test_a_reference_ending_a_sentence_still_grounds() -> None:
    plan = _plan("Case PO-12. Sao rồi?", kind="open_case", po_reference_mention="PO-12")
    assert (plan.outcome, plan.po_reference) == (CaseQueryOutcome.OPEN, "PO-12")


def test_a_reference_after_a_bullet_dash_still_grounds() -> None:
    """A separator with no code before it starts the line, not the code."""
    plan = _plan("Danh sách:\n-PO-12 thế nào?", kind="open_case", po_reference_mention="PO-12")
    assert (plan.outcome, plan.po_reference) == (CaseQueryOutcome.OPEN, "PO-12")


def test_a_supplier_name_that_is_the_front_of_a_longer_name_is_not_grounded() -> None:
    plan = _plan("PO của Sunhouse-VN", supplier_mention="Sunhouse")
    assert plan.outcome is CaseQueryOutcome.NOT_UNDERSTOOD


def test_an_open_reading_with_no_po_says_so_before_anything_else() -> None:
    """ "Which PO?" is the honest answer; complaining about the supplier the
    question did state would not be."""
    plan = _plan("Case của Sunhouse đang vướng gì?", kind="open_case", supplier_mention="Sunhouse")
    assert plan.outcome is CaseQueryOutcome.PO_REFERENCE_MISSING


def test_a_blank_state_is_read_as_absent() -> None:
    intent = CaseQueryIntent.model_validate(
        {"kind": "list_cases", "supplier_mention": "Sunhouse", "state": ""}
    )
    assert intent.state is None
