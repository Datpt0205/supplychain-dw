"""Unit: commercial data and BM04 as fields (ADR 0026, E15; ticket ai-automation/01).

What is under test is the code's own decisions: the fixed sets, the totals code
computes, the BM04 schema a tenant may replace, and the handlers' scope rules —
a caller without `supply_chain.commercial.read` reads every price as redacted
(null, never 0), a caller without `.write` cannot set one, and a profile saved
by someone who may not touch prices keeps the prices it had. Fakes honour their
ports the way RLS does (another tenant's or workspace's rows are absent).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from dw_kernel.errors import DomainError, NotFoundError, PermissionDeniedError
from dw_kernel.ports import FixedClock, Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.application.commercial import (
    Bm04SchemaSource,
    GetPOCommercial,
    GetProductProfile,
    GetSupplierBankAccount,
    RecordPOPayment,
    SaveProductProfile,
    SaveSupplierBankAccount,
    SetBm04SchemaOverride,
    SetPOCommercialTerms,
)
from dw_supply_chain.application.handlers import (
    ACTION_DUTIES_WRITE,
    COMMERCIAL_READ,
    COMMERCIAL_WRITE,
    PO_CASE_READ,
    PRODUCT_CASE_READ,
    PRODUCT_CASE_WRITE,
)
from dw_supply_chain.bm04_schema import (
    BM04_SCHEMA_POLICY_ID,
    SupplyChainBm04Schema,
    load_supply_chain_bm04_schema,
)
from dw_supply_chain.domain.case_document import (
    CaseDocument,
    CaseDocumentId,
    CaseKind,
    DocumentType,
)
from dw_supply_chain.domain.commercial import (
    CURRENCY_CODES,
    CommercialTerms,
    Incoterm,
    NewPOPayment,
    NewProductProfile,
    NewSupplierBankAccount,
    PaymentKind,
    POCommercial,
    POPayment,
    PricedLine,
    ProductProfile,
    ProfileCommercial,
    SupplierBankAccount,
    currency_code,
    order_total,
    same_account,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[5]
SCHEMA = load_supply_chain_bm04_schema(
    REPO_ROOT / "configs" / "policies" / "supply_chain_bm04_schema@1.0.0.yaml"
)
TENANT, WORKSPACE, OTHER_TENANT, OTHER_WORKSPACE = (uuid.uuid4() for _ in range(4))
NOW = datetime(2026, 10, 9, 9, 0, tzinfo=UTC)


def _context(
    scopes: frozenset[str],
    *,
    tenant: uuid.UUID = TENANT,
    workspace: uuid.UUID = WORKSPACE,
) -> AccessContext:
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=scopes,
        plan_id="professional",
    )


# ------------------------------------------------------------------ domain --


def test_currency_is_an_iso_4217_price_currency() -> None:
    assert currency_code(" vnd ") == "VND"
    assert {"VND", "USD", "CNY", "EUR"} <= CURRENCY_CODES
    for bad in ("XAU", "XTS", "ABC", "", "US"):
        with pytest.raises(DomainError):
            currency_code(bad)


def test_deposit_percent_is_bounded_and_terms_are_trimmed() -> None:
    terms = CommercialTerms.of(
        currency="usd",
        incoterm=Incoterm.FOB,
        payment_terms="  30% cọc, 70% trước giao  ",
        deposit_percent="30",
        expected_delivery_date=date(2026, 12, 1),
    )
    assert terms.currency == "USD"
    assert terms.deposit_percent == Decimal("30")
    assert terms.payment_terms == "30% cọc, 70% trước giao"
    for bad in ("-1", "100.01"):
        with pytest.raises(DomainError):
            CommercialTerms.of(
                currency=None,
                incoterm=None,
                payment_terms=None,
                deposit_percent=bad,
                expected_delivery_date=None,
            )


def test_the_total_is_computed_and_unknown_while_a_line_lacks_a_price() -> None:
    sku_a, sku_b = uuid.uuid4(), uuid.uuid4()
    priced = (
        PricedLine(sku_a, 100, Decimal("2.50")),
        PricedLine(sku_b, 3, Decimal("10")),
    )
    assert order_total(priced) == Decimal("280.00")
    assert order_total((priced[0], PricedLine(sku_b, 3, None))) is None
    assert order_total((priced[0], PricedLine(sku_b, None, Decimal("1")))) is None
    assert order_total(()) is None


def test_a_payment_is_positive_with_two_decimals() -> None:
    for bad in ("0", "-5", "1.001"):
        with pytest.raises(DomainError):
            NewPOPayment.of(
                id=uuid.uuid4(),
                po_case_id=uuid.uuid4(),
                kind=PaymentKind.DEPOSIT,
                amount_value=bad,
                currency="VND",
                due_date=None,
                paid_on=None,
                document_id=None,
            )


def test_account_numbers_are_compared_by_code_after_normalisation() -> None:
    assert same_account("0071 000-123 456", "0071000123456")
    assert not same_account("0071000123456", "0071000123457")
    assert not same_account("", "")
    account = NewSupplierBankAccount.of(
        id=uuid.uuid4(),
        supplier_id=uuid.uuid4(),
        bank_name="Vietcombank",
        account_number="0071 000 123 456",
        account_holder="Công ty A",
    )
    assert account.account_number == "0071000123456"


def test_a_price_needs_its_currency() -> None:
    with pytest.raises(DomainError):
        ProfileCommercial.of(
            unit_price="1", currency=None, moq=None, lead_time_days=None, incoterm=None
        )


def test_a_writer_without_prices_keeps_the_prices_the_last_version_had() -> None:
    before = ProfileCommercial.of(
        unit_price="12.5", currency="USD", moq=500, lead_time_days=30, incoterm=Incoterm.FOB
    )
    planning = ProfileCommercial.of(
        unit_price=None, currency=None, moq=1000, lead_time_days=45, incoterm=Incoterm.CIF
    )
    kept = planning.with_prices_of(before)
    assert (kept.unit_price, kept.currency) == (Decimal("12.5"), "USD")
    assert (kept.moq, kept.lead_time_days, kept.incoterm) == (1000, 45, Incoterm.CIF)


# ------------------------------------------------------------- BM04 schema --


def test_the_neutral_schema_accepts_a_profile_and_names_every_wrong_field() -> None:
    clean = SCHEMA.check(
        {"product_name": " Nồi inox 24cm ", "net_weight_g": 1200.5, "food_contact": True}
    )
    assert clean == {"product_name": "Nồi inox 24cm", "net_weight_g": 1200.5, "food_contact": True}
    with pytest.raises(DomainError) as refused:
        SCHEMA.check({"net_weight_g": "1200", "units_per_carton": True, "secret": "x"})
    errors = {
        (e["field"], e["error"])
        for e in cast(list[dict[str, str]], refused.value.details["errors"])
    }
    assert errors == {
        ("product_name", "required"),
        ("net_weight_g", "not_number"),
        ("units_per_carton", "not_integer"),
        ("secret", "unknown_field"),
    }


def test_a_schema_cannot_declare_a_price_as_an_attribute() -> None:
    raw = SCHEMA.model_dump(mode="json")
    raw["fields"].append({"key": "unit_price", "label": "Giá", "kind": "number"})
    with pytest.raises(ValueError, match="typed column or a price"):
        SupplyChainBm04Schema.model_validate(raw)


# ---------------------------------------------------------------- fakes ---


class FakeCases:
    """`case_workspace` tenant-scoped only, so the handler's own workspace
    check is what is under test."""

    def __init__(self) -> None:
        self.cases: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID]] = {}

    def add(self, tenant: uuid.UUID = TENANT, workspace: uuid.UUID = WORKSPACE) -> uuid.UUID:
        case_id = uuid.uuid4()
        self.cases[case_id] = (tenant, workspace)
        return case_id

    async def case_workspace(self, context: AccessContext, case_id: uuid.UUID) -> uuid.UUID | None:
        found = self.cases.get(case_id)
        if found is None or found[0] != context.tenant_id:
            return None
        return found[1]


@dataclass
class FakeProfiles:
    rows: list[tuple[uuid.UUID, uuid.UUID, ProductProfile]] = field(default_factory=list)
    audits: list[AuditEvent] = field(default_factory=list)

    async def latest(self, context: AccessContext, case_id: uuid.UUID) -> ProductProfile | None:
        mine = [
            p
            for t, w, p in self.rows
            if (t, w) == (context.tenant_id, context.workspace_id)
            and p.product_dev_case_id == case_id
        ]
        return max(mine, key=lambda p: p.version, default=None)

    async def add(
        self, context: AccessContext, profile: NewProductProfile, *, audit: AuditEvent
    ) -> ProductProfile:
        latest = await self.latest(context, profile.product_dev_case_id)
        saved = ProductProfile(
            id=profile.id,
            product_dev_case_id=profile.product_dev_case_id,
            version=1 + (latest.version if latest else 0),
            commercial=profile.commercial,
            attributes=profile.attributes,
            schema_version=profile.schema_version,
            created_by=context.principal_id,
            created_at=NOW,
        )
        self.rows.append((context.tenant_id, context.workspace_id, saved))
        self.audits.append(audit)
        return saved


@dataclass
class FakeOverrides:
    by_tenant: dict[tuple[uuid.UUID, str], dict[str, object]] = field(default_factory=dict)
    audits: list[AuditEvent] = field(default_factory=list)

    async def get(self, context: AccessContext, policy_id: str) -> dict[str, object] | None:
        return self.by_tenant.get((context.tenant_id, policy_id))

    async def put(
        self,
        context: AccessContext,
        policy_id: str,
        content: Any,
        *,
        audit: AuditEvent,
    ) -> None:
        self.by_tenant[(context.tenant_id, policy_id)] = dict(content)
        self.audits.append(audit)


@dataclass
class FakeCommercial:
    lines: dict[uuid.UUID, tuple[PricedLine, ...]] = field(default_factory=dict)
    terms: dict[uuid.UUID, CommercialTerms] = field(default_factory=dict)
    payments: list[POPayment] = field(default_factory=list)
    audits: list[AuditEvent] = field(default_factory=list)

    async def read(self, context: AccessContext, case_id: uuid.UUID) -> POCommercial:
        return POCommercial(
            terms=self.terms.get(case_id, CommercialTerms()),
            lines=self.lines.get(case_id, ()),
            payments=tuple(p for p in self.payments if p.po_case_id == case_id),
        )

    async def set_terms(
        self,
        context: AccessContext,
        case_id: uuid.UUID,
        terms: CommercialTerms,
        line_prices: Mapping[uuid.UUID, Decimal | None],
        *,
        audit: AuditEvent,
    ) -> POCommercial:
        self.terms[case_id] = terms
        self.lines[case_id] = tuple(
            replace(line, unit_price=line_prices.get(line.sku_id, line.unit_price))
            for line in self.lines.get(case_id, ())
        )
        self.audits.append(audit)
        return await self.read(context, case_id)

    async def add_payment(
        self, context: AccessContext, payment: NewPOPayment, *, audit: AuditEvent
    ) -> POPayment:
        version = 1 + max(
            (
                p.version
                for p in self.payments
                if p.po_case_id == payment.po_case_id and p.kind is payment.kind
            ),
            default=0,
        )
        saved = POPayment(
            id=payment.id,
            po_case_id=payment.po_case_id,
            kind=payment.kind,
            version=version,
            amount=payment.amount,
            currency=payment.currency,
            due_date=payment.due_date,
            paid_on=payment.paid_on,
            document_id=payment.document_id,
            recorded_by=context.principal_id,
            recorded_at=NOW,
        )
        self.payments.append(saved)
        self.audits.append(audit)
        return saved


@dataclass
class FakeDocuments:
    rows: list[CaseDocument] = field(default_factory=list)

    async def get(self, context: AccessContext, document_id: CaseDocumentId) -> CaseDocument | None:
        for row in self.rows:
            if (
                row.id == document_id
                and row.tenant_id == context.tenant_id
                and row.workspace_id == context.workspace_id
            ):
                return row
        return None


def _document(case_id: uuid.UUID, doc_type: DocumentType) -> CaseDocument:
    return CaseDocument(
        id=CaseDocumentId(uuid.uuid4()),
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        case_kind=CaseKind.PO,
        case_id=case_id,
        doc_type=doc_type,
        object_key="k",
        filename="f.pdf",
        content_type="application/pdf",
        size_bytes=10,
        sha256="0" * 64,
        version=1,
        uploaded_by=uuid.uuid4(),
        uploaded_at=NOW,
    )


AUTHZ = ScopeAuthorizationService()
IDS = Uuid4Generator()
CLOCK = FixedClock(NOW)


def _schemas(overrides: FakeOverrides | None = None) -> Bm04SchemaSource:
    return Bm04SchemaSource(
        policy_override_repo=overrides or FakeOverrides(), platform_default=SCHEMA
    )


# ----------------------------------------------------------- BM04 profile --

_WRITER = frozenset({PRODUCT_CASE_READ, PRODUCT_CASE_WRITE})
_PRICED_WRITER = _WRITER | {COMMERCIAL_READ, COMMERCIAL_WRITE}


async def _save(
    profiles: FakeProfiles,
    cases: FakeCases,
    case_id: uuid.UUID,
    scopes: frozenset[str],
    *,
    prices: ProfileCommercial | None,
    overrides: FakeOverrides | None = None,
    context: AccessContext | None = None,
) -> ProductProfile:
    return await SaveProductProfile(
        cases=cases,
        profiles=profiles,
        schemas=_schemas(overrides),
        authz=AUTHZ,
        ids=IDS,
        clock=CLOCK,
    ).handle(
        context or _context(scopes),
        case_id,
        attributes={"product_name": "Nồi 24cm"},
        planning=ProfileCommercial.of(
            unit_price=None, currency=None, moq=500, lead_time_days=30, incoterm=Incoterm.FOB
        ),
        prices=prices,
    )


_PRICES = ProfileCommercial.of(
    unit_price="3.75", currency="USD", moq=None, lead_time_days=None, incoterm=None
)


async def test_a_reader_without_the_commercial_scope_reads_the_price_redacted_not_zero() -> None:
    cases, profiles = FakeCases(), FakeProfiles()
    case_id = cases.add()
    await _save(profiles, cases, case_id, _PRICED_WRITER, prices=_PRICES)

    get = GetProductProfile(cases=cases, profiles=profiles, schemas=_schemas(), authz=AUTHZ)
    blind = await get.handle(_context(frozenset({PRODUCT_CASE_READ})), case_id)
    assert blind.prices_visible is False
    assert blind.can_edit_prices is False
    assert blind.profile is not None
    # Redacted where it is read, not where it is shown: no price leaves the handler.
    assert blind.profile.commercial.unit_price is None
    assert blind.profile.commercial.moq == 500
    seeing = await get.handle(_context(frozenset({PRODUCT_CASE_READ, COMMERCIAL_READ})), case_id)
    assert seeing.prices_visible is True
    assert seeing.profile is not None
    assert seeing.profile.commercial.unit_price == Decimal("3.75")


async def test_setting_a_price_needs_the_commercial_write_scope() -> None:
    cases, profiles = FakeCases(), FakeProfiles()
    case_id = cases.add()
    with pytest.raises(PermissionDeniedError):
        await _save(profiles, cases, case_id, _WRITER | {COMMERCIAL_READ}, prices=_PRICES)
    assert profiles.rows == []


async def test_a_save_without_prices_carries_the_last_prices_forward() -> None:
    cases, profiles = FakeCases(), FakeProfiles()
    case_id = cases.add()
    await _save(profiles, cases, case_id, _PRICED_WRITER, prices=_PRICES)
    second = await _save(profiles, cases, case_id, _WRITER, prices=None)
    assert second.version == 2
    assert second.commercial.unit_price == Decimal("3.75")
    assert second.commercial.currency == "USD"
    assert second.commercial.moq == 500


async def test_attributes_against_the_tenants_own_schema_and_not_anothers() -> None:
    cases, profiles, overrides = FakeCases(), FakeProfiles(), FakeOverrides()
    case_id = cases.add()
    own = SCHEMA.model_dump(mode="json")
    own["fields"] = [{"key": "ma_khuon", "label": "Mã khuôn", "kind": "text", "required": True}]
    await SetBm04SchemaOverride(
        policy_override_repo=overrides, authz=AUTHZ, ids=IDS, clock=CLOCK
    ).handle(_context(frozenset({ACTION_DUTIES_WRITE})), SupplyChainBm04Schema.model_validate(own))
    assert (TENANT, BM04_SCHEMA_POLICY_ID) in overrides.by_tenant

    # Under the tenant's schema the platform's required product_name is unknown.
    with pytest.raises(DomainError) as refused:
        await _save(profiles, cases, case_id, _WRITER, prices=None, overrides=overrides)
    assert {e["field"] for e in cast(list[dict[str, str]], refused.value.details["errors"])} == {
        "ma_khuon",
        "product_name",
    }
    # Another tenant still has the platform's.
    other_case = cases.add(tenant=OTHER_TENANT)
    other = _context(_WRITER, tenant=OTHER_TENANT)
    saved = await _save(
        profiles, cases, other_case, _WRITER, prices=None, overrides=overrides, context=other
    )
    assert saved.attributes == {"product_name": "Nồi 24cm"}
    assert saved.schema_version == SCHEMA.policy_version


async def test_another_workspaces_or_tenants_case_reads_as_not_found() -> None:
    cases, profiles = FakeCases(), FakeProfiles()
    elsewhere = cases.add(workspace=OTHER_WORKSPACE)
    foreign = cases.add(tenant=OTHER_TENANT)
    get = GetProductProfile(cases=cases, profiles=profiles, schemas=_schemas(), authz=AUTHZ)
    for case_id in (elsewhere, foreign):
        with pytest.raises(NotFoundError):
            await get.handle(_context(frozenset({PRODUCT_CASE_READ})), case_id)
        with pytest.raises(NotFoundError):
            await _save(profiles, cases, case_id, _PRICED_WRITER, prices=_PRICES)
    assert profiles.rows == []


# ------------------------------------------------------- PO commercial ----


async def test_po_prices_terms_and_payments_are_redacted_without_the_read_scope() -> None:
    cases, commercial = FakeCases(), FakeCommercial()
    case_id = cases.add()
    sku = uuid.uuid4()
    commercial.lines[case_id] = (PricedLine(sku, 100, None),)
    writer = _context(frozenset({PO_CASE_READ, COMMERCIAL_READ, COMMERCIAL_WRITE}))
    await SetPOCommercialTerms(
        cases=cases, commercial=commercial, authz=AUTHZ, ids=IDS, clock=CLOCK
    ).handle(
        writer,
        case_id,
        terms=CommercialTerms.of(
            currency="USD",
            incoterm=Incoterm.FOB,
            payment_terms="30/70",
            deposit_percent=30,
            expected_delivery_date=None,
        ),
        line_prices={sku: Decimal("2.5")},
    )
    get = GetPOCommercial(cases=cases, commercial=commercial, authz=AUTHZ)
    seeing = await get.handle(writer, case_id)
    assert seeing.prices_visible and seeing.can_edit
    assert seeing.commercial.order_total == Decimal("250.0")
    blind = await get.handle(_context(frozenset({PO_CASE_READ})), case_id)
    assert not blind.prices_visible and not blind.can_edit
    assert [line.unit_price for line in blind.commercial.lines] == [None]
    assert blind.commercial.order_total is None
    assert blind.commercial.terms.deposit_percent is None
    assert blind.commercial.terms.payment_terms is None
    assert blind.commercial.terms.incoterm is Incoterm.FOB


async def test_setting_terms_needs_the_write_scope_and_names_only_this_cases_lines() -> None:
    cases, commercial = FakeCases(), FakeCommercial()
    case_id = cases.add()
    commercial.lines[case_id] = (PricedLine(uuid.uuid4(), 1, None),)
    set_terms = SetPOCommercialTerms(
        cases=cases, commercial=commercial, authz=AUTHZ, ids=IDS, clock=CLOCK
    )
    with pytest.raises(PermissionDeniedError):
        await set_terms.handle(
            _context(frozenset({PO_CASE_READ, COMMERCIAL_READ})),
            case_id,
            terms=CommercialTerms(),
            line_prices={},
        )
    with pytest.raises(DomainError):
        await set_terms.handle(
            _context(frozenset({COMMERCIAL_WRITE})),
            case_id,
            terms=CommercialTerms(currency="USD"),
            line_prices={uuid.uuid4(): Decimal("1")},
        )
    # A price without the case's currency is refused, not stored bare.
    with pytest.raises(DomainError):
        await set_terms.handle(
            _context(frozenset({COMMERCIAL_WRITE})),
            case_id,
            terms=CommercialTerms(),
            line_prices={commercial.lines[case_id][0].sku_id: Decimal("1")},
        )
    assert commercial.audits == []


async def test_a_payment_takes_only_this_cases_paper_of_its_kind() -> None:
    cases, commercial, documents = FakeCases(), FakeCommercial(), FakeDocuments()
    case_id, other_case = cases.add(), cases.add()
    deposit_paper = _document(case_id, DocumentType.DEPOSIT_DOCS)
    payment_paper = _document(case_id, DocumentType.PAYMENT_DOCS)
    foreign_paper = _document(other_case, DocumentType.DEPOSIT_DOCS)
    documents.rows += [deposit_paper, payment_paper, foreign_paper]
    record = RecordPOPayment(
        cases=cases, commercial=commercial, documents=documents, authz=AUTHZ, ids=IDS, clock=CLOCK
    )
    writer = _context(frozenset({COMMERCIAL_WRITE}))

    async def pay(document: CaseDocument | None) -> POPayment:
        return await record.handle(
            writer,
            case_id,
            kind=PaymentKind.DEPOSIT,
            amount_value="1500",
            currency="USD",
            due_date=date(2026, 11, 1),
            paid_on=None,
            document_id=None if document is None else document.id.value,
        )

    for wrong in (payment_paper, foreign_paper):
        with pytest.raises(DomainError):
            await pay(wrong)
    first = await pay(deposit_paper)
    second = await pay(None)
    assert (first.version, second.version) == (1, 2)
    with pytest.raises(PermissionDeniedError):
        await record.handle(
            _context(frozenset({PO_CASE_READ, COMMERCIAL_READ})),
            case_id,
            kind=PaymentKind.FINAL,
            amount_value="1",
            currency="USD",
            due_date=None,
            paid_on=None,
            document_id=None,
        )


# ------------------------------------------------------------ suppliers ---


@dataclass
class FakeBankAccounts:
    suppliers: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID]] = field(default_factory=dict)
    rows: list[SupplierBankAccount] = field(default_factory=list)
    audits: list[AuditEvent] = field(default_factory=list)

    async def supplier_workspace(
        self, context: AccessContext, supplier_id: uuid.UUID
    ) -> uuid.UUID | None:
        found = self.suppliers.get(supplier_id)
        if found is None or found[0] != context.tenant_id:
            return None
        return found[1]

    async def latest_bank_account(
        self, context: AccessContext, supplier_id: uuid.UUID
    ) -> SupplierBankAccount | None:
        mine = [r for r in self.rows if r.supplier_id == supplier_id]
        return max(mine, key=lambda r: r.version, default=None)

    async def add_bank_account(
        self, context: AccessContext, account: NewSupplierBankAccount, *, audit: AuditEvent
    ) -> SupplierBankAccount:
        latest = await self.latest_bank_account(context, account.supplier_id)
        saved = SupplierBankAccount(
            id=account.id,
            supplier_id=account.supplier_id,
            version=1 + (latest.version if latest else 0),
            bank_name=account.bank_name,
            account_number=account.account_number,
            account_holder=account.account_holder,
            created_by=context.principal_id,
            created_at=NOW,
        )
        self.rows.append(saved)
        self.audits.append(audit)
        return saved


async def test_a_bank_account_is_written_and_read_only_with_the_commercial_scope() -> None:
    accounts = FakeBankAccounts()
    supplier = uuid.uuid4()
    accounts.suppliers[supplier] = (TENANT, WORKSPACE)
    save = SaveSupplierBankAccount(accounts=accounts, authz=AUTHZ, ids=IDS, clock=CLOCK)
    with pytest.raises(PermissionDeniedError):
        await save.handle(
            _context(frozenset({PO_CASE_READ})),
            supplier,
            bank_name="VCB",
            account_number="0071000123456",
            account_holder="A",
        )
    await save.handle(
        _context(frozenset({COMMERCIAL_WRITE})),
        supplier,
        bank_name="VCB",
        account_number="0071000123456",
        account_holder="A",
    )
    # The audit names the version, never the number.
    assert "0071000123456" not in repr(accounts.audits[0].details)
    get = GetSupplierBankAccount(accounts=accounts, authz=AUTHZ)
    blind = await get.handle(_context(frozenset({PO_CASE_READ})), supplier)
    assert blind.prices_visible is False
    assert blind.account is None
    seeing = await get.handle(_context(frozenset({PO_CASE_READ, COMMERCIAL_READ})), supplier)
    assert seeing.account is not None and seeing.account.account_number == "0071000123456"
    with pytest.raises(NotFoundError):
        await get.handle(
            _context(frozenset({PO_CASE_READ, COMMERCIAL_READ}), workspace=OTHER_WORKSPACE),
            supplier,
        )


# ------------------------------------------------------------- the views --


async def test_the_api_shows_a_hidden_price_as_null_and_redacted_never_as_zero() -> None:
    from dw_supply_chain.presentation.commercial_routes import _commercial_view

    cases, commercial = FakeCases(), FakeCommercial()
    case_id = cases.add()
    sku = uuid.uuid4()
    commercial.lines[case_id] = (PricedLine(sku, 10, Decimal("0")),)
    commercial.terms[case_id] = CommercialTerms(currency="VND", deposit_percent=Decimal("0"))
    get = GetPOCommercial(cases=cases, commercial=commercial, authz=AUTHZ)

    blind = _commercial_view(
        case_id, await get.handle(_context(frozenset({PO_CASE_READ})), case_id)
    ).model_dump(mode="json")
    assert blind["lines"][0]["unit_price"] == {"value": None, "redacted": True}
    assert blind["order_total"] == {"value": None, "redacted": True}
    assert blind["deposit_percent"] == {"value": None, "redacted": True}
    assert blind["payment_terms_redacted"] is True
    assert blind["currency"] == "VND"

    # A real price of 0 is shown as "0", and unredacted.
    seeing = _commercial_view(
        case_id, await get.handle(_context(frozenset({PO_CASE_READ, COMMERCIAL_READ})), case_id)
    ).model_dump(mode="json")
    assert seeing["lines"][0]["unit_price"] == {"value": "0", "redacted": False}
    assert seeing["order_total"] == {"value": "0", "redacted": False}
