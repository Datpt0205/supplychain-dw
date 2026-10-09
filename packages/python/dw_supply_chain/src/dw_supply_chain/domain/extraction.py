"""Reading a case document into cited fields (ADR 0021 amended 2026-10-09;
ticket ai-automation/02).

The model reads; code decides what the reading is worth. What this module owns:

- **Which document types are read, and into what.** `EXTRACTION_SPECS`: one
  entry per type, naming its prompt and the kind of each field. A type with no
  entry is not read by a model at all.
- **The model's output shape.** Every field is a `Cited`: the value as the
  document writes it and the verbatim quote it came from. Nothing typed comes
  from the model: code parses numbers and dates from the strings.
- **Grounding.** A field is kept only when its quote is found in the document
  text AND its value is found in its quote (a number equal to a number the
  quote writes, a date the quote writes, a text the quote contains, a choice
  from the field's list). Anything else is a named gap, never a value.
  Arithmetic is code's: a quotation line whose total is not quantity times
  unit price, or a total that is not the sum of the lines, is a gap.
- **Redaction.** Bank account numbers and similar identifiers are replaced in
  the text before any model sees it (point 5): an IBAN, a number written after
  "STK"/"số tài khoản"/"account"/"A/C", a run of 9-19 digits, and a run of digit
  groups separated by spaces or dashes. Amounts written with thousands dots or
  commas are left alone; an amount written as one long run of digits is masked
  too, which reads back as a gap: refusing a value is recoverable, sending an
  account number is not.
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.commercial import account_digest, normalize_account_number

# What a reader returns, and what the transcription prompt is told to return,
# when a file has nothing readable (`configs/policies/attachment_ingest@1.1.0.yaml`).
UNREADABLE_MARKER = "[không đọc được nội dung]"
REDACTED_IDENTIFIER = "[đã che số tài khoản]"


class ExtractionStatus(StrEnum):
    EXTRACTED = "extracted"
    UNREADABLE = "unreadable"
    REFUSED = "refused"
    FAILED = "failed"


class FieldKind(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    CHOICE = "choice"


class GapReason(StrEnum):
    MISSING = "missing"
    QUOTE_NOT_FOUND = "quote_not_found"
    VALUE_NOT_IN_QUOTE = "value_not_in_quote"
    NOT_A_NUMBER = "not_a_number"
    NOT_A_DATE = "not_a_date"
    NOT_AN_OPTION = "not_an_option"
    NUMBER_MISMATCH = "number_mismatch"


# -------------------------------------------------------- model output ------


class Cited(BaseModel):
    """One value as the document writes it, with the verbatim quote it came
    from. Both null when the document does not say."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: str | None = Field(default=None, max_length=2000)
    quote: str | None = Field(default=None, max_length=2000)


class SampleEvaluationReading(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    result: Cited = Cited()
    evaluated_on: Cited = Cited()
    evaluator: Cited = Cited()
    sample_round: Cited = Cited()
    failed_criteria: list[Cited] = Field(default_factory=list, max_length=50)
    notes: Cited = Cited()


class SupplierConfirmationReading(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    confirmed: Cited = Cited()
    product_name: Cited = Cited()
    quantity: Cited = Cited()
    unit_price: Cited = Cited()
    currency: Cited = Cited()
    delivery_date: Cited = Cited()
    conditions: Cited = Cited()
    # The terms code compares with the BM04 (ticket ai-automation/12).
    moq: Cited = Cited()
    lead_time_days: Cited = Cited()
    specification: Cited = Cited()
    packaging: Cited = Cited()


class ProfileBm04Reading(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    product_name: Cited = Cited()
    model_code: Cited = Cited()
    material: Cited = Cited()
    dimensions: Cited = Cited()
    net_weight_g: Cited = Cited()
    packaging: Cited = Cited()
    unit_price: Cited = Cited()
    currency: Cited = Cited()
    moq: Cited = Cited()
    lead_time_days: Cited = Cited()
    incoterm: Cited = Cited()


class QuotationLineReading(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    description: Cited = Cited()
    quantity: Cited = Cited()
    unit_price: Cited = Cited()
    line_total: Cited = Cited()


class SupplierQuotationReading(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    supplier_name: Cited = Cited()
    quotation_date: Cited = Cited()
    valid_until: Cited = Cited()
    currency: Cited = Cited()
    incoterm: Cited = Cited()
    moq: Cited = Cited()
    lead_time_days: Cited = Cited()
    lines: list[QuotationLineReading] = Field(default_factory=list, max_length=200)
    total: Cited = Cited()


class InvoiceLineReading(BaseModel):
    """One line of a supplier's invoice (ticket ai-automation/15): the SKU it
    names, so code matches it with a PO line, and its numbers as written."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku_code: Cited = Cited()
    description: Cited = Cited()
    quantity: Cited = Cited()
    unit_price: Cited = Cited()
    line_total: Cited = Cited()


class ProformaInvoiceReading(BaseModel):
    """A supplier's proforma invoice (PI), step 11. No account number: the
    text the model reads has none (`redact_identifiers`); code reads it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    supplier_name: Cited = Cited()
    invoice_number: Cited = Cited()
    invoice_date: Cited = Cited()
    currency: Cited = Cited()
    lines: list[InvoiceLineReading] = Field(default_factory=list, max_length=200)
    total: Cited = Cited()
    deposit_percent: Cited = Cited()
    deposit_amount: Cited = Cited()
    beneficiary_name: Cited = Cited()
    bank_name: Cited = Cited()


class CommercialInvoiceReading(BaseModel):
    """A supplier's commercial invoice, step 16."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    supplier_name: Cited = Cited()
    invoice_number: Cited = Cited()
    invoice_date: Cited = Cited()
    currency: Cited = Cited()
    lines: list[InvoiceLineReading] = Field(default_factory=list, max_length=200)
    total: Cited = Cited()
    beneficiary_name: Cited = Cited()
    bank_name: Cited = Cited()


class BankTransferReading(BaseModel):
    """The bank's transfer receipt (UNC) Kế toán uploads after paying."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    transfer_date: Cited = Cited()
    amount: Cited = Cited()
    currency: Cited = Cited()
    beneficiary_name: Cited = Cited()
    bank_name: Cited = Cited()
    payment_content: Cited = Cited()


class PackagingDesignReading(BaseModel):
    """A packaging design proof (bản in thiết kế bao bì) at step 12 (ticket
    ai-automation/16): what the label prints, each as the proof writes it.
    Code compares it with the BM04 and the label rules; the model only reads."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    product_name: Cited = Cited()
    item_code: Cited = Cited()
    sku_codes: list[Cited] = Field(default_factory=list, max_length=50)
    dimensions: Cited = Cited()
    material: Cited = Cited()
    origin: Cited = Cited()
    responsible_party: Cited = Cited()
    usage_instructions: Cited = Cited()
    warnings: Cited = Cited()


# ------------------------------------------------------------- specs --------


@dataclass(frozen=True, slots=True)
class ExtractionSpec:
    """How one document type is read: its prompt and each field's kind. A
    field absent from `kinds` is text."""

    doc_type: DocumentType
    prompt_id: str
    prompt_version: str
    reading: type[BaseModel]
    kinds: Mapping[str, FieldKind]
    options: Mapping[str, frozenset[str]] = field(default_factory=dict)
    # Whether code reads the beneficiary account numbers the document names
    # from its text BEFORE redaction (ticket ai-automation/15), keeping their
    # digests (`ACCOUNTS_FIELD`); the model is never shown them.
    reads_accounts: bool = False
    # Whether code reads the barcodes a proof prints from its text BEFORE
    # redaction (ticket ai-automation/16, `BARCODES_FIELD`): a 13-digit EAN is
    # masked like an account number, so the model never sees it either.
    reads_barcodes: bool = False

    @property
    def prompt_ref(self) -> str:
        return f"{self.prompt_id}@{self.prompt_version}"


_INCOTERMS = frozenset(
    {"EXW", "FCA", "CPT", "CIP", "DAP", "DPU", "DDP", "FAS", "FOB", "CFR", "CIF"}
)

EXTRACTION_SPECS: Mapping[DocumentType, ExtractionSpec] = {
    spec.doc_type: spec
    for spec in (
        ExtractionSpec(
            doc_type=DocumentType.SAMPLE_EVALUATION,
            prompt_id="supply_chain.extract_sample_evaluation",
            prompt_version="1.1.0",
            reading=SampleEvaluationReading,
            kinds={
                "result": FieldKind.CHOICE,
                "evaluated_on": FieldKind.DATE,
                "sample_round": FieldKind.NUMBER,
            },
            options={"result": frozenset({"pass", "fail", "revise"})},
        ),
        ExtractionSpec(
            doc_type=DocumentType.SUPPLIER_CONFIRMATION_EMAIL,
            prompt_id="supply_chain.extract_supplier_confirmation_email",
            prompt_version="1.3.0",
            reading=SupplierConfirmationReading,
            kinds={
                "confirmed": FieldKind.CHOICE,
                "quantity": FieldKind.NUMBER,
                "unit_price": FieldKind.NUMBER,
                "delivery_date": FieldKind.DATE,
                "moq": FieldKind.NUMBER,
                "lead_time_days": FieldKind.NUMBER,
            },
            options={"confirmed": frozenset({"yes", "no", "partly"})},
        ),
        ExtractionSpec(
            doc_type=DocumentType.PRODUCT_PROFILE_BM04,
            prompt_id="supply_chain.extract_product_profile_bm04",
            prompt_version="1.1.0",
            reading=ProfileBm04Reading,
            kinds={
                "net_weight_g": FieldKind.NUMBER,
                "unit_price": FieldKind.NUMBER,
                "moq": FieldKind.NUMBER,
                "lead_time_days": FieldKind.NUMBER,
                "incoterm": FieldKind.CHOICE,
            },
            options={"incoterm": _INCOTERMS},
        ),
        ExtractionSpec(
            doc_type=DocumentType.SUPPLIER_QUOTATION,
            prompt_id="supply_chain.extract_supplier_quotation",
            prompt_version="1.1.0",
            reading=SupplierQuotationReading,
            kinds={
                "quotation_date": FieldKind.DATE,
                "valid_until": FieldKind.DATE,
                "moq": FieldKind.NUMBER,
                "lead_time_days": FieldKind.NUMBER,
                "incoterm": FieldKind.CHOICE,
                "quantity": FieldKind.NUMBER,
                "unit_price": FieldKind.NUMBER,
                "line_total": FieldKind.NUMBER,
                "total": FieldKind.NUMBER,
            },
            options={"incoterm": _INCOTERMS},
        ),
        ExtractionSpec(
            doc_type=DocumentType.PROFORMA_INVOICE,
            prompt_id="supply_chain.extract_proforma_invoice",
            prompt_version="1.0.0",
            reading=ProformaInvoiceReading,
            kinds={
                "invoice_date": FieldKind.DATE,
                "quantity": FieldKind.NUMBER,
                "unit_price": FieldKind.NUMBER,
                "line_total": FieldKind.NUMBER,
                "total": FieldKind.NUMBER,
                "deposit_percent": FieldKind.NUMBER,
                "deposit_amount": FieldKind.NUMBER,
            },
            reads_accounts=True,
        ),
        ExtractionSpec(
            doc_type=DocumentType.COMMERCIAL_INVOICE,
            prompt_id="supply_chain.extract_commercial_invoice",
            prompt_version="1.0.0",
            reading=CommercialInvoiceReading,
            kinds={
                "invoice_date": FieldKind.DATE,
                "quantity": FieldKind.NUMBER,
                "unit_price": FieldKind.NUMBER,
                "line_total": FieldKind.NUMBER,
                "total": FieldKind.NUMBER,
            },
            reads_accounts=True,
        ),
        ExtractionSpec(
            doc_type=DocumentType.BANK_TRANSFER_RECEIPT,
            prompt_id="supply_chain.extract_bank_transfer_receipt",
            prompt_version="1.0.0",
            reading=BankTransferReading,
            kinds={"transfer_date": FieldKind.DATE, "amount": FieldKind.NUMBER},
            reads_accounts=True,
        ),
        ExtractionSpec(
            doc_type=DocumentType.PACKAGING_DESIGN,
            prompt_id="supply_chain.extract_packaging_design",
            prompt_version="1.0.0",
            reading=PackagingDesignReading,
            kinds={},
            reads_barcodes=True,
        ),
    )
}


# ------------------------------------------------------------ parsing -------

_NUMBER_TOKEN = re.compile(r"-?\d[\d.,\s]*\d|-?\d")
_THOUSANDS_DOT = re.compile(r"^-?\d{1,3}(\.\d{3})+(,\d+)?$")
_THOUSANDS_COMMA = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")
_THOUSANDS_SPACE = re.compile(r"^-?\d{1,3}( \d{3})+([.,]\d+)?$")


def parse_number(raw: str) -> Decimal | None:
    """A number as a document writes it, in either convention, or None.

    `1.234.567`, `1,234,567`, `1 234 567` and `1.234,5` / `1,234.5` are read;
    a lone separator before exactly three digits (`1.234`, `1,234`) is
    ambiguous between the two conventions and is refused, not guessed."""
    text = raw.strip().replace(chr(0xA0), " ")
    if not text:
        return None
    if _THOUSANDS_SPACE.fullmatch(text):
        text = text.replace(" ", "").replace(",", ".")
    elif _THOUSANDS_DOT.fullmatch(text) and "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif _THOUSANDS_COMMA.fullmatch(text) and "." in text:
        text = text.replace(",", "")
    elif re.fullmatch(r"-?\d{1,3}[.,]\d{3}", text):
        return None
    elif _THOUSANDS_DOT.fullmatch(text):
        text = text.replace(".", "")
    elif _THOUSANDS_COMMA.fullmatch(text):
        text = text.replace(",", "")
    elif re.fullmatch(r"-?\d+([.,]\d+)?", text):
        text = text.replace(",", ".")
    else:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


_AMBIGUOUS = re.compile(r"^-?\d{1,3}[.,]\d{3}$")


def numbers_in(text: str) -> list[Decimal]:
    """Every number a text writes. A token `parse_number` calls ambiguous
    (`1.000`) counts as both of its readings here: this only answers "does the
    quote write this number", and a value the model wrote unambiguously is
    still parsed strictly."""
    found = []
    for token in _NUMBER_TOKEN.findall(text):
        for candidate in (token, *token.split()):
            bare = candidate.strip(" .,")
            value = parse_number(bare)
            if value is not None:
                found.append(value)
            elif _AMBIGUOUS.fullmatch(bare):
                found.append(Decimal(bare.replace(",", "").replace(".", "")))
                found.append(Decimal(bare.replace(",", ".")))
    return found


_DATE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_DAY_FIRST = re.compile(r"^(\d{1,2})([/.-])(\d{1,2})\2(\d{4})$")
# One number and words around it, as a document writes a quantity ("1200 cái").
_NUMBER_WITH_WORDS = re.compile(r"^[^\d-]*(-?\d[\d.,\s]*\d|-?\d)[^\d]*$")


def parse_date(raw: str) -> date | None:
    """A date the model wrote: ISO, or the document's own day-first spelling
    (`20/11/2026`, `20-11-2026`, `20.11.2026`) when it cannot be read the other
    way round. `05/12/2026` could be either convention and is refused, not
    guessed, as `parse_number` refuses `1.234`."""
    text = raw.strip()
    try:
        if _DATE_ISO.fullmatch(text):
            return date.fromisoformat(text)
        match = _DATE_DAY_FIRST.fullmatch(text)
        if match is None:
            return None
        day, month, year = int(match.group(1)), int(match.group(3)), int(match.group(4))
        if day <= 12 and day != month:
            return None
        return date(year, month, day)
    except ValueError:
        return None


def parse_quantity(raw: str) -> Decimal | None:
    """`parse_number`, or the one number of a value that adds only words to it
    (a unit, a currency): `1200 cái` is 1200; two numbers are not one."""
    number = parse_number(raw)
    if number is not None:
        return number
    match = _NUMBER_WITH_WORDS.fullmatch(raw.strip().replace(chr(0xA0), " "))
    return None if match is None else parse_number(match.group(1).strip())


def _date_spellings(day: date) -> list[str]:
    d, m, y = day.day, day.month, day.year
    return [
        day.isoformat(),
        f"{d:02d}/{m:02d}/{y}",
        f"{d}/{m}/{y}",
        f"{d:02d}-{m:02d}-{y}",
        f"{d}-{m}-{y}",
        f"{d:02d}.{m:02d}.{y}",
        f"ngày {d} tháng {m} năm {y}",
        f"ngày {d:02d} tháng {m:02d} năm {y}",
    ]


def normalize(text: str) -> str:
    """For comparing a quote with the document: NFC, entities the prompt
    escaped turned back, whitespace runs to one space, case folded."""
    unescaped = html.unescape(unicodedata.normalize("NFC", text))
    return re.sub(r"\s+", " ", unescaped).strip().casefold()


# ------------------------------------------------------------ grounding -----


@dataclass(frozen=True, slots=True)
class Gap:
    field: str
    reason: GapReason

    def as_json(self) -> dict[str, str]:
        return {"field": self.field, "reason": self.reason.value}


@dataclass(frozen=True, slots=True)
class Grounded:
    """What a reading is worth against its text: kept fields (each its typed
    value and its quote) and named gaps."""

    fields: dict[str, Any]
    gaps: tuple[Gap, ...]


def _ground_one(
    name: str, path: str, cited: Cited, spec: ExtractionSpec, text: str
) -> tuple[dict[str, Any] | None, Gap | None]:
    if cited.value is None or not cited.value.strip():
        return None, Gap(path, GapReason.MISSING)
    if cited.quote is None or not cited.quote.strip():
        return None, Gap(path, GapReason.QUOTE_NOT_FOUND)
    quote = normalize(cited.quote)
    if quote not in text:
        return None, Gap(path, GapReason.QUOTE_NOT_FOUND)
    kind = spec.kinds.get(name, FieldKind.TEXT)
    value: Any
    match kind:
        case FieldKind.NUMBER:
            number = parse_quantity(html.unescape(cited.value))
            if number is None:
                return None, Gap(path, GapReason.NOT_A_NUMBER)
            if number not in numbers_in(html.unescape(cited.quote)):
                return None, Gap(path, GapReason.VALUE_NOT_IN_QUOTE)
            value = str(number)
        case FieldKind.DATE:
            day = parse_date(cited.value)
            if day is None:
                return None, Gap(path, GapReason.NOT_A_DATE)
            if not any(normalize(s) in quote for s in _date_spellings(day)):
                return None, Gap(path, GapReason.VALUE_NOT_IN_QUOTE)
            value = day.isoformat()
        case FieldKind.CHOICE:
            choice = cited.value.strip()
            if choice not in spec.options.get(name, frozenset()):
                return None, Gap(path, GapReason.NOT_AN_OPTION)
            value = choice
        case FieldKind.TEXT:
            if normalize(cited.value) not in quote:
                return None, Gap(path, GapReason.VALUE_NOT_IN_QUOTE)
            # The prompt shows `&` as `&amp;`; what is kept is what the file says.
            value = html.unescape(cited.value.strip())
    return {"value": value, "quote": html.unescape(cited.quote.strip())}, None


def _ground_model(
    reading: BaseModel, spec: ExtractionSpec, text: str, prefix: str = ""
) -> tuple[dict[str, Any], list[Gap]]:
    kept: dict[str, Any] = {}
    gaps: list[Gap] = []
    for name in type(reading).model_fields:
        value = getattr(reading, name)
        path = f"{prefix}{name}"
        if isinstance(value, Cited):
            grounded, gap = _ground_one(name, path, value, spec, text)
            if grounded is not None:
                kept[name] = grounded
            if gap is not None:
                gaps.append(gap)
        elif isinstance(value, list):
            items: list[Any] = []
            for index, item in enumerate(value):
                item_path = f"{path}[{index}]"
                if isinstance(item, Cited):
                    grounded, gap = _ground_one(name, item_path, item, spec, text)
                    if grounded is not None:
                        items.append(grounded)
                    if gap is not None:
                        gaps.append(gap)
                elif isinstance(item, BaseModel):
                    inner, inner_gaps = _ground_model(item, spec, text, f"{item_path}.")
                    items.append(inner)
                    gaps.extend(inner_gaps)
            kept[name] = items
    return kept, gaps


def _number(entry: Mapping[str, Any] | None) -> Decimal | None:
    return None if entry is None else Decimal(entry["value"])


def _check_arithmetic(kept: dict[str, Any], gaps: list[Gap]) -> None:
    """Quotation arithmetic is code's: a line total must be quantity times
    unit price and the total the sum of the line totals, to the cent."""
    lines = kept.get("lines")
    if not isinstance(lines, list):
        return
    cent = Decimal("0.01")
    line_totals: list[Decimal | None] = []
    for index, line in enumerate(lines):
        quantity, price, total = (
            _number(line.get("quantity")),
            _number(line.get("unit_price")),
            _number(line.get("line_total")),
        )
        if (
            quantity is not None
            and price is not None
            and total is not None
            and abs(quantity * price - total) >= cent
        ):
            del line["line_total"]
            gaps.append(Gap(f"lines[{index}].line_total", GapReason.NUMBER_MISMATCH))
            total = None
        line_totals.append(total)
    stated = _number(kept.get("total"))
    if stated is None or not line_totals or any(t is None for t in line_totals):
        return
    if abs(sum(t for t in line_totals if t is not None) - stated) >= cent:
        del kept["total"]
        gaps.append(Gap("total", GapReason.NUMBER_MISMATCH))


def ground(reading: BaseModel, spec: ExtractionSpec, text: str) -> Grounded:
    """Keep what the text proves; name the rest."""
    kept, gaps = _ground_model(reading, spec, normalize(text))
    _check_arithmetic(kept, gaps)
    return Grounded(fields=kept, gaps=tuple(gaps))


# ------------------------------------------------------------ redaction -----

_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?\b")
_AFTER_KEYWORD = re.compile(
    r"(?i)(\b(?:stk|s[ốo]\s*t(?:ài|ai)\s*kho[ảa]n|account(?:\s*(?:no\.?|number|#))?|a/c|acct\.?)"
    r"\s*[:#.]?\s*)([0-9][0-9 .\-]{4,40}[0-9])"
)
_DIGIT_RUN = re.compile(r"(?<![\d.,])\d{9,19}(?![\d.,])")
_DIGIT_GROUPS = re.compile(r"(?<![\d.,])\d{2,6}(?:[ \-]\d{2,6}){2,7}(?![\d.,])")


@dataclass(frozen=True, slots=True)
class Redacted:
    text: str
    count: int


def redact_identifiers(text: str) -> Redacted:
    """`text` with account numbers and similar identifiers masked."""
    count = 0

    def mask(_: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return REDACTED_IDENTIFIER

    def mask_after_keyword(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match.group(1)}{REDACTED_IDENTIFIER}"

    out = _AFTER_KEYWORD.sub(mask_after_keyword, text)
    out = _IBAN.sub(mask, out)

    def mask_groups(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        return mask(match) if 9 <= len(digits) <= 24 else match.group(0)

    out = _DIGIT_GROUPS.sub(mask_groups, out)
    out = _DIGIT_RUN.sub(mask, out)
    return Redacted(text=out, count=count)


# The key under which code keeps the beneficiary accounts a document names
# (ticket ai-automation/15). Not a field of any reading model: the model never
# writes it, and grounding never keeps a key it did not ground.
ACCOUNTS_FIELD = "beneficiary_accounts"


def account_numbers_in(text: str) -> list[str]:
    """The account numbers a document names AS accounts, normalised: a
    number written after "STK", "số tài khoản", "account", "A/C" and the
    like, and an IBAN. A long bare run of digits is masked before the model
    (`redact_identifiers`) but is not taken for an account here: an amount
    written without separators would otherwise read as a changed account.
    Read by code from the text before redaction; never shown to a model."""
    found: list[str] = []
    for match in _AFTER_KEYWORD.finditer(text):
        found.append(normalize_account_number(match.group(2)))
    for match in _IBAN.finditer(text):
        found.append(normalize_account_number(match.group(0)))
    seen: set[str] = set()
    out: list[str] = []
    for number in found:
        if 6 <= len(number) <= 34 and number not in seen:
            seen.add(number)
            out.append(number)
    return out


def account_marks(text: str) -> list[dict[str, str]]:
    """What a reading keeps of each account number `text` names: its digest
    (`account_digest`, what code compares with the supplier's master) and
    nothing a person or a model could read the number back from."""
    return [{"digest": account_digest(number)} for number in account_numbers_in(text)]


# The key under which code keeps the barcodes a proof prints (ticket
# ai-automation/16); like `ACCOUNTS_FIELD`, never a model's.
BARCODES_FIELD = "barcodes"
_BARCODE = re.compile(r"(?<![\d.,])(\d{13}|\d{12}|\d{8})(?![\d.,])")


def barcodes_in(text: str) -> list[str]:
    """Every run of 8, 12 or 13 digits standing alone: what an EAN-8, UPC-A or
    EAN-13 prints under its bars. Whether each is valid is the proof check's."""
    seen: list[str] = []
    for match in _BARCODE.finditer(text):
        if match.group(1) not in seen:
            seen.append(match.group(1))
    return seen


def is_unreadable(text: str) -> bool:
    """Nothing to read: an empty text, or the transcription's marker."""
    return not text.strip() or UNREADABLE_MARKER in text


def gaps_json(gaps: Iterable[Gap]) -> list[dict[str, str]]:
    return [gap.as_json() for gap in gaps]
