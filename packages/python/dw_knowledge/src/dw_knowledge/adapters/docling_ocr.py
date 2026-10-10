"""Images and scanned PDFs read in process: Docling for layout and reading
order, EasyOCR as its OCR engine (supply-chain ticket ai-automation/21; Đạt
chose the pair on 2026-10-10).

Nothing here calls a model, so the text can be redacted before any model sees
it, as a PDF's text layer is (supply-chain ADR 0021 amended 2026-10-09).

Measured before depending on it (failure-modes #4), on two real Vietnamese
pages rendered clean, as a good scan and as a bad one (numbers in the ticket):

- **Vietnamese.** EasyOCR's `latin_g2` recogniser writes tone marks (93-98% of
  toned words exact on a clean or good page). Docling's default OCR engine,
  RapidOCR, cannot: its dictionaries lack most toned vowels.
- **Reading order.** Docling's layout and table models put lines back into
  paragraphs and table rows: 76-84% of words in order on a clean page, against
  60-80% for EasyOCR's lines alone (55-73% with Docling's table model off).
  That is why Docling is the pipeline and not EasyOCR on its own.
- **Scanned PDFs** are rendered by pdfium: through Docling's default PDF
  backend the same good scan read only 77-80% of toned words.
- **Confidence.** Docling drops every line under `confidence_threshold` without
  a trace (default 0.5). A bad scan would then read as a short, confident text.
  So the threshold is 0 here: every line is kept and reported with its score,
  and the CALLER decides what a doubtful document or line is worth.

Bounded, because a lane that reads one document at a time must not be held by
a 200-page scan: a document over `max_pages` is refused before any page is
read, and one that runs past `timeout_seconds` is refused (Docling checks the
clock between pages, so the overrun is at most one page). A refusal raises;
the caller records the document as unreadable.

Weights: with `artifacts_path` set (the deployed worker image bakes them, see
`infra/docker/worker.Dockerfile`), every model loads from there and nothing is
downloaded. Without it the engines fetch their weights on first use, which is
acceptable on a developer's machine only; the worker's settings refuse a
deployed profile without the path.
"""

from __future__ import annotations

import asyncio
import io
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dw_knowledge.ports import OcrLine, OcrReading

PNG = "image/png"
JPEG = "image/jpeg"
PDF = "application/pdf"
_SUFFIX = {PNG: ".png", JPEG: ".jpg", PDF: ".pdf"}

# A PI, an invoice, a transfer receipt is one to three pages; at the measured
# 37-50 s a page on a 4-core CPU (up to 100 s under load), five pages fit the
# timeout below.
DEFAULT_MAX_PAGES = 5
DEFAULT_TIMEOUT_SECONDS = 300.0


class OcrRefusedError(Exception):
    """The reader would not, or could not, finish this file."""


@dataclass
class DoclingOcrReader:
    languages: tuple[str, ...] = ("vi",)
    max_pages: int = DEFAULT_MAX_PAGES
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    # Where the baked weights live (Docling's layout and table models, and
    # EasyOCR's under `EasyOcr/`). None lets the engines download on first use.
    artifacts_path: Path | None = None
    _converter: Any = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def supports(self, content_type: str) -> bool:
        return content_type in _SUFFIX

    async def read(self, data: bytes, content_type: str) -> OcrReading:
        if content_type not in _SUFFIX:
            raise OcrRefusedError(f"no OCR for {content_type!r}")
        return await asyncio.to_thread(self._read, data, content_type)

    def _read(self, data: bytes, content_type: str) -> OcrReading:
        from docling.datamodel.base_models import ConversionStatus, DocumentStream

        stream = DocumentStream(name=f"document{_SUFFIX[content_type]}", stream=io.BytesIO(data))
        # One conversion at a time: the models are shared and not thread-safe.
        with self._lock:
            result = self._converter_once().convert(
                stream, raises_on_error=False, max_num_pages=self.max_pages
            )
        if result.status != ConversionStatus.SUCCESS:
            reasons = "; ".join(str(e.error_message) for e in result.errors)
            raise OcrRefusedError(f"OCR did not finish ({result.status.value}): {reasons}"[:500])
        lines = tuple(
            OcrLine(text=cell.text, confidence=float(cell.confidence))
            for page in result.pages
            if page.parsed_page is not None
            for cell in page.parsed_page.textline_cells
            if cell.from_ocr and cell.text.strip()
        )
        text = result.document.export_to_markdown(
            escape_html=False, escape_underscores=False, compact_tables=True
        )
        return OcrReading(text=text, lines=lines, page_count=len(result.pages))

    def _converter_once(self) -> Any:
        if self._converter is None:
            self._converter = self._build()
        return self._converter

    def _build(self) -> Any:
        from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import EasyOcrOptions, PdfPipelineOptions
        from docling.document_converter import (
            DocumentConverter,
            ImageFormatOption,
            PdfFormatOption,
        )

        options = PdfPipelineOptions(
            artifacts_path=self.artifacts_path,
            do_ocr=True,
            do_table_structure=True,
            ocr_options=EasyOcrOptions(
                lang=list(self.languages),
                # Every line, with its score: the caller decides (module doc).
                confidence_threshold=0.0,
                # Only a file with no text layer reaches this reader.
                force_full_page_ocr=True,
                download_enabled=self.artifacts_path is None,
            ),
            # The per-line cells and their confidence live on the parsed page.
            generate_parsed_pages=True,
            document_timeout=self.timeout_seconds,
        )
        return DocumentConverter(
            format_options={
                InputFormat.IMAGE: ImageFormatOption(pipeline_options=options),
                # pdfium renders a scanned page as the image it holds; with
                # Docling's default backend the same good scan read 77-80% of
                # toned words instead of 93-96% (module doc).
                InputFormat.PDF: PdfFormatOption(
                    pipeline_options=options, backend=PyPdfiumDocumentBackend
                ),
            }
        )
