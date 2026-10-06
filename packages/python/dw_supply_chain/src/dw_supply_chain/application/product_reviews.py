"""Step 6: raising BGĐ's review of a passed sample, once per sample round
(stage-1 ticket 02, ADR 0016 and ADR 0020).

`EnsureBodReview.ensure` is the one way the review is raised. Two callers:
the step command, right after a step leaves a case in `pending_bod_review`
(its own transaction is already committed), and the worker's reconcile lane
(`ReconcileBodReviews`), for a case still waiting with no review: its start
was refused or failed, or it reached the state before this slice existed.

- **Idempotent on the platform's own seam, not on a check.** The run's thread
  is derived from (case, `bod_review`, sample round), and
  `uq_worker_runs_active_thread` lets one unfinished run hold a thread, so a
  second ensure of the same round collides (`ConflictError` naming the
  thread) and starts nothing. Each attempt has a fresh run id. A thread whose
  last run failed is free again, and invoking it with new input starts a new
  pass from START. Measured on 2026-10-06 against LangGraph 1.2.11 on the
  Postgres saver for both failure shapes: paused at the interrupt with no
  approval written, and `apply` failed after BGĐ decided (the old decision is
  not replayed; a new review is raised and applies once).
- **"Raised" is read back, not assumed.** `start` returns normally when the
  approval row could not be written (the runner ends that run failed), so
  ensure asks the approval inbox for the case's pending review after start.
- **Who decides is stamped now.** `required_scope` is resolved from the
  tenant's `supply_chain_product_approvals` here, at the moment the review is
  raised, and travels in the interrupt payload onto the approval row.
- **Who is told.** Only when THIS call raised the review: the members of the
  case's workspace who hold both what deciding needs (`approvals.decide` and
  the stamped scope), less the requester, who may not decide it. Never from
  the graph node, which re-executes on resume. A failed delivery is logged
  and does not un-raise the review: the approval is in `/approvals` either way.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from dw_agent_runtime.contracts import RunContext
from dw_kernel.errors import ConflictError
from dw_kernel.pagination import MAX_PAGE_SIZE, page_request
from dw_kernel.ports import IdGenerator
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import PolicyOverridePort
from dw_platform.domain.approval import APPROVALS_DECIDE
from dw_supply_chain.application.follow_up_sweep import sweep_context
from dw_supply_chain.application.handlers import resolve_product_approvals
from dw_supply_chain.application.ports import (
    PendingApprovalRecord,
    PendingApprovalsPort,
    ProductCaseListFilter,
    ProductCaseRepositoryPort,
    ReviewNotifierPort,
    ReviewRaise,
    ReviewRequester,
    ReviewRunStarterPort,
    ScopeHoldersPort,
    TenantPlanPort,
    WorkspacesAwaitingReviewPort,
)
from dw_supply_chain.domain.product_development_case import (
    ProductDevelopmentCase,
    ProductDevState,
)
from dw_supply_chain.product_approvals import SupplyChainProductApprovals
from dw_supply_chain.workflows.advance_product_case_graph import (
    BOD_REVIEW_APPROVAL_TYPE,
    BOD_REVIEW_CASE_KEY,
    WORKER_ID,
    WORKER_VERSION,
)

logger = logging.getLogger(__name__)

# How many reviews the reconcile lane tries to raise per tick.
RECONCILE_BATCH = 20

_THREAD_NAMESPACE = uuid.UUID("0b0d7e7e-5d1e-4c6a-9b6e-7c0d5e6f0a06")


def bod_review_thread_id(case_id: uuid.UUID, sample_round: int) -> uuid.UUID:
    """The thread BGĐ's review of one sample round runs on: the same for every
    attempt, so two attempts meet at `uq_worker_runs_active_thread`."""
    return uuid.uuid5(_THREAD_NAMESPACE, f"{case_id}:bod_review:{sample_round}")


@dataclass(frozen=True)
class EnsureBodReview:
    """Implements `BodReviewPort`."""

    runner: ReviewRunStarterPort
    approvals: PendingApprovalsPort
    holders: ScopeHoldersPort
    notifier: ReviewNotifierPort
    policy_override_repo: PolicyOverridePort
    platform_default_approvals: SupplyChainProductApprovals
    ids: IdGenerator

    async def pending(
        self, context: AccessContext, case: ProductDevelopmentCase
    ) -> PendingApprovalRecord | None:
        """The case's undecided review in `context`'s workspace, if any."""
        return await self.approvals.pending_by_payload(
            context,
            approval_type=BOD_REVIEW_APPROVAL_TYPE,
            key=BOD_REVIEW_CASE_KEY,
            value=str(case.id),
        )

    async def ensure(
        self, context: AccessContext, case: ProductDevelopmentCase, requester: ReviewRequester
    ) -> ReviewRaise:
        """Raise the review unless it exists. A refused start (plan quota,
        spend ceiling) or any other failure propagates: the caller decides
        whether that undoes anything (the step command: no)."""
        if case.state is not ProductDevState.PENDING_BOD_REVIEW:
            return ReviewRaise.NOT_WAITING
        if await self.pending(context, case) is not None:
            return ReviewRaise.ALREADY_PENDING
        policy = await resolve_product_approvals(
            context, self.policy_override_repo, self.platform_default_approvals
        )
        required_scope = policy.bod_review.required_scope
        thread_id = bod_review_thread_id(case.id.value, case.sample_round)
        run_id = self.ids.new_uuid()
        try:
            await self.runner.start(
                run_context=RunContext(
                    run_id=run_id,
                    thread_id=thread_id,
                    tenant_id=context.tenant_id,
                    workspace_id=case.workspace_id.value,
                    actor_id=requester.principal_id,
                    worker_id=WORKER_ID,
                    worker_version=WORKER_VERSION,
                    channel=requester.channel,
                    plan_id=requester.plan_id,
                    roles=requester.roles,
                    scopes=requester.scopes,
                    trace_id=str(run_id),
                    subject_ref=f"product_dev_case:{case.id}",
                ),
                input_payload={
                    BOD_REVIEW_CASE_KEY: str(case.id),
                    "proposal_code": case.proposal_code,
                    "product_name": case.product_name,
                    "sample_round": case.sample_round,
                    "required_scope": required_scope,
                },
            )
        except ConflictError as exc:
            # Only the thread's own claim means "someone else is raising it".
            if exc.details.get("thread_id") != str(thread_id):
                raise
            found = await self.pending(context, case)
            return ReviewRaise.ALREADY_PENDING if found is not None else ReviewRaise.NOT_RAISED
        raised = await self.pending(context, case)
        if raised is None:
            return ReviewRaise.NOT_RAISED
        await self._notify(context, case, raised, required_scope, requester.principal_id)
        return ReviewRaise.RAISED

    async def _notify(
        self,
        context: AccessContext,
        case: ProductDevelopmentCase,
        approval: PendingApprovalRecord,
        required_scope: str,
        requester: uuid.UUID,
    ) -> None:
        workspace = case.workspace_id.value
        try:
            stamped = set(
                await self.holders.holding(context, workspace, frozenset({required_scope}))
            )
            deciders = set(
                await self.holders.holding(context, workspace, frozenset({APPROVALS_DECIDE}))
            )
            recipients = sorted(stamped & deciders - {requester})
            await self.notifier.deliver(
                context,
                recipients=recipients,
                source_key=f"supply_chain.bod_review:{approval.id}",
                title=f"Chờ BGĐ duyệt mẫu: {case.proposal_code}",
                body=(
                    f"{case.product_name}, vòng mẫu {case.sample_round}: mẫu đã đạt,"
                    " chờ người có quyền BGĐ duyệt."
                ),
                link="/approvals",
            )
        except Exception:
            logger.exception(
                "BGĐ review raised but its notification failed",
                extra={"case_id": str(case.id), "approval_id": str(approval.id)},
            )


@dataclass(frozen=True, slots=True)
class ReconcileOutcome:
    attempted: int = 0
    raised: int = 0
    failed: int = 0


@dataclass(frozen=True)
class ReconcileBodReviews:
    """The worker lane `supply_chain_product_review_reconcile`: raises BGĐ's
    review for every case waiting in `pending_bod_review` with none pending.
    The backfill for cases that reached the state before S2, and the retry for
    a start that was refused or failed.

    A system process, like the follow-up sweep: it reads under a context with
    no roles and no scopes, one (tenant, workspace) at a time, and crosses
    tenants only to learn which to visit. The run it starts is the requester's
    review: requested by whoever took the step that left the case waiting
    (read from the case's history, a stamp, never a fresh guess), so they still
    cannot decide it, and counted against the tenant's plan as any run is. It
    carries no scopes: the graph authorizes nothing with them.

    At most `batch` starts per tick, and a tenant's first failed start ends
    its turn for the tick: refused (plan quota, spend ceiling, both
    tenant-wide) or run without raising the review. So a tenant whose reviews
    cannot be raised costs one attempt (and at most one failed run) per tick,
    and every tenant sorted after it is still visited; the next tick retries
    it. A workspace whose reads fail is logged and the next is visited."""

    workspaces: WorkspacesAwaitingReviewPort
    cases: ProductCaseRepositoryPort
    plans: TenantPlanPort
    reviews: EnsureBodReview
    batch: int = RECONCILE_BATCH

    async def run(self) -> ReconcileOutcome:
        total = ReconcileOutcome()
        ended: set[uuid.UUID] = set()
        for tenant_id, workspace_id in await self.workspaces.awaiting_bod_review():
            if total.attempted >= self.batch:
                break
            if tenant_id in ended:
                continue
            try:
                total, ends_turn = await self._workspace(
                    sweep_context(tenant_id, workspace_id), total
                )
            except Exception:
                # A read failed, before any start: the tenant's next workspace
                # is still visited, so one broken workspace starves no other.
                logger.exception(
                    "BGĐ review reconcile failed for a workspace",
                    extra={"tenant_id": str(tenant_id), "workspace_id": str(workspace_id)},
                )
                total = ReconcileOutcome(total.attempted, total.raised, total.failed + 1)
                continue
            if ends_turn:
                ended.add(tenant_id)
        return total

    async def _workspace(
        self, context: AccessContext, total: ReconcileOutcome
    ) -> tuple[ReconcileOutcome, bool]:
        """The workspace's waiting cases, until the batch is spent or a start
        fails (True: the tenant's turn is over)."""
        plan = await self.plans.plan_of(context.tenant_id)
        if plan is None:
            # A tenant without a plan may start no run (the access context
            # fails closed the same way); nothing to try.
            return total, False
        case_filter = ProductCaseListFilter(state=ProductDevState.PENDING_BOD_REVIEW)
        cursor: str | None = None
        while True:
            page = await self.cases.list_page(
                context,
                page_request(
                    limit=MAX_PAGE_SIZE,
                    cursor=cursor,
                    query=case_filter.page_query(context.tenant_id, context.workspace_id),
                ),
                case_filter,
            )
            for case in page.items:
                if total.attempted >= self.batch:
                    return total, False
                if await self.reviews.pending(context, case) is not None:
                    continue
                requester = await self._requester(context, case, plan)
                if requester is None:
                    continue
                outcome = ReviewRaise.NOT_RAISED
                try:
                    outcome = await self.reviews.ensure(context, case, requester)
                except Exception:
                    logger.exception(
                        "BGĐ review not raised; the next tick retries",
                        extra={"case_id": str(case.id)},
                    )
                failed = outcome is ReviewRaise.NOT_RAISED
                total = ReconcileOutcome(
                    attempted=total.attempted + 1,
                    raised=total.raised + (outcome is ReviewRaise.RAISED),
                    failed=total.failed + failed,
                )
                if failed:
                    return total, True
            if page.next_cursor is None:
                return total, False
            cursor = page.next_cursor

    async def _requester(
        self, context: AccessContext, case: ProductDevelopmentCase, plan: str
    ) -> ReviewRequester | None:
        """Whoever took the step that left the case waiting for BGĐ."""
        history = await self.cases.list_transitions(context, case.id)
        entered = [t for t in history if t.to_state is ProductDevState.PENDING_BOD_REVIEW]
        if not entered:
            return None
        return ReviewRequester(
            principal_id=entered[-1].actor_id,
            roles=frozenset(),
            scopes=frozenset(),
            plan_id=plan,
            channel="worker",
        )


__all__ = [
    "EnsureBodReview",
    "ReconcileBodReviews",
    "ReconcileOutcome",
    "bod_review_thread_id",
]
