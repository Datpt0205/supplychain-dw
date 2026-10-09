"""A one-time import of the data a tenant already has (ADR 0027, E16; ticket
onboarding/01): suppliers with their contact and bank account, the item and
SKU catalogue, and users with their roles and workspaces, from the Excel
template this repository ships.

What this module owns:

- **The template.** `SHEETS`: each sheet's title, its columns (header text,
  required, maximum length) and the column a row is keyed by. The template the
  admin screen hands out and `docs/products/elmich/import-template.xlsx` are
  both written from it, and the reader checks a workbook's headers against it,
  so the three cannot drift apart.
- **What a row says**, parsed into a typed row or refused with its row number
  and the reason. Nothing is inferred: a blank required cell is a refusal, an
  unknown header is a refusal of the sheet, and no model reads any cell.
- **Duplicates inside one file**: the second row with the same key is refused
  naming the first. Whether a key already exists in the application is the
  database's answer, asked by the handler.
- **The report**: one line per row, what happened (or would happen, in a dry
  run) and why. A bank account number never appears in it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

# A workbook bigger than this is refused before it is opened: the import is a
# few thousand rows of text, never megabytes.
MAX_IMPORT_BYTES = 5 * 1024 * 1024
# Rows per sheet, header excluded. A larger catalogue is imported in parts.
MAX_ROWS = 5000
TEMPLATE_FILENAME = "mau-nap-du-lieu-supply-chain.xlsx"
TEMPLATE_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_KEY = re.compile(r"^[^\x00-\x1f]+$")


class ImportSheet(StrEnum):
    SUPPLIERS = "suppliers"
    CATALOGUE = "catalogue"
    USERS = "users"


@dataclass(frozen=True, slots=True)
class Column:
    key: str
    header: str
    required: bool = False
    max_length: int = 200
    # Written as text in the template (a code, a phone, an account number):
    # Excel would otherwise drop a leading zero.
    text: bool = False


@dataclass(frozen=True, slots=True)
class SheetSpec:
    sheet: ImportSheet
    title: str
    columns: tuple[Column, ...]
    note: str

    def column(self, key: str) -> Column:
        return next(c for c in self.columns if c.key == key)


SHEETS: tuple[SheetSpec, ...] = (
    SheetSpec(
        sheet=ImportSheet.SUPPLIERS,
        title="NCC",
        columns=(
            Column("code", "Mã NCC", required=True, max_length=64, text=True),
            Column("name", "Tên NCC", required=True, max_length=200),
            Column("contact_name", "Người liên hệ", max_length=200),
            Column("contact_email", "Email liên hệ", max_length=254),
            Column("contact_phone", "Điện thoại", max_length=40, text=True),
            Column("bank_name", "Ngân hàng", max_length=200),
            Column("account_number", "Số tài khoản", max_length=60, text=True),
            Column("account_holder", "Chủ tài khoản", max_length=200),
        ),
        note=(
            "Mỗi dòng một NCC, khóa theo Mã NCC. Tài khoản: điền đủ ba ô Ngân hàng, Số tài"
            " khoản, Chủ tài khoản, hoặc để trống cả ba."
        ),
    ),
    SheetSpec(
        sheet=ImportSheet.CATALOGUE,
        title="Danh mục",
        columns=(
            Column("item_code", "Mã hàng", required=True, max_length=64, text=True),
            Column("sku_code", "Mã SKU", max_length=64, text=True),
            Column("name", "Tên hàng", required=True, max_length=300),
            Column("category", "Category", max_length=100),
        ),
        note=(
            "Mỗi dòng một SKU (hoặc một mã hàng chưa có SKU, để trống Mã SKU). Bước 9 kiểm"
            " trùng mã với danh mục này."
        ),
    ),
    SheetSpec(
        sheet=ImportSheet.USERS,
        title="Người dùng",
        columns=(
            Column("email", "Email", required=True, max_length=254),
            Column("display_name", "Họ tên", required=True, max_length=120),
            Column("roles", "Vai", required=True, max_length=500),
            Column("workspaces", "Workspace", max_length=500),
        ),
        note=(
            "Vai là mã vai, cách nhau bởi dấu phẩy (ví dụ sc_operator, sc_finance). Workspace"
            " là mã (slug), cách nhau bởi dấu phẩy; để trống là workspace đang nạp."
        ),
    ),
)
SHEET_BY_KIND: Mapping[ImportSheet, SheetSpec] = {s.sheet: s for s in SHEETS}


class RowStatus(StrEnum):
    """What happened to a row, or would in a dry run."""

    CREATED = "created"
    EXISTS = "exists"
    PARTIAL = "partial"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class RowResult:
    sheet: ImportSheet
    # The Excel row number (the header is row 1).
    row: int
    # The row's key as written (a code, an email); empty when unreadable.
    key: str
    status: RowStatus
    messages: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SheetProblem:
    """A sheet that cannot be read at all (its headers), never a row's."""

    sheet: ImportSheet
    message: str


@dataclass(frozen=True, slots=True)
class ImportReport:
    dry_run: bool
    rows: tuple[RowResult, ...]
    problems: tuple[SheetProblem, ...] = ()

    def count(self, sheet: ImportSheet, status: RowStatus) -> int:
        return sum(1 for r in self.rows if r.sheet is sheet and r.status is status)


# A workbook as the reader hands it over: each sheet's rows, cells as text or
# None, the header row first.
RawSheet = Sequence[Sequence[str | None]]


@dataclass(frozen=True, slots=True)
class ParsedRow:
    row: int
    cells: Mapping[str, str | None]

    def get(self, key: str) -> str | None:
        return self.cells.get(key)

    def text(self, key: str) -> str:
        value = self.cells.get(key)
        assert value is not None  # a required cell, checked by `parse_sheet`
        return value


@dataclass(frozen=True, slots=True)
class ParsedSheet:
    rows: tuple[ParsedRow, ...] = ()
    refused: tuple[RowResult, ...] = ()
    problem: SheetProblem | None = None


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _header_key(header: str | None) -> str:
    """A header as matched: spacing and case aside, and the template's
    required-column mark (a trailing `*`) too."""
    return " ".join((header or "").strip().rstrip("*").split()).casefold()


def parse_sheet(spec: SheetSpec, raw: RawSheet) -> ParsedSheet:
    """The sheet's rows keyed by column, each required cell present and no
    cell longer than its column allows; a row that is not is refused by
    number. Columns are matched by header text (case and spacing aside), in
    any order; an unknown header or a missing required one refuses the
    sheet, since every row would be read wrong. A wholly blank row is
    skipped, and a sheet over `MAX_ROWS` is refused whole."""
    if not raw:
        return ParsedSheet(problem=SheetProblem(spec.sheet, "Sheet không có dòng tiêu đề"))
    by_header = {_header_key(c.header): c for c in spec.columns}
    positions: dict[int, Column] = {}
    unknown: list[str] = []
    for index, header in enumerate(raw[0]):
        if _header_key(header) == "":
            continue
        column = by_header.get(_header_key(header))
        if column is None:
            unknown.append(str(header))
        elif column in positions.values():
            unknown.append(f"{header} (lặp)")
        else:
            positions[index] = column
    missing = [c.header for c in spec.columns if c.required and c not in positions.values()]
    if unknown or missing:
        parts = []
        if unknown:
            parts.append("cột không thuộc mẫu: " + ", ".join(unknown))
        if missing:
            parts.append("thiếu cột: " + ", ".join(missing))
        return ParsedSheet(problem=SheetProblem(spec.sheet, "; ".join(parts)))
    body = raw[1:]
    filled = [(n, r) for n, r in enumerate(body, start=2) if any(_clean(c) for c in r)]
    if len(filled) > MAX_ROWS:
        return ParsedSheet(
            problem=SheetProblem(spec.sheet, f"tối đa {MAX_ROWS} dòng mỗi sheet; nạp làm nhiều lần")
        )
    rows: list[ParsedRow] = []
    refused: list[RowResult] = []
    for number, cells in filled:
        values: dict[str, str | None] = {c.key: None for c in spec.columns}
        for index, column in positions.items():
            values[column.key] = _clean(cells[index]) if index < len(cells) else None
        errors = [f"thiếu {c.header}" for c in spec.columns if c.required and values[c.key] is None]
        errors += [
            f"{c.header} dài quá {c.max_length} ký tự"
            for c in spec.columns
            if values[c.key] is not None and len(values[c.key] or "") > c.max_length
        ]
        errors += [
            f"{c.header} có ký tự điều khiển"
            for c in spec.columns
            if values[c.key] is not None and not _KEY.fullmatch(values[c.key] or "")
        ]
        key_column = spec.columns[0]
        if errors:
            refused.append(
                RowResult(
                    spec.sheet,
                    number,
                    values[key_column.key] or "",
                    RowStatus.REJECTED,
                    tuple(errors),
                )
            )
            continue
        rows.append(ParsedRow(number, values))
    return ParsedSheet(rows=tuple(rows), refused=tuple(refused))


def first_of_each(
    sheet: ImportSheet, rows: Sequence[ParsedRow], key: tuple[str, ...], label: str
) -> tuple[list[ParsedRow], list[RowResult]]:
    """Rows whose `key` cells repeat an earlier row's are refused naming it;
    the first keeps its place. Keys compare as written, case aside."""
    seen: dict[tuple[str, ...], int] = {}
    kept: list[ParsedRow] = []
    refused: list[RowResult] = []
    for row in rows:
        value = tuple((row.get(k) or "").casefold() for k in key)
        if value in seen:
            refused.append(
                RowResult(
                    sheet,
                    row.row,
                    row.get(key[0]) or "",
                    RowStatus.REJECTED,
                    (f"{label} trùng dòng {seen[value]} trong file",),
                )
            )
            continue
        seen[value] = row.row
        kept.append(row)
    return kept, refused


# ------------------------------------------------------------------ users --


def split_list(value: str | None) -> tuple[str, ...]:
    """A comma-separated cell as its distinct items, in order."""
    if value is None:
        return ()
    items = [i.strip() for i in value.split(",")]
    return tuple(dict.fromkeys(i for i in items if i))


def valid_email(value: str) -> bool:
    return bool(_EMAIL.fullmatch(value))


@dataclass(frozen=True, slots=True)
class RowOutcome:
    """A row's parts as the handler settles them: each part's status and
    words, combined into the row's one status."""

    parts: list[tuple[RowStatus, str]] = field(default_factory=list)

    def add(self, status: RowStatus, message: str) -> None:
        self.parts.append((status, message))

    def status(self) -> RowStatus:
        statuses = {s for s, _ in self.parts}
        if not statuses or statuses == {RowStatus.EXISTS}:
            return RowStatus.EXISTS
        if RowStatus.REJECTED in statuses:
            done = statuses & {RowStatus.CREATED, RowStatus.EXISTS}
            return RowStatus.PARTIAL if RowStatus.CREATED in done else RowStatus.REJECTED
        return RowStatus.CREATED

    def result(self, sheet: ImportSheet, row: int, key: str) -> RowResult:
        return RowResult(sheet, row, key, self.status(), tuple(m for _, m in self.parts))


__all__ = [
    "MAX_IMPORT_BYTES",
    "MAX_ROWS",
    "SHEETS",
    "SHEET_BY_KIND",
    "TEMPLATE_CONTENT_TYPE",
    "TEMPLATE_FILENAME",
    "Column",
    "ImportReport",
    "ImportSheet",
    "ParsedRow",
    "ParsedSheet",
    "RawSheet",
    "RowOutcome",
    "RowResult",
    "RowStatus",
    "SheetProblem",
    "SheetSpec",
    "first_of_each",
    "parse_sheet",
    "split_list",
    "valid_email",
]
