"""Commercial data and BM04 as fields: read, write, redact (ADR 0026, E15; ticket
ai-automation/01).

The rules, each decided here where the read or the write happens:

- **Who sees a price.** `supply_chain.commercial.read`, asked of the
  authorization port (so a `platform_admin` or a support grant is judged the way
  every other check judges it). Without it a reading comes back with every
  field of `PRICE_FIELDS` emptied *in this layer*: no consumer of a handler can
  show a price it was never handed. The flag `prices_visible` says the empties
  are redactions, so a view writes `redacted: true` rather than 0.
- **Who sets one.** `supply_chain.commercial.write`. A BM04 save without it may
  still change the profile's other fields; the new version then carries the
  previous version's price and currency forward, so it neither drops nor
  invents a price its writer could not see.
- **Which case or supplier.** Read under the caller's tenant (RLS) and required
  to be in the caller's workspace; a miss of either reads as not found.
- **What BM04 attributes may hold.** The tenant's own schema if it has set one
  (`PolicyOverridePort`), the platform's otherwise, read at every save.
- **What a payment may cite.** Only a document of this PO case, of the type its
  kind needs (`deposit_docs` for a deposit, `payment_docs` for the final
  payment); the composite FK refuses another case's again in the database.

Audit events commit with their row. They name versions and kinds, never an
amount or an account number: the audit log is read by people who do not hold
the commercial scope.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Protocol

from dw_kernel.errors import DomainError, NotFoundError, PermissionDeniedError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import AuthorizationPort, PolicyOverridePort
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.application.case_documents import CaseLookupPort
from dw_supply_chain.application.handlers import (
    ACTION_DUTIES_READ,
    ACTION_DUTIES_WRITE,
    COMMERCIAL_READ,
    COMMERCIAL_WRITE,
    PO_CASE_READ,
    PRODUCT_CASE_READ,
    PRODUCT_CASE_WRITE,
)
from dw_supply_chain.bm04_schema import BM04_SCHEMA_POLICY_ID, SupplyChainBm04Schema
from dw_supply_chain.domain.case_document import (
    CaseDocument,
    CaseDocumentId,
    CaseKind,
    DocumentType,
)
from dw_supply_chain.domain.commercial import (
    CommercialTerms,
    NewPOPayment,
    NewProductProfile,
    NewSupplierBankAccount,
    NewSupplierContact,
    PaymentKind,
    POCommercial,
    POPayment,
    ProductProfile,
    ProfileCommercial,
    SupplierBankAccount,
    SupplierContact,
)

_PROFILE = "product_profile"
_PO_COMMERCIAL = "po_commercial"
_PAYMENT = "po_payment"
_SUPPLIER = "supplier"
_BM04_SCHEMA = "bm04_schema"

PROFILE_SAVED = "supply_chain.product_profile.saved"
TERMS_SET = "supply_chain.po_case.commercial_terms_set"
PAYMENT_RECORDED = "supply_chain.po_payment.recorded"
CONTACT_SAVED = "supply_chain.supplier_contact.saved"
BANK_ACCOUNT_SAVED = "supply_chain.supplier_bank_account.saved"

# The paper each kind of payment may cite.
PAYMENT_DOCUMENT_TYPE: Mapping[PaymentKind, DocumentType] = {
    PaymentKind.DEPOSIT: DocumentType.DEPOSIT_DOCS,
    PaymentKind.FINAL: DocumentType.PAYMENT_DOCS,
}


async def allows(
    authz: AuthorizationPort, context: AccessContext, scope: str, resource_type: str
) -> bool:
    """Whether `authz` would let `context` act under `scope`: the same decision
    a `require` makes, asked without raising."""
    try:
        await authz.require(context=context, action=scope, resource_type=resource_type)
    except PermissionDeniedError:
        return False
    return True


async def _require_in_workspace(
    lookup: CaseLookupPort, context: AccessContext, case_id: uuid.UUID, what: str
) -> None:
    workspace = await lookup.case_workspace(context, case_id)
    if workspace is None or workspace != context.workspace_id:
        raise NotFoundError(f"{what} not found", details={"id": str(case_id)})


def _audit(
    context: AccessContext,
    ids: IdGenerator,
    clock: UtcClock,
    *,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID,
    details: dict[str, Any],
) -> AuditEvent:
    return AuditEvent(
        id=ids.new_uuid(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        actor_id=UserId(context.principal_id),
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id),
        occurred_at=clock.now(),
        details=details,
    )


# --------------------------------------------------------------- BM04 -------


@dataclass(frozen=True)
class Bm04SchemaSource:
    """The tenant's own BM04 schema if it has set one, the platform's otherwise.
    Re-validated on read, so a row edited past the handler is refused loudly."""

    policy_override_repo: PolicyOverridePort
    platform_default: SupplyChainBm04Schema

    async def resolve(self, context: AccessContext) -> SupplyChainBm04Schema:
        override = await self.policy_override_repo.get(context, BM04_SCHEMA_POLICY_ID)
        if override is None:
            return self.platform_default
        return SupplyChainBm04Schema.model_validate(override)


@dataclass(frozen=True)
class GetBm04Schema:
    schemas: Bm04SchemaSource
    authz: AuthorizationPort

    async def handle(self, context: AccessContext) -> SupplyChainBm04Schema:
        await self.authz.require(
            context=context, action=ACTION_DUTIES_READ, resource_type=_BM04_SCHEMA
        )
        return await self.schemas.resolve(context)


@dataclass(frozen=True)
class SetBm04SchemaOverride:
    """Replaces the tenant's BM04 schema, whole. A process rule: the same
    scope as the tenant's step-to-duty policies."""

    policy_override_repo: PolicyOverridePort
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(self, context: AccessContext, schema: SupplyChainBm04Schema) -> None:
        await self.authz.require(
            context=context, action=ACTION_DUTIES_WRITE, resource_type=_BM04_SCHEMA
        )
        await self.policy_override_repo.put(
            context,
            BM04_SCHEMA_POLICY_ID,
            schema.model_dump(mode="json"),
            audit=AuditEvent(
                id=self.ids.new_uuid(),
                tenant_id=TenantId(context.tenant_id),
                workspace_id=WorkspaceId(context.workspace_id),
                actor_id=UserId(context.principal_id),
                action=f"supply_chain.{_BM04_SCHEMA}.override_set",
                resource_type=_BM04_SCHEMA,
                resource_id=BM04_SCHEMA_POLICY_ID,
                occurred_at=self.clock.now(),
            ),
        )


class ProductProfileRepositoryPort(Protocol):
    async def latest(self, context: AccessContext, case_id: uuid.UUID) -> ProductProfile | None: ...

    async def add(
        self, context: AccessContext, profile: NewProductProfile, *, audit: AuditEvent
    ) -> ProductProfile: ...


@dataclass(frozen=True, slots=True)
class ProfileReading:
    """The latest BM04 version (prices emptied unless `prices_visible`), the
    schema it is filled against, and what the caller may do."""

    profile: ProductProfile | None
    schema: SupplyChainBm04Schema
    prices_visible: bool
    can_edit: bool
    can_edit_prices: bool


@dataclass(frozen=True)
class GetProductProfile:
    cases: CaseLookupPort
    profiles: ProductProfileRepositoryPort
    schemas: Bm04SchemaSource
    authz: AuthorizationPort

    async def handle(self, context: AccessContext, case_id: uuid.UUID) -> ProfileReading:
        await self.authz.require(
            context=context,
            action=PRODUCT_CASE_READ,
            resource_type=_PROFILE,
            resource_id=str(case_id),
        )
        await _require_in_workspace(self.cases, context, case_id, "product case")
        profile = await self.profiles.latest(context, case_id)
        visible = await allows(self.authz, context, COMMERCIAL_READ, _PROFILE)
        if profile is not None and not visible:
            profile = profile.without_prices()
        return ProfileReading(
            profile=profile,
            schema=await self.schemas.resolve(context),
            prices_visible=visible,
            can_edit=await allows(self.authz, context, PRODUCT_CASE_WRITE, _PROFILE),
            can_edit_prices=await allows(self.authz, context, COMMERCIAL_WRITE, _PROFILE),
        )


@dataclass(frozen=True)
class SaveProductProfile:
    """One new BM04 version. `planning` carries MOQ, lead time and Incoterm;
    `prices` (price and currency) is None to keep the previous version's."""

    cases: CaseLookupPort
    profiles: ProductProfileRepositoryPort
    schemas: Bm04SchemaSource
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(
        self,
        context: AccessContext,
        case_id: uuid.UUID,
        *,
        attributes: Mapping[str, Any],
        planning: ProfileCommercial,
        prices: ProfileCommercial | None,
    ) -> ProductProfile:
        await self.authz.require(
            context=context,
            action=PRODUCT_CASE_WRITE,
            resource_type=_PROFILE,
            resource_id=str(case_id),
        )
        if prices is not None:
            await self.authz.require(
                context=context,
                action=COMMERCIAL_WRITE,
                resource_type=_PROFILE,
                resource_id=str(case_id),
            )
        await _require_in_workspace(self.cases, context, case_id, "product case")
        schema = await self.schemas.resolve(context)
        clean = schema.check(attributes)
        if prices is None:
            previous = await self.profiles.latest(context, case_id)
            commercial = planning.with_prices_of(None if previous is None else previous.commercial)
        else:
            commercial = planning.with_prices_of(prices)
        profile_id = self.ids.new_uuid()
        return await self.profiles.add(
            context,
            NewProductProfile(
                id=profile_id,
                product_dev_case_id=case_id,
                commercial=commercial,
                attributes=clean,
                schema_version=schema.policy_version,
            ),
            audit=_audit(
                context,
                self.ids,
                self.clock,
                action=PROFILE_SAVED,
                resource_type=_PROFILE,
                resource_id=profile_id,
                details={
                    "product_dev_case_id": str(case_id),
                    "schema_version": schema.policy_version,
                    "prices_set": prices is not None,
                },
            ),
        )


# ---------------------------------------------------------- PO case --------


class POCommercialRepositoryPort(Protocol):
    async def read(self, context: AccessContext, case_id: uuid.UUID) -> POCommercial: ...

    async def set_terms(
        self,
        context: AccessContext,
        case_id: uuid.UUID,
        terms: CommercialTerms,
        line_prices: Mapping[uuid.UUID, Decimal | None],
        *,
        audit: AuditEvent,
    ) -> POCommercial: ...

    async def add_payment(
        self, context: AccessContext, payment: NewPOPayment, *, audit: AuditEvent
    ) -> POPayment: ...


@dataclass(frozen=True, slots=True)
class POCommercialReading:
    commercial: POCommercial
    prices_visible: bool
    can_edit: bool


@dataclass(frozen=True)
class GetPOCommercial:
    cases: CaseLookupPort
    commercial: POCommercialRepositoryPort
    authz: AuthorizationPort

    async def handle(self, context: AccessContext, case_id: uuid.UUID) -> POCommercialReading:
        await self.authz.require(
            context=context,
            action=PO_CASE_READ,
            resource_type=_PO_COMMERCIAL,
            resource_id=str(case_id),
        )
        await _require_in_workspace(self.cases, context, case_id, "PO case")
        commercial = await self.commercial.read(context, case_id)
        visible = await allows(self.authz, context, COMMERCIAL_READ, _PO_COMMERCIAL)
        return POCommercialReading(
            commercial=commercial if visible else commercial.without_prices(),
            prices_visible=visible,
            can_edit=await allows(self.authz, context, COMMERCIAL_WRITE, _PO_COMMERCIAL),
        )


@dataclass(frozen=True)
class SetPOCommercialTerms:
    """The case's terms and its lines' unit prices, replaced together. A price
    for a SKU that is not a line of this case is refused, not ignored."""

    cases: CaseLookupPort
    commercial: POCommercialRepositoryPort
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(
        self,
        context: AccessContext,
        case_id: uuid.UUID,
        *,
        terms: CommercialTerms,
        line_prices: Mapping[uuid.UUID, Decimal | None],
    ) -> POCommercial:
        await self.authz.require(
            context=context,
            action=COMMERCIAL_WRITE,
            resource_type=_PO_COMMERCIAL,
            resource_id=str(case_id),
        )
        await _require_in_workspace(self.cases, context, case_id, "PO case")
        current = await self.commercial.read(context, case_id)
        lines = {line.sku_id for line in current.lines}
        unknown = sorted(str(sku) for sku in line_prices if sku not in lines)
        if unknown:
            raise DomainError("SKU không phải dòng của PO này", details={"sku_ids": unknown})
        if terms.currency is None and any(p is not None for p in line_prices.values()):
            raise DomainError("đơn giá cần tiền tệ của PO", details={"field": "currency"})
        return await self.commercial.set_terms(
            context,
            case_id,
            terms,
            line_prices,
            audit=_audit(
                context,
                self.ids,
                self.clock,
                action=TERMS_SET,
                resource_type=_PO_COMMERCIAL,
                resource_id=case_id,
                details={"lines_priced": len(line_prices)},
            ),
        )


class PODocumentLookupPort(Protocol):
    async def get(
        self, context: AccessContext, document_id: CaseDocumentId
    ) -> CaseDocument | None: ...


@dataclass(frozen=True)
class RecordPOPayment:
    """A new version of the case's deposit or final payment."""

    cases: CaseLookupPort
    commercial: POCommercialRepositoryPort
    documents: PODocumentLookupPort
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(
        self,
        context: AccessContext,
        case_id: uuid.UUID,
        *,
        kind: PaymentKind,
        amount_value: Decimal | int | str,
        currency: str,
        due_date: date | None,
        paid_on: date | None,
        document_id: uuid.UUID | None,
    ) -> POPayment:
        await self.authz.require(
            context=context,
            action=COMMERCIAL_WRITE,
            resource_type=_PAYMENT,
            resource_id=str(case_id),
        )
        await _require_in_workspace(self.cases, context, case_id, "PO case")
        payment = NewPOPayment.of(
            id=self.ids.new_uuid(),
            po_case_id=case_id,
            kind=kind,
            amount_value=amount_value,
            currency=currency,
            due_date=due_date,
            paid_on=paid_on,
            document_id=document_id,
        )
        if document_id is not None:
            document = await self.documents.get(context, CaseDocumentId(document_id))
            if (
                document is None
                or document.case_kind is not CaseKind.PO
                or document.case_id != case_id
                or document.doc_type is not PAYMENT_DOCUMENT_TYPE[kind]
            ):
                raise DomainError(
                    "chứng từ không phải chứng từ thanh toán của PO này",
                    details={
                        "document_id": str(document_id),
                        "expected_doc_type": PAYMENT_DOCUMENT_TYPE[kind].value,
                    },
                )
        return await self.commercial.add_payment(
            context,
            payment,
            audit=_audit(
                context,
                self.ids,
                self.clock,
                action=PAYMENT_RECORDED,
                resource_type=_PAYMENT,
                resource_id=payment.id,
                details={"po_case_id": str(case_id), "kind": kind.value},
            ),
        )


# -------------------------------------------------------------- suppliers ---


class SupplierLookupPort(Protocol):
    async def supplier_workspace(
        self, context: AccessContext, supplier_id: uuid.UUID
    ) -> uuid.UUID | None: ...


@dataclass(frozen=True, slots=True)
class SupplierSummary:
    id: uuid.UUID
    name: str
    code: str | None


class SupplierDirectoryPort(Protocol):
    async def list_suppliers(self, context: AccessContext) -> list[SupplierSummary]: ...


class SupplierContactsPort(SupplierLookupPort, Protocol):
    async def latest_contact(
        self, context: AccessContext, supplier_id: uuid.UUID
    ) -> SupplierContact | None: ...

    async def add_contact(
        self, context: AccessContext, contact: NewSupplierContact, *, audit: AuditEvent
    ) -> SupplierContact: ...


class SupplierBankAccountsPort(SupplierLookupPort, Protocol):
    async def latest_bank_account(
        self, context: AccessContext, supplier_id: uuid.UUID
    ) -> SupplierBankAccount | None: ...

    async def add_bank_account(
        self, context: AccessContext, account: NewSupplierBankAccount, *, audit: AuditEvent
    ) -> SupplierBankAccount: ...


async def _require_supplier(
    lookup: SupplierLookupPort, context: AccessContext, supplier_id: uuid.UUID
) -> None:
    workspace = await lookup.supplier_workspace(context, supplier_id)
    if workspace is None or workspace != context.workspace_id:
        raise NotFoundError("supplier not found", details={"supplier_id": str(supplier_id)})


@dataclass(frozen=True)
class ListSuppliers:
    suppliers: SupplierDirectoryPort
    authz: AuthorizationPort

    async def handle(self, context: AccessContext) -> list[SupplierSummary]:
        await self.authz.require(context=context, action=PO_CASE_READ, resource_type=_SUPPLIER)
        return await self.suppliers.list_suppliers(context)


@dataclass(frozen=True, slots=True)
class ContactReading:
    contact: SupplierContact | None
    can_edit: bool


@dataclass(frozen=True)
class GetSupplierContact:
    contacts: SupplierContactsPort
    authz: AuthorizationPort

    async def handle(self, context: AccessContext, supplier_id: uuid.UUID) -> ContactReading:
        await self.authz.require(
            context=context,
            action=PO_CASE_READ,
            resource_type=_SUPPLIER,
            resource_id=str(supplier_id),
        )
        await _require_supplier(self.contacts, context, supplier_id)
        return ContactReading(
            contact=await self.contacts.latest_contact(context, supplier_id),
            can_edit=await allows(self.authz, context, COMMERCIAL_WRITE, _SUPPLIER),
        )


@dataclass(frozen=True)
class SaveSupplierContact:
    contacts: SupplierContactsPort
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(
        self,
        context: AccessContext,
        supplier_id: uuid.UUID,
        *,
        name: str,
        email: str | None,
        phone: str | None,
    ) -> SupplierContact:
        await self.authz.require(
            context=context,
            action=COMMERCIAL_WRITE,
            resource_type=_SUPPLIER,
            resource_id=str(supplier_id),
        )
        await _require_supplier(self.contacts, context, supplier_id)
        contact = NewSupplierContact.of(
            id=self.ids.new_uuid(), supplier_id=supplier_id, name=name, email=email, phone=phone
        )
        return await self.contacts.add_contact(
            context,
            contact,
            audit=_audit(
                context,
                self.ids,
                self.clock,
                action=CONTACT_SAVED,
                resource_type=_SUPPLIER,
                resource_id=supplier_id,
                details={"contact_id": str(contact.id)},
            ),
        )


@dataclass(frozen=True, slots=True)
class BankAccountReading:
    """None as `account` with `prices_visible` False is a redaction."""

    account: SupplierBankAccount | None
    prices_visible: bool
    can_edit: bool


@dataclass(frozen=True)
class GetSupplierBankAccount:
    accounts: SupplierBankAccountsPort
    authz: AuthorizationPort

    async def handle(self, context: AccessContext, supplier_id: uuid.UUID) -> BankAccountReading:
        await self.authz.require(
            context=context,
            action=PO_CASE_READ,
            resource_type=_SUPPLIER,
            resource_id=str(supplier_id),
        )
        await _require_supplier(self.accounts, context, supplier_id)
        visible = await allows(self.authz, context, COMMERCIAL_READ, _SUPPLIER)
        return BankAccountReading(
            account=await self.accounts.latest_bank_account(context, supplier_id)
            if visible
            else None,
            prices_visible=visible,
            can_edit=await allows(self.authz, context, COMMERCIAL_WRITE, _SUPPLIER),
        )


@dataclass(frozen=True)
class SaveSupplierBankAccount:
    accounts: SupplierBankAccountsPort
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(
        self,
        context: AccessContext,
        supplier_id: uuid.UUID,
        *,
        bank_name: str,
        account_number: str,
        account_holder: str,
    ) -> SupplierBankAccount:
        await self.authz.require(
            context=context,
            action=COMMERCIAL_WRITE,
            resource_type=_SUPPLIER,
            resource_id=str(supplier_id),
        )
        await _require_supplier(self.accounts, context, supplier_id)
        account = NewSupplierBankAccount.of(
            id=self.ids.new_uuid(),
            supplier_id=supplier_id,
            bank_name=bank_name,
            account_number=account_number,
            account_holder=account_holder,
        )
        return await self.accounts.add_bank_account(
            context,
            account,
            audit=_audit(
                context,
                self.ids,
                self.clock,
                action=BANK_ACCOUNT_SAVED,
                resource_type=_SUPPLIER,
                resource_id=supplier_id,
                details={"bank_account_id": str(account.id)},
            ),
        )
