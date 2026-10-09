"""Case documents: a file attached to one case, with a type and a version (ADR 0021).

What this module owns:

- **The document types.** `DocumentType` is the one list; the database CHECK
  `ck_case_documents_doc_type` repeats it, and an integration test asserts the
  two are equal, so a value added to one side fails loudly instead of being
  refused at the first upload of that type.
- **The object key.** Built here from the verified ids and nothing else, and
  parsed back by the same class: the orphan sweep reads a key's tenant and
  workspace through `ObjectKey.parse`, so a key it cannot read exactly is a key
  it leaves alone. A filename never reaches the key.
- **What content is accepted.** The declared type must be on the list AND,
  where the format has a magic number, the first bytes must carry it. The
  bytes are untrusted input: an HTML page declared as a PDF is refused here,
  not discovered by whoever opens it.

The file's content is never read here beyond those first bytes. It reaches a
model only through the extraction lane, as untrusted data, redacted first (ADR
0021 amended 2026-10-09; `domain.extraction`).
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Self

from dw_kernel.errors import DomainError, UnsupportedMediaTypeError


class DocumentType(StrEnum):
    """ADR 0021's fourteen document types, from process.md section 2, the
    three papers of step 12's sub-flow (slice PK) and the supplier's quotation
    (ai-automation/02)."""

    PROPOSAL_LIST = "proposal_list"
    PRODUCT_IMAGE = "product_image"
    SAMPLE_PHOTO = "sample_photo"
    SAMPLE_EVALUATION = "sample_evaluation"
    SAMPLE_REVISION_REQUEST = "sample_revision_request"
    PRODUCT_PROFILE_BM04 = "product_profile_bm04"
    OFFICIAL_ITEM_CODE = "official_item_code"
    SUPPLIER_CONFIRMATION_EMAIL = "supplier_confirmation_email"
    PURCHASE_ORDER = "purchase_order"
    DEPOSIT_DOCS = "deposit_docs"
    PAYMENT_DOCS = "payment_docs"
    PACKAGING_CONTENT = "packaging_content"
    USER_MANUAL = "user_manual"
    MAQUETTE = "maquette"
    # Step 12 (slice PK): the supplier's colour sample, the packaging design
    # Cung ứng approves, and R&D's pre-production test report.
    COLOUR_SAMPLE = "colour_sample"
    PACKAGING_DESIGN = "packaging_design"
    PRE_PRODUCTION_TEST_REPORT = "pre_production_test_report"
    # A supplier's quotation or specification (ticket ai-automation/02): read
    # by the extraction lane into cited fields.
    SUPPLIER_QUOTATION = "supplier_quotation"


class CaseKind(StrEnum):
    """Which kind of case a document belongs to; the key's fourth segment, and
    which of `case_documents`' two case columns holds the case id."""

    PO = "po"
    # The product-development case (stage-1 ticket 01, ADR 0016).
    PRODUCT = "product"


@dataclass(frozen=True, slots=True)
class CaseDocumentId:
    value: uuid.UUID

    def __str__(self) -> str:
        return str(self.value)


_KEY_ROOT = "supply_chain"
# A uuid exactly as `str(uuid.UUID)` prints it, so a parsed key re-builds to the
# same string; any other spelling is not a key this module wrote.
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_KEY_PATTERN = re.compile(
    rf"^{_KEY_ROOT}/(?P<tenant>{_UUID})/(?P<workspace>{_UUID})/"
    rf"(?P<kind>{'|'.join(re.escape(k.value) for k in CaseKind)})/"
    rf"(?P<case>{_UUID})/(?P<document>{_UUID})$"
)


@dataclass(frozen=True, slots=True)
class ObjectKey:
    """`supply_chain/{tenant}/{workspace}/{case_kind}/{case_id}/{document_id}`."""

    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    case_kind: CaseKind
    case_id: uuid.UUID
    document_id: uuid.UUID

    # Every key in the bucket starts with this; the sweep lists under it.
    ROOT_PREFIX = f"{_KEY_ROOT}/"

    @classmethod
    def build(
        cls,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID,
        case_kind: CaseKind,
        case_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> Self:
        return cls(tenant_id, workspace_id, case_kind, case_id, document_id)

    @classmethod
    def parse(cls, raw: str) -> Self | None:
        """The ids of a key in exactly the shape `build` writes, else None."""
        match = _KEY_PATTERN.fullmatch(raw)
        if match is None:
            return None
        return cls(
            tenant_id=uuid.UUID(match["tenant"]),
            workspace_id=uuid.UUID(match["workspace"]),
            case_kind=CaseKind(match["kind"]),
            case_id=uuid.UUID(match["case"]),
            document_id=uuid.UUID(match["document"]),
        )

    @staticmethod
    def tenant_prefix(tenant_id: uuid.UUID) -> str:
        """Every key of one tenant: what offboarding exports and deletes."""
        return f"{_KEY_ROOT}/{tenant_id}/"

    @property
    def value(self) -> str:
        return (
            f"{_KEY_ROOT}/{self.tenant_id}/{self.workspace_id}/"
            f"{self.case_kind.value}/{self.case_id}/{self.document_id}"
        )


_PDF = "application/pdf"
_JPEG = "image/jpeg"
_PNG = "image/png"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_EML = "message/rfc822"
_MSG = "application/vnd.ms-outlook"

_ZIP = b"PK\x03\x04"
# Each allowed type and the bytes its file must start with. XLSX and DOCX are
# ZIP containers and share a signature; MSG is an OLE compound file. EML is
# plain text with no signature, so its declared type is all there is to check.
_SIGNATURES: dict[str, bytes | None] = {
    _PDF: b"%PDF-",
    _JPEG: b"\xff\xd8\xff",
    _PNG: b"\x89PNG\r\n\x1a\n",
    _XLSX: _ZIP,
    _DOCX: _ZIP,
    _EML: None,
    _MSG: b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
}
ALLOWED_CONTENT_TYPES = frozenset(_SIGNATURES)
# Enough bytes for the longest signature above.
SNIFF_BYTES = max(len(s) for s in _SIGNATURES.values() if s is not None)


def accepted_content_type(declared: str, head: bytes) -> str:
    """The normalised content type to store, or `UnsupportedMediaTypeError`.

    Parameters after `;` are dropped and the type is compared lower-case; the
    stored value is always one of `ALLOWED_CONTENT_TYPES`, which is what a
    download later sends back as `Content-Type`."""
    content_type = declared.split(";", 1)[0].strip().lower()
    if content_type not in _SIGNATURES:
        raise UnsupportedMediaTypeError(
            "chỉ nhận PDF, JPEG, PNG, XLSX, DOCX, EML hoặc MSG",
            details={"content_type": content_type},
        )
    signature = _SIGNATURES[content_type]
    if signature is not None and not head.startswith(signature):
        raise UnsupportedMediaTypeError(
            "nội dung file không khớp loại file đã khai",
            details={"content_type": content_type},
        )
    return content_type


_FILENAME_MAX = 255


def clean_filename(raw: str) -> str:
    """The name a person gave the file, kept only as a label.

    The last path segment, NFC, no control characters, at most 255 characters
    (the tail, so the extension survives). It never reaches the object key."""
    name = unicodedata.normalize("NFC", raw).replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip()
    if name in {"", ".", ".."}:
        raise DomainError("tên file trống", details={"filename": raw[:_FILENAME_MAX]})
    return name[-_FILENAME_MAX:]


@dataclass(frozen=True, slots=True)
class CaseDocument:
    """One stored version of one document type on one case."""

    id: CaseDocumentId
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    case_kind: CaseKind
    case_id: uuid.UUID
    doc_type: DocumentType
    object_key: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    version: int
    uploaded_by: uuid.UUID
    uploaded_at: datetime
