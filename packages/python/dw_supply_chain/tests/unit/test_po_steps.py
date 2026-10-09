"""Unit: steps 11 and 16 prepared by code, approved on the PO case page (ticket
ai-automation/15; ADR 0025, ADR 0026).

The domain (accounts compared by digest, amounts to the cent, invoice lines
matched by SKU) and the real `PreparePOSteps`, `GetPOStepProposal` and
`ApprovePOStep` over `testing.po_steps`: the deposit is the order total times
the PO's %, the balance the total less the deposit recorded; a beneficiary
account that is not the supplier's master account is a red finding that
blocks nothing; nothing of another tenant or workspace is read; an amount a
draft states that is not code's is refused; the paper the tenant requires is
on the case before a payment is confirmed; the payment is recorded in the
step's transaction; no finding and no notice carries an amount or an account.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from dw_kernel.errors import ConflictError, DomainError, NotFoundError, PermissionDeniedError
from dw_supply_chain.application.document_drafts import NewDocumentDraft
from dw_supply_chain.application.handlers import COMMERCIAL_WRITE, PO_CASE_READ
from dw_supply_chain.domain.case_document import CaseKind, DocumentType
from dw_supply_chain.domain.commercial import PaymentKind, account_digest
from dw_supply_chain.domain.document_draft import DocumentDraft, content_sha256
from dw_supply_chain.domain.extraction import (
    ACCOUNTS_FIELD,
    ExtractionStatus,
    account_numbers_in,
)
from dw_supply_chain.domain.payment_check import (
    AccountCheck,
    OrderedLine,
    PaymentFacts,
    SourceRead,
    check_accounts,
    line_findings,
)
from dw_supply_chain.domain.po_case import CaseState, POCase
from dw_supply_chain.domain.po_step import PO_STEPS, POStepKind, step_for_state
from dw_supply_chain.testing.po_steps import (
    MASTER_ACCOUNT,
    OTHER_ACCOUNT,
    PO_STEP_SCOPES,
    POStepWorld,
)
from dw_supply_chain.testing.purchase_orders import FINANCE, ORDERING

pytestmark = pytest.mark.unit

PI_MATCHING = {
    "invoice_number": "PI-2026-77",
    "currency": "USD",
    "total": "4250",
    "deposit_amount": "1275",
    "lines": [
        {"sku_code": "EL-00001-01", "quantity": "500", "unit_price": "2.50"},
        {"sku_code": "EL-00001-02", "quantity": "1200", "unit_price": "2.50"},
    ],
}
# Every number the world's PO computes to, and the accounts: none may leave the
# page or a notice.
SECRETS = ("4250", "1275", "2975", MASTER_ACCOUNT, OTHER_ACCOUNT)


# ------------------------------------------------------------------ domain --


def test_each_state_has_at_most_one_step_and_each_step_its_state() -> None:
    assert (
        step_for_state(CaseState.PO_CREATED, list(POStepKind))
        is PO_STEPS[POStepKind.DEPOSIT_REQUEST]
    )
    assert step_for_state(CaseState.PO_CREATED, []) is None
    assert step_for_state(CaseState.COMPLETED, list(POStepKind)) is None


def test_the_deposit_is_the_total_times_the_percent_and_the_balance_the_rest() -> None:
    facts = PaymentFacts(
        currency="USD",
        order_total=Decimal("4250.0000"),
        deposit_percent=Decimal("30"),
        deposit_paid=Decimal("1275"),
        master_digest=None,
    )
    assert facts.deposit_due == Decimal("1275.00")
    assert facts.balance_due == Decimal("2975.00")
    assert replace(facts, deposit_percent=None).deposit_due is None
    assert replace(facts, deposit_paid=None).balance_due is None


def test_an_account_is_found_after_its_keyword_and_a_bare_amount_is_not_one() -> None:
    text = (
        "Beneficiary: MINH PHAT\nSTK: 0071 000 123 456 tại Vietcombank\n"
        "Total amount 150000000 VND\nIBAN DE89370400440532013000"
    )
    found = account_numbers_in(text)
    assert "0071000123456" in found
    assert "DE89370400440532013000" in found
    assert "150000000" not in found


def _source(accounts: list[str]) -> SourceRead:
    return SourceRead(
        DocumentType.PROFORMA_INVOICE,
        document_id=uuid.uuid4(),
        status=ExtractionStatus.EXTRACTED,
        fields={ACCOUNTS_FIELD: [{"digest": account_digest(a)} for a in accounts]},
    )


def test_an_account_other_than_the_master_differs_whatever_else_matches() -> None:
    master = account_digest(MASTER_ACCOUNT)
    assert check_accounts(_source([MASTER_ACCOUNT]), master) is AccountCheck.MATCHES
    assert check_accounts(_source(["0071-000-123-456"]), master) is AccountCheck.MATCHES
    assert check_accounts(_source([MASTER_ACCOUNT, OTHER_ACCOUNT]), master) is (
        AccountCheck.DIFFERS
    )
    assert check_accounts(_source([]), master) is AccountCheck.NOT_STATED
    assert check_accounts(_source([MASTER_ACCOUNT]), None) is AccountCheck.NO_MASTER


def test_invoice_lines_are_matched_by_sku_quantity_and_price() -> None:
    ordered = [
        OrderedLine("EL-00001-01", 500, Decimal("2.50")),
        OrderedLine("EL-00001-02", 1200, Decimal("2.50")),
    ]
    source = SourceRead(
        DocumentType.COMMERCIAL_INVOICE,
        document_id=uuid.uuid4(),
        status=ExtractionStatus.EXTRACTED,
        fields={
            "lines": [
                {
                    "sku_code": {"value": "el-00001-01"},
                    "quantity": {"value": "450"},
                    "unit_price": {"value": "2.5"},
                },
                {"sku_code": {"value": "EL-99"}, "quantity": {"value": "10"}},
                {"description": {"value": "phụ kiện"}},
            ]
        },
    )
    found = {(f.code, f.subject) for f in line_findings(ordered, source)}
    assert ("line_quantity_differs", "EL-00001-01") in found
    assert ("line_not_on_po", "EL-99") in found
    assert ("line_missing", "EL-00001-02") in found
    assert ("line_unmatched", "lines[2]") in found
    assert not any(code == "line_price_differs" for code, _ in found)


# --------------------------------------------------------------- deposit --


def _texts(world: POStepWorld, *more: Any) -> str:
    # The words of each notice; ids are random and may spell any number.
    words = [f"{n.get('title', '')} {n.get('body', '')}" for n in world.notifier.sent]
    return json.dumps([words, *more], default=str, ensure_ascii=False)


async def _drafted(world: POStepWorld, case: POCase, doc_type: DocumentType) -> DocumentDraft:
    drafts = await world.drafts.latest_for_case(world.context(), CaseKind.PO, case.id.value)
    (draft,) = [d for d in drafts if d.doc_type is doc_type]
    return draft


def _values(draft: DocumentDraft) -> dict[str, Any]:
    return {k: v.get("value") for k, v in draft.fields.items() if isinstance(v, dict)}


async def test_the_deposit_request_is_drafted_by_code_once_and_cung_ung_is_told() -> None:
    world = POStepWorld()
    world.add_master_account()
    case = world.add_case(CaseState.PO_CREATED)
    world.add_document(case, DocumentType.PROFORMA_INVOICE, PI_MATCHING, accounts=[MASTER_ACCOUNT])

    assert (await world.lane().run()).drafted == 1
    assert (await world.lane().run()).drafted == 0

    values = _values(await _drafted(world, case, DocumentType.DEPOSIT_DOCS))
    assert values["order_total"] == "4250"
    assert values["deposit_percent"] == "30"
    assert values["amount"] == "1275"
    assert values["account_number"] == MASTER_ACCOUNT
    assert values["proforma_reference"] == "PI-2026-77"
    assert [n["recipients"] for n in world.notifier.sent] == [[world.buyer]]
    notices = _texts(world)
    assert not any(secret in notices for secret in SECRETS)


async def test_the_platform_drafts_nothing_and_another_tenants_case_is_not_drafted() -> None:
    world = POStepWorld()
    world.overrides.stored.pop("supply_chain_step_preparation")
    world.preparation = world.preparation.model_copy(update={"po_steps": ()})
    world.add_case(CaseState.PO_CREATED)
    assert (await world.lane().run()).drafted == 0

    other = POStepWorld()
    other.add_case(CaseState.PO_CREATED, tenant=uuid.uuid4())
    assert (await other.lane().run()).drafted == 0
    assert other.drafts.rows == []


async def test_a_matching_pi_is_proposed_with_nothing_found() -> None:
    world = POStepWorld()
    world.add_master_account()
    case = world.add_case(CaseState.PO_CREATED)
    world.add_document(case, DocumentType.PROFORMA_INVOICE, PI_MATCHING, accounts=[MASTER_ACCOUNT])
    await world.lane().run()

    page = await world.page().handle(world.context(), case.id)
    assert page.findings == ()
    assert page.proposed and page.can_approve


@pytest.mark.parametrize(
    ("change", "accounts", "code"),
    [
        ({"total": "4205"}, [MASTER_ACCOUNT], "amount_differs"),
        ({"deposit_amount": "1500"}, [MASTER_ACCOUNT], "amount_differs"),
        ({"currency": "EUR"}, [MASTER_ACCOUNT], "currency_differs"),
        ({}, [OTHER_ACCOUNT], "account_differs"),
        ({}, [], "account_not_stated"),
    ],
)
async def test_a_pi_that_does_not_match_is_named_and_still_decidable(
    change: dict[str, str], accounts: list[str], code: str
) -> None:
    world = POStepWorld()
    world.add_master_account()
    case = world.add_case(CaseState.PO_CREATED)
    world.add_document(
        case, DocumentType.PROFORMA_INVOICE, {**PI_MATCHING, **change}, accounts=accounts
    )
    await world.lane().run()

    page = await world.page().handle(world.context(), case.id)
    assert code in [f.code for f in page.findings]
    assert not page.proposed
    # A finding never blocks: a person decides, seeing it.
    assert page.can_approve
    words = " ".join(f.message for f in page.findings)
    assert not any(secret in words for secret in SECRETS)


async def test_a_pi_read_in_another_workspace_or_not_readable_is_not_used() -> None:
    world = POStepWorld()
    world.add_master_account()
    case = world.add_case(CaseState.PO_CREATED)
    world.add_document(
        case,
        DocumentType.PROFORMA_INVOICE,
        {**PI_MATCHING, "invoice_number": "PI-ELSEWHERE"},
        accounts=[OTHER_ACCOUNT],
        workspace=uuid.uuid4(),
    )
    await world.lane().run()
    values = _values(await _drafted(world, case, DocumentType.DEPOSIT_DOCS))
    assert "proforma_reference" not in values
    page = await world.page().handle(world.context(), case.id)
    assert "source_missing" in [f.code for f in page.findings]
    assert "account_differs" not in [f.code for f in page.findings]

    scan = POStepWorld()
    scan.add_master_account()
    scanned = scan.add_case(CaseState.PO_CREATED)
    scan.add_document(scanned, DocumentType.PROFORMA_INVOICE, status=ExtractionStatus.UNREADABLE)
    page = await scan.page().handle(scan.context(), scanned.id)
    (unread,) = [f for f in page.findings if f.code == "source_unread"]
    assert "OCR" in unread.message


async def test_no_master_account_leaves_the_beneficiary_empty_and_says_so() -> None:
    world = POStepWorld()
    case = world.add_case(CaseState.PO_CREATED)
    world.add_document(case, DocumentType.PROFORMA_INVOICE, PI_MATCHING, accounts=[OTHER_ACCOUNT])
    await world.lane().run()

    values = _values(await _drafted(world, case, DocumentType.DEPOSIT_DOCS))
    assert "account_number" not in values
    codes = [f.code for f in (await world.page().handle(world.context(), case.id)).findings]
    assert "beneficiary_missing" in codes and "account_no_master" in codes


async def _approve_request(
    world: POStepWorld,
    case: POCase,
    kind: POStepKind = POStepKind.DEPOSIT_REQUEST,
    *,
    scopes: frozenset[str] = PO_STEP_SCOPES,
    draft: DocumentDraft | None = None,
) -> POCase:
    spec = PO_STEPS[kind]
    assert spec.draft is not None
    draft = draft or await _drafted(world, case, spec.draft)
    return await world.approver().handle(
        world.context(scopes),
        case.id,
        kind=kind,
        draft_id=draft.id,
        content_sha256=draft.content_sha256,
        results={},
    )


async def test_cung_ung_approves_the_request_into_the_deposit_papers_and_ke_toan_is_told() -> None:
    world = POStepWorld()
    world.add_master_account()
    case = world.add_case(CaseState.PO_CREATED)
    world.add_document(case, DocumentType.PROFORMA_INVOICE, PI_MATCHING, accounts=[MASTER_ACCOUNT])
    await world.lane().run()
    world.notifier.sent.clear()

    moved = await _approve_request(world, case)

    assert moved.state is CaseState.WAITING_DEPOSIT
    assert world.case(case).state is CaseState.WAITING_DEPOSIT
    (paper,) = [d for d in world.documents.rows if d.doc_type is DocumentType.DEPOSIT_DOCS]
    assert paper.object_key in world.storage.objects
    assert [a.action for a in world.outcomes.audits][:1] == ["supply_chain.po_case.request_deposit"]
    assert [n["recipients"] for n in world.notifier.sent] == [[world.accountant]]
    assert not any(secret in _texts(world) for secret in SECRETS)


@pytest.mark.parametrize(
    ("scopes", "error"),
    [
        (frozenset({FINANCE, COMMERCIAL_WRITE, PO_CASE_READ}), PermissionDeniedError),
        (frozenset({ORDERING, PO_CASE_READ}), PermissionDeniedError),
    ],
)
async def test_the_request_needs_its_duty_and_the_price_write_scope(
    scopes: frozenset[str], error: type[Exception]
) -> None:
    world = POStepWorld()
    world.add_master_account()
    case = world.add_case(CaseState.PO_CREATED)
    await world.lane().run()

    with pytest.raises(error):
        await _approve_request(world, case, scopes=scopes)
    assert world.outcomes.applied == 0


def _edit(world: POStepWorld, draft: DocumentDraft, **values: str) -> DocumentDraft:
    fields = json.loads(json.dumps(draft.fields))
    for name, value in values.items():
        fields[name] = {"value": value, "source": {"edited_by": "test"}}
    return world.drafts.insert(
        world.context(),
        NewDocumentDraft(
            id=uuid.uuid4(),
            lineage_id=draft.lineage_id,
            version=draft.version + 1,
            case_kind=draft.case_kind,
            case_id=draft.case_id,
            doc_type=draft.doc_type,
            template_id=draft.template_id,
            template_version=draft.template_version,
            prompt_id=None,
            prompt_version=None,
            fields=fields,
            gaps=[],
            sources=[],
            content_sha256=content_sha256(
                draft.doc_type, f"{draft.template_id}@{draft.template_version}", fields
            ),
        ),
    )


async def test_an_amount_in_the_draft_that_is_not_codes_is_refused() -> None:
    world = POStepWorld()
    world.add_master_account()
    case = world.add_case(CaseState.PO_CREATED)
    await world.lane().run()
    edited = _edit(world, await _drafted(world, case, DocumentType.DEPOSIT_DOCS), amount="1.00")
    edited = await world.drafts.get(world.context(), edited.id) or edited

    page = await world.page().handle(world.context(), case.id)
    assert "total_differs" in [f.code for f in page.findings]
    with pytest.raises(DomainError):
        await _approve_request(world, case, draft=edited)
    assert world.case(case).state is CaseState.PO_CREATED


async def test_a_draft_seen_before_an_edit_or_another_tenants_case_is_refused() -> None:
    world = POStepWorld()
    world.add_master_account()
    case = world.add_case(CaseState.PO_CREATED)
    await world.lane().run()
    seen = await _drafted(world, case, DocumentType.DEPOSIT_DOCS)
    _edit(world, seen, notes="sửa")

    with pytest.raises(ConflictError):
        await _approve_request(world, case, draft=seen)
    with pytest.raises(NotFoundError):
        await world.approver().handle(
            world.context(tenant=uuid.uuid4()),
            case.id,
            kind=POStepKind.DEPOSIT_REQUEST,
            draft_id=seen.id,
            content_sha256=seen.content_sha256,
            results={},
        )
    assert world.outcomes.applied == 0


# --------------------------------------------------------- confirmations --


async def _waiting_deposit(world: POStepWorld, *, paper: bool = True) -> POCase:
    world.add_master_account()
    case = world.add_case(CaseState.WAITING_DEPOSIT)
    if paper:
        world.add_document(case, DocumentType.DEPOSIT_DOCS, status=None)
    return case


async def test_ke_toan_confirms_the_deposit_with_the_amount_typed_beside_the_unc() -> None:
    world = POStepWorld()
    case = await _waiting_deposit(world)
    world.add_document(
        case,
        DocumentType.BANK_TRANSFER_RECEIPT,
        {"amount": "1275", "currency": "USD", "transfer_date": "2026-10-11"},
        accounts=[MASTER_ACCOUNT],
    )

    page = await world.page().handle(world.context(), case.id)
    assert page.findings == () and page.proposed and page.can_approve
    suggested = {r.field.name: r.suggestion for r in page.results}
    assert suggested["paid_amount"] is not None and suggested["paid_amount"].value == "1275"
    assert suggested["paid_on"] is not None and suggested["paid_on"].value == "2026-10-11"

    # Without the price scope the amount AI read is not shown.
    hidden = await world.page().handle(world.context(frozenset({PO_CASE_READ})), case.id)
    (amount,) = [r for r in hidden.results if r.field.name == "paid_amount"]
    assert amount.suggestion is None and amount.redacted

    with pytest.raises(DomainError):
        await world.approver().handle(
            world.context(),
            case.id,
            kind=POStepKind.DEPOSIT_PAYMENT,
            draft_id=None,
            content_sha256=None,
            results={"paid_on": "2026-10-11"},
        )
    moved = await world.approver().handle(
        world.context(),
        case.id,
        kind=POStepKind.DEPOSIT_PAYMENT,
        draft_id=None,
        content_sha256=None,
        results={"paid_amount": "1275", "paid_on": "2026-10-11"},
    )
    assert moved.state is CaseState.DEPOSIT_CONFIRMED
    (payment,) = world.store.payments[case.id.value]
    assert payment.kind is PaymentKind.DEPOSIT and payment.amount == Decimal("1275")
    assert payment.paid_on == date(2026, 10, 11)
    (paper,) = [d for d in world.documents.rows if d.doc_type is DocumentType.DEPOSIT_DOCS]
    assert payment.document_id == paper.id.value
    assert "supply_chain.po_payment.recorded" in [a.action for a in world.outcomes.audits]
    assert not any(secret in _texts(world) for secret in SECRETS)


async def test_a_transfer_to_another_account_is_red_and_the_decision_stays_a_persons() -> None:
    world = POStepWorld()
    case = await _waiting_deposit(world)
    world.add_document(
        case,
        DocumentType.BANK_TRANSFER_RECEIPT,
        {"amount": "1200", "currency": "USD"},
        accounts=[OTHER_ACCOUNT],
    )

    page = await world.page().handle(world.context(), case.id)
    codes = [f.code for f in page.findings]
    assert "account_differs" in codes and "amount_differs" in codes
    assert not page.proposed and page.can_approve


async def test_the_deposit_is_not_confirmed_until_the_tenants_paper_is_on_the_case() -> None:
    world = POStepWorld()
    case = await _waiting_deposit(world, paper=False)

    page = await world.page().handle(world.context(), case.id)
    assert page.missing_paper is DocumentType.DEPOSIT_DOCS and not page.can_approve
    with pytest.raises(ConflictError):
        await world.approver().handle(
            world.context(),
            case.id,
            kind=POStepKind.DEPOSIT_PAYMENT,
            draft_id=None,
            content_sha256=None,
            results={"paid_amount": "1275", "paid_on": "2026-10-11"},
        )
    assert world.store.payments == {}


async def test_confirming_a_payment_needs_finance_and_the_price_write_scope() -> None:
    world = POStepWorld()
    case = await _waiting_deposit(world)
    for scopes in (
        frozenset({ORDERING, COMMERCIAL_WRITE, PO_CASE_READ}),
        frozenset({FINANCE, PO_CASE_READ}),
    ):
        with pytest.raises(PermissionDeniedError):
            await world.approver().handle(
                world.context(scopes),
                case.id,
                kind=POStepKind.DEPOSIT_PAYMENT,
                draft_id=None,
                content_sha256=None,
                results={"paid_amount": "1275", "paid_on": "2026-10-11"},
            )
    assert world.store.payments == {}


# ----------------------------------------------------------------- final --


INVOICE_MATCHING = {
    "invoice_number": "CI-2026-301",
    "currency": "USD",
    "total": "4250",
    "lines": PI_MATCHING["lines"],
}


async def test_the_final_request_is_the_total_less_the_deposit_paid_matched_line_by_line() -> None:
    world = POStepWorld()
    world.add_master_account()
    case = world.add_case(CaseState.ARRIVED_PORT)
    world.add_payment(case, PaymentKind.DEPOSIT, "1275")
    world.add_document(
        case,
        DocumentType.COMMERCIAL_INVOICE,
        {
            **INVOICE_MATCHING,
            "lines": [
                {"sku_code": "EL-00001-01", "quantity": "500", "unit_price": "2.60"},
                {"sku_code": "EL-00001-02", "quantity": "1100", "unit_price": "2.50"},
            ],
        },
        accounts=[MASTER_ACCOUNT],
    )
    await world.lane().run()

    values = _values(await _drafted(world, case, DocumentType.PAYMENT_DOCS))
    assert (values["order_total"], values["deposit_paid"], values["amount"]) == (
        "4250",
        "1275",
        "2975",
    )
    page = await world.page().handle(world.context(), case.id)
    found = {(f.code, f.subject) for f in page.findings}
    assert ("line_price_differs", "EL-00001-01") in found
    assert ("line_quantity_differs", "EL-00001-02") in found

    moved = await _approve_request(world, case, POStepKind.FINAL_PAYMENT_REQUEST)
    assert moved.state is CaseState.WAITING_PAYMENT


async def test_no_deposit_recorded_leaves_the_balance_empty_and_the_request_undecidable() -> None:
    world = POStepWorld()
    world.add_master_account()
    case = world.add_case(CaseState.ARRIVED_PORT)
    await world.lane().run()

    values = _values(await _drafted(world, case, DocumentType.PAYMENT_DOCS))
    assert "amount" not in values and "deposit_paid" not in values
    page = await world.page().handle(world.context(), case.id)
    assert "deposit_unrecorded" in [f.code for f in page.findings]
    with pytest.raises(DomainError):
        await _approve_request(world, case, POStepKind.FINAL_PAYMENT_REQUEST)


async def test_the_final_payment_is_recorded_against_the_balance() -> None:
    world = POStepWorld()
    world.add_master_account()
    case = world.add_case(CaseState.WAITING_PAYMENT)
    world.add_payment(case, PaymentKind.DEPOSIT, "1275")
    world.add_document(case, DocumentType.PAYMENT_DOCS, status=None)
    world.add_document(
        case,
        DocumentType.BANK_TRANSFER_RECEIPT,
        {"amount": "2975", "currency": "USD", "transfer_date": "2026-11-20"},
        accounts=[MASTER_ACCOUNT],
    )
    page = await world.page().handle(world.context(), case.id)
    assert page.findings == ()

    await world.approver().handle(
        world.context(),
        case.id,
        kind=POStepKind.FINAL_PAYMENT,
        draft_id=None,
        content_sha256=None,
        results={"paid_amount": "2975", "paid_on": "2026-11-20"},
    )
    final = [p for p in world.store.payments[case.id.value] if p.kind is PaymentKind.FINAL]
    assert [p.amount for p in final] == [Decimal("2975")]
    assert world.case(case).state is CaseState.PAYMENT_COMPLETED


async def test_a_step_the_tenant_did_not_turn_on_or_a_case_that_moved_is_refused() -> None:
    world = POStepWorld()
    case = await _waiting_deposit(world)
    world.store.cases[case.id.value] = replace(case, state=CaseState.DEPOSIT_CONFIRMED)
    with pytest.raises(ConflictError):
        await world.approver().handle(
            world.context(),
            case.id,
            kind=POStepKind.DEPOSIT_PAYMENT,
            draft_id=None,
            content_sha256=None,
            results={"paid_amount": "1275", "paid_on": "2026-10-11"},
        )

    off = POStepWorld()
    off.preparation = off.preparation.model_copy(update={"po_steps": ()})
    off.overrides.stored["supply_chain_step_preparation"] = off.preparation.model_dump(mode="json")
    other = await _waiting_deposit(off)
    page = await off.page().handle(off.context(), other.id)
    assert page.spec is None and not page.can_approve
    with pytest.raises(DomainError):
        await off.approver().handle(
            off.context(),
            other.id,
            kind=POStepKind.DEPOSIT_PAYMENT,
            draft_id=None,
            content_sha256=None,
            results={"paid_amount": "1275", "paid_on": "2026-10-11"},
        )


async def test_a_store_that_forgot_rls_still_cannot_lend_another_workspaces_paper() -> None:
    # The second layer: the sources keep only documents of the caller's own
    # tenant and workspace, whatever an adapter hands back.
    world = POStepWorld()
    world.documents.leaky = True
    world.readings.leaky = True
    world.add_master_account()
    case = world.add_case(CaseState.PO_CREATED)
    world.add_document(
        case,
        DocumentType.PROFORMA_INVOICE,
        PI_MATCHING,
        accounts=[OTHER_ACCOUNT],
        workspace=uuid.uuid4(),
    )
    page = await world.page().handle(world.context(), case.id)
    codes = [f.code for f in page.findings]
    assert "source_missing" in codes and "account_differs" not in codes
