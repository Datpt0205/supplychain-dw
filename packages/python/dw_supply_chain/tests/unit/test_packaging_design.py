"""Step 12's sub-flow and step 13's gate, in the domain (slice PK)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from dw_kernel.errors import ConflictError, DomainError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_supply_chain.domain.case_document import (
    CaseDocument,
    CaseDocumentId,
    CaseKind,
    DocumentType,
)
from dw_supply_chain.domain.packaging_design import (
    MKT_LEAD_NOTE,
    PackagingAction,
    PackagingDesign,
    PreProductionTest,
    ProductionGate,
    ReviewStatus,
)
from dw_supply_chain.domain.po_case import (
    CaseAction,
    CaseState,
    POCase,
    POCaseId,
    apply_action,
)
from dw_supply_chain.packaging_policy import load_supply_chain_packaging_policy

pytestmark = pytest.mark.unit

TENANT = uuid.uuid4()
WORKSPACE = uuid.uuid4()
CASE = uuid.uuid4()
T0 = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)
A = PackagingAction


def design() -> PackagingDesign:
    return PackagingDesign(
        po_case_id=CASE, tenant_id=TenantId(TENANT), workspace_id=WorkspaceId(WORKSPACE)
    )


def report(
    *,
    case_id: uuid.UUID = CASE,
    tenant: uuid.UUID = TENANT,
    workspace: uuid.UUID = WORKSPACE,
    kind: CaseKind = CaseKind.PO,
    doc_type: DocumentType = DocumentType.PRE_PRODUCTION_TEST_REPORT,
    at: datetime = T0 + timedelta(hours=2),
) -> CaseDocument:
    return CaseDocument(
        id=CaseDocumentId(uuid.uuid4()),
        tenant_id=tenant,
        workspace_id=workspace,
        case_kind=kind,
        case_id=case_id,
        doc_type=doc_type,
        object_key="k",
        filename="bien-ban.pdf",
        content_type="application/pdf",
        size_bytes=10,
        sha256="0" * 64,
        version=1,
        uploaded_by=uuid.uuid4(),
        uploaded_at=at,
    )


def take(
    d: PackagingDesign,
    action: PackagingAction,
    *,
    reason: str | None = None,
    document: CaseDocument | None = None,
    in_pre_production: bool = True,
    now: datetime = T0 + timedelta(hours=1),
) -> None:
    d.take(
        action,
        case_in_pre_production=in_pre_production,
        reason=reason,
        document=document,
        now=now,
    )


def to_sample(d: PackagingDesign) -> None:
    take(d, A.APPROVE_COLOUR)
    take(d, A.APPROVE_DESIGN)
    take(d, A.RECEIVE_PRE_PRODUCTION_SAMPLE)


def test_the_sub_flow_in_the_diagrams_order_with_the_mkt_note_on_the_colour() -> None:
    d = design()
    take(d, A.REQUEST_COLOUR_REVISION, reason="màu lệch")
    take(d, A.APPROVE_COLOUR)
    take(d, A.REQUEST_DESIGN_REVISION, reason="sai logo")
    take(d, A.APPROVE_DESIGN)
    take(d, A.RECEIVE_PRE_PRODUCTION_SAMPLE)
    take(d, A.FAIL_PRE_PRODUCTION_TEST, reason="bong tróc", document=report())
    take(d, A.PASS_PRE_PRODUCTION_TEST, document=report())

    assert (d.colour_status, d.design_status) == (ReviewStatus.APPROVED, ReviewStatus.APPROVED)
    assert d.pre_production_test is PreProductionTest.PASSED
    assert d.version == 7
    events = d.pop_pending_events()
    assert [e.action for e in events] == [
        A.REQUEST_COLOUR_REVISION,
        A.APPROVE_COLOUR,
        A.REQUEST_DESIGN_REVISION,
        A.APPROVE_DESIGN,
        A.RECEIVE_PRE_PRODUCTION_SAMPLE,
        A.FAIL_PRE_PRODUCTION_TEST,
        A.PASS_PRE_PRODUCTION_TEST,
    ]
    assert [e.note for e in events if e.note] == [MKT_LEAD_NOTE]
    assert events[1].action is A.APPROVE_COLOUR and events[1].note == MKT_LEAD_NOTE
    assert all(
        (e.document_id is not None)
        == (e.action in {A.PASS_PRE_PRODUCTION_TEST, A.FAIL_PRE_PRODUCTION_TEST})
        for e in events
    )


@pytest.mark.parametrize("action", list(PackagingAction))
def test_no_step_outside_pre_production(action: PackagingAction) -> None:
    d = design()
    with pytest.raises(ConflictError):
        take(d, action, reason="x", document=report(), in_pre_production=False)
    assert d.version == 0 and d.pop_pending_events() == []


@pytest.mark.parametrize(
    ("setup", "action"),
    [
        ([], A.APPROVE_DESIGN),
        ([], A.REQUEST_DESIGN_REVISION),
        ([A.APPROVE_COLOUR], A.RECEIVE_PRE_PRODUCTION_SAMPLE),
        ([A.APPROVE_COLOUR, A.APPROVE_DESIGN], A.PASS_PRE_PRODUCTION_TEST),
        ([A.APPROVE_COLOUR], A.APPROVE_COLOUR),
        ([A.APPROVE_COLOUR], A.REQUEST_COLOUR_REVISION),
        ([A.APPROVE_COLOUR, A.APPROVE_DESIGN], A.REQUEST_DESIGN_REVISION),
        (
            [A.APPROVE_COLOUR, A.APPROVE_DESIGN, A.RECEIVE_PRE_PRODUCTION_SAMPLE],
            A.RECEIVE_PRE_PRODUCTION_SAMPLE,
        ),
    ],
    ids=[
        "design-before-colour",
        "design-revision-before-colour",
        "sample-before-design",
        "test-before-sample",
        "colour-twice",
        "revise-approved-colour",
        "revise-approved-design",
        "sample-twice",
    ],
)
def test_out_of_order_or_after_approval_is_refused(
    setup: list[PackagingAction], action: PackagingAction
) -> None:
    d = design()
    for step in setup:
        take(d, step)
    version = d.version
    paper = report() if action is A.PASS_PRE_PRODUCTION_TEST else None
    with pytest.raises(ConflictError) as raised:
        take(d, action, reason="x", document=paper)
    assert "missing_document_type" not in raised.value.details
    assert d.version == version


def test_a_passed_test_is_final() -> None:
    d = design()
    to_sample(d)
    take(d, A.PASS_PRE_PRODUCTION_TEST, document=report())
    with pytest.raises(ConflictError):
        take(d, A.FAIL_PRE_PRODUCTION_TEST, reason="x", document=report())
    assert d.pre_production_test is PreProductionTest.PASSED


@pytest.mark.parametrize(
    "action", [A.REQUEST_COLOUR_REVISION, A.REQUEST_DESIGN_REVISION, A.FAIL_PRE_PRODUCTION_TEST]
)
@pytest.mark.parametrize("reason", [None, "", "   "])
def test_sending_back_or_failing_says_why(action: PackagingAction, reason: str | None) -> None:
    d = design()
    if action is not A.REQUEST_COLOUR_REVISION:
        take(d, A.APPROVE_COLOUR)
    if action is A.FAIL_PRE_PRODUCTION_TEST:
        take(d, A.APPROVE_DESIGN)
        take(d, A.RECEIVE_PRE_PRODUCTION_SAMPLE)
    with pytest.raises(DomainError, match="requires a reason"):
        take(d, action, reason=reason, document=report())


@pytest.mark.parametrize(
    "paper",
    [
        None,
        report(case_id=uuid.uuid4()),
        report(tenant=uuid.uuid4()),
        report(workspace=uuid.uuid4()),
        report(kind=CaseKind.PRODUCT),
        report(doc_type=DocumentType.PACKAGING_DESIGN),
        report(at=T0),
    ],
    ids=[
        "none",
        "another-case",
        "another-tenant",
        "another-workspace",
        "a-product-case",
        "wrong-type",
        "before-the-sample",
    ],
)
@pytest.mark.parametrize("action", [A.PASS_PRE_PRODUCTION_TEST, A.FAIL_PRE_PRODUCTION_TEST])
def test_a_test_needs_this_cases_report_since_the_sample(
    paper: CaseDocument | None, action: PackagingAction
) -> None:
    d = design()
    to_sample(d)
    with pytest.raises(ConflictError) as raised:
        take(d, action, reason="x", document=paper)
    assert raised.value.details["missing_document_type"] == "pre_production_test_report"
    assert d.pre_production_test is PreProductionTest.PENDING


def test_a_step_without_paper_refuses_one() -> None:
    d = design()
    with pytest.raises(DomainError, match="takes no document"):
        take(d, A.APPROVE_COLOUR, document=report())


# ---- step 13's gate ----------------------------------------------------------


def pre_production_case() -> POCase:
    case = POCase(
        id=POCaseId(CASE),
        tenant_id=TenantId(TENANT),
        workspace_id=WorkspaceId(WORKSPACE),
        po_reference="PO-1",
        supplier_name="NCC",
    )
    case.request_deposit()
    case.confirm_deposit()
    case.start_pre_production()
    return case


@pytest.mark.parametrize("test", [PreProductionTest.PENDING, PreProductionTest.FAILED])
def test_a_required_test_not_passed_keeps_the_case_out_of_production(
    test: PreProductionTest,
) -> None:
    case = pre_production_case()
    with pytest.raises(ConflictError) as raised:
        apply_action(
            case,
            action=CaseAction.START_PRODUCTION,
            reason=None,
            gate=ProductionGate(required=True, test=test),
        )
    assert raised.value.details["reason"] == "pre_production_test_not_passed"
    assert case.state is CaseState.PRE_PRODUCTION


@pytest.mark.parametrize(
    "gate",
    [
        ProductionGate(required=True, test=PreProductionTest.PASSED),
        ProductionGate(required=False, test=PreProductionTest.PENDING),
        ProductionGate(required=False, test=PreProductionTest.FAILED),
    ],
    ids=["required-passed", "not-required", "not-required-failed"],
)
def test_production_opens_on_a_passed_test_or_when_not_required(gate: ProductionGate) -> None:
    case = pre_production_case()
    apply_action(case, action=CaseAction.START_PRODUCTION, reason=None, gate=gate)
    assert case.state is CaseState.PRODUCTION


def test_without_a_gate_start_production_is_refused_never_let_through() -> None:
    case = pre_production_case()
    with pytest.raises(DomainError, match="production gate"):
        apply_action(case, action=CaseAction.START_PRODUCTION, reason=None)
    assert case.state is CaseState.PRE_PRODUCTION


def test_the_platform_rule_is_off_and_elmichs_override_turns_it_on() -> None:
    """The platform default changes nothing for any existing case; Elmich's
    override (written by `seed_supply_chain_demo.py elmich-packaging`) requires
    the test."""
    root = Path(__file__).resolve().parents[5]
    platform = load_supply_chain_packaging_policy(
        root / "configs" / "policies" / "supply_chain_packaging@1.0.0.yaml"
    )
    elmich = load_supply_chain_packaging_policy(root / "scripts" / "elmich_packaging_override.yaml")
    assert platform.require_pre_production_test is False
    assert elmich.require_pre_production_test is True
