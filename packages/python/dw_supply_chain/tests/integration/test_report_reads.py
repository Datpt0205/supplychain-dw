"""Integration: the report reads (ticket ai-automation/20) under RLS.

What only the real database can show: a PO case and its history written in
one workspace are counted there, and nowhere else (another workspace of the
same tenant, another tenant); the scorecard groups by supplier; no read
needs a privilege `dw_app` lacks.

Owed when written (2026-10-10): not run, no Docker on the machine that wrote it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls

from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.adapters.persistence.report_reads import SqlReportReads
from dw_supply_chain.application.po_case_audit import po_case_audit
from dw_supply_chain.domain.case_import import imported_po_case
from dw_supply_chain.domain.po_case import CaseState, OrderKind, POCaseId

pytestmark = pytest.mark.integration


@pytest.fixture
async def sessions(db_urls: DatabaseUrls) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


def _context(tenant: uuid.UUID, workspace: uuid.UUID) -> AccessContext:
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=frozenset(),
        plan_id="professional",
    )


async def test_a_workspaces_cases_are_counted_there_and_nowhere_else(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    mine = _context(tenant, workspace)
    now = datetime.now(UTC)
    case = imported_po_case(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(tenant),
        workspace_id=WorkspaceId(workspace),
        po_reference=f"PO-{uuid.uuid4().hex[:8]}",
        supplier_name="Minh Phát",
        state=CaseState.REWORK,
        order_kind=OrderKind.REORDER,
        product_dev_case_id=None,
        pic_user_id=None,
        category=None,
        created_at=now - timedelta(hours=2),
    )
    await SqlPOCaseRepository(sessions).add_imported(
        mine,
        case,
        entered_at=now - timedelta(hours=1),
        audit=po_case_audit(mine, Uuid4Generator(), SystemClock(), case.id, "imported", {}),
    )
    reads = SqlReportReads(sessions)
    start, end = now - timedelta(days=1), now + timedelta(days=1)
    moves = await reads.po_moves(mine, start, end)
    assert [(m.reference, m.to_state) for m in moves] == [(case.po_reference, "rework")]
    _, pos = await reads.open_states(mine)
    assert pos == {"rework": 1}
    (record,) = await reads.supplier_records(mine)
    assert (record.supplier, record.po_total, record.qc_reworks) == ("Minh Phát", 1, 1)
    for other in (_context(tenant, uuid.uuid4()), _context(uuid.uuid4(), workspace)):
        assert await reads.po_moves(other, start, end) == []
        assert await reads.po_created(other, start, end) == []
        assert await reads.supplier_records(other) == []
        assert await reads.open_states(other) == ({}, {})
