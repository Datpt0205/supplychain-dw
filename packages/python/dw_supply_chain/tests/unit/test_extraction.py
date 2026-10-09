"""Unit: grounding a model's reading against the document, and redaction
(ADR 0021 amended 2026-10-09; ticket ai-automation/02).

A field the text does not prove is a gap, never a value; arithmetic is code's;
an account number never survives redaction.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.extraction import (
    EXTRACTION_SPECS,
    REDACTED_IDENTIFIER,
    Cited,
    GapReason,
    QuotationLineReading,
    SampleEvaluationReading,
    SupplierQuotationReading,
    ground,
    is_unreadable,
    parse_number,
    redact_identifiers,
)

pytestmark = pytest.mark.unit

QUOTE_SPEC = EXTRACTION_SPECS[DocumentType.SUPPLIER_QUOTATION]
EVAL_SPEC = EXTRACTION_SPECS[DocumentType.SAMPLE_EVALUATION]

QUOTATION = """BÁO GIÁ số BG-0912
Ngày báo giá: 09/10/2026
Nhà cung cấp: Công ty Gia dụng Minh Phát
Điều kiện giao hàng: FOB Ningbo
| Nồi inox 24cm | 1.000 | 2,50 USD | 2.500,00 USD |
| Chảo chống dính 28cm | 500 | 3,10 USD | 1.550,00 USD |
Tổng cộng: 4.050,00 USD
"""


def _c(value: str | None, quote: str | None) -> Cited:
    return Cited(value=value, quote=quote)


def _line(desc: str, qty: str, price: str, total: str, quote: str) -> QuotationLineReading:
    return QuotationLineReading(
        description=_c(desc, quote),
        quantity=_c(qty, quote),
        unit_price=_c(price, quote),
        line_total=_c(total, quote),
    )


LINE_1 = "| Nồi inox 24cm | 1.000 | 2,50 USD | 2.500,00 USD |"
LINE_2 = "| Chảo chống dính 28cm | 500 | 3,10 USD | 1.550,00 USD |"


def _quotation(**overrides: object) -> SupplierQuotationReading:
    base: dict[str, object] = {
        "supplier_name": _c(
            "Công ty Gia dụng Minh Phát", "Nhà cung cấp: Công ty Gia dụng Minh Phát"
        ),
        "quotation_date": _c("2026-10-09", "Ngày báo giá: 09/10/2026"),
        "incoterm": _c("FOB", "Điều kiện giao hàng: FOB Ningbo"),
        "lines": [
            _line("Nồi inox 24cm", "1000", "2.50", "2500.00", LINE_1),
            _line("Chảo chống dính 28cm", "500", "3.10", "1550.00", LINE_2),
        ],
        "total": _c("4050.00", "Tổng cộng: 4.050,00 USD"),
    }
    base.update(overrides)
    return SupplierQuotationReading.model_validate(base)


def test_a_reading_the_text_proves_is_kept_with_typed_values() -> None:
    grounded = ground(_quotation(), QUOTE_SPEC, QUOTATION)
    assert grounded.fields["incoterm"]["value"] == "FOB"
    assert grounded.fields["quotation_date"]["value"] == "2026-10-09"
    assert grounded.fields["total"]["value"] == "4050.00"
    assert grounded.fields["lines"][0]["unit_price"]["value"] == "2.50"
    # Fields the document does not state are gaps named "missing", not values.
    assert {(g.field, g.reason) for g in grounded.gaps} == {
        ("valid_until", GapReason.MISSING),
        ("currency", GapReason.MISSING),
        ("moq", GapReason.MISSING),
        ("lead_time_days", GapReason.MISSING),
    }


def test_a_quote_the_text_does_not_contain_is_a_gap_not_a_value() -> None:
    reading = _quotation(
        incoterm=_c("CIF", "Điều kiện giao hàng: CIF Hải Phòng"),
        supplier_name=_c("Công ty Khác", "Nhà cung cấp: Công ty Gia dụng Minh Phát"),
    )
    grounded = ground(reading, QUOTE_SPEC, QUOTATION)
    assert "incoterm" not in grounded.fields
    assert "supplier_name" not in grounded.fields
    reasons = {g.field: g.reason for g in grounded.gaps}
    assert reasons["incoterm"] is GapReason.QUOTE_NOT_FOUND
    assert reasons["supplier_name"] is GapReason.VALUE_NOT_IN_QUOTE


def test_a_number_the_quote_does_not_write_is_refused() -> None:
    reading = _quotation(total=_c("4500.00", "Tổng cộng: 4.050,00 USD"))
    grounded = ground(reading, QUOTE_SPEC, QUOTATION)
    assert "total" not in grounded.fields
    assert ("total", GapReason.VALUE_NOT_IN_QUOTE) in {(g.field, g.reason) for g in grounded.gaps}


def test_quotation_arithmetic_is_codes_and_a_wrong_total_is_a_gap() -> None:
    text = QUOTATION.replace("Tổng cộng: 4.050,00 USD", "Tổng cộng: 4.500,00 USD")
    reading = _quotation(total=_c("4500.00", "Tổng cộng: 4.500,00 USD"))
    grounded = ground(reading, QUOTE_SPEC, text)
    assert "total" not in grounded.fields
    assert ("total", GapReason.NUMBER_MISMATCH) in {(g.field, g.reason) for g in grounded.gaps}


def test_a_line_whose_total_is_not_quantity_times_price_loses_its_total() -> None:
    bad_line = "| Nồi inox 24cm | 1.000 | 2,50 USD | 2.600,00 USD |"
    text = QUOTATION.replace(LINE_1, bad_line)
    reading = _quotation(
        lines=[
            _line("Nồi inox 24cm", "1000", "2.50", "2600.00", bad_line),
            _line("Chảo chống dính 28cm", "500", "3.10", "1550.00", LINE_2),
        ],
        total=_c("4050.00", "Tổng cộng: 4.050,00 USD"),
    )
    grounded = ground(reading, QUOTE_SPEC, text)
    assert "line_total" not in grounded.fields["lines"][0]
    assert ("lines[0].line_total", GapReason.NUMBER_MISMATCH) in {
        (g.field, g.reason) for g in grounded.gaps
    }


def test_a_choice_outside_its_list_and_a_date_the_quote_does_not_write_are_gaps() -> None:
    text = "BIÊN BẢN ĐÁNH GIÁ MẪU\nNgày đánh giá: 05/10/2026\nKết luận: Không đạt"
    reading = SampleEvaluationReading(
        result=_c("approved", "Kết luận: Không đạt"),
        evaluated_on=_c("2026-10-06", "Ngày đánh giá: 05/10/2026"),
    )
    grounded = ground(reading, EVAL_SPEC, text)
    reasons = {g.field: g.reason for g in grounded.gaps}
    assert reasons["result"] is GapReason.NOT_AN_OPTION
    assert reasons["evaluated_on"] is GapReason.VALUE_NOT_IN_QUOTE
    ok = ground(
        SampleEvaluationReading(
            result=_c("fail", "Kết luận: Không đạt"),
            evaluated_on=_c("2026-10-05", "Ngày đánh giá: 05/10/2026"),
        ),
        EVAL_SPEC,
        text,
    )
    assert ok.fields["result"]["value"] == "fail"
    assert ok.fields["evaluated_on"]["value"] == "2026-10-05"


def test_a_quote_carrying_the_prompts_escapes_still_matches_its_text() -> None:
    text = "Ghi chú: nắp <kính> & tay cầm"
    reading = SampleEvaluationReading(
        notes=_c("nắp <kính> & tay cầm", "nắp &lt;kính&gt; &amp; tay cầm")
    )
    assert ground(reading, EVAL_SPEC, text).fields["notes"]["value"] == "nắp <kính> & tay cầm"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.234.567", Decimal("1234567")),
        ("1,234,567", Decimal("1234567")),
        ("1 234 567", Decimal("1234567")),
        ("1.234,5", Decimal("1234.5")),
        ("1,234.5", Decimal("1234.5")),
        ("2,50", Decimal("2.50")),
        ("2.50", Decimal("2.50")),
        ("1000", Decimal("1000")),
        ("1.234", None),
        ("1,234", None),
        ("abc", None),
    ],
)
def test_numbers_are_read_in_either_convention_and_ambiguity_is_refused(
    raw: str, expected: Decimal | None
) -> None:
    assert parse_number(raw) == expected


@pytest.mark.parametrize(
    "text",
    [
        "STK: 0071 000 123 456 tại Vietcombank",
        "Số tài khoản 19034567890123 - Techcombank",
        "Account No. 1234-5678-9012",
        "A/C: 123456789",
        "IBAN DE89 3704 0044 0532 0130 00",
        "chuyển vào 0071000123456",
    ],
)
def test_account_numbers_never_survive_redaction(text: str) -> None:
    redacted = redact_identifiers(text)
    assert REDACTED_IDENTIFIER in redacted.text
    assert redacted.count >= 1
    digits = "".join(ch for ch in redacted.text if ch.isdigit())
    assert len(digits) < 9, redacted.text


def test_amounts_dates_and_short_numbers_are_left_alone() -> None:
    text = "Tổng cộng: 18.450.000.000 đ; 4,050.00 USD; ngày 09/10/2026; SL 1.000"
    assert redact_identifiers(text).text == text


def test_an_empty_text_or_the_marker_is_unreadable() -> None:
    assert is_unreadable("  ")
    assert is_unreadable("[không đọc được nội dung]")
    assert not is_unreadable("Biên bản")
