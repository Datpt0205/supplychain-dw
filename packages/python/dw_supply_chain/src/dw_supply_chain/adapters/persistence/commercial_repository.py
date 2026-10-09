"""SQL persistence for commercial data and BM04 profiles (82221a867e62, ADR 0026).

The only module that reads or writes a price column. Every statement runs under
`tenant_session` (tenant AND workspace bound; all four new tables and
`po_cases` are narrowed by both) and names the tenant and workspace itself as a
second layer. Append-only tables compute their next version inside the INSERT;
the UNIQUE of each refuses a concurrent duplicate, which becomes a
`ConflictError` by constraint name, and the client retries.

Audit events commit with their row.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_kernel.errors import ConflictError
from dw_platform.adapters.persistence.repositories import SqlAuditRepository
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence import tables
from dw_supply_chain.application.commercial import SupplierSummary
from dw_supply_chain.domain.commercial import (
    CommercialTerms,
    Incoterm,
    NewPOPayment,
    NewProductProfile,
    NewSupplierBankAccount,
    NewSupplierContact,
    PaymentKind,
    POCommercial,
    POPayment,
    PricedLine,
    ProductProfile,
    ProfileCommercial,
    SupplierBankAccount,
    SupplierContact,
)

_pp = tables.product_profiles
_pc = tables.po_cases
_lines = tables.po_case_lines
_pay = tables.po_payments
_s = tables.suppliers
_sc = tables.supplier_contacts
_sb = tables.supplier_bank_accounts

# Each append-only table's version guard, by name.
VERSION_CONSTRAINTS = frozenset(
    {
        "uq_product_profiles_tenant_id_product_dev_case_id_version",
        "uq_po_payments_tenant_id_po_case_id_kind_version",
        "uq_supplier_contacts_tenant_id_supplier_id_version",
        "uq_supplier_bank_accounts_tenant_id_supplier_id_version",
    }
)


def _scope(context: AccessContext) -> TenantScope:
    return TenantScope.from_access_context(context)


def _mine(table: sa.Table, context: AccessContext) -> tuple[sa.ColumnElement[bool], ...]:
    return (table.c.tenant_id == context.tenant_id, table.c.workspace_id == context.workspace_id)


def _incoterm(value: str | None) -> Incoterm | None:
    return None if value is None else Incoterm(value)


def _profile(row: Row[Any]) -> ProductProfile:
    m = row._mapping
    return ProductProfile(
        id=m[_pp.c.id],
        product_dev_case_id=m[_pp.c.product_dev_case_id],
        version=m[_pp.c.version],
        commercial=ProfileCommercial(
            unit_price=m[_pp.c.unit_price],
            currency=m[_pp.c.currency],
            moq=m[_pp.c.moq],
            lead_time_days=m[_pp.c.lead_time_days],
            incoterm=_incoterm(m[_pp.c.incoterm]),
        ),
        attributes=dict(m[_pp.c.attributes]),
        schema_version=m[_pp.c.schema_version],
        created_by=m[_pp.c.created_by],
        created_at=m[_pp.c.created_at],
    )


def _payment(row: Row[Any]) -> POPayment:
    m = row._mapping
    return POPayment(
        id=m[_pay.c.id],
        po_case_id=m[_pay.c.po_case_id],
        kind=PaymentKind(m[_pay.c.kind]),
        version=m[_pay.c.version],
        amount=m[_pay.c.amount],
        currency=m[_pay.c.currency],
        due_date=m[_pay.c.due_date],
        paid_on=m[_pay.c.paid_on],
        document_id=m[_pay.c.document_id],
        recorded_by=m[_pay.c.recorded_by],
        recorded_at=m[_pay.c.recorded_at],
    )


def _next_version(table: sa.Table, *where: sa.ColumnElement[bool]) -> sa.ScalarSelect[int]:
    return (
        sa.select(sa.func.coalesce(sa.func.max(table.c.version), 0) + 1)
        .where(*where)
        .scalar_subquery()
    )


def _version_conflict(exc: IntegrityError, what: str) -> ConflictError | None:
    if any(name in str(exc.orig) for name in VERSION_CONSTRAINTS):
        return ConflictError(f"another save of this {what} took the same version; try again")
    return None


@dataclass(frozen=True)
class SqlProductProfileRepository:
    """Implements `ProductProfileRepositoryPort`."""

    session_factory: async_sessionmaker[AsyncSession]

    async def latest(self, context: AccessContext, case_id: uuid.UUID) -> ProductProfile | None:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            row = (
                await session.execute(
                    sa.select(_pp)
                    .where(*_mine(_pp, context), _pp.c.product_dev_case_id == case_id)
                    .order_by(_pp.c.version.desc())
                    .limit(1)
                )
            ).first()
        return None if row is None else _profile(row)

    async def add(
        self, context: AccessContext, profile: NewProductProfile, *, audit: AuditEvent
    ) -> ProductProfile:
        commercial = profile.commercial
        try:
            async with tenant_session(self.session_factory, _scope(context)) as session:
                row = (
                    await session.execute(
                        sa.insert(_pp)
                        .values(
                            id=profile.id,
                            tenant_id=context.tenant_id,
                            workspace_id=context.workspace_id,
                            product_dev_case_id=profile.product_dev_case_id,
                            version=_next_version(
                                _pp,
                                _pp.c.tenant_id == context.tenant_id,
                                _pp.c.product_dev_case_id == profile.product_dev_case_id,
                            ),
                            unit_price=commercial.unit_price,
                            currency=commercial.currency,
                            moq=commercial.moq,
                            lead_time_days=commercial.lead_time_days,
                            incoterm=None
                            if commercial.incoterm is None
                            else commercial.incoterm.value,
                            attributes=profile.attributes,
                            schema_version=profile.schema_version,
                            created_by=context.principal_id,
                        )
                        .returning(_pp)
                    )
                ).one()
                await SqlAuditRepository(session).append(audit)
        except IntegrityError as exc:
            conflict = _version_conflict(exc, "BM04")
            if conflict is not None:
                raise conflict from exc
            raise
        return _profile(row)


async def _read_commercial(
    session: AsyncSession, context: AccessContext, case_id: uuid.UUID
) -> POCommercial:
    terms_row = (
        await session.execute(
            sa.select(
                _pc.c.currency,
                _pc.c.incoterm,
                _pc.c.payment_terms,
                _pc.c.deposit_percent,
                _pc.c.expected_delivery_date,
            ).where(*_mine(_pc, context), _pc.c.id == case_id)
        )
    ).first()
    skus = tables.skus
    line_rows = (
        await session.execute(
            sa.select(
                _lines.c.sku_id,
                _lines.c.quantity,
                _lines.c.unit_price,
                skus.c.sku_code,
                skus.c.variant_label,
            )
            .select_from(
                _lines.outerjoin(
                    skus,
                    sa.and_(skus.c.tenant_id == _lines.c.tenant_id, skus.c.id == _lines.c.sku_id),
                )
            )
            .where(*_mine(_lines, context), _lines.c.po_case_id == case_id)
            .order_by(_lines.c.created_at.asc(), skus.c.sku_code.asc())
        )
    ).all()
    payment_rows = (
        await session.execute(
            sa.select(_pay)
            .where(*_mine(_pay, context), _pay.c.po_case_id == case_id)
            .order_by(_pay.c.kind, _pay.c.version)
        )
    ).all()
    terms = (
        CommercialTerms()
        if terms_row is None
        else CommercialTerms(
            currency=terms_row.currency,
            incoterm=_incoterm(terms_row.incoterm),
            payment_terms=terms_row.payment_terms,
            deposit_percent=terms_row.deposit_percent,
            expected_delivery_date=terms_row.expected_delivery_date,
        )
    )
    return POCommercial(
        terms=terms,
        lines=tuple(
            PricedLine(
                sku_id=r.sku_id,
                quantity=r.quantity,
                unit_price=r.unit_price,
                sku_code=r.sku_code,
                variant_label=r.variant_label,
            )
            for r in line_rows
        ),
        payments=tuple(_payment(r) for r in payment_rows),
    )


@dataclass(frozen=True)
class SqlPOCommercialRepository:
    """Implements `POCommercialRepositoryPort`."""

    session_factory: async_sessionmaker[AsyncSession]

    async def read(self, context: AccessContext, case_id: uuid.UUID) -> POCommercial:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            return await _read_commercial(session, context, case_id)

    async def set_terms(
        self,
        context: AccessContext,
        case_id: uuid.UUID,
        terms: CommercialTerms,
        line_prices: Mapping[uuid.UUID, Decimal | None],
        *,
        audit: AuditEvent,
    ) -> POCommercial:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            await session.execute(
                sa.update(_pc)
                .where(*_mine(_pc, context), _pc.c.id == case_id)
                .values(
                    currency=terms.currency,
                    incoterm=None if terms.incoterm is None else terms.incoterm.value,
                    payment_terms=terms.payment_terms,
                    deposit_percent=terms.deposit_percent,
                    expected_delivery_date=terms.expected_delivery_date,
                )
            )
            for sku_id, unit_price in line_prices.items():
                await session.execute(
                    sa.update(_lines)
                    .where(
                        *_mine(_lines, context),
                        _lines.c.po_case_id == case_id,
                        _lines.c.sku_id == sku_id,
                    )
                    .values(unit_price=unit_price)
                )
            await SqlAuditRepository(session).append(audit)
            return await _read_commercial(session, context, case_id)

    async def add_payment(
        self, context: AccessContext, payment: NewPOPayment, *, audit: AuditEvent
    ) -> POPayment:
        try:
            async with tenant_session(self.session_factory, _scope(context)) as session:
                row = (
                    await session.execute(
                        sa.insert(_pay)
                        .values(
                            id=payment.id,
                            tenant_id=context.tenant_id,
                            workspace_id=context.workspace_id,
                            po_case_id=payment.po_case_id,
                            kind=payment.kind.value,
                            version=_next_version(
                                _pay,
                                _pay.c.tenant_id == context.tenant_id,
                                _pay.c.po_case_id == payment.po_case_id,
                                _pay.c.kind == payment.kind.value,
                            ),
                            amount=payment.amount,
                            currency=payment.currency,
                            due_date=payment.due_date,
                            paid_on=payment.paid_on,
                            document_id=payment.document_id,
                            recorded_by=context.principal_id,
                        )
                        .returning(_pay)
                    )
                ).one()
                await SqlAuditRepository(session).append(audit)
        except IntegrityError as exc:
            conflict = _version_conflict(exc, "payment")
            if conflict is not None:
                raise conflict from exc
            raise
        return _payment(row)


def _contact(row: Row[Any]) -> SupplierContact:
    m = row._mapping
    return SupplierContact(
        id=m[_sc.c.id],
        supplier_id=m[_sc.c.supplier_id],
        version=m[_sc.c.version],
        name=m[_sc.c.name],
        email=m[_sc.c.email],
        phone=m[_sc.c.phone],
        created_by=m[_sc.c.created_by],
        created_at=m[_sc.c.created_at],
    )


def _account(row: Row[Any]) -> SupplierBankAccount:
    m = row._mapping
    return SupplierBankAccount(
        id=m[_sb.c.id],
        supplier_id=m[_sb.c.supplier_id],
        version=m[_sb.c.version],
        bank_name=m[_sb.c.bank_name],
        account_number=m[_sb.c.account_number],
        account_holder=m[_sb.c.account_holder],
        created_by=m[_sb.c.created_by],
        created_at=m[_sb.c.created_at],
    )


@dataclass(frozen=True)
class SqlSupplierRecords:
    """Implements `SupplierDirectoryPort`, `SupplierContactsPort` and
    `SupplierBankAccountsPort`."""

    session_factory: async_sessionmaker[AsyncSession]

    async def supplier_workspace(
        self, context: AccessContext, supplier_id: uuid.UUID
    ) -> uuid.UUID | None:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            found: uuid.UUID | None = await session.scalar(
                sa.select(_s.c.workspace_id).where(
                    _s.c.tenant_id == context.tenant_id, _s.c.id == supplier_id
                )
            )
        return found

    async def list_suppliers(self, context: AccessContext) -> list[SupplierSummary]:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            rows = (
                await session.execute(
                    sa.select(_s.c.id, _s.c.name, _s.c.code)
                    .where(*_mine(_s, context))
                    .order_by(_s.c.normalized_name)
                    .limit(1000)
                )
            ).all()
        return [SupplierSummary(id=r.id, name=r.name, code=r.code) for r in rows]

    async def _latest(
        self, context: AccessContext, table: sa.Table, supplier_id: uuid.UUID
    ) -> Row[Any] | None:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            return (
                await session.execute(
                    sa.select(table)
                    .where(*_mine(table, context), table.c.supplier_id == supplier_id)
                    .order_by(table.c.version.desc())
                    .limit(1)
                )
            ).first()

    async def _insert(
        self,
        context: AccessContext,
        table: sa.Table,
        supplier_id: uuid.UUID,
        values: dict[str, Any],
        audit: AuditEvent,
        what: str,
    ) -> Row[Any]:
        try:
            async with tenant_session(self.session_factory, _scope(context)) as session:
                row = (
                    await session.execute(
                        sa.insert(table)
                        .values(
                            tenant_id=context.tenant_id,
                            workspace_id=context.workspace_id,
                            supplier_id=supplier_id,
                            version=_next_version(
                                table,
                                table.c.tenant_id == context.tenant_id,
                                table.c.supplier_id == supplier_id,
                            ),
                            created_by=context.principal_id,
                            **values,
                        )
                        .returning(table)
                    )
                ).one()
                await SqlAuditRepository(session).append(audit)
        except IntegrityError as exc:
            conflict = _version_conflict(exc, what)
            if conflict is not None:
                raise conflict from exc
            raise
        return row

    async def latest_contact(
        self, context: AccessContext, supplier_id: uuid.UUID
    ) -> SupplierContact | None:
        row = await self._latest(context, _sc, supplier_id)
        return None if row is None else _contact(row)

    async def add_contact(
        self, context: AccessContext, contact: NewSupplierContact, *, audit: AuditEvent
    ) -> SupplierContact:
        row = await self._insert(
            context,
            _sc,
            contact.supplier_id,
            {
                "id": contact.id,
                "name": contact.name,
                "email": contact.email,
                "phone": contact.phone,
            },
            audit,
            "contact",
        )
        return _contact(row)

    async def latest_bank_account(
        self, context: AccessContext, supplier_id: uuid.UUID
    ) -> SupplierBankAccount | None:
        row = await self._latest(context, _sb, supplier_id)
        return None if row is None else _account(row)

    async def add_bank_account(
        self, context: AccessContext, account: NewSupplierBankAccount, *, audit: AuditEvent
    ) -> SupplierBankAccount:
        row = await self._insert(
            context,
            _sb,
            account.supplier_id,
            {
                "id": account.id,
                "bank_name": account.bank_name,
                "account_number": account.account_number,
                "account_holder": account.account_holder,
            },
            audit,
            "bank account",
        )
        return _account(row)
