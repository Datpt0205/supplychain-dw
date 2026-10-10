"""The import template as an Excel workbook (ADR 0027; ticket onboarding/01),
read and written with openpyxl.

`XlsxWorkbookReader` implements `WorkbookReaderPort`: each sheet's cells as
last saved (`data_only`: a formula's cached value, never the formula), read
streaming (`read_only`) and bounded (`MAX_ROWS` + the header, and as many
columns as the widest sheet of the template), so a sheet that claims a
million rows is not walked; a zip that would inflate past
`_MAX_INFLATED_BYTES` is refused before openpyxl opens it. A cell is text: a
whole number written as a number reads without its ".0", a date as its ISO
date. A file openpyxl cannot
open is a `DomainError`, never a 500.

`build_template` writes the empty template from `SHEETS`, the one
declaration the reader checks headers against: the admin screen hands it out
and `scripts/build_import_template.py` writes the copy under
`docs/products/elmich/`. Code columns are formatted as text so Excel keeps a
leading zero.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from dw_kernel.errors import DomainError
from dw_supply_chain.domain.data_import import MAX_ROWS, SHEETS, RawSheet

_MAX_COLUMNS = max(len(s.columns) for s in SHEETS) + 5
_GUIDE = "Hướng dẫn"
# What a template-sized workbook inflates to, with room: 5,000 rows of text
# per sheet is a few megabytes of XML.
_MAX_INFLATED_BYTES = 64 * 1024 * 1024
_MAX_PARTS = 200


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _refuse_a_bomb(data: bytes) -> None:
    """A workbook is a zip: its parts' declared sizes are summed before
    openpyxl inflates any (it reads the shared strings whole), so a few
    megabytes that expand to gigabytes are refused, not parsed."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            parts = archive.infolist()
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        raise DomainError(
            "file không phải Excel (.xlsx) đọc được; dùng mẫu nạp của hệ thống"
        ) from exc
    if len(parts) > _MAX_PARTS or sum(p.file_size for p in parts) > _MAX_INFLATED_BYTES:
        raise DomainError(
            "file Excel quá lớn khi giải nén; tách thành nhiều file",
            details={"max_inflated_bytes": _MAX_INFLATED_BYTES},
        )


class XlsxWorkbookReader:
    """Implements `WorkbookReaderPort`."""

    def read(self, data: bytes) -> Mapping[str, RawSheet]:
        from openpyxl import load_workbook

        _refuse_a_bomb(data)
        try:
            workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except (zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
            raise DomainError(
                "file không phải Excel (.xlsx) đọc được; dùng mẫu nạp của hệ thống"
            ) from exc
        try:
            sheets: dict[str, RawSheet] = {}
            for sheet in workbook.worksheets:
                rows = [
                    tuple(_text(v) for v in row)
                    for row in sheet.iter_rows(
                        max_row=MAX_ROWS + 2, max_col=_MAX_COLUMNS, values_only=True
                    )
                ]
                sheets[sheet.title] = rows
            return sheets
        finally:
            workbook.close()


def build_template() -> bytes:
    """The empty template: a guide sheet, then one sheet per `SHEETS` entry
    with its headers, required ones marked, code columns as text."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    workbook = Workbook()
    guide = workbook.active
    guide.title = _GUIDE
    guide.append(["Mẫu nạp dữ liệu Supply Chain (một lần)"])
    guide["A1"].font = Font(bold=True, size=13)
    guide.append([])
    guide.append(["Cột có dấu * là bắt buộc. Không đổi tên sheet, không đổi tiêu đề cột."])
    guide.append(["Chạy thử trước trên màn Nạp dữ liệu: hệ thống báo từng dòng, không ghi gì."])
    guide.append(["Nạp lại cùng file không tạo bản sao: dòng đã có được bỏ qua."])
    guide.append([])
    for spec in SHEETS:
        guide.append([f"Sheet {spec.title}", spec.note])
    guide.column_dimensions["A"].width = 22
    guide.column_dimensions["B"].width = 110
    for spec in SHEETS:
        sheet = workbook.create_sheet(spec.title)
        sheet.append([c.header + (" *" if c.required else "") for c in spec.columns])
        for index, column in enumerate(spec.columns, start=1):
            cell = sheet.cell(row=1, column=index)
            cell.font = Font(bold=True)
            letter = cell.column_letter
            sheet.column_dimensions[letter].width = max(14, len(column.header) + 6)
            if column.text:
                for row in range(2, 502):
                    sheet.cell(row=row, column=index).number_format = "@"
        sheet.freeze_panes = "A2"
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


__all__ = ["XlsxWorkbookReader", "build_template"]
