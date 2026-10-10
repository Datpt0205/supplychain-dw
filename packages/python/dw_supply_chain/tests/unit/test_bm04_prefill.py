"""Unit: step 7, the BM04 AI prefills (ticket ai-automation/11).

The real `PrepareStep` and `ApplyStepProposal` over the in-memory world of
`testing.step_preparation`, with Elmich's own step 7 (`BM04_STEP`, read from
its override file), the shipped template and prompt rendered through the real
registry, and a scripted model.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from dw_agent_runtime.model.prompts import load_shipped_prompts
from dw_supply_chain.application.handlers import COMMERCIAL_WRITE
from dw_supply_chain.application.step_preparation import Prepared
from dw_supply_chain.bm04_schema import SupplyChainBm04Schema
from dw_supply_chain.domain.bm04_prefill import (
    Bm04Candidate,
    Bm04FieldWriting,
    Bm04Source,
    Bm04Writing,
    conflict_message,
    reconcile,
)
from dw_supply_chain.domain.case_document import CaseDocument, DocumentType
from dw_supply_chain.domain.document_draft import DocumentDraft
from dw_supply_chain.domain.product_development_case import (
    ProductDevelopmentCase,
    ProductDevState,
)
from dw_supply_chain.testing.extraction import ScriptedGateway
from dw_supply_chain.testing.step_preparation import BM04_STEP, StepWorld

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[5]
PROMPTS = load_shipped_prompts(REPO_ROOT / "configs")

QUOTATION: dict[str, Any] = {
    "supplier_name": {"value": "Minh Phát", "quote": "Nhà cung cấp: Minh Phát"},
    "currency": {"value": "USD", "quote": "Tiền tệ: USD"},
    "moq": {"value": "500", "quote": "MOQ: 500"},
    "lead_time_days": {"value": "45", "quote": "Thời gian sản xuất (ngày): 45"},
    "incoterm": {"value": "FOB", "quote": "Incoterm: FOB Ningbo"},
    "lines": [
        {
            "description": {
                "value": "Nồi inox 3 đáy 24cm, chất liệu inox 304, màu bạc",
                "quote": "| Nồi inox 3 đáy 24cm, chất liệu inox 304, màu bạc | 1.000 | 2,50 USD |",
            },
            "unit_price": {
                "value": "2.50",
                "quote": "| Nồi inox 3 đáy 24cm, chất liệu inox 304, màu bạc | 1.000 | 2,50 USD |",
            },
        }
    ],
}
RECORD_ROWS = [
    {"criterion": "Độ dày đáy", "standard": "≥ 3 mm", "measured": "3.2", "result": "Đạt"},
]


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _world(writing: Bm04Writing | None = None, **kwargs: Any) -> StepWorld:
    return StepWorld(
        gateway=None if writing is None else ScriptedGateway(PROMPTS, answer=writing),
        **kwargs,
    )


def _case(world: StepWorld, **kwargs: Any) -> tuple[ProductDevelopmentCase, Any]:
    return world.add_case(ProductDevState.PROFILE_IN_PROGRESS, **kwargs)


def _quotation(
    world: StepWorld, case: ProductDevelopmentCase, fields: Mapping[str, Any] = QUOTATION
) -> CaseDocument:
    document = world.add_document(case, DocumentType.SUPPLIER_QUOTATION, text="báo giá")
    world.add_reading(document, fields)
    return document


def _prepare(world: StepWorld, case: ProductDevelopmentCase, entered: Any) -> Prepared:
    lane = world.context(world.lane_context(case).principal_id)
    prepared: Prepared = _run(
        world.preparer().prepare(lane, world.request(case, entered, BM04_STEP))
    )
    return prepared


def _bm04(world: StepWorld) -> DocumentDraft:
    return next(d for d in world.drafts.rows if d.doc_type is DocumentType.PRODUCT_PROFILE_BM04)


def _value(draft: DocumentDraft, name: str) -> Any:
    entry = draft.fields.get(name)
    return entry.get("value") if isinstance(entry, Mapping) else None


def _findings(prepared: Prepared) -> list[dict[str, str]]:
    return list(prepared.payload.get("findings", []))


def _writing(doc: CaseDocument | uuid.UUID, *fields: tuple[str, str, str]) -> Bm04Writing:
    key = f"doc:{doc.id if isinstance(doc, CaseDocument) else doc}"
    return Bm04Writing(
        fields=[Bm04FieldWriting(field=f, value=v, quote=q, cite=key) for f, v, q in fields]
    )


# ------------------------------------------------------------------ domain --


def test_two_sources_that_agree_keep_one_value_and_two_that_differ_keep_none() -> None:
    a, b = Bm04Source("doc:a", "Báo giá"), Bm04Source("doc:b", "BM04 NCC")
    found = reconcile(
        [
            Bm04Candidate("moq", "500", a, "MOQ: 500"),
            Bm04Candidate("moq", "500.0", b, "MOQ 500 cái"),
            Bm04Candidate("material", "Inox 304", a, "Inox 304"),
            Bm04Candidate("material", "Inox 201", b, "Inox 201"),
        ],
        frozenset({"moq"}),
    )
    assert found.kept["moq"].source is a
    assert "material" not in found.kept
    [conflict] = found.conflicts
    assert [c.source for c in conflict.candidates] == [a, b]


def test_a_price_conflict_names_its_documents_and_no_number() -> None:
    a, b = Bm04Source("doc:a", "Báo giá"), Bm04Source("doc:b", "BM04 NCC")
    found = reconcile(
        [
            Bm04Candidate("unit_price", "2.50", a, "Đơn giá 2,50 USD"),
            Bm04Candidate("unit_price", "2.70", b, "Đơn giá 2,70 USD"),
        ],
        frozenset({"unit_price"}),
    )
    words = conflict_message(found.conflicts[0], "Đơn giá", [Decimal("2.5"), Decimal("2.7")])
    assert "Báo giá" in words and "BM04 NCC" in words
    assert "2,5" not in words and "2.5" not in words and "2,7" not in words


def test_a_conflict_quotes_both_sources_and_masks_any_price_in_them() -> None:
    a, b = Bm04Source("doc:a", "Báo giá"), Bm04Source("doc:b", "BM04 NCC")
    found = reconcile(
        [
            Bm04Candidate("moq", "500", a, "MOQ 500 cái giá 2,50 USD"),
            Bm04Candidate("moq", "1000", b, "MOQ: 1000"),
        ],
        frozenset({"moq"}),
    )
    words = conflict_message(found.conflicts[0], "MOQ", [Decimal("2.5")])
    assert "MOQ 500 cái" in words and "MOQ: 1000" in words
    assert "2,50" not in words and "[giá]" in words


# ------------------------------------------------------------ preparing --


def test_code_fills_the_price_and_terms_from_the_quotation_and_the_model_never_sees_a_price() -> (
    None
):
    world = _world()
    case, entered = _case(world)
    quote_doc = _quotation(world, case)
    world.approve_record(case, RECORD_ROWS)
    world.gateway = ScriptedGateway(
        PROMPTS,
        answer=_writing(
            quote_doc,
            ("material", "inox 304", "chất liệu inox 304"),
            ("colours", "bạc", "màu bạc"),
        ),
    )
    prepared = _prepare(world, case, entered)
    assert prepared.outcome.value == "proposed"
    draft = _bm04(world)
    assert _value(draft, "product_name") == case.product_name
    assert _value(draft, "unit_price") == "2.50"
    assert draft.fields["unit_price"]["source"]["document_id"] == str(quote_doc.id)
    assert (_value(draft, "currency"), _value(draft, "moq")) == ("USD", "500")
    assert (_value(draft, "lead_time_days"), _value(draft, "incoterm")) == ("45", "FOB")
    assert _value(draft, "material") == "inox 304"
    assert draft.fields["material"]["source"]["ai_written"] is True
    assert draft.prompt_id == "supply_chain.draft_bm04"
    [sent] = world.gateway.sent
    for price in ("2,50", "2.50", "2.5"):
        assert price not in sent.user
    assert "chất liệu inox 304" in sent.user and "draft:" in sent.user
    assert not [f for f in _findings(prepared) if f["code"].startswith("bm04")]


def test_without_a_quotation_the_price_is_empty_and_never_asked_of_the_model() -> None:
    world = _world(Bm04Writing(fields=[]))
    case, entered = _case(world)
    prepared = _prepare(world, case, entered)
    assert prepared.outcome.value == "proposed"
    draft = _bm04(world)
    assert _value(draft, "unit_price") is None and _value(draft, "currency") is None
    [sent] = world.gateway.sent
    assert '"unit_price"' not in sent.user and '"moq"' not in sent.user


def test_two_prices_that_differ_are_a_conflict_naming_both_documents_and_an_empty_price() -> None:
    world = _world(Bm04Writing(fields=[]))
    case, entered = _case(world)
    _quotation(world, case)
    spec = world.add_document(case, DocumentType.PRODUCT_PROFILE_BM04, text="bm04 ncc")
    world.add_reading(
        spec,
        {
            "unit_price": {"value": "2.70", "quote": "Đơn giá: 2,70"},
            "currency": {"value": "USD", "quote": "USD"},
        },
    )
    prepared = _prepare(world, case, entered)
    assert _value(_bm04(world), "unit_price") is None
    assert _value(_bm04(world), "currency") == "USD"
    [conflict] = [f for f in _findings(prepared) if f["code"] == "bm04_conflict"]
    assert conflict["subject"] == "unit_price"
    assert (
        "Báo giá/spec của NCC" in conflict["message"] and "BM04 do NCC gửi" in conflict["message"]
    )
    assert "2,70" not in conflict["message"] and "2,50" not in conflict["message"]


def test_a_value_the_model_cannot_ground_and_any_commercial_field_it_writes_are_dropped() -> None:
    world = _world()
    case, entered = _case(world)
    quote_doc = _quotation(world, case)
    world.gateway = ScriptedGateway(
        PROMPTS,
        answer=_writing(
            quote_doc,
            ("material", "inox 316", "chất liệu inox 316"),  # quote not in evidence
            ("net_weight_g", "1300", "chất liệu inox 304"),  # number not in its quote
            ("colours", "đen", "màu bạc"),  # value not in its quote
            ("moq", "500", "MOQ: 500"),  # commercial: code's alone
        ),
    )
    prepared = _prepare(world, case, entered)
    assert prepared.outcome.value == "proposed"
    draft = _bm04(world)
    for name in ("material", "net_weight_g", "colours"):
        assert _value(draft, name) is None
    assert draft.fields["moq"]["source"]["document_id"] == str(quote_doc.id)
    assert "ai_written" not in draft.fields["moq"]["source"]


def test_a_source_read_in_another_workspace_or_a_case_of_another_tenant_calls_nothing() -> None:
    world = _world(Bm04Writing(fields=[]))
    case, entered = _case(world)
    document = world.add_document(case, DocumentType.SUPPLIER_QUOTATION, text="báo giá")
    reading = world.add_reading(document, QUOTATION)
    world.readings.rows[-1] = (document.tenant_id, uuid.uuid4(), reading)
    prepared = _prepare(world, case, entered)
    assert prepared.reason == "source_not_read_yet:supplier_quotation"
    assert world.gateway.sent == [] and _value_or_none(world) is None

    other = _world(Bm04Writing(fields=[]))
    stranger, entered = _case(other, tenant=uuid.uuid4())
    assert _prepare(other, stranger, entered).reason == "case_moved"
    assert other.gateway.sent == [] and other.drafts.rows == []


def _value_or_none(world: StepWorld) -> DocumentDraft | None:
    return next(
        (d for d in world.drafts.rows if d.doc_type is DocumentType.PRODUCT_PROFILE_BM04), None
    )


def test_several_price_lines_none_naming_the_product_is_no_price_and_a_finding() -> None:
    world = _world(Bm04Writing(fields=[]))
    case, entered = _case(world)
    line = QUOTATION["lines"][0]
    other_line = {
        "description": {"value": "Chảo 28cm", "quote": "| Chảo 28cm | 3,10 USD |"},
        "unit_price": {"value": "3.10", "quote": "| Chảo 28cm | 3,10 USD |"},
    }
    renamed = {**line, "description": {"value": "Nồi 26cm", "quote": "| Nồi 26cm |"}}
    _quotation(world, case, {**QUOTATION, "lines": [renamed, other_line]})
    prepared = _prepare(world, case, entered)
    assert _value(_bm04(world), "unit_price") is None
    assert "quotation_lines_ambiguous" in {f["code"] for f in _findings(prepared)}


def test_a_field_the_tenants_schema_requires_without_a_source_is_named() -> None:
    world = _world(Bm04Writing(fields=[]))
    schema = world.bm04_schema.model_dump()
    schema["fields"] = [
        {**f, "required": True} if f["key"] == "material" else f for f in schema["fields"]
    ]
    world.bm04_schema = SupplyChainBm04Schema.model_validate(schema)
    case, entered = _case(world)
    _quotation(world, case)
    prepared = _prepare(world, case, entered)
    missing = [f for f in _findings(prepared) if f["code"] == "bm04_required_missing"]
    assert [f["subject"] for f in missing] == ["material"]


# ------------------------------------------------------------- applying --


def _approve(world: StepWorld, prepared: Prepared, scopes: Sequence[str] = ()) -> str:
    decider = world.context(scopes=frozenset(scopes))
    result: str = _run(
        world.applier().apply(
            decider, prepared.payload, approved=True, comment="ok", typed_input={}, run_id=None
        )
    )
    return result


def _prepared_with_quotation(world: StepWorld) -> tuple[ProductDevelopmentCase, Prepared]:
    case, entered = _case(world)
    quote_doc = _quotation(world, case)
    world.gateway = ScriptedGateway(
        PROMPTS, answer=_writing(quote_doc, ("material", "inox 304", "chất liệu inox 304"))
    )
    return case, _prepare(world, case, entered)


def test_approving_makes_the_bm04_a_document_and_a_priced_profile_for_a_price_writer() -> None:
    world = _world()
    case, prepared = _prepared_with_quotation(world)
    assert _approve(world, prepared, [COMMERCIAL_WRITE]) == "complete_profile"
    assert world.cases.cases[case.id.value].state is ProductDevState.SUPPLIER_CONFIRMATION
    assert any(d.doc_type is DocumentType.PRODUCT_PROFILE_BM04 for d in world.documents.rows)
    [(_, profile)] = world.outcomes.profiles
    assert profile.commercial.unit_price == Decimal("2.50")
    assert profile.commercial.currency == "USD"
    assert (profile.commercial.moq, profile.commercial.lead_time_days) == (500, 45)
    assert profile.commercial.incoterm is not None and profile.commercial.incoterm.value == "FOB"
    assert profile.attributes["material"] == "inox 304"
    assert profile.attributes["product_name"] == case.product_name


def test_a_decider_who_may_not_set_prices_writes_the_profile_without_one() -> None:
    world = _world()
    _, prepared = _prepared_with_quotation(world)
    _approve(world, prepared)
    [(_, profile)] = world.outcomes.profiles
    assert profile.commercial.unit_price is None and profile.commercial.currency is None
    assert profile.commercial.moq == 500


def test_a_required_field_still_missing_writes_no_profile_and_says_so() -> None:
    world = _world()
    schema = world.bm04_schema.model_dump()
    schema["fields"] = [
        {**f, "required": True} if f["key"] == "origin_country" else f for f in schema["fields"]
    ]
    world.bm04_schema = SupplyChainBm04Schema.model_validate(schema)
    _, prepared = _prepared_with_quotation(world)
    assert _approve(world, prepared, [COMMERCIAL_WRITE]) == "complete_profile"
    assert world.outcomes.profiles == []
    [refused] = [a for a in world.outcomes.audits if a.action.endswith("not_saved")]
    assert "origin_country" in list(refused.details["missing"])  # type: ignore[call-overload]


def test_a_bm04_without_a_price_keeps_the_previous_versions_price() -> None:
    from datetime import UTC, datetime

    from dw_supply_chain.domain.commercial import ProductProfile, ProfileCommercial

    world = _world(Bm04Writing(fields=[]))
    case, entered = _case(world)
    earlier = ProductProfile(
        id=uuid.uuid4(),
        product_dev_case_id=case.id.value,
        version=1,
        commercial=ProfileCommercial.of(
            unit_price="2.40", currency="USD", moq=None, lead_time_days=None, incoterm=None
        ),
        attributes={"product_name": case.product_name},
        schema_version="1.0.0",
        created_by=uuid.uuid4(),
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    world.profiles.rows.append(earlier)
    world.profiles.scopes[earlier.id] = (case.tenant_id.value, case.workspace_id.value)
    prepared = _prepare(world, case, entered)
    _approve(world, prepared, [COMMERCIAL_WRITE])
    [(_, profile)] = world.outcomes.profiles
    assert profile.commercial.unit_price == Decimal("2.40")


def test_grounding_never_keeps_a_commercial_field_even_when_a_caller_offers_it() -> None:
    from dw_supply_chain.domain.bm04_prefill import ground_bm04_writing
    from dw_supply_chain.domain.grounded_writing import EvidenceItem

    source = Bm04Source("doc:a", "Báo giá")
    evidence = [EvidenceItem("doc:a", "Báo giá", "Đơn giá: 2,50 USD; MOQ: 500")]
    kept, dropped = ground_bm04_writing(
        Bm04Writing(
            fields=[
                Bm04FieldWriting(field="unit_price", value="2.50", quote="2,50 USD", cite="doc:a"),
                Bm04FieldWriting(field="moq", value="500", quote="MOQ: 500", cite="doc:a"),
            ]
        ),
        evidence,
        frozenset({"unit_price", "moq"}),
        frozenset({"unit_price", "moq"}),
        {"doc:a": source},
    )
    assert (kept, dropped) == ([], 2)
