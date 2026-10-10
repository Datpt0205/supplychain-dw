"""Unit: what a model may write in a draft (tickets ai-automation/07-10).

`ground_sentences` keeps a sentence only when every key it cites is an item of
THIS evidence, every number it writes is one its cited items write, and it
holds nothing that reads as a bank account. Each guard has a case that goes red
without it.
"""

from __future__ import annotations

import pytest

from dw_supply_chain.domain.grounded_writing import (
    CitedSentence,
    DropReason,
    EvidenceItem,
    ground_sentences,
)

pytestmark = pytest.mark.unit

EVIDENCE = (
    EvidenceItem("case", "Hồ sơ", "Mã đề xuất: DX-2026-041; Sản phẩm: Nồi inox 3 đáy 24cm"),
    EvidenceItem("reply_by", "Hạn phản hồi", "Hạn phản hồi: 16/10/2026"),
    EvidenceItem("follow_up:f1", "Việc cần theo dõi", "Quá hạn mốc lấy mẫu: đã 12 ngày (hạn 10)"),
)


def _one(text: str, *cites: str) -> tuple[list[str], list[DropReason]]:
    grounded = ground_sentences([CitedSentence(text=text, cites=list(cites))], EVIDENCE)
    return [k.text for k in grounded.kept], [d.reason for d in grounded.dropped]


def test_a_sentence_whose_numbers_its_citations_write_is_kept() -> None:
    kept, dropped = _one(
        "Mẫu nồi 24cm của hồ sơ DX-2026-041 đã trễ 12 ngày.", "case", "follow_up:f1"
    )
    assert kept and not dropped


def test_a_date_counts_by_its_parts() -> None:
    kept, _ = _one("Xin phản hồi trước 16/10/2026.", "reply_by")
    assert kept
    kept, dropped = _one("Xin phản hồi trước 2026-10-16.", "reply_by")
    assert kept and not dropped


def test_a_number_no_cited_item_writes_is_dropped() -> None:
    # 12 is in the follow-up, which this sentence does not cite.
    assert _one("Mẫu đã trễ 12 ngày.", "case") == ([], [DropReason.NUMBER_NOT_IN_EVIDENCE])
    assert _one("Đơn giá 45.000 đồng như đã thống nhất.", "case", "follow_up:f1") == (
        [],
        [DropReason.NUMBER_NOT_IN_EVIDENCE],
    )


def test_a_citation_to_anything_not_in_this_evidence_is_dropped() -> None:
    assert _one("Theo biên bản hồ sơ khác.", "doc:other-case") == (
        [],
        [DropReason.UNKNOWN_CITATION],
    )
    assert _one("Không dẫn gì.") == ([], [DropReason.NO_CITATION])


def test_an_account_number_is_dropped_even_if_cited() -> None:
    evidence = (EvidenceItem("case", "Hồ sơ", "STK 0451000123456"),)
    grounded = ground_sentences(
        [CitedSentence(text="Chuyển khoản tới STK 0451000123456.", cites=["case"])], evidence
    )
    assert [d.reason for d in grounded.dropped] == [DropReason.ACCOUNT_NUMBER]


def test_kept_sentences_keep_the_model_order_and_name_each_drop() -> None:
    grounded = ground_sentences(
        [
            CitedSentence(text="Câu một.", cites=["case"]),
            CitedSentence(text="Câu bịa 99.", cites=["case"]),
            CitedSentence(text="Câu ba, hạn 16/10/2026.", cites=["reply_by", "reply_by"]),
        ],
        EVIDENCE,
    )
    assert [k.text for k in grounded.kept] == ["Câu một.", "Câu ba, hạn 16/10/2026."]
    assert grounded.kept[1].cites == ("reply_by",)
    assert [(d.index, d.reason) for d in grounded.dropped] == [
        (1, DropReason.NUMBER_NOT_IN_EVIDENCE)
    ]
