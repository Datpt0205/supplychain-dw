"""The one-time import of a tenant's existing data (ADR 0027, E16; ticket
onboarding/01), as the admin who runs it.

`ImportSupplyChainData` reads the shipped template's sheets and settles every
row, in a dry run (reads only: what WOULD happen) or for real. The rules, each
decided where the write happens:

- **Who.** `supply_chain.import` to run it at all. Each part of a row then goes
  through the handler or the service that owns it, with its own check: a
  supplier's contact and bank account through `SaveSupplierContact` and
  `SaveSupplierBankAccount` (`supply_chain.commercial.write`), a user through
  the platform's member service (`platform.members.write`, no administrative
  role). A part the caller may not write is refused on its row; the rest of
  the file still runs.
- **Where.** Suppliers and the catalogue land in the caller's tenant AND
  workspace (RLS); a user's workspaces must be the caller's tenant's.
- **Idempotent by each row's own key** (a supplier's code, a catalogue
  item's item and SKU code, a user's email): a row already in the
  application is `exists` and nothing is written, so running the same file
  again adds nothing. A row is its own unit: a bad one is refused with its
  reason and the others carry on.
- **Nothing inferred.** A blank required cell is a refusal, never a guess; no
  model reads the file. A bank account is compared with the one on file by
  code (`same_account`), never echoed back: a different one is refused
  (changing where a supplier is paid is not an import's job).
- **Audit.** Every write is audited in its own transaction, the caller as
  actor (`via: import` where this module writes it).
- **Open cases** (ticket onboarding/02; ADR 0027 decision 3): a product case
  or a PO case starts at the state its row gives, with ONE history row
  (`import`, "Nạp từ dữ liệu cũ") dated when the row says it entered that
  state, and its creation dated as the row says (both declared; the import's
  day when blank); the SLA clock runs from the declared date. Opening a case
  needs the write of its kind (`product_case.write`, `po_case.write`), checked
  once per sheet. The PIC is a member of the caller's workspace, named by
  email; a product case's Category one of the tenant's. A PO row may name a
  product case already in the application, or one this file's product sheet
  opens. A case already there by its key is `exists`: an import never moves
  one. No model reads any cell.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Protocol

from dw_kernel.errors import (
    ConflictError,
    DomainError,
    DWError,
    PayloadTooLargeError,
    PermissionDeniedError,
)
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import AuthorizationPort
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.application.commercial import (
    SaveSupplierBankAccount,
    SaveSupplierContact,
    SupplierBankAccountsPort,
    SupplierContactsPort,
    allows,
)
from dw_supply_chain.application.handlers import (
    COMMERCIAL_WRITE,
    PO_CASE_WRITE,
    PRODUCT_CASE_WRITE,
)
from dw_supply_chain.domain.case_import import check_dates, imported_po_case
from dw_supply_chain.domain.commercial import (
    NewSupplierBankAccount,
    NewSupplierContact,
    same_account,
)
from dw_supply_chain.domain.daily_brief import VIETNAM
from dw_supply_chain.domain.data_import import (
    MAX_IMPORT_BYTES,
    SHEETS,
    ImportReport,
    ImportSheet,
    ParsedRow,
    RawSheet,
    RowOutcome,
    RowResult,
    RowStatus,
    SheetProblem,
    first_of_each,
    parse_day,
    parse_sheet,
    split_list,
    valid_email,
)
from dw_supply_chain.domain.po_case import CaseState, OrderKind, POCase, POCaseId
from dw_supply_chain.domain.product_development_case import (
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
)
from dw_supply_chain.sla_policy import ProductCategory

SUPPLY_CHAIN_IMPORT = "supply_chain.import"
_RESOURCE = "supply_chain_import"
SUPPLIER_IMPORTED = "supply_chain.supplier.imported"
SUPPLIER_CODE_ASSIGNED = "supply_chain.supplier.code_assigned"
CATALOGUE_ITEM_IMPORTED = "supply_chain.catalogue_item.imported"
PRODUCT_CASE_IMPORTED = "supply_chain.product_case.imported"
PO_CASE_IMPORTED = "supply_chain.po_case.imported"
_VIA = "import"
_NIL = uuid.UUID(int=0)


# ------------------------------------------------------------------ ports --


class WorkbookReaderPort(Protocol):
    def read(self, data: bytes) -> Mapping[str, RawSheet]:
        """Each sheet's rows by title, cells as text (None when blank), the
        header first. A file that is not a workbook is a `DomainError`."""
        ...


@dataclass(frozen=True, slots=True)
class KnownSupplier:
    id: uuid.UUID
    name: str
    code: str | None


class SupplierImportPort(Protocol):
    """The caller's workspace's suppliers (RLS), as an import meets them."""

    async def by_code(self, context: AccessContext, code: str) -> KnownSupplier | None: ...

    async def by_name(self, context: AccessContext, name: str) -> KnownSupplier | None:
        """The supplier whose name normalises to the same as `name`, by the
        database's own rule (`supply_chain.normalize_supplier_name`)."""
        ...

    async def create(
        self,
        context: AccessContext,
        *,
        supplier_id: uuid.UUID,
        name: str,
        code: str,
        audit: AuditEvent,
    ) -> bool:
        """Insert with its audit; False when the name or the code was taken
        meanwhile (nothing written)."""
        ...

    async def assign_code(
        self, context: AccessContext, *, supplier_id: uuid.UUID, code: str, audit: AuditEvent
    ) -> bool:
        """Set the code of a supplier that has none; False when it has one
        by now or the code is another's (nothing written)."""
        ...


@dataclass(frozen=True, slots=True)
class CatalogueItem:
    item_code: str
    sku_code: str | None
    name: str
    category: str | None


@dataclass(frozen=True, slots=True)
class NewCatalogueItem:
    id: uuid.UUID
    item_code: str
    sku_code: str | None
    name: str
    category: str | None


class CatalogueImportPort(Protocol):
    async def matching(
        self, context: AccessContext, item_codes: Sequence[str], sku_codes: Sequence[str]
    ) -> list[CatalogueItem]:
        """The workspace's catalogue rows whose item code or SKU code is one
        of these."""
        ...

    async def add(
        self, context: AccessContext, item: NewCatalogueItem, *, audit: AuditEvent
    ) -> bool:
        """Insert with its audit; False when the same item and SKU are there
        already. A SKU code under another item code is a `ConflictError`."""
        ...


@dataclass(frozen=True, slots=True)
class WorkspaceRef:
    id: uuid.UUID
    slug: str
    name: str


class MemberDirectoryPort(Protocol):
    """The caller's tenant's people, through the platform's own services
    (which check `platform.members.read|write` and refuse an administrative
    role themselves)."""

    async def workspaces(self, context: AccessContext) -> list[WorkspaceRef]: ...

    async def assignable_roles(self, context: AccessContext) -> frozenset[str]:
        """Role keys an import may hand out: no administrative one."""
        ...

    async def member_emails(self, context: AccessContext) -> frozenset[str]: ...

    async def workspace_member_ids(self, context: AccessContext) -> Mapping[str, uuid.UUID]:
        """The members of the caller's workspace, by lower-cased email: who
        may be an imported case's PIC."""
        ...

    async def invite(
        self,
        context: AccessContext,
        *,
        display_name: str,
        email: str,
        memberships: Mapping[uuid.UUID, frozenset[str]],
    ) -> bool:
        """True: created; False: already a member of the tenant."""
        ...


@dataclass(frozen=True, slots=True)
class KnownProductCase:
    id: uuid.UUID
    category: str
    pic_user_id: uuid.UUID


class OpenCaseImportPort(Protocol):
    """The caller's workspace's cases (RLS), as the import meets them."""

    async def product_cases(
        self, context: AccessContext, codes: Sequence[str]
    ) -> Mapping[str, KnownProductCase]:
        """The product cases with one of these proposal codes, by code."""
        ...

    async def po_references(self, context: AccessContext, refs: Sequence[str]) -> frozenset[str]:
        """Which of these PO references the workspace already has."""
        ...

    async def add_product_case(
        self, context: AccessContext, case: ProductDevelopmentCase, *, audit: AuditEvent
    ) -> None:
        """The case, its pending `import` row (and round) and the audit, in one
        transaction; a code taken meanwhile is a `ConflictError`."""
        ...

    async def add_po_case(
        self, context: AccessContext, case: POCase, *, entered_at: datetime, audit: AuditEvent
    ) -> None:
        """The case, its one `import` row dated `entered_at` and the audit, in
        one transaction; a reference taken meanwhile is a `ConflictError`."""
        ...


class CategoryListPort(Protocol):
    async def handle(self, context: AccessContext) -> list[ProductCategory]: ...


# --------------------------------------------------------------- handler --


def _words(exc: DWError) -> str:
    return str(exc) or type(exc).__name__


@dataclass(frozen=True)
class ImportSupplyChainData:
    reader: WorkbookReaderPort
    suppliers: SupplierImportPort
    contacts: SupplierContactsPort
    accounts: SupplierBankAccountsPort
    save_contact: SaveSupplierContact
    save_account: SaveSupplierBankAccount
    catalogue: CatalogueImportPort
    members: MemberDirectoryPort
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock
    # Open cases (ticket onboarding/02).
    open_cases: OpenCaseImportPort
    categories: CategoryListPort

    async def handle(self, context: AccessContext, data: bytes, *, apply: bool) -> ImportReport:
        await self.authz.require(
            context=context, action=SUPPLY_CHAIN_IMPORT, resource_type=_RESOURCE
        )
        if len(data) > MAX_IMPORT_BYTES:
            raise PayloadTooLargeError(
                f"file nạp tối đa {MAX_IMPORT_BYTES} byte", details={"max_bytes": MAX_IMPORT_BYTES}
            )
        workbook = self.reader.read(data)
        # The product cases this file opens (or would, in a dry run), which a
        # PO row of the same file may name.
        opened: dict[str, KnownProductCase] = {}
        settle: dict[
            ImportSheet,
            Callable[
                [AccessContext, Sequence[ParsedRow], bool], Coroutine[Any, Any, list[RowResult]]
            ],
        ] = {
            ImportSheet.SUPPLIERS: self._suppliers,
            ImportSheet.CATALOGUE: self._catalogue,
            ImportSheet.USERS: self._users,
            ImportSheet.PRODUCT_CASES: lambda c, r, a: self._product_cases(c, r, a, opened),
            ImportSheet.PO_CASES: lambda c, r, a: self._po_cases(c, r, a, opened),
        }
        rows: list[RowResult] = []
        problems: list[SheetProblem] = []
        present = 0
        for spec in SHEETS:
            raw = workbook.get(spec.title)
            if raw is None:
                continue
            present += 1
            parsed = parse_sheet(spec, raw)
            if parsed.problem is not None:
                problems.append(parsed.problem)
                continue
            rows.extend(parsed.refused)
            rows.extend(await settle[spec.sheet](context, parsed.rows, apply))
        if present == 0:
            raise DomainError(
                "file không có sheet nào của mẫu nạp",
                details={"sheets": [s.title for s in SHEETS]},
            )
        order = {s.sheet: i for i, s in enumerate(SHEETS)}
        rows.sort(key=lambda r: (order[r.sheet], r.row))
        return ImportReport(dry_run=not apply, rows=tuple(rows), problems=tuple(problems))

    def _audit(
        self,
        context: AccessContext,
        action: str,
        resource_type: str,
        resource_id: uuid.UUID,
        details: dict[str, Any],
    ) -> AuditEvent:
        return AuditEvent(
            id=self.ids.new_uuid(),
            tenant_id=TenantId(context.tenant_id),
            workspace_id=WorkspaceId(context.workspace_id),
            actor_id=UserId(context.principal_id),
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id),
            occurred_at=self.clock.now(),
            details={**details, "via": _VIA},
        )

    # -- suppliers -----------------------------------------------------------

    async def _suppliers(
        self, context: AccessContext, parsed: Sequence[ParsedRow], apply: bool
    ) -> list[RowResult]:
        rows, results = first_of_each(ImportSheet.SUPPLIERS, parsed, ("code",), "Mã NCC")
        commercial = await allows(self.authz, context, COMMERCIAL_WRITE, "supplier")
        for row in rows:
            results.append(await self._supplier(context, row, apply, commercial))
        return results

    async def _supplier(
        self, context: AccessContext, row: ParsedRow, apply: bool, commercial: bool
    ) -> RowResult:
        code, name = row.text("code"), row.text("name")
        outcome = RowOutcome()
        contact, account, refused = self._commercial_parts(row)
        for message in refused:
            outcome.add(RowStatus.REJECTED, message)
        if refused:
            return outcome.result(ImportSheet.SUPPLIERS, row.row, code)
        supplier = await self._resolve_supplier(context, code, name, apply, outcome)
        if supplier is None:
            return outcome.result(ImportSheet.SUPPLIERS, row.row, code)
        if (contact or account) and not commercial:
            outcome.add(
                RowStatus.REJECTED,
                "Liên hệ, tài khoản: cần quyền ghi dữ liệu thương mại"
                " (supply_chain.commercial.write)",
            )
            return outcome.result(ImportSheet.SUPPLIERS, row.row, code)
        if contact is not None:
            await self._contact(context, supplier, contact, apply, outcome)
        if account is not None:
            await self._account(context, supplier, account, apply, outcome)
        return outcome.result(ImportSheet.SUPPLIERS, row.row, code)

    def _commercial_parts(
        self, row: ParsedRow
    ) -> tuple[NewSupplierContact | None, NewSupplierBankAccount | None, list[str]]:
        """The row's contact and account, checked by the domain's own
        constructors (the ones the handlers use), or why not."""
        refused: list[str] = []
        contact: NewSupplierContact | None = None
        account: NewSupplierBankAccount | None = None
        email, phone = row.get("contact_email"), row.get("contact_phone")
        if row.get("contact_name") or email or phone:
            try:
                contact = NewSupplierContact.of(
                    id=_NIL,
                    supplier_id=_NIL,
                    name=row.get("contact_name") or "",
                    email=email,
                    phone=phone,
                )
            except DomainError as exc:
                refused.append(f"Liên hệ: {_words(exc)}")
        bank = [row.get(k) for k in ("bank_name", "account_number", "account_holder")]
        if any(bank):
            if not all(bank):
                refused.append("Tài khoản: cần đủ Ngân hàng, Số tài khoản, Chủ tài khoản")
            else:
                try:
                    account = NewSupplierBankAccount.of(
                        id=_NIL,
                        supplier_id=_NIL,
                        bank_name=bank[0] or "",
                        account_number=bank[1] or "",
                        account_holder=bank[2] or "",
                    )
                except DomainError as exc:
                    refused.append(f"Tài khoản: {_words(exc)}")
        return contact, account, refused

    async def _resolve_supplier(
        self, context: AccessContext, code: str, name: str, apply: bool, outcome: RowOutcome
    ) -> KnownSupplier | None:
        """The supplier the row names, created or given its code when that is
        what the row says; None when the row contradicts the application."""
        by_code = await self.suppliers.by_code(context, code)
        by_name = await self.suppliers.by_name(context, name)
        if by_code is not None:
            if by_name is not None and by_name.id != by_code.id:
                outcome.add(
                    RowStatus.REJECTED,
                    f"NCC: tên đã là của NCC khác ({by_name.code or 'chưa có mã'})",
                )
                return None
            outcome.add(RowStatus.EXISTS, "NCC: đã có mã này, bỏ qua")
            return by_code
        if by_name is not None:
            if by_name.code is not None:
                outcome.add(RowStatus.REJECTED, f"NCC: cùng tên đã có mã khác ({by_name.code})")
                return None
            if apply and not await self.suppliers.assign_code(
                context,
                supplier_id=by_name.id,
                code=code,
                audit=self._audit(
                    context, SUPPLIER_CODE_ASSIGNED, "supplier", by_name.id, {"code": code}
                ),
            ):
                outcome.add(RowStatus.REJECTED, "NCC: mã vừa được gán cho NCC khác; chạy lại")
                return None
            outcome.add(RowStatus.CREATED, "NCC: đã có (theo tên), gán mã")
            return KnownSupplier(by_name.id, by_name.name, code)
        supplier_id = self.ids.new_uuid()
        if apply and not await self.suppliers.create(
            context,
            supplier_id=supplier_id,
            name=name,
            code=code,
            audit=self._audit(
                context, SUPPLIER_IMPORTED, "supplier", supplier_id, {"code": code, "name": name}
            ),
        ):
            outcome.add(
                RowStatus.REJECTED, "NCC: tên hoặc mã vừa được tạo bởi người khác; chạy lại"
            )
            return None
        outcome.add(RowStatus.CREATED, "NCC: tạo mới")
        return KnownSupplier(supplier_id, name, code)

    async def _contact(
        self,
        context: AccessContext,
        supplier: KnownSupplier,
        contact: NewSupplierContact,
        apply: bool,
        outcome: RowOutcome,
    ) -> None:
        latest = await self.contacts.latest_contact(context, supplier.id)
        if latest is not None and (latest.name, latest.email, latest.phone) == (
            contact.name,
            contact.email,
            contact.phone,
        ):
            outcome.add(RowStatus.EXISTS, "Liên hệ: như đã lưu")
            return
        if apply:
            try:
                await self.save_contact.handle(
                    context,
                    supplier.id,
                    name=contact.name,
                    email=contact.email,
                    phone=contact.phone,
                )
            except DWError as exc:
                outcome.add(RowStatus.REJECTED, f"Liên hệ: {_words(exc)}")
                return
        outcome.add(RowStatus.CREATED, "Liên hệ: thêm phiên bản mới" if latest else "Liên hệ: thêm")

    async def _account(
        self,
        context: AccessContext,
        supplier: KnownSupplier,
        account: NewSupplierBankAccount,
        apply: bool,
        outcome: RowOutcome,
    ) -> None:
        """Compared with the account on file by code; the report never says
        either number."""
        latest = await self.accounts.latest_bank_account(context, supplier.id)
        if latest is not None:
            if same_account(latest.account_number, account.account_number):
                outcome.add(RowStatus.EXISTS, "Tài khoản: khớp tài khoản đã lưu")
            else:
                outcome.add(
                    RowStatus.REJECTED,
                    "Tài khoản: khác tài khoản đã lưu; đổi tài khoản NCC trên thẻ NCC,"
                    " không qua nạp",
                )
            return
        if apply:
            try:
                await self.save_account.handle(
                    context,
                    supplier.id,
                    bank_name=account.bank_name,
                    account_number=account.account_number,
                    account_holder=account.account_holder,
                )
            except DWError as exc:
                outcome.add(RowStatus.REJECTED, f"Tài khoản: {_words(exc)}")
                return
        outcome.add(RowStatus.CREATED, "Tài khoản: thêm")

    # -- catalogue -----------------------------------------------------------

    async def _catalogue(
        self, context: AccessContext, parsed: Sequence[ParsedRow], apply: bool
    ) -> list[RowResult]:
        rows, results = first_of_each(
            ImportSheet.CATALOGUE, parsed, ("item_code", "sku_code"), "Mã hàng và SKU"
        )
        with_sku = [r for r in rows if r.get("sku_code")]
        kept_skus, refused_skus = first_of_each(
            ImportSheet.CATALOGUE, with_sku, ("sku_code",), "Mã SKU"
        )
        refused_rows = {r.row for r in refused_skus}
        results.extend(refused_skus)
        rows = [r for r in rows if r.row not in refused_rows]
        known = await self.catalogue.matching(
            context,
            sorted({r.text("item_code") for r in rows}),
            sorted({r.get("sku_code") or "" for r in kept_skus}),
        )
        pairs = {(k.item_code, k.sku_code) for k in known}
        sku_owner = {k.sku_code: k.item_code for k in known if k.sku_code is not None}
        for row in rows:
            item_code, sku_code = row.text("item_code"), row.get("sku_code")
            key = f"{item_code} / {sku_code}" if sku_code else item_code
            if (item_code, sku_code) in pairs:
                results.append(
                    RowResult(
                        ImportSheet.CATALOGUE, row.row, key, RowStatus.EXISTS, ("Đã có, bỏ qua",)
                    )
                )
                continue
            if sku_code is not None and sku_code in sku_owner:
                results.append(
                    RowResult(
                        ImportSheet.CATALOGUE,
                        row.row,
                        key,
                        RowStatus.REJECTED,
                        (f"SKU đã có dưới mã hàng {sku_owner[sku_code]}",),
                    )
                )
                continue
            if apply:
                results.append(await self._add_item(context, row, item_code, sku_code, key))
                continue
            results.append(
                RowResult(
                    ImportSheet.CATALOGUE, row.row, key, RowStatus.CREATED, ("Thêm vào danh mục",)
                )
            )
        return results

    async def _add_item(
        self, context: AccessContext, row: ParsedRow, item_code: str, sku_code: str | None, key: str
    ) -> RowResult:
        item = NewCatalogueItem(
            id=self.ids.new_uuid(),
            item_code=item_code,
            sku_code=sku_code,
            name=row.text("name"),
            category=row.get("category"),
        )
        try:
            added = await self.catalogue.add(
                context,
                item,
                audit=self._audit(
                    context,
                    CATALOGUE_ITEM_IMPORTED,
                    "catalogue_item",
                    item.id,
                    {"item_code": item_code, "sku_code": sku_code},
                ),
            )
        except ConflictError as exc:
            return RowResult(
                ImportSheet.CATALOGUE, row.row, key, RowStatus.REJECTED, (_words(exc),)
            )
        if not added:
            return RowResult(
                ImportSheet.CATALOGUE, row.row, key, RowStatus.EXISTS, ("Đã có, bỏ qua",)
            )
        return RowResult(
            ImportSheet.CATALOGUE, row.row, key, RowStatus.CREATED, ("Thêm vào danh mục",)
        )

    # -- users ---------------------------------------------------------------

    async def _users(
        self, context: AccessContext, parsed: Sequence[ParsedRow], apply: bool
    ) -> list[RowResult]:
        rows, results = first_of_each(ImportSheet.USERS, parsed, ("email",), "Email")
        try:
            workspaces = await self.members.workspaces(context)
            roles = await self.members.assignable_roles(context)
            members = await self.members.member_emails(context)
        except PermissionDeniedError:
            results.extend(
                RowResult(
                    ImportSheet.USERS,
                    r.row,
                    r.text("email"),
                    RowStatus.REJECTED,
                    ("Cần quyền quản lý thành viên (platform.members.read, .write)",),
                )
                for r in rows
            )
            return results
        by_slug = {w.slug.casefold(): w for w in workspaces}
        for row in rows:
            results.append(await self._user(context, row, by_slug, roles, members, apply))
        return results

    async def _user(
        self,
        context: AccessContext,
        row: ParsedRow,
        by_slug: Mapping[str, WorkspaceRef],
        roles: frozenset[str],
        members: frozenset[str],
        apply: bool,
    ) -> RowResult:
        email = row.text("email").lower()

        def refused(*messages: str) -> RowResult:
            return RowResult(ImportSheet.USERS, row.row, email, RowStatus.REJECTED, messages)

        if not valid_email(email):
            return refused("Email không hợp lệ")
        if email in members:
            return RowResult(
                ImportSheet.USERS, row.row, email, RowStatus.EXISTS, ("Đã là thành viên, bỏ qua",)
            )
        asked = split_list(row.get("roles"))
        unknown = [r for r in asked if r not in roles]
        slugs = split_list(row.get("workspaces"))
        missing = [s for s in slugs if s.casefold() not in by_slug]
        problems = []
        if not asked:
            problems.append("Thiếu vai")
        if unknown:
            problems.append(
                "Vai không có hoặc là vai quản trị (cấp trên trang thành viên): "
                + ", ".join(unknown)
            )
        if missing:
            problems.append("Workspace không thuộc công ty: " + ", ".join(missing))
        if problems:
            return refused(*problems)
        targets = [by_slug[s.casefold()].id for s in slugs] or [context.workspace_id]
        if apply:
            try:
                created = await self.members.invite(
                    context,
                    display_name=row.text("display_name"),
                    email=email,
                    memberships={ws: frozenset(asked) for ws in targets},
                )
            except DWError as exc:
                return refused(_words(exc))
            if not created:
                return RowResult(
                    ImportSheet.USERS,
                    row.row,
                    email,
                    RowStatus.EXISTS,
                    ("Đã là thành viên, bỏ qua",),
                )
        return RowResult(
            ImportSheet.USERS,
            row.row,
            email,
            RowStatus.CREATED,
            ("Mời vào công ty; đăng nhập lần đầu bằng email này sẽ liên kết tài khoản",),
        )

    # -- open cases (ticket onboarding/02) -----------------------------------

    async def _members(self, context: AccessContext) -> Mapping[str, uuid.UUID] | None:
        try:
            return await self.members.workspace_member_ids(context)
        except PermissionDeniedError:
            return None

    async def _product_cases(
        self,
        context: AccessContext,
        parsed: Sequence[ParsedRow],
        apply: bool,
        opened: dict[str, KnownProductCase],
    ) -> list[RowResult]:
        sheet = ImportSheet.PRODUCT_CASES
        rows, results = first_of_each(sheet, parsed, ("proposal_code",), "Mã đề xuất")
        if not await _may(self.authz, context, PRODUCT_CASE_WRITE):
            return results + [
                _refused(sheet, r, r.text("proposal_code"), "Cần quyền mở hồ sơ sản phẩm")
                for r in rows
            ]
        members = await self._members(context)
        try:
            categories = {c.key for c in await self.categories.handle(context)}
        except PermissionDeniedError:
            categories = set()
        known = await self.open_cases.product_cases(
            context, [r.text("proposal_code") for r in rows]
        )
        now = self.clock.now()
        for row in rows:
            code = row.text("proposal_code")
            if code in known:
                results.append(
                    RowResult(sheet, row.row, code, RowStatus.EXISTS, ("Đã có, bỏ qua",))
                )
                continue
            try:
                case = self._product_case(context, row, members, categories, now)
            except _RowRefusedError as refusal:
                results.append(_refused(sheet, row, code, str(refusal)))
                continue
            except DWError as exc:
                results.append(_refused(sheet, row, code, _words(exc)))
                continue
            if apply:
                try:
                    await self.open_cases.add_product_case(
                        context, case, audit=self._case_audit(context, case.id.value, row)
                    )
                except DWError as exc:
                    results.append(_refused(sheet, row, code, _words(exc)))
                    continue
            opened[code] = KnownProductCase(case.id.value, case.category, case.pic_user_id)
            results.append(
                RowResult(
                    sheet, row.row, code, RowStatus.CREATED, (f"Mở ở bước {case.state.value}",)
                )
            )
        return results

    def _product_case(
        self,
        context: AccessContext,
        row: ParsedRow,
        members: Mapping[str, uuid.UUID] | None,
        categories: set[str],
        now: datetime,
    ) -> ProductDevelopmentCase:
        if members is None:
            raise _RowRefusedError("Cần quyền xem thành viên để kiểm PIC (platform.members.read)")
        pic = _pic(row, members)
        assert pic is not None  # the column is required
        category = row.text("category").strip()
        if category not in categories:
            raise _RowRefusedError("Nhóm sản phẩm không có trong danh sách của công ty")
        try:
            state = ProductDevState(row.text("state").strip())
        except ValueError:
            raise _RowRefusedError("Bước hiện tại không phải một bước của hồ sơ sản phẩm") from None
        raw_round = row.get("sample_round")
        if raw_round is not None and not raw_round.strip().isdigit():
            raise _RowRefusedError("Vòng mẫu phải là số nguyên")
        default_round = (
            0 if state in (ProductDevState.PROPOSED, ProductDevState.SAMPLE_REQUESTED) else 1
        )
        entered = _day(row, "entered_on", "Ngày vào bước", now)
        created = _day(row, "created_on", "Ngày tạo hồ sơ", min(entered, now))
        check_dates(entered, created, now)
        return ProductDevelopmentCase.imported(
            id=ProductDevelopmentCaseId(self.ids.new_uuid()),
            tenant_id=TenantId(context.tenant_id),
            workspace_id=WorkspaceId(context.workspace_id),
            proposal_code=row.text("proposal_code"),
            product_name=row.text("product_name"),
            category=category,
            supplier_name=row.get("supplier_name"),
            state=state,
            sample_round=default_round if raw_round is None else int(raw_round),
            pic_user_id=pic,
            actor_id=context.principal_id,
            entered_at=entered,
            created_at=created,
        )

    async def _po_cases(
        self,
        context: AccessContext,
        parsed: Sequence[ParsedRow],
        apply: bool,
        opened: Mapping[str, KnownProductCase],
    ) -> list[RowResult]:
        sheet = ImportSheet.PO_CASES
        rows, results = first_of_each(sheet, parsed, ("po_reference",), "Số PO")
        if not await _may(self.authz, context, PO_CASE_WRITE):
            return results + [
                _refused(sheet, r, r.text("po_reference"), "Cần quyền mở hồ sơ PO") for r in rows
            ]
        members = await self._members(context)
        taken = await self.open_cases.po_references(context, [r.text("po_reference") for r in rows])
        codes = sorted({r.text("proposal_code") for r in rows if r.get("proposal_code")})
        products = {**await self.open_cases.product_cases(context, codes), **opened}
        now = self.clock.now()
        for row in rows:
            ref = row.text("po_reference")
            if ref in taken:
                results.append(RowResult(sheet, row.row, ref, RowStatus.EXISTS, ("Đã có, bỏ qua",)))
                continue
            try:
                case, entered = self._po_case(context, row, members, products, now)
            except _RowRefusedError as refusal:
                results.append(_refused(sheet, row, ref, str(refusal)))
                continue
            except DWError as exc:
                results.append(_refused(sheet, row, ref, _words(exc)))
                continue
            if apply:
                try:
                    await self.open_cases.add_po_case(
                        context,
                        case,
                        entered_at=entered,
                        audit=self._case_audit(context, case.id.value, row, kind="po_case"),
                    )
                except DWError as exc:
                    results.append(_refused(sheet, row, ref, _words(exc)))
                    continue
            results.append(
                RowResult(
                    sheet, row.row, ref, RowStatus.CREATED, (f"Mở ở bước {case.state.value}",)
                )
            )
        return results

    def _po_case(
        self,
        context: AccessContext,
        row: ParsedRow,
        members: Mapping[str, uuid.UUID] | None,
        products: Mapping[str, KnownProductCase],
        now: datetime,
    ) -> tuple[POCase, datetime]:
        try:
            state = CaseState(row.text("state").strip())
        except ValueError:
            raise _RowRefusedError("Bước hiện tại không phải một bước của hồ sơ PO") from None
        try:
            kind = OrderKind((row.get("order_kind") or "reorder").strip().lower())
        except ValueError:
            raise _RowRefusedError("Loại đơn là new hoặc reorder") from None
        pic: uuid.UUID | None = None
        if row.get("pic_email") is not None:
            if members is None:
                raise _RowRefusedError(
                    "Cần quyền xem thành viên để kiểm PIC (platform.members.read)"
                )
            pic = _pic(row, members)
        product: KnownProductCase | None = None
        code = row.get("proposal_code")
        if code is not None:
            product = products.get(code)
            if product is None:
                raise _RowRefusedError("Mã đề xuất không có hồ sơ sản phẩm trong workspace này")
        entered = _day(row, "entered_on", "Ngày vào bước", now)
        created = _day(row, "created_on", "Ngày tạo hồ sơ", min(entered, now))
        check_dates(entered, created, now)
        case = imported_po_case(
            id=POCaseId(self.ids.new_uuid()),
            tenant_id=TenantId(context.tenant_id),
            workspace_id=WorkspaceId(context.workspace_id),
            po_reference=row.text("po_reference"),
            supplier_name=row.text("supplier_name"),
            state=state,
            order_kind=kind,
            product_dev_case_id=None if product is None else product.id,
            pic_user_id=pic,
            category=None if product is None else product.category,
            created_at=created,
        )
        return case, entered

    def _case_audit(
        self, context: AccessContext, case_id: uuid.UUID, row: ParsedRow, *, kind: str = ""
    ) -> AuditEvent:
        po = kind == "po_case"
        return self._audit(
            context,
            PO_CASE_IMPORTED if po else PRODUCT_CASE_IMPORTED,
            "po_case" if po else "product_dev_case",
            case_id,
            {
                "row": row.row,
                "state": row.text("state").strip(),
                # Declared by the sheet, not observed by the application.
                "entered_on_declared": row.get("entered_on"),
                "created_on_declared": row.get("created_on"),
            },
        )


class _RowRefusedError(Exception):
    """A row's refusal, in words, while its cells are read."""


def _day(row: ParsedRow, key: str, label: str, now: datetime) -> datetime:
    """A declared day as the start of that day in Vietnam, or now when blank."""
    raw = row.get(key)
    if raw is None:
        return now
    day = parse_day(raw)
    if day is None:
        raise _RowRefusedError(f"{label} không phải ngày (dd/mm/yyyy)")
    return datetime.combine(day, time(), tzinfo=VIETNAM)


def _refused(sheet: ImportSheet, row: ParsedRow, key: str, message: str) -> RowResult:
    return RowResult(sheet, row.row, key, RowStatus.REJECTED, (message,))


def _pic(row: ParsedRow, members: Mapping[str, uuid.UUID]) -> uuid.UUID | None:
    email = row.get("pic_email")
    if email is None:
        return None
    found = members.get(email.strip().lower())
    if found is None:
        raise _RowRefusedError("PIC không phải thành viên workspace này")
    return found


async def _may(authz: AuthorizationPort, context: AccessContext, scope: str) -> bool:
    return await allows(authz, context, scope, _RESOURCE)


@dataclass(frozen=True)
class GetImportTemplate:
    """The empty template, for whoever may run the import."""

    authz: AuthorizationPort
    build: Callable[[], bytes]

    async def handle(self, context: AccessContext) -> bytes:
        await self.authz.require(
            context=context, action=SUPPLY_CHAIN_IMPORT, resource_type=_RESOURCE
        )
        return self.build()


__all__ = [
    "CATALOGUE_ITEM_IMPORTED",
    "PO_CASE_IMPORTED",
    "PRODUCT_CASE_IMPORTED",
    "SUPPLIER_CODE_ASSIGNED",
    "SUPPLIER_IMPORTED",
    "SUPPLY_CHAIN_IMPORT",
    "CatalogueImportPort",
    "CatalogueItem",
    "CategoryListPort",
    "GetImportTemplate",
    "ImportSupplyChainData",
    "KnownProductCase",
    "KnownSupplier",
    "MemberDirectoryPort",
    "NewCatalogueItem",
    "OpenCaseImportPort",
    "SupplierImportPort",
    "WorkbookReaderPort",
    "WorkspaceRef",
]
