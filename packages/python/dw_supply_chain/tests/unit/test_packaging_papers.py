"""Unit: step 12 with MKT as a minimal user, and its papers prepared by code
(ticket ai-automation/16; ADR 0028).

The domain (MKT's two steps in the diagram's order, only where the tenant
requires them; the proof check against the label rules, the BM04 and the PO's
SKUs) and the real `TakePackagingStep`, `GetPackagingDesign`,
`PreparePackagingPapers` and `GetPackagingProof` over `testing.packaging_papers`:
MKT is told when its pack is sent and takes no Cung ứng step; Cung ứng cannot
submit MKT's content; the skeletons carry only the BM04's words; a revision
request is drafted once per source; nothing of another workspace is read.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
import yaml

from dw_kernel.errors import ConflictError, PermissionDeniedError
from dw_supply_chain.application.handlers import PO_CASE_READ
from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.extraction import ExtractionStatus, normalize
from dw_supply_chain.domain.packaging_design import (
    MKT_LEAD_NOTE,
    MKT_PACK_NOTE,
    PackagingAction,
    PackagingDesign,
    ReviewStatus,
)
from dw_supply_chain.domain.payment_check import SourceRead
from dw_supply_chain.domain.po_case import POCase
from dw_supply_chain.domain.proof_check import (
    LABEL_REQUIRED,
    barcode_valid,
    proof_findings,
)
from dw_supply_chain.testing.packaging_papers import (
    BM04,
    ELMICH_PACKAGING,
    FULL_PROOF,
    MKT,
    PackagingWorld,
    drafted_values,
    proof_fields,
)
from dw_supply_chain.testing.purchase_orders import NOW, PLATFORM_DUTIES

pytestmark = pytest.mark.unit

A = PackagingAction
REPO_ROOT = Path(__file__).resolve().parents[5]
SKUS = ["EL-00001-01", "EL-00001-02"]


def _proof(**over: object) -> SourceRead:
    reading = {**FULL_PROOF, **over}
    return SourceRead(
        DocumentType.PACKAGING_DESIGN,
        document_id=uuid.uuid4(),
        status=ExtractionStatus.EXTRACTED,
        fields=proof_fields({k: v for k, v in reading.items() if v is not None}),
    )


# ------------------------------------------------------------------ domain --


def test_the_label_rules_the_check_holds_are_the_ones_the_skill_explains() -> None:
    """One copy of the knowledge in words (the skill), one in code (the
    check); each item here must be a line of the skill, so they cannot drift
    quietly."""
    skill = yaml.safe_load(
        (REPO_ROOT / "configs/skills/supply_chain/supply_chain.label_rules@1.1.0.yaml").read_text(
            encoding="utf-8"
        )
    )
    body = normalize(skill["body"])
    for _, words in LABEL_REQUIRED:
        assert normalize(words) in body, words


def test_a_full_proof_matching_the_bm04_has_nothing_to_say() -> None:
    assert proof_findings(_proof(), BM04, SKUS) == []


@pytest.mark.parametrize(
    ("over", "found"),
    [
        ({"origin": None}, ("label_missing", "origin")),
        ({"warnings": None}, ("label_missing", "warnings")),
        ({"responsible_party": None}, ("label_missing", "responsible_party")),
        ({"material": "Nhôm"}, ("proof_differs", "material")),
        ({"product_name": "Chảo chống dính 26cm"}, ("proof_differs", "product_name")),
        ({"dimensions": "Đường kính 26 cm, cao 13 cm"}, ("proof_differs", "dimensions")),
        ({"origin": "Sản xuất tại Việt Nam"}, ("proof_differs", "origin")),
        ({"sku_codes": ["EL-00001-01", "EL-99999-01"]}, ("sku_unknown", "EL-99999-01")),
        ({"sku_codes": ["EL-00001-01"]}, ("sku_missing", "EL-00001-02")),
        ({"barcode": "8935001800018"}, ("barcode_invalid", "barcode")),
    ],
)
def test_what_the_proof_lacks_or_prints_differently_is_named(
    over: dict[str, object], found: tuple[str, str]
) -> None:
    named = {(f.code, f.subject) for f in proof_findings(_proof(**over), BM04, SKUS)}
    assert found in named


def test_no_bm04_is_named_never_a_pass_and_an_unread_proof_says_nothing() -> None:
    assert [f.code for f in proof_findings(_proof(), None, SKUS)] == ["bm04_missing"]
    unread = SourceRead(DocumentType.PACKAGING_DESIGN, document_id=uuid.uuid4())
    assert proof_findings(unread, BM04, SKUS) == []


def test_a_barcode_is_checked_by_its_own_check_digit() -> None:
    assert barcode_valid("8935001800019")
    assert barcode_valid("036000291452")  # UPC-A
    assert barcode_valid("96385074")  # EAN-8
    assert not barcode_valid("8935001800018")
    assert not barcode_valid("12345")


def _design() -> PackagingDesign:
    from dw_kernel.ids import TenantId, WorkspaceId

    return PackagingDesign(
        po_case_id=uuid.uuid4(),
        tenant_id=TenantId(uuid.uuid4()),
        workspace_id=WorkspaceId(uuid.uuid4()),
    )


def test_mkts_steps_stand_between_the_colour_and_the_design_only_where_required() -> None:
    d = _design()
    take = d.take
    take(
        A.APPROVE_COLOUR,
        case_in_pre_production=True,
        reason=None,
        document=None,
        now=NOW,
        mkt_required=True,
    )
    with pytest.raises(ConflictError):
        take(
            A.APPROVE_DESIGN,
            case_in_pre_production=True,
            reason=None,
            document=None,
            now=NOW,
            mkt_required=True,
        )
    take(
        A.SEND_MKT_PACK,
        case_in_pre_production=True,
        reason=None,
        document=None,
        now=NOW,
        mkt_required=True,
    )
    events = d.pop_pending_events()
    assert [e.note for e in events] == [None, MKT_PACK_NOTE]

    plain = _design()
    plain.take(
        A.APPROVE_COLOUR,
        case_in_pre_production=True,
        reason=None,
        document=None,
        now=NOW,
        mkt_required=False,
    )
    assert [e.note for e in plain.pop_pending_events()] == [MKT_LEAD_NOTE]
    with pytest.raises(ConflictError):
        plain.take(
            A.SEND_MKT_PACK,
            case_in_pre_production=True,
            reason=None,
            document=None,
            now=NOW,
            mkt_required=False,
        )


# ------------------------------------------------------------- handlers --


async def _pack_sent(world: PackagingWorld) -> POCase:
    case = world.add_case()
    world.set_design(case, colour_status=ReviewStatus.APPROVED)
    await world.take().handle(world.context(), po_case_id=case.id, action=A.SEND_MKT_PACK)
    return case


async def test_sending_the_pack_tells_mkt_and_mkt_submits_its_content_then_cung_ung() -> None:
    world = PackagingWorld()
    case = await _pack_sent(world)
    assert world.notifier.sent[-1]["recipients"] == [world.marketer]

    early = world.add_document(
        case, DocumentType.PACKAGING_CONTENT, status=None, uploaded_at=NOW - timedelta(days=1)
    )
    marketer = world.context(frozenset({MKT, PO_CASE_READ}))
    with pytest.raises(ConflictError):
        await world.take().handle(
            marketer,
            po_case_id=case.id,
            action=A.SUBMIT_PACKAGING_CONTENT,
            document_id=early.id.value,
        )
    content = world.add_document(
        case, DocumentType.PACKAGING_CONTENT, status=None, uploaded_at=NOW + timedelta(hours=1)
    )
    # Cung ứng does not submit MKT's content; MKT does not approve the design.
    with pytest.raises(PermissionDeniedError):
        await world.take().handle(
            world.context(),
            po_case_id=case.id,
            action=A.SUBMIT_PACKAGING_CONTENT,
            document_id=content.id.value,
        )
    await world.take().handle(
        marketer,
        po_case_id=case.id,
        action=A.SUBMIT_PACKAGING_CONTENT,
        document_id=content.id.value,
    )
    assert world.notifier.sent[-1]["recipients"] == [world.buyer]
    with pytest.raises(PermissionDeniedError):
        await world.take().handle(
            marketer,
            po_case_id=case.id,
            action=A.APPROVE_DESIGN,
        )
    await world.take().handle(world.context(), po_case_id=case.id, action=A.APPROVE_DESIGN)


async def test_the_page_offers_mkts_steps_and_the_pack_only_where_required() -> None:
    world = PackagingWorld()
    case = world.add_case()
    world.add_document(case, DocumentType.MAQUETTE, status=None)
    page = await world.page().handle(world.context(), case.id)
    assert {s.action for s in page.steps} >= {A.SEND_MKT_PACK, A.SUBMIT_PACKAGING_CONTENT}
    pack = {item.doc_type: item.document_id for item in page.pack}
    assert pack[DocumentType.MAQUETTE] is not None
    assert pack[DocumentType.USER_MANUAL] is None

    plain = PackagingWorld()
    plain.overrides.stored["supply_chain_packaging"] = ELMICH_PACKAGING.model_copy(
        update={"require_packaging_content": False}
    ).model_dump(mode="json")
    other = plain.add_case()
    page = await plain.page().handle(plain.context(), other.id)
    assert not {s.action for s in page.steps} & {A.SEND_MKT_PACK, A.SUBMIT_PACKAGING_CONTENT}
    assert page.pack == ()


# ------------------------------------------------------------------ lane --


async def test_the_skeletons_carry_only_the_bm04s_words_once_the_colour_is_approved() -> None:
    world = PackagingWorld()
    case = world.add_case()
    world.add_profile(case)
    assert await world.lane().run() is not None
    assert world.drafted(DocumentType.PACKAGING_CONTENT) == []

    world.set_design(case, colour_status=ReviewStatus.APPROVED)
    await world.lane().run()
    await world.lane().run()
    (content,) = drafted_values(world.drafted(DocumentType.PACKAGING_CONTENT))
    assert content["product_name"] == BM04["product_name"]
    assert content["origin"] == BM04["origin_country"]
    assert content["sku_codes"] == "EL-00001-01, EL-00001-02"
    assert "warnings" not in content and "responsible_party" not in content
    (draft,) = world.drafted(DocumentType.PACKAGING_CONTENT)
    assert "warnings" in draft.gaps and "responsible_party" in draft.gaps
    assert len(world.drafted(DocumentType.USER_MANUAL)) == 1


async def test_a_colour_sample_gets_one_request_until_a_new_sample() -> None:
    world = PackagingWorld()
    case = world.add_case()
    world.add_profile(case)
    world.add_document(case, DocumentType.COLOUR_SAMPLE, status=None)
    await world.lane().run()
    await world.lane().run()
    (request,) = drafted_values(world.drafted(DocumentType.COLOUR_REVISION_REQUEST))
    assert request["expected_colours"] == "Bạc"
    assert "requirement" not in request
    world.add_document(case, DocumentType.COLOUR_SAMPLE, status=None)
    await world.lane().run()
    assert len(world.drafted(DocumentType.COLOUR_REVISION_REQUEST)) == 2


async def test_a_proof_with_findings_gets_a_design_request_one_item_each() -> None:
    world = PackagingWorld()
    case = world.add_case()
    world.add_profile(case)
    world.add_document(
        case, DocumentType.PACKAGING_DESIGN, {**FULL_PROOF, "origin": None, "material": "Nhôm"}
    )
    await world.lane().run()
    await world.lane().run()
    (request,) = drafted_values(world.drafted(DocumentType.DESIGN_REVISION_REQUEST))
    items = {(i["subject"], i["requirement"]) for i in request["items"]}
    assert ("origin", "Theo BM04: Trung Quốc") in items
    assert ("material", "Theo BM04: Inox 304") in items
    assert world.notifier.sent[-1]["recipients"] == [world.buyer]

    clean = PackagingWorld()
    good = clean.add_case()
    clean.add_profile(good)
    clean.add_document(good, DocumentType.PACKAGING_DESIGN, FULL_PROOF)
    await clean.lane().run()
    assert clean.drafted(DocumentType.DESIGN_REVISION_REQUEST) == []


async def test_another_workspaces_bm04_or_proof_is_not_read() -> None:
    world = PackagingWorld()
    case = world.add_case()
    world.add_profile(case, workspace=uuid.uuid4())
    world.add_document(
        case,
        DocumentType.PACKAGING_DESIGN,
        {**FULL_PROOF, "origin": None},
        workspace=uuid.uuid4(),
    )
    world.set_design(case, colour_status=ReviewStatus.APPROVED)
    await world.lane().run()
    assert world.drafted(DocumentType.DESIGN_REVISION_REQUEST) == []
    (content,) = drafted_values(world.drafted(DocumentType.PACKAGING_CONTENT))
    assert "product_name" not in content
    page = await world.proof_page().handle(world.context(), case.id)
    assert page.proof is not None and page.proof.document_id is None and page.findings == ()


async def test_the_platform_prepares_nothing_and_the_proof_page_says_so() -> None:
    world = PackagingWorld()
    world.overrides.stored["supply_chain_step_preparation"] = (
        world.lane()
        .platform_default_policy.model_copy(update={"packaging": False})
        .model_dump(mode="json")
    )
    case = world.add_case()
    world.add_profile(case)
    world.add_document(case, DocumentType.PACKAGING_DESIGN, {**FULL_PROOF, "origin": None})
    world.add_document(case, DocumentType.COLOUR_SAMPLE, status=None)
    await world.lane().run()
    assert world.drafts.rows == []
    page = await world.proof_page().handle(world.context(), case.id)
    assert page.proof is None and page.findings == ()


async def test_a_proof_finding_names_no_price() -> None:
    world = PackagingWorld()
    case = world.add_case()
    world.add_profile(case)
    world.add_document(case, DocumentType.PACKAGING_DESIGN, {**FULL_PROOF, "origin": None})
    page = await world.proof_page().handle(world.context(), case.id)
    words = json.dumps([f.message for f in page.findings], ensure_ascii=False)
    assert not re.search(r"\d+[.,]\d{2}\b", words)


async def test_a_tenant_whose_duty_override_predates_mkt_still_takes_mkts_steps() -> None:
    # The resolver every step authorizes by reads a stored override through
    # `from_stored`: one written at 1.2.0 never named MKT's steps.
    world = PackagingWorld()
    stored = PLATFORM_DUTIES.model_dump(mode="json")
    stored["policy_version"] = "1.2.0"
    for step in ("send_mkt_pack", "submit_packaging_content"):
        stored["action_duties"].pop(step)
    world.overrides.stored["supply_chain_action_duties"] = stored
    case = await _pack_sent(world)
    assert world.notifier.sent[-1]["recipients"] == [world.marketer]
    assert case.id.value in world.designs.rows
