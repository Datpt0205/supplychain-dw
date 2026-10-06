"""Integration: the daily brief, composed over the real repositories.

`test_handlers.py` (unit) proves the handler's grouping against fakes that
honor the ports' contracts because they were written to. What only a real
database can show: the whole composition — active cases, the latest supplier
update per case, the last day's transitions, a case read back by id and the
platform's approval inbox — sees one tenant's rows and nothing else, because
RLS says so, not because a fake was kind.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls

from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import FixedClock
from dw_platform.adapters.persistence.approval_queries import SqlPendingApprovalQuery
from dw_platform.adapters.persistence.repositories import SqlApprovalRepository
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.domain.approval import ApprovalRequest
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.adapters.persistence.supplier_update_repository import (
    SqlSupplierUpdateRepository,
)
from dw_supply_chain.application.handlers import GetDailyBrief
from dw_supply_chain.brief_policy import SupplyChainBriefPolicy
from dw_supply_chain.domain.daily_brief import BriefSignal
from dw_supply_chain.domain.po_case import POCase, POCaseId
from dw_supply_chain.domain.supplier_update import (
    SupplierEventType,
    SupplierUpdate,
    SupplierUpdateExtraction,
    SupplierUpdateId,
)
from dw_supply_chain.sla_policy import (
    SLAConfirmationStatus,
    SLAMilestone,
    SupplierUpdateCadence,
    SupplyChainSLAPolicy,
)
from dw_supply_chain.workflows.advance_case_graph import APPROVAL_TYPE_PREFIX

pytestmark = pytest.mark.integration


@pytest.fixture
async def sessions(db_urls: DatabaseUrls) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


class _NoOverrides:
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
        raise NotImplementedError("not exercised by the daily brief")


def _context(tenant: uuid.UUID, workspace: uuid.UUID) -> AccessContext:
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=frozenset(
            {"supply_chain.po_case.read", "supply_chain.po_case.write", "approvals.read"}
        ),
        plan_id="professional",
    )


def _case(context: AccessContext) -> POCase:
    return POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        po_reference=f"PO-{uuid.uuid4().hex[:8]}",
        supplier_name="Elmich Co.",
    )


def _delay_update(context: AccessContext, case: POCase, *, delay_days: int) -> SupplierUpdate:
    return SupplierUpdate(
        id=SupplierUpdateId(uuid.uuid4()),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        po_case_id=case.id,
        raw_text=f"delayed by {delay_days} days",
        extraction=SupplierUpdateExtraction(
            event_type=SupplierEventType.PRODUCTION_DELAY,
            delay_days=delay_days,
            reason="component shortage",
            proposed_action="wait",
            confidence=0.95,
            source_ref=f"delayed by {delay_days} days",
        ),
        requires_confirmation=False,
    )


async def _pending_approval(
    sessions: async_sessionmaker[AsyncSession], context: AccessContext, case: POCase
) -> ApprovalRequest:
    request = ApprovalRequest(
        id=uuid.uuid4(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        approval_type=f"{APPROVAL_TYPE_PREFIX}cancel",
        requested_by=UserId(context.principal_id),
        reason="cancel needs approval",
        payload={"po_case_id": str(case.id.value)},
    )
    async with tenant_session(sessions, TenantScope.from_access_context(context)) as session:
        await SqlApprovalRepository(session).add(request)
    return request


def _handler(sessions: async_sessionmaker[AsyncSession], *, now: datetime) -> GetDailyBrief:
    return GetDailyBrief(
        po_case_repo=SqlPOCaseRepository(sessions),
        supplier_update_repo=SqlSupplierUpdateRepository(sessions),
        policy_override_repo=_NoOverrides(),
        # A 0-day deposit SLA: any case waiting on its deposit is over it,
        # so the test needs no clock far enough ahead to leave the change
        # window.
        platform_default_policy=SupplyChainSLAPolicy(
            schema_version="1.0",
            policy_id="supply_chain_sla",
            policy_version="1.0.0",
            sla={"deposit": SLAMilestone(duration="0d", status=SLAConfirmationStatus.CONFIRMED)},
            supplier_update=SupplierUpdateCadence(reminder_after="5d", escalation_after="10d"),
        ),
        platform_default_brief_policy=SupplyChainBriefPolicy(
            schema_version="1.0",
            policy_id="supply_chain_brief",
            policy_version="1.0.0",
            signal_order=tuple(BriefSignal),
        ),
        pending_approvals=SqlPendingApprovalQuery(sessions),
        authz=ScopeAuthorizationService(),
        clock=FixedClock(now),
    )


async def test_the_brief_holds_only_the_callers_tenant(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    mine = _context(uuid.uuid4(), uuid.uuid4())
    # Same workspace id, another tenant (UUIDs are not tenant-bound): only the
    # tenant boundary can keep its rows out, not the workspace filter the
    # approval count carries since platform-runtime/approval-audit-and-workspace/02.
    other = _context(uuid.uuid4(), mine.workspace_id)
    cases = SqlPOCaseRepository(sessions)
    updates = SqlSupplierUpdateRepository(sessions)

    waiting = _case(mine)
    await cases.add(mine, waiting)
    waiting.request_deposit()
    await cases.save(mine, waiting)
    await updates.add(mine, _delay_update(mine, waiting, delay_days=6))
    await _pending_approval(sessions, mine, waiting)

    # The other tenant has the same shape of everything.
    theirs = _case(other)
    await cases.add(other, theirs)
    theirs.request_deposit()
    await cases.save(other, theirs)
    await updates.add(other, _delay_update(other, theirs, delay_days=11))
    await _pending_approval(sessions, other, theirs)

    brief = await _handler(sessions, now=datetime.now(UTC) + timedelta(hours=1)).handle(mine)

    in_brief = {entry.case.id for group in brief.groups for entry in group.entries}
    assert in_brief == {waiting.id}
    keys = [group.key for group in brief.groups]
    assert keys == [
        "sla_breached:deposit",
        "approval_pending",
        "supplier_reported_delay",
        "waiting_on_us:waiting_deposit",
        "changed_recently",
    ]
    assert brief.group("approval_pending").total == 1  # type: ignore[union-attr]
    assert brief.group("supplier_reported_delay").entries[0].days == 6  # type: ignore[union-attr]
    (changed,) = brief.group("changed_recently").entries  # type: ignore[union-attr]
    assert changed.transition is not None
    assert changed.transition.to_state.value == "waiting_deposit"
    assert brief.active_case_count == 1
