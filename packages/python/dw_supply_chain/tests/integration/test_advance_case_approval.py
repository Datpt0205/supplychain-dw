"""Integration: `AdvancePOCase`'s HITL wrap, through the real runner + Postgres.

`test_advance_case_graph.py` (unit) already proves the pause/resume/apply
cycle against `MemorySaver` and a fake repository. What only a real database
can show:

- the SAME cycle survives a process restart between pause and resume — the
  `SqlAlchemyCheckpointSaver` genuinely persists the paused graph, not the
  runner object graph in memory (same proof `test_resume_after_restart.py`
  gives the platform's own demo graph);
- the `ApprovalRequest` row `AdvancePOCase.handle()` starts really exists
  under RLS, carries the real `run_id`, and is what `ApproveAndResumeService`
  — the same generic service `POST /api/v1/approvals/{id}/decisions` calls in
  production — actually resolves against;
- `strict_approval_prefixes`, wired at the composition root
  (`apps/api/src/dw_api/bootstrap/wiring.py`) for this graph's
  `supply_chain.case_action.` prefix, genuinely refuses self-approval and a
  blank comment when a real `ApproveAndResumeService.decide()` call enforces
  it — not just when a unit test's fake does.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from supply_chain_harness import REPO_ROOT, DatabaseUrls

from dw_agent_runtime.adapters.checkpoint import SqlAlchemyCheckpointSaver
from dw_agent_runtime.adapters.langgraph_runner import LangGraphWorkflowRunner
from dw_agent_runtime.adapters.run_store import RunStatus, SqlWorkerRunStore
from dw_agent_runtime.approval_flow import ApproveAndResumeService
from dw_agent_runtime.autonomy import AutonomyApprovalPolicy
from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.model.budget import RunBudgetLedger
from dw_agent_runtime.registry import GraphRegistry, WorkerRegistry
from dw_kernel.errors import ConflictError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.adapters.persistence.policy_overrides import SqlPolicyOverrideRepository
from dw_platform.adapters.persistence.uow import SqlPlatformUnitOfWorkFactory
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.domain.approval import ApprovalStatus
from dw_supply_chain.action_duties import load_supply_chain_action_duties
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.application.handlers import AdvancePOCase, CaseActionPendingApproval
from dw_supply_chain.approval_matrix import SupplyChainApprovalMatrix
from dw_supply_chain.domain.po_case import CaseAction, CaseState, POCase, POCaseId
from dw_supply_chain.workflows.advance_case_graph import (
    APPROVAL_TYPE_PREFIX,
    GRAPH_VERSION,
    WORKER_ID,
    build_advance_case_graph,
)

pytestmark = pytest.mark.integration

STALE_AFTER_SECONDS_LOCAL = 3600
_WORKER_CONFIG = REPO_ROOT / "configs" / "workers" / "supply_chain_advance_case.yaml"
_ACTION_DUTIES = REPO_ROOT / "configs" / "policies" / "supply_chain_action_duties@1.0.0.yaml"


class _UnmeteredPlan:
    """Any plan, no daily limit — the run quota is not what these tests exercise."""

    def runs_per_day(self, plan_id: str) -> int | None:
        return None

    def spend_usd_per_day(self, plan_id: str) -> Decimal | None:
        return None


def _matrix_requiring(*actions: CaseAction) -> SupplyChainApprovalMatrix:
    return SupplyChainApprovalMatrix(
        schema_version="1.0",
        policy_id="supply_chain_approval_matrix",
        policy_version="1.0.0",
        approval_required_actions=frozenset(actions),
    )


def _case(*, tenant: uuid.UUID, workspace: uuid.UUID) -> POCase:
    return POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(tenant),
        workspace_id=WorkspaceId(workspace),
        po_reference=f"PO-{uuid.uuid4().hex[:8]}",
        supplier_name="Elmich Co.",
    )


def _context(
    *, tenant: uuid.UUID, workspace: uuid.UUID, principal: uuid.UUID, scopes: frozenset[str]
) -> AccessContext:
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=principal,
        roles=frozenset({"member"}),
        scopes=scopes,
        plan_id="professional",
    )


class RunnerStack:
    """One 'process' worth of runtime objects, wired the same way
    `apps/api/src/dw_api/bootstrap/wiring.py` wires the real thing: the
    graph registered under `WORKER_ID`/`GRAPH_VERSION`, the shipped worker
    YAML loaded from its real path, `strict_approval_prefixes` carrying this
    graph's approval-type prefix."""

    def __init__(self, app_url: str) -> None:
        self.engine = create_async_engine(app_url, poolclass=NullPool)
        session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.po_case_repo = SqlPOCaseRepository(session_factory)
        self.policy_override_repo = SqlPolicyOverrideRepository(session_factory)
        self.authz = ScopeAuthorizationService()

        graphs = GraphRegistry()
        graphs.register(
            WORKER_ID, GRAPH_VERSION, lambda: build_advance_case_graph(self.po_case_repo)
        )
        workers = WorkerRegistry(graph_registry=graphs)
        workers.load_file(_WORKER_CONFIG)

        self.uow_factory = SqlPlatformUnitOfWorkFactory(session_factory)
        self.run_store = SqlWorkerRunStore(
            session_factory, stale_run_after_seconds=STALE_AFTER_SECONDS_LOCAL
        )
        self.runner = LangGraphWorkflowRunner(
            worker_registry=workers,
            graph_registry=graphs,
            checkpoint_saver=SqlAlchemyCheckpointSaver(session_factory),
            run_store=self.run_store,
            uow_factory=self.uow_factory,
            clock=SystemClock(),
            id_generator=Uuid4Generator(),
            allowance=_UnmeteredPlan(),
            budget=RunBudgetLedger(),
            approval_policy=AutonomyApprovalPolicy(),
        )
        self.approve_and_resume = ApproveAndResumeService(
            uow_factory=self.uow_factory,
            runner=self.runner,
            run_store=self.run_store,
            clock=SystemClock(),
            id_generator=Uuid4Generator(),
            strict_approval_prefixes=frozenset({APPROVAL_TYPE_PREFIX}),
        )

    def advance_po_case(self, matrix: SupplyChainApprovalMatrix) -> AdvancePOCase:
        return AdvancePOCase(
            repo=self.po_case_repo,
            authz=self.authz,
            policy_override_repo=self.policy_override_repo,
            platform_default_approval_matrix=matrix,
            platform_default_action_duties=load_supply_chain_action_duties(_ACTION_DUTIES),
            runner=self.runner,
            ids=Uuid4Generator(),
        )

    def run_context_for_read(self, tenant_id: uuid.UUID, workspace_id: uuid.UUID) -> RunContext:
        # `run_store.get()` reads `run_context.tenant_id` and `workspace_id` to
        # scope the query (RLS the tenant, the store the workspace) — the rest
        # is unused for a read, same as `ApproveAndResumeService._run_context_for`'s
        # own lookup context.
        return RunContext(
            run_id=uuid.uuid4(),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=uuid.uuid4(),
            worker_id="unknown",
            worker_version="0.0.0",
            channel="web",
            plan_id="professional",
            roles=frozenset(),
            scopes=frozenset(),
            trace_id="lookup",
        )

    async def dispose(self) -> None:
        await self.engine.dispose()


async def test_pause_survives_restart_then_approval_applies_the_transition(
    db_urls: DatabaseUrls,
) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    requester, approver = uuid.uuid4(), uuid.uuid4()
    case = _case(tenant=tenant, workspace=workspace)

    # --- process 1: create the case, start the run, pause for approval ---
    stack1 = RunnerStack(db_urls.app)
    requester_context = _context(
        tenant=tenant,
        workspace=workspace,
        principal=requester,
        scopes=frozenset({"supply_chain.po_case.read", "supply_chain.duty.ordering"}),
    )
    await stack1.po_case_repo.add(requester_context, case)

    result = await stack1.advance_po_case(_matrix_requiring(CaseAction.REQUEST_DEPOSIT)).handle(
        requester_context, po_case_id=case.id, action=CaseAction.REQUEST_DEPOSIT
    )
    assert isinstance(result, CaseActionPendingApproval)
    run_id = result.run_id

    record = await stack1.run_store.get(stack1.run_context_for_read(tenant, workspace), run_id)
    assert record.status is RunStatus.WAITING_APPROVAL
    assert record.approval_request_id is not None

    async with stack1.uow_factory(requester_context) as uow:
        approval = await uow.approvals.get(
            record.approval_request_id, workspace_id=requester_context.workspace_id
        )
        assert approval is not None
        assert approval.approval_type == f"{APPROVAL_TYPE_PREFIX}request_deposit"
        assert approval.run_id == run_id
        assert approval.requested_by.value == requester

    # Nothing applied yet — the case is untouched.
    unchanged = await stack1.po_case_repo.get(requester_context, case.id)
    assert unchanged is not None
    assert unchanged.state is CaseState.PO_CREATED
    await stack1.dispose()

    # --- process 2: brand-new stack, resumes from the checkpoint ---
    stack2 = RunnerStack(db_urls.app)
    approver_context = _context(
        tenant=tenant,
        workspace=workspace,
        principal=approver,
        scopes=frozenset({"approvals.decide"}),
    )
    approved = await stack2.approve_and_resume.decide(
        approval_id=record.approval_request_id,
        approve=True,
        comment="đã kiểm tra, đồng ý tiến hành",
        context=approver_context,
        authorization=stack2.authz,
    )
    assert approved.status is ApprovalStatus.APPROVED

    final = await stack2.run_store.get(stack2.run_context_for_read(tenant, workspace), run_id)
    assert final.status is RunStatus.COMPLETED

    applied = await stack2.po_case_repo.get(requester_context, case.id)
    assert applied is not None
    assert applied.state is CaseState.WAITING_DEPOSIT
    assert applied.version == 2
    await stack2.dispose()


async def test_rejecting_applies_nothing(db_urls: DatabaseUrls) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    requester, approver = uuid.uuid4(), uuid.uuid4()
    case = _case(tenant=tenant, workspace=workspace)

    stack = RunnerStack(db_urls.app)
    requester_context = _context(
        tenant=tenant,
        workspace=workspace,
        principal=requester,
        scopes=frozenset({"supply_chain.po_case.read", "supply_chain.duty.ordering"}),
    )
    await stack.po_case_repo.add(requester_context, case)
    result = await stack.advance_po_case(_matrix_requiring(CaseAction.REQUEST_DEPOSIT)).handle(
        requester_context, po_case_id=case.id, action=CaseAction.REQUEST_DEPOSIT
    )
    assert isinstance(result, CaseActionPendingApproval)
    record = await stack.run_store.get(stack.run_context_for_read(tenant, workspace), result.run_id)
    assert record.approval_request_id is not None

    approver_context = _context(
        tenant=tenant,
        workspace=workspace,
        principal=approver,
        scopes=frozenset({"approvals.decide"}),
    )
    await stack.approve_and_resume.decide(
        approval_id=record.approval_request_id,
        approve=False,
        comment="không đủ evidence",
        context=approver_context,
        authorization=stack.authz,
    )

    final = await stack.run_store.get(stack.run_context_for_read(tenant, workspace), result.run_id)
    assert final.status is RunStatus.COMPLETED

    untouched = await stack.po_case_repo.get(requester_context, case.id)
    assert untouched is not None
    assert untouched.state is CaseState.PO_CREATED
    await stack.dispose()


async def test_strict_prefix_refuses_self_approval(db_urls: DatabaseUrls) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    requester = uuid.uuid4()
    case = _case(tenant=tenant, workspace=workspace)

    stack = RunnerStack(db_urls.app)
    requester_context = _context(
        tenant=tenant,
        workspace=workspace,
        principal=requester,
        scopes=frozenset(
            {"supply_chain.po_case.read", "supply_chain.duty.ordering", "approvals.decide"}
        ),
    )
    await stack.po_case_repo.add(requester_context, case)
    result = await stack.advance_po_case(_matrix_requiring(CaseAction.REQUEST_DEPOSIT)).handle(
        requester_context, po_case_id=case.id, action=CaseAction.REQUEST_DEPOSIT
    )
    assert isinstance(result, CaseActionPendingApproval)
    record = await stack.run_store.get(stack.run_context_for_read(tenant, workspace), result.run_id)
    assert record.approval_request_id is not None

    with pytest.raises(ConflictError, match="separation of duties"):
        await stack.approve_and_resume.decide(
            approval_id=record.approval_request_id,
            approve=True,
            comment="tự duyệt luôn cho nhanh",
            context=requester_context,  # same principal as the requester
            authorization=stack.authz,
        )
    await stack.dispose()


async def test_strict_prefix_refuses_a_blank_comment(db_urls: DatabaseUrls) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    requester, approver = uuid.uuid4(), uuid.uuid4()
    case = _case(tenant=tenant, workspace=workspace)

    stack = RunnerStack(db_urls.app)
    requester_context = _context(
        tenant=tenant,
        workspace=workspace,
        principal=requester,
        scopes=frozenset({"supply_chain.po_case.read", "supply_chain.duty.ordering"}),
    )
    await stack.po_case_repo.add(requester_context, case)
    result = await stack.advance_po_case(_matrix_requiring(CaseAction.REQUEST_DEPOSIT)).handle(
        requester_context, po_case_id=case.id, action=CaseAction.REQUEST_DEPOSIT
    )
    assert isinstance(result, CaseActionPendingApproval)
    record = await stack.run_store.get(stack.run_context_for_read(tenant, workspace), result.run_id)
    assert record.approval_request_id is not None

    approver_context = _context(
        tenant=tenant,
        workspace=workspace,
        principal=approver,
        scopes=frozenset({"approvals.decide"}),
    )
    with pytest.raises(ConflictError, match="review comment"):
        await stack.approve_and_resume.decide(
            approval_id=record.approval_request_id,
            approve=True,
            comment="   ",
            context=approver_context,
            authorization=stack.authz,
        )
    await stack.dispose()
