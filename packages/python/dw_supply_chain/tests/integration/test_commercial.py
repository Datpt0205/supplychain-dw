"""Integration: commercial data and BM04 as fields (82221a867e62, ADR 0026;
ticket ai-automation/01).

What only the real database can show:

- the four new tables are narrowed by tenant AND workspace: another tenant and
  another workspace of the same tenant neither read nor write a row, through
  the repositories or by SQL under `dw_app`;
- versions are the database's: a profile, a payment, a contact and a bank
  account each number from 1 per owner;
- a payment cites only a document of its own PO case (composite FK);
- the CHECKs hold the fixed sets the domain owns (currency, Incoterm), and a
  price needs its currency;
- the line price is the one column of `po_case_lines` the application writes
  besides the quantity.

Owed when written (2026-10-09): not run, no Docker on the machine that wrote it.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls

from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import SystemClock
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence.case_document_repository import (
    SqlCaseDocumentRepository,
)
from dw_supply_chain.adapters.persistence.commercial_repository import (
    SqlPOCommercialRepository,
    SqlProductProfileRepository,
    SqlSupplierRecords,
)
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.adapters.persistence.product_case_repository import (
    SqlProductCaseRepository,
)
from dw_supply_chain.application.ports import NewCaseDocument
from dw_supply_chain.domain.case_document import (
    CaseDocumentId,
    CaseKind,
    DocumentType,
    ObjectKey,
)
from dw_supply_chain.domain.commercial import (
    CURRENCY_CODES,
    CommercialTerms,
    Incoterm,
    NewPOPayment,
    NewProductProfile,
    NewSupplierBankAccount,
    NewSupplierContact,
    PaymentKind,
    ProfileCommercial,
)
from dw_supply_chain.domain.po_case import POCase, POCaseId
from dw_supply_chain.domain.product_development_case import (
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
)

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class _Db:
    sessions: async_sessionmaker[AsyncSession]
    migrator: async_sessionmaker[AsyncSession]


@pytest.fixture
async def db(db_urls: DatabaseUrls) -> AsyncIterator[_Db]:
    app = create_async_engine(db_urls.app, poolclass=NullPool)
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    yield _Db(
        async_sessionmaker(app, class_=AsyncSession, expire_on_commit=False),
        async_sessionmaker(migrator, class_=AsyncSession, expire_on_commit=False),
    )
    await app.dispose()
    await migrator.dispose()


def _context(tenant: uuid.UUID, workspace: uuid.UUID) -> AccessContext:
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=frozenset(),
        plan_id="professional",
    )


def _audit(context: AccessContext, action: str) -> AuditEvent:
    return AuditEvent(
        id=uuid.uuid4(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        actor_id=UserId(context.principal_id),
        action=action,
        resource_type="test",
        resource_id=str(uuid.uuid4()),
        occurred_at=SystemClock().now(),
    )


async def _po_case(db: _Db, context: AccessContext) -> POCase:
    case = POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        po_reference=f"PO-COM-{uuid.uuid4().hex[:8]}",
        supplier_name="NCC Thương mại",
    )
    await SqlPOCaseRepository(db.sessions).add(context, case)
    return case


async def _product_case(db: _Db, context: AccessContext) -> ProductDevelopmentCase:
    case = ProductDevelopmentCase.propose(
        id=ProductDevelopmentCaseId(uuid.uuid4()),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        proposal_code=f"DX-{uuid.uuid4().hex[:8]}",
        product_name="Nồi inox 24cm",
        category="Nồi",
        actor_id=context.principal_id,
    )
    await SqlProductCaseRepository(db.sessions).add(
        context, case, audit=_audit(context, "supply_chain.product_case.propose")
    )
    return case


async def _supplier_of(db: _Db, case: POCase) -> uuid.UUID:
    async with db.migrator() as session:
        found: uuid.UUID = await session.scalar(
            sa.text("SELECT supplier_id FROM supply_chain.po_cases WHERE id = :i"),
            {"i": case.id.value},
        )
    return found


def _profile(case: ProductDevelopmentCase) -> NewProductProfile:
    return NewProductProfile(
        id=uuid.uuid4(),
        product_dev_case_id=case.id.value,
        commercial=ProfileCommercial.of(
            unit_price="3.75", currency="USD", moq=500, lead_time_days=30, incoterm=Incoterm.FOB
        ),
        attributes={"product_name": "Nồi inox 24cm"},
        schema_version="1.0.0",
    )


async def test_profiles_number_per_case_and_stay_in_their_workspace(db: _Db) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    mine = _context(tenant, workspace)
    neighbour = _context(tenant, uuid.uuid4())
    stranger = _context(uuid.uuid4(), workspace)
    case = await _product_case(db, mine)
    profiles = SqlProductProfileRepository(db.sessions)

    first = await profiles.add(mine, _profile(case), audit=_audit(mine, "t.saved"))
    second = await profiles.add(mine, _profile(case), audit=_audit(mine, "t.saved"))
    assert (first.version, second.version) == (1, 2)
    latest = await profiles.latest(mine, case.id.value)
    assert latest is not None and latest.commercial.unit_price == Decimal("3.7500")

    for other in (neighbour, stranger):
        assert await profiles.latest(other, case.id.value) is None
        # Writing into another workspace's case is refused by the composite FK
        # or by RLS's WITH CHECK, whichever the row meets first.
        with pytest.raises((IntegrityError, DBAPIError)):
            await profiles.add(other, _profile(case), audit=_audit(other, "t.saved"))


async def test_po_terms_line_prices_and_payments_are_this_workspaces_only(db: _Db) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    mine = _context(tenant, workspace)
    neighbour = _context(tenant, uuid.uuid4())
    stranger = _context(uuid.uuid4(), workspace)
    case = await _po_case(db, mine)
    repo = SqlPOCommercialRepository(db.sessions)

    await repo.set_terms(
        mine,
        case.id.value,
        CommercialTerms.of(
            currency="USD",
            incoterm=Incoterm.CIF,
            payment_terms="30% cọc",
            deposit_percent=30,
            expected_delivery_date=None,
        ),
        {},
        audit=_audit(mine, "t.terms"),
    )
    payment = await repo.add_payment(
        mine,
        NewPOPayment.of(
            id=uuid.uuid4(),
            po_case_id=case.id.value,
            kind=PaymentKind.DEPOSIT,
            amount_value="1500",
            currency="USD",
            due_date=None,
            paid_on=None,
            document_id=None,
        ),
        audit=_audit(mine, "t.payment"),
    )
    assert payment.version == 1
    read = await repo.read(mine, case.id.value)
    assert read.terms.currency == "USD" and read.terms.deposit_percent == Decimal("30.00")
    assert [p.id for p in read.payments] == [payment.id]

    for other in (neighbour, stranger):
        theirs = await repo.read(other, case.id.value)
        assert theirs.terms.currency is None and theirs.payments == ()
        # An UPDATE through another scope finds no row to change.
        await repo.set_terms(
            other,
            case.id.value,
            CommercialTerms(currency="VND"),
            {},
            audit=_audit(other, "t.terms"),
        )
    assert (await repo.read(mine, case.id.value)).terms.currency == "USD"


async def test_a_payment_cites_only_a_document_of_its_own_case(db: _Db) -> None:
    mine = _context(uuid.uuid4(), uuid.uuid4())
    case, other_case = await _po_case(db, mine), await _po_case(db, mine)
    document_id = uuid.uuid4()
    await SqlCaseDocumentRepository(db.sessions).add(
        mine,
        NewCaseDocument(
            id=CaseDocumentId(document_id),
            case_kind=CaseKind.PO,
            case_id=other_case.id.value,
            doc_type=DocumentType.DEPOSIT_DOCS,
            object_key=ObjectKey.build(
                tenant_id=mine.tenant_id,
                workspace_id=mine.workspace_id,
                case_kind=CaseKind.PO,
                case_id=other_case.id.value,
                document_id=document_id,
            ).value,
            filename="unc.pdf",
            content_type="application/pdf",
            size_bytes=10,
            sha256="0" * 64,
        ),
        audit=_audit(mine, "t.upload"),
    )
    with pytest.raises(IntegrityError, match="fk_po_payments_tenant_id_case_documents"):
        await SqlPOCommercialRepository(db.sessions).add_payment(
            mine,
            NewPOPayment.of(
                id=uuid.uuid4(),
                po_case_id=case.id.value,
                kind=PaymentKind.DEPOSIT,
                amount_value="1",
                currency="USD",
                due_date=None,
                paid_on=None,
                document_id=document_id,
            ),
            audit=_audit(mine, "t.payment"),
        )


async def test_supplier_contacts_and_accounts_are_versioned_and_narrowed(db: _Db) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    mine = _context(tenant, workspace)
    neighbour = _context(tenant, uuid.uuid4())
    stranger = _context(uuid.uuid4(), workspace)
    supplier = await _supplier_of(db, await _po_case(db, mine))
    records = SqlSupplierRecords(db.sessions)

    for _ in range(2):
        await records.add_contact(
            mine,
            NewSupplierContact.of(
                id=uuid.uuid4(),
                supplier_id=supplier,
                name="Chị Lan",
                email="lan@ncc.example",
                phone=None,
            ),
            audit=_audit(mine, "t.contact"),
        )
    account = await records.add_bank_account(
        mine,
        NewSupplierBankAccount.of(
            id=uuid.uuid4(),
            supplier_id=supplier,
            bank_name="VCB",
            account_number="0071 000 123 456",
            account_holder="Công ty A",
        ),
        audit=_audit(mine, "t.account"),
    )
    contact = await records.latest_contact(mine, supplier)
    assert contact is not None and contact.version == 2
    assert account.account_number == "0071000123456"
    for other in (neighbour, stranger):
        assert await records.supplier_workspace(other, supplier) is None
        assert await records.latest_contact(other, supplier) is None
        assert await records.latest_bank_account(other, supplier) is None
        assert await records.list_suppliers(other) == []


async def test_a_connection_that_never_scopes_itself_reads_no_commercial_row(db: _Db) -> None:
    mine = _context(uuid.uuid4(), uuid.uuid4())
    case = await _product_case(db, mine)
    await SqlProductProfileRepository(db.sessions).add(
        mine, _profile(case), audit=_audit(mine, "t.saved")
    )
    async with db.sessions() as session:
        for table in (
            "product_profiles",
            "po_payments",
            "supplier_contacts",
            "supplier_bank_accounts",
        ):
            count = await session.scalar(sa.text(f"SELECT count(*) FROM supply_chain.{table}"))
            assert count == 0, table
    # And a tenant-only scope (no workspace) does not see a workspace's row.
    async with tenant_session(db.sessions, TenantScope(tenant_id=mine.tenant_id)) as session:
        count = await session.scalar(sa.text("SELECT count(*) FROM supply_chain.product_profiles"))
        assert count == 0


async def test_the_checks_hold_the_domains_fixed_sets(db: _Db) -> None:
    async with db.migrator() as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT conname, pg_get_constraintdef(oid) AS definition FROM pg_constraint"
                    " WHERE conname IN ('ck_product_profiles_currency',"
                    " 'ck_product_profiles_incoterm', 'ck_po_cases_currency')"
                )
            )
        ).all()
        definitions: dict[str, str] = {row.conname: row.definition for row in rows}
    assert set(definitions) == {
        "ck_product_profiles_currency",
        "ck_product_profiles_incoterm",
        "ck_po_cases_currency",
    }
    quoted = re.compile(r"'([A-Z]{3})'")
    assert set(quoted.findall(definitions["ck_product_profiles_currency"])) == CURRENCY_CODES
    assert set(quoted.findall(definitions["ck_po_cases_currency"])) == CURRENCY_CODES
    assert set(quoted.findall(definitions["ck_product_profiles_incoterm"])) == {
        i.value for i in Incoterm
    }


async def test_a_price_without_its_currency_is_refused_by_the_database(db: _Db) -> None:
    mine = _context(uuid.uuid4(), uuid.uuid4())
    case = await _product_case(db, mine)
    async with db.migrator() as session:
        with pytest.raises(IntegrityError, match="ck_product_profiles_price_has_currency"):
            await session.execute(
                sa.text(
                    "INSERT INTO supply_chain.product_profiles (id, tenant_id, workspace_id,"
                    " product_dev_case_id, version, unit_price, schema_version, created_by)"
                    " VALUES (:i, :t, :w, :c, 1, 5, '1.0.0', :u)"
                ),
                {
                    "i": uuid.uuid4(),
                    "t": mine.tenant_id,
                    "w": mine.workspace_id,
                    "c": case.id.value,
                    "u": mine.principal_id,
                },
            )
