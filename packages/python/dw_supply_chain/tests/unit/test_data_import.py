"""Unit: the one-time import (ADR 0027, E16; ticket onboarding/01).

What is under test is the import's own decisions over fakes that keep their
ports' promises (a reader sees only its own tenant AND workspace, a UNIQUE
refuses what the database would): a dry run writes nothing, a second run of
the same file adds nothing, a bad row is refused by number without spoiling
the others, a bank account is compared by code and never echoed, each part of
a row needs its own scope, and nothing outside the caller's tenant or
workspace is read or written. The workbook adapter runs for real.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from dw_kernel.errors import (
    ConflictError,
    DomainError,
    PayloadTooLargeError,
    PermissionDeniedError,
)
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import FixedClock, Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.import_workbook import XlsxWorkbookReader, build_template
from dw_supply_chain.application.commercial import SaveSupplierBankAccount, SaveSupplierContact
from dw_supply_chain.application.data_import import (
    PO_CASE_IMPORTED,
    PRODUCT_CASE_IMPORTED,
    SUPPLY_CHAIN_IMPORT,
    CatalogueItem,
    GetImportTemplate,
    ImportSupplyChainData,
    KnownProductCase,
    KnownSupplier,
    NewCatalogueItem,
    WorkspaceRef,
)
from dw_supply_chain.application.handlers import (
    COMMERCIAL_WRITE,
    PO_CASE_WRITE,
    PRODUCT_CASE_WRITE,
)
from dw_supply_chain.domain.case_import import IMPORT_REASON
from dw_supply_chain.domain.commercial import (
    NewSupplierBankAccount,
    NewSupplierContact,
    SupplierBankAccount,
    SupplierContact,
)
from dw_supply_chain.domain.data_import import (
    MAX_ROWS,
    SHEET_BY_KIND,
    SHEETS,
    ImportReport,
    ImportSheet,
    RowStatus,
    parse_sheet,
)
from dw_supply_chain.domain.po_case import CaseState, POCase
from dw_supply_chain.domain.product_development_case import (
    ProductAction,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
)
from dw_supply_chain.sla_policy import ProductCategory

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[5]
NOW = datetime(2026, 10, 9, 9, 0, tzinfo=UTC)
TENANT, WORKSPACE = uuid.uuid4(), uuid.uuid4()
SUPPLIERS = SHEET_BY_KIND[ImportSheet.SUPPLIERS]
CATALOGUE = SHEET_BY_KIND[ImportSheet.CATALOGUE]
USERS = SHEET_BY_KIND[ImportSheet.USERS]


def context(
    scopes: frozenset[str] = frozenset({SUPPLY_CHAIN_IMPORT, COMMERCIAL_WRITE}),
    *,
    tenant: uuid.UUID = TENANT,
    workspace: uuid.UUID = WORKSPACE,
) -> AccessContext:
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset(),
        scopes=scopes,
        plan_id="professional",
    )


def _mine(ctx: AccessContext, scope: tuple[uuid.UUID, uuid.UUID]) -> bool:
    return scope == (ctx.tenant_id, ctx.workspace_id)


# ------------------------------------------------------------------ fakes --


@dataclass
class FakeSuppliers:
    """`SupplierImportPort` + contacts + bank accounts, as RLS answers."""

    rows: dict[uuid.UUID, tuple[tuple[uuid.UUID, uuid.UUID], KnownSupplier]] = field(
        default_factory=dict
    )
    contacts: list[tuple[uuid.UUID, SupplierContact]] = field(default_factory=list)
    accounts: list[tuple[uuid.UUID, SupplierBankAccount]] = field(default_factory=list)
    audits: list[AuditEvent] = field(default_factory=list)
    writes: int = 0

    def seed(
        self,
        name: str,
        code: str | None,
        *,
        scope: tuple[uuid.UUID, uuid.UUID] = (TENANT, WORKSPACE),
    ) -> KnownSupplier:
        supplier = KnownSupplier(uuid.uuid4(), name, code)
        self.rows[supplier.id] = (scope, supplier)
        return supplier

    def _visible(self, ctx: AccessContext) -> list[KnownSupplier]:
        return [s for scope, s in self.rows.values() if _mine(ctx, scope)]

    async def by_code(self, ctx: AccessContext, code: str) -> KnownSupplier | None:
        return next((s for s in self._visible(ctx) if s.code == code), None)

    async def by_name(self, ctx: AccessContext, name: str) -> KnownSupplier | None:
        # The database's normaliser, as far as these tests need it.
        key = " ".join(name.split()).casefold()
        return next(
            (s for s in self._visible(ctx) if " ".join(s.name.split()).casefold() == key), None
        )

    async def create(
        self, ctx: AccessContext, *, supplier_id: uuid.UUID, name: str, code: str, audit: AuditEvent
    ) -> bool:
        if await self.by_code(ctx, code) or await self.by_name(ctx, name):
            return False
        self.rows[supplier_id] = (
            (ctx.tenant_id, ctx.workspace_id),
            KnownSupplier(supplier_id, name, code),
        )
        self.audits.append(audit)
        self.writes += 1
        return True

    async def assign_code(
        self, ctx: AccessContext, *, supplier_id: uuid.UUID, code: str, audit: AuditEvent
    ) -> bool:
        held = self.rows.get(supplier_id)
        if held is None or not _mine(ctx, held[0]) or held[1].code is not None:
            return False
        if await self.by_code(ctx, code):
            return False
        self.rows[supplier_id] = (held[0], KnownSupplier(supplier_id, held[1].name, code))
        self.audits.append(audit)
        self.writes += 1
        return True

    async def supplier_workspace(
        self, ctx: AccessContext, supplier_id: uuid.UUID
    ) -> uuid.UUID | None:
        held = self.rows.get(supplier_id)
        return None if held is None or held[0][0] != ctx.tenant_id else held[0][1]

    async def latest_contact(
        self, ctx: AccessContext, supplier_id: uuid.UUID
    ) -> SupplierContact | None:
        mine = [c for s, c in self.contacts if s == supplier_id]
        return mine[-1] if mine else None

    async def add_contact(
        self, ctx: AccessContext, contact: NewSupplierContact, *, audit: AuditEvent
    ) -> SupplierContact:
        stored = SupplierContact(
            id=contact.id,
            supplier_id=contact.supplier_id,
            version=1 + sum(1 for s, _ in self.contacts if s == contact.supplier_id),
            name=contact.name,
            email=contact.email,
            phone=contact.phone,
            created_by=ctx.principal_id,
            created_at=NOW,
        )
        self.contacts.append((contact.supplier_id, stored))
        self.audits.append(audit)
        self.writes += 1
        return stored

    async def latest_bank_account(
        self, ctx: AccessContext, supplier_id: uuid.UUID
    ) -> SupplierBankAccount | None:
        mine = [a for s, a in self.accounts if s == supplier_id]
        return mine[-1] if mine else None

    async def add_bank_account(
        self, ctx: AccessContext, account: NewSupplierBankAccount, *, audit: AuditEvent
    ) -> SupplierBankAccount:
        stored = SupplierBankAccount(
            id=account.id,
            supplier_id=account.supplier_id,
            version=1,
            bank_name=account.bank_name,
            account_number=account.account_number,
            account_holder=account.account_holder,
            created_by=ctx.principal_id,
            created_at=NOW,
        )
        self.accounts.append((account.supplier_id, stored))
        self.audits.append(audit)
        self.writes += 1
        return stored


@dataclass
class FakeCatalogue:
    rows: list[tuple[tuple[uuid.UUID, uuid.UUID], CatalogueItem]] = field(default_factory=list)
    writes: int = 0

    def seed(
        self,
        item_code: str,
        sku_code: str | None,
        *,
        scope: tuple[uuid.UUID, uuid.UUID] = (TENANT, WORKSPACE),
    ) -> None:
        self.rows.append((scope, CatalogueItem(item_code, sku_code, "Có sẵn", None)))

    async def matching(
        self, ctx: AccessContext, item_codes: Sequence[str], sku_codes: Sequence[str]
    ) -> list[CatalogueItem]:
        return [
            i
            for scope, i in self.rows
            if _mine(ctx, scope) and (i.item_code in item_codes or i.sku_code in sku_codes)
        ]

    async def add(self, ctx: AccessContext, item: NewCatalogueItem, *, audit: AuditEvent) -> bool:
        mine = [i for scope, i in self.rows if _mine(ctx, scope)]
        if any((i.item_code, i.sku_code) == (item.item_code, item.sku_code) for i in mine):
            return False
        if item.sku_code is not None and any(i.sku_code == item.sku_code for i in mine):
            raise ConflictError("SKU đã có dưới mã hàng khác trong danh mục")
        self.rows.append(
            (
                (ctx.tenant_id, ctx.workspace_id),
                CatalogueItem(item.item_code, item.sku_code, item.name, item.category),
            )
        )
        self.writes += 1
        return True


@dataclass
class FakeMembers:
    workspace_list: list[WorkspaceRef] = field(
        default_factory=lambda: [
            WorkspaceRef(WORKSPACE, "cung-ung", "Cung ứng"),
            WorkspaceRef(uuid.uuid4(), "rnd", "R&D"),
        ]
    )
    roles: frozenset[str] = frozenset({"sc_operator", "sc_finance", "sc_viewer"})
    emails: set[str] = field(default_factory=set)
    invited: list[tuple[str, Mapping[uuid.UUID, frozenset[str]]]] = field(default_factory=list)
    allowed: bool = True
    # The caller's workspace's members by email (ticket onboarding/02).
    in_workspace: dict[str, uuid.UUID] = field(default_factory=dict)

    def _check(self) -> None:
        if not self.allowed:
            raise PermissionDeniedError("platform.members.read")

    async def workspaces(self, ctx: AccessContext) -> list[WorkspaceRef]:
        self._check()
        return self.workspace_list

    async def assignable_roles(self, ctx: AccessContext) -> frozenset[str]:
        self._check()
        return self.roles

    async def member_emails(self, ctx: AccessContext) -> frozenset[str]:
        self._check()
        return frozenset(self.emails)

    async def workspace_member_ids(self, ctx: AccessContext) -> Mapping[str, uuid.UUID]:
        self._check()
        return dict(self.in_workspace) if ctx.workspace_id == WORKSPACE else {}

    async def invite(
        self,
        ctx: AccessContext,
        *,
        display_name: str,
        email: str,
        memberships: Mapping[uuid.UUID, frozenset[str]],
    ) -> bool:
        self._check()
        if email in self.emails:
            return False
        self.emails.add(email)
        self.invited.append((email, memberships))
        return True


@dataclass
class FakeOpenCases:
    """`OpenCaseImportPort` as RLS answers: a reader sees its own tenant AND
    workspace; a key taken is refused as the UNIQUE would."""

    products: list[tuple[tuple[uuid.UUID, uuid.UUID], ProductDevelopmentCase]] = field(
        default_factory=list
    )
    pos: list[tuple[tuple[uuid.UUID, uuid.UUID], POCase, datetime]] = field(default_factory=list)
    steps: list[Any] = field(default_factory=list)
    audits: list[AuditEvent] = field(default_factory=list)

    async def product_cases(
        self, ctx: AccessContext, codes: Sequence[str]
    ) -> Mapping[str, KnownProductCase]:
        return {
            c.proposal_code: KnownProductCase(c.id.value, c.category, c.pic_user_id)
            for scope, c in self.products
            if _mine(ctx, scope) and c.proposal_code in codes
        }

    async def po_references(self, ctx: AccessContext, refs: Sequence[str]) -> frozenset[str]:
        return frozenset(
            c.po_reference
            for scope, c, _ in self.pos
            if _mine(ctx, scope) and c.po_reference in refs and c.po_reference is not None
        )

    async def add_product_case(
        self, ctx: AccessContext, case: ProductDevelopmentCase, *, audit: AuditEvent
    ) -> None:
        if await self.product_cases(ctx, [case.proposal_code]):
            raise ConflictError("mã đề xuất đã có")
        self.steps.extend(case.pop_pending_steps())
        self.products.append(((ctx.tenant_id, ctx.workspace_id), case))
        self.audits.append(audit)

    async def add_po_case(
        self, ctx: AccessContext, case: POCase, *, entered_at: datetime, audit: AuditEvent
    ) -> None:
        if await self.po_references(ctx, [case.po_reference or ""]):
            raise ConflictError("số PO đã có")
        self.pos.append(((ctx.tenant_id, ctx.workspace_id), case, entered_at))
        self.audits.append(audit)

    @property
    def writes(self) -> int:
        return len(self.products) + len(self.pos)


@dataclass
class StaticCategories:
    keys: tuple[str, ...] = ("noi", "chao")

    async def handle(self, ctx: AccessContext) -> list[ProductCategory]:
        return [ProductCategory(key=k, label=k.upper()) for k in self.keys]


@dataclass
class StaticReader:
    sheets: Mapping[str, list[tuple[str | None, ...]]]

    def read(self, data: bytes) -> Mapping[str, list[tuple[str | None, ...]]]:
        return self.sheets


@dataclass
class World:
    suppliers: FakeSuppliers = field(default_factory=FakeSuppliers)
    catalogue: FakeCatalogue = field(default_factory=FakeCatalogue)
    members: FakeMembers = field(default_factory=FakeMembers)
    open_cases: FakeOpenCases = field(default_factory=FakeOpenCases)

    def handler(self, sheets: Mapping[str, list[tuple[str | None, ...]]]) -> ImportSupplyChainData:
        authz = ScopeAuthorizationService()
        ids, clock = Uuid4Generator(), FixedClock(NOW)
        return ImportSupplyChainData(
            reader=StaticReader(sheets),
            suppliers=self.suppliers,
            contacts=self.suppliers,
            accounts=self.suppliers,
            save_contact=SaveSupplierContact(self.suppliers, authz, ids, clock),
            save_account=SaveSupplierBankAccount(self.suppliers, authz, ids, clock),
            catalogue=self.catalogue,
            members=self.members,
            authz=authz,
            ids=ids,
            clock=clock,
            open_cases=self.open_cases,
            categories=StaticCategories(),
        )

    @property
    def writes(self) -> int:
        return (
            self.suppliers.writes
            + self.catalogue.writes
            + len(self.members.invited)
            + self.open_cases.writes
        )


def header(spec_kind: ImportSheet) -> tuple[str | None, ...]:
    return tuple(c.header for c in SHEET_BY_KIND[spec_kind].columns)


def supplier_row(
    code: str,
    name: str,
    contact: str | None = None,
    email: str | None = None,
    bank: tuple[str, str, str] | None = None,
) -> tuple[str | None, ...]:
    return (code, name, contact, email, None, *(bank or (None, None, None)))


ACCOUNT = ("Vietcombank", "0071 000 123 456", "CONG TY MINH PHAT")


def statuses(report: ImportReport, sheet: ImportSheet) -> dict[int, RowStatus]:
    return {r.row: r.status for r in report.rows if r.sheet is sheet}


# --------------------------------------------------------------- parsing --


def test_headers_match_by_text_in_any_order_with_the_required_mark() -> None:
    raw = [
        ("tên ncc *", " MÃ NCC* ", "Người liên hệ"),
        ("Minh Phát", "MP01", "Chị Lan"),
        (None, "  ", None),
    ]
    parsed = parse_sheet(SUPPLIERS, raw)
    assert parsed.problem is None
    assert [(r.row, r.get("code"), r.get("name")) for r in parsed.rows] == [
        (2, "MP01", "Minh Phát")
    ]


def test_an_unknown_or_a_missing_required_header_refuses_the_sheet() -> None:
    unknown = parse_sheet(SUPPLIERS, [("Mã NCC", "Tên NCC", "Giá")])
    assert unknown.problem is not None and "Giá" in unknown.problem.message
    missing = parse_sheet(SUPPLIERS, [("Tên NCC",)])
    assert missing.problem is not None and "Mã NCC" in missing.problem.message
    assert parse_sheet(SUPPLIERS, []).problem is not None


def test_a_row_missing_a_required_cell_or_too_long_is_refused_by_number() -> None:
    raw = [header(ImportSheet.SUPPLIERS), ("MP01", None), ("X" * 65, "Dài"), ("MP02", "Ổn")]
    parsed = parse_sheet(SUPPLIERS, raw)
    assert [r.row for r in parsed.rows] == [4]
    assert [(r.row, r.status) for r in parsed.refused] == [
        (2, RowStatus.REJECTED),
        (3, RowStatus.REJECTED),
    ]
    assert "thiếu Tên NCC" in parsed.refused[0].messages


def test_a_sheet_over_the_row_limit_is_refused_whole() -> None:
    raw = [header(ImportSheet.CATALOGUE)] + [
        (f"M{i}", None, "Tên", None) for i in range(MAX_ROWS + 1)
    ]
    assert parse_sheet(CATALOGUE, raw).problem is not None


# --------------------------------------------------------------- handler --


async def test_without_the_import_scope_nothing_is_read() -> None:
    world = World()
    with pytest.raises(PermissionDeniedError):
        await world.handler({}).handle(context(frozenset({COMMERCIAL_WRITE})), b"x", apply=False)


async def test_a_file_over_the_limit_or_without_a_template_sheet_is_refused() -> None:
    world = World()
    with pytest.raises(PayloadTooLargeError):
        await world.handler({}).handle(context(), b"x" * (5 * 1024 * 1024 + 1), apply=False)
    with pytest.raises(DomainError):
        await world.handler({"Sheet1": [("a",)]}).handle(context(), b"x", apply=False)


async def test_a_dry_run_writes_nothing_and_reports_what_would_be_done() -> None:
    world = World()
    sheets = {
        "NCC": [
            header(ImportSheet.SUPPLIERS),
            supplier_row("MP01", "Minh Phát", "Chị Lan", "lan@minhphat.vn", ACCOUNT),
        ],
        "Danh mục": [header(ImportSheet.CATALOGUE), ("EL-00001", "EL-00001-01", "Nồi 24cm", "noi")],
        "Người dùng": [
            header(ImportSheet.USERS),
            ("an@elmich.vn", "Nguyễn An", "sc_operator", None),
        ],
    }
    report = await world.handler(sheets).handle(context(), b"x", apply=False)
    assert report.dry_run
    assert world.writes == 0
    assert [r.status for r in report.rows] == [RowStatus.CREATED] * 3


async def test_the_same_file_twice_adds_nothing_the_second_time() -> None:
    world = World()
    sheets = {
        "NCC": [
            header(ImportSheet.SUPPLIERS),
            supplier_row("MP01", "Minh Phát", "Chị Lan", "lan@minhphat.vn", ACCOUNT),
        ],
        "Danh mục": [
            header(ImportSheet.CATALOGUE),
            ("EL-00001", "EL-00001-01", "Nồi 24cm đỏ", "noi"),
            ("EL-00001", "EL-00001-02", "Nồi 24cm xanh", "noi"),
            ("EL-00002", None, "Chảo", None),
        ],
        "Người dùng": [
            header(ImportSheet.USERS),
            ("An@Elmich.vn", "Nguyễn An", "sc_operator, sc_finance", "cung-ung, rnd"),
        ],
    }
    first = await world.handler(sheets).handle(context(), b"x", apply=True)
    assert {r.status for r in first.rows} == {RowStatus.CREATED}
    written = world.writes
    assert written == 3 + 3 + 1  # supplier, contact, account; three items; one person
    second = await world.handler(sheets).handle(context(), b"x", apply=True)
    assert world.writes == written
    assert {r.status for r in second.rows} == {RowStatus.EXISTS}
    email, memberships = world.members.invited[0]
    assert email == "an@elmich.vn"
    assert set(memberships) == {w.id for w in world.members.workspace_list}
    assert all(r == frozenset({"sc_operator", "sc_finance"}) for r in memberships.values())


async def test_a_bad_row_is_refused_and_the_others_still_go_in() -> None:
    world = World()
    sheets = {
        "NCC": [
            header(ImportSheet.SUPPLIERS),
            supplier_row("MP01", "Minh Phát"),
            supplier_row("MP02", "Hưng Thịnh", None, "không-phải-email"),
            supplier_row("MP03", "Đông Á", bank=("VCB", "12", "X")),
            supplier_row("MP01", "Bản sao"),
            supplier_row("MP04", "Gia Phú"),
        ],
    }
    report = await world.handler(sheets).handle(context(), b"x", apply=True)
    assert statuses(report, ImportSheet.SUPPLIERS) == {
        2: RowStatus.CREATED,
        3: RowStatus.REJECTED,
        4: RowStatus.REJECTED,
        5: RowStatus.REJECTED,
        6: RowStatus.CREATED,
    }
    assert {s.code for _, s in world.suppliers.rows.values()} == {"MP01", "MP04"}
    duplicate = next(r for r in report.rows if r.row == 5)
    assert "trùng dòng 2" in duplicate.messages[0]


async def test_a_supplier_made_from_a_case_gets_its_code_and_a_contradiction_is_refused() -> None:
    world = World()
    from_case = world.suppliers.seed("Công ty Minh Phát", None)
    world.suppliers.seed("Hưng Thịnh", "HT-OLD")
    sheets = {
        "NCC": [
            header(ImportSheet.SUPPLIERS),
            supplier_row("MP01", "công ty  minh phát"),
            supplier_row("HT01", "Hưng Thịnh"),
        ]
    }
    world.suppliers.seed("Gia Phú", "GP01")
    sheets["NCC"].append(supplier_row("GP01", "Hưng Thịnh"))
    report = await world.handler(sheets).handle(context(), b"x", apply=True)
    assert statuses(report, ImportSheet.SUPPLIERS) == {
        2: RowStatus.CREATED,
        3: RowStatus.REJECTED,
        4: RowStatus.REJECTED,
    }
    assert world.suppliers.rows[from_case.id][1].code == "MP01"
    assert "HT-OLD" in report.rows[1].messages[0]


async def test_a_different_bank_account_is_refused_and_no_number_is_ever_reported() -> None:
    world = World()
    on_file = world.suppliers.seed("Minh Phát", "MP01")
    await world.suppliers.add_bank_account(
        context(),
        NewSupplierBankAccount.of(
            id=uuid.uuid4(),
            supplier_id=on_file.id,
            bank_name="Vietcombank",
            account_number="0071000123456",
            account_holder="CONG TY MINH PHAT",
        ),
        audit=AuditEvent(
            id=uuid.uuid4(),
            tenant_id=TenantId(TENANT),
            workspace_id=WorkspaceId(WORKSPACE),
            actor_id=UserId(uuid.uuid4()),
            action="seed",
            resource_type="supplier",
            resource_id=str(on_file.id),
            occurred_at=NOW,
            details={},
        ),
    )
    writes = world.writes
    same = {"NCC": [header(ImportSheet.SUPPLIERS), supplier_row("MP01", "Minh Phát", bank=ACCOUNT)]}
    report = await world.handler(same).handle(context(), b"x", apply=True)
    assert report.rows[0].status is RowStatus.EXISTS
    other = ("Vietcombank", "0071 999 888 777", "CONG TY MINH PHAT")
    changed = {
        "NCC": [header(ImportSheet.SUPPLIERS), supplier_row("MP01", "Minh Phát", bank=other)]
    }
    report = await world.handler(changed).handle(context(), b"x", apply=True)
    assert report.rows[0].status is RowStatus.REJECTED
    assert world.writes == writes
    words = " ".join(m for r in report.rows for m in r.messages)
    for number in ("0071999888777", "0071 999 888 777", "0071000123456"):
        assert number not in words


async def test_contact_and_account_need_the_commercial_write_scope() -> None:
    world = World()
    sheets = {
        "NCC": [
            header(ImportSheet.SUPPLIERS),
            supplier_row("MP01", "Minh Phát", "Chị Lan", None, ACCOUNT),
            supplier_row("MP02", "Hưng Thịnh"),
        ]
    }
    dry = await world.handler(sheets).handle(
        context(frozenset({SUPPLY_CHAIN_IMPORT})), b"x", apply=False
    )
    assert statuses(dry, ImportSheet.SUPPLIERS) == {2: RowStatus.PARTIAL, 3: RowStatus.CREATED}
    report = await world.handler(sheets).handle(
        context(frozenset({SUPPLY_CHAIN_IMPORT})), b"x", apply=True
    )
    assert statuses(report, ImportSheet.SUPPLIERS) == {2: RowStatus.PARTIAL, 3: RowStatus.CREATED}
    assert world.suppliers.contacts == [] and world.suppliers.accounts == []


async def test_another_workspace_s_rows_are_neither_found_nor_touched() -> None:
    world = World()
    elsewhere = (TENANT, uuid.uuid4())
    other_tenant = (uuid.uuid4(), WORKSPACE)
    theirs = world.suppliers.seed("Minh Phát", "MP01", scope=elsewhere)
    world.suppliers.seed("Minh Phát", "MP01", scope=other_tenant)
    world.catalogue.seed("EL-00001", "EL-00001-01", scope=elsewhere)
    sheets = {
        "NCC": [header(ImportSheet.SUPPLIERS), supplier_row("MP01", "Minh Phát")],
        "Danh mục": [header(ImportSheet.CATALOGUE), ("EL-00001", "EL-00001-01", "Nồi", None)],
    }
    report = await world.handler(sheets).handle(context(), b"x", apply=True)
    assert {r.status for r in report.rows} == {RowStatus.CREATED}
    assert world.suppliers.rows[theirs.id] == (elsewhere, theirs)
    mine = [s for scope, s in world.suppliers.rows.values() if scope == (TENANT, WORKSPACE)]
    assert [s.code for s in mine] == ["MP01"]


async def test_catalogue_duplicates_in_the_file_and_a_sku_under_another_item() -> None:
    world = World()
    world.catalogue.seed("EL-00009", "EL-00009-01")
    world.catalogue.seed("EL-00001", "EL-00001-01")
    sheets = {
        "Danh mục": [
            header(ImportSheet.CATALOGUE),
            ("EL-00001", "EL-00001-01", "Nồi", None),
            ("EL-00002", "EL-00009-01", "SKU của mã khác", None),
            ("EL-00003", "EL-00003-01", "Mới", None),
            ("EL-00004", "EL-00003-01", "SKU trùng trong file", None),
            ("el-00003", "el-00003-01", "Trùng cặp", None),
        ]
    }
    dry = await world.handler(sheets).handle(context(), b"x", apply=False)
    assert statuses(dry, ImportSheet.CATALOGUE)[3] is RowStatus.REJECTED
    report = await world.handler(sheets).handle(context(), b"x", apply=True)
    assert statuses(report, ImportSheet.CATALOGUE) == {
        2: RowStatus.EXISTS,
        3: RowStatus.REJECTED,
        4: RowStatus.CREATED,
        5: RowStatus.REJECTED,
        6: RowStatus.REJECTED,
    }
    assert world.catalogue.writes == 1


async def test_users_need_the_member_scopes_known_roles_and_the_tenant_s_workspaces() -> None:
    world = World()
    world.members.emails.add("cu@elmich.vn")
    sheets = {
        "Người dùng": [
            header(ImportSheet.USERS),
            ("cu@elmich.vn", "Người cũ", "sc_operator", None),
            ("moi@elmich.vn", "Người mới", "org_admin", None),
            ("ws@elmich.vn", "Sai workspace", "sc_viewer", "kho-khac"),
            ("email-sai", "Sai email", "sc_viewer", None),
            ("ok@elmich.vn", "Đúng", "sc_viewer", None),
        ]
    }
    report = await world.handler(sheets).handle(context(), b"x", apply=True)
    assert statuses(report, ImportSheet.USERS) == {
        2: RowStatus.EXISTS,
        3: RowStatus.REJECTED,
        4: RowStatus.REJECTED,
        5: RowStatus.REJECTED,
        6: RowStatus.CREATED,
    }
    assert world.members.invited == [("ok@elmich.vn", {WORKSPACE: frozenset({"sc_viewer"})})]
    world.members.allowed = False
    denied = await world.handler(sheets).handle(context(), b"x", apply=True)
    assert {r.status for r in denied.rows} == {RowStatus.REJECTED}


# -------------------------------------------------------------- workbook --


def test_the_template_round_trips_through_the_reader() -> None:
    sheets = XlsxWorkbookReader().read(build_template())
    for spec in SHEETS:
        parsed = parse_sheet(spec, sheets[spec.title])
        assert parsed.problem is None and parsed.rows == ()


def test_the_reader_reads_numbers_as_text_and_refuses_what_is_not_a_workbook() -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "NCC"
    sheet.append(["Mã NCC", "Tên NCC", "Số tài khoản"])
    sheet.append([1001, "Minh Phát", 71000123456])
    out = io.BytesIO()
    workbook.save(out)
    read = XlsxWorkbookReader().read(out.getvalue())
    assert read["NCC"][1][:3] == ("1001", "Minh Phát", "71000123456")
    with pytest.raises(DomainError):
        XlsxWorkbookReader().read(b"not a workbook")


def test_a_workbook_that_inflates_past_the_bound_is_refused_before_it_is_parsed() -> None:
    import zipfile

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/sharedStrings.xml", b"a" * (65 * 1024 * 1024))
    assert len(out.getvalue()) < 1024 * 1024
    with pytest.raises(DomainError, match="giải nén"):
        XlsxWorkbookReader().read(out.getvalue())


def test_the_shipped_template_has_the_headers_the_reader_checks() -> None:
    shipped = (REPO_ROOT / "docs" / "products" / "elmich" / "import-template.xlsx").read_bytes()
    sheets = XlsxWorkbookReader().read(shipped)
    for spec in SHEETS:
        assert parse_sheet(spec, sheets[spec.title]).problem is None, spec.title
        assert len([h for h in sheets[spec.title][0] if h]) == len(spec.columns), spec.title


async def test_the_template_is_for_whoever_may_import() -> None:
    template = GetImportTemplate(ScopeAuthorizationService(), build_template)
    assert (await template.handle(context()))[:2] == b"PK"
    with pytest.raises(PermissionDeniedError):
        await template.handle(context(frozenset()))


# ------------------------------------------------- open cases (ON-02) --

CASES = frozenset({SUPPLY_CHAIN_IMPORT, PRODUCT_CASE_WRITE, PO_CASE_WRITE})
LAN = uuid.uuid4()
VN_DAY = datetime(2026, 9, 1, tzinfo=UTC) - timedelta(hours=7)  # 00:00 01/09/2026 giờ VN


def product_row(
    code: str = "DX-2026-041",
    state: str = "sample_testing",
    *,
    category: str = "noi",
    supplier: str | None = "Minh Phát",
    sample_round: str | None = "2",
    pic: str = "lan@elmich.vn",
    entered: str | None = "01/09/2026",
    created: str | None = "15/08/2026",
) -> tuple[str | None, ...]:
    return (code, "Nồi inox 24cm", category, state, supplier, sample_round, pic, entered, created)


def po_row(
    ref: str = "PO-2026-0101",
    state: str = "production",
    *,
    kind: str | None = "new",
    code: str | None = None,
    pic: str | None = None,
    entered: str | None = "01/09/2026",
    created: str | None = None,
) -> tuple[str | None, ...]:
    return (ref, "Minh Phát", state, kind, code, pic, entered, created)


def case_world() -> World:
    world = World()
    world.members.in_workspace = {"lan@elmich.vn": LAN}
    return world


def case_sheets(
    products: Sequence[tuple[str | None, ...]] = (),
    pos: Sequence[tuple[str | None, ...]] = (),
) -> dict[str, list[tuple[str | None, ...]]]:
    sheets: dict[str, list[tuple[str | None, ...]]] = {}
    if products:
        sheets["Hồ sơ SP"] = [header(ImportSheet.PRODUCT_CASES), *products]
    if pos:
        sheets["Hồ sơ PO"] = [header(ImportSheet.PO_CASES), *pos]
    return sheets


async def test_a_product_case_opens_at_its_state_with_one_backdated_import_row() -> None:
    world = case_world()
    report = await world.handler(case_sheets([product_row()])).handle(
        context(CASES), b"x", apply=True
    )
    assert statuses(report, ImportSheet.PRODUCT_CASES) == {2: RowStatus.CREATED}
    ((_, case),) = world.open_cases.products
    assert (case.state, case.sample_round, case.pic_user_id) == (
        ProductDevState.SAMPLE_TESTING,
        2,
        LAN,
    )
    assert case.created_at == datetime(2026, 8, 15, tzinfo=UTC) - timedelta(hours=7)
    (step,) = world.open_cases.steps
    assert (step.action, step.from_state, step.to_state, step.reason) == (
        ProductAction.IMPORT,
        None,
        ProductDevState.SAMPLE_TESTING,
        IMPORT_REASON,
    )
    assert step.occurred_at == VN_DAY and step.opens_round == 2
    (audit,) = world.open_cases.audits
    assert audit.action == PRODUCT_CASE_IMPORTED
    assert audit.details["via"] == "import"
    assert audit.details["entered_on_declared"] == "01/09/2026"


async def test_a_dry_run_opens_nothing_and_a_second_run_finds_the_case() -> None:
    world = case_world()
    sheets = case_sheets([product_row()], [po_row()])
    dry = await world.handler(sheets).handle(context(CASES), b"x", apply=False)
    assert statuses(dry, ImportSheet.PRODUCT_CASES) == {2: RowStatus.CREATED}
    assert statuses(dry, ImportSheet.PO_CASES) == {2: RowStatus.CREATED}
    assert world.writes == 0
    await world.handler(sheets).handle(context(CASES), b"x", apply=True)
    again = await world.handler(sheets).handle(context(CASES), b"x", apply=True)
    assert set(statuses(again, ImportSheet.PRODUCT_CASES).values()) == {RowStatus.EXISTS}
    assert set(statuses(again, ImportSheet.PO_CASES).values()) == {RowStatus.EXISTS}
    assert world.open_cases.writes == 2


async def test_a_po_case_starts_at_its_state_dated_when_it_entered_it() -> None:
    world = case_world()
    report = await world.handler(case_sheets(pos=[po_row(created=None)])).handle(
        context(CASES), b"x", apply=True
    )
    assert statuses(report, ImportSheet.PO_CASES) == {2: RowStatus.CREATED}
    ((_, case, entered),) = world.open_cases.pos
    assert (case.state, entered, case.created_at) == (CaseState.PRODUCTION, VN_DAY, VN_DAY)
    assert world.open_cases.audits[0].action == PO_CASE_IMPORTED


async def test_a_po_row_may_name_a_product_case_this_file_opens_and_takes_its_category() -> None:
    world = case_world()
    sheets = case_sheets(
        [product_row(state="item_coding")], [po_row(code="DX-2026-041", pic="lan@elmich.vn")]
    )
    dry = await world.handler(sheets).handle(context(CASES), b"x", apply=False)
    assert statuses(dry, ImportSheet.PO_CASES) == {2: RowStatus.CREATED}
    await world.handler(sheets).handle(context(CASES), b"x", apply=True)
    ((_, product),) = world.open_cases.products
    ((_, po, _),) = world.open_cases.pos
    assert (po.product_dev_case_id, po.category, po.pic_user_id) == (product.id.value, "noi", LAN)


@pytest.mark.parametrize(
    ("row", "words"),
    [
        (product_row(pic="khac@elmich.vn"), "PIC không phải thành viên"),
        (product_row(category="am_dun"), "Nhóm sản phẩm"),
        (product_row(state="pending_bod_review"), "bước một người làm tiếp"),
        (product_row(state="ordered"), "bước một người làm tiếp"),
        (product_row(state="blocked"), "bước một người làm tiếp"),
        (product_row(state="dang_test"), "không phải một bước"),
        (product_row(state="proposed", sample_round="1"), "chưa có mẫu"),
        (product_row(state="sample_testing", sample_round="0"), "cần số vòng mẫu"),
        (product_row(state="sample_requested", supplier=None, sample_round=None), "tên NCC"),
        (product_row(entered="31/02/2026"), "không phải ngày"),
        (product_row(entered="01/09/2027"), "tương lai"),
        (product_row(entered="01/08/2026", created="15/08/2026"), "trước ngày tạo"),
    ],
)
async def test_a_product_row_the_import_may_not_write_is_refused_with_its_reason(
    row: tuple[str | None, ...], words: str
) -> None:
    world = case_world()
    report = await world.handler(case_sheets([row])).handle(context(CASES), b"x", apply=True)
    (result,) = [r for r in report.rows if r.sheet is ImportSheet.PRODUCT_CASES]
    assert result.status is RowStatus.REJECTED
    assert words in " ".join(result.messages), result.messages
    assert world.open_cases.writes == 0


@pytest.mark.parametrize(
    ("row", "words"),
    [
        (po_row(state="completed"), "bước một người làm tiếp"),
        (po_row(state="order_requested"), "bước một người làm tiếp"),
        (po_row(kind="gap"), "new hoặc reorder"),
        (po_row(code="DX-KHONG-CO", pic="lan@elmich.vn"), "Mã đề xuất không có"),
        (po_row(pic="khac@elmich.vn"), "PIC không phải thành viên"),
    ],
)
async def test_a_po_row_the_import_may_not_write_is_refused_with_its_reason(
    row: tuple[str | None, ...], words: str
) -> None:
    world = case_world()
    report = await world.handler(case_sheets(pos=[row])).handle(context(CASES), b"x", apply=True)
    (result,) = [r for r in report.rows if r.sheet is ImportSheet.PO_CASES]
    assert result.status is RowStatus.REJECTED
    assert words in " ".join(result.messages), result.messages
    assert world.open_cases.writes == 0


async def test_opening_a_case_needs_the_write_of_its_kind() -> None:
    world = case_world()
    report = await world.handler(case_sheets([product_row()], [po_row()])).handle(
        context(frozenset({SUPPLY_CHAIN_IMPORT})), b"x", apply=True
    )
    assert set(statuses(report, ImportSheet.PRODUCT_CASES).values()) == {RowStatus.REJECTED}
    assert set(statuses(report, ImportSheet.PO_CASES).values()) == {RowStatus.REJECTED}
    assert world.open_cases.writes == 0


async def test_a_case_of_another_workspace_neither_counts_as_there_nor_links() -> None:
    world = case_world()
    other = context(CASES, workspace=uuid.uuid4())
    elsewhere = ProductDevelopmentCase.imported(
        id=ProductDevelopmentCaseId(uuid.uuid4()),
        tenant_id=TenantId(TENANT),
        workspace_id=WorkspaceId(other.workspace_id),
        proposal_code="DX-2026-041",
        product_name="Nồi",
        category="noi",
        supplier_name=None,
        state=ProductDevState.PROPOSED,
        sample_round=0,
        pic_user_id=LAN,
        actor_id=LAN,
        entered_at=VN_DAY,
        created_at=VN_DAY,
    )
    world.open_cases.products.append(((TENANT, other.workspace_id), elsewhere))
    report = await world.handler(
        case_sheets(pos=[po_row(code="DX-2026-041", pic="lan@elmich.vn")])
    ).handle(context(CASES), b"x", apply=True)
    assert statuses(report, ImportSheet.PO_CASES) == {2: RowStatus.REJECTED}
    mine = await world.handler(case_sheets([product_row()])).handle(
        context(CASES), b"x", apply=False
    )
    assert statuses(mine, ImportSheet.PRODUCT_CASES) == {2: RowStatus.CREATED}
