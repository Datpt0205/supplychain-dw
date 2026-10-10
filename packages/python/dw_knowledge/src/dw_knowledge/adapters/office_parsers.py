"""Parsers that read a file in process: PDF text layers, DOCX, XLSX and EML.

Each implements ``DocumentParserPort`` and is chosen by the file's declared
content type (the caller has already checked it against the file's first bytes).
Nothing here calls a model: text read in process can be redacted before any
model sees it, which a file handed to a vision model cannot (supply-chain ADR
0021 amended 2026-10-09, point 5).

What each one reads, measured with the pinned libraries:

- **PDF:** the text layer, page by page (pypdf). A scan has none and yields an
  empty text; the caller decides what an empty text means.
- **DOCX:** paragraphs and table cells, in document order (python-docx). Field
  codes and macros are never evaluated; a DOCX cannot carry a macro anyway.
- **XLSX:** every sheet's cell values as last saved (openpyxl, ``data_only``):
  a formula is never evaluated, its cached result is read.
- **EML:** the headers that matter, the plain body (an HTML-only body stripped
  of tags), and each attachment this module can read, under its own heading.
  Anything else is named and left unread.

Every text is capped at ``max_chars``; a longer one is cut and says so in its
warnings, never silently.
"""

from __future__ import annotations

import email
import email.policy
import html
import io
import re
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from typing import ClassVar

from dw_knowledge.ports import ParsedDocument

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EML = "message/rfc822"
PLAIN = "text/plain"

# Generous for a quotation, an evaluation or an email thread; a document past
# it is unusual enough to be cut with a warning rather than read whole.
DEFAULT_MAX_CHARS = 120_000

_TAG = re.compile(r"<[^>]+>")
_SPACE_RUNS = re.compile(r"[ \t]+")


def _capped(text: str, max_chars: int, title: str | None) -> ParsedDocument:
    if len(text) > max_chars:
        return ParsedDocument(text=text[:max_chars], detected_title=title, warnings=("truncated",))
    return ParsedDocument(text=text, detected_title=title)


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(f"--- Trang {number} ---\n{text}")
    return "\n\n".join(pages)


def docx_text(data: bytes) -> str:
    from docx import Document

    document = Document(io.BytesIO(data))
    parts: list[str] = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def xlsx_text(data: bytes) -> str:
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        parts: list[str] = []
        for sheet in workbook.worksheets:
            rows = []
            for row in sheet.iter_rows(values_only=True):
                cells = ["" if v is None else str(v) for v in row]
                if any(c.strip() for c in cells):
                    rows.append(" | ".join(cells).rstrip(" |"))
            if rows:
                parts.append(f"## {sheet.title}\n" + "\n".join(rows))
        return "\n\n".join(parts)
    finally:
        workbook.close()


def _html_to_text(raw: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", raw)
    text = html.unescape(_TAG.sub(" ", text))
    return "\n".join(_SPACE_RUNS.sub(" ", line).strip() for line in text.splitlines()).strip()


# What an attachment of an email is read as, by its declared type.
_ATTACHMENT_READERS: dict[str, Callable[[bytes], str]] = {
    PDF: pdf_text,
    DOCX: docx_text,
    XLSX: xlsx_text,
    PLAIN: lambda data: data.decode("utf-8", errors="replace"),
}


def eml_text(data: bytes, *, depth: int = 0) -> str:
    message = email.message_from_bytes(data, policy=email.policy.default)
    assert isinstance(message, EmailMessage)
    parts = [
        f"{name}: {message[name]}"
        for name in ("From", "To", "Cc", "Date", "Subject")
        if message[name] is not None
    ]
    body = message.get_body(preferencelist=("plain", "html"))
    if body is not None:
        content = body.get_content()
        if body.get_content_type() == "text/html":
            content = _html_to_text(content)
        parts.append("")
        parts.append(str(content).strip())
    for attachment in message.iter_attachments():
        name = attachment.get_filename() or "(không tên)"
        content_type = attachment.get_content_type()
        payload = attachment.get_payload(decode=True)
        reader = _ATTACHMENT_READERS.get(content_type)
        if content_type == EML and depth < 2 and isinstance(payload, bytes):
            reader = lambda inner: eml_text(inner, depth=depth + 1)  # noqa: E731
        if reader is None or not isinstance(payload, bytes):
            parts.append(f"\n[tệp đính kèm chưa đọc: {name} ({content_type})]")
            continue
        try:
            inner = reader(payload).strip()
        except Exception:
            inner = ""
        parts.append(f"\n--- Tệp đính kèm: {name} ---\n{inner or '[không đọc được nội dung]'}")
    return "\n".join(parts).strip()


@dataclass(frozen=True)
class InProcessDocumentParser:
    """PDF (text layer), DOCX, XLSX and EML, read without a model."""

    max_chars: int = DEFAULT_MAX_CHARS

    _READERS: ClassVar[dict[str, Callable[[bytes], str]]] = {
        PDF: pdf_text,
        DOCX: docx_text,
        XLSX: xlsx_text,
        EML: eml_text,
    }

    def supports(self, content_type: str, filename: str) -> bool:
        return content_type in self._READERS

    async def parse(self, data: bytes, content_type: str, filename: str) -> ParsedDocument:
        reader = self._READERS.get(content_type)
        if reader is None:
            raise ValueError(f"no in-process reader for {content_type!r}")
        return _capped(reader(data), self.max_chars, filename)
