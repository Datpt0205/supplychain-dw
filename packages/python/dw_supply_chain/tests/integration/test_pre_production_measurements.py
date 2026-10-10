"""Integration: the pre-production test's values and the drafted record filed
(2bb10bdd4420; ticket ai-automation/17, item 2).

What only the real database can show:

- `pre_production_measurements` is narrowed by tenant AND workspace (RLS
  FORCE): a neighbour workspace and another tenant read no value of the case,
  and a value written on another workspace's case is refused (the composite
  FK, or the policy's WITH CHECK);
- a value, a criterion key and an attempt outside their CHECKs are refused.

Owed when written (2026-10-10): not run, no Docker on the machine that wrote it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import replace

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls

from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.adapters.persistence.pre_production_measurements import (
    SqlPreProductionMeasurements,
)
from dw_supply_chain.application.po_case_audit import po_case_audit
from dw_supply_chain.application.pre_production_test import NewPreProductionMeasurement
from dw_supply_chain.domain.po_case import CaseState, POCase, POCaseId

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


async def _case(sessions: async_sessionmaker[AsyncSession], context: AccessContext) -> POCase:
    case = POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        po_reference=f"PO-{uuid.uuid4().hex[:8]}",
        supplier_name="Công ty Gia dụng Minh Phát",
        state=CaseState.PRE_PRODUCTION,
    )
    await SqlPOCaseRepository(sessions).add(context, case)
    return case


def _value(case: POCase) -> NewPreProductionMeasurement:
    return NewPreProductionMeasurement(
        id=uuid.uuid4(),
        po_case_id=case.id.value,
        attempt=1,
        criterion="base_thickness",
        value="2.5",
        note=None,
    )


def _audit(context: AccessContext, case: POCase) -> AuditEvent:
    return po_case_audit(
        context, Uuid4Generator(), SystemClock(), case.id, "pre_production_measurement.recorded", {}
    )


async def test_values_stay_in_their_workspace(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    mine = _context(tenant, workspace)
    case = await _case(sessions, mine)
    store = SqlPreProductionMeasurements(sessions)
    await store.add(mine, _value(case), audit=_audit(mine, case))
    assert [m.value for m in await store.for_attempt(mine, case.id.value, 1)] == ["2.5"]
    for other in (_context(tenant, uuid.uuid4()), _context(uuid.uuid4(), workspace)):
        assert await store.for_attempt(other, case.id.value, 1) == []
        with pytest.raises((IntegrityError, DBAPIError)):
            await store.add(other, _value(case), audit=_audit(other, case))


async def test_values_outside_their_checks_are_refused(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _case(sessions, context)
    store = SqlPreProductionMeasurements(sessions)
    for bad in (
        replace(_value(case), value=" "),
        replace(_value(case), criterion="Độ dày"),
        replace(_value(case), attempt=0),
    ):
        with pytest.raises(IntegrityError):
            await store.add(context, bad, audit=_audit(context, case))
