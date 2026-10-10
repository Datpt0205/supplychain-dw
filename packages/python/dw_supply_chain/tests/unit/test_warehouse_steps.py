"""Unit: step 17 prepared by code (ticket ai-automation/18).

The domain (`receipt_check`: every PO line counted, a count refused out of
range, one answer to "which lines differ") and the real `PreparePOSteps`,
`GetPOStepProposal`, `ApprovePOStep` and the supplier message lane over the
in-memory worlds: the goods-received note is drafted from the PO lines and
the packing list; the warehouse types each count beside what was shipped; an
empty count never approves; the counts are recorded with the step and filed
on the note; a count that differs gets ONE discrepancy report and ONE claim
letter a person sends.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from docx import Document

from dw_agent_runtime.model.prompts import load_shipped_prompts
from dw_kernel.errors import DomainError, PermissionDeniedError
from dw_supply_chain.action_duties import CaseDuty
from dw_supply_chain.application.handlers import PO_CASE_READ, duty_scope
from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.document_draft import DraftDecision
from dw_supply_chain.domain.grounded_writing import CitedSentence
from dw_supply_chain.domain.po_case import CaseState, POCase
from dw_supply_chain.domain.po_step import POStepKind
from dw_supply_chain.domain.receipt_check import (
    LineCount,
    NewLineReceipt,
    counted_lines,
    discrepancies,
)
from dw_supply_chain.domain.supplier_message import MessagePurpose, SupplierMessageWriting
from dw_supply_chain.testing.extraction import ScriptedGateway
from dw_supply_chain.testing.po_steps import POStepWorld
from dw_supply_chain.testing.purchase_orders import NOW
from dw_supply_chain.testing.supplier_messages import MessageWorld

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[5]
WAREHOUSE = frozenset({duty_scope(CaseDuty.WAREHOUSE), PO_CASE_READ})
PACKING = {
    "lines": [
        {"sku_code": "EL-00001-01", "quantity": "500"},
        {"sku_code": "EL-00001-02", "quantity": "1200"},
    ]
}


def _ids(world: POStepWorld, case: POCase) -> tuple[uuid.UUID, uuid.UUID]:
    first, second = world.case(case).lines
    return first.sku_id, second.sku_id


# ------------------------------------------------------------------ domain --


def test_every_po_line_needs_its_count_and_a_count_is_a_whole_number_in_range() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    lines = [(a, "EL-1", 10), (b, "EL-2", 20)]
    with pytest.raises(DomainError) as missing:
        counted_lines(lines, {}, {a: 10})
    assert missing.value.details["skus"] == ["EL-2"]
    with pytest.raises(DomainError):
        counted_lines(lines, {}, {a: 10, b: 20, uuid.uuid4(): 1})
    with pytest.raises(DomainError):
        counted_lines(lines, {}, {a: -1, b: 20})
    counted = counted_lines(lines, {"EL-1": 9}, {a: 9, b: 18})
    assert [(c.shipped, c.expected) for c in counted] == [(9, 9), (None, 20)]


def test_a_count_is_held_to_what_was_shipped_or_else_to_what_was_ordered() -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    lines = [
        LineCount(a, "EL-1", ordered=500, shipped=500, counted=480),
        LineCount(b, "EL-2", ordered=1200, shipped=1200, counted=1200),
        LineCount(c, "EL-3", ordered=100, shipped=None, counted=110),
    ]
    differing = discrepancies(lines)
    assert [(d.sku_code, d.difference) for d in differing] == [("EL-1", -20), ("EL-3", 10)]
    assert differing[0].words() == "SKU EL-1: PO 500, NCC giao 500, kho đếm 480, thiếu 20"
    assert differing[1].words() == (
        "SKU EL-3: PO 100, packing list không ghi, kho đếm 110, thừa 10"
    )


# -------------------------------------------------------------- the step --


async def test_the_note_is_drafted_from_the_po_and_the_packing_list() -> None:
    world = POStepWorld()
    case = world.add_case(CaseState.WAREHOUSE_RECEIVING)
    world.add_document(case, DocumentType.PACKING_LIST, PACKING)
    await world.lane().run()
    await world.lane().run()
    (note,) = [d for d in world.drafts.rows if d.doc_type is DocumentType.WAREHOUSE_RECEIPT]
    rows = note.fields["items"]["value"]
    assert [(r["sku_code"], r["ordered"], r["shipped"], r["counted"]) for r in rows] == [
        ("EL-00001-01", "500", "500", None),
        ("EL-00001-02", "1200", "1200", None),
    ]
    page = await world.page().handle(world.context(WAREHOUSE), case.id)
    assert page.can_approve and page.proposed
    assert [(line.shipped, line.ordered) for line in page.lines] == [(500, 500), (1200, 1200)]


async def test_an_empty_count_does_not_approve_and_nothing_is_written() -> None:
    world = POStepWorld()
    case = world.add_case(CaseState.WAREHOUSE_RECEIVING)
    world.add_document(case, DocumentType.PACKING_LIST, PACKING)
    await world.lane().run()
    (note,) = [d for d in world.drafts.rows if d.doc_type is DocumentType.WAREHOUSE_RECEIPT]
    first, _ = _ids(world, case)
    for counts in ({}, {first: 500}):
        with pytest.raises(DomainError) as refused:
            await world.approver().handle(
                world.context(WAREHOUSE),
                case.id,
                kind=POStepKind.WAREHOUSE,
                draft_id=note.id,
                content_sha256=note.content_sha256,
                results={},
                counts=counts,
            )
        assert refused.value.details["field"] == "counts"
    assert world.outcomes.applied == 0 and world.receipts.rows == []
    assert world.case(case).state is CaseState.WAREHOUSE_RECEIVING


async def test_counts_on_a_step_that_takes_none_are_refused() -> None:
    world = POStepWorld()
    case = world.add_case(CaseState.IN_TRANSIT)
    world.add_document(case, DocumentType.ARRIVAL_NOTICE, {"eta": "2026-12-02"})
    first, _ = _ids(world, case)
    with pytest.raises(DomainError):
        await world.approver().handle(
            world.context(frozenset({duty_scope(CaseDuty.LOGISTICS), PO_CASE_READ})),
            case.id,
            kind=POStepKind.ARRIVAL,
            draft_id=None,
            content_sha256=None,
            results={"eta": "2026-12-02"},
            counts={first: 1},
        )


async def test_only_the_warehouse_counts() -> None:
    world = POStepWorld()
    case = world.add_case(CaseState.WAREHOUSE_RECEIVING)
    world.add_document(case, DocumentType.PACKING_LIST, PACKING)
    await world.lane().run()
    (note,) = [d for d in world.drafts.rows if d.doc_type is DocumentType.WAREHOUSE_RECEIPT]
    first, second = _ids(world, case)
    with pytest.raises(PermissionDeniedError):
        await world.approver().handle(
            world.context(),
            case.id,
            kind=POStepKind.WAREHOUSE,
            draft_id=note.id,
            content_sha256=note.content_sha256,
            results={},
            counts={first: 500, second: 1200},
        )


async def _counted(world: POStepWorld, case: POCase, first: int, second: int) -> None:
    await world.lane().run()
    (note,) = [d for d in world.drafts.rows if d.doc_type is DocumentType.WAREHOUSE_RECEIPT]
    a, b = _ids(world, case)
    moved = await world.approver().handle(
        world.context(WAREHOUSE),
        case.id,
        kind=POStepKind.WAREHOUSE,
        draft_id=note.id,
        content_sha256=note.content_sha256,
        results={},
        counts={a: first, b: second},
    )
    assert moved.state is CaseState.COMPLETED
    assert world.drafts.decisions[note.id][0] is DraftDecision.CONFIRMED


async def test_the_counts_are_recorded_with_the_step_and_filed_on_the_note() -> None:
    world = POStepWorld()
    case = world.add_case(CaseState.WAREHOUSE_RECEIVING)
    world.add_document(case, DocumentType.PACKING_LIST, PACKING)
    await _counted(world, case, 500, 1200)
    receipts = [r for _, _, r, _ in world.receipts.rows]
    assert [(r.line.sku_code, r.line.shipped, r.line.counted) for r in receipts] == [
        ("EL-00001-01", 500, 500),
        ("EL-00001-02", 1200, 1200),
    ]
    (filed,) = [d for d in world.documents.rows if d.doc_type is DocumentType.WAREHOUSE_RECEIPT]
    assert {r.document_id for r in receipts} == {filed.id.value}
    (step,) = [a for a in world.outcomes.audits if "differing_lines" in a.details]
    assert step.details["differing_lines"] == 0
    # Nothing differs: no report, no claim.
    await world.lane().run()
    assert not [d for d in world.drafts.rows if d.doc_type is DocumentType.DISCREPANCY_REPORT]


async def test_a_short_count_gets_one_discrepancy_report_cung_ung_is_told() -> None:
    world = POStepWorld()
    case = world.add_case(CaseState.WAREHOUSE_RECEIVING)
    world.add_document(case, DocumentType.PACKING_LIST, PACKING)
    await _counted(world, case, 480, 1200)
    (step,) = [a for a in world.outcomes.audits if "differing_lines" in a.details]
    assert step.details["differing_lines"] == 1
    # The filed note carries the count typed, not the draft's empty cell.
    (filed,) = [d for d in world.documents.rows if d.doc_type is DocumentType.WAREHOUSE_RECEIPT]
    note = Document(io.BytesIO(world.storage.objects[filed.object_key][0]))
    cells = [c.text for t in note.tables for row in t.rows for c in row.cells]
    assert "480" in cells
    await world.lane().run()
    await world.lane().run()
    (report,) = [d for d in world.drafts.rows if d.doc_type is DocumentType.DISCREPANCY_REPORT]
    (row,) = report.fields["items"]["value"]
    assert (row["sku_code"], row["shipped"], row["counted"], row["difference"]) == (
        "EL-00001-01",
        "500",
        "480",
        "-20",
    )
    assert any("biên bản chênh lệch" in n["title"] for n in world.notifier.sent)


async def test_a_count_of_another_workspace_drafts_nothing_here() -> None:
    world = POStepWorld()
    elsewhere = world.add_case(CaseState.COMPLETED, workspace=uuid.uuid4())
    (line, _) = elsewhere.lines
    # Both adapters forgot RLS: the lane's own workspace check holds.
    world.receipts.leaky = True
    world.store.leaky = True
    world.receipts.rows.append(
        (
            elsewhere.tenant_id.value,
            elsewhere.workspace_id.value,
            NewLineReceipt(
                id=uuid.uuid4(),
                po_case_id=elsewhere.id.value,
                line=LineCount(line.sku_id, "EL-00001-01", 500, 500, 1),
                document_id=None,
            ),
            NOW,
        )
    )
    assert await world.lane().discrepancy_reports(world.lane_context()) == 0
    assert not [d for d in world.drafts.rows if d.doc_type is DocumentType.DISCREPANCY_REPORT]


async def test_a_count_older_than_the_window_is_not_drafted() -> None:
    world = POStepWorld()
    case = world.add_case(CaseState.COMPLETED)
    (line, _) = case.lines
    world.receipts.rows.append(
        (
            case.tenant_id.value,
            case.workspace_id.value,
            NewLineReceipt(
                id=uuid.uuid4(),
                po_case_id=case.id.value,
                line=LineCount(line.sku_id, "EL-00001-01", 500, 500, 1),
                document_id=None,
            ),
            NOW - timedelta(days=31),
        )
    )
    assert await world.lane().discrepancy_reports(world.lane_context()) == 0


# ------------------------------------------------------------- the claim --


def test_a_count_that_differs_gets_one_claim_letter_citing_the_counts() -> None:
    prompts = load_shipped_prompts(REPO_ROOT / "configs")
    world = MessageWorld(
        gateway=ScriptedGateway(
            prompts,
            answer=SupplierMessageWriting(
                paragraphs=[CitedSentence(text="Kho đếm thiếu 20 sản phẩm.", cites=["x"])]
            ),
        )
    )
    steps = POStepWorld(tenant_id=world.tenant_id, workspace_id=world.workspace_id)
    case = steps.add_case(CaseState.COMPLETED)
    world.po_cases.cases[case.id.value] = case
    (line, _) = case.lines
    world.receipts.rows.append(
        (
            world.tenant_id,
            world.workspace_id,
            NewLineReceipt(
                id=uuid.uuid4(),
                po_case_id=case.id.value,
                line=LineCount(line.sku_id, "EL-00001-01", 500, 500, 480),
                document_id=None,
            ),
            NOW,
        )
    )
    world.enable(MessagePurpose.DISCREPANCY_CLAIM)
    asyncio.run(world.lane().run())
    asyncio.run(world.lane().run())
    [message] = world.messages.rows
    assert message.purpose is MessagePurpose.DISCREPANCY_CLAIM
    assert message.source_key == f"discrepancy_claim:{case.id}"
    [sent] = world.gateway.sent
    assert "SKU EL-00001-01: PO 500, NCC giao 500, kho đếm 480, thiếu 20" in sent.user
    assert "2.50" not in sent.user


def test_a_tenant_that_did_not_turn_claims_on_gets_none() -> None:
    world = MessageWorld(
        gateway=ScriptedGateway(
            load_shipped_prompts(REPO_ROOT / "configs"),
            answer=SupplierMessageWriting(paragraphs=[]),
        )
    )
    steps = POStepWorld(tenant_id=world.tenant_id, workspace_id=world.workspace_id)
    case = steps.add_case(CaseState.COMPLETED)
    world.po_cases.cases[case.id.value] = case
    (line, _) = case.lines
    world.receipts.rows.append(
        (
            world.tenant_id,
            world.workspace_id,
            NewLineReceipt(
                id=uuid.uuid4(),
                po_case_id=case.id.value,
                line=LineCount(line.sku_id, "EL-00001-01", 500, 500, 480),
                document_id=None,
            ),
            NOW,
        )
    )
    world.enable(MessagePurpose.PRODUCTION_PROGRESS)
    asyncio.run(world.lane().run())
    assert world.messages.rows == [] and world.gateway.sent == []
