"""Integration: every PO-case write commits one audit event with it (ticket P2).

On the real database, through the real handlers and repositories:

- `CreatePOCase`, a step `AdvancePOCase` applies, `SubmitSupplierUpdate` and
  `AnalyzeDelayImpact` each leave exactly one `platform.audit_events` row,
  carrying the caller's tenant, workspace and actor;
- a write the database refuses (a PO reference already taken) leaves no
  audit row: the event is in the write's transaction, not after it;
- a write refused before it starts (another tenant's case) leaves none, and
  no row of either tenant's audit names the other tenant.

The model is a fake: what is under test is the write and its audit, not the
extraction (`test_handlers.py` covers the plan-day refusal before the model).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from supply_chain_harness import REPO_ROOT, DatabaseUrls

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.ports import ModelRequest
from dw_kernel.errors import ConflictError, NotFoundError
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.adapters.persistence.policy_overrides import SqlPolicyOverrideRepository
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_supply_chain.action_duties import CaseDuty, load_supply_chain_action_duties
from dw_supply_chain.adapters.persistence.delay_impact_repository import (
    SqlDelayImpactAnalysisRepository,
)
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.adapters.persistence.supplier_update_repository import (
    SqlSupplierUpdateRepository,
)
from dw_supply_chain.application.handlers import (
    AdvancePOCase,
    AnalyzeDelayImpact,
    CaseActionApplied,
    CreatePOCase,
    SubmitSupplierUpdate,
)
from dw_supply_chain.approval_matrix import SupplyChainApprovalMatrix
from dw_supply_chain.domain.delay_impact import DelayImpactExtraction, MitigationOption
from dw_supply_chain.domain.po_case import CaseAction, CaseState, OrderKind, POCase
from dw_supply_chain.domain.supplier_update import SupplierEventType, SupplierUpdateExtraction
from dw_supply_chain.policy_files import SLA_POLICY_FILE
from dw_supply_chain.sla_policy import load_supply_chain_sla_policy
from dw_supply_chain.testing.po_papers import open_paper_gate
from dw_supply_chain.testing.production_gate import open_production_gate

pytestmark = pytest.mark.integration

POLICIES = REPO_ROOT / "configs" / "policies"
_SCOPES = frozenset(
    {
        "supply_chain.po_case.read",
        "supply_chain.po_case.write",
        "supply_chain.supplier_update.write",
        "supply_chain.delay_impact.write",
    }
) | frozenset(f"supply_chain.duty.{duty.value}" for duty in CaseDuty)
_MESSAGE = "NCC báo: lô hàng trễ 7 ngày vì thiếu linh kiện, bí mật giá 12.345 USD"


class _Model:
    """Answers the extraction the handler asks for; the words never matter."""

    async def generate_structured(
        self, request: ModelRequest, output_type: type[Any], *, run_context: RunContext
    ) -> Any:
        if output_type is SupplierUpdateExtraction:
            return SupplierUpdateExtraction(
                event_type=SupplierEventType.PRODUCTION_DELAY,
                delay_days=7,
                reason="thiếu linh kiện",
                proposed_action="chia lô",
                confidence=0.95,
                source_ref="trễ 7 ngày",
            )
        return DelayImpactExtraction(
            assumptions=["lịch sản xuất không đổi thêm"],
            mitigation_options=[MitigationOption(description="chia lô", tradeoff="cước cao")],
        )


class _Stack:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine) -> None:
        self.migrator = migrator
        self.cases = SqlPOCaseRepository(sessions)
        self.updates = SqlSupplierUpdateRepository(sessions)
        self.analyses = SqlDelayImpactAnalysisRepository(sessions)
        self.overrides = SqlPolicyOverrideRepository(sessions)

    def create(self) -> CreatePOCase:
        return CreatePOCase(
            repo=self.cases,
            authz=ScopeAuthorizationService(),
            ids=Uuid4Generator(),
            clock=SystemClock(),
            policy_override_repo=self.overrides,
            platform_default_sla_policy=load_supply_chain_sla_policy(POLICIES / SLA_POLICY_FILE),
        )

    def advance(self) -> AdvancePOCase:
        return AdvancePOCase(
            repo=self.cases,
            authz=ScopeAuthorizationService(),
            policy_override_repo=self.overrides,
            platform_default_approval_matrix=SupplyChainApprovalMatrix(
                schema_version="1.0",
                policy_id="supply_chain_approval_matrix",
                policy_version="1.0.0",
            ),
            platform_default_action_duties=load_supply_chain_action_duties(
                POLICIES / "supply_chain_action_duties@1.2.0.yaml"
            ),
            runner=_NoRunner(),
            production_gate=open_production_gate(),
            papers=open_paper_gate(),
            ids=Uuid4Generator(),
            clock=SystemClock(),
        )

    def submit(self) -> SubmitSupplierUpdate:
        return SubmitSupplierUpdate(
            po_case_repo=self.cases,
            supplier_update_repo=self.updates,
            gateway=_Model(),
            authz=ScopeAuthorizationService(),
            ids=Uuid4Generator(),
            clock=SystemClock(),
        )

    def analyze(self) -> AnalyzeDelayImpact:
        return AnalyzeDelayImpact(
            po_case_repo=self.cases,
            supplier_update_repo=self.updates,
            delay_impact_repo=self.analyses,
            gateway=_Model(),
            authz=ScopeAuthorizationService(),
            ids=Uuid4Generator(),
            clock=SystemClock(),
        )

    async def audits(self, *, where: str, **params: object) -> list[sa.Row[Any]]:
        async with self.migrator.connect() as conn:
            return list(
                (
                    await conn.execute(
                        sa.text(
                            "SELECT tenant_id, workspace_id, actor_id, action, resource_type,"
                            f" resource_id, details FROM platform.audit_events WHERE {where}"
                        ),
                        params,
                    )
                ).all()
            )


class _NoRunner:
    """No step here needs approval: a run started would be a test bug."""

    def hosts(self, *, worker_id: str, worker_version: str, graph_version: str) -> bool:
        return True

    async def start(
        self, *, run_context: RunContext, input_payload: dict[str, object]
    ) -> uuid.UUID:
        raise AssertionError("no step in this file needs approval")

    async def resume(
        self, *, run_context: RunContext, run_id: uuid.UUID, resume_payload: dict[str, object]
    ) -> None:
        raise NotImplementedError("not exercised by these writes")


@pytest.fixture
async def stack(db_urls: DatabaseUrls) -> AsyncIterator[_Stack]:
    app = create_async_engine(db_urls.app, poolclass=NullPool)
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    yield _Stack(async_sessionmaker(app, expire_on_commit=False), migrator)
    await app.dispose()
    await migrator.dispose()


def _context(tenant: uuid.UUID | None = None) -> AccessContext:
    return AccessContext(
        tenant_id=tenant or uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=_SCOPES,
        plan_id="professional",
    )


async def _create(stack: _Stack, context: AccessContext, reference: str | None = None) -> POCase:
    return await stack.create().handle(
        context,
        po_reference=reference or f"PO-AU-{uuid.uuid4().hex[:8]}",
        supplier_name="Kangaroo",
        order_kind=OrderKind.REORDER,
    )


async def test_creating_a_case_writes_one_audit_row_under_the_caller(stack: _Stack) -> None:
    context = _context()
    case = await _create(stack, context)

    rows = await stack.audits(where="resource_id = :r", r=str(case.id))

    assert [(r.tenant_id, r.workspace_id, r.actor_id, r.action, r.resource_type) for r in rows] == [
        (
            context.tenant_id,
            context.workspace_id,
            context.principal_id,
            "supply_chain.po_case.create",
            "po_case",
        )
    ]


async def test_a_refused_create_leaves_no_audit_row(stack: _Stack) -> None:
    """The second case under a taken reference is refused by the database;
    its audit event was in the same transaction, so it is gone too."""
    context = _context()
    reference = f"PO-AU-{uuid.uuid4().hex[:8]}"
    await _create(stack, context, reference)

    with pytest.raises(ConflictError):
        await _create(stack, context, reference)

    rows = await stack.audits(
        where="tenant_id = :t AND action = 'supply_chain.po_case.create'", t=context.tenant_id
    )
    assert len(rows) == 1


async def test_a_step_applied_directly_writes_one_audit_row(stack: _Stack) -> None:
    context = _context()
    case = await _create(stack, context)

    result = await stack.advance().handle(
        context, po_case_id=case.id, action=CaseAction.REQUEST_DEPOSIT
    )

    assert isinstance(result, CaseActionApplied)
    rows = await stack.audits(
        where="resource_id = :r AND action = 'supply_chain.po_case.request_deposit'",
        r=str(case.id),
    )
    assert [(r.tenant_id, r.actor_id, r.details) for r in rows] == [
        (
            context.tenant_id,
            context.principal_id,
            {"from_state": "po_created", "to_state": "waiting_deposit", "reason": None},
        )
    ]


async def test_an_illegal_step_leaves_no_audit_row(stack: _Stack) -> None:
    context = _context()
    case = await _create(stack, context)

    with pytest.raises(ConflictError):
        await stack.advance().handle(context, po_case_id=case.id, action=CaseAction.CONFIRM_DEPOSIT)

    rows = await stack.audits(where="resource_id = :r", r=str(case.id))
    assert [r.action for r in rows] == ["supply_chain.po_case.create"]


async def test_a_supplier_update_and_its_analysis_each_write_one_audit_row(
    stack: _Stack,
) -> None:
    context = _context()
    case = await _create(stack, context)
    for action in (
        CaseAction.REQUEST_DEPOSIT,
        CaseAction.CONFIRM_DEPOSIT,
        CaseAction.START_PRE_PRODUCTION,
        CaseAction.START_PRODUCTION,
    ):
        await stack.advance().handle(context, po_case_id=case.id, action=action)
    current = await stack.cases.get(context, case.id)
    assert current is not None and current.state is CaseState.PRODUCTION

    update = await stack.submit().handle(context, po_case_id=case.id, raw_text=_MESSAGE)
    analysis = await stack.analyze().handle(
        context, po_case_id=case.id, supplier_update_id=update.id
    )

    submitted = await stack.audits(where="resource_id = :r", r=str(update.id))
    analyzed = await stack.audits(where="resource_id = :r", r=str(analysis.id))
    assert [(r.tenant_id, r.actor_id, r.action, r.resource_type) for r in submitted] == [
        (
            context.tenant_id,
            context.principal_id,
            "supply_chain.supplier_update.submit",
            "supplier_update",
        )
    ]
    assert [(r.tenant_id, r.actor_id, r.action, r.resource_type) for r in analyzed] == [
        (
            context.tenant_id,
            context.principal_id,
            "supply_chain.delay_impact.analyze",
            "delay_impact_analysis",
        )
    ]
    # The supplier's words stay on the record they belong to, not in the trail.
    assert "12.345" not in str(submitted[0].details)
    assert "thiếu linh kiện" not in str(submitted[0].details)


async def test_another_tenants_writes_leave_no_audit_row_and_name_no_other_tenant(
    stack: _Stack,
) -> None:
    owner = _context()
    case = await _create(stack, owner)
    await stack.advance().handle(owner, po_case_id=case.id, action=CaseAction.REQUEST_DEPOSIT)
    intruder = _context()

    with pytest.raises(NotFoundError):
        await stack.advance().handle(
            intruder, po_case_id=case.id, action=CaseAction.CONFIRM_DEPOSIT
        )
    with pytest.raises(NotFoundError):
        await stack.submit().handle(intruder, po_case_id=case.id, raw_text=_MESSAGE)

    assert await stack.audits(where="tenant_id = :t", t=intruder.tenant_id) == []
    case_rows = await stack.audits(where="resource_id = :r", r=str(case.id))
    assert {r.tenant_id for r in case_rows} == {owner.tenant_id}
    assert str(intruder.tenant_id) not in str([r.details for r in case_rows])
