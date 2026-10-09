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
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
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
from dw_supply_chain.application.handlers import COMMERCIAL_WRITE
from dw_supply_chain.domain.commercial import (
    NewSupplierBankAccount,
    NewSupplierContact,
    same_account,
)
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
    parse_sheet,
    split_list,
    valid_email,
)

SUPPLY_CHAIN_IMPORT = "supply_chain.import"
_RESOURCE = "supply_chain_import"
SUPPLIER_IMPORTED = "supply_chain.supplier.imported"
SUPPLIER_CODE_ASSIGNED = "supply_chain.supplier.code_assigned"
CATALOGUE_ITEM_IMPORTED = "supply_chain.catalogue_item.imported"
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

    async def handle(self, context: AccessContext, data: bytes, *, apply: bool) -> ImportReport:
        await self.authz.require(
            context=context, action=SUPPLY_CHAIN_IMPORT, resource_type=_RESOURCE
        )
        if len(data) > MAX_IMPORT_BYTES:
            raise PayloadTooLargeError(
                f"file nạp tối đa {MAX_IMPORT_BYTES} byte", details={"max_bytes": MAX_IMPORT_BYTES}
            )
        workbook = self.reader.read(data)
        settle: dict[
            ImportSheet,
            Callable[
                [AccessContext, Sequence[ParsedRow], bool], Coroutine[Any, Any, list[RowResult]]
            ],
        ] = {
            ImportSheet.SUPPLIERS: self._suppliers,
            ImportSheet.CATALOGUE: self._catalogue,
            ImportSheet.USERS: self._users,
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
    "SUPPLIER_CODE_ASSIGNED",
    "SUPPLIER_IMPORTED",
    "SUPPLY_CHAIN_IMPORT",
    "CatalogueImportPort",
    "CatalogueItem",
    "GetImportTemplate",
    "ImportSupplyChainData",
    "KnownSupplier",
    "MemberDirectoryPort",
    "NewCatalogueItem",
    "SupplierImportPort",
    "WorkbookReaderPort",
    "WorkspaceRef",
]
