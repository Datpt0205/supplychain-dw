"""SQL persistence of the one-time import (ADR 0027; ticket onboarding/01).

`SqlSupplierImport` finds a workspace's supplier by code or by name (the
database's own `normalize_supplier_name`, never a Python copy of it), creates
one, or gives a supplier that has none its code (`dw_app` holds UPDATE on that
one column, and the statement only touches a NULL one). `SqlCatalogue` reads
and adds catalogue rows; the UNIQUEs decide what is already there. Every
statement runs under `tenant_session` (tenant AND workspace) and names both as
a second layer; each write and its audit event are one transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_kernel.errors import ConflictError
from dw_platform.adapters.persistence.repositories import SqlAuditRepository
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence import tables
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.adapters.persistence.product_case_repository import SqlProductCaseRepository
from dw_supply_chain.application.data_import import (
    CatalogueItem,
    KnownProductCase,
    KnownSupplier,
    NewCatalogueItem,
)
from dw_supply_chain.domain.po_case import POCase
from dw_supply_chain.domain.product_development_case import ProductDevelopmentCase

_s = tables.suppliers
_c = tables.catalogue_items
_SUPPLIER_CODE = "uq_suppliers_tenant_id_workspace_id_code"
CATALOGUE_PAIR = "uq_catalogue_items_tenant_id_workspace_id_item_code_sku_code"
CATALOGUE_SKU = "uq_catalogue_items_tenant_id_workspace_id_sku_code"


def _scope(context: AccessContext) -> TenantScope:
    return TenantScope.from_access_context(context)


def _mine(table: sa.Table, context: AccessContext) -> tuple[sa.ColumnElement[bool], ...]:
    return (table.c.tenant_id == context.tenant_id, table.c.workspace_id == context.workspace_id)


@dataclass(frozen=True)
class SqlSupplierImport:
    """Implements `SupplierImportPort`."""

    session_factory: async_sessionmaker[AsyncSession]

    async def _one(
        self, context: AccessContext, *where: sa.ColumnElement[bool]
    ) -> KnownSupplier | None:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            row = (
                await session.execute(
                    sa.select(_s.c.id, _s.c.name, _s.c.code).where(*_mine(_s, context), *where)
                )
            ).first()
        return None if row is None else KnownSupplier(id=row.id, name=row.name, code=row.code)

    async def by_code(self, context: AccessContext, code: str) -> KnownSupplier | None:
        return await self._one(context, _s.c.code == code)

    async def by_name(self, context: AccessContext, name: str) -> KnownSupplier | None:
        return await self._one(
            context, _s.c.normalized_name == sa.func.supply_chain.normalize_supplier_name(name)
        )

    async def create(
        self,
        context: AccessContext,
        *,
        supplier_id: uuid.UUID,
        name: str,
        code: str,
        audit: AuditEvent,
    ) -> bool:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            created = await session.scalar(
                pg_insert(_s)
                .values(
                    id=supplier_id,
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    name=name.strip(),
                    code=code,
                )
                .on_conflict_do_nothing()
                .returning(_s.c.id)
            )
            if created is None:
                return False
            await SqlAuditRepository(session).append(audit)
        return True

    async def assign_code(
        self, context: AccessContext, *, supplier_id: uuid.UUID, code: str, audit: AuditEvent
    ) -> bool:
        try:
            async with tenant_session(self.session_factory, _scope(context)) as session:
                updated = await session.scalar(
                    sa.update(_s)
                    .where(*_mine(_s, context), _s.c.id == supplier_id, _s.c.code.is_(None))
                    .values(code=code)
                    .returning(_s.c.id)
                )
                if updated is None:
                    return False
                await SqlAuditRepository(session).append(audit)
        except IntegrityError as exc:
            if _SUPPLIER_CODE in str(exc.orig):
                return False
            raise
        return True


@dataclass(frozen=True)
class SqlCatalogue:
    """Implements `CatalogueImportPort`."""

    session_factory: async_sessionmaker[AsyncSession]

    async def matching(
        self, context: AccessContext, item_codes: Sequence[str], sku_codes: Sequence[str]
    ) -> list[CatalogueItem]:
        if not item_codes and not sku_codes:
            return []
        async with tenant_session(self.session_factory, _scope(context)) as session:
            rows = (
                await session.execute(
                    sa.select(_c.c.item_code, _c.c.sku_code, _c.c.name, _c.c.category).where(
                        *_mine(_c, context),
                        sa.or_(_c.c.item_code.in_(item_codes), _c.c.sku_code.in_(sku_codes)),
                    )
                )
            ).all()
        return [
            CatalogueItem(
                item_code=r.item_code, sku_code=r.sku_code, name=r.name, category=r.category
            )
            for r in rows
        ]

    async def add(
        self, context: AccessContext, item: NewCatalogueItem, *, audit: AuditEvent
    ) -> bool:
        try:
            async with tenant_session(self.session_factory, _scope(context)) as session:
                created = await session.scalar(
                    pg_insert(_c)
                    .values(
                        id=item.id,
                        tenant_id=context.tenant_id,
                        workspace_id=context.workspace_id,
                        item_code=item.item_code,
                        sku_code=item.sku_code,
                        name=item.name,
                        category=item.category,
                        imported_by=context.principal_id,
                    )
                    .on_conflict_do_nothing(constraint=CATALOGUE_PAIR)
                    .returning(_c.c.id)
                )
                if created is None:
                    return False
                await SqlAuditRepository(session).append(audit)
        except IntegrityError as exc:
            if CATALOGUE_SKU in str(exc.orig):
                raise ConflictError(
                    "SKU đã có dưới mã hàng khác trong danh mục",
                    details={"sku_code": item.sku_code, "constraint": CATALOGUE_SKU},
                ) from exc
            raise
        return True


__all__ = ["CATALOGUE_PAIR", "CATALOGUE_SKU", "SqlCatalogue", "SqlSupplierImport"]


@dataclass(frozen=True)
class SqlOpenCaseImport:
    """Implements `OpenCaseImportPort` (ticket onboarding/02): the lookups by
    key under the caller's RLS, and the writes through the cases' own
    repositories, which write the row, its one `import` history row and the
    audit in one transaction."""

    session_factory: async_sessionmaker[AsyncSession]

    async def product_cases(
        self, context: AccessContext, codes: Sequence[str]
    ) -> Mapping[str, KnownProductCase]:
        if not codes:
            return {}
        p = tables.product_dev_cases
        async with tenant_session(self.session_factory, _scope(context)) as session:
            rows = (
                await session.execute(
                    sa.select(p.c.id, p.c.proposal_code, p.c.category, p.c.pic_user_id).where(
                        *_mine(p, context), p.c.proposal_code.in_(list(codes))
                    )
                )
            ).all()
        return {r.proposal_code: KnownProductCase(r.id, r.category, r.pic_user_id) for r in rows}

    async def po_references(self, context: AccessContext, refs: Sequence[str]) -> frozenset[str]:
        if not refs:
            return frozenset()
        c = tables.po_cases
        async with tenant_session(self.session_factory, _scope(context)) as session:
            found = (
                await session.scalars(
                    sa.select(c.c.po_reference).where(
                        *_mine(c, context), c.c.po_reference.in_(list(refs))
                    )
                )
            ).all()
        return frozenset(r for r in found if r is not None)

    async def add_product_case(
        self, context: AccessContext, case: ProductDevelopmentCase, *, audit: AuditEvent
    ) -> None:
        await SqlProductCaseRepository(self.session_factory).add(context, case, audit=audit)

    async def add_po_case(
        self, context: AccessContext, case: POCase, *, entered_at: datetime, audit: AuditEvent
    ) -> None:
        await SqlPOCaseRepository(self.session_factory).add_imported(
            context, case, entered_at=entered_at, audit=audit
        )
