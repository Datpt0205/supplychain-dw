"""The warehouse's counts at step 17 (ticket ai-automation/18).

`insert_receipts` writes them in the approving step's transaction
(`SqlPOStepOutcomes`); `SqlLineReceipts` reads them back under the caller's
RLS for the discrepancy report and the claim letter. Append-only: `dw_app`
holds SELECT and INSERT only, and a second count of a line is its next
version, never an update.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.adapters.persistence import tables
from dw_supply_chain.domain.receipt_check import LineCount, NewLineReceipt

_r = tables.po_case_line_receipts


async def insert_receipts(
    session: AsyncSession, context: AccessContext, receipts: Sequence[NewLineReceipt]
) -> None:
    for receipt in receipts:
        line = receipt.line
        prior = (
            sa.select(sa.func.coalesce(sa.func.max(_r.c.version), 0) + 1)
            .where(
                _r.c.tenant_id == context.tenant_id,
                _r.c.po_case_id == receipt.po_case_id,
                _r.c.sku_id == line.sku_id,
            )
            .scalar_subquery()
        )
        await session.execute(
            sa.insert(_r).values(
                id=receipt.id,
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                po_case_id=receipt.po_case_id,
                sku_id=line.sku_id,
                version=prior,
                sku_code=line.sku_code,
                ordered=line.ordered,
                shipped=line.shipped,
                counted=line.counted,
                document_id=receipt.document_id,
                recorded_by=context.principal_id,
            )
        )


def _latest() -> sa.Subquery:
    """Each line's newest count."""
    return (
        sa.select(_r)
        .distinct(_r.c.po_case_id, _r.c.sku_id)
        .order_by(_r.c.po_case_id, _r.c.sku_id, _r.c.version.desc())
        .subquery()
    )


@dataclass(frozen=True)
class SqlLineReceipts:
    """Implements `LineReceiptsPort`."""

    session_factory: async_sessionmaker[AsyncSession]

    async def for_case(self, context: AccessContext, case_id: uuid.UUID) -> list[LineCount]:
        latest = _latest()
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            rows = (
                await session.execute(
                    sa.select(latest)
                    .where(
                        latest.c.po_case_id == case_id,
                        latest.c.workspace_id == context.workspace_id,
                    )
                    .order_by(latest.c.sku_code)
                )
            ).all()
        return [
            LineCount(
                sku_id=row.sku_id,
                sku_code=row.sku_code,
                ordered=row.ordered,
                shipped=row.shipped,
                counted=row.counted,
            )
            for row in rows
        ]

    async def discrepant_cases(self, context: AccessContext, *, since: datetime) -> list[uuid.UUID]:
        latest = _latest()
        held = sa.func.coalesce(latest.c.shipped, latest.c.ordered)
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            found = (
                await session.execute(
                    sa.select(latest.c.po_case_id)
                    .where(
                        latest.c.workspace_id == context.workspace_id,
                        latest.c.recorded_at >= since,
                        latest.c.counted.is_distinct_from(held),
                    )
                    .distinct()
                    .order_by(latest.c.po_case_id)
                )
            ).scalars()
            return list(found)


__all__ = ["SqlLineReceipts", "insert_receipts"]
