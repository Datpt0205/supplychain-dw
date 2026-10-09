"""Commercial data: prices, terms, payments, a supplier's contact and bank account
(ADR 0026, E15; ticket ai-automation/01).

What this module owns:

- **The fixed sets.** `CURRENCY_CODES` (ISO 4217, the currencies a price is
  quoted in; funds, metals and test codes are not prices) and `Incoterm`
  (Incoterms 2020). The migration's CHECKs repeat both and an integration test
  asserts they are equal, the way `DocumentType` and its CHECK are held.
- **Which fields are commercial.** `PRICE_FIELDS` is the one list of what a
  caller without `supply_chain.commercial.read` reads as redacted, and what a
  channel that leaves the portal (Zalo) must never carry. A projection for a
  channel is checked against it by a test, so a price added to a channel's view
  goes red instead of out.
- **Totals.** An order total is computed here from typed lines; it is never
  stored, never typed by a person, and never taken from a model. A line
  without a quantity or a price makes the total unknown (None), not zero.
- **Bank account numbers** are compared here, by code, after normalisation.
  They never reach a prompt (ADR 0021 amended 2026-10-09, point 5).
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from dw_kernel.errors import DomainError
from dw_supply_chain.domain.case_document import DocumentType

# ISO 4217 currencies a price can be quoted in (list as of 2025, with XCG
# replacing ANG and SLE, VES, ZWG the current forms). Fund codes (BOV, CHE,
# CHW, CLF, COU, MXV, USN, UYI, UYW), precious metals and the X-test codes are
# left out on purpose: none of them is a price a supplier writes.
CURRENCY_CODES: frozenset[str] = frozenset(
    [
        "AED",
        "AFN",
        "ALL",
        "AMD",
        "AOA",
        "ARS",
        "AUD",
        "AWG",
        "AZN",
        "BAM",
        "BBD",
        "BDT",
        "BGN",
        "BHD",
        "BIF",
        "BMD",
        "BND",
        "BOB",
        "BRL",
        "BSD",
        "BTN",
        "BWP",
        "BYN",
        "BZD",
        "CAD",
        "CDF",
        "CHF",
        "CLP",
        "CNY",
        "COP",
        "CRC",
        "CUP",
        "CVE",
        "CZK",
        "DJF",
        "DKK",
        "DOP",
        "DZD",
        "EGP",
        "ERN",
        "ETB",
        "EUR",
        "FJD",
        "FKP",
        "GBP",
        "GEL",
        "GHS",
        "GIP",
        "GMD",
        "GNF",
        "GTQ",
        "GYD",
        "HKD",
        "HNL",
        "HTG",
        "HUF",
        "IDR",
        "ILS",
        "INR",
        "IQD",
        "IRR",
        "ISK",
        "JMD",
        "JOD",
        "JPY",
        "KES",
        "KGS",
        "KHR",
        "KMF",
        "KPW",
        "KRW",
        "KWD",
        "KYD",
        "KZT",
        "LAK",
        "LBP",
        "LKR",
        "LRD",
        "LSL",
        "LYD",
        "MAD",
        "MDL",
        "MGA",
        "MKD",
        "MMK",
        "MNT",
        "MOP",
        "MRU",
        "MUR",
        "MVR",
        "MWK",
        "MXN",
        "MYR",
        "MZN",
        "NAD",
        "NGN",
        "NIO",
        "NOK",
        "NPR",
        "NZD",
        "OMR",
        "PAB",
        "PEN",
        "PGK",
        "PHP",
        "PKR",
        "PLN",
        "PYG",
        "QAR",
        "RON",
        "RSD",
        "RUB",
        "RWF",
        "SAR",
        "SBD",
        "SCR",
        "SDG",
        "SEK",
        "SGD",
        "SHP",
        "SLE",
        "SOS",
        "SRD",
        "SSP",
        "STN",
        "SVC",
        "SYP",
        "SZL",
        "THB",
        "TJS",
        "TMT",
        "TND",
        "TOP",
        "TRY",
        "TTD",
        "TWD",
        "TZS",
        "UAH",
        "UGX",
        "USD",
        "UYU",
        "UZS",
        "VED",
        "VES",
        "VND",
        "VUV",
        "WST",
        "XAF",
        "XCD",
        "XCG",
        "XOF",
        "XPF",
        "YER",
        "ZAR",
        "ZMW",
        "ZWG",
    ]
)


class Incoterm(StrEnum):
    """Incoterms 2020: who carries cost and risk to where."""

    EXW = "EXW"
    FCA = "FCA"
    CPT = "CPT"
    CIP = "CIP"
    DAP = "DAP"
    DPU = "DPU"
    DDP = "DDP"
    FAS = "FAS"
    FOB = "FOB"
    CFR = "CFR"
    CIF = "CIF"


class PaymentKind(StrEnum):
    """Step 11's deposit and step 16's final payment."""

    DEPOSIT = "deposit"
    FINAL = "final"


# Every field name that carries a price, an amount, a payment term or a bank
# detail, on any record of this module and any view built from one. The one
# owner of "what is commercial": redaction reads it, and a channel's view is
# tested against it.
PRICE_FIELDS: frozenset[str] = frozenset(
    {
        "unit_price",
        "amount",
        "order_total",
        "line_total",
        "deposit_amount",
        "deposit_percent",
        # What a supplier was already paid as its deposit (ticket
        # ai-automation/15): the final payment request prints it.
        "deposit_paid",
        "payment_terms",
        "account_number",
        "account_holder",
        "bank_name",
    }
)

# The case documents whose file prints prices: the approved PO, which code
# wrote from a draft's price fields (ticket ai-automation/14), and the payment
# papers of steps 11 and 16 (ticket ai-automation/15): the deposit and final
# payment requests code drafts, and the supplier's invoices and the bank's
# transfer receipt, which state amounts and a beneficiary account. Reading
# one's file needs `supply_chain.commercial.read` besides the document scope.
PRICED_DOCUMENT_TYPES: frozenset[DocumentType] = frozenset(
    {
        DocumentType.PURCHASE_ORDER,
        DocumentType.DEPOSIT_DOCS,
        DocumentType.PAYMENT_DOCS,
        DocumentType.PROFORMA_INVOICE,
        DocumentType.COMMERCIAL_INVOICE,
        DocumentType.BANK_TRANSFER_RECEIPT,
    }
)

# The fields of a bank account record. Never a prompt variable: a test walks
# every registered prompt against this set.
BANK_ACCOUNT_FIELDS: frozenset[str] = frozenset({"account_number", "account_holder", "bank_name"})

_MAX_TERMS = 500
_MAX_NAME = 200
_MAX_EMAIL = 254
# Digits as numeric(18, 4): fourteen before the point.
_MAX_PRICE = Decimal("99999999999999.9999")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def currency_code(raw: str) -> str:
    """The ISO 4217 code `raw` names, upper-cased, or a 422."""
    code = raw.strip().upper()
    if code not in CURRENCY_CODES:
        raise DomainError("tiền tệ không phải mã ISO 4217", details={"currency": raw[:8]})
    return code


def price(raw: Decimal | int | str, *, name: str = "unit_price") -> Decimal:
    """A non-negative amount with at most four decimals."""
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise DomainError("giá không hợp lệ", details={"field": name}) from exc
    if not value.is_finite() or value < 0 or value > _MAX_PRICE:
        raise DomainError("giá phải là số không âm", details={"field": name})
    if value.as_tuple().exponent < -4:  # type: ignore[operator]
        raise DomainError("giá tối đa 4 chữ số thập phân", details={"field": name})
    return value


def amount(raw: Decimal | int | str) -> Decimal:
    """A payment amount: strictly positive, at most two decimals."""
    value = price(raw, name="amount")
    if value <= 0:
        raise DomainError("số tiền phải lớn hơn 0", details={"field": "amount"})
    if value.as_tuple().exponent < -2:  # type: ignore[operator]
        raise DomainError("số tiền tối đa 2 chữ số thập phân", details={"field": "amount"})
    return value


def _optional_text(value: str | None, name: str, limit: int) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > limit:
        raise DomainError(f"{name} tối đa {limit} ký tự", details={"field": name})
    return text


def _required_text(value: str, name: str, limit: int) -> str:
    text = _optional_text(value, name, limit)
    if text is None:
        raise DomainError(f"{name} không được trống", details={"field": name})
    return text


@dataclass(frozen=True, slots=True)
class CommercialTerms:
    """The commercial terms of a PO case (columns on `po_cases`)."""

    currency: str | None = None
    incoterm: Incoterm | None = None
    payment_terms: str | None = None
    deposit_percent: Decimal | None = None
    expected_delivery_date: date | None = None

    @classmethod
    def of(
        cls,
        *,
        currency: str | None,
        incoterm: Incoterm | None,
        payment_terms: str | None,
        deposit_percent: Decimal | int | str | None,
        expected_delivery_date: date | None,
    ) -> CommercialTerms:
        percent = None
        if deposit_percent is not None:
            percent = price(deposit_percent, name="deposit_percent")
            if percent > 100:
                raise DomainError(
                    "% đặt cọc phải từ 0 đến 100", details={"field": "deposit_percent"}
                )
        return cls(
            currency=None if currency is None else currency_code(currency),
            incoterm=incoterm,
            payment_terms=_optional_text(payment_terms, "payment_terms", _MAX_TERMS),
            deposit_percent=percent,
            expected_delivery_date=expected_delivery_date,
        )


@dataclass(frozen=True, slots=True)
class PricedLine:
    """One line of a PO case with its unit price (NULL until someone sets it)."""

    sku_id: uuid.UUID
    quantity: int | None
    unit_price: Decimal | None
    sku_code: str | None = None
    variant_label: str | None = None

    @property
    def line_total(self) -> Decimal | None:
        if self.quantity is None or self.unit_price is None:
            return None
        return self.unit_price * self.quantity


def order_total(lines: Iterable[PricedLine]) -> Decimal | None:
    """The order's total, computed; unknown when any line lacks a quantity or a
    price, and unknown for an order with no line at all."""
    total = Decimal(0)
    seen = False
    for line in lines:
        line_total = line.line_total
        if line_total is None:
            return None
        total += line_total
        seen = True
    return total if seen else None


@dataclass(frozen=True, slots=True)
class POPayment:
    """One recorded version of a deposit or a final payment (append-only: a
    later version of the same kind supersedes an earlier one).

    `amount` is None only in a reading redacted for a caller without the
    commercial scope (`POCommercial.without_prices`); a stored payment always
    has one (`ck_po_payments_amount`)."""

    id: uuid.UUID
    po_case_id: uuid.UUID
    kind: PaymentKind
    version: int
    amount: Decimal | None
    currency: str
    due_date: date | None
    paid_on: date | None
    document_id: uuid.UUID | None
    recorded_by: uuid.UUID
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class NewPOPayment:
    id: uuid.UUID
    po_case_id: uuid.UUID
    kind: PaymentKind
    amount: Decimal
    currency: str
    due_date: date | None
    paid_on: date | None
    document_id: uuid.UUID | None

    @classmethod
    def of(
        cls,
        *,
        id: uuid.UUID,
        po_case_id: uuid.UUID,
        kind: PaymentKind,
        amount_value: Decimal | int | str,
        currency: str,
        due_date: date | None,
        paid_on: date | None,
        document_id: uuid.UUID | None,
    ) -> NewPOPayment:
        return cls(
            id=id,
            po_case_id=po_case_id,
            kind=kind,
            amount=amount(amount_value),
            currency=currency_code(currency),
            due_date=due_date,
            paid_on=paid_on,
            document_id=document_id,
        )


def latest_payments(payments: Iterable[POPayment]) -> dict[PaymentKind, POPayment]:
    """The current version of each kind."""
    current: dict[PaymentKind, POPayment] = {}
    for payment in payments:
        held = current.get(payment.kind)
        if held is None or payment.version > held.version:
            current[payment.kind] = payment
    return current


@dataclass(frozen=True, slots=True)
class ProfileCommercial:
    """The typed columns of a BM04 profile that code computes with. Price and
    currency need the commercial scope to write; MOQ, lead time and Incoterm
    are planning data any profile writer sets."""

    unit_price: Decimal | None = None
    currency: str | None = None
    moq: int | None = None
    lead_time_days: int | None = None
    incoterm: Incoterm | None = None

    @classmethod
    def of(
        cls,
        *,
        unit_price: Decimal | int | str | None,
        currency: str | None,
        moq: int | None,
        lead_time_days: int | None,
        incoterm: Incoterm | None,
    ) -> ProfileCommercial:
        if moq is not None and moq <= 0:
            raise DomainError("MOQ phải lớn hơn 0", details={"field": "moq"})
        if lead_time_days is not None and not 0 <= lead_time_days <= 3650:
            raise DomainError(
                "thời gian sản xuất phải từ 0 đến 3650 ngày", details={"field": "lead_time_days"}
            )
        if unit_price is not None and currency is None:
            raise DomainError("giá cần kèm tiền tệ", details={"field": "currency"})
        return cls(
            unit_price=None if unit_price is None else price(unit_price),
            currency=None if currency is None else currency_code(currency),
            moq=moq,
            lead_time_days=lead_time_days,
            incoterm=incoterm,
        )

    def with_prices_of(self, other: ProfileCommercial | None) -> ProfileCommercial:
        """These planning fields, with `other`'s price and currency: what a
        writer without the commercial scope saves, so a new version never
        drops (or invents) a price they could not see."""
        return ProfileCommercial(
            unit_price=None if other is None else other.unit_price,
            currency=None if other is None else other.currency,
            moq=self.moq,
            lead_time_days=self.lead_time_days,
            incoterm=self.incoterm,
        )

    @property
    def has_prices(self) -> bool:
        return self.unit_price is not None or self.currency is not None


@dataclass(frozen=True, slots=True)
class ProductProfile:
    """One saved version of a product's BM04 (append-only)."""

    id: uuid.UUID
    product_dev_case_id: uuid.UUID
    version: int
    commercial: ProfileCommercial
    attributes: dict[str, Any]
    schema_version: str
    created_by: uuid.UUID
    created_at: datetime

    def without_prices(self) -> ProductProfile:
        """The profile with its price emptied (currency, MOQ, lead time and
        Incoterm are planning data and stay)."""
        return replace(self, commercial=replace(self.commercial, unit_price=None))


@dataclass(frozen=True, slots=True)
class NewProductProfile:
    id: uuid.UUID
    product_dev_case_id: uuid.UUID
    commercial: ProfileCommercial
    attributes: dict[str, Any]
    schema_version: str


@dataclass(frozen=True, slots=True)
class SupplierContact:
    """The person a supplier is reached through (append-only versions)."""

    id: uuid.UUID
    supplier_id: uuid.UUID
    version: int
    name: str
    email: str | None
    phone: str | None
    created_by: uuid.UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NewSupplierContact:
    id: uuid.UUID
    supplier_id: uuid.UUID
    name: str
    email: str | None
    phone: str | None

    @classmethod
    def of(
        cls,
        *,
        id: uuid.UUID,
        supplier_id: uuid.UUID,
        name: str,
        email: str | None,
        phone: str | None,
    ) -> NewSupplierContact:
        clean_email = _optional_text(email, "email", _MAX_EMAIL)
        if clean_email is not None and not _EMAIL.fullmatch(clean_email):
            raise DomainError("email không hợp lệ", details={"field": "email"})
        return cls(
            id=id,
            supplier_id=supplier_id,
            name=_required_text(name, "name", _MAX_NAME),
            email=clean_email,
            phone=_optional_text(phone, "phone", 40),
        )


_ACCOUNT_CHARS = re.compile(r"[^0-9A-Z]")


def normalize_account_number(raw: str) -> str:
    """Digits and letters only, upper-cased: '0071 000-123 456' and
    '0071000123456' are one account."""
    return _ACCOUNT_CHARS.sub("", raw.upper())


def same_account(a: str, b: str) -> bool:
    """Whether two written account numbers are the same account. Code compares;
    a model never sees either."""
    left, right = normalize_account_number(a), normalize_account_number(b)
    return bool(left) and left == right


def account_digest(raw: str) -> str:
    """What a reading keeps of an account number a document names (ticket
    ai-automation/15): the SHA-256 of its normalised form, so code compares it
    with the supplier's master account (`account_digest(master) == digest`)
    without the number itself being stored beside the reading, where a later
    prompt could pick it up. Equal digests are `same_account`."""
    return hashlib.sha256(normalize_account_number(raw).encode("ascii", "ignore")).hexdigest()


@dataclass(frozen=True, slots=True)
class SupplierBankAccount:
    """A supplier's bank account (append-only versions). Read and compared by
    code only; never a prompt variable."""

    id: uuid.UUID
    supplier_id: uuid.UUID
    version: int
    bank_name: str
    account_number: str
    account_holder: str
    created_by: uuid.UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NewSupplierBankAccount:
    id: uuid.UUID
    supplier_id: uuid.UUID
    bank_name: str
    account_number: str
    account_holder: str

    @classmethod
    def of(
        cls,
        *,
        id: uuid.UUID,
        supplier_id: uuid.UUID,
        bank_name: str,
        account_number: str,
        account_holder: str,
    ) -> NewSupplierBankAccount:
        number = normalize_account_number(account_number)
        if not 6 <= len(number) <= 34:
            raise DomainError(
                "số tài khoản phải có 6-34 chữ số hoặc chữ cái",
                details={"field": "account_number"},
            )
        return cls(
            id=id,
            supplier_id=supplier_id,
            bank_name=_required_text(bank_name, "bank_name", _MAX_NAME),
            account_number=number,
            account_holder=_required_text(account_holder, "account_holder", _MAX_NAME),
        )


@dataclass(frozen=True, slots=True)
class POCommercial:
    """Everything commercial about one PO case, as read."""

    terms: CommercialTerms
    lines: tuple[PricedLine, ...] = ()
    payments: tuple[POPayment, ...] = field(default=())

    @property
    def order_total(self) -> Decimal | None:
        return order_total(self.lines)

    def without_prices(self) -> POCommercial:
        """What a caller without `supply_chain.commercial.read` may read: every
        field of `PRICE_FIELDS` emptied, everything else as stored."""
        return POCommercial(
            terms=replace(self.terms, payment_terms=None, deposit_percent=None),
            lines=tuple(replace(line, unit_price=None) for line in self.lines),
            payments=tuple(replace(p, amount=None) for p in self.payments),
        )
