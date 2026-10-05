"""Unit: the download's Content-Disposition, which carries a user-typed name."""

from __future__ import annotations

import pytest

from dw_supply_chain.presentation.document_routes import content_disposition

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "filename",
    [
        'a"; filename="evil.html',
        "a\r\nSet-Cookie: s=1\r\n.pdf",
        "back\\slash.pdf",
        "tab\there.pdf",
    ],
)
def test_a_name_cannot_end_the_header_or_its_quoted_string(filename: str) -> None:
    header = content_disposition(filename)

    assert "\r" not in header and "\n" not in header
    assert header.startswith('attachment; filename="')
    fallback = header.split('filename="', 1)[1].split('"; filename*=', 1)[0]
    assert '"' not in fallback and "\\" not in fallback
    assert header.isascii()


def test_a_vietnamese_name_survives_in_filename_star_and_folds_in_the_fallback() -> None:
    header = content_disposition("Đơn đặt hàng.pdf")

    assert 'filename="Don dat hang.pdf"' in header
    assert "filename*=UTF-8''%C4%90%C6%A1n%20%C4%91%E1%BA%B7t%20h%C3%A0ng.pdf" in header


def test_a_name_with_nothing_ascii_still_has_a_fallback() -> None:
    assert 'filename="document"' in content_disposition("文書")
