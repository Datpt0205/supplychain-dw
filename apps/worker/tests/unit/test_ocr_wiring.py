"""Unit: how the worker reaches OCR for the extraction lane (supply-chain
ticket ai-automation/21). A file with a text layer is read from it; only an
image or a PDF without one goes to OCR, and a deployed worker neither runs
without OCR nor downloads its weights."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pypdf import PdfWriter

from dw_knowledge.ports import OcrLine, OcrReading
from dw_worker import composition
from dw_worker.consumers.supply_chain import InProcessDocumentText
from dw_worker.settings import WorkerSettings

pytestmark = pytest.mark.unit

PDF = "application/pdf"
PNG = "image/png"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_DB = "postgresql+asyncpg://dw_app:x@db:5432/dw"


@dataclass
class FakeOcr:
    """Reads images and PDFs, as the Docling reader does; records each call."""

    calls: list[str] = field(default_factory=list)

    def supports(self, content_type: str) -> bool:
        return content_type in (PNG, PDF)

    async def read(self, data: bytes, content_type: str) -> OcrReading:
        self.calls.append(content_type)
        return OcrReading(
            text="Kết luận: Đạt",
            lines=(OcrLine("Kết luận: Đạt", 0.91), OcrLine("Ngày: 05/1O", 0.32)),
            page_count=1,
        )


def _scan_pdf() -> bytes:
    """A PDF with a page and no text layer: what a scanner writes."""
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


async def test_an_image_and_a_scan_go_to_ocr_with_their_lines() -> None:
    ocr = FakeOcr()
    reader = InProcessDocumentText(ocr=ocr)
    assert reader.supports(PNG) and reader.supports(PDF) and reader.supports(DOCX)

    image = await reader.text_of(b"\x89PNG", PNG, "unc.png")
    scan = await reader.text_of(_scan_pdf(), PDF, "unc.pdf")

    assert ocr.calls == [PNG, PDF]
    for read in (image, scan):
        assert read.text == "Kết luận: Đạt"
        assert read.recognised is not None
        assert [line.confidence for line in read.recognised] == [0.91, 0.32]


async def test_a_text_layer_is_read_from_the_layer_never_by_ocr() -> None:
    ocr = FakeOcr()
    reader = InProcessDocumentText(ocr=ocr)
    docx = io.BytesIO()
    from docx import Document

    document = Document()
    document.add_paragraph("Kết luận: Không đạt")
    document.save(docx)

    read = await reader.text_of(docx.getvalue(), DOCX, "bb.docx")

    assert ocr.calls == []
    assert read.recognised is None and "Không đạt" in read.text


async def test_without_ocr_an_image_is_not_read_and_a_scan_is_an_empty_text() -> None:
    reader = InProcessDocumentText()
    assert not reader.supports(PNG)
    with pytest.raises(ValueError, match="no in-process reader"):
        await reader.text_of(b"\x89PNG", PNG, "unc.png")
    scan = await reader.text_of(_scan_pdf(), PDF, "unc.pdf")
    assert scan.text.strip() == "" and scan.recognised is None


def test_a_deployed_worker_refuses_to_start_without_baked_ocr_weights() -> None:
    deployed: dict[str, object] = {
        "profile": "uat",
        "database_url": _DB,
        "embedding_provider": "openai_compatible",
        "qdrant_url": "http://qdrant:6333",
        "zalo_bot_token": "",
        "zalo_link_secret": "",
    }
    with pytest.raises(RuntimeError, match="OCR_ARTIFACTS_PATH"):
        WorkerSettings(**deployed).validate_for_profile()  # type: ignore[arg-type]
    WorkerSettings(
        **deployed,  # type: ignore[arg-type]
        ocr_artifacts_path=Path("/app/models/ocr"),
    ).validate_for_profile()


def test_without_docling_a_local_worker_reads_no_image_and_a_deployed_one_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(composition.importlib.util, "find_spec", lambda name: None)
    assert composition.build_ocr_reader(WorkerSettings(profile="local")) is None
    with pytest.raises(RuntimeError, match="parsers"):
        composition.build_ocr_reader(
            WorkerSettings(profile="production", ocr_artifacts_path=Path("/app/models/ocr"))
        )


def test_the_reader_takes_its_bounds_and_weights_from_the_settings() -> None:
    pytest.importorskip("docling")
    reader = composition.build_ocr_reader(
        WorkerSettings(
            profile="local",
            ocr_max_pages=3,
            ocr_timeout_seconds=120,
            ocr_artifacts_path=Path("/models"),
        )
    )
    from dw_knowledge.adapters.docling_ocr import DoclingOcrReader

    assert isinstance(reader, DoclingOcrReader)
    assert (reader.max_pages, reader.timeout_seconds) == (3, 120)
    assert reader.artifacts_path == Path("/models")
