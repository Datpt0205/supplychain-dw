"""Unit: `domain.brief_summary` — which of a model's sentences about a brief
a person gets to read. Every check that keeps or drops a sentence has a case
here that turns it the other way."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from dw_kernel.ids import TenantId, WorkspaceId
from dw_supply_chain.domain.brief_summary import (
    BriefSummaryDraft,
    BriefSummaryStatus,
    ground_summary,
)
from dw_supply_chain.domain.daily_brief import (
    BriefEntry,
    BriefGroup,
    BriefSignal,
    DailyBrief,
)
from dw_supply_chain.domain.po_case import CaseState, POCase, POCaseId

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 28, 8, tzinfo=UTC)


def _case(po_reference: str, supplier_name: str = "Elmich Co.") -> POCase:
    return POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(uuid.uuid4()),
        workspace_id=WorkspaceId(uuid.uuid4()),
        po_reference=po_reference,
        supplier_name=supplier_name,
        state=CaseState.WAITING_DEPOSIT,
        created_at=_NOW,
    )


def _brief() -> DailyBrief:
    deposit = BriefGroup(
        signal=BriefSignal.SLA_BREACHED,
        qualifier="deposit",
        entries=(
            BriefEntry(case=_case("PO-2026-001", "Sunhouse"), days=15, limit_days=10),
            BriefEntry(case=_case("PO-2026-004", "Sunhouse"), days=12, limit_days=10),
        ),
        total=3,
    )
    blocked = BriefGroup(
        signal=BriefSignal.CASE_BLOCKED,
        qualifier=None,
        entries=(BriefEntry(case=_case("PO-77", "Toshiba"), days=6),),
        total=1,
        state=CaseState.BLOCKED,
    )
    moved = BriefGroup(
        signal=BriefSignal.CHANGED_RECENTLY,
        qualifier=None,
        entries=(BriefEntry(case=_case("PO-90", "Kangaroo")),),
        total=1,
    )
    return DailyBrief(
        generated_at=_NOW,
        active_case_count=20,
        flagged_case_count=4,
        groups=(deposit, blocked, moved),
        approvals_visible=True,
    )


def _ground(*sentences: tuple[str, list[str]]) -> object:
    draft = BriefSummaryDraft.model_validate(
        {"sentences": [{"text": text, "group_keys": keys} for text, keys in sentences]}
    )
    return ground_summary(draft, _brief())


def _kept(summary: object) -> list[str]:
    return [sentence.text for sentence in summary.sentences]  # type: ignore[attr-defined]


# -- what survives -------------------------------------------------------------


def test_a_sentence_built_from_its_cited_groups_figures_is_kept() -> None:
    summary = _ground(
        (
            "Ưu tiên 3 PO quá SLA đặt cọc, lâu nhất PO-2026-001 đã 15 ngày (hạn 10).",
            ["sla_breached:deposit"],
        ),
        ("PO-77 của Toshiba bị chặn 6 ngày.", ["case_blocked"]),
    )
    assert summary.status is BriefSummaryStatus.WRITTEN  # type: ignore[attr-defined]
    assert summary.dropped == 0  # type: ignore[attr-defined]
    assert len(_kept(summary)) == 2


def test_the_change_window_is_a_figure_only_the_change_group_carries() -> None:
    kept = _ground(("1 case vừa đổi trạng thái trong 24 giờ qua.", ["changed_recently"]))
    dropped = _ground(("Trong 24 giờ qua PO-77 vẫn bị chặn.", ["case_blocked"]))
    assert _kept(kept) == ["1 case vừa đổi trạng thái trong 24 giờ qua."]
    assert _kept(dropped) == []


def test_a_sentence_without_figures_or_names_is_kept() -> None:
    summary = _ground(("Nên xử lý nhóm quá hạn đặt cọc trước.", ["sla_breached:deposit"]))
    assert _kept(summary) == ["Nên xử lý nhóm quá hạn đặt cọc trước."]


# -- what is dropped, and why -------------------------------------------------


def test_a_sentence_citing_a_group_the_brief_does_not_have_is_dropped() -> None:
    summary = _ground(("3 PO quá SLA thanh toán.", ["sla_breached:payment"]))
    assert summary.status is BriefSummaryStatus.NOTHING_KEPT  # type: ignore[attr-defined]
    assert summary.dropped == 1  # type: ignore[attr-defined]


def test_an_invented_figure_drops_the_sentence() -> None:
    assert _kept(_ground(("5 PO quá SLA đặt cọc.", ["sla_breached:deposit"]))) == []


def test_a_figure_from_a_group_the_sentence_does_not_cite_drops_it() -> None:
    """6 is the blocked case's day count, not the deposit group's."""
    assert _kept(_ground(("PO quá SLA đặt cọc đã 6 ngày.", ["sla_breached:deposit"]))) == []


def test_a_po_from_another_group_drops_the_sentence() -> None:
    """PO-77 is in the brief, but not in the group this sentence cites."""
    assert _kept(_ground(("PO-77 quá SLA đặt cọc.", ["sla_breached:deposit"]))) == []


def test_a_supplier_from_another_group_drops_the_sentence() -> None:
    assert _kept(_ground(("Toshiba đang trễ đặt cọc.", ["sla_breached:deposit"]))) == []


def test_a_po_not_in_the_brief_at_all_is_caught_by_its_figures() -> None:
    assert _kept(_ground(("PO-2031-999 quá SLA đặt cọc.", ["sla_breached:deposit"]))) == []


def test_citing_both_groups_lets_a_sentence_name_both() -> None:
    summary = _ground(
        (
            "Sunhouse có 3 PO quá hạn cọc; PO-77 của Toshiba bị chặn.",
            ["sla_breached:deposit", "case_blocked"],
        )
    )
    assert len(_kept(summary)) == 1


def test_a_name_is_matched_as_whole_words_not_inside_another() -> None:
    """Another group's supplier "Sun" is not mentioned by "Sunhouse": a
    substring match would drop a sentence that names only its own group."""
    brief = _brief()
    blocked = brief.groups[1]
    renamed = BriefGroup(
        signal=blocked.signal,
        qualifier=blocked.qualifier,
        entries=(BriefEntry(case=_case("PO-77", "Sun"), days=6),),
        total=blocked.total,
        state=blocked.state,
    )
    brief = DailyBrief(
        generated_at=brief.generated_at,
        active_case_count=brief.active_case_count,
        flagged_case_count=brief.flagged_case_count,
        groups=(brief.groups[0], renamed, brief.groups[2]),
        approvals_visible=True,
    )
    draft = BriefSummaryDraft.model_validate(
        {
            "sentences": [
                {"text": "Sunhouse có 3 PO quá hạn cọc.", "group_keys": ["sla_breached:deposit"]}
            ]
        }
    )
    assert [s.text for s in ground_summary(draft, brief).sentences] == [
        "Sunhouse có 3 PO quá hạn cọc."
    ]
    # And a real mention of "Sun" is still caught.
    draft = BriefSummaryDraft.model_validate(
        {
            "sentences": [
                {"text": "Sun có 3 PO quá hạn cọc.", "group_keys": ["sla_breached:deposit"]}
            ]
        }
    )
    assert ground_summary(draft, brief).sentences == ()


def test_only_the_bad_sentences_go_and_order_is_kept() -> None:
    summary = _ground(
        ("PO-77 bị chặn 6 ngày.", ["case_blocked"]),
        ("99 PO quá SLA.", ["sla_breached:deposit"]),
        ("3 PO quá SLA đặt cọc.", ["sla_breached:deposit"]),
    )
    assert _kept(summary) == ["PO-77 bị chặn 6 ngày.", "3 PO quá SLA đặt cọc."]
    assert summary.dropped == 1  # type: ignore[attr-defined]


def test_a_repeated_key_is_cited_once() -> None:
    summary = _ground(("PO-77 bị chặn.", ["case_blocked", "case_blocked"]))
    assert summary.sentences[0].group_keys == ("case_blocked",)  # type: ignore[attr-defined]


def test_no_sentence_is_nothing_written_not_nothing_kept() -> None:
    summary = ground_summary(BriefSummaryDraft(sentences=[]), _brief())
    assert summary.status is BriefSummaryStatus.NOTHING_WRITTEN
    assert summary.dropped == 0


# -- the schema the model must answer in ---------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        {"sentences": [{"text": "x", "group_keys": ["case_blocked"], "priority": 1}]},
        {"sentences": [{"text": "x", "group_keys": []}]},
        {"sentences": [{"text": "", "group_keys": ["case_blocked"]}]},
        {"sentences": [{"text": "x" * 301, "group_keys": ["case_blocked"]}]},
        {"sentences": [{"text": "x", "group_keys": ["case_blocked"]}] * 6},
        {"sentences": [], "narrative": "free text the page would render"},
    ],
)
def test_an_answer_outside_the_schema_is_refused(answer: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        BriefSummaryDraft.model_validate(answer)
