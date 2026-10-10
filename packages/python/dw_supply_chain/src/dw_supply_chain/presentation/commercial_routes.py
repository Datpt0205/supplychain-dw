"""HTTP surface of commercial data and BM04 as fields (ADR 0026, E15; ticket
ai-automation/01). Decisions live in `application.commercial`.

`GET|POST /product-cases/{id}/profile` — the BM04 form: its tenant's schema,
the latest version and a new one. `GET|PUT /po-cases/{id}/commercial` — terms
and line prices; `POST /po-cases/{id}/payments`. `GET /suppliers`,
`GET|POST /suppliers/{id}/contact`, `GET|POST /suppliers/{id}/bank-account`.
`GET|PUT /bm04-schema` — the tenant's schema.

A price the caller may not read is `{"value": null, "redacted": true}`, never
a number: a 0 would read as a price. The handler has already emptied it; the
view only says why it is empty. Money travels as a decimal string.

No `from __future__ import annotations`, for the reason `routes.py` gives.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from dw_platform.application.access_context import AccessContext
from dw_supply_chain.application.commercial import (
    BankAccountReading,
    ContactReading,
    GetBm04Schema,
    GetPOCommercial,
    GetProductProfile,
    GetSupplierBankAccount,
    GetSupplierContact,
    ListSuppliers,
    POCommercialReading,
    ProfileReading,
    RecordPOPayment,
    SaveProductProfile,
    SaveSupplierBankAccount,
    SaveSupplierContact,
    SetBm04SchemaOverride,
    SetPOCommercialTerms,
)
from dw_supply_chain.bm04_schema import SupplyChainBm04Schema
from dw_supply_chain.domain.commercial import (
    CommercialTerms,
    Incoterm,
    PaymentKind,
    POPayment,
    ProfileCommercial,
    SupplierBankAccount,
    SupplierContact,
)
from dw_supply_chain.presentation.routes import SupportsIdempotentRecord

AccessContextResolver = Callable[..., object]
IdempotencyResolver = Callable[..., object]


class RedactableAmount(BaseModel):
    """A price or amount: its value, or null with `redacted` when the caller
    lacks `supply_chain.commercial.read`."""

    value: str | None
    redacted: bool


def _amount(value: Decimal | None, visible: bool) -> RedactableAmount:
    if not visible:
        return RedactableAmount(value=None, redacted=True)
    return RedactableAmount(value=None if value is None else str(value), redacted=False)


# ------------------------------------------------------------------ BM04 ----


class ProductProfileView(BaseModel):
    product_dev_case_id: uuid.UUID
    # None until the first save.
    version: int | None
    attributes: dict[str, Any]
    unit_price: RedactableAmount
    currency: str | None
    moq: int | None
    lead_time_days: int | None
    incoterm: Incoterm | None
    # The schema the latest version was filled against, and the one in force.
    schema_version: str | None
    bm04_schema: SupplyChainBm04Schema
    prices_visible: bool
    can_edit: bool
    can_edit_prices: bool
    created_by: uuid.UUID | None
    created_at: datetime | None


def _profile_view(case_id: uuid.UUID, reading: ProfileReading) -> ProductProfileView:
    profile = reading.profile
    commercial = None if profile is None else profile.commercial
    return ProductProfileView(
        product_dev_case_id=case_id,
        version=None if profile is None else profile.version,
        attributes={} if profile is None else profile.attributes,
        unit_price=_amount(
            None if commercial is None else commercial.unit_price, reading.prices_visible
        ),
        currency=None if commercial is None else commercial.currency,
        moq=None if commercial is None else commercial.moq,
        lead_time_days=None if commercial is None else commercial.lead_time_days,
        incoterm=None if commercial is None else commercial.incoterm,
        schema_version=None if profile is None else profile.schema_version,
        bm04_schema=reading.schema,
        prices_visible=reading.prices_visible,
        can_edit=reading.can_edit,
        can_edit_prices=reading.can_edit_prices,
        created_by=None if profile is None else profile.created_by,
        created_at=None if profile is None else profile.created_at,
    )


class ProfilePricesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_price: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=8)


class SaveProductProfileRequest(BaseModel):
    """`prices` absent keeps the last version's price and currency; present, it
    needs `supply_chain.commercial.write`."""

    model_config = ConfigDict(extra="forbid")

    attributes: dict[str, Any] = Field(default_factory=dict)
    moq: int | None = None
    lead_time_days: int | None = None
    incoterm: Incoterm | None = None
    prices: ProfilePricesRequest | None = None


# ------------------------------------------------------------- PO case ------


class PricedLineView(BaseModel):
    sku_id: uuid.UUID
    sku_code: str | None
    variant_label: str | None
    quantity: int | None
    unit_price: RedactableAmount
    line_total: RedactableAmount


class POPaymentView(BaseModel):
    id: uuid.UUID
    kind: PaymentKind
    version: int
    amount: RedactableAmount
    currency: str
    due_date: date | None
    paid_on: date | None
    document_id: uuid.UUID | None
    recorded_by: uuid.UUID
    recorded_at: datetime


def _payment_view(payment: POPayment, visible: bool) -> POPaymentView:
    return POPaymentView(
        id=payment.id,
        kind=payment.kind,
        version=payment.version,
        amount=_amount(payment.amount, visible),
        currency=payment.currency,
        due_date=payment.due_date,
        paid_on=payment.paid_on,
        document_id=payment.document_id,
        recorded_by=payment.recorded_by,
        recorded_at=payment.recorded_at,
    )


class POCommercialView(BaseModel):
    po_case_id: uuid.UUID
    currency: str | None
    incoterm: Incoterm | None
    payment_terms: str | None
    payment_terms_redacted: bool
    deposit_percent: RedactableAmount
    expected_delivery_date: date | None
    lines: list[PricedLineView]
    # Computed from the lines; unknown (null, not redacted) while a line has
    # no quantity or no price.
    order_total: RedactableAmount
    payments: list[POPaymentView]
    prices_visible: bool
    can_edit: bool


def _commercial_view(case_id: uuid.UUID, reading: POCommercialReading) -> POCommercialView:
    visible = reading.prices_visible
    commercial = reading.commercial
    terms = commercial.terms
    return POCommercialView(
        po_case_id=case_id,
        currency=terms.currency,
        incoterm=terms.incoterm,
        payment_terms=terms.payment_terms if visible else None,
        payment_terms_redacted=not visible,
        deposit_percent=_amount(terms.deposit_percent, visible),
        expected_delivery_date=terms.expected_delivery_date,
        lines=[
            PricedLineView(
                sku_id=line.sku_id,
                sku_code=line.sku_code,
                variant_label=line.variant_label,
                quantity=line.quantity,
                unit_price=_amount(line.unit_price, visible),
                line_total=_amount(line.line_total, visible),
            )
            for line in commercial.lines
        ],
        order_total=_amount(commercial.order_total, visible),
        payments=[_payment_view(p, visible) for p in commercial.payments],
        prices_visible=visible,
        can_edit=reading.can_edit,
    )


class LinePriceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku_id: uuid.UUID
    unit_price: Decimal | None = Field(default=None, ge=0)


class SetPOCommercialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str | None = Field(default=None, max_length=8)
    incoterm: Incoterm | None = None
    payment_terms: str | None = Field(default=None, max_length=500)
    deposit_percent: Decimal | None = Field(default=None, ge=0, le=100)
    expected_delivery_date: date | None = None
    line_prices: list[LinePriceRequest] = Field(default_factory=list, max_length=500)


class RecordPaymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: PaymentKind
    amount: Decimal = Field(gt=0)
    currency: str = Field(max_length=8)
    due_date: date | None = None
    paid_on: date | None = None
    document_id: uuid.UUID | None = None


# ------------------------------------------------------------ suppliers -----


class SupplierView(BaseModel):
    id: uuid.UUID
    name: str
    code: str | None


class SupplierContactView(BaseModel):
    supplier_id: uuid.UUID
    version: int | None
    name: str | None
    email: str | None
    phone: str | None
    can_edit: bool


def _contact_view(supplier_id: uuid.UUID, reading: ContactReading) -> SupplierContactView:
    contact: SupplierContact | None = reading.contact
    return SupplierContactView(
        supplier_id=supplier_id,
        version=None if contact is None else contact.version,
        name=None if contact is None else contact.name,
        email=None if contact is None else contact.email,
        phone=None if contact is None else contact.phone,
        can_edit=reading.can_edit,
    )


class SupplierContactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=40)


class SupplierBankAccountView(BaseModel):
    """Every field null with `redacted` when the caller lacks the commercial
    read scope."""

    supplier_id: uuid.UUID
    version: int | None
    bank_name: str | None
    account_number: str | None
    account_holder: str | None
    redacted: bool
    can_edit: bool


def _bank_view(supplier_id: uuid.UUID, reading: BankAccountReading) -> SupplierBankAccountView:
    account: SupplierBankAccount | None = reading.account
    return SupplierBankAccountView(
        supplier_id=supplier_id,
        version=None if account is None else account.version,
        bank_name=None if account is None else account.bank_name,
        account_number=None if account is None else account.account_number,
        account_holder=None if account is None else account.account_holder,
        redacted=not reading.prices_visible,
        can_edit=reading.can_edit,
    )


class SupplierBankAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bank_name: str = Field(min_length=1, max_length=200)
    account_number: str = Field(min_length=6, max_length=64)
    account_holder: str = Field(min_length=1, max_length=200)


@dataclass(frozen=True)
class CommercialHandlers:
    """What the composition root builds for this router."""

    get_profile: GetProductProfile
    save_profile: SaveProductProfile
    get_schema: GetBm04Schema
    set_schema: SetBm04SchemaOverride
    get_commercial: GetPOCommercial
    set_commercial: SetPOCommercialTerms
    record_payment: RecordPOPayment
    list_suppliers: ListSuppliers
    get_contact: GetSupplierContact
    save_contact: SaveSupplierContact
    get_bank_account: GetSupplierBankAccount
    save_bank_account: SaveSupplierBankAccount


def build_commercial_router(
    handlers: CommercialHandlers,
    *,
    resolve_access_context: AccessContextResolver,
    resolve_idempotency: IdempotencyResolver,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/supply-chain", tags=["supply_chain"])
    require_access_context = Annotated[AccessContext, Depends(resolve_access_context)]
    require_idempotency = Annotated[SupportsIdempotentRecord, Depends(resolve_idempotency)]
    h = handlers

    @router.get("/product-cases/{case_id}/profile", response_model=ProductProfileView)
    async def get_product_profile(
        case_id: uuid.UUID, context: require_access_context
    ) -> ProductProfileView:
        return _profile_view(case_id, await h.get_profile.handle(context, case_id))

    @router.post("/product-cases/{case_id}/profile", response_model=ProductProfileView)
    async def save_product_profile(
        case_id: uuid.UUID,
        body: SaveProductProfileRequest,
        context: require_access_context,
        idempotency: require_idempotency,
    ) -> ProductProfileView:
        prices = (
            None
            if body.prices is None
            else ProfileCommercial.of(
                unit_price=body.prices.unit_price,
                currency=body.prices.currency,
                moq=None,
                lead_time_days=None,
                incoterm=None,
            )
        )
        await h.save_profile.handle(
            context,
            case_id,
            attributes=body.attributes,
            planning=ProfileCommercial.of(
                unit_price=None,
                currency=None,
                moq=body.moq,
                lead_time_days=body.lead_time_days,
                incoterm=body.incoterm,
            ),
            prices=prices,
        )
        view = _profile_view(case_id, await h.get_profile.handle(context, case_id))
        return await idempotency.record(view)

    @router.get("/bm04-schema", response_model=SupplyChainBm04Schema)
    async def get_bm04_schema(context: require_access_context) -> SupplyChainBm04Schema:
        return await h.get_schema.handle(context)

    @router.put("/bm04-schema", response_model=SupplyChainBm04Schema)
    async def set_bm04_schema(
        body: SupplyChainBm04Schema, context: require_access_context
    ) -> SupplyChainBm04Schema:
        """Replaces the tenant's BM04 schema, whole. No `Idempotency-Key`, as for
        the other policies' `PUT`."""
        await h.set_schema.handle(context, body)
        return body

    @router.get("/po-cases/{case_id}/commercial", response_model=POCommercialView)
    async def get_po_commercial(
        case_id: uuid.UUID, context: require_access_context
    ) -> POCommercialView:
        return _commercial_view(case_id, await h.get_commercial.handle(context, case_id))

    @router.put("/po-cases/{case_id}/commercial", response_model=POCommercialView)
    async def set_po_commercial(
        case_id: uuid.UUID,
        body: SetPOCommercialRequest,
        context: require_access_context,
        idempotency: require_idempotency,
    ) -> POCommercialView:
        await h.set_commercial.handle(
            context,
            case_id,
            terms=CommercialTerms.of(
                currency=body.currency,
                incoterm=body.incoterm,
                payment_terms=body.payment_terms,
                deposit_percent=body.deposit_percent,
                expected_delivery_date=body.expected_delivery_date,
            ),
            line_prices={line.sku_id: line.unit_price for line in body.line_prices},
        )
        view = _commercial_view(case_id, await h.get_commercial.handle(context, case_id))
        return await idempotency.record(view)

    @router.post("/po-cases/{case_id}/payments", response_model=POPaymentView, status_code=201)
    async def record_po_payment(
        case_id: uuid.UUID,
        body: RecordPaymentRequest,
        context: require_access_context,
        idempotency: require_idempotency,
    ) -> POPaymentView:
        payment = await h.record_payment.handle(
            context,
            case_id,
            kind=body.kind,
            amount_value=body.amount,
            currency=body.currency,
            due_date=body.due_date,
            paid_on=body.paid_on,
            document_id=body.document_id,
        )
        # Only a holder of the write scope gets here; the write scope is a
        # commercial scope, so the amount it just wrote is shown back to it.
        return await idempotency.record(_payment_view(payment, True), status_code=201)

    @router.get("/suppliers", response_model=list[SupplierView])
    async def list_suppliers(context: require_access_context) -> list[SupplierView]:
        return [
            SupplierView(id=s.id, name=s.name, code=s.code)
            for s in await h.list_suppliers.handle(context)
        ]

    @router.get("/suppliers/{supplier_id}/contact", response_model=SupplierContactView)
    async def get_supplier_contact(
        supplier_id: uuid.UUID, context: require_access_context
    ) -> SupplierContactView:
        return _contact_view(supplier_id, await h.get_contact.handle(context, supplier_id))

    @router.post("/suppliers/{supplier_id}/contact", response_model=SupplierContactView)
    async def save_supplier_contact(
        supplier_id: uuid.UUID,
        body: SupplierContactRequest,
        context: require_access_context,
        idempotency: require_idempotency,
    ) -> SupplierContactView:
        await h.save_contact.handle(
            context, supplier_id, name=body.name, email=body.email, phone=body.phone
        )
        view = _contact_view(supplier_id, await h.get_contact.handle(context, supplier_id))
        return await idempotency.record(view)

    @router.get("/suppliers/{supplier_id}/bank-account", response_model=SupplierBankAccountView)
    async def get_supplier_bank_account(
        supplier_id: uuid.UUID, context: require_access_context
    ) -> SupplierBankAccountView:
        return _bank_view(supplier_id, await h.get_bank_account.handle(context, supplier_id))

    @router.post("/suppliers/{supplier_id}/bank-account", response_model=SupplierBankAccountView)
    async def save_supplier_bank_account(
        supplier_id: uuid.UUID,
        body: SupplierBankAccountRequest,
        context: require_access_context,
        idempotency: require_idempotency,
    ) -> SupplierBankAccountView:
        await h.save_bank_account.handle(
            context,
            supplier_id,
            bank_name=body.bank_name,
            account_number=body.account_number,
            account_holder=body.account_holder,
        )
        view = _bank_view(supplier_id, await h.get_bank_account.handle(context, supplier_id))
        return await idempotency.record(view)

    return router
