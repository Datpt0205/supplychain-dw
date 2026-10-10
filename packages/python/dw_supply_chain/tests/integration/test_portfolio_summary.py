"""Integration: the Control Tower summary, composed over the real repositories.

`test_handlers.py` (unit) proves the handler's own counting against fakes
that scope by tenant because they were written to. What only a real
database can show: the whole composition — `list_active`, both bulk
timestamp reads and the summary over them — sees one tenant's cases and
nothing else, because RLS says so, not because a fake was kind.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls

from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.ports import FixedClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.adapters.persistence.supplier_update_repository import (
    SqlSupplierUpdateRepository,
)
from dw_supply_chain.application import handlers
from dw_supply_chain.application.handlers import GetPortfolioSummary
from dw_supply_chain.domain.po_case import CaseState, POCase, POCaseId
from dw_supply_chain.domain.supplier_update import (
    SupplierEventType,
    SupplierUpdate,
    SupplierUpdateExtraction,
    SupplierUpdateId,
)
from dw_supply_chain.sla_policy import (
    ProductCategory,
    SLAConfirmationStatus,
    SLAMilestone,
    SupplierUpdateCadence,
    SupplyChainSLAPolicy,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def sessions(db_urls: DatabaseUrls) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def counted(
    db_urls: DatabaseUrls,
) -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], list[int]]]:
    """Sessions whose every statement's bound-parameter count is recorded."""
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    bound: list[int] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _count(
        conn: object, cursor: object, statement: str, parameters: object, *args: object
    ) -> None:
        bound.append(len(parameters) if isinstance(parameters, tuple | list | dict) else 0)

    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), bound
    await engine.dispose()


class _NoOverrides:
    """No tenant has an SLA override here — the policy is not what this
    file tests; resolving one is covered by the handler's own unit tests."""

    async def get(self, context: AccessContext, policy_id: str) -> dict[str, object] | None:
        return None

    async def put(
        self,
        context: AccessContext,
        policy_id: str,
        content: Mapping[str, object],
        *,
        audit: AuditEvent,
    ) -> None:
        raise NotImplementedError("not exercised by the portfolio summary")


def _context(tenant: uuid.UUID, workspace: uuid.UUID) -> AccessContext:
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=frozenset({"supply_chain.po_case.read", "supply_chain.po_case.write"}),
        plan_id="professional",
    )


def _case(context: AccessContext, supplier_name: str) -> POCase:
    return POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        po_reference=f"PO-{uuid.uuid4().hex[:8]}",
        supplier_name=supplier_name,
    )


def _update(context: AccessContext, case: POCase) -> SupplierUpdate:
    return SupplierUpdate(
        id=SupplierUpdateId(uuid.uuid4()),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        po_case_id=case.id,
        raw_text="deposit received, starting soon",
        extraction=SupplierUpdateExtraction(
            event_type=SupplierEventType.DEPOSIT_CONFIRMATION,
            reason="deposit received",
            proposed_action="none",
            confidence=0.95,
            source_ref="deposit received",
        ),
        requires_confirmation=False,
    )


def _handler(sessions: async_sessionmaker[AsyncSession], *, now: datetime) -> GetPortfolioSummary:
    return GetPortfolioSummary(
        po_case_repo=SqlPOCaseRepository(sessions),
        supplier_update_repo=SqlSupplierUpdateRepository(sessions),
        policy_override_repo=_NoOverrides(),
        platform_default_policy=SupplyChainSLAPolicy(
            schema_version="2.0",
            policy_id="supply_chain_sla",
            policy_version="2.0.0",
            categories=(ProductCategory(key="noi", label="Nồi"),),
            default={
                "deposit": SLAMilestone(duration="2d", status=SLAConfirmationStatus.CONFIRMED)
            },
            supplier_update=SupplierUpdateCadence(reminder_after="5d", escalation_after="10d"),
        ),
        authz=ScopeAuthorizationService(),
        clock=FixedClock(now),
    )


async def test_portfolio_summary_counts_only_the_callers_tenant(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    mine = _context(uuid.uuid4(), uuid.uuid4())
    other = _context(uuid.uuid4(), uuid.uuid4())
    cases = SqlPOCaseRepository(sessions)
    updates = SqlSupplierUpdateRepository(sessions)

    waiting = _case(mine, "Mine Co.")
    await cases.add(mine, waiting)
    waiting.request_deposit()
    await cases.save(mine, waiting)
    await updates.add(mine, _update(mine, waiting))
    await cases.add(mine, _case(mine, "Mine Co."))

    theirs = _case(other, "Theirs Co.")
    await cases.add(other, theirs)
    theirs.request_deposit()
    await cases.save(other, theirs)

    # Three days on: past the 2-day deposit SLA, still inside the five-day
    # missing-update reminder window for every case.
    now = datetime.now(UTC) + timedelta(days=3)
    summary = await _handler(sessions, now=now).handle(mine)

    assert summary.active_case_count == 2
    assert summary.sla_breached_count == 1
    assert [(row.state, row.case_count) for row in summary.by_state] == [
        (CaseState.PO_CREATED, 1),
        (CaseState.WAITING_DEPOSIT, 1),
    ]
    assert [row.supplier_name for row in summary.by_supplier] == ["Mine Co."]
    (deposit,) = [row for row in summary.by_state if row.state is CaseState.WAITING_DEPOSIT]
    assert deposit.sla_breached_count == 1

    theirs_summary = await _handler(sessions, now=now).handle(other)
    assert theirs_summary.active_case_count == 1
    assert [row.supplier_name for row in theirs_summary.by_supplier] == ["Theirs Co."]


async def test_the_active_cases_are_read_a_page_at_a_time_inside_the_callers_workspace(
    counted: tuple[async_sessionmaker[AsyncSession], list[int]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """More active cases than a page: every one of the caller's workspace is
    counted once, none of another workspace or tenant, and no statement binds
    more parameters than a page of ids (plus the few every query carries) —
    the old read bound one per active case, which asyncpg refuses past
    32 767."""
    sessions, bound = counted
    page = 2
    monkeypatch.setattr(handlers, "ACTIVE_CASES_PAGE", page)
    tenant = uuid.uuid4()
    mine = _context(tenant, uuid.uuid4())
    neighbour = _context(tenant, uuid.uuid4())
    stranger = _context(uuid.uuid4(), uuid.uuid4())
    cases = SqlPOCaseRepository(sessions)
    for context, count in ((mine, 12), (neighbour, 3), (stranger, 2)):
        for _ in range(count):
            await cases.add(context, _case(context, "Paged Co."))
    bound.clear()

    summary = await _handler(sessions, now=datetime.now(UTC)).handle(mine)

    assert summary.active_case_count == 12
    assert [(row.supplier_name, row.case_count) for row in summary.by_supplier] == [
        ("Paged Co.", 12)
    ]
    assert max(bound) <= page + 6, bound
