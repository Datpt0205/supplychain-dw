"""Unit: steps 13-15 prepared by code (ticket ai-automation/17).

The domain (QC suggested by the numbers, never the report's words; the
customs file held to the skill; containers and ETD) and the real
`PreparePOSteps`, `GetPOStepProposal` and `ApprovePOStep` over
`testing.po_steps`: QC's verdict is QC's, typed beside code's suggestion; a
rework request is drafted only when the numbers fail and is closed when QC
passes; the arrival is proposed once the carrier's notice is read; the packing
list is matched with the PO by SKU; the dates and the container are written by
the step that learns them. And the weekly production chase a person sends.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from datetime import date
from pathlib import Path

import pytest
import yaml

from dw_agent_runtime.model.prompts import load_shipped_prompts
from dw_kernel.errors import DomainError, PermissionDeniedError
from dw_supply_chain.action_duties import CaseDuty
from dw_supply_chain.application.handlers import COMMERCIAL_WRITE, PO_CASE_READ, duty_scope
from dw_supply_chain.application.step_preparation import StoredReading
from dw_supply_chain.domain.case_document import CaseKind, DocumentType
from dw_supply_chain.domain.commercial import CommercialTerms, PaymentKind
from dw_supply_chain.domain.document_draft import DraftDecision
from dw_supply_chain.domain.extraction import EXTRACTION_SPECS, ExtractionStatus, normalize
from dw_supply_chain.domain.grounded_writing import CitedSentence
from dw_supply_chain.domain.payment_check import SourceRead
from dw_supply_chain.domain.po_case import CaseState, POCase, container_number
from dw_supply_chain.domain.po_step import POStepKind
from dw_supply_chain.domain.shipping_check import CUSTOMS_FILE, qc_suggestion
from dw_supply_chain.domain.supplier_message import MessagePurpose, SupplierMessageWriting
from dw_supply_chain.testing.extraction import ScriptedGateway
from dw_supply_chain.testing.po_steps import POStepWorld, cited
from dw_supply_chain.testing.purchase_orders import NOW
from dw_supply_chain.testing.supplier_messages import MessageWorld

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[5]
QC = duty_scope(CaseDuty.QC)
LOGISTICS = duty_scope(CaseDuty.LOGISTICS)
ORDERING = duty_scope(CaseDuty.ORDERING)
QC_PASSING = {
    "critical_found": "0",
    "critical_accept": "0",
    "major_found": "2",
    "major_accept": "3",
    "minor_found": "4",
    "minor_accept": "7",
    "stated_result": "pass",
}
QC_FAILING = {**QC_PASSING, "major_found": "5", "defects": None}


def _report(fields: Mapping[str, str | None]) -> SourceRead:
    return SourceRead(
        DocumentType.QC_REPORT,
        document_id=uuid.uuid4(),
        status=ExtractionStatus.EXTRACTED,
        fields=cited({k: v for k, v in fields.items() if v is not None}),
    )


# ------------------------------------------------------------------ domain --


def test_qc_follows_the_numbers_never_the_reports_word() -> None:
    passing = qc_suggestion(_report(QC_PASSING))
    assert passing.verdict == "pass" and passing.findings == ()
    failing = qc_suggestion(_report(QC_FAILING))
    assert failing.verdict == "fail"
    assert {f.code for f in failing.findings} == {"qc_over_aql", "qc_stated_differs"}
    bare = qc_suggestion(_report({"stated_result": "pass"}))
    assert bare.verdict is None
    assert [f.code for f in bare.findings] == ["qc_numbers_missing"]


def test_the_customs_file_the_check_holds_is_the_one_the_skill_explains() -> None:
    skill = yaml.safe_load(
        (REPO_ROOT / "configs/skills/supply_chain/supply_chain.customs_file@1.1.0.yaml").read_text(
            encoding="utf-8"
        )
    )
    body = normalize(skill["body"])
    for _, words, _ in CUSTOMS_FILE:
        assert normalize(words) in body, words


def test_a_container_number_is_an_iso_6346_one() -> None:
    assert container_number("mscu 123456-7") == "MSCU1234567"
    with pytest.raises(DomainError):
        container_number("MSC1234567")


# ---------------------------------------------------------------------- QC --

QC_SCOPES = frozenset({QC, PO_CASE_READ})


async def test_qc_is_typed_beside_codes_suggestion_and_a_failing_report_gets_a_rework_draft() -> (
    None
):
    world = POStepWorld()
    case = world.add_case(CaseState.QC)
    world.add_document(
        case,
        DocumentType.QC_REPORT,
        {**{k: v for k, v in QC_FAILING.items() if v is not None}, "defects": None},
    )
    world.add_document(case, DocumentType.PACKING_LIST, {"container_number": "MSCU1234567"})
    await world.lane().run()
    await world.lane().run()
    reworks = [d for d in world.drafts.rows if d.doc_type is DocumentType.REWORK_REQUEST]
    assert len(reworks) == 1

    page = await world.page().handle(world.context(QC_SCOPES), case.id)
    suggested = {r.field.name: r.suggestion for r in page.results}
    assert suggested["qc_result"] is not None and suggested["qc_result"].value == "fail"
    assert suggested["container_number"] is not None
    codes = [f.code for f in page.findings]
    assert "qc_over_aql" in codes and "qc_stated_differs" in codes
    assert page.can_approve and not page.proposed

    # The suggestion never decides: no verdict typed, nothing is taken.
    with pytest.raises(DomainError):
        await world.approver().handle(
            world.context(QC_SCOPES),
            case.id,
            kind=POStepKind.QC,
            draft_id=None,
            content_sha256=None,
            results={},
        )
    # A fail needs its reason, refused as that field before the case moves.
    with pytest.raises(DomainError) as refused:
        await world.approver().handle(
            world.context(QC_SCOPES),
            case.id,
            kind=POStepKind.QC,
            draft_id=None,
            content_sha256=None,
            results={"qc_result": "fail"},
        )
    assert refused.value.details == {"field": "reason"}
    rework = reworks[0]
    moved = await world.approver().handle(
        world.context(QC_SCOPES),
        case.id,
        kind=POStepKind.QC,
        draft_id=rework.id,
        content_sha256=rework.content_sha256,
        results={"qc_result": "fail", "reason": "Lỗi nặng vượt Ac"},
    )
    assert moved.state is CaseState.REWORK
    assert [d.doc_type for d in world.documents.rows if d.doc_type is DocumentType.REWORK_REQUEST]
    assert world.drafts.decisions[rework.id][0] is DraftDecision.CONFIRMED


async def test_qc_passing_by_hand_closes_the_rework_draft_and_records_the_container() -> None:
    world = POStepWorld()
    case = world.add_case(CaseState.QC)
    world.add_document(case, DocumentType.QC_REPORT, {"major_found": "5", "major_accept": "3"})
    await world.lane().run()
    (rework,) = [d for d in world.drafts.rows if d.doc_type is DocumentType.REWORK_REQUEST]
    with pytest.raises(DomainError):
        await world.approver().handle(
            world.context(QC_SCOPES),
            case.id,
            kind=POStepKind.QC,
            draft_id=rework.id,
            content_sha256=rework.content_sha256,
            results={"qc_result": "pass", "container_number": "NOT-A-CONTAINER"},
        )
    moved = await world.approver().handle(
        world.context(QC_SCOPES),
        case.id,
        kind=POStepKind.QC,
        draft_id=rework.id,
        content_sha256=rework.content_sha256,
        results={"qc_result": "pass", "container_number": "MSCU 1234567"},
    )
    assert moved.state is CaseState.IN_TRANSIT
    assert world.case(case).shipping.container_number == "MSCU1234567"
    assert world.drafts.decisions[rework.id][0] is DraftDecision.REJECTED
    assert not [d for d in world.documents.rows if d.doc_type is DocumentType.REWORK_REQUEST]


async def test_a_passing_report_drafts_no_rework_and_only_qc_decides() -> None:
    world = POStepWorld()
    case = world.add_case(CaseState.QC)
    world.add_document(case, DocumentType.QC_REPORT, QC_PASSING)
    await world.lane().run()
    assert not [d for d in world.drafts.rows if d.doc_type is DocumentType.REWORK_REQUEST]
    page = await world.page().handle(world.context(QC_SCOPES), case.id)
    assert page.proposed
    with pytest.raises(PermissionDeniedError):
        await world.approver().handle(
            world.context(frozenset({ORDERING, PO_CASE_READ})),
            case.id,
            kind=POStepKind.QC,
            draft_id=None,
            content_sha256=None,
            results={"qc_result": "pass"},
        )


# -------------------------------------------------------- production, arrival --


async def test_production_names_a_late_etd_and_records_the_etd_typed() -> None:
    world = POStepWorld()
    case = world.add_case(CaseState.PRODUCTION)
    world.store.terms[case.id.value] = CommercialTerms.of(
        currency="USD",
        incoterm=None,
        payment_terms=None,
        deposit_percent="30",
        expected_delivery_date=date(2026, 11, 15),
    )
    world.add_document(case, DocumentType.PRODUCTION_SCHEDULE, {"etd": "2026-11-20"})
    page = await world.page().handle(world.context(), case.id)
    assert "etd_late" in [f.code for f in page.findings]
    (etd,) = [r for r in page.results if r.field.name == "etd"]
    assert etd.suggestion is not None and etd.suggestion.value == "2026-11-20"
    moved = await world.approver().handle(
        world.context(),
        case.id,
        kind=POStepKind.PRODUCTION,
        draft_id=None,
        content_sha256=None,
        results={"etd": "2026-11-20"},
    )
    assert moved.state is CaseState.QC
    assert world.case(case).shipping.etd == date(2026, 11, 20)


LOGISTICS_SCOPES = frozenset({LOGISTICS, PO_CASE_READ})
PACKING_MATCHING = {
    "container_number": "MSCU1234567",
    "lines": [
        {"sku_code": "EL-00001-01", "quantity": "500"},
        {"sku_code": "EL-00001-02", "quantity": "1200"},
    ],
}


def _customs(world: POStepWorld, case: POCase) -> None:
    for doc_type in (DocumentType.PURCHASE_ORDER, DocumentType.CERTIFICATE_OF_ORIGIN):
        world.add_document(case, doc_type, status=None)
    world.add_document(case, DocumentType.COMMERCIAL_INVOICE, {"total": "4250"})
    world.add_document(
        case, DocumentType.BILL_OF_LADING, {"container_numbers": None, "eta": "2026-12-01"}
    )


async def test_the_arrival_waits_for_the_notice_then_is_proposed_when_all_matches() -> None:
    world = POStepWorld()
    case = world.add_case(CaseState.IN_TRANSIT)
    _customs(world, case)
    world.add_document(case, DocumentType.PACKING_LIST, PACKING_MATCHING)
    page = await world.page().handle(world.context(LOGISTICS_SCOPES), case.id)
    assert not page.proposed
    assert ("source_missing", "arrival_notice") in {(f.code, f.subject) for f in page.findings}

    world.add_document(case, DocumentType.ARRIVAL_NOTICE, {"eta": "2026-12-02"})
    page = await world.page().handle(world.context(LOGISTICS_SCOPES), case.id)
    assert page.findings == () and page.proposed
    (eta,) = [r for r in page.results if r.field.name == "eta"]
    assert eta.suggestion is not None and eta.suggestion.value == "2026-12-02"
    moved = await world.approver().handle(
        world.context(LOGISTICS_SCOPES),
        case.id,
        kind=POStepKind.ARRIVAL,
        draft_id=None,
        content_sha256=None,
        results={"eta": "2026-12-02"},
    )
    assert moved.state is CaseState.ARRIVED_PORT
    assert world.case(case).shipping.eta == date(2026, 12, 2)


async def test_the_packing_list_is_matched_with_the_po_and_the_customs_file_is_counted() -> None:
    world = POStepWorld()
    case = world.add_case(CaseState.IN_TRANSIT)
    world.add_document(
        case,
        DocumentType.PACKING_LIST,
        {
            "container_number": "TGHU7654321",
            "lines": [
                {"sku_code": "EL-00001-01", "quantity": "480"},
                {"sku_code": "EL-00001-02", "quantity": "1200"},
            ],
        },
    )
    world.add_document(case, DocumentType.ARRIVAL_NOTICE, {"eta": "2026-12-02"})
    notice = world.readings.rows[-1][2]
    world.readings.rows[-1] = (
        world.readings.rows[-1][0],
        world.readings.rows[-1][1],
        StoredReading(
            id=notice.id,
            document_id=notice.document_id,
            sha256=notice.sha256,
            prompt_id=notice.prompt_id,
            prompt_version=notice.prompt_version,
            status=notice.status,
            fields={
                **notice.fields,
                "container_numbers": [{"value": "MSCU1234567", "quote": "MSCU1234567"}],
            },
            gaps=[],
        ),
    )
    page = await world.page().handle(world.context(LOGISTICS_SCOPES), case.id)
    found = {(f.code, f.subject) for f in page.findings}
    assert ("line_quantity_differs", "EL-00001-01") in found
    assert ("container_differs", "arrival_notice") in found
    assert ("customs_missing", "bill_of_lading") in found
    assert ("customs_optional_missing", "certificate_of_origin") in found


async def test_the_final_request_matches_three_ways() -> None:
    world = POStepWorld()
    world.add_master_account()
    case = world.add_case(CaseState.ARRIVED_PORT)
    world.add_payment(case, PaymentKind.DEPOSIT, "1275")
    world.add_document(
        case,
        DocumentType.PACKING_LIST,
        {"lines": [{"sku_code": "EL-00001-01", "quantity": "500"}]},
    )
    world.add_document(case, DocumentType.QC_REPORT, {"major_found": "9", "major_accept": "3"})
    page = await world.page().handle(world.context(), case.id)
    found = {(f.code, f.subject) for f in page.findings}
    assert ("line_missing", "EL-00001-02") in found
    assert ("qc_report_fails", "qc_report") in found


# ------------------------------------------------------- the weekly chase --


def test_a_po_in_production_gets_one_progress_chase_a_week_citing_its_schedule() -> None:
    prompts = load_shipped_prompts(REPO_ROOT / "configs")
    world = MessageWorld(
        gateway=ScriptedGateway(
            prompts,
            answer=SupplierMessageWriting(
                paragraphs=[
                    CitedSentence(
                        text="Theo lịch, công đoạn đóng gói dự kiến 2026-11-10.", cites=["doc"]
                    )
                ]
            ),
        )
    )
    from dw_supply_chain.testing.po_steps import POStepWorld as _Steps

    steps = _Steps(tenant_id=world.tenant_id, workspace_id=world.workspace_id)
    case = steps.add_case(CaseState.PRODUCTION)
    other = steps.add_case(CaseState.PRODUCTION, workspace=uuid.uuid4())
    world.po_cases.cases.update({case.id.value: case, other.id.value: other})
    paper = steps.add_document(
        case,
        DocumentType.PRODUCTION_SCHEDULE,
        {"etd": "2026-11-20", "milestones": None},
    )
    world.documents.rows.append(paper)
    # A newer paper of another kind is not the schedule.
    world.documents.rows.append(
        steps.add_document(case, DocumentType.PACKING_LIST, uploaded_at=NOW)
    )
    # A case past production is not chased.
    shipped = steps.add_case(CaseState.IN_TRANSIT)
    world.po_cases.cases[shipped.id.value] = shipped
    spec = EXTRACTION_SPECS[DocumentType.PRODUCTION_SCHEDULE]
    world.readings.rows.append(
        (
            world.tenant_id,
            world.workspace_id,
            StoredReading(
                id=uuid.uuid4(),
                document_id=paper.id.value,
                sha256=paper.sha256,
                prompt_id=spec.prompt_id,
                prompt_version=spec.prompt_version,
                status=ExtractionStatus.EXTRACTED,
                fields={
                    "etd": {"value": "2026-11-20", "quote": "ETD: 20/11/2026"},
                    "milestones": [
                        {
                            "name": {"value": "Đóng gói", "quote": "Đóng gói"},
                            "planned_date": {"value": "2026-11-10", "quote": "10/11/2026"},
                        }
                    ],
                },
                gaps=[],
            ),
        )
    )
    world.enable(MessagePurpose.PRODUCTION_PROGRESS)
    asyncio.run(world.lane().run())
    asyncio.run(world.lane().run())
    [message] = world.messages.rows
    assert message.case_id == case.id.value
    assert message.purpose is MessagePurpose.PRODUCTION_PROGRESS
    year, week, _ = NOW.date().isocalendar()
    assert message.source_key.endswith(f"{year}-W{week:02d}")
    [sent] = world.gateway.sent
    assert "Đóng gói: 2026-11-10" in sent.user and "ETD: 2026-11-20" in sent.user
    assert "4250" not in sent.user and "2.50" not in sent.user
    assert COMMERCIAL_WRITE not in sent.user
    assert CaseKind.PO is message.case_kind
