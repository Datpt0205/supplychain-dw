"""Integration: stage-1 ticket 08's reads over the real database.

What only Postgres can show: the brief's stage-1 groups, a question's lookup
by proposal code and its Category filter, and the daily report see one
tenant's and one workspace's rows and nothing else, because RLS says so — not
because a fake was kind. Tenant B shares A's workspace id on purpose: only the
tenant boundary can keep its rows out.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls

from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.pagination import page_request
from dw_kernel.ports import FixedClock, SystemClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.adapters.persistence.product_case_repository import (
    SqlProductCaseRepository,
)
from dw_supply_chain.adapters.persistence.supplier_update_repository import (
    SqlSupplierUpdateRepository,
)
from dw_supply_chain.application.daily_report import SendStageOneReport
from dw_supply_chain.application.handlers import GetDailyBrief
from dw_supply_chain.application.ports import PendingApprovalRecord, ProductCaseListFilter
from dw_supply_chain.brief_policy import load_supply_chain_brief_policy
from dw_supply_chain.domain.daily_brief import BriefSignal
from dw_supply_chain.domain.product_development_case import (
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    SampleResult,
)
from dw_supply_chain.policy_files import SLA_POLICY_FILE
from dw_supply_chain.sla_policy import load_supply_chain_sla_policy

pytestmark = pytest.mark.integration

_POLICIES = Path(__file__).resolve().parents[5] / "configs" / "policies"
READ = frozenset({"supply_chain.po_case.read", "supply_chain.product_case.read", "approvals.read"})


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
        raise NotImplementedError("not exercised by stage-1 reads")


def _context(tenant: uuid.UUID, workspace: uuid.UUID) -> AccessContext:
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=READ,
        plan_id="professional",
    )


def _audit(context: AccessContext, case: ProductDevelopmentCase, action: str) -> AuditEvent:
    return AuditEvent(
        id=uuid.uuid4(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        actor_id=UserId(context.principal_id),
        action=f"supply_chain.product_case.{action}",
        resource_type="product_dev_case",
        resource_id=str(case.id),
        occurred_at=SystemClock().now(),
    )


async def _rejected_today(
    repo: SqlProductCaseRepository, context: AccessContext, code: str, category: str
) -> ProductDevelopmentCase:
    """A case whose sample round was closed (Hủy) just now: proposed, sample
    asked for and received, rejected — the one path to a closed round that
    needs no document."""
    actor = context.principal_id
    case = ProductDevelopmentCase.propose(
        id=ProductDevelopmentCaseId(uuid.uuid4()),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        proposal_code=code,
        product_name=f"Sản phẩm {code}",
        category=category,
        actor_id=actor,
    )
    await repo.add(context, case, audit=_audit(context, case, "propose"))
    case.request_sample(actor_id=actor, supplier_name="NCC Minh Long")
    await repo.save(context, case, audit=_audit(context, case, "request_sample"))
    case.receive_sample(actor_id=actor)
    await repo.save(context, case, audit=_audit(context, case, "receive_sample"))
    case.reject_sample(actor_id=actor, reason="mẫu nứt")
    await repo.save(context, case, audit=_audit(context, case, "reject_sample"))
    return case


async def test_closed_rounds_and_codes_are_read_only_in_their_own_workspace(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlProductCaseRepository(sessions)
    a_w1 = _context(uuid.uuid4(), uuid.uuid4())
    a_w2 = _context(a_w1.tenant_id, uuid.uuid4())
    b_w1 = _context(uuid.uuid4(), a_w1.workspace_id)
    code = f"SP-{uuid.uuid4().hex[:6]}"
    mine = await _rejected_today(repo, a_w1, code, "chao")
    await _rejected_today(repo, a_w2, f"{code}-W2", "chao")
    # Tenant B even reuses A's code: codes are unique per tenant only.
    await _rejected_today(repo, b_w1, code, "chao")
    since = datetime.now(UTC) - timedelta(hours=1)

    closed = await repo.closed_rounds_since(a_w1, since)
    assert [(c.case.id, c.sample_round.result) for c in closed] == [
        (mine.id, SampleResult.REJECTED)
    ]
    assert closed[0].sample_round.requested_changes is None
    assert await repo.closed_rounds_since(a_w1, datetime.now(UTC) + timedelta(hours=1)) == []

    assert [c.id for c in await repo.find_by_proposal_code(a_w1, f"  {code.lower()} ")] == [mine.id]
    # W2 of the same tenant, asking for W1's code: nothing.
    assert await repo.find_by_proposal_code(a_w2, code) == []

    page = await repo.list_page(
        a_w1,
        page_request(
            limit=10,
            cursor=None,
            query=ProductCaseListFilter().page_query(a_w1.tenant_id, a_w1.workspace_id),
        ),
        ProductCaseListFilter(category="chao"),
    )
    assert [c.id for c in page.items] == [mine.id]
    assert (
        await repo.list_page(
            a_w1,
            page_request(
                limit=10,
                cursor=None,
                query=ProductCaseListFilter().page_query(a_w1.tenant_id, a_w1.workspace_id),
            ),
            ProductCaseListFilter(category="noi"),
        )
    ).items == ()


async def test_the_brief_holds_only_the_callers_tenant_and_workspace(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlProductCaseRepository(sessions)
    a_w1 = _context(uuid.uuid4(), uuid.uuid4())
    a_w2 = _context(a_w1.tenant_id, uuid.uuid4())
    b_w1 = _context(uuid.uuid4(), a_w1.workspace_id)
    mine = await _rejected_today(repo, a_w1, "SP-A1", "noi")
    await _rejected_today(repo, a_w2, "SP-A2", "noi")
    await _rejected_today(repo, b_w1, "SP-B1", "noi")

    brief = await GetDailyBrief(
        po_case_repo=SqlPOCaseRepository(sessions),
        supplier_update_repo=SqlSupplierUpdateRepository(sessions),
        product_case_repo=repo,
        policy_override_repo=_NoOverrides(),
        platform_default_policy=load_supply_chain_sla_policy(_POLICIES / SLA_POLICY_FILE),
        platform_default_brief_policy=load_supply_chain_brief_policy(
            _POLICIES / "supply_chain_brief@1.1.0.yaml"
        ),
        pending_approvals=_NoPending(),
        authz=ScopeAuthorizationService(),
        clock=SystemClock(),
    ).handle(a_w1)

    found = {e.case.id for g in brief.groups for e in g.product_entries}
    assert found == {mine.id}
    (group,) = [g for g in brief.groups if g.signal is BriefSignal.SAMPLE_EVALUATED_TODAY]
    assert group.key == "sample_evaluated_today:rejected"


class _NoPending:
    async def list_pending_by_type_prefix(
        self, context: AccessContext, *, prefix: str, limit: int
    ) -> tuple[int, Sequence[PendingApprovalRecord]]:
        return 0, []

    async def pending_by_payload(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError("not exercised by the brief")


class _Holders:
    """Everyone asked about holds every scope: the report's own intersection
    is unit-tested; this test is about which workspace's cases it counts."""

    def __init__(self, people: dict[uuid.UUID, uuid.UUID]) -> None:
        self.people = people

    async def holding(
        self, tenant_id: uuid.UUID, workspace_id: uuid.UUID, scopes: frozenset[str]
    ) -> list[uuid.UUID]:
        return [p for p, ws in self.people.items() if ws == workspace_id]


class _Inbox:
    def __init__(self) -> None:
        self.sent: list[tuple[uuid.UUID, uuid.UUID, str]] = []

    async def deliver(
        self,
        context: AccessContext,
        *,
        recipients: Sequence[uuid.UUID],
        source_key: str,
        title: str,
        body: str,
        link: str | None,
    ) -> None:
        self.sent.extend((context.workspace_id, r, body) for r in recipients)


class _Only:
    def __init__(self, pairs: list[tuple[uuid.UUID, uuid.UUID]]) -> None:
        self.pairs = pairs

    async def workspaces(self) -> list[tuple[uuid.UUID, uuid.UUID]]:
        return self.pairs


async def test_the_report_counts_each_workspace_under_its_own_rls(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlProductCaseRepository(sessions)
    a_w1 = _context(uuid.uuid4(), uuid.uuid4())
    a_w2 = _context(a_w1.tenant_id, uuid.uuid4())
    await _rejected_today(repo, a_w1, "SP-R1", "noi")
    await _rejected_today(repo, a_w1, "SP-R2", "noi")
    lead_w1, lead_w2 = uuid.uuid4(), uuid.uuid4()
    inbox = _Inbox()
    # `report_workspace` directly: the hour gate is unit-tested, this is RLS.
    now = datetime.now(UTC)
    report = SendStageOneReport(
        workspaces=_Only(
            [(a_w1.tenant_id, a_w1.workspace_id), (a_w2.tenant_id, a_w2.workspace_id)]
        ),
        product_case_repo=repo,
        policy_override_repo=_NoOverrides(),
        platform_default_sla_policy=load_supply_chain_sla_policy(_POLICIES / SLA_POLICY_FILE),
        holders=_Holders({lead_w1: a_w1.workspace_id, lead_w2: a_w2.workspace_id}),
        notifier=inbox,
        clock=FixedClock(now),
    )
    await report.report_workspace(_context(a_w1.tenant_id, a_w1.workspace_id))
    await report.report_workspace(_context(a_w2.tenant_id, a_w2.workspace_id))

    assert [(ws, who) for ws, who, _ in inbox.sent] == [(a_w1.workspace_id, lead_w1)]
    assert "0 đạt, 0 cần chỉnh sửa, 2 hủy" in inbox.sent[0][2]
