"""Unit: the Docling + EasyOCR reader (supply-chain ticket ai-automation/21).

The real engine runs on a small Vietnamese image this test draws, and only
where Docling, a font with Vietnamese glyphs and the model weights are all on
the machine: CI installs no `parsers` extra, so there it is skipped, and the
lane's behaviour is covered by the scripted reader in `dw_supply_chain`.
"""

from __future__ import annotations

import io
import os
import time
import unicodedata
from pathlib import Path

import pytest

from dw_knowledge.adapters.docling_ocr import DoclingOcrReader, OcrRefusedError

pytestmark = pytest.mark.unit

_FONTS = (
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/Library/Fonts/Arial Unicode.ttf"),
)
_WEIGHTS = (
    Path.home() / ".EasyOCR" / "model" / "craft_mlt_25k.pth",
    Path.home() / ".EasyOCR" / "model" / "latin_g2.pth",
)
_LAYOUT = (
    Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    / "hub"
    / "models--docling-project--docling-layout-heron"
)

LINES = (
    "BIÊN BẢN ĐÁNH GIÁ MẪU",
    "Ngày đánh giá: 05/10/2026",
    "Người đánh giá: Phòng nghiên cứu",
    "Kết luận: Không đạt",
)


def test_only_images_and_pdfs_are_offered() -> None:
    reader = DoclingOcrReader()
    assert reader.supports("image/png") and reader.supports("image/jpeg")
    assert reader.supports("application/pdf")
    assert not reader.supports("message/rfc822")


async def test_a_type_it_does_not_read_is_refused_not_returned_empty() -> None:
    with pytest.raises(OcrRefusedError):
        await DoclingOcrReader().read(b"x", "image/gif")


def _vietnamese_png() -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    font_path = next((f for f in _FONTS if f.exists()), None)
    if font_path is None:
        pytest.skip("no font with Vietnamese glyphs on this machine")
    font = ImageFont.truetype(str(font_path), 40)
    image = Image.new("RGB", (1100, 90 * len(LINES) + 60), "white")
    draw = ImageDraw.Draw(image)
    for row, line in enumerate(LINES):
        draw.text((40, 40 + 90 * row), line, fill="black", font=font)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


async def test_the_real_engine_reads_vietnamese_tone_marks_and_reports_every_line() -> None:
    pytest.importorskip("docling")
    if not all(w.exists() for w in _WEIGHTS) or not _LAYOUT.exists():
        pytest.skip("OCR weights not on this machine (first run downloads them)")
    reader = DoclingOcrReader(max_pages=2, timeout_seconds=600)

    started = time.perf_counter()
    reading = await reader.read(_vietnamese_png(), "image/png")
    elapsed = time.perf_counter() - started

    text = unicodedata.normalize("NFC", reading.text)
    # The toned words, as the threshold of the ticket counts them (>= 90% exact).
    # Measured here: all but "luận", read "Iuận" (l and I look alike in Arial).
    toned = ("BIÊN", "BẢN", "ĐÁNH", "MẪU", "Ngày", "đánh", "giá", "Người", "Phòng", "nghiên")
    toned += ("cứu", "Kết", "luận", "Không", "đạt")
    read = [word for word in toned if word in text]
    assert len(read) / len(toned) >= 0.9, (sorted(set(toned) - set(read)), text)
    assert "05/10/2026" in text
    assert reading.page_count == 1
    # Every line, each with the engine's own score; none dropped.
    assert len(reading.lines) >= len(LINES)
    assert all(0.0 <= line.confidence <= 1.0 for line in reading.lines)
    assert elapsed < 600


async def test_a_document_over_the_page_limit_is_refused_before_it_is_read() -> None:
    pytest.importorskip("docling")
    if not all(w.exists() for w in _WEIGHTS) or not _LAYOUT.exists():
        pytest.skip("OCR weights not on this machine (first run downloads them)")
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=595, height=842)
    out = io.BytesIO()
    writer.write(out)

    with pytest.raises(OcrRefusedError, match=r"pages"):
        await DoclingOcrReader(max_pages=2).read(out.getvalue(), "application/pdf")
