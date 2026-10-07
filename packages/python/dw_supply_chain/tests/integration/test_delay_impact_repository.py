"""Integration: `DelayImpactAnalysis` persists under RLS, cascades with its
case and its triggering update, and its JSONB columns round-trip faithfully.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls

from dw_kernel.ids import TenantId, WorkspaceId
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.adapters.persistence.delay_impact_repository import (
    SqlDelayImpactAnalysisRepository,
)
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.adapters.persistence.supplier_update_repository import (
    SqlSupplierUpdateRepository,
)
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

TENANT_A = uuid.UUID(int=0xA00)
WORKSPACE_A = uuid.UUID(int=0xA01)
TENANT_B = uuid.UUID(int=0xB00)
WORKSPACE_B = uuid.UUID(int=0xB01)


@pytest.fixture
async def sessions(db_urls: DatabaseUrls) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


def _context(*, tenant: uuid.UUID = TENANT_A, workspace: uuid.UUID = WORKSPACE_A) -> AccessContext:
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=frozenset(
            {
                "supply_chain.po_case.write",
                "supply_chain.supplier_update.write",
            }
        ),
        plan_id="professional",
    )


def _case(*, tenant: uuid.UUID = TENANT_A, workspace: uuid.UUID = WORKSPACE_A) -> POCase:
    return POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(tenant),
        workspace_id=WorkspaceId(workspace),
        po_reference=f"PO-{uuid.uuid4().hex[:8]}",
        supplier_name="Elmich Co.",
        state=CaseState.PRODUCTION,
    )


def _supplier_update(
    case: POCase, *, tenant: uuid.UUID = TENANT_A, workspace: uuid.UUID = WORKSPACE_A
) -> SupplierUpdate:
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


def _analysis(
    case: POCase,
    update: SupplierUpdate,
    *,
    tenant: uuid.UUID = TENANT_A,
    workspace: uuid.UUID = WORKSPACE_A,
) -> DelayImpactAnalysis:
    return DelayImpactAnalysis(
        id=DelayImpactAnalysisId(uuid.uuid4()),
        tenant_id=TenantId(tenant),
        workspace_id=WorkspaceId(workspace),
        po_case_id=case.id,
        supplier_update_id=update.id,
        delay_days=7,
        impacted_milestones=(
            ImpactedMilestoneEstimate(milestone=CaseState.QC, estimated_delay_days=7),
            ImpactedMilestoneEstimate(milestone=CaseState.IN_TRANSIT, estimated_delay_days=7),
        ),
        extraction=DelayImpactExtraction(
            assumptions=["no further change to the production schedule"],
            mitigation_options=[
                MitigationOption(description="split shipment", tradeoff="higher freight cost"),
                MitigationOption(description="expedite QC", tradeoff="needs supplier agreement"),
            ],
        ),
    )


async def _seed(
    sessions: async_sessionmaker[AsyncSession], context: AccessContext
) -> tuple[POCase, SupplierUpdate]:
    case = _case(tenant=context.tenant_id, workspace=context.workspace_id)
    await SqlPOCaseRepository(sessions).add(context, case)
    update = _supplier_update(case, tenant=context.tenant_id, workspace=context.workspace_id)
    await SqlSupplierUpdateRepository(sessions).add(context, update)
    return case, update


async def test_add_and_list_round_trips_the_jsonb_columns_faithfully(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    context = _context()
    case, update = await _seed(sessions, context)
    repo = SqlDelayImpactAnalysisRepository(sessions)
    analysis = _analysis(case, update, tenant=context.tenant_id, workspace=context.workspace_id)

    await repo.add(context, analysis)
    listed = await repo.list_for_case(context, case.id)

    assert len(listed) == 1
    fetched = listed[0]
    assert fetched.id == analysis.id
    assert fetched.supplier_update_id == update.id
    assert fetched.delay_days == 7
    assert fetched.impacted_milestones == analysis.impacted_milestones
    assert fetched.extraction.assumptions == analysis.extraction.assumptions
    assert fetched.extraction.mitigation_options == analysis.extraction.mitigation_options
    assert fetched.created_at is not None


async def test_list_returns_newest_first(sessions: async_sessionmaker[AsyncSession]) -> None:
    context = _context()
    case, update = await _seed(sessions, context)
    repo = SqlDelayImpactAnalysisRepository(sessions)

    first = _analysis(case, update, tenant=context.tenant_id, workspace=context.workspace_id)
    await repo.add(context, first)
    second = _analysis(case, update, tenant=context.tenant_id, workspace=context.workspace_id)
    await repo.add(context, second)

    listed = await repo.list_for_case(context, case.id)
    assert [a.id for a in listed] == [second.id, first.id]


async def test_deleting_the_case_cascades_to_its_analyses(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    context = _context()
    case, update = await _seed(sessions, context)
    repo = SqlDelayImpactAnalysisRepository(sessions)
    await repo.add(
        context, _analysis(case, update, tenant=context.tenant_id, workspace=context.workspace_id)
    )

    async with sessions() as session, session.begin():
        await session.execute(
            text(
                "SELECT set_config('app.tenant_id', :t, true),"
                " set_config('app.workspace_id', :w, true)"
            ),
            {"t": str(context.tenant_id), "w": str(context.workspace_id)},
        )
        await session.execute(
            text("DELETE FROM supply_chain.po_cases WHERE id = :id"), {"id": str(case.id)}
        )

    assert await repo.list_for_case(context, case.id) == []


async def test_another_tenant_cannot_see_the_analysis(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    context_a = _context(tenant=TENANT_A, workspace=WORKSPACE_A)
    case, update = await _seed(sessions, context_a)
    repo = SqlDelayImpactAnalysisRepository(sessions)
    await repo.add(context_a, _analysis(case, update, tenant=TENANT_A, workspace=WORKSPACE_A))

    listed_by_b = await repo.list_for_case(
        _context(tenant=TENANT_B, workspace=WORKSPACE_B), case.id
    )
    assert listed_by_b == []


async def test_rls_hides_the_row_even_from_a_query_with_no_tenant_filter(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    context = _context(tenant=TENANT_A, workspace=WORKSPACE_A)
    case, update = await _seed(sessions, context)
    repo = SqlDelayImpactAnalysisRepository(sessions)
    analysis = _analysis(case, update, tenant=TENANT_A, workspace=WORKSPACE_A)
    await repo.add(context, analysis)

    async with sessions() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(TENANT_B)}
        )
        rows = (
            await session.execute(
                text("SELECT id FROM supply_chain.delay_impact_analyses WHERE id = :id"),
                {"id": str(analysis.id)},
            )
        ).all()

    assert rows == []
