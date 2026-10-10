"""DOCX templates in process (python-docx): what a template's placeholders are,
and a rendered document (`TemplateInspectorPort`, `DocumentRendererPort`).

A placeholder is `{{field}}`, or `{{field.column}}` inside one table row that
is repeated once per item of the table field. Each placeholder must sit inside
ONE run of text: a placeholder Word split across runs (a spell-check mark, a
format change in the middle) is refused when the template loads, by name,
instead of rendering as literal braces.

Rendering is substitution, nothing else:

- one pass over each run's text: a value that spells `{{other}}` stays as it
  was written, it is never read as a placeholder;
- a value is text: it becomes the run's text, so it cannot add XML, a field
  code, a hyperlink or a formula; characters XML cannot carry are dropped;
- a template with macros (`vbaProject.bin`, a macro-enabled content type) is
  refused at load, so a rendered document never carries one.
"""

from __future__ import annotations

import copy
import io
import re
import zipfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from docx import Document
from docx.document import Document as DocxDocument
from docx.table import Table, _Row
from docx.text.paragraph import Paragraph

from dw_agent_runtime.doc_templates import (
    LoadedDocTemplate,
    RenderedFile,
    TemplateFieldKind,
    TemplateValue,
)
from dw_kernel.errors import ConfigError

DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PLACEHOLDER = re.compile(r"\{\{([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)?)\}\}")
# XML 1.0 cannot carry these; python-docx would refuse the whole document.
_NOT_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿\ud800-\udfff]")


def _clean(text: str) -> str:
    return _NOT_XML.sub("", text)


def _table_paragraphs(table: Table) -> Iterator[Paragraph]:
    for row in table.rows:
        yield from _row_paragraphs(row)


def _row_paragraphs(row: _Row) -> Iterator[Paragraph]:
    for cell in row.cells:
        yield from cell.paragraphs
        for inner in cell.tables:
            yield from _table_paragraphs(inner)


def _all_tables(document: DocxDocument) -> Iterator[Table]:
    def walk(tables: Sequence[Table]) -> Iterator[Table]:
        for table in tables:
            yield table
            for row in table.rows:
                for cell in row.cells:
                    yield from walk(cell.tables)

    yield from walk(document.tables)
    for section in document.sections:
        for part in (section.header, section.footer):
            yield from walk(part.tables)


def _all_paragraphs(document: DocxDocument) -> Iterator[Paragraph]:
    yield from document.paragraphs
    for table in document.tables:
        yield from _table_paragraphs(table)
    for section in document.sections:
        for part in (section.header, section.footer):
            yield from part.paragraphs
            for table in part.tables:
                yield from _table_paragraphs(table)


def _refuse_macros(docx: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(docx)) as archive:
            names = archive.namelist()
            types = archive.read("[Content_Types].xml").decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ConfigError("template is not a DOCX file") from exc
    if any(name.lower().endswith("vbaproject.bin") for name in names) or (
        "macroenabled" in types.lower()
    ):
        raise ConfigError("template carries macros; a template may not")


class DocxTemplateInspector:
    """Implements `TemplateInspectorPort`."""

    def placeholders(self, docx: bytes) -> frozenset[str]:
        _refuse_macros(docx)
        document = Document(io.BytesIO(docx))
        found: set[str] = set()
        for paragraph in _all_paragraphs(document):
            in_runs = [name for run in paragraph.runs for name in PLACEHOLDER.findall(run.text)]
            in_text = PLACEHOLDER.findall(paragraph.text)
            if len(in_runs) != len(in_text) or (
                "{{" in paragraph.text and len(in_text) != paragraph.text.count("{{")
            ):
                raise ConfigError(
                    f"template placeholder split across runs or malformed: {paragraph.text!r}"
                )
            found.update(in_runs)
        # A table field's placeholders must share one row: that row repeats.
        for table in _all_tables(document):
            for row in table.rows:
                fields = {
                    name.split(".", 1)[0]
                    for paragraph in _row_paragraphs(row)
                    for name in PLACEHOLDER.findall(paragraph.text)
                    if "." in name
                }
                if len(fields) > 1:
                    raise ConfigError(f"one table row repeats two fields: {sorted(fields)}")
        return frozenset(found)


@dataclass(frozen=True)
class DocxRenderer:
    """Implements `DocumentRendererPort`. `missing` writes what stands where a
    value nobody filled would be, from the field's label: a gap is visible in
    the document, never blank."""

    missing: Callable[[str], str] = lambda label: f"[cần điền: {label}]"

    def render(
        self, template: LoadedDocTemplate, values: Mapping[str, TemplateValue]
    ) -> RenderedFile:
        spec = template.spec
        document = Document(io.BytesIO(template.docx))
        labels: dict[str, str] = {}
        for f in spec.fields:
            labels[f.name] = f.label
            for column in f.columns or ():
                labels[f"{f.name}.{column.name}"] = f"{f.label} / {column.label}"

        def text_of(raw: object, name: str) -> str:
            if isinstance(raw, str) and raw.strip():
                return _clean(raw)
            return self.missing(labels.get(name, name))

        # Every run is filled exactly once: a run already filled is never
        # scanned again, so a value that spells a placeholder stays text.
        filled: set[Any] = set()

        def fill(paragraph: Paragraph, item: Mapping[str, Any] | None) -> None:
            for run in paragraph.runs:
                if run._r in filled or "{{" not in run.text:
                    continue

                def value(match: re.Match[str]) -> str:
                    name = match.group(1)
                    if "." not in name:
                        return text_of(values.get(name), name)
                    column = name.split(".", 1)[1]
                    return text_of(None if item is None else item.get(column), name)

                run.text = PLACEHOLDER.sub(value, run.text)
                filled.add(run._r)

        for f in spec.fields:
            if f.kind is not TemplateFieldKind.TABLE:
                continue
            rows = values.get(f.name)
            items = [r for r in rows if isinstance(r, Mapping)] if isinstance(rows, list) else []
            for table in list(_all_tables(document)):
                for row in list(table.rows):
                    names = {
                        n
                        for p in _row_paragraphs(row)
                        for n in PLACEHOLDER.findall(p.text)
                        if n.startswith(f"{f.name}.")
                    }
                    if not names:
                        continue
                    template_tr = row._tr
                    for item in items:
                        clone = copy.deepcopy(template_tr)
                        template_tr.addprevious(clone)
                        for paragraph in _row_paragraphs(_Row(clone, table)):
                            fill(paragraph, item)
                    template_tr.getparent().remove(template_tr)

        for paragraph in _all_paragraphs(document):
            fill(paragraph, None)
        out = io.BytesIO()
        document.save(out)
        return RenderedFile(data=out.getvalue(), content_type=DOCX_CONTENT_TYPE, extension="docx")
