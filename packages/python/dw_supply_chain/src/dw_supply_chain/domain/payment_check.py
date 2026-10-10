"""Steps 11 and 16 checked by code (ticket ai-automation/15; ADR 0025, ADR 0026).

The model reads a supplier's proforma or commercial invoice and the bank's
transfer receipt (UNC) into cited fields; this module decides what they are
worth against the PO case:

- **Amounts are code's.** The deposit due is the order total times the PO's
  deposit %, to the cent; the balance is the order total less the deposit
  recorded as paid. A total, a deposit or a transfer a document states is
  compared with those to the cent; a document that states none is named, not
  read as agreeing.
- **The beneficiary account is code's.** The extraction lane keeps only the
  digests of the account numbers a document names (`ACCOUNTS_FIELD`); here
  they are compared with the digest of the supplier's master account. One that
  differs is a red finding (an invoice or a transfer to a changed account is
  how payment fraud looks); nothing is blocked by it, a person decides.
- **Lines match by SKU** (the three-way match of step 16: PO, invoice and,
  once ticket 17 reads it, the packing list): a quantity or a unit price
  that differs, a SKU the PO does not have, a PO line the invoice leaves out.

Findings carry no amount and no account number: the PO case page shows them to
anyone who may read the case, with or without the commercial scope.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.extraction import (
    ACCOUNTS_FIELD,
    ACCOUNTS_UNREAD_FIELD,
    ExtractionStatus,
)
from dw_supply_chain.domain.purchase_order_draft import to_decimal
from dw_supply_chain.domain.step_proposal import Finding

CENT = Decimal("0.01")

# What a finding calls each paper, in words a person reads.
DOC_WORDS: Mapping[DocumentType, str] = {
    DocumentType.PROFORMA_INVOICE: "PI của NCC",
    DocumentType.COMMERCIAL_INVOICE: "hóa đơn thương mại",
    DocumentType.BANK_TRANSFER_RECEIPT: "UNC",
    DocumentType.DEPOSIT_DOCS: "hồ sơ đặt cọc",
    DocumentType.PAYMENT_DOCS: "hồ sơ thanh toán",
    # Steps 13-15 (ticket ai-automation/17).
    DocumentType.PRODUCTION_SCHEDULE: "lịch sản xuất",
    DocumentType.QC_REPORT: "báo cáo QC",
    DocumentType.PACKING_LIST: "packing list",
    DocumentType.BILL_OF_LADING: "vận đơn",
    DocumentType.ARRIVAL_NOTICE: "giấy báo hàng đến",
    DocumentType.CERTIFICATE_OF_ORIGIN: "C/O",
    # Step 17 (ticket ai-automation/18).
    DocumentType.WAREHOUSE_RECEIPT: "phiếu nhập kho",
    DocumentType.DISCREPANCY_REPORT: "biên bản chênh lệch",
}


def doc_words(doc_type: DocumentType) -> str:
    return DOC_WORDS.get(doc_type, doc_type.value)


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def same_amount(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) < CENT


@dataclass(frozen=True, slots=True)
class PaymentFacts:
    """What the PO case says about money, as code read it."""

    currency: str | None
    order_total: Decimal | None
    deposit_percent: Decimal | None
    # The deposit recorded as paid (the latest `po_payments` deposit), None
    # when none is recorded.
    deposit_paid: Decimal | None
    # The digest of the supplier's current master account, None when the
    # supplier has none in the directory.
    master_digest: str | None

    @property
    def deposit_due(self) -> Decimal | None:
        if self.order_total is None or self.deposit_percent is None:
            return None
        return cents(self.order_total * self.deposit_percent / Decimal(100))

    @property
    def balance_due(self) -> Decimal | None:
        if self.order_total is None or self.deposit_paid is None:
            return None
        return cents(self.order_total - self.deposit_paid)


@dataclass(frozen=True, slots=True)
class SourceRead:
    """The newest document of a type on the case and its reading under the
    current prompt: no document (`document_id` None), not read yet (`status`
    None), or read with a status."""

    doc_type: DocumentType
    document_id: uuid.UUID | None = None
    sha256: str | None = None
    extraction_id: uuid.UUID | None = None
    status: ExtractionStatus | None = None
    fields: Mapping[str, Any] = field(default_factory=dict)

    @property
    def extracted(self) -> bool:
        return self.status is ExtractionStatus.EXTRACTED

    def value(self, name: str) -> Any:
        entry = self.fields.get(name)
        return entry.get("value") if isinstance(entry, Mapping) else None

    def quote(self, name: str) -> str | None:
        entry = self.fields.get(name)
        quote = entry.get("quote") if isinstance(entry, Mapping) else None
        return quote if isinstance(quote, str) else None

    def number(self, name: str) -> Decimal | None:
        return to_decimal(self.value(name))


def source_findings(source: SourceRead) -> list[Finding]:
    """A document the step reads that is absent or not readable."""
    words = doc_words(source.doc_type)
    subject = source.doc_type.value
    if source.document_id is None:
        return [Finding("source_missing", subject, f"Chưa có {words} trên hồ sơ")]
    if source.status is None:
        return [Finding("source_pending", subject, f"Máy đang đọc {words}; mở lại sau ít phút")]
    if source.status is ExtractionStatus.UNREADABLE:
        return [
            Finding(
                "source_unread",
                subject,
                f"Máy không đọc được {words} (ảnh hay bản quét quá mờ để OCR đọc chắc);"
                " người kiểm bằng mắt",
            )
        ]
    if not source.extracted:
        return [Finding("source_unread", subject, f"Máy chưa đọc được {words}; người kiểm")]
    return []


class AccountCheck(StrEnum):
    MATCHES = "matches"
    DIFFERS = "differs"
    NO_MASTER = "no_master"
    NOT_STATED = "not_stated"
    # Read by OCR (ticket ai-automation/21): code does not read an account
    # from an image, so it cannot say whether it matches.
    NOT_READ = "not_read"


def check_accounts(source: SourceRead, master_digest: str | None) -> AccountCheck:
    """The accounts the document names against the supplier's master: any one
    that is not the master's is a difference."""
    if source.fields.get(ACCOUNTS_UNREAD_FIELD):
        return AccountCheck.NOT_READ
    marks = source.fields.get(ACCOUNTS_FIELD)
    digests = [
        m["digest"]
        for m in (marks if isinstance(marks, list) else [])
        if isinstance(m, Mapping) and isinstance(m.get("digest"), str)
    ]
    if not digests:
        return AccountCheck.NOT_STATED
    if master_digest is None:
        return AccountCheck.NO_MASTER
    if any(d != master_digest for d in digests):
        return AccountCheck.DIFFERS
    return AccountCheck.MATCHES


def account_findings(source: SourceRead, master_digest: str | None) -> list[Finding]:
    if not source.extracted:
        return []
    words = doc_words(source.doc_type)
    subject = source.doc_type.value
    match check_accounts(source, master_digest):
        case AccountCheck.DIFFERS:
            return [
                Finding(
                    "account_differs",
                    subject,
                    f"Tài khoản thụ hưởng trên {words} KHÁC tài khoản trong danh mục NCC: dấu hiệu"
                    " lừa đảo đổi tài khoản; xác minh với NCC qua kênh đã biết trước khi chi",
                )
            ]
        case AccountCheck.NO_MASTER:
            return [
                Finding(
                    "account_no_master",
                    subject,
                    "NCC chưa có tài khoản trong danh mục: hệ thống không so được tài khoản"
                    " thụ hưởng; nhập tài khoản đã xác minh vào danh mục NCC",
                )
            ]
        case AccountCheck.NOT_STATED:
            return [
                Finding(
                    "account_not_stated",
                    subject,
                    f"{words.capitalize()} không nêu số tài khoản thụ hưởng; người kiểm",
                )
            ]
        case AccountCheck.NOT_READ:
            return [
                Finding(
                    "account_not_read",
                    subject,
                    f"{words.capitalize()} là ảnh hay bản quét: máy không đọc số tài khoản thụ"
                    " hưởng từ ảnh; người so bằng mắt với tài khoản trong danh mục NCC",
                )
            ]
        case AccountCheck.MATCHES:
            return []


def amount_findings(
    source: SourceRead, name: str, words: str, expected: Decimal | None
) -> list[Finding]:
    """A number the document states against code's: differs, or unstated."""
    if not source.extracted:
        return []
    doc = doc_words(source.doc_type)
    stated = source.number(name)
    if stated is None:
        return [Finding("amount_unstated", name, f"{doc.capitalize()} không nêu {words}")]
    if expected is None:
        return []
    if not same_amount(stated, expected):
        return [
            Finding(
                "amount_differs",
                name,
                f"{words.capitalize()} trên {doc} khác số hệ thống tính từ PO; kiểm trước khi chi",
            )
        ]
    return []


def currency_findings(source: SourceRead, currency: str | None) -> list[Finding]:
    if not source.extracted or currency is None:
        return []
    stated = source.value("currency")
    if not isinstance(stated, str) or not stated.strip():
        return []
    if stated.strip().upper() != currency.upper():
        return [
            Finding(
                "currency_differs",
                "currency",
                f"Tiền tệ trên {doc_words(source.doc_type)} khác tiền tệ của PO",
            )
        ]
    return []


@dataclass(frozen=True, slots=True)
class OrderedLine:
    """A PO line as code compares it: its SKU, quantity and unit price."""

    sku_code: str
    quantity: int | None
    unit_price: Decimal | None


def _sku(raw: Any) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    return "".join(raw.split()).upper()


def _cell(row: Mapping[str, Any], name: str) -> Any:
    entry = row.get(name)
    return entry.get("value") if isinstance(entry, Mapping) else None


def line_findings(
    ordered: Sequence[OrderedLine], source: SourceRead, *, prices: bool = True
) -> list[Finding]:
    """The document's lines against the PO's, by SKU: quantity (and unit
    price, for an invoice) per SKU, a SKU the PO lacks, a PO SKU the document
    leaves out, a line naming no SKU. Subjects name the SKU, never a number."""
    if not source.extracted:
        return []
    doc = doc_words(source.doc_type)
    rows = source.fields.get("lines")
    lines = [r for r in rows if isinstance(r, Mapping)] if isinstance(rows, list) else []
    by_sku = {_sku(line.sku_code): line for line in ordered}
    found: list[Finding] = []
    seen: dict[str, Decimal] = {}
    for index, row in enumerate(lines):
        sku = _sku(_cell(row, "sku_code"))
        if sku is None:
            found.append(
                Finding(
                    "line_unmatched",
                    f"lines[{index}]",
                    f"Dòng {index + 1} của {doc} không ghi SKU: không đối chiếu được",
                )
            )
            continue
        line = by_sku.get(sku)
        if line is None:
            found.append(
                Finding("line_not_on_po", sku, f"SKU {sku} có trên {doc} nhưng không có trong PO")
            )
            continue
        quantity = to_decimal(_cell(row, "quantity"))
        if quantity is not None:
            seen[sku] = seen.get(sku, Decimal(0)) + quantity
        price = to_decimal(_cell(row, "unit_price"))
        if (
            prices
            and price is not None
            and line.unit_price is not None
            and not same_amount(price, line.unit_price)
        ):
            found.append(
                Finding("line_price_differs", sku, f"Đơn giá SKU {sku} trên {doc} khác PO")
            )
    for sku, line in by_sku.items():
        if sku is None:
            continue
        if sku not in seen:
            found.append(
                Finding("line_missing", sku, f"SKU {sku} của PO không có số lượng trên {doc}")
            )
            continue
        if line.quantity is not None and seen[sku] != line.quantity:
            found.append(
                Finding("line_quantity_differs", sku, f"Số lượng SKU {sku} trên {doc} khác PO")
            )
    return found


def sku_key(raw: Any) -> str | None:
    """A SKU code as code compares it: no spaces, upper case; None for none."""
    return _sku(raw)


def line_quantities(source: SourceRead) -> dict[str, tuple[int, str | None]]:
    """The quantity a document states per SKU (summed over its lines), with
    the quote of the first line naming it: what the packing list says was
    shipped (ticket ai-automation/18). A quantity that is not a whole number
    is left out, as unread."""
    if not source.extracted:
        return {}
    rows = source.fields.get("lines")
    found: dict[str, tuple[int, str | None]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        sku = _sku(_cell(row, "sku_code"))
        quantity = to_decimal(_cell(row, "quantity"))
        if sku is None or quantity is None or quantity != quantity.to_integral_value():
            continue
        entry = row.get("quantity")
        quote = entry.get("quote") if isinstance(entry, Mapping) else None
        held, first = found.get(sku, (0, quote if isinstance(quote, str) else None))
        found[sku] = (held + int(quantity), first)
    return found


def drafted_differs(fields: Mapping[str, Any], expected: Mapping[str, Decimal | None]) -> list[str]:
    """Each amount a draft states that is not code's, by field name. A stated
    amount where code has none is a difference too."""
    differ: list[str] = []
    for name, computed in expected.items():
        entry = fields.get(name)
        raw = entry.get("value") if isinstance(entry, Mapping) else None
        if raw in (None, ""):
            continue
        stated = to_decimal(raw)
        if stated is None or computed is None or not same_amount(stated, computed):
            differ.append(name)
    return differ


def codes(findings: Iterable[Finding]) -> list[str]:
    return [f.code for f in findings]


__all__ = [
    "CENT",
    "DOC_WORDS",
    "AccountCheck",
    "OrderedLine",
    "PaymentFacts",
    "SourceRead",
    "account_findings",
    "amount_findings",
    "cents",
    "check_accounts",
    "codes",
    "currency_findings",
    "doc_words",
    "drafted_differs",
    "line_findings",
    "line_quantities",
    "same_amount",
    "sku_key",
    "source_findings",
]
