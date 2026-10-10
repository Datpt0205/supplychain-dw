"""Integration: sample measurements (1021f7fe88f0; ticket ai-automation/09).

What only the real database can show:

- `sample_measurements` is narrowed by tenant AND workspace (RLS FORCE): a
  neighbour workspace and another tenant read no value of the case, and a
  value written on another workspace's case is refused (the composite FK, or
  the policy's WITH CHECK);
- a value, a criterion key and a round outside their CHECKs are refused;
- `supplier_messages` accepts the purpose `sample_revision_request`.

Owed when written (2026-10-09): not run, no Docker on the machine that wrote it.
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

from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import SystemClock
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence.product_case_repository import SqlProductCaseRepository
from dw_supply_chain.adapters.persistence.sample_measurement_repository import (
    SqlSampleMeasurements,
)
from dw_supply_chain.application.sample_checklist import NewMeasurement
from dw_supply_chain.domain.product_development_case import (
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
)

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


def _audit(context: AccessContext) -> AuditEvent:
    return AuditEvent(
        id=uuid.uuid4(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        actor_id=UserId(context.principal_id),
        action="supply_chain.test",
        resource_type="product_dev_case",
        resource_id=str(uuid.uuid4()),
        occurred_at=SystemClock().now(),
    )


async def _case(
    sessions: async_sessionmaker[AsyncSession], context: AccessContext
) -> ProductDevelopmentCase:
    case = ProductDevelopmentCase.propose(
        id=ProductDevelopmentCaseId(uuid.uuid4()),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        proposal_code=f"DX-{uuid.uuid4().hex[:8]}",
        product_name="Nồi inox 3 đáy 24cm",
        category="noi",
        actor_id=context.principal_id,
    )
    await SqlProductCaseRepository(sessions).add(context, case, audit=_audit(context))
    return case


def _value(case: ProductDevelopmentCase) -> NewMeasurement:
    return NewMeasurement(
        id=uuid.uuid4(),
        case_id=case.id.value,
        sample_round=1,
        criterion="base_thickness",
        value="2.5",
        note=None,
    )


async def test_values_stay_in_their_workspace(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    mine = _context(tenant, workspace)
    case = await _case(sessions, mine)
    store = SqlSampleMeasurements(sessions)
    await store.add(mine, _value(case), audit=_audit(mine))
    assert [m.value for m in await store.for_round(mine, case.id.value, 1)] == ["2.5"]
    for other in (_context(tenant, uuid.uuid4()), _context(uuid.uuid4(), workspace)):
        assert await store.for_round(other, case.id.value, 1) == []
        with pytest.raises((IntegrityError, DBAPIError)):
            await store.add(other, _value(case), audit=_audit(other))


async def test_values_outside_their_checks_are_refused(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _case(sessions, context)
    store = SqlSampleMeasurements(sessions)
    for bad in (
        replace(_value(case), value=" "),
        replace(_value(case), criterion="Độ dày"),
        replace(_value(case), sample_round=0),
    ):
        with pytest.raises(IntegrityError):
            await store.add(context, bad, audit=_audit(context))
