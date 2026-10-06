"""Integration: BGĐ's review of a passed sample (step 6), through the real
runner, the Postgres checkpointer, `ApproveAndResumeService` and the
approval inbox (stage-1 ticket 02, ADR 0016, ADR 0020).

What only the real thing can show:

- `pass_sample` raises exactly one `bod_review` approval, stamped with the
  tenant's `required_scope`, and tells who may decide it and nobody else;
- the run pauses, a brand-new runner stack ("worker restart") resumes THE SAME
  run from its checkpoint on the decision, and the decider, not the
  requester, is the actor of BGĐ's step, its history row and its audit event;
- deciding is refused, with nothing moved, to the requester, to a decider
  without the stamped scope, to a decider of another workspace or tenant;
- a review that could not be raised (a refused start, a failed approval
  insert, a case that got there before S2) is raised by the reconcile lane,
  and a thread whose run failed is reused correctly by the Postgres saver.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

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

from dw_agent_runtime.adapters.checkpoint import SqlAlchemyCheckpointSaver
from dw_agent_runtime.adapters.langgraph_runner import LangGraphWorkflowRunner
from dw_agent_runtime.adapters.run_store import RunStatus, SqlWorkerRunStore
from dw_agent_runtime.approval_flow import ApproveAndResumeService
from dw_agent_runtime.autonomy import AutonomyApprovalPolicy
from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.model.budget import RunBudgetLedger
from dw_agent_runtime.registry import GraphRegistry, WorkerRegistry
from dw_kernel.errors import ConflictError, NotFoundError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.adapters.persistence.approval_queries import SqlPendingApprovalQuery
from dw_platform.adapters.persistence.identity_provisioning import SqlIdentityBootstrap
from dw_platform.adapters.persistence.membership_admin import SqlMembershipAdminRepository
from dw_platform.adapters.persistence.notifications import SqlNotificationRepository
from dw_platform.adapters.persistence.policy_overrides import SqlPolicyOverrideRepository
from dw_platform.adapters.persistence.scope_holders import SqlScopeHolders
from dw_platform.adapters.persistence.tenant_plans import SqlTenantPlans
from dw_platform.adapters.persistence.uow import SqlPlatformUnitOfWorkFactory
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.notifications import NotificationService
from dw_platform.domain.approval import ApprovalRequest, ApprovalStatus
from dw_platform.domain.audit import AuditEvent
from dw_platform.testing.seed_env import seed_test_env
from dw_supply_chain.adapters.persistence.case_document_repository import (
    SqlCaseDocumentRepository,
)
from dw_supply_chain.adapters.persistence.product_case_repository import (
    SqlProductCaseRepository,
    SqlWorkspacesAwaitingReview,
)
from dw_supply_chain.application.ports import (
    NewCaseDocument,
    ProductCaseReviewPort,
    ReviewRaise,
    ReviewRequester,
)
from dw_supply_chain.application.product_cases import AdvanceProductCase
from dw_supply_chain.application.product_reviews import EnsureBodReview, ReconcileBodReviews
from dw_supply_chain.domain.case_document import (
    CaseDocumentId,
    CaseKind,
    DocumentType,
    ObjectKey,
)
from dw_supply_chain.domain.product_development_case import (
    ProductAction,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
)
from dw_supply_chain.product_action_duties import load_supply_chain_product_action_duties
from dw_supply_chain.product_approvals import (
    PRODUCT_APPROVALS_POLICY_ID,
    load_supply_chain_product_approvals,
)
from dw_supply_chain.workflows.advance_product_case_graph import (
    APPROVAL_TYPE_PREFIX,
    BOD_REVIEW_APPROVAL_TYPE,
    BOD_REVIEW_CASE_KEY,
    GRAPH_VERSION,
    SUPERSEDED,
    WORKER_ID,
    build_advance_product_case_graph,
)

pytestmark = pytest.mark.integration

STALE_AFTER_SECONDS_LOCAL = 3600
_CONFIGS = REPO_ROOT / "configs"
_WORKER_CONFIG = _CONFIGS / "workers" / "supply_chain_advance_product_case.yaml"
_DUTIES = _CONFIGS / "policies" / "supply_chain_product_action_duties@1.0.0.yaml"
_APPROVALS = _CONFIGS / "policies" / "supply_chain_product_approvals@1.0.0.yaml"
# The seeded tenant (`seed_test_env`): active, on a plan, so scope holders and
# the tenant's plan are real rows.
ALPHA = uuid.UUID("d6b43d0e-c3c6-5dbc-bc08-150621bd9a5d")
ALPHA_WS = uuid.UUID("64764894-718d-5558-ba17-9a2949214063")
BETA = uuid.UUID("6634f09a-d1d7-54a6-aa23-f3f018f41f28")
BOD_SCOPE = "supply_chain.approve.bod"
PDF = b"%PDF-1.7\n" + b"x" * 64

OPERATOR_SCOPES = frozenset(
    {
        "supply_chain.product_case.read",
        "supply_chain.product_case.write",
        "supply_chain.duty.ordering",
        "supply_chain.duty.exceptions",
        "supply_chain.document.read",
        "supply_chain.document.write",
    }
)


class _Unmetered:
    def runs_per_day(self, plan_id: str) -> int | None:
        return None

    def spend_usd_per_day(self, plan_id: str) -> Decimal | None:
        return None


class _NoRunsLeft:
    def runs_per_day(self, plan_id: str) -> int | None:
        return 0

    def spend_usd_per_day(self, plan_id: str) -> Decimal | None:
        return None


@dataclass
class _SaveFailsOnce:
    """The graph's case records, whose first save loses a race: BGĐ's step
    fails AFTER the decision was committed."""

    inner: SqlProductCaseRepository
    failed: bool = False

    async def get(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> ProductDevelopmentCase | None:
        return await self.inner.get(context, case_id)

    async def save(
        self, context: AccessContext, case: ProductDevelopmentCase, *, audit: AuditEvent
    ) -> None:
        if not self.failed:
            self.failed = True
            raise ConflictError("the case was saved by someone else first")
        await self.inner.save(context, case, audit=audit)

    async def append_audit(self, context: AccessContext, audit: AuditEvent) -> None:
        await self.inner.append_audit(context, audit)


class ReviewStack:
    """One process worth of runtime objects, wired as
    `apps/api/src/dw_api/bootstrap/wiring.py` wires them: the review graph
    under its worker id, the shipped worker YAML and policies, the strict
    prefix on the approval flow."""

    def __init__(
        self, app_url: str, *, allowance: object | None = None, save_fails_once: bool = False
    ) -> None:
        self.engine = create_async_engine(app_url, poolclass=NullPool)
        self.sessions = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        ids, clock = Uuid4Generator(), SystemClock()
        self.cases = SqlProductCaseRepository(self.sessions)
        graph_repo: ProductCaseReviewPort = (
            _SaveFailsOnce(self.cases) if save_fails_once else self.cases
        )
        self.policies = SqlPolicyOverrideRepository(self.sessions)
        graphs = GraphRegistry()
        graphs.register(
            WORKER_ID,
            GRAPH_VERSION,
            lambda: build_advance_product_case_graph(graph_repo, ids, clock),
        )
        workers = WorkerRegistry(graph_registry=graphs)
        workers.load_file(_WORKER_CONFIG)
        self.uow_factory = SqlPlatformUnitOfWorkFactory(self.sessions)
        self.run_store = SqlWorkerRunStore(
            self.sessions, stale_run_after_seconds=STALE_AFTER_SECONDS_LOCAL
        )
        self.runner = LangGraphWorkflowRunner(
            worker_registry=workers,
            graph_registry=graphs,
            checkpoint_saver=SqlAlchemyCheckpointSaver(self.sessions),
            run_store=self.run_store,
            uow_factory=self.uow_factory,
            clock=clock,
            id_generator=ids,
            allowance=allowance or _Unmetered(),  # type: ignore[arg-type]
            budget=RunBudgetLedger(),
            approval_policy=AutonomyApprovalPolicy(),
        )
        self.decisions = ApproveAndResumeService(
            uow_factory=self.uow_factory,
            runner=self.runner,
            run_store=self.run_store,
            clock=clock,
            id_generator=ids,
            strict_approval_prefixes=frozenset({APPROVAL_TYPE_PREFIX}),
        )
        self.approvals = SqlPendingApprovalQuery(self.sessions, ScopeAuthorizationService())
        self.reviews = EnsureBodReview(
            runner=self.runner,
            approvals=self.approvals,
            holders=SqlScopeHolders(self.sessions),
            notifier=SqlNotificationRepository(self.sessions),
            policy_override_repo=self.policies,
            platform_default_approvals=load_supply_chain_product_approvals(_APPROVALS),
            ids=ids,
        )
        self.advance = AdvanceProductCase(
            repo=self.cases,
            documents=SqlCaseDocumentRepository(self.sessions),
            authz=ScopeAuthorizationService(),
            policy_override_repo=self.policies,
            platform_default_duties=load_supply_chain_product_action_duties(_DUTIES),
            reviews=self.reviews,
            ids=ids,
            clock=clock,
        )
        self.lane = ReconcileBodReviews(
            workspaces=SqlWorkspacesAwaitingReview(self.sessions),
            cases=self.cases,
            plans=SqlTenantPlans(self.sessions),
            reviews=self.reviews,
        )

    def lookup(self, workspace: uuid.UUID = ALPHA_WS) -> RunContext:
        return RunContext(
            run_id=uuid.uuid4(),
            tenant_id=ALPHA,
            workspace_id=workspace,
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


@dataclass(frozen=True)
class _Identity:
    subject: str
    email: str | None
    issuer: str = "https://issuer.test/realms/dw"
    name: str | None = None


@dataclass
class World:
    urls: DatabaseUrls
    migrator: AsyncEngine
    stack: ReviewStack


@pytest.fixture
async def world(db_urls: DatabaseUrls) -> AsyncIterator[World]:
    await seed_test_env(db_urls.migrator)
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    stack = ReviewStack(db_urls.app)
    yield World(db_urls, migrator, stack)
    await stack.dispose()
    await migrator.dispose()


def _context(
    scopes: frozenset[str],
    *,
    principal: uuid.UUID | None = None,
    roles: frozenset[str] = frozenset({"member"}),
    tenant: uuid.UUID = ALPHA,
    workspace: uuid.UUID = ALPHA_WS,
) -> AccessContext:
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=principal or uuid.uuid4(),
        roles=roles,
        scopes=scopes,
        plan_id="professional",
    )


async def _member(world: World, *roles: str, workspace: uuid.UUID = ALPHA_WS) -> AccessContext:
    """A real member of an Alpha workspace (its main one unless named) holding
    `roles`, with the scopes the catalogue gives them: who the inbox can
    address."""
    view = await SqlIdentityBootstrap(
        session_factory=world.stack.sessions,
        default_tenant_id=ALPHA,
        default_workspace_id=ALPHA_WS,
        auto_provision_default_membership=True,
    ).bootstrap(_Identity(subject=f"sub-{uuid.uuid4()}", email=f"{uuid.uuid4().hex[:8]}@bod.test"))
    admin = _context(frozenset({"platform.members.write"}), roles=frozenset({"org_admin"}))
    await SqlMembershipAdminRepository(world.stack.sessions).grant(
        admin,
        user_id=view.principal_id,
        workspace_id=workspace,
        role_keys=frozenset(roles),
        department="general",
        audit=AuditEvent(
            id=uuid.uuid4(),
            tenant_id=TenantId(ALPHA),
            workspace_id=WorkspaceId(ALPHA_WS),
            actor_id=UserId(admin.principal_id),
            action="platform.membership.grant",
            resource_type="membership",
            resource_id=str(view.principal_id),
            occurred_at=datetime.now(UTC),
        ),
    )
    async with world.migrator.connect() as conn:
        rows = await conn.execute(
            sa.text("SELECT scopes FROM platform.roles WHERE key = ANY(:k)"), {"k": list(roles)}
        )
        scopes = frozenset(scope for (held,) in rows for scope in held)
    return _context(
        scopes, principal=view.principal_id, roles=frozenset(roles), workspace=workspace
    )


async def _another_workspace(world: World) -> uuid.UUID:
    """A second workspace of Alpha: the same tenant, so RLS lets its members'
    reads through and only the workspace filter can stop them."""
    workspace = uuid.uuid4()
    async with world.migrator.begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO platform.workspaces (id, tenant_id, slug, name)"
                " VALUES (:id, :tenant, :slug, 'Phòng khác')"
            ),
            {"id": workspace, "tenant": ALPHA, "slug": f"bod-{workspace.hex[:8]}"},
        )
    return workspace


async def _testing(world: World, operator: AccessContext, tester: AccessContext) -> uuid.UUID:
    """A case of Alpha with a sample in test, round 1, under the real handlers'
    repository (steps as the S1 suite saves them)."""
    case = ProductDevelopmentCase.propose(
        id=ProductDevelopmentCaseId(uuid.uuid4()),
        tenant_id=TenantId(ALPHA),
        workspace_id=WorkspaceId(ALPHA_WS),
        proposal_code=f"DX-{uuid.uuid4().hex[:8]}",
        product_name="Nồi inox 3 đáy 24cm",
        category="Nồi",
        actor_id=operator.principal_id,
    )
    stack = world.stack
    await stack.cases.add(operator, case, audit=_audit(operator, case, "propose"))
    case.request_sample(actor_id=operator.principal_id, supplier_name="NCC Minh Long")
    await stack.cases.save(operator, case, audit=_audit(operator, case, "request_sample"))
    case.receive_sample(actor_id=tester.principal_id)
    await stack.cases.save(tester, case, audit=_audit(tester, case, "receive_sample"))
    return case.id.value


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


async def _evaluation(world: World, tester: AccessContext, case_id: uuid.UUID) -> uuid.UUID:
    document_id = CaseDocumentId(uuid.uuid4())
    await SqlCaseDocumentRepository(world.stack.sessions).add(
        tester,
        NewCaseDocument(
            id=document_id,
            case_kind=CaseKind.PRODUCT,
            case_id=case_id,
            doc_type=DocumentType.SAMPLE_EVALUATION,
            object_key=ObjectKey.build(
                tenant_id=ALPHA,
                workspace_id=ALPHA_WS,
                case_kind=CaseKind.PRODUCT,
                case_id=case_id,
                document_id=document_id.value,
            ).value,
            filename="bien-ban.pdf",
            content_type="application/pdf",
            size_bytes=len(PDF),
            sha256="0" * 64,
        ),
        audit=AuditEvent(
            id=uuid.uuid4(),
            tenant_id=TenantId(ALPHA),
            workspace_id=WorkspaceId(ALPHA_WS),
            actor_id=UserId(tester.principal_id),
            action="supply_chain.document.upload",
            resource_type="case_document",
            resource_id=str(document_id),
            occurred_at=SystemClock().now(),
        ),
    )
    return document_id.value


@dataclass
class Waiting:
    case_id: uuid.UUID
    review: ReviewRaise | None


async def _pass(
    world: World,
    operator: AccessContext,
    tester: AccessContext,
    *,
    stack: ReviewStack | None = None,
) -> Waiting:
    case_id = await _testing(world, operator, tester)
    evaluation = await _evaluation(world, tester, case_id)
    result = await (stack or world.stack).advance.handle(
        tester,
        case_id=ProductDevelopmentCaseId(case_id),
        action=ProductAction.PASS_SAMPLE,
        document_id=evaluation,
    )
    return Waiting(case_id, result.review)


async def _pending(world: World, case_id: uuid.UUID) -> ApprovalRequest | None:
    """Whether the review is raised: the raiser's bookkeeping read, which no
    audience narrows (a no-scope context still finds it)."""
    found = await world.stack.approvals.raised_by_payload(
        _context(frozenset()),
        approval_type=BOD_REVIEW_APPROVAL_TYPE,
        key=BOD_REVIEW_CASE_KEY,
        value=str(case_id),
    )
    return found


async def _count(world: World, sql: str, **params: object) -> int:
    async with world.migrator.connect() as conn:
        return int(await conn.scalar(sa.text(sql), params) or 0)


async def _case(world: World, case_id: uuid.UUID) -> ProductDevelopmentCase:
    found = await world.stack.cases.get(_context(frozenset()), ProductDevelopmentCaseId(case_id))
    assert found is not None
    return found


def _rnd() -> AccessContext:
    return _context(
        frozenset(
            {
                "supply_chain.product_case.read",
                "supply_chain.duty.rnd",
                "supply_chain.document.read",
                "supply_chain.document.write",
            }
        )
    )


# --- raising the review ---------------------------------------------------------------


async def test_passing_a_sample_raises_one_review_and_tells_who_may_decide(world: World) -> None:
    operator = _context(OPERATOR_SCOPES)
    # The tester holds both decide scopes too, and is still not told: the
    # strict prefix keeps the requester from deciding.
    tester = await _member(world, "sc_rnd", "sc_bod", "approver")
    bod = await _member(world, "sc_bod", "approver")
    approver_only = await _member(world, "approver")
    viewer_bod = await _member(world, "sc_bod")

    waiting = await _pass(world, operator, tester)

    assert waiting.review is ReviewRaise.RAISED
    approval = await _pending(world, waiting.case_id)
    assert approval is not None
    assert approval.approval_type == BOD_REVIEW_APPROVAL_TYPE
    assert approval.required_scope == BOD_SCOPE
    assert approval.requested_by.value == tester.principal_id
    assert set(approval.payload) == {
        "approval_type",
        "reason",
        "product_dev_case_id",
        "proposal_code",
        "product_name",
        "sample_round",
        "required_scope",
    }
    assert approval.run_id is not None
    run = await world.stack.run_store.get(world.stack.lookup(), approval.run_id)
    assert run.status is RunStatus.WAITING_APPROVAL
    assert run.requested_by == tester.principal_id

    inbox = NotificationService(SqlNotificationRepository(world.stack.sessions))
    code = (await _case(world, waiting.case_id)).proposal_code

    async def told(member: AccessContext) -> list[str | None]:
        items = (await inbox.latest(member)).items
        return [n.link for n in items if code in n.title]

    assert await told(bod) == ["/approvals"]
    assert await told(approver_only) == []
    assert await told(viewer_bod) == []
    assert await told(tester) == []

    # Asking again, from the step's ensure or from the lane, raises nothing.
    case = await _case(world, waiting.case_id)
    again = await world.stack.reviews.ensure(tester, case, ReviewRequester.from_context(tester))
    assert again is ReviewRaise.ALREADY_PENDING
    await world.stack.lane.run()
    assert (
        await _count(
            world,
            "SELECT count(*) FROM platform.approval_requests"
            " WHERE approval_type = :t AND payload->>'product_dev_case_id' = :c",
            t=BOD_REVIEW_APPROVAL_TYPE,
            c=str(waiting.case_id),
        )
        == 1
    )
    assert await told(bod) == ["/approvals"]


# --- deciding ------------------------------------------------------------------------


async def test_a_restarted_worker_resumes_the_same_run_and_bgd_is_the_actor(
    world: World,
) -> None:
    operator, tester = _context(OPERATOR_SCOPES), _rnd()
    waiting = await _pass(world, operator, tester)
    approval = await _pending(world, waiting.case_id)
    assert approval is not None and approval.run_id is not None
    await world.stack.dispose()

    # A brand-new process: nothing in memory from the one that paused.
    restarted = ReviewStack(world.urls.app)
    world.stack = restarted
    bod = _context(frozenset({"approvals.decide", BOD_SCOPE}))
    decided = await restarted.decisions.decide(
        approval_id=approval.id,
        approve=True,
        comment="Đồng ý giá vốn và chất lượng mẫu",
        context=bod,
        authorization=ScopeAuthorizationService(),
    )

    assert decided.status is ApprovalStatus.APPROVED
    run = await restarted.run_store.get(restarted.lookup(), approval.run_id)
    assert run.status is RunStatus.COMPLETED
    assert run.result is not None and run.result["outcome"] == "bod_approve"
    case = await _case(world, waiting.case_id)
    assert case.state is ProductDevState.PROFILE_IN_PROGRESS
    history = await restarted.cases.list_transitions(_context(frozenset()), case.id)
    assert (history[-1].action, history[-1].actor_id, history[-1].reason) == (
        ProductAction.BOD_APPROVE,
        bod.principal_id,
        None,
    )
    assert (
        await _count(
            world,
            "SELECT count(*) FROM platform.audit_events WHERE resource_id = :c"
            " AND action = 'supply_chain.product_case.bod_approve' AND actor_id = :a",
            c=str(case.id),
            a=bod.principal_id,
        )
        == 1
    )


async def test_bgd_not_approving_cancels_with_the_comment_as_the_reason(world: World) -> None:
    operator, tester = _context(OPERATOR_SCOPES), _rnd()
    waiting = await _pass(world, operator, tester)
    approval = await _pending(world, waiting.case_id)
    assert approval is not None
    bod = _context(frozenset({"approvals.decide", BOD_SCOPE}))

    await world.stack.decisions.decide(
        approval_id=approval.id,
        approve=False,
        comment="Giá vốn vượt mục tiêu 15%",
        context=bod,
        authorization=ScopeAuthorizationService(),
    )

    case = await _case(world, waiting.case_id)
    assert case.state is ProductDevState.CANCELLED
    history = await world.stack.cases.list_transitions(_context(frozenset()), case.id)
    rejections = [t for t in history if t.action is ProductAction.BOD_REJECT]
    assert [(t.actor_id, t.reason) for t in rejections] == [
        (bod.principal_id, "Giá vốn vượt mục tiêu 15%")
    ]


async def test_the_requester_cannot_decide_their_own_review_and_a_comment_is_required(
    world: World,
) -> None:
    operator = _context(OPERATOR_SCOPES)
    tester = _context(_rnd().scopes | {"approvals.decide", BOD_SCOPE}, principal=uuid.uuid4())
    waiting = await _pass(world, operator, tester)
    approval = await _pending(world, waiting.case_id)
    assert approval is not None

    with pytest.raises(ConflictError, match="separation of duties"):
        await world.stack.decisions.decide(
            approval_id=approval.id,
            approve=True,
            comment="tự duyệt",
            context=tester,
            authorization=ScopeAuthorizationService(),
        )
    with pytest.raises(ConflictError, match="review comment"):
        await world.stack.decisions.decide(
            approval_id=approval.id,
            approve=True,
            comment="  ",
            context=_context(frozenset({"approvals.decide", BOD_SCOPE})),
            authorization=ScopeAuthorizationService(),
        )
    assert (await _case(world, waiting.case_id)).state is ProductDevState.PENDING_BOD_REVIEW


async def _assert_untouched(world: World, waiting: Waiting, approval: ApprovalRequest) -> None:
    assert (await _case(world, waiting.case_id)).state is ProductDevState.PENDING_BOD_REVIEW
    still = await _pending(world, waiting.case_id)
    assert still is not None and still.id == approval.id
    assert approval.run_id is not None
    run = await world.stack.run_store.get(world.stack.lookup(), approval.run_id)
    assert run.status is RunStatus.WAITING_APPROVAL


@pytest.mark.parametrize("approve", [True, False])
async def test_the_decide_right_without_bgds_scope_does_not_find_it_and_nothing_moves(
    world: World, approve: bool
) -> None:
    """Platform ADR 0004 amendment (ADR 0020 sửa đổi 2026-10-07): a manager
    without BGĐ's scope may not decide the review, so they do not find it;
    a 404, not a 403 that would confirm it and name the scope."""
    operator, tester = _context(OPERATOR_SCOPES), _rnd()
    waiting = await _pass(world, operator, tester)
    approval = await _pending(world, waiting.case_id)
    assert approval is not None

    with pytest.raises(NotFoundError):
        await world.stack.decisions.decide(
            approval_id=approval.id,
            approve=approve,
            comment="tôi là quản lý, tôi duyệt",
            context=_context(frozenset({"approvals.decide"}), roles=frozenset({"manager"})),
            authorization=ScopeAuthorizationService(),
        )
    await _assert_untouched(world, waiting, approval)


async def test_the_requester_cannot_withdraw_their_own_review_either(world: World) -> None:
    """Withdrawing your own request needs neither `approvals.decide` nor the
    stamped scope, so for this type the strict prefix is the only guard. Here
    a rejection is BGĐ's step (it cancels the case): the R&D tester, with no
    cancel duty and no BGĐ scope, must not take it under BGĐ's name."""
    operator, tester = _context(OPERATOR_SCOPES), _rnd()
    waiting = await _pass(world, operator, tester)
    approval = await _pending(world, waiting.case_id)
    assert approval is not None

    with pytest.raises(ConflictError, match="separation of duties"):
        await world.stack.decisions.decide(
            approval_id=approval.id,
            approve=False,
            comment="rút lại",
            context=tester,
            authorization=ScopeAuthorizationService(),
        )
    await _assert_untouched(world, waiting, approval)


@pytest.mark.parametrize(
    "tenant",
    [
        pytest.param(ALPHA, id="another-workspace-same-tenant"),
        pytest.param(BETA, id="another-tenant"),
    ],
)
async def test_bgd_of_another_workspace_or_tenant_finds_no_review(
    world: World, tenant: uuid.UUID
) -> None:
    operator, tester = _context(OPERATOR_SCOPES), _rnd()
    waiting = await _pass(world, operator, tester)
    approval = await _pending(world, waiting.case_id)
    assert approval is not None
    if tenant == ALPHA:
        # A real BGĐ member of Alpha's other workspace: sc_bod and approver
        # granted THERE, so they hold both scopes deciding needs.
        elsewhere = await _member(
            world, "sc_bod", "approver", workspace=await _another_workspace(world)
        )
        assert {"approvals.decide", BOD_SCOPE} <= elsewhere.scopes
    else:
        elsewhere = _context(
            frozenset({"approvals.decide", BOD_SCOPE}), tenant=tenant, workspace=uuid.uuid4()
        )

    with pytest.raises(NotFoundError):
        await world.stack.decisions.decide(
            approval_id=approval.id,
            approve=True,
            comment="Đồng ý",
            context=elsewhere,
            authorization=ScopeAuthorizationService(),
        )
    # Nor does their /approvals inbox list it, nor the case page's lookup find
    # it; Alpha's main workspace, the control, does list it.
    total, listed = await world.stack.approvals.list_pending_by_type_prefix(
        elsewhere, prefix=APPROVAL_TYPE_PREFIX, limit=50
    )
    assert (total, listed) == (0, [])
    _, home = await world.stack.approvals.list_pending_by_type_prefix(
        _context(frozenset({"approvals.decide", BOD_SCOPE})), prefix=APPROVAL_TYPE_PREFIX, limit=50
    )
    assert approval.id in {row.id for row in home}
    assert (
        await world.stack.approvals.pending_by_payload(
            elsewhere,
            approval_type=BOD_REVIEW_APPROVAL_TYPE,
            key=BOD_REVIEW_CASE_KEY,
            value=str(waiting.case_id),
        )
        is None
    )
    await _assert_untouched(world, waiting, approval)


# --- the stamp ------------------------------------------------------------------------


async def test_a_tenant_override_reaches_reviews_raised_after_it_only(world: World) -> None:
    operator, tester = _context(OPERATOR_SCOPES), _rnd()
    before = await _pass(world, operator, tester)
    override = load_supply_chain_product_approvals(_APPROVALS).model_dump(mode="json")
    override["bod_review"] = {"required_scope": "supply_chain.approve.ceo"}
    admin = _context(frozenset())
    await world.stack.policies.put(
        admin,
        PRODUCT_APPROVALS_POLICY_ID,
        override,
        audit=AuditEvent(
            id=uuid.uuid4(),
            tenant_id=TenantId(ALPHA),
            workspace_id=WorkspaceId(ALPHA_WS),
            actor_id=UserId(admin.principal_id),
            action="supply_chain.product_approvals.override_set",
            resource_type="product_approvals",
            resource_id=PRODUCT_APPROVALS_POLICY_ID,
            occurred_at=SystemClock().now(),
        ),
    )
    try:
        after = await _pass(world, operator, tester)

        raised_before = await _pending(world, before.case_id)
        raised_after = await _pending(world, after.case_id)
        assert raised_before is not None and raised_after is not None
        assert raised_before.required_scope == BOD_SCOPE
        assert raised_after.required_scope == "supply_chain.approve.ceo"
        # The stamp is what decide reads: BGĐ still decides the older one,
        # and no longer the newer one, which they no longer even find.
        with pytest.raises(NotFoundError):
            await world.stack.decisions.decide(
                approval_id=raised_after.id,
                approve=True,
                comment="Đồng ý",
                context=_context(frozenset({"approvals.decide", BOD_SCOPE})),
                authorization=ScopeAuthorizationService(),
            )
        await world.stack.decisions.decide(
            approval_id=raised_before.id,
            approve=True,
            comment="Đồng ý",
            context=_context(frozenset({"approvals.decide", BOD_SCOPE})),
            authorization=ScopeAuthorizationService(),
        )
        assert (await _case(world, before.case_id)).state is ProductDevState.PROFILE_IN_PROGRESS
    finally:
        async with world.migrator.begin() as conn:
            await conn.execute(
                sa.text(
                    "DELETE FROM platform.policy_overrides WHERE tenant_id = :t AND policy_id = :p"
                ),
                {"t": ALPHA, "p": PRODUCT_APPROVALS_POLICY_ID},
            )


# --- a case that moved on while BGĐ thought ----------------------------------------------


async def test_cancelled_before_bgd_decides_the_decision_is_superseded(world: World) -> None:
    operator, tester = _context(OPERATOR_SCOPES), _rnd()
    waiting = await _pass(world, operator, tester)
    approval = await _pending(world, waiting.case_id)
    assert approval is not None and approval.run_id is not None
    cancelled = await world.stack.advance.handle(
        operator,
        case_id=ProductDevelopmentCaseId(waiting.case_id),
        action=ProductAction.CANCEL,
        reason="Dự án dừng",
    )
    assert cancelled.case.state is ProductDevState.CANCELLED
    # Open item: the review stays in /approvals until decided.
    assert await _pending(world, waiting.case_id) is not None

    bod = _context(frozenset({"approvals.decide", BOD_SCOPE}))
    await world.stack.decisions.decide(
        approval_id=approval.id,
        approve=True,
        comment="Đồng ý",
        context=bod,
        authorization=ScopeAuthorizationService(),
    )

    run = await world.stack.run_store.get(world.stack.lookup(), approval.run_id)
    assert run.status is RunStatus.COMPLETED
    assert run.result is not None and run.result["outcome"] == SUPERSEDED
    case = await _case(world, waiting.case_id)
    assert case.state is ProductDevState.CANCELLED
    history = await world.stack.cases.list_transitions(_context(frozenset()), case.id)
    assert not [t for t in history if t.action in {ProductAction.BOD_APPROVE}]
    assert (
        await _count(
            world,
            "SELECT count(*) FROM platform.audit_events WHERE resource_id = :c"
            " AND action = 'supply_chain.product_case.bod_review_superseded' AND actor_id = :a",
            c=str(case.id),
            a=bod.principal_id,
        )
        == 1
    )


async def test_decided_before_a_cancel_the_cancel_acts_on_the_new_state(world: World) -> None:
    operator, tester = _context(OPERATOR_SCOPES), _rnd()
    waiting = await _pass(world, operator, tester)
    approval = await _pending(world, waiting.case_id)
    assert approval is not None
    await world.stack.decisions.decide(
        approval_id=approval.id,
        approve=True,
        comment="Đồng ý",
        context=_context(frozenset({"approvals.decide", BOD_SCOPE})),
        authorization=ScopeAuthorizationService(),
    )

    cancelled = await world.stack.advance.handle(
        operator,
        case_id=ProductDevelopmentCaseId(waiting.case_id),
        action=ProductAction.CANCEL,
        reason="Đổi kế hoạch",
    )

    assert cancelled.case.state is ProductDevState.CANCELLED
    history = await world.stack.cases.list_transitions(_context(frozenset()), cancelled.case.id)
    assert [t.action for t in history][-2:] == [ProductAction.BOD_APPROVE, ProductAction.CANCEL]
    assert history[-1].from_state is ProductDevState.PROFILE_IN_PROGRESS


# --- reviews a start missed ------------------------------------------------------------


async def test_a_refused_start_keeps_the_step_and_the_lane_raises_the_review(
    world: World,
) -> None:
    operator, tester = _context(OPERATOR_SCOPES), _rnd()
    refusing = ReviewStack(world.urls.app, allowance=_NoRunsLeft())
    try:
        waiting = await _pass(world, operator, tester, stack=refusing)
    finally:
        await refusing.dispose()

    assert waiting.review is ReviewRaise.NOT_RAISED
    assert (await _case(world, waiting.case_id)).state is ProductDevState.PENDING_BOD_REVIEW
    assert await _pending(world, waiting.case_id) is None

    await world.stack.lane.run()

    approval = await _pending(world, waiting.case_id)
    assert approval is not None
    # Requested by the tester who passed the sample, read from the history.
    assert approval.requested_by.value == tester.principal_id
    assert approval.required_scope == BOD_SCOPE


async def test_a_case_waiting_since_before_s2_is_backfilled_by_the_lane(world: World) -> None:
    """S1 saved `pending_bod_review` with no run at all: the repository write
    alone, as `pass_sample` did then."""
    operator, tester = _context(OPERATOR_SCOPES), _rnd()
    case_id = await _testing(world, operator, tester)
    evaluation_id = await _evaluation(world, tester, case_id)
    case = await _case(world, case_id)
    document = await SqlCaseDocumentRepository(world.stack.sessions).get(
        tester, CaseDocumentId(evaluation_id)
    )
    case.pass_sample(actor_id=tester.principal_id, evaluation=document)
    await world.stack.cases.save(tester, case, audit=_audit(tester, case, "pass_sample"))
    assert await _pending(world, case_id) is None

    await world.stack.lane.run()

    approval = await _pending(world, case_id)
    assert approval is not None
    assert approval.requested_by.value == tester.principal_id


async def test_a_thread_whose_run_failed_is_reused_and_decided_correctly(world: World) -> None:
    """Measured on the Postgres saver (failure-modes #4): the first attempt's
    approval insert fails (a malformed stamped scope, refused by its CHECK),
    the run ends failed over a checkpoint paused at the interrupt; the next
    attempt on the SAME thread raises a fresh review, and deciding it resumes
    the new run, not the failed one."""
    operator, tester = _context(OPERATOR_SCOPES), _rnd()
    admin = _context(frozenset())
    broken = load_supply_chain_product_approvals(_APPROVALS).model_dump(mode="json")
    broken["bod_review"] = {"required_scope": "not a scope!"}
    audit = AuditEvent(
        id=uuid.uuid4(),
        tenant_id=TenantId(ALPHA),
        workspace_id=WorkspaceId(ALPHA_WS),
        actor_id=UserId(admin.principal_id),
        action="supply_chain.product_approvals.override_set",
        resource_type="product_approvals",
        resource_id=PRODUCT_APPROVALS_POLICY_ID,
        occurred_at=SystemClock().now(),
    )
    await world.stack.policies.put(admin, PRODUCT_APPROVALS_POLICY_ID, broken, audit=audit)
    try:
        waiting = await _pass(world, operator, tester)
    finally:
        async with world.migrator.begin() as conn:
            await conn.execute(
                sa.text(
                    "DELETE FROM platform.policy_overrides WHERE tenant_id = :t AND policy_id = :p"
                ),
                {"t": ALPHA, "p": PRODUCT_APPROVALS_POLICY_ID},
            )
    assert waiting.review is ReviewRaise.NOT_RAISED
    failed_runs = await _count(
        world,
        "SELECT count(*) FROM platform.worker_runs WHERE subject_ref = :s AND status = 'failed'",
        s=f"product_dev_case:{waiting.case_id}",
    )
    assert failed_runs == 1

    await world.stack.lane.run()

    approval = await _pending(world, waiting.case_id)
    assert approval is not None and approval.run_id is not None
    assert approval.required_scope == BOD_SCOPE
    threads = await _count(
        world,
        "SELECT count(DISTINCT thread_id) FROM platform.worker_runs WHERE subject_ref = :s",
        s=f"product_dev_case:{waiting.case_id}",
    )
    assert threads == 1  # the same thread, a new run
    bod = _context(frozenset({"approvals.decide", BOD_SCOPE}))
    await world.stack.decisions.decide(
        approval_id=approval.id,
        approve=False,
        comment="Không đạt mục tiêu giá",
        context=bod,
        authorization=ScopeAuthorizationService(),
    )
    case = await _case(world, waiting.case_id)
    assert case.state is ProductDevState.CANCELLED
    run = await world.stack.run_store.get(world.stack.lookup(), approval.run_id)
    assert run.status is RunStatus.COMPLETED


async def test_a_run_that_fails_after_bgd_decided_is_raised_again_and_applied_once(
    world: World,
) -> None:
    """Measured on the Postgres saver (failure-modes #4), the shape the
    earlier measurement did not cover: BGĐ decides, the decision commits, and
    `apply` then fails (its save loses a race). The checkpoint holds
    `request_review` done and `apply` errored, not a pause at the interrupt.
    The lane's fresh input on the SAME thread must start from START: a new
    review is raised, and BGĐ's step applies exactly once, from the new
    decision, never from the old one."""
    operator, tester = _context(OPERATOR_SCOPES), _rnd()
    flaky = ReviewStack(world.urls.app, save_fails_once=True)
    try:
        waiting = await _pass(world, operator, tester, stack=flaky)
        first = await _pending(world, waiting.case_id)
        assert first is not None and first.run_id is not None
        with pytest.raises(ConflictError, match="saved by someone else"):
            await flaky.decisions.decide(
                approval_id=first.id,
                approve=True,
                comment="Đồng ý",
                context=_context(frozenset({"approvals.decide", BOD_SCOPE})),
                authorization=ScopeAuthorizationService(),
            )
    finally:
        await flaky.dispose()
    assert (await _case(world, waiting.case_id)).state is ProductDevState.PENDING_BOD_REVIEW
    assert await _pending(world, waiting.case_id) is None  # decided, no longer pending
    failed = await world.stack.run_store.get(world.stack.lookup(), first.run_id)
    assert failed.status is RunStatus.FAILED

    await world.stack.lane.run()

    second = await _pending(world, waiting.case_id)
    assert second is not None and second.run_id is not None
    assert second.id != first.id and second.run_id != first.run_id
    assert (
        await _count(
            world,
            "SELECT count(DISTINCT thread_id) FROM platform.worker_runs WHERE subject_ref = :s",
            s=f"product_dev_case:{waiting.case_id}",
        )
        == 1
    )
    # Raising it applied nothing: the first decision is not replayed.
    assert (await _case(world, waiting.case_id)).state is ProductDevState.PENDING_BOD_REVIEW
    bod = _context(frozenset({"approvals.decide", BOD_SCOPE}))
    await world.stack.decisions.decide(
        approval_id=second.id,
        approve=False,
        comment="Giá vốn vượt mục tiêu",
        context=bod,
        authorization=ScopeAuthorizationService(),
    )
    case = await _case(world, waiting.case_id)
    assert case.state is ProductDevState.CANCELLED
    history = await world.stack.cases.list_transitions(_context(frozenset()), case.id)
    bgd_steps = [
        (t.action, t.actor_id)
        for t in history
        if t.action in {ProductAction.BOD_APPROVE, ProductAction.BOD_REJECT}
    ]
    assert bgd_steps == [(ProductAction.BOD_REJECT, bod.principal_id)]
    run = await world.stack.run_store.get(world.stack.lookup(), second.run_id)
    assert run.status is RunStatus.COMPLETED
    assert run.result is not None and run.result["outcome"] == "bod_reject"


# --- the lane's two system reads ---------------------------------------------------------


async def test_the_cross_tenant_read_is_ids_of_waiting_workspaces_only(world: World) -> None:
    """`workspaces_awaiting_bod_review()` crosses tenants (SECURITY DEFINER),
    so it returns (tenant, workspace) and nothing else, and only for a case
    actually waiting for BGĐ; every read after it is under that tenant's RLS."""
    operator, tester = _context(OPERATOR_SCOPES), _rnd()
    waiting = await _pass(world, operator, tester)
    still_testing = await _testing(world, operator, tester)

    async with world.stack.sessions() as session, session.begin():
        result = await session.execute(
            sa.text("SELECT * FROM supply_chain.workspaces_awaiting_bod_review()")
        )
        columns = list(result.keys())
        pairs = set(result.tuples().all())
    assert columns == ["tenant_id", "workspace_id"]
    assert (ALPHA, ALPHA_WS) in pairs
    assert waiting.case_id not in {value for pair in pairs for value in pair}
    assert still_testing not in {value for pair in pairs for value in pair}
    # And the application role reads no case without setting a tenant.
    async with world.stack.sessions() as session, session.begin():
        visible = await session.scalar(
            sa.text("SELECT count(*) FROM supply_chain.product_dev_cases")
        )
    assert visible == 0


async def test_the_tenant_plan_is_the_tenants_and_an_unknown_tenant_has_none(
    world: World,
) -> None:
    plans = SqlTenantPlans(world.stack.sessions)
    assert await plans.plan_of(ALPHA) == "professional"
    assert await plans.plan_of(BETA) == "basic"
    assert await plans.plan_of(uuid.uuid4()) is None


async def test_a_tenant_that_is_not_active_has_no_plan_and_the_lane_starts_nothing(
    world: World,
) -> None:
    """A locked tenant keeps its entitlements row; only its status changed.
    The lane must still start no run for it, as its people could not."""
    operator, tester = _context(OPERATOR_SCOPES), _rnd()
    refusing = ReviewStack(world.urls.app, allowance=_NoRunsLeft())
    try:
        waiting = await _pass(world, operator, tester, stack=refusing)
    finally:
        await refusing.dispose()
    assert await _pending(world, waiting.case_id) is None

    lock = sa.text("UPDATE platform.tenants SET status = :status WHERE id = :id")
    async with world.migrator.begin() as conn:
        await conn.execute(lock, {"status": "locked", "id": ALPHA})
    try:
        assert await _count(
            world, "SELECT count(*) FROM platform.entitlements WHERE tenant_id = :id", id=ALPHA
        )
        assert await SqlTenantPlans(world.stack.sessions).plan_of(ALPHA) is None

        await world.stack.lane.run()

        assert await _pending(world, waiting.case_id) is None
    finally:
        async with world.migrator.begin() as conn:
            await conn.execute(lock, {"status": "active", "id": ALPHA})

    # The same lane, the tenant active again: the status was the only reason.
    await world.stack.lane.run()
    assert await _pending(world, waiting.case_id) is not None


async def test_a_pending_review_is_shown_to_bgd_and_its_requester_only(world: World) -> None:
    """Platform ADR 0004 amendment (2026-10-07): a stamped request is shown only
    to who may decide it and to its requester. The case page's lookup and the
    brief's count read it as the inbox does; a case reader without BGĐ's
    scopes finds nothing. The raiser's bookkeeping read (`raised_by_payload`)
    is not a person's and still finds it with no scope at all, or the reconcile
    lane would raise the review a second time."""
    operator, tester = _context(OPERATOR_SCOPES), _rnd()
    waiting = await _pass(world, operator, tester)
    approval = await _pending(world, waiting.case_id)
    assert approval is not None and approval.required_scope == BOD_SCOPE

    async def shown(reader: AccessContext) -> tuple[bool, bool]:
        on_page = await world.stack.approvals.pending_by_payload(
            reader,
            approval_type=BOD_REVIEW_APPROVAL_TYPE,
            key=BOD_REVIEW_CASE_KEY,
            value=str(waiting.case_id),
        )
        _, counted = await world.stack.approvals.list_pending_by_type_prefix(
            reader, prefix=APPROVAL_TYPE_PREFIX, limit=50
        )
        return on_page is not None, approval.id in {row.id for row in counted}

    requester = _context(frozenset(), principal=approval.requested_by.value)
    assert await shown(operator) == (False, False)
    assert await shown(_context(frozenset({"approvals.decide"}))) == (False, False)
    assert await shown(_context(frozenset({BOD_SCOPE}))) == (False, False)
    assert await shown(_context(frozenset(), roles=frozenset({"platform_admin"}))) == (
        False,
        False,
    )
    assert await shown(_context(frozenset({"approvals.decide", BOD_SCOPE}))) == (True, True)
    assert await shown(requester) == (True, True)
