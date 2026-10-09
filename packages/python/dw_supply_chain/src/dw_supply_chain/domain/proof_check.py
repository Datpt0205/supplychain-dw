"""A packaging design proof checked by code (ticket ai-automation/16; ADR 0028).

The model reads the proof into cited fields (`PackagingDesignReading`); this
module decides what they are worth against the BM04 and the label rules:

- **Mandatory label content** (`LABEL_REQUIRED`): what Nghị định 43/2017
  (amended by 111/2021) requires on a label of household goods, as the skill
  `supply_chain.label_rules` explains it to the model and to a person. A field
  the proof does not carry, with a quote found, is a named gap: never "present"
  because the model said so. The skill is the words; this list is the check; a
  test holds each item here to a line of the skill, so the two cannot drift
  quietly.
- **Against the BM04** (the product's latest saved version): the name, the
  material, the origin compared as texts (the proof's must contain the BM04's
  or the other way round, after normalisation), the dimensions as the numbers
  each writes. A BM04 field left empty is not compared, and no BM04 at all is a
  finding of its own, never a pass.
- **Codes:** each SKU the proof prints must be one of the PO case's lines;
  each line's SKU missing from the proof is named. A barcode (read by code
  from the text, never by the model: a 13-digit run is masked before it) is
  checked by its own check digit (EAN-13, UPC-A, EAN-8): arithmetic, not a
  guess.

Findings carry no price: the PO case page shows them to anyone who reads it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from dw_supply_chain.domain.extraction import BARCODES_FIELD, normalize, numbers_in
from dw_supply_chain.domain.payment_check import SourceRead
from dw_supply_chain.domain.step_proposal import Finding

# (reading field, the words a finding and the skill use for it).
LABEL_REQUIRED: tuple[tuple[str, str], ...] = (
    ("product_name", "tên hàng hóa"),
    ("responsible_party", "tên và địa chỉ của tổ chức, cá nhân chịu trách nhiệm"),
    ("origin", "xuất xứ"),
    ("material", "chất liệu"),
    ("dimensions", "thông số kỹ thuật"),
    ("usage_instructions", "hướng dẫn sử dụng"),
    ("warnings", "thông tin cảnh báo"),
)

# The BM04 attribute each compared proof field is held to.
_BM04_TEXT = {"product_name": "product_name", "material": "material", "origin": "origin_country"}
_WORDS = dict(LABEL_REQUIRED)


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _same_text(a: str, b: str) -> bool:
    left, right = normalize(a), normalize(b)
    return left in right or right in left


def _sku(raw: Any) -> str | None:
    text = _text(raw)
    return None if text is None else "".join(text.split()).upper()


def barcode_valid(raw: str) -> bool:
    """An EAN-13, UPC-A or EAN-8 by its check digit."""
    digits = re.sub(r"[\s-]", "", raw)
    if not digits.isdigit() or len(digits) not in (8, 12, 13):
        return False
    body, check = digits[:-1], int(digits[-1])
    weights = (3, 1) if len(body) % 2 == 1 else (1, 3)
    total = sum(int(d) * weights[i % 2] for i, d in enumerate(body))
    return (10 - total % 10) % 10 == check


def label_findings(proof: SourceRead) -> list[Finding]:
    """Each mandatory label content the proof does not carry."""
    if not proof.extracted:
        return []
    return [
        Finding(
            "label_missing",
            name,
            f"Bản in thiếu {words} (nội dung bắt buộc của nhãn, NĐ 43/2017, 111/2021)",
        )
        for name, words in LABEL_REQUIRED
        if _text(proof.value(name)) is None
    ]


def bm04_findings(proof: SourceRead, attributes: Mapping[str, Any] | None) -> list[Finding]:
    """The proof against the product's latest BM04 version."""
    if not proof.extracted:
        return []
    if attributes is None:
        return [
            Finding(
                "bm04_missing",
                "product_profile_bm04",
                "Sản phẩm chưa có BM04 trong ứng dụng: không so được bản in, người kiểm",
            )
        ]
    found: list[Finding] = []
    for name, key in _BM04_TEXT.items():
        printed, recorded = _text(proof.value(name)), _text(attributes.get(key))
        if printed is not None and recorded is not None and not _same_text(printed, recorded):
            found.append(
                Finding("proof_differs", name, f"{_WORDS[name].capitalize()} trên bản in khác BM04")
            )
    printed, recorded = _text(proof.value("dimensions")), _text(attributes.get("dimensions"))
    if (
        printed is not None
        and recorded is not None
        and sorted(numbers_in(printed)) != sorted(numbers_in(recorded))
    ):
        found.append(Finding("proof_differs", "dimensions", "Kích thước trên bản in khác BM04"))
    return found


def code_findings(proof: SourceRead, case_skus: Iterable[str]) -> list[Finding]:
    """The SKUs the proof prints against the PO case's lines, and its barcode."""
    if not proof.extracted:
        return []
    ordered = {s for s in (_sku(c) for c in case_skus) if s is not None}
    rows = proof.fields.get("sku_codes")
    printed = {
        s
        for s in (
            _sku(r.get("value"))
            for r in (rows if isinstance(rows, list) else [])
            if isinstance(r, Mapping)
        )
        if s is not None
    }
    found = [
        Finding("sku_unknown", sku, f"SKU {sku} in trên bản in không có trong Hồ sơ PO")
        for sku in sorted(printed - ordered)
    ]
    if printed:
        found += [
            Finding("sku_missing", sku, f"SKU {sku} của Hồ sơ PO không có trên bản in")
            for sku in sorted(ordered - printed)
        ]
    barcodes = proof.fields.get(BARCODES_FIELD)
    for barcode in barcodes if isinstance(barcodes, list) else []:
        if isinstance(barcode, str) and not barcode_valid(barcode):
            found.append(
                Finding("barcode_invalid", "barcode", "Mã vạch trên bản in sai số kiểm tra")
            )
            break
    return found


def proof_findings(
    proof: SourceRead, attributes: Mapping[str, Any] | None, case_skus: Iterable[str]
) -> list[Finding]:
    return (
        label_findings(proof) + bm04_findings(proof, attributes) + code_findings(proof, case_skus)
    )


__all__ = [
    "LABEL_REQUIRED",
    "barcode_valid",
    "bm04_findings",
    "code_findings",
    "label_findings",
    "proof_findings",
]
