"""Unit: step 8, the supplier's confirmation against the BM04 (ticket
ai-automation/12).

The domain comparison; the real `PrepareStep`, `StepProposalSubject` and
`ApplyStepProposal` over `testing.step_preparation` with Elmich's own step 8
(`TERMS_STEP`); and the real message lane drafting the confirmation from the
BM04 over `testing.supplier_messages`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from dw_agent_runtime.model.prompts import load_shipped_prompts
from dw_supply_chain.application.step_preparation import Prepared
from dw_supply_chain.domain.case_document import (
    CaseDocument,
    CaseDocumentId,
    CaseKind,
    DocumentType,
)
from dw_supply_chain.domain.commercial import Incoterm, ProductProfile, ProfileCommercial
from dw_supply_chain.domain.grounded_writing import CitedSentence
from dw_supply_chain.domain.product_development_case import (
    ProductDevelopmentCase,
    ProductDevState,
)
from dw_supply_chain.domain.supplier_message import MessagePurpose, SupplierMessageWriting
from dw_supply_chain.domain.supplier_terms import (
    TermStatus,
    bm04_terms,
    compare_terms,
    prices_of,
    term_message,
)
from dw_supply_chain.testing.extraction import ScriptedGateway
from dw_supply_chain.testing.step_preparation import NOW, TERMS_STEP, StepWorld
from dw_supply_chain.testing.supplier_messages import MessageWorld

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[5]
PROMPTS = load_shipped_prompts(REPO_ROOT / "configs")


def _profile(case_id: uuid.UUID, **attributes: Any) -> ProductProfile:
    return ProductProfile(
        id=uuid.uuid4(),
        product_dev_case_id=case_id,
        version=1,
        commercial=ProfileCommercial.of(
            unit_price="2.50",
            currency="USD",
            moq=500,
            lead_time_days=45,
            incoterm=Incoterm.FOB,
        ),
        attributes={
            "product_name": "Nồi inox 3 đáy 24cm",
            "material": "Inox 304",
            "dimensions": "24 x 12 cm",
            "packaging": "Hộp màu, 6 cái/thùng",
            **attributes,
        },
        schema_version="1.0.0",
        created_by=uuid.uuid4(),
        created_at=datetime(2026, 10, 8, tzinfo=UTC),
    )


def _c(value: str, quote: str | None = None) -> dict[str, str]:
    return {"value": value, "quote": quote or value}


REPLY_AGREES_TO_ALL = {
    "confirmed": _c("yes", "Chúng tôi đồng ý mọi điều khoản."),
    "unit_price": _c("2.70", "Đơn giá: 2,70 USD"),
    "currency": _c("USD", "Đơn giá: 2,70 USD"),
    "moq": _c("500", "MOQ: 500"),
    "specification": _c(
        "Inox 304, 24 x 12 cm, đáy 3 lớp", "Quy cách: Inox 304, 24 x 12 cm, đáy 3 lớp"
    ),
}


# ------------------------------------------------------------------ domain --


def test_each_term_is_matched_differs_unstated_or_missing_from_the_bm04() -> None:
    profile = _profile(uuid.uuid4(), packaging=None)
    rows = {
        r.field: r
        for r in compare_terms(
            {**REPLY_AGREES_TO_ALL, "packaging": _c("Túi nilon")}, bm04_terms(profile)
        )
    }
    assert rows["unit_price"].status is TermStatus.DIFFER
    assert rows["currency"].status is TermStatus.MATCH
    assert rows["moq"].status is TermStatus.MATCH
    assert rows["specification"].status is TermStatus.MATCH
    assert rows["lead_time_days"].status is TermStatus.NOT_STATED
    assert rows["packaging"].status is TermStatus.NOT_IN_BM04


def test_a_specification_missing_a_bm04_part_differs() -> None:
    rows = {
        r.field: r
        for r in compare_terms(
            {"specification": _c("Inox 201, 24 x 12 cm")}, bm04_terms(_profile(uuid.uuid4()))
        )
    }
    assert rows["specification"].status is TermStatus.DIFFER


def test_a_price_row_and_its_words_carry_no_number_and_other_words_mask_it() -> None:
    rows = compare_terms(
        {**REPLY_AGREES_TO_ALL, "moq": _c("400", "MOQ 400 cái, giá 2,70 USD")},
        bm04_terms(_profile(uuid.uuid4())),
    )
    price = next(r for r in rows if r.field == "unit_price")
    prices = prices_of(rows)
    assert price.as_json(prices)["bm04"] is None and price.as_json(prices)["reply"] is None
    assert set(prices) == {Decimal("2.50"), Decimal("2.70")}
    words = " ".join(term_message(r, prices) for r in rows if r.status is not TermStatus.MATCH)
    for number in ("2,70", "2.70", "2.5", "2,50"):
        assert number not in words
    assert "MOQ 400 cái" in words and "[giá]" in words


# ------------------------------------------------------------- preparing --


def _seed(
    world: StepWorld, reply: dict[str, Any], *, profile_in: str = "own"
) -> tuple[ProductDevelopmentCase, Any, ProductProfile]:
    case, entered = world.add_case(ProductDevState.SUPPLIER_CONFIRMATION)
    email = world.add_document(case, DocumentType.SUPPLIER_CONFIRMATION_EMAIL, text="thư NCC")
    world.add_reading(email, reply)
    profile = _profile(case.id.value)
    world.profiles.rows.append(profile)
    workspace = case.workspace_id.value if profile_in == "own" else uuid.uuid4()
    world.profiles.scopes[profile.id] = (case.tenant_id.value, workspace)
    return case, entered, profile


def _prepare(world: StepWorld, case: ProductDevelopmentCase, entered: Any) -> Prepared:
    lane = world.context(world.lane_context(case).principal_id)
    prepared: Prepared = asyncio.run(
        world.preparer().prepare(lane, world.request(case, entered, TERMS_STEP))
    )
    return prepared


def test_a_reply_agreeing_to_all_terms_with_another_price_still_differs_on_price() -> None:
    world = StepWorld()
    case, entered, profile = _seed(world, REPLY_AGREES_TO_ALL)
    prepared = _prepare(world, case, entered)
    assert prepared.outcome.value == "proposed"
    findings = {f["subject"]: f for f in prepared.payload["findings"]}
    assert findings["unit_price"]["code"] == "terms_differ"
    assert findings["lead_time_days"]["code"] == "term_not_stated"
    comparison = prepared.payload["comparison"]
    assert comparison["profile_id"] == str(profile.id)
    by_field = {r["field"]: r for r in comparison["rows"]}
    assert by_field["unit_price"]["status"] == "differ"
    assert by_field["moq"]["status"] == "match"
    text = json.dumps(prepared.payload, ensure_ascii=False)
    for number in ("2,70", "2.70", "2.50", "2,50"):
        assert number not in text


def test_a_bm04_of_another_workspace_is_no_bm04() -> None:
    world = StepWorld()
    case, entered, _ = _seed(world, REPLY_AGREES_TO_ALL, profile_in="other_workspace")
    prepared = _prepare(world, case, entered)
    assert [f["code"] for f in prepared.payload["findings"]] == ["bm04_missing"]
    assert prepared.payload["comparison"] == {"profile_id": None, "rows": []}


def test_a_bm04_saved_after_the_proposal_supersedes_the_decision() -> None:
    world = StepWorld()
    case, entered, _ = _seed(world, REPLY_AGREES_TO_ALL)
    prepared = _prepare(world, case, entered)
    newer = _profile(case.id.value)
    newer = replace(newer, version=2)
    world.profiles.rows.append(newer)
    world.profiles.scopes[newer.id] = (case.tenant_id.value, case.workspace_id.value)
    outcome = asyncio.run(
        world.applier().apply(
            world.context(),
            prepared.payload,
            approved=True,
            comment="ok",
            typed_input={},
            run_id=None,
        )
    )
    assert outcome == "superseded"
    assert world.cases.cases[case.id.value].state is ProductDevState.SUPPLIER_CONFIRMATION


# ---------------------------------------------------------------- message --


def test_the_confirmation_is_drafted_from_the_bm04_without_its_price_and_attaches_it() -> None:
    world = MessageWorld(
        gateway=ScriptedGateway(
            PROMPTS,
            answer=SupplierMessageWriting(
                paragraphs=[CitedSentence(text="Xác nhận MOQ 500.", cites=["case"])]
            ),
        )
    )
    world.enable(MessagePurpose.SUPPLIER_CONFIRMATION)
    case = world.add_case(ProductDevState.SUPPLIER_CONFIRMATION)
    profile = _profile(case.id.value)
    world.profiles.rows.append(profile)
    world.profiles.scopes[profile.id] = (world.tenant_id, world.workspace_id)
    data = b"bm04"
    paper = CaseDocument(
        id=CaseDocumentId(uuid.uuid4()),
        tenant_id=world.tenant_id,
        workspace_id=world.workspace_id,
        case_kind=CaseKind.PRODUCT,
        case_id=case.id.value,
        doc_type=DocumentType.PRODUCT_PROFILE_BM04,
        object_key="k/bm04",
        filename="BM04.docx",
        content_type="application/pdf",
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        version=1,
        uploaded_by=uuid.uuid4(),
        uploaded_at=NOW,
    )
    world.documents.rows.append(paper)
    asyncio.run(world.lane().run())
    [message] = world.messages.rows
    assert message.purpose is MessagePurpose.SUPPLIER_CONFIRMATION
    assert message.attachments == (paper.id.value,)
    [sent] = world.gateway.sent
    assert f"doc:{paper.id}" in sent.user and "MOQ: 500" in sent.user
    assert "Inox 304" in sent.user
    for price in ("2.50", "2,50", "unit_price"):
        assert price not in sent.user


def test_a_price_difference_is_said_without_any_value_not_even_a_masked_one() -> None:
    rows = compare_terms(REPLY_AGREES_TO_ALL, bm04_terms(_profile(uuid.uuid4())))
    price = next(r for r in rows if r.field == "unit_price")
    words = term_message(price, prices_of(rows))
    assert words == "Đơn giá: thư NCC khác BM04; giá chỉ xem trên chứng từ"


def test_the_replys_moq_and_lead_time_are_read_as_numbers() -> None:
    from dw_supply_chain.domain.extraction import EXTRACTION_SPECS, ground

    spec = EXTRACTION_SPECS[DocumentType.SUPPLIER_CONFIRMATION_EMAIL]
    text = "MOQ: 1,000 pcs\nLead time: 45 days"
    reading = spec.reading.model_validate(
        {
            "moq": {"value": "1000", "quote": "MOQ: 1,000 pcs"},
            "lead_time_days": {"value": "45 days", "quote": "Lead time: 45 days"},
        }
    )
    kept = ground(reading, spec, text).fields
    assert (kept["moq"]["value"], kept["lead_time_days"]["value"]) == ("1000", "45")
