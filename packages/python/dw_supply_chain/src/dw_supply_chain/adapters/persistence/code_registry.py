"""The codes a workspace already holds (ticket ai-automation/13; ADR 0018,
ADR 0027): the application's item codes and SKUs, and the imported catalogue.

Read under `tenant_session` (tenant AND workspace) and naming both as a
second layer. A code another workspace of the tenant issued is not seen here;
`uq_item_codes_tenant_id_code` and `uq_skus_tenant_id_sku_code`, tenant-wide,
still refuse it when it is written (409 naming the code).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.adapters.persistence import tables
from dw_supply_chain.domain.item_coding import Holder, TakenCodes

_i = tables.item_codes
_s = tables.skus
_c = tables.catalogue_items


def _mine(table: sa.Table, context: AccessContext) -> tuple[sa.ColumnElement[bool], ...]:
    return (table.c.tenant_id == context.tenant_id, table.c.workspace_id == context.workspace_id)


@dataclass(frozen=True)
class SqlCodeRegistry:
    """Implements `CodeRegistryPort`."""

    session_factory: async_sessionmaker[AsyncSession]

    async def item_codes_with_prefix(self, context: AccessContext, prefix: str) -> list[str]:
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            app = await session.scalars(
                sa.select(_i.c.code).where(
                    *_mine(_i, context), _i.c.code.startswith(prefix, autoescape=True)
                )
            )
            catalogue = await session.scalars(
                sa.select(_c.c.item_code)
                .where(*_mine(_c, context), _c.c.item_code.startswith(prefix, autoescape=True))
                .distinct()
            )
            return [*app, *catalogue]

    async def taken(
        self,
        context: AccessContext,
        item_codes: Sequence[str],
        sku_codes: Sequence[str],
        *,
        except_case: uuid.UUID,
    ) -> TakenCodes:
        items: dict[str, set[str]] = {}
        skus: dict[str, set[str]] = {}
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            if item_codes:
                for code in await session.scalars(
                    sa.select(_i.c.code).where(
                        *_mine(_i, context),
                        _i.c.code.in_(item_codes),
                        _i.c.product_dev_case_id != except_case,
                    )
                ):
                    items.setdefault(code, set()).add(Holder.APP)
                for code in await session.scalars(
                    sa.select(_c.c.item_code).where(
                        *_mine(_c, context), _c.c.item_code.in_(item_codes)
                    )
                ):
                    items.setdefault(code, set()).add(Holder.CATALOGUE)
            if sku_codes:
                for code in await session.scalars(
                    sa.select(_s.c.sku_code).where(
                        *_mine(_s, context),
                        _s.c.sku_code.in_(sku_codes),
                        _s.c.product_dev_case_id != except_case,
                    )
                ):
                    skus.setdefault(code, set()).add(Holder.APP)
                for code in await session.scalars(
                    sa.select(_c.c.sku_code).where(
                        *_mine(_c, context), _c.c.sku_code.in_(sku_codes)
                    )
                ):
                    if code is not None:
                        skus.setdefault(code, set()).add(Holder.CATALOGUE)
        return TakenCodes(
            item_codes={c: tuple(sorted(h)) for c, h in items.items()},
            sku_codes={c: tuple(sorted(h)) for c, h in skus.items()},
        )


__all__ = ["SqlCodeRegistry"]
