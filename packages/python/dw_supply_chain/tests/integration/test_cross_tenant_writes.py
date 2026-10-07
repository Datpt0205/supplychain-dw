"""Integration: a tenant cannot write onto another tenant's PO case.

Two layers, each tested on its own so that removing either turns a test red:

- the handlers read the case through the RLS-scoped repository first, so
  tenant B naming tenant A's case id gets `NotFoundError`, no model call and
  no row;
- the database refuses the row anyway: every child's FK carries the tenant
  (`bc3f0c1279fd`), and Postgres checks an FK without row security, so a
  tenant-only FK would have proved only that the case exists somewhere.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.ports import ModelRequest
from dw_kernel.errors import NotFoundError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_supply_chain.adapters.persistence.delay_impact_repository import (
    SqlDelayImpactAnalysisRepository,
)
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.adapters.persistence.supplier_update_repository import (
    SqlSupplierUpdateRepository,
)
from dw_supply_chain.application.handlers import AnalyzeDelayImpact, SubmitSupplierUpdate
from dw_supply_chain.domain.delay_impact import (
    DelayImpactAnalysis,
    DelayImpactAnalysisId,
    DelayImpactExtraction,
    ImpactedMilestoneEstimate,
    MitigationOption,
)
from dw_supply_chain.domain.po_case import CaseState, POCase, POCaseId
from dw_supply_chain.domain.supplier_update import (
    SupplierEventType,
    SupplierUpdate,
    SupplierUpdateExtraction,
    SupplierUpdateId,
)

pytestmark = pytest.mark.integration

TENANT_A, WORKSPACE_A = uuid.UUID(int=0xC1A00), uuid.UUID(int=0xC1A01)
TENANT_B, WORKSPACE_B = uuid.UUID(int=0xC1B00), uuid.UUID(int=0xC1B01)


class _NeverCalledGateway:
    """A model call on a case the caller may not see is already a leak."""

    def __init__(self) -> None:
        self.calls: list[ModelRequest] = []

    async def generate_structured(
        self, request: ModelRequest, output_type: type[Any], *, run_context: RunContext
    ) -> Any:
        self.calls.append(request)
        raise AssertionError("the model was called for another tenant's case")


@pytest.fixture
async def sessions(db_urls: DatabaseUrls) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def _count(db_urls: DatabaseUrls, table: str, case: POCase) -> int:
    # The migrator bypasses RLS, so this counts rows of EVERY tenant: a row
    # written under tenant B would be invisible to tenant A's own read.
    engine = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            found = await conn.scalar(
                sa.text(f"SELECT count(*) FROM supply_chain.{table} WHERE po_case_id = :id"),
                {"id": str(case.id)},
            )
    finally:
        await engine.dispose()
    return int(found or 0)


def _context(tenant: uuid.UUID, workspace: uuid.UUID) -> AccessContext:
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=frozenset(
            {
                "supply_chain.po_case.write",
                "supply_chain.supplier_update.write",
                "supply_chain.delay_impact.write",
            }
        ),
        plan_id="professional",
    )


def _update(case: POCase, tenant: uuid.UUID, workspace: uuid.UUID) -> SupplierUpdate:
    return SupplierUpdate(
        id=SupplierUpdateId(uuid.uuid4()),
        tenant_id=TenantId(tenant),
        workspace_id=WorkspaceId(workspace),
        po_case_id=case.id,
        raw_text="we will be delayed by 7 days",
        extraction=SupplierUpdateExtraction(
            event_type=SupplierEventType.PRODUCTION_DELAY,
            delay_days=7,
            reason="component shortage",
            proposed_action="split shipment",
            confidence=0.95,
            source_ref="delayed by 7 days",
        ),
        requires_confirmation=False,
    )


async def _tenant_a_case_with_update(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[POCase, SupplierUpdate]:
    context = _context(TENANT_A, WORKSPACE_A)
    case = POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(TENANT_A),
        workspace_id=WorkspaceId(WORKSPACE_A),
        po_reference=f"PO-{uuid.uuid4().hex[:8]}",
        supplier_name="Elmich Co.",
        state=CaseState.PRODUCTION,
    )
    await SqlPOCaseRepository(sessions).add(context, case)
    update = _update(case, TENANT_A, WORKSPACE_A)
    await SqlSupplierUpdateRepository(sessions).add(context, update)
    return case, update


async def test_another_tenant_cannot_submit_a_supplier_update_onto_the_case(
    sessions: async_sessionmaker[AsyncSession], db_urls: DatabaseUrls
) -> None:
    case, _ = await _tenant_a_case_with_update(sessions)
    gateway = _NeverCalledGateway()
    handler = SubmitSupplierUpdate(
        po_case_repo=SqlPOCaseRepository(sessions),
        supplier_update_repo=SqlSupplierUpdateRepository(sessions),
        gateway=gateway,
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
        clock=SystemClock(),
    )

    with pytest.raises(NotFoundError):
        await handler.handle(
            _context(TENANT_B, WORKSPACE_B), po_case_id=case.id, raw_text="delayed by 9 days"
        )

    assert gateway.calls == []
    assert await _count(db_urls, "supplier_updates", case) == 1  # tenant A's own, only


async def test_another_tenant_cannot_analyze_the_case(
    sessions: async_sessionmaker[AsyncSession], db_urls: DatabaseUrls
) -> None:
    case, update = await _tenant_a_case_with_update(sessions)
    gateway = _NeverCalledGateway()
    handler = AnalyzeDelayImpact(
        po_case_repo=SqlPOCaseRepository(sessions),
        supplier_update_repo=SqlSupplierUpdateRepository(sessions),
        delay_impact_repo=SqlDelayImpactAnalysisRepository(sessions),
        gateway=gateway,
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
        clock=SystemClock(),
    )

    with pytest.raises(NotFoundError):
        await handler.handle(
            _context(TENANT_B, WORKSPACE_B), po_case_id=case.id, supplier_update_id=update.id
        )

    assert gateway.calls == []
    assert await _count(db_urls, "delay_impact_analyses", case) == 0


async def test_the_database_refuses_a_supplier_update_onto_another_tenants_case(
    sessions: async_sessionmaker[AsyncSession], db_urls: DatabaseUrls
) -> None:
    # The repository straight, skipping the handler's read: what a future
    # handler that forgot the read, or a worker writing directly, would do.
    case, _ = await _tenant_a_case_with_update(sessions)
    stray = _update(case, TENANT_B, WORKSPACE_B)

    with pytest.raises(IntegrityError, match="fk_supplier_updates_tenant_id_po_cases"):
        await SqlSupplierUpdateRepository(sessions).add(_context(TENANT_B, WORKSPACE_B), stray)

    assert await _count(db_urls, "supplier_updates", case) == 1


async def test_the_database_refuses_an_analysis_onto_another_tenants_case(
    sessions: async_sessionmaker[AsyncSession], db_urls: DatabaseUrls
) -> None:
    case, update = await _tenant_a_case_with_update(sessions)
    stray = DelayImpactAnalysis(
        id=DelayImpactAnalysisId(uuid.uuid4()),
        tenant_id=TenantId(TENANT_B),
        workspace_id=WorkspaceId(WORKSPACE_B),
        po_case_id=case.id,
        supplier_update_id=update.id,
        delay_days=7,
        impacted_milestones=(
            ImpactedMilestoneEstimate(milestone=CaseState.QC, estimated_delay_days=7),
        ),
        extraction=DelayImpactExtraction(
            assumptions=["x"],
            mitigation_options=[MitigationOption(description="split", tradeoff="cost")],
        ),
    )

    with pytest.raises(IntegrityError, match="fk_delay_impact_analyses_tenant_id_"):
        await SqlDelayImpactAnalysisRepository(sessions).add(_context(TENANT_B, WORKSPACE_B), stray)

    assert await _count(db_urls, "delay_impact_analyses", case) == 0


@pytest.mark.parametrize(
    ("table", "columns", "values"),
    [
        (
            "po_case_state_transitions",
            "id, tenant_id, workspace_id, po_case_id, from_state, to_state",
            ":id, :tenant, :workspace, :case, 'production', 'qc'",
        ),
        (
            "follow_ups",
            "id, tenant_id, workspace_id, po_case_id, kind, episode, days, recipient_scopes",
            ":id, :tenant, :workspace, :case, 'update_reminder', 'e1', 1,"
            " '[\"supply_chain.duty.ordering\"]'::jsonb",
        ),
    ],
)
async def test_the_database_refuses_a_child_row_onto_another_tenants_case(
    sessions: async_sessionmaker[AsyncSession],
    db_urls: DatabaseUrls,
    table: str,
    columns: str,
    values: str,
) -> None:
    case, _ = await _tenant_a_case_with_update(sessions)

    with pytest.raises(IntegrityError, match=f"fk_{table}_tenant_id_po_cases"):
        async with sessions() as session, session.begin():
            for setting, value in (("app.tenant_id", TENANT_B), ("app.workspace_id", WORKSPACE_B)):
                await session.execute(
                    sa.text("SELECT set_config(:k, :v, true)"), {"k": setting, "v": str(value)}
                )
            await session.execute(
                sa.text(f"INSERT INTO supply_chain.{table} ({columns}) VALUES ({values})"),
                {
                    "id": str(uuid.uuid4()),
                    "tenant": str(TENANT_B),
                    "workspace": str(WORKSPACE_B),
                    "case": str(case.id),
                },
            )

    assert await _count(db_urls, table, case) == 0
