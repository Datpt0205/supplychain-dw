"""Unit: the case-document domain — key, content check, filename, doc types."""

from __future__ import annotations

import uuid

import pytest

from dw_kernel.errors import DomainError, UnsupportedMediaTypeError
from dw_supply_chain.domain.case_document import (
    ALLOWED_CONTENT_TYPES,
    CaseKind,
    DocumentType,
    ObjectKey,
    accepted_content_type,
    clean_filename,
)

pytestmark = pytest.mark.unit

TENANT, WORKSPACE, CASE, DOC = (uuid.uuid4() for _ in range(4))

PDF = b"%PDF-1.7\n..."
PNG = b"\x89PNG\r\n\x1a\n...."
JPEG = b"\xff\xd8\xff\xe0...."
ZIP = b"PK\x03\x04...."
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1...."
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def test_adr_0021_names_fourteen_document_types_step_12_three_and_ai_02_one() -> None:
    assert {t.value for t in DocumentType} == {
        "proposal_list",
        "product_image",
        "sample_photo",
        "sample_evaluation",
        "sample_revision_request",
        "product_profile_bm04",
        "official_item_code",
        "supplier_confirmation_email",
        "purchase_order",
        "deposit_docs",
        "payment_docs",
        "packaging_content",
        "user_manual",
        "maquette",
        # Slice PK, step 12's sub-flow.
        "colour_sample",
        "packaging_design",
        "pre_production_test_report",
        "supplier_quotation",
        "bod_submission",
        # Steps 11 and 16's papers (ai-automation/15).
        "proforma_invoice",
        "commercial_invoice",
        "bank_transfer_receipt",
        # Step 12's revision requests (ai-automation/16).
        "colour_revision_request",
        "design_revision_request",
        # Steps 13-15's papers (ai-automation/17).
        "production_schedule",
        "qc_report",
        "packing_list",
        "bill_of_lading",
        "arrival_notice",
        "certificate_of_origin",
        "rework_request",
    }


def test_the_key_is_built_from_the_verified_ids_only() -> None:
    key = ObjectKey.build(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        case_kind=CaseKind.PO,
        case_id=CASE,
        document_id=DOC,
    )
    assert key.value == f"supply_chain/{TENANT}/{WORKSPACE}/po/{CASE}/{DOC}"
    assert key.value.startswith(f"supply_chain/{TENANT}/{WORKSPACE}/")


def test_a_key_parses_back_into_its_ids() -> None:
    key = ObjectKey.build(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        case_kind=CaseKind.PO,
        case_id=CASE,
        document_id=DOC,
    )
    assert ObjectKey.parse(key.value) == key


def test_a_product_case_key_parses_back_too() -> None:
    """Stage 1: what the orphan sweep reads to find a product document's
    tenant and workspace."""
    key = ObjectKey.build(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        case_kind=CaseKind.PRODUCT,
        case_id=CASE,
        document_id=DOC,
    )
    assert key.value == f"supply_chain/{TENANT}/{WORKSPACE}/product/{CASE}/{DOC}"
    assert ObjectKey.parse(key.value) == key


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "supply_chain/",
        f"supply_chain/{TENANT}/{WORKSPACE}/po/{CASE}",
        f"supply_chain/{TENANT}/{WORKSPACE}/po/{CASE}/{DOC}/extra",
        f"supply_chain/not-a-uuid/{WORKSPACE}/po/{CASE}/{DOC}",
        f"supply_chain/{TENANT}/{WORKSPACE}/sample/{CASE}/{DOC}",
        f"feedback/{TENANT}/{WORKSPACE}/po/{CASE}/{DOC}",
        f"supply_chain/{str(TENANT).upper()}/{WORKSPACE}/po/{CASE}/{DOC}",
        f"supply_chain/{TENANT.hex}/{WORKSPACE}/po/{CASE}/{DOC}",
        f"supply_chain/{TENANT}/../po/{CASE}/{DOC}",
    ],
)
def test_a_key_not_in_the_exact_shape_does_not_parse(raw: str) -> None:
    assert ObjectKey.parse(raw) is None


@pytest.mark.parametrize(
    ("declared", "head", "expected"),
    [
        ("application/pdf", PDF, "application/pdf"),
        ("Application/PDF; charset=binary", PDF, "application/pdf"),
        ("image/png", PNG, "image/png"),
        ("image/jpeg", JPEG, "image/jpeg"),
        (XLSX, ZIP, XLSX),
        (DOCX, ZIP, DOCX),
        ("application/vnd.ms-outlook", OLE, "application/vnd.ms-outlook"),
        # EML has no magic number: the declared type is all there is.
        ("message/rfc822", b"From: a@b.c\r\nSubject: x\r\n", "message/rfc822"),
    ],
)
def test_an_allowed_type_whose_bytes_agree_is_accepted(
    declared: str, head: bytes, expected: str
) -> None:
    assert accepted_content_type(declared, head) == expected


@pytest.mark.parametrize(
    ("declared", "head"),
    [
        ("text/html", b"<html>"),
        ("application/octet-stream", PDF),
        ("image/svg+xml", b"<svg/>"),
        ("", PDF),
        # Declared as an allowed type, but the bytes are something else.
        ("application/pdf", b"<html><script>"),
        ("image/png", JPEG),
        (XLSX, PDF),
        ("application/vnd.ms-outlook", ZIP),
    ],
)
def test_a_type_outside_the_list_or_against_its_bytes_is_415(declared: str, head: bytes) -> None:
    with pytest.raises(UnsupportedMediaTypeError):
        accepted_content_type(declared, head)


def test_the_allow_list_is_the_seven_the_ticket_names() -> None:
    assert (
        frozenset(
            {
                "application/pdf",
                "image/jpeg",
                "image/png",
                XLSX,
                DOCX,
                "message/rfc822",
                "application/vnd.ms-outlook",
            }
        )
        == ALLOWED_CONTENT_TYPES
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PO-123.pdf", "PO-123.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\a\\Biên bản.pdf", "Biên bản.pdf"),
        ("bad\r\nname\x00.pdf", "badname.pdf"),
        ("  spaced.pdf  ", "spaced.pdf"),
        ("x" * 300 + ".pdf", ("x" * 300 + ".pdf")[-255:]),
    ],
)
def test_a_filename_is_kept_as_a_label_with_no_path_or_control_characters(
    raw: str, expected: str
) -> None:
    assert clean_filename(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "../", "\x00\x01"])
def test_a_filename_with_nothing_left_is_refused(raw: str) -> None:
    with pytest.raises(DomainError):
        clean_filename(raw)
