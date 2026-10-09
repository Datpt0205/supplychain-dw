"""Unit: raising BGĐ's review (step 6) once per sample round and the
sign-off (step 9) once per sign-off round, and the reconcile lane that raises
the ones a start missed and tells the signers of later steps (stage-1 tickets
02 and 04).

The runner is faked the way the real one behaves at its edges: a second start
on an unfinished thread is a `ConflictError` naming the thread
(`uq_worker_runs_active_thread`), and a start whose approval row could not be
written returns normally with no approval. `test_product_bod_review.py`
(integration) runs the real runner, approvals and Postgres.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from dw_agent_runtime.contracts import RunContext
from dw_kernel.errors import ConflictError, QuotaExceededError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.pagination import Page, PageRequest
from dw_kernel.ports import Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.application.follow_up_sweep import sweep_context
from dw_supply_chain.application.ports import (
    PendingApprovalRecord,
    ProductCaseListFilter,
    ReviewRaise,
    ReviewRequester,
)
from dw_supply_chain.application.product_reviews import (
    EnsureProductApproval,
    ReconcileProductApprovals,
    bod_review_thread_id,
    signoff_thread_id,
)
from dw_supply_chain.domain.product_development_case import (
    AWAITING_APPROVAL_STATES,
    ItemCode,
    ProductAction,
    ProductCaseTransition,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
    SampleRound,
)
from dw_supply_chain.product_approvals import (
    PRODUCT_APPROVALS_POLICY_ID,
    SupplyChainProductApprovals,
)
from dw_supply_chain.testing.pages import history_page
from dw_supply_chain.workflows import product_signoff_graph
from dw_supply_chain.workflows.advance_product_case_graph import (
    BOD_REVIEW_APPROVAL_TYPE,
    WORKER_ID,
    WORKER_VERSION,
)

pytestmark = pytest.mark.unit

TENANT, WORKSPACE = uuid.uuid4(), uuid.uuid4()
TESTER, BOD, BOD_WITHOUT_DECIDE, APPROVER_ONLY = (uuid.uuid4() for _ in range(4))
ACCOUNTANT = uuid.uuid4()
NOW = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
BOD_SCOPE = "supply_chain.approve.bod"
ACCOUNTING_SCOPE = "supply_chain.approve.accounting"
SIGNOFF_STEPS = [
    {"step": "bod", "label": "BGĐ", "required_scope": BOD_SCOPE},
    {"step": "accounting", "label": "Kế toán", "required_scope": ACCOUNTING_SCOPE},
]

PLATFORM_APPROVALS = SupplyChainProductApprovals.model_validate(
    {
        "schema_version": "1.0",
        "policy_id": PRODUCT_APPROVALS_POLICY_ID,
        "policy_version": "1.1.0",
        "bod_review": {"required_scope": BOD_SCOPE},
        "signoff": SIGNOFF_STEPS,
    }
)


@dataclass(frozen=True)
class Approval:
    id: uuid.UUID
    approval_type: str
    payload: Mapping[str, object]
    created_at: datetime | None
    required_scope: str | None


@dataclass
class FakeInbox:
    """The approval inbox: the pending approval per (workspace, case id), of
    the type it was raised as."""

    pending: dict[tuple[uuid.UUID, str], Approval] = field(default_factory=dict)

    async def raised_by_payload(
        self, context: AccessContext, *, approval_type: str, key: str, value: str
    ) -> PendingApprovalRecord | None:
        assert key == "product_dev_case_id"
        found = self.pending.get((context.workspace_id, value))
        return found if found is not None and found.approval_type == approval_type else None


@dataclass
class FakeRunner:
    """One unfinished run per thread; the run raises the approval unless
    `approval_insert_fails`, as `_handle_outcome` swallows that failure."""

    inbox: FakeInbox
    approval_insert_fails: bool = False
    refuse: Exception | None = None
    # Per tenant, as the plan quota and a tenant's own policy are.
    out_of_runs: set[uuid.UUID] = field(default_factory=set)
    insert_fails_for: set[uuid.UUID] = field(default_factory=set)
    started: list[tuple[RunContext, dict[str, Any]]] = field(default_factory=list)
    active_threads: set[uuid.UUID] = field(default_factory=set)

    async def start(self, *, run_context: RunContext, input_payload: dict[str, Any]) -> uuid.UUID:
        if self.refuse is not None:
            raise self.refuse
        if run_context.tenant_id in self.out_of_runs:
            raise QuotaExceededError("hết lượt chạy trong ngày")
        assert run_context.thread_id is not None
        if run_context.thread_id in self.active_threads:
            raise ConflictError(
                "this conversation already has a turn in flight",
                details={"thread_id": str(run_context.thread_id)},
            )
        self.started.append((run_context, input_payload))
        if self.approval_insert_fails or run_context.tenant_id in self.insert_fails_for:
            return run_context.run_id  # the run ended failed; the thread is free
        self.active_threads.add(run_context.thread_id)
        self.inbox.pending[(run_context.workspace_id, input_payload["product_dev_case_id"])] = (
            _first_pause(run_context.worker_id, input_payload)
        )
        return run_context.run_id


def _first_pause(worker_id: str, run_input: dict[str, Any]) -> Approval:
    """The approval a graph's first pause writes, as the real graphs do."""
    if worker_id == product_signoff_graph.WORKER_ID:
        first = run_input["steps"][0]
        return Approval(
            id=uuid.uuid4(),
            approval_type=product_signoff_graph.SIGNOFF_APPROVAL_TYPE,
            payload={
                **run_input,
                "step": first["step"],
                "step_label": first["label"],
                "step_no": 1,
            },
            created_at=NOW,
            required_scope=first["required_scope"],
        )
    return Approval(
        id=uuid.uuid4(),
        approval_type=BOD_REVIEW_APPROVAL_TYPE,
        payload=run_input,
        created_at=NOW,
        required_scope=run_input["required_scope"],
    )


@dataclass
class FakeHolders:
    by_scope: dict[str, list[uuid.UUID]]
    asked: list[tuple[uuid.UUID, frozenset[str]]] = field(default_factory=list)

    async def holding(
        self, tenant_id: uuid.UUID, workspace_id: uuid.UUID, scopes: frozenset[str]
    ) -> list[uuid.UUID]:
        self.asked.append((workspace_id, scopes))
        return sorted({user for scope in scopes for user in self.by_scope.get(scope, [])})


@dataclass
class FakeNotifier:
    fail: bool = False
    delivered: list[dict[str, Any]] = field(default_factory=list)

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
        if self.fail:
            raise RuntimeError("inbox down")
        self.delivered.append(
            {"recipients": list(recipients), "source_key": source_key, "link": link}
        )


@dataclass
class FakePolicies:
    stored: dict[str, Mapping[str, object]] = field(default_factory=dict)

    async def get(self, context: AccessContext, policy_id: str) -> dict[str, object] | None:
        found = self.stored.get(policy_id)
        return dict(found) if found is not None else None

    async def put(
        self,
        context: AccessContext,
        policy_id: str,
        content: Mapping[str, object],
        *,
        audit: AuditEvent,
    ) -> None:
        raise NotImplementedError("no route writes this policy")


def _context(principal: uuid.UUID = TESTER, workspace: uuid.UUID = WORKSPACE) -> AccessContext:
    return AccessContext(
        tenant_id=TENANT,
        workspace_id=workspace,
        principal_id=principal,
        roles=frozenset({"member", "sc_rnd"}),
        scopes=frozenset({"supply_chain.duty.rnd"}),
        plan_id="professional",
    )


def _waiting(**overrides: object) -> ProductDevelopmentCase:
    fields: dict[str, object] = {
        "id": ProductDevelopmentCaseId(uuid.uuid4()),
        "tenant_id": TenantId(TENANT),
        "workspace_id": WorkspaceId(WORKSPACE),
        "proposal_code": "DX-2026-001",
        "product_name": "Nồi inox 3 đáy 24cm",
        "category": "Nồi",
        "pic_user_id": TESTER,
        "created_by": TESTER,
        "state": ProductDevState.PENDING_BOD_REVIEW,
        "sample_round": 2,
        "created_at": NOW,
    }
    fields.update(overrides)
    return ProductDevelopmentCase(**fields)  # type: ignore[arg-type]


@dataclass
class Stack:
    inbox: FakeInbox = field(default_factory=FakeInbox)
    policies: FakePolicies = field(default_factory=FakePolicies)
    notifier: FakeNotifier = field(default_factory=FakeNotifier)
    holders: FakeHolders = field(
        default_factory=lambda: FakeHolders(
            {
                BOD_SCOPE: [BOD, BOD_WITHOUT_DECIDE, TESTER],
                ACCOUNTING_SCOPE: [ACCOUNTANT, TESTER],
                "approvals.decide": [BOD, APPROVER_ONLY, TESTER, ACCOUNTANT],
            }
        )
    )
    runner: FakeRunner | None = None

    def __post_init__(self) -> None:
        if self.runner is None:
            self.runner = FakeRunner(self.inbox)

    @property
    def run(self) -> FakeRunner:
        assert self.runner is not None
        return self.runner

    def reviews(self) -> EnsureProductApproval:
        return EnsureProductApproval(
            runner=self.run,
            approvals=self.inbox,
            holders=self.holders,
            notifier=self.notifier,
            policy_override_repo=self.policies,
            platform_default_approvals=PLATFORM_APPROVALS,
            ids=Uuid4Generator(),
        )


def _requester(context: AccessContext) -> ReviewRequester:
    return ReviewRequester.from_context(context)


async def test_raising_starts_one_run_stamped_with_the_tenants_scope_and_the_tester() -> None:
    stack = Stack()
    case = _waiting()
    context = _context()

    outcome = await stack.reviews().ensure(context, case, _requester(context))

    assert outcome is ReviewRaise.RAISED
    ((run, payload),) = stack.run.started
    assert run.thread_id == bod_review_thread_id(case.id.value, 2)
    assert (run.worker_id, run.worker_version) == (WORKER_ID, WORKER_VERSION)
    assert (run.actor_id, run.plan_id, run.scopes) == (TESTER, "professional", context.scopes)
    assert (run.tenant_id, run.workspace_id) == (TENANT, WORKSPACE)
    assert payload == {
        "product_dev_case_id": str(case.id),
        "proposal_code": "DX-2026-001",
        "product_name": "Nồi inox 3 đáy 24cm",
        "sample_round": 2,
        "required_scope": BOD_SCOPE,
        # No drafter wired here: the review is raised without a tờ trình.
        "bod_submission": None,
    }


async def test_a_tenant_override_decides_the_scope_stamped_on_the_next_review() -> None:
    stack = Stack()
    stack.policies.stored[PRODUCT_APPROVALS_POLICY_ID] = {
        **PLATFORM_APPROVALS.model_dump(mode="json"),
        "bod_review": {"required_scope": "supply_chain.approve.ceo"},
    }
    context = _context()

    await stack.reviews().ensure(context, _waiting(), _requester(context))

    assert stack.run.started[0][1]["required_scope"] == "supply_chain.approve.ceo"


async def test_who_may_decide_is_told_and_nobody_else() -> None:
    """Holders of BOTH what deciding needs, in the case's workspace, less the
    tester who asked: the strict prefix refuses them anyway."""
    stack = Stack()
    context = _context()

    await stack.reviews().ensure(context, _waiting(), _requester(context))

    (delivery,) = stack.notifier.delivered
    assert delivery["recipients"] == [BOD]
    (approval,) = stack.inbox.pending.values()
    # The review's own page, which opens after sign-in (ticket Z5).
    assert delivery["link"] == f"/approvals/{approval.id}?workspace={WORKSPACE}"
    assert delivery["source_key"] == f"supply_chain.bod_review:{approval.id}"
    assert {workspace for workspace, _ in stack.holders.asked} == {WORKSPACE}


async def test_a_second_ensure_of_the_same_round_raises_nothing_and_tells_nobody() -> None:
    stack = Stack()
    case = _waiting()
    context = _context()
    reviews = stack.reviews()
    await reviews.ensure(context, case, _requester(context))

    again = await reviews.ensure(context, case, _requester(context))

    assert again is ReviewRaise.ALREADY_PENDING
    assert len(stack.run.started) == 1
    assert len(stack.notifier.delivered) == 1


async def test_a_pending_review_whose_thread_is_free_is_not_raised_twice() -> None:
    """The pre-check's own case: the review is still waiting in `/approvals`
    but its thread holds no unfinished run (that run failed after the approval
    row was written), so the thread seam would let a second run start and a
    second review for the same case would be raised."""
    stack = Stack()
    case = _waiting()
    context = _context()
    reviews = stack.reviews()
    await reviews.ensure(context, case, _requester(context))
    stack.run.active_threads.clear()  # the run behind the review ended

    again = await reviews.ensure(context, case, _requester(context))

    assert again is ReviewRaise.ALREADY_PENDING
    assert len(stack.run.started) == 1
    assert len(stack.notifier.delivered) == 1


async def test_two_ensures_racing_meet_at_the_thread_and_raise_one_review() -> None:
    """The pre-check cannot see a review being raised in the same instant;
    the thread's one-unfinished-run seam does."""
    stack = Stack()
    case = _waiting()
    context = _context()
    thread = bod_review_thread_id(case.id.value, case.sample_round)
    stack.run.active_threads.add(thread)  # the other ensure's run, still running

    outcome = await stack.reviews().ensure(context, case, _requester(context))

    assert outcome is ReviewRaise.NOT_RAISED  # not raised by THIS call, not yet visible
    assert stack.run.started == []
    assert stack.notifier.delivered == []


async def test_a_conflict_that_is_not_the_threads_claim_is_not_swallowed() -> None:
    stack = Stack()
    stack.run.refuse = ConflictError("something else", details={"thread_id": str(uuid.uuid4())})
    context = _context()
    with pytest.raises(ConflictError, match="something else"):
        await stack.reviews().ensure(context, _waiting(), _requester(context))


async def test_a_start_whose_approval_was_not_recorded_is_not_raised() -> None:
    """`start` returned normally (the runner ended the run failed); ensure
    reads the inbox rather than trusting that, and tells nobody."""
    stack = Stack()
    stack.run.approval_insert_fails = True
    context = _context()

    outcome = await stack.reviews().ensure(context, _waiting(), _requester(context))

    assert outcome is ReviewRaise.NOT_RAISED
    assert stack.notifier.delivered == []


async def test_a_refused_start_propagates_to_the_caller() -> None:
    stack = Stack()
    stack.run.refuse = QuotaExceededError("hết lượt chạy")
    context = _context()
    with pytest.raises(QuotaExceededError):
        await stack.reviews().ensure(context, _waiting(), _requester(context))


async def test_a_failed_notification_does_not_unraise_the_review() -> None:
    stack = Stack(notifier=FakeNotifier(fail=True))
    context = _context()

    outcome = await stack.reviews().ensure(context, _waiting(), _requester(context))

    assert outcome is ReviewRaise.RAISED


@pytest.mark.parametrize(
    "state",
    [s for s in ProductDevState if s not in AWAITING_APPROVAL_STATES],
)
async def test_a_case_not_waiting_on_an_approval_raises_nothing(state: ProductDevState) -> None:
    stack = Stack()
    context = _context()
    outcome = await stack.reviews().ensure(context, _waiting(state=state), _requester(context))
    assert outcome is ReviewRaise.NOT_WAITING
    assert stack.run.started == []


def test_each_round_has_its_own_thread_and_each_case_its_own() -> None:
    case_id = uuid.uuid4()
    assert bod_review_thread_id(case_id, 1) == bod_review_thread_id(case_id, 1)
    assert bod_review_thread_id(case_id, 1) != bod_review_thread_id(case_id, 2)
    assert bod_review_thread_id(case_id, 1) != bod_review_thread_id(uuid.uuid4(), 1)


# --- the reconcile lane --------------------------------------------------------------


@dataclass
class FakeCaseList:
    """The lane's reads: a workspace's cases (RLS-narrowed) and their history."""

    cases: list[ProductDevelopmentCase]
    history: dict[uuid.UUID, list[ProductCaseTransition]] = field(default_factory=dict)
    broken_workspaces: set[uuid.UUID] = field(default_factory=set)

    async def list_page(
        self, context: AccessContext, request: PageRequest, case_filter: ProductCaseListFilter
    ) -> Page[ProductDevelopmentCase]:
        if context.workspace_id in self.broken_workspaces:
            raise RuntimeError("database hiccup")
        assert context.roles == frozenset() and context.scopes == frozenset()
        items = [
            c
            for c in self.cases
            if (c.tenant_id.value, c.workspace_id.value)
            == (context.tenant_id, context.workspace_id)
            and (case_filter.state is None or c.state is case_filter.state)
        ]
        return Page(items=tuple(items), next_cursor=None)

    async def list_transitions(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId, request: PageRequest
    ) -> Page[ProductCaseTransition]:
        return history_page(self.history.get(case_id.value, []), request)

    async def add(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError("the lane writes no case")

    async def get(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError("the lane reads cases a page at a time")

    async def save(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError("the lane writes no case")

    async def list_rounds(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> list[SampleRound]:
        raise NotImplementedError("not read by the lane")

    async def place_order(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError("the lane orders nothing")

    async def po_case_of(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError("not read by the lane")

    async def state_entered_at(
        self, context: AccessContext, case_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, datetime]:
        raise NotImplementedError("not read by the lane")


@dataclass
class FakeWorkspaces:
    pairs: list[tuple[uuid.UUID, uuid.UUID]]

    async def awaiting_approval(self) -> list[tuple[uuid.UUID, uuid.UUID]]:
        return self.pairs


@dataclass
class FakePlans:
    plans: dict[uuid.UUID, str]

    async def plan_of(self, tenant_id: uuid.UUID) -> str | None:
        return self.plans.get(tenant_id)


def _entered(case: ProductDevelopmentCase, actor: uuid.UUID) -> list[ProductCaseTransition]:
    """The step that left the case where it waits."""
    signoff = case.state is ProductDevState.PENDING_SIGNOFF
    return [
        ProductCaseTransition(
            action=ProductAction.SUBMIT_FOR_SIGNOFF if signoff else ProductAction.PASS_SAMPLE,
            from_state=ProductDevState.ITEM_CODING if signoff else ProductDevState.SAMPLE_TESTING,
            to_state=case.state,
            reason=None,
            actor_id=actor,
            occurred_at=NOW,
        )
    ]


def _lane(
    stack: Stack,
    cases: FakeCaseList,
    pairs: list[tuple[uuid.UUID, uuid.UUID]],
    *,
    batch: int = 20,
    plans: dict[uuid.UUID, str] | None = None,
) -> ReconcileProductApprovals:
    return ReconcileProductApprovals(
        workspaces=FakeWorkspaces(pairs),
        cases=cases,
        plans=FakePlans(plans if plans is not None else {TENANT: "basic"}),
        reviews=stack.reviews(),
        batch=batch,
    )


async def test_the_lane_raises_a_review_a_waiting_case_lacks_as_its_tester() -> None:
    """The backfill for a case that reached `pending_bod_review` before S2,
    and the retry for a refused start: the requester is whoever took the
    step into waiting (a stamp from history), the plan the tenant's."""
    stack = Stack()
    case = _waiting()
    cases = FakeCaseList([case], history={case.id.value: _entered(case, TESTER)})

    outcome = await _lane(stack, cases, [(TENANT, WORKSPACE)]).run()

    assert (outcome.attempted, outcome.raised, outcome.failed) == (1, 1, 0)
    ((run, _),) = stack.run.started
    assert (run.actor_id, run.plan_id, run.channel) == (TESTER, "basic", "worker")
    assert run.scopes == frozenset() and run.roles == frozenset()


async def test_the_lane_leaves_a_case_whose_review_is_pending_alone() -> None:
    stack = Stack()
    case = _waiting()
    context = sweep_context(TENANT, WORKSPACE)
    await stack.reviews().ensure(context, case, ReviewRequester.from_context(_context()))
    stack.run.started.clear()
    cases = FakeCaseList([case], history={case.id.value: _entered(case, TESTER)})

    outcome = await _lane(stack, cases, [(TENANT, WORKSPACE)]).run()

    assert outcome.attempted == 0
    assert stack.run.started == []
    # It is told again under the same key, which the inbox delivers once.
    assert {d["source_key"] for d in stack.notifier.delivered} == {
        f"supply_chain.bod_review:{approval.id}" for approval in stack.inbox.pending.values()
    }


async def test_the_lane_starts_at_most_a_batch_per_tick() -> None:
    stack = Stack()
    waiting = [_waiting(proposal_code=f"DX-{n}") for n in range(5)]
    cases = FakeCaseList(waiting, history={c.id.value: _entered(c, TESTER) for c in waiting})

    outcome = await _lane(stack, cases, [(TENANT, WORKSPACE)], batch=2).run()

    assert outcome.attempted == 2
    assert len(stack.run.started) == 2


async def test_one_failing_workspace_does_not_stop_the_others() -> None:
    stack = Stack()
    other_workspace = uuid.uuid4()
    case = _waiting()
    cases = FakeCaseList(
        [case],
        history={case.id.value: _entered(case, TESTER)},
        broken_workspaces={other_workspace},
    )

    outcome = await _lane(stack, cases, [(TENANT, other_workspace), (TENANT, WORKSPACE)]).run()

    assert (outcome.raised, outcome.failed) == (1, 1)


async def test_a_tenant_without_a_plan_gets_no_run() -> None:
    stack = Stack()
    case = _waiting()
    cases = FakeCaseList([case], history={case.id.value: _entered(case, TESTER)})

    outcome = await _lane(stack, cases, [(TENANT, WORKSPACE)], plans={}).run()

    assert outcome.attempted == 0
    assert stack.run.started == []


async def test_a_refused_start_in_the_lane_is_counted_and_retried_next_tick() -> None:
    stack = Stack()
    stack.run.refuse = QuotaExceededError("hết lượt chạy")
    case = _waiting()
    cases = FakeCaseList([case], history={case.id.value: _entered(case, TESTER)})

    outcome = await _lane(stack, cases, [(TENANT, WORKSPACE)]).run()

    assert (outcome.attempted, outcome.raised, outcome.failed) == (1, 0, 1)


OTHER_TENANT, OTHER_WORKSPACE = uuid.uuid4(), uuid.uuid4()


def _two_tenants(stuck: int) -> tuple[FakeCaseList, ProductDevelopmentCase]:
    """`stuck` cases waiting in TENANT, which sorts first, and one in another
    tenant after it."""
    first = [_waiting(proposal_code=f"DX-{n}") for n in range(stuck)]
    later = _waiting(tenant_id=TenantId(OTHER_TENANT), workspace_id=WorkspaceId(OTHER_WORKSPACE))
    cases = FakeCaseList(
        [*first, later],
        history={c.id.value: _entered(c, TESTER) for c in [*first, later]},
    )
    return cases, later


_PAIRS = [(TENANT, WORKSPACE), (OTHER_TENANT, OTHER_WORKSPACE)]
_PLANS = {TENANT: "basic", OTHER_TENANT: "basic"}


async def test_a_tenant_out_of_runs_does_not_starve_the_tenants_after_it() -> None:
    """More waiting cases than a batch, every start refused by the plan quota:
    the tenant's first refusal ends its turn, and the next tenant's review is
    raised in the same tick."""
    stack = Stack()
    stack.run.out_of_runs.add(TENANT)
    cases, later = _two_tenants(stuck=5)

    outcome = await _lane(stack, cases, _PAIRS, batch=3, plans=_PLANS).run()

    assert (outcome.attempted, outcome.raised, outcome.failed) == (2, 1, 1)
    assert [run.tenant_id for run, _ in stack.run.started] == [OTHER_TENANT]
    assert (OTHER_WORKSPACE, str(later.id)) in stack.inbox.pending


async def test_a_tenant_whose_reviews_are_never_raised_costs_one_failed_run_a_tick() -> None:
    """The run starts and the approval is not written (a stamped scope the
    approval's CHECK refuses): one failed run for that tenant per tick, not
    one per waiting case, and the tenant after it is still served."""
    stack = Stack()
    stack.run.insert_fails_for.add(TENANT)
    cases, later = _two_tenants(stuck=5)
    lane = _lane(stack, cases, _PAIRS, batch=20, plans=_PLANS)

    for _ in range(2):
        await lane.run()

    tenants = [run.tenant_id for run, _ in stack.run.started]
    assert tenants.count(TENANT) == 2
    assert tenants.count(OTHER_TENANT) == 1
    assert (OTHER_WORKSPACE, str(later.id)) in stack.inbox.pending


async def test_a_stuck_tenant_with_two_waiting_workspaces_still_costs_one_failed_run() -> None:
    """The turn ends for the TENANT, not only for the workspace that failed:
    a second waiting workspace of the same tenant gets no start in this tick,
    and the tenant after it is still served."""
    stack = Stack()
    stack.run.insert_fails_for.add(TENANT)
    second_workspace = uuid.uuid4()
    cases, later = _two_tenants(stuck=2)
    elsewhere = _waiting(proposal_code="DX-W2", workspace_id=WorkspaceId(second_workspace))
    cases.cases.append(elsewhere)
    cases.history[elsewhere.id.value] = _entered(elsewhere, TESTER)
    pairs = [(TENANT, WORKSPACE), (TENANT, second_workspace), (OTHER_TENANT, OTHER_WORKSPACE)]

    await _lane(stack, cases, pairs, batch=20, plans=_PLANS).run()

    tenants = [run.tenant_id for run, _ in stack.run.started]
    assert tenants.count(TENANT) == 1
    assert tenants.count(OTHER_TENANT) == 1
    assert (OTHER_WORKSPACE, str(later.id)) in stack.inbox.pending


# --- the step-9 sign-off (ticket 04) -----------------------------------------------------


def _submitted(**overrides: object) -> ProductDevelopmentCase:
    return _waiting(
        **{
            "state": ProductDevState.PENDING_SIGNOFF,
            "item_code": ItemCode(uuid.uuid4(), "MH-0001"),
            "signoff_round": 3,
            **overrides,
        }
    )


async def test_submitting_starts_the_signoff_run_with_the_tenants_order_stamped() -> None:
    stack = Stack()
    case = _submitted()
    context = _context()

    outcome = await stack.reviews().ensure(context, case, _requester(context))

    assert outcome is ReviewRaise.RAISED
    ((run, payload),) = stack.run.started
    assert run.thread_id == signoff_thread_id(case.id.value, 3)
    assert (run.worker_id, run.worker_version) == (
        product_signoff_graph.WORKER_ID,
        product_signoff_graph.WORKER_VERSION,
    )
    assert run.actor_id == TESTER
    assert payload == {
        "product_dev_case_id": str(case.id),
        "proposal_code": "DX-2026-001",
        "product_name": "Nồi inox 3 đáy 24cm",
        "item_code": "MH-0001",
        "signoff_round": 3,
        "steps": SIGNOFF_STEPS,
        "step_index": 0,
    }


async def test_the_first_signers_are_told_and_nobody_else() -> None:
    """The first step is BGĐ's: holders of its stamp and of the decide right,
    less the requester. Kế toán is not told until its own step is raised."""
    stack = Stack()
    context = _context()

    await stack.reviews().ensure(context, _submitted(), _requester(context))

    (delivery,) = stack.notifier.delivered
    (approval,) = stack.inbox.pending.values()
    assert delivery["recipients"] == [BOD]
    assert delivery["source_key"] == f"supply_chain.signoff:{approval.id}"
    assert delivery["link"] == f"/approvals/{approval.id}?workspace={WORKSPACE}"


async def test_a_tenant_order_reaches_the_next_submission() -> None:
    stack = Stack()
    reordered = [SIGNOFF_STEPS[1], SIGNOFF_STEPS[0]]
    stack.policies.stored[PRODUCT_APPROVALS_POLICY_ID] = {
        **PLATFORM_APPROVALS.model_dump(mode="json"),
        "signoff": reordered,
    }
    context = _context()

    await stack.reviews().ensure(context, _submitted(), _requester(context))

    assert stack.run.started[0][1]["steps"] == reordered
    (delivery,) = stack.notifier.delivered
    assert delivery["recipients"] == [ACCOUNTANT]


async def test_each_signoff_round_has_its_own_thread_apart_from_the_reviews() -> None:
    case_id = uuid.uuid4()
    assert signoff_thread_id(case_id, 1) != signoff_thread_id(case_id, 2)
    assert signoff_thread_id(case_id, 1) != bod_review_thread_id(case_id, 1)


async def test_a_case_waiting_for_signoff_without_an_item_code_is_not_signed() -> None:
    """Fail closed: the domain never submits one, so a row saying otherwise
    gets no run rather than a sign-off naming no code."""
    stack = Stack()
    context = _context()
    with pytest.raises(ConflictError, match="item code"):
        await stack.reviews().ensure(context, _submitted(item_code=None), _requester(context))
    assert stack.run.started == []


async def test_the_lane_raises_a_missing_signoff_as_the_submitter() -> None:
    stack = Stack()
    case = _submitted()
    cases = FakeCaseList([case], history={case.id.value: _entered(case, TESTER)})

    outcome = await _lane(stack, cases, [(TENANT, WORKSPACE)]).run()

    assert (outcome.attempted, outcome.raised) == (1, 1)
    ((run, _),) = stack.run.started
    assert (run.worker_id, run.actor_id) == (product_signoff_graph.WORKER_ID, TESTER)


async def test_the_lane_tells_the_signers_of_a_later_step_raised_inside_the_run() -> None:
    """BGĐ signed; the run raised Kế toán's step on that decision, where
    nobody is told. The lane tells Kế toán, once per approval, and starts
    nothing."""
    stack = Stack()
    case = _submitted()
    later = Approval(
        id=uuid.uuid4(),
        approval_type=product_signoff_graph.SIGNOFF_APPROVAL_TYPE,
        payload={
            "product_dev_case_id": str(case.id),
            "item_code": "MH-0001",
            "step": "accounting",
            "step_label": "Kế toán",
            "step_no": 2,
            "steps": [{"step": "bod", "label": "BGĐ"}, {"step": "accounting", "label": "Kế toán"}],
        },
        created_at=NOW,
        required_scope=ACCOUNTING_SCOPE,
    )
    stack.inbox.pending[(WORKSPACE, str(case.id))] = later
    cases = FakeCaseList([case], history={case.id.value: _entered(case, TESTER)})

    outcome = await _lane(stack, cases, [(TENANT, WORKSPACE)]).run()

    assert outcome.attempted == 0
    assert stack.run.started == []
    (delivery,) = stack.notifier.delivered
    assert delivery["recipients"] == [ACCOUNTANT]
    assert delivery["source_key"] == f"supply_chain.signoff:{later.id}"
