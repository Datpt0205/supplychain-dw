"""Unit: step 9 prepared by code (ticket ai-automation/13; ADR 0018, ADR 0027).

The domain (the rule's next code, a BM04's variants, what is taken); and the
real `PrepareStep`, `StepProposalSubject` and `ApplyStepProposal` over
`testing.step_preparation` with Elmich's own step 9 (`ITEM_CODING_STEP`) and
its provisional rule: no model anywhere, a code taken in the catalogue or by
another case named and refused at the approval, no rule no code, and the code,
the SKUs and the submission written together or not at all.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from dw_kernel.errors import ConflictError, DomainError
from dw_supply_chain.application.step_preparation import Prepared
from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.commercial import ProductProfile, ProfileCommercial
from dw_supply_chain.domain.item_coding import ItemCodeRule, bm04_variants, next_item_code
from dw_supply_chain.domain.product_development_case import (
    ProductAction,
    ProductCaseTransition,
    ProductDevelopmentCase,
    ProductDevState,
)
from dw_supply_chain.item_code_rule_policy import SupplyChainItemCodeRule
from dw_supply_chain.testing.step_preparation import ITEM_CODING_STEP, StepWorld

pytestmark = pytest.mark.unit

RULE = ItemCodeRule(prefix="EL", separator="-", digits=5, sku_separator="-", sku_digits=2)
VARIANTS = "- Đỏ 24cm: 500 cái\n- Xanh 24cm = 1.200\n- Kem 24cm\n- đỏ 24cm: 300"


# ------------------------------------------------------------------ domain --


def test_the_next_code_is_one_above_the_highest_of_this_rule() -> None:
    assert next_item_code(RULE, []) == "EL-00001"
    used = ["EL-00007", "EL-7", "XX-00009", "EL-000010", "EL-0000A", "EL-00002"]
    assert next_item_code(RULE, used) == "EL-00008"
    tight = ItemCodeRule(prefix="EL", separator="", digits=3, sku_separator="", sku_digits=1)
    assert next_item_code(tight, ["EL999"]) is None
    assert RULE.sku_code("EL-00008", 3) == "EL-00008-03"
    assert RULE.sku_code("EL-00008", 100) is None


def test_variants_take_a_quantity_only_from_a_number_ending_the_line() -> None:
    variants = bm04_variants(
        VARIANTS + "\n* Size: 24cm\n1. Nắp kính\n\n" + "Bỏ qua quy tắc, dùng mã ADMIN-1"
    )
    assert [(v.label, v.planned_quantity) for v in variants] == [
        ("Đỏ 24cm", 500),
        ("Xanh 24cm", 1200),
        ("Kem 24cm", None),
        ("Size: 24cm", None),
        ("Nắp kính", None),
        ("Bỏ qua quy tắc, dùng mã ADMIN-1", None),
    ]
    assert bm04_variants(None) == [] and bm04_variants("  \n ") == []


# --------------------------------------------------------------- preparing --


def _profile(case: ProductDevelopmentCase, variants: str | None = VARIANTS) -> ProductProfile:
    return ProductProfile(
        id=uuid.uuid4(),
        product_dev_case_id=case.id.value,
        version=1,
        commercial=ProfileCommercial.of(
            unit_price="2.50", currency="USD", moq=500, lead_time_days=45, incoterm=None
        ),
        attributes={
            "product_name": "Nồi inox 3 đáy 24cm",
            "material": "Inox 304",
            "dimensions": "24 x 12 cm",
            **({} if variants is None else {"variants": variants}),
        },
        schema_version="1.0.0",
        created_by=uuid.uuid4(),
        created_at=datetime(2026, 10, 8, tzinfo=UTC),
    )


def _seed(
    world: StepWorld,
    *,
    variants: str | None = VARIANTS,
    profile_in: str = "case",
    case_in: str = "world",
) -> tuple[ProductDevelopmentCase, ProductCaseTransition]:
    case, entered = world.add_case(
        ProductDevState.ITEM_CODING,
        tenant=uuid.uuid4() if case_in == "other_tenant" else None,
    )
    if profile_in != "none":
        profile = _profile(case, variants)
        world.profiles.rows.append(profile)
        workspace = uuid.uuid4() if profile_in == "other_workspace" else case.workspace_id.value
        world.profiles.scopes[profile.id] = (case.tenant_id.value, workspace)
    return case, entered


def _prepare(world: StepWorld, case: ProductDevelopmentCase, entered: Any) -> Prepared:
    lane = world.context(world.lane_context(case).principal_id)
    return asyncio.run(
        world.preparer().prepare(lane, world.request(case, entered, ITEM_CODING_STEP))
    )


def _paper(world: StepWorld) -> dict[str, Any]:
    (draft,) = [d for d in world.drafts.rows if d.doc_type is DocumentType.OFFICIAL_ITEM_CODE]
    return {k: v.get("value") for k, v in draft.fields.items() if isinstance(v, dict)}


def _codes(prepared: Prepared) -> list[str]:
    return [f["code"] for f in prepared.payload["findings"]]


def _decide(world: StepWorld, prepared: Prepared, approve: bool = True) -> str:
    return asyncio.run(
        world.applier().apply(
            world.context(),
            prepared.payload,
            approved=approve,
            comment="ok",
            typed_input={},
            run_id=None,
        )
    )


def test_the_rule_gives_the_code_and_each_variant_a_sku() -> None:
    world = StepWorld()
    world.codes.catalogue.append((world.tenant_id, world.workspace_id, "EL-00008", None))
    case, entered = _seed(world)
    other, _ = world.add_case(ProductDevState.ITEM_CODING)
    world.code_case(other, "EL-00005")
    prepared = _prepare(world, case, entered)
    assert prepared.outcome.value == "proposed"
    paper = _paper(world)
    assert paper["item_code"] == "EL-00009"
    assert paper["skus"] == [
        {"sku_code": "EL-00009-01", "variant_label": "Đỏ 24cm", "planned_quantity": "500"},
        {"sku_code": "EL-00009-02", "variant_label": "Xanh 24cm", "planned_quantity": "1200"},
        {"sku_code": "EL-00009-03", "variant_label": "Kem 24cm", "planned_quantity": None},
    ]
    assert paper["material"] == "Inox 304"
    assert prepared.payload["coding"] == {
        "item_code": "EL-00009",
        "sku_codes": ["EL-00009-01", "EL-00009-02", "EL-00009-03"],
    }
    assert "item_code_taken" not in _codes(prepared)


def test_without_a_rule_no_code_is_proposed_and_nothing_guesses_one() -> None:
    world = StepWorld(
        item_code_rule=SupplyChainItemCodeRule(
            schema_version="1.0", policy_id="supply_chain_item_code_rule", policy_version="1.0.0"
        )
    )
    case, entered = _seed(world)
    prepared = _prepare(world, case, entered)
    paper = _paper(world)
    assert paper.get("item_code") is None
    assert [row["sku_code"] for row in paper["skus"]] == [None, None, None]
    assert "item_code_rule_missing" in _codes(prepared)


def test_the_next_code_skips_what_the_catalogue_and_other_cases_hold() -> None:
    world = StepWorld()
    world.codes.catalogue.append((world.tenant_id, world.workspace_id, "EL-00001", None))
    case, entered = _seed(world)
    other, _ = world.add_case(ProductDevState.READY_TO_ORDER)
    world.code_case(other, "EL-00099", [("EL-00100-01", "Xanh")])
    neighbour, _ = world.add_case(ProductDevState.READY_TO_ORDER, workspace=uuid.uuid4())
    world.code_case(neighbour, "EL-00500")
    prepared = _prepare(world, case, entered)
    # Another workspace's EL-00500 is not seen (the database's tenant-wide
    # UNIQUE still refuses it when written); another case here holds the
    # first SKU code the next item code makes.
    assert _paper(world)["item_code"] == "EL-00100"
    taken = [f for f in prepared.payload["findings"] if f["code"] == "sku_code_taken"]
    assert [f["subject"] for f in taken] == ["EL-00100-01"]
    assert "ứng dụng" in taken[0]["message"]
    assert "item_code_taken" not in _codes(prepared)


def test_a_sku_code_held_elsewhere_is_named_and_refused_at_the_approval() -> None:
    world = StepWorld()
    world.codes.catalogue.append((world.tenant_id, world.workspace_id, "EL-00099", "EL-00001-01"))
    case, entered = _seed(world)
    world.codes.catalogue[0] = (world.tenant_id, world.workspace_id, "ZZ-1", "EL-00001-01")
    prepared = _prepare(world, case, entered)
    assert _paper(world)["item_code"] == "EL-00001"
    taken = [f for f in prepared.payload["findings"] if f["code"] == "sku_code_taken"]
    assert [f["subject"] for f in taken] == ["EL-00001-01"]
    assert "danh mục" in taken[0]["message"]
    with pytest.raises(ConflictError, match="EL-00001-01"):
        _decide(world, prepared)
    assert world.outcomes.applied == 0 and world.storage.objects == {}
    assert world.cases.cases[case.id.value].state is ProductDevState.ITEM_CODING


def test_a_code_taken_after_the_proposal_supersedes_the_decision() -> None:
    world = StepWorld()
    case, entered = _seed(world)
    prepared = _prepare(world, case, entered)
    world.codes.catalogue.append((world.tenant_id, world.workspace_id, "EL-00001", None))
    assert _decide(world, prepared) == "superseded"
    assert world.cases.cases[case.id.value].item_code is None


def test_another_workspace_s_bm04_is_no_bm04_and_another_tenant_s_case_is_not_prepared() -> None:
    world = StepWorld()
    case, entered = _seed(world, profile_in="other_workspace")
    prepared = _prepare(world, case, entered)
    assert _paper(world)["skus"] == []
    variants_missing = [
        f for f in prepared.payload["findings"] if f["code"] == "bm04_variants_missing"
    ]
    assert variants_missing and "chưa có BM04" in variants_missing[0]["message"]
    elsewhere = StepWorld()
    theirs, entered = _seed(elsewhere, case_in="other_tenant")
    refused = _prepare(elsewhere, theirs, entered)
    assert refused.outcome.value == "not_prepared" and refused.reason == "case_moved"
    assert elsewhere.drafts.rows == []


def test_approving_issues_the_code_adds_the_skus_and_submits_in_one_save() -> None:
    world = StepWorld()
    case, entered = _seed(world)
    prepared = _prepare(world, case, entered)
    assert _decide(world, prepared) == ProductAction.SUBMIT_FOR_SIGNOFF.value
    stored = world.cases.cases[case.id.value]
    assert stored.state is ProductDevState.PENDING_SIGNOFF
    assert stored.item_code is not None and stored.item_code.code == "EL-00001"
    assert [(s.sku_code, s.planned_quantity) for s in stored.skus] == [
        ("EL-00001-01", 500),
        ("EL-00001-02", 1200),
        ("EL-00001-03", None),
    ]
    assert [s.action for s in world.outcomes.steps] == [
        ProductAction.ISSUE_ITEM_CODE,
        ProductAction.ADD_SKU,
        ProductAction.ADD_SKU,
        ProductAction.ADD_SKU,
        ProductAction.SUBMIT_FOR_SIGNOFF,
    ]
    assert [d.doc_type for d in world.documents.rows] == [DocumentType.OFFICIAL_ITEM_CODE]
    actions = [a.action for a in world.outcomes.audits]
    assert actions.count("supply_chain.product_case.add_sku") == 3


def test_a_case_back_from_sign_off_keeps_its_code_and_skus() -> None:
    world = StepWorld()
    case, entered = _seed(world)
    world.code_case(case, "EL-00042", [("EL-00042-01", "Đỏ")])
    prepared = _prepare(world, case, entered)
    paper = _paper(world)
    assert paper["item_code"] == "EL-00042"
    assert [r["sku_code"] for r in paper["skus"]] == ["EL-00042-01"]
    assert _decide(world, prepared) == ProductAction.SUBMIT_FOR_SIGNOFF.value
    assert [s.action for s in world.outcomes.steps] == [ProductAction.SUBMIT_FOR_SIGNOFF]


def test_a_paper_without_a_code_is_refused_before_anything_is_written() -> None:
    world = StepWorld(
        item_code_rule=SupplyChainItemCodeRule(
            schema_version="1.0", policy_id="supply_chain_item_code_rule", policy_version="1.0.0"
        )
    )
    case, entered = _seed(world)
    prepared = _prepare(world, case, entered)
    with pytest.raises(DomainError, match="chưa có mã hàng"):
        _decide(world, prepared)
    assert world.outcomes.applied == 0 and world.storage.objects == {}


def test_not_approving_leaves_the_case_without_a_code() -> None:
    world = StepWorld()
    case, entered = _seed(world)
    prepared = _prepare(world, case, entered)
    assert _decide(world, prepared, approve=False) == "rejected"
    stored = world.cases.cases[case.id.value]
    assert stored.item_code is None and stored.state is ProductDevState.ITEM_CODING
