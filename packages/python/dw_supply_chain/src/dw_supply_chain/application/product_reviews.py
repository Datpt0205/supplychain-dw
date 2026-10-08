"""Raising the approval a product case waits on: BGĐ's review of a passed
sample (step 6, stage-1 ticket 02) once per sample round, and the sign-off of a
coded product (step 9, ticket 04) once per sign-off round (ADR 0016, ADR 0020).

`EnsureProductApproval.ensure` is the one way either is raised. What differs
between the two is a row of `_WAITS`, keyed by the state the case waits in:
the approval type, the graph's worker, the round that names the thread, the
run's input and the words of its notification. Two callers: the step command,
right after a step leaves a case waiting (its own transaction is already
committed), and the worker's reconcile lane (`ReconcileProductApprovals`), for
a case still waiting with no approval: its start was refused or failed, or it
reached the state before the approval existed.

- **Idempotent on the platform's own seam, not on a check.** The run's thread
  is derived from (case, kind, round), and `uq_worker_runs_active_thread` lets
  one unfinished run hold a thread, so a second ensure of the same round
  collides (`ConflictError` naming the thread) and starts nothing. Each
  attempt has a fresh run id. A thread whose last run failed is free again,
  and invoking it with new input starts a new pass from START. Measured on
  2026-10-06 against LangGraph 1.2.11 on the Postgres saver for both failure
  shapes: paused at the interrupt with no approval written, and `apply`
  failed after BGĐ decided (the old decision is not replayed; a new review is
  raised and applies once).
- **"Raised" is read back, not assumed.** `start` returns normally when the
  approval row could not be written (the runner ends that run failed), so
  ensure asks the approval inbox for the case's pending approval after start.
- **Who decides is stamped now.** `required_scope` (for the sign-off, the
  whole ordered list of steps) is resolved from the tenant's
  `supply_chain_product_approvals` here, at the moment the approval is raised,
  and travels in the run's input onto each approval row.
- **Who is told.** The members of the case's workspace who hold both what
  deciding needs (`approvals.decide` and the scope stamped on THAT approval),
  less the requester, who may not decide it; once per approval (the inbox
  delivers a `source_key` once per person). `ensure` tells them about the
  approval it raised. A sign-off's later steps are raised inside the run, on
  the decision of the step before, so the lane tells their signers on its
  next tick (`notify`, for every pending approval it passes). Never from a
  graph node, which re-executes on resume. A failed delivery is logged and
  does not un-raise anything: the approval is in `/approvals` either way.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from dw_agent_runtime.contracts import RunContext
from dw_kernel.errors import ConflictError
from dw_kernel.pagination import MAX_PAGE_SIZE, PageQuery, page_request
from dw_kernel.ports import IdGenerator
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import PolicyOverridePort
from dw_platform.domain.approval import APPROVALS_DECIDE, approval_link
from dw_supply_chain.application.follow_up_sweep import sweep_context
from dw_supply_chain.application.handlers import resolve_product_approvals
from dw_supply_chain.application.ports import (
    PendingApprovalRecord,
    ProductCaseListFilter,
    ProductCaseRepositoryPort,
    RaisedApprovalsPort,
    ReviewNotifierPort,
    ReviewRaise,
    ReviewRequester,
    ReviewRunStarterPort,
    ScopeHoldersPort,
    TenantPlanPort,
    WorkspacesAwaitingApprovalPort,
)
from dw_supply_chain.domain.product_development_case import (
    AWAITING_APPROVAL_STATES,
    ProductDevelopmentCase,
    ProductDevState,
)
from dw_supply_chain.product_approvals import SupplyChainProductApprovals
from dw_supply_chain.workflows import advance_product_case_graph as review_graph
from dw_supply_chain.workflows import product_signoff_graph as signoff_graph

logger = logging.getLogger(__name__)

# How many approvals the reconcile lane tries to raise per tick.
RECONCILE_BATCH = 20

_THREAD_NAMESPACE = uuid.UUID("0b0d7e7e-5d1e-4c6a-9b6e-7c0d5e6f0a06")


def _thread_id(case_id: uuid.UUID, kind: str, round_no: int) -> uuid.UUID:
    return uuid.uuid5(_THREAD_NAMESPACE, f"{case_id}:{kind}:{round_no}")


def bod_review_thread_id(case_id: uuid.UUID, sample_round: int) -> uuid.UUID:
    """The thread BGĐ's review of one sample round runs on: the same for every
    attempt, so two attempts meet at `uq_worker_runs_active_thread`."""
    return _thread_id(case_id, "bod_review", sample_round)


def signoff_thread_id(case_id: uuid.UUID, signoff_round: int) -> uuid.UUID:
    """The thread one sign-off round runs on, as `bod_review_thread_id`."""
    return _thread_id(case_id, "signoff", signoff_round)


def _review_input(
    case: ProductDevelopmentCase, policy: SupplyChainProductApprovals
) -> dict[str, object]:
    return {
        review_graph.BOD_REVIEW_CASE_KEY: str(case.id),
        "proposal_code": case.proposal_code,
        "product_name": case.product_name,
        "sample_round": case.sample_round,
        "required_scope": policy.bod_review.required_scope,
    }


def _signoff_input(
    case: ProductDevelopmentCase, policy: SupplyChainProductApprovals
) -> dict[str, object]:
    if case.item_code is None:
        # `submit_for_signoff` refuses a case without one; a row that says
        # otherwise is not signed on a guess.
        raise ConflictError(
            "a case waiting for sign-off has no item code", details={"case_id": str(case.id)}
        )
    return {
        signoff_graph.SIGNOFF_CASE_KEY: str(case.id),
        "proposal_code": case.proposal_code,
        "product_name": case.product_name,
        "item_code": case.item_code.code,
        "signoff_round": case.signoff_round,
        "steps": [step.model_dump() for step in policy.signoff],
        "step_index": 0,
    }


def _review_notice(
    case: ProductDevelopmentCase, approval: PendingApprovalRecord
) -> tuple[str, str]:
    return (
        f"Chờ BGĐ duyệt mẫu: {case.proposal_code}",
        f"{case.product_name}, vòng mẫu {case.sample_round}: mẫu đã đạt,"
        " chờ người có quyền BGĐ duyệt.",
    )


def _signoff_notice(
    case: ProductDevelopmentCase, approval: PendingApprovalRecord
) -> tuple[str, str]:
    payload = approval.payload
    label = payload.get("step_label", "")
    steps = payload.get("steps")
    total = len(steps) if isinstance(steps, list) else "?"
    return (
        f"Chờ ký hồ sơ ({label}): {case.proposal_code}",
        f"{case.product_name}, mã hàng {payload.get('item_code', '')}: bước ký"
        f" {payload.get('step_no', '?')}/{total}, chờ người có quyền {label} ký.",
    )


@dataclass(frozen=True, slots=True)
class _Wait:
    """What raising the approval a case waits on in one state needs."""

    kind: str
    approval_type: str
    worker_id: str
    worker_version: str
    round_of: Callable[[ProductDevelopmentCase], int]
    input_of: Callable[[ProductDevelopmentCase, SupplyChainProductApprovals], dict[str, object]]
    notice_of: Callable[[ProductDevelopmentCase, PendingApprovalRecord], tuple[str, str]]


_WAITS: Mapping[ProductDevState, _Wait] = {
    ProductDevState.PENDING_BOD_REVIEW: _Wait(
        kind="bod_review",
        approval_type=review_graph.BOD_REVIEW_APPROVAL_TYPE,
        worker_id=review_graph.WORKER_ID,
        worker_version=review_graph.WORKER_VERSION,
        round_of=lambda case: case.sample_round,
        input_of=_review_input,
        notice_of=_review_notice,
    ),
    ProductDevState.PENDING_SIGNOFF: _Wait(
        kind="signoff",
        approval_type=signoff_graph.SIGNOFF_APPROVAL_TYPE,
        worker_id=signoff_graph.WORKER_ID,
        worker_version=signoff_graph.WORKER_VERSION,
        round_of=lambda case: case.signoff_round,
        input_of=_signoff_input,
        notice_of=_signoff_notice,
    ),
}
assert set(_WAITS) == AWAITING_APPROVAL_STATES  # every waiting state can be raised

# The approval type a case waits on in each waiting state: what the case page
# looks its pending approval up by.
AWAITED_APPROVAL_TYPE: Mapping[ProductDevState, str] = {
    state: wait.approval_type for state, wait in _WAITS.items()
}


@dataclass(frozen=True)
class EnsureProductApproval:
    """Implements `ProductApprovalPort`."""

    runner: ReviewRunStarterPort
    approvals: RaisedApprovalsPort
    holders: ScopeHoldersPort
    notifier: ReviewNotifierPort
    policy_override_repo: PolicyOverridePort
    platform_default_approvals: SupplyChainProductApprovals
    ids: IdGenerator

    async def pending(
        self, context: AccessContext, case: ProductDevelopmentCase
    ) -> PendingApprovalRecord | None:
        """The case's undecided approval in `context`'s workspace, if it is
        waiting on one."""
        wait = _WAITS.get(case.state)
        if wait is None:
            return None
        return await self.approvals.raised_by_payload(
            context,
            approval_type=wait.approval_type,
            key=review_graph.BOD_REVIEW_CASE_KEY,
            value=str(case.id),
        )

    async def ensure(
        self, context: AccessContext, case: ProductDevelopmentCase, requester: ReviewRequester
    ) -> ReviewRaise:
        """Raise the approval unless it exists. A refused start (plan quota,
        spend ceiling) or any other failure propagates: the caller decides
        whether that undoes anything (the step command: no)."""
        wait = _WAITS.get(case.state)
        if wait is None:
            return ReviewRaise.NOT_WAITING
        if await self.pending(context, case) is not None:
            return ReviewRaise.ALREADY_PENDING
        policy = await resolve_product_approvals(
            context, self.policy_override_repo, self.platform_default_approvals
        )
        thread_id = _thread_id(case.id.value, wait.kind, wait.round_of(case))
        run_id = self.ids.new_uuid()
        try:
            await self.runner.start(
                run_context=RunContext(
                    run_id=run_id,
                    thread_id=thread_id,
                    tenant_id=context.tenant_id,
                    workspace_id=case.workspace_id.value,
                    actor_id=requester.principal_id,
                    worker_id=wait.worker_id,
                    worker_version=wait.worker_version,
                    channel=requester.channel,
                    plan_id=requester.plan_id,
                    roles=requester.roles,
                    scopes=requester.scopes,
                    trace_id=str(run_id),
                    subject_ref=f"product_dev_case:{case.id}",
                ),
                input_payload=wait.input_of(case, policy),
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
        await self.notify(context, case, raised, requester.principal_id)
        return ReviewRaise.RAISED

    async def notify(
        self,
        context: AccessContext,
        case: ProductDevelopmentCase,
        approval: PendingApprovalRecord,
        requester: uuid.UUID,
    ) -> None:
        """Tell who may decide `approval` (its stamp and `approvals.decide`),
        less `requester`. Once per approval and person, however often asked."""
        wait = _WAITS.get(case.state)
        if wait is None or approval.required_scope is None:
            return
        workspace = case.workspace_id.value
        try:
            stamped = set(
                await self.holders.holding(
                    context.tenant_id, workspace, frozenset({approval.required_scope})
                )
            )
            deciders = set(
                await self.holders.holding(
                    context.tenant_id, workspace, frozenset({APPROVALS_DECIDE})
                )
            )
            title, body = wait.notice_of(case, approval)
            await self.notifier.deliver(
                context,
                recipients=sorted(stamped & deciders - {requester}),
                source_key=f"supply_chain.{wait.kind}:{approval.id}",
                title=title,
                body=body,
                # The approval's own page: it opens after sign-in and issues
                # the code a decision on Zalo needs (zalo-channel ticket 05).
                link=approval_link(approval.id, workspace),
            )
        except Exception:
            logger.exception(
                "approval raised but its notification failed",
                extra={"case_id": str(case.id), "approval_id": str(approval.id)},
            )


@dataclass(frozen=True, slots=True)
class ReconcileOutcome:
    attempted: int = 0
    raised: int = 0
    failed: int = 0


@dataclass(frozen=True)
class ReconcileProductApprovals:
    """The worker lane `supply_chain_product_review_reconcile`: raises the
    approval every case waiting in `pending_bod_review` or `pending_signoff`
    lacks, and tells the deciders of every one already pending (the sign-off's
    later steps are raised inside their run, where nobody is told). The
    backfill for cases that reached a waiting state before its approval
    existed, and the retry for a start that was refused or failed.

    A system process, like the follow-up sweep: it reads under a context with
    no roles and no scopes, one (tenant, workspace) at a time, and crosses
    tenants only to learn which to visit. The run it starts is the requester's
    approval: requested by whoever took the step that left the case waiting
    (read from the case's history, a stamp, never a fresh guess), so they still
    cannot decide it, and counted against the tenant's plan as any run is. It
    carries no scopes: the graphs authorize nothing with them.

    At most `batch` starts per tick, and a tenant's first failed start ends
    its turn for the tick: refused (plan quota, spend ceiling, both
    tenant-wide) or run without raising the approval. So a tenant whose
    approvals cannot be raised costs one attempt (and at most one failed run)
    per tick, and every tenant sorted after it is still visited; the next tick
    retries it. A workspace whose reads fail is logged and the next is
    visited."""

    workspaces: WorkspacesAwaitingApprovalPort
    cases: ProductCaseRepositoryPort
    plans: TenantPlanPort
    reviews: EnsureProductApproval
    batch: int = RECONCILE_BATCH

    async def run(self) -> ReconcileOutcome:
        total = ReconcileOutcome()
        ended: set[uuid.UUID] = set()
        for tenant_id, workspace_id in await self.workspaces.awaiting_approval():
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
                    "product approval reconcile failed for a workspace",
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
        for state in sorted(AWAITING_APPROVAL_STATES):
            total, ends_turn = await self._waiting_in(context, state, plan, total)
            if ends_turn or total.attempted >= self.batch:
                return total, ends_turn
        return total, False

    async def _waiting_in(
        self, context: AccessContext, state: ProductDevState, plan: str, total: ReconcileOutcome
    ) -> tuple[ReconcileOutcome, bool]:
        case_filter = ProductCaseListFilter(state=state)
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
                requester = await self._requester(context, case, plan)
                if requester is None:
                    continue
                pending = await self.reviews.pending(context, case)
                if pending is not None:
                    await self.reviews.notify(context, case, pending, requester.principal_id)
                    continue
                outcome = ReviewRaise.NOT_RAISED
                try:
                    outcome = await self.reviews.ensure(context, case, requester)
                except Exception:
                    logger.exception(
                        "product approval not raised; the next tick retries",
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
        """Whoever took the step that left the case waiting where it is: the
        newest transition into its state, read newest first a page at a
        time (the first page holds it unless the case moved since)."""
        query = PageQuery(key="supply_chain.product_case_transitions", filters={"case": case.id})
        cursor: str | None = None
        while True:
            page = await self.cases.list_transitions(
                context, case.id, page_request(limit=MAX_PAGE_SIZE, cursor=cursor, query=query)
            )
            entered = next((t for t in page.items if t.to_state is case.state), None)
            if entered is not None or page.next_cursor is None:
                break
            cursor = page.next_cursor
        if entered is None:
            return None
        return ReviewRequester(
            principal_id=entered.actor_id,
            roles=frozenset(),
            scopes=frozenset(),
            plan_id=plan,
            channel="worker",
        )


__all__ = [
    "AWAITED_APPROVAL_TYPE",
    "EnsureProductApproval",
    "ReconcileOutcome",
    "ReconcileProductApprovals",
    "bod_review_thread_id",
    "signoff_thread_id",
]
