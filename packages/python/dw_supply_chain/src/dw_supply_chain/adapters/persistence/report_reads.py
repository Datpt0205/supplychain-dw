"""SQL reads of the weekly report, the supplier scorecard and the AI
acceptance metrics (ticket ai-automation/20). Read only.

Every statement runs under `tenant_session` (tenant AND workspace bound) and
names both as a second layer. The weekly reads are bounded by the week; the
scorecard reads the workspace's cases, its sample rounds and the warehouse's
latest count of each line; the acceptance read is bounded by when a draft's
lineage began. No price column is read.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.adapters.persistence import tables
from dw_supply_chain.domain.reports import CaseMove, DraftVersion, SupplierRecord

_PO_CLOSED = ("completed", "cancelled")
_PRODUCT_CLOSED = ("ordered", "cancelled")


def _mine(table: sa.Table, context: AccessContext) -> tuple[sa.ColumnElement[bool], ...]:
    return (table.c.tenant_id == context.tenant_id, table.c.workspace_id == context.workspace_id)


@dataclass(frozen=True)
class SqlReportReads:
    """Implements `ReportReadsPort`."""

    session_factory: async_sessionmaker[AsyncSession]

    async def product_moves(
        self, context: AccessContext, start: datetime, end: datetime
    ) -> list[CaseMove]:
        t, c = tables.product_dev_case_state_transitions, tables.product_dev_cases
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            rows = (
                await session.execute(
                    sa.select(c.c.proposal_code, t.c.action, t.c.to_state)
                    .join(c, c.c.id == t.c.product_dev_case_id)
                    .where(*_mine(t, context), t.c.occurred_at >= start, t.c.occurred_at < end)
                    .order_by(t.c.occurred_at)
                    .limit(10000)
                )
            ).all()
        return [CaseMove(r.proposal_code, r.to_state, r.action) for r in rows]

    async def po_moves(
        self, context: AccessContext, start: datetime, end: datetime
    ) -> list[CaseMove]:
        t, c = tables.po_case_state_transitions, tables.po_cases
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            rows = (
                await session.execute(
                    sa.select(c.c.po_reference, c.c.id, t.c.to_state)
                    .join(c, c.c.id == t.c.po_case_id)
                    .where(*_mine(t, context), t.c.occurred_at >= start, t.c.occurred_at < end)
                    .order_by(t.c.occurred_at)
                    .limit(10000)
                )
            ).all()
        return [CaseMove(r.po_reference or str(r.id), r.to_state) for r in rows]

    async def po_created(self, context: AccessContext, start: datetime, end: datetime) -> list[str]:
        c = tables.po_cases
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            rows = (
                await session.execute(
                    sa.select(c.c.po_reference, c.c.id)
                    .where(*_mine(c, context), c.c.created_at >= start, c.c.created_at < end)
                    .order_by(c.c.created_at)
                    .limit(10000)
                )
            ).all()
        return [r.po_reference or str(r.id) for r in rows]

    async def open_states(
        self, context: AccessContext
    ) -> tuple[Mapping[str, int], Mapping[str, int]]:
        p, c = tables.product_dev_cases, tables.po_cases
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            products = (
                await session.execute(
                    sa.select(p.c.state, sa.func.count())
                    .where(*_mine(p, context), p.c.state.not_in(_PRODUCT_CLOSED))
                    .group_by(p.c.state)
                )
            ).all()
            pos = (
                await session.execute(
                    sa.select(c.c.state, sa.func.count())
                    .where(*_mine(c, context), c.c.state.not_in(_PO_CLOSED))
                    .group_by(c.c.state)
                )
            ).all()
        return {s: int(n) for s, n in products}, {s: int(n) for s, n in pos}

    async def supplier_records(self, context: AccessContext) -> list[SupplierRecord]:
        c, t = tables.po_cases, tables.po_case_state_transitions
        p, r = tables.product_dev_cases, tables.product_sample_rounds
        lr = tables.po_case_line_receipts
        records: dict[str, SupplierRecord] = {}

        def bump(name: str | None, **counts: int) -> None:
            if not name:
                return
            current = records.get(name) or SupplierRecord(name)
            records[name] = replace(
                current, **{k: getattr(current, k) + v for k, v in counts.items()}
            )

        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            for name, state, n in (
                await session.execute(
                    sa.select(c.c.supplier_name, c.c.state, sa.func.count())
                    .where(*_mine(c, context))
                    .group_by(c.c.supplier_name, c.c.state)
                )
            ).all():
                bump(
                    name,
                    po_total=int(n),
                    po_open=int(n) if state not in _PO_CLOSED else 0,
                    po_completed=int(n) if state == "completed" else 0,
                )
            for name, n in (
                await session.execute(
                    sa.select(c.c.supplier_name, sa.func.count())
                    .join(c, c.c.id == t.c.po_case_id)
                    .where(*_mine(t, context), t.c.to_state == "rework")
                    .group_by(c.c.supplier_name)
                )
            ).all():
                bump(name, qc_reworks=int(n))
            for name, result, n in (
                await session.execute(
                    sa.select(p.c.supplier_name, r.c.result, sa.func.count())
                    .join(p, p.c.id == r.c.product_dev_case_id)
                    .where(*_mine(r, context), r.c.result.is_not(None))
                    .group_by(p.c.supplier_name, r.c.result)
                )
            ).all():
                key = {
                    "passed": "rounds_passed",
                    "needs_revision": "rounds_revised",
                    "rejected": "rounds_rejected",
                }.get(result)
                if key is not None:
                    bump(name, **{key: int(n)})
            newest = (
                sa.select(
                    lr.c.po_case_id,
                    lr.c.sku_id,
                    sa.func.max(lr.c.version).label("version"),
                )
                .where(*_mine(lr, context))
                .group_by(lr.c.po_case_id, lr.c.sku_id)
                .subquery()
            )
            for name, n in (
                await session.execute(
                    sa.select(c.c.supplier_name, sa.func.count())
                    .select_from(lr)
                    .join(
                        newest,
                        sa.and_(
                            newest.c.po_case_id == lr.c.po_case_id,
                            newest.c.sku_id == lr.c.sku_id,
                            newest.c.version == lr.c.version,
                        ),
                    )
                    .join(c, c.c.id == lr.c.po_case_id)
                    .where(
                        *_mine(lr, context),
                        lr.c.counted != sa.func.coalesce(lr.c.shipped, lr.c.ordered),
                    )
                    .group_by(c.c.supplier_name)
                )
            ).all():
                bump(name, lines_differing=int(n))
        return list(records.values())

    async def draft_versions(self, context: AccessContext, since: datetime) -> list[DraftVersion]:
        d, x = tables.document_drafts, tables.document_draft_decisions
        began = (
            sa.select(d.c.lineage_id)
            .where(*_mine(d, context), d.c.version == 1, d.c.created_at >= since)
            .scalar_subquery()
        )
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            rows = (
                await session.execute(
                    sa.select(d.c.doc_type, d.c.lineage_id, d.c.version, x.c.decision)
                    .select_from(d)
                    .outerjoin(x, x.c.draft_id == d.c.id)
                    .where(*_mine(d, context), d.c.lineage_id.in_(began))
                    .limit(50000)
                )
            ).all()
        return [
            DraftVersion(r.doc_type, str(r.lineage_id), int(r.version), r.decision) for r in rows
        ]


__all__ = ["SqlReportReads"]
