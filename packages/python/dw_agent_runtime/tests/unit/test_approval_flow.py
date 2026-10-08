import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from dw_agent_runtime.adapters.run_store import RunRecord, RunStatus
from dw_agent_runtime.approval_flow import (
    CHANNEL_DECIDED_ACTION,
    DECIDED_ACTION,
    ApproveAndResumeService,
    DecisionGuard,
)
from dw_agent_runtime.contracts import RunContext
from dw_kernel.autonomy import AutonomyLevel
from dw_kernel.errors import ConflictError, NotFoundError, PermissionDeniedError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import FixedClock, SequentialIdGenerator
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ApprovalAudience, ScopeAuthorizationService
from dw_platform.application.ports import PlatformUnitOfWork
from dw_platform.domain.approval import ApprovalDecision, ApprovalRequest, ApprovalStatus
from dw_platform.domain.audit import AuditEvent
from dw_platform.domain.outbox import OutboxEvent

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)
REQUESTER = uuid.UUID(int=1)
APPROVER = uuid.UUID(int=2)
WORKSPACE = uuid.UUID(int=101)


@dataclass
class FakeApprovalRepo:
    """Honours the port: another workspace's request is not found, nor one the
    audience may not see (ADR 0004)."""

    request: ApprovalRequest
    decisions: list[ApprovalDecision] = field(default_factory=list)

    async def get(
        self, approval_id: uuid.UUID, *, workspace_id: uuid.UUID, audience: ApprovalAudience
    ) -> ApprovalRequest | None:
        found = (
            approval_id == self.request.id
            and workspace_id == self.request.workspace_id.value
            and audience.may_see(self.request)
        )
        return self.request if found else None

    async def save(self, request: ApprovalRequest) -> None: ...

    async def add_decision(self, decision: ApprovalDecision) -> None:
        self.decisions.append(decision)


@dataclass
class FakeOutbox:
    """Keeps what was added and whether the UoW had already committed by then:
    an event added after the commit is one a crash between the two loses."""

    events: list[OutboxEvent] = field(default_factory=list)
    added_after_commit: list[OutboxEvent] = field(default_factory=list)
    committed: bool = False

    async def add(self, event: OutboxEvent) -> None:
        (self.added_after_commit if self.committed else self.events).append(event)

    async def list_unprocessed(self, limit: int = 100) -> list[OutboxEvent]:
        raise NotImplementedError("not exercised by ApproveAndResumeService")

    async def has_unprocessed(self, event_type: str, aggregate_id: uuid.UUID) -> bool:
        raise NotImplementedError("not exercised by ApproveAndResumeService")


@dataclass
class FakeAudit:
    """Like `FakeOutbox`: an event appended after the commit is one a crash
    between the two loses, so it is kept apart."""

    events: list[AuditEvent] = field(default_factory=list)
    added_after_commit: list[AuditEvent] = field(default_factory=list)
    committed: bool = False

    async def append(self, event: AuditEvent) -> None:
        (self.added_after_commit if self.committed else self.events).append(event)


@dataclass
class FakeUoW:
    approvals: FakeApprovalRepo
    outbox: FakeOutbox = field(default_factory=FakeOutbox)
    audit: FakeAudit = field(default_factory=FakeAudit)

    async def __aenter__(self) -> "FakeUoW":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    async def commit(self) -> None:
        self.outbox.committed = True
        self.audit.committed = True

    async def rollback(self) -> None: ...


RUN_ID = uuid.UUID(int=20)


REQUESTER_SCOPES = frozenset({"crm.read"})
# The ADR-003 roll-up the requester started the run with: themselves plus one
# report. The approver's own subtree is deliberately a different set.
REQUESTER_OWNERS = frozenset({REQUESTER, uuid.UUID(int=41)})
# Deliberately not the A4 a fresh context would get, so a resume that re-resolved
# it instead of replaying the stamp would be caught.
REQUESTER_AUTONOMY: AutonomyLevel = "A3"


@dataclass
class FakeRunStore:
    """Honours `SqlWorkerRunStore.get`: a run is read in its own workspace only."""

    status: RunStatus = RunStatus.WAITING_APPROVAL
    workspace_id: uuid.UUID = WORKSPACE
    worker_version: str = "1.0.0"
    actor_scopes: frozenset[str] = REQUESTER_SCOPES
    actor_clearance: str = "confidential"
    actor_record_visibility: str = "restricted"
    actor_visible_owners: frozenset[uuid.UUID] | None = REQUESTER_OWNERS

    async def get(self, run_context: RunContext, run_id: uuid.UUID) -> RunRecord:
        if run_context.workspace_id != self.workspace_id:
            raise NotFoundError("run not found", details={"run_id": str(run_id)})
        return RunRecord(
            id=run_id,
            thread_id=uuid.UUID(int=21),
            workspace_id=self.workspace_id,
            status=self.status,
            worker_id="sales_chat",
            worker_version=self.worker_version,
            graph_version="1.0.0",
            prompt_bundle_version="1.0.0",
            toolset_version="1.0.0",
            policy_version="1.0.0",
            memory_policy_version="1.0.0",
            autonomy_level=REQUESTER_AUTONOMY,
            approval_policy_version="1.0.0",
            input={},
            result=None,
            error=None,
            approval_request_id=uuid.UUID(int=10),
            release_manifest_ref=None,
            requested_by=REQUESTER,
            actor_roles=frozenset({"member"}),
            actor_scopes=self.actor_scopes,
            actor_plan_id="starter",
            actor_clearance=self.actor_clearance,
            actor_record_visibility=self.actor_record_visibility,
            actor_visible_owners=self.actor_visible_owners,
        )


@dataclass
class FakeRunner:
    hosted: bool
    asked: list[tuple[str, str, str]] = field(default_factory=list)
    resumed: list[uuid.UUID] = field(default_factory=list)
    contexts: list[RunContext] = field(default_factory=list)
    payloads: list[dict[str, Any]] = field(default_factory=list)

    def hosts(self, *, worker_id: str, worker_version: str, graph_version: str) -> bool:
        self.asked.append((worker_id, worker_version, graph_version))
        return self.hosted

    async def resume(
        self, *, run_context: RunContext, run_id: uuid.UUID, resume_payload: dict[str, Any]
    ) -> None:
        self.resumed.append(run_id)
        self.contexts.append(run_context)
        self.payloads.append(resume_payload)


def make_request(
    approval_type: str,
    run_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> ApprovalRequest:
    return ApprovalRequest(
        id=uuid.UUID(int=10),
        tenant_id=TenantId(uuid.UUID(int=100)),
        workspace_id=WorkspaceId(WORKSPACE),
        approval_type=approval_type,
        requested_by=UserId(REQUESTER),
        reason="cần duyệt",
        payload=payload or {},
        run_id=run_id,
    )


def make_service(
    request: ApprovalRequest,
    prefixes: frozenset[str],
    runner: Any = None,
    repo: FakeApprovalRepo | None = None,
    run_status: RunStatus = RunStatus.WAITING_APPROVAL,
    run_workspace: uuid.UUID = WORKSPACE,
    outbox: FakeOutbox | None = None,
    guards: dict[str, DecisionGuard] | None = None,
    audit: FakeAudit | None = None,
) -> ApproveAndResumeService:
    resolved = repo or FakeApprovalRepo(request=request)
    resolved_outbox = outbox or FakeOutbox()
    resolved_audit = audit or FakeAudit()

    def uow_factory(context: AccessContext) -> PlatformUnitOfWork:
        return cast(
            PlatformUnitOfWork,
            FakeUoW(approvals=resolved, outbox=resolved_outbox, audit=resolved_audit),
        )

    return ApproveAndResumeService(
        uow_factory=uow_factory,
        runner=cast(Any, runner),
        run_store=cast(Any, FakeRunStore(status=run_status, workspace_id=run_workspace)),
        clock=FixedClock(NOW),
        id_generator=SequentialIdGenerator(),
        strict_approval_prefixes=prefixes,
        decision_guards=guards or {},
    )


def make_context(principal: uuid.UUID) -> AccessContext:
    return AccessContext(
        tenant_id=uuid.UUID(int=100),
        workspace_id=uuid.UUID(int=101),
        principal_id=principal,
        roles=frozenset({"approver"}),
        scopes=frozenset({"approvals.decide"}),
        plan_id="professional",
    )


async def decide(
    service: ApproveAndResumeService, principal: uuid.UUID, comment: str
) -> ApprovalRequest:
    return await service.decide(
        approval_id=uuid.UUID(int=10),
        approve=True,
        comment=comment,
        context=make_context(principal),
        authorization=ScopeAuthorizationService(),
    )


async def test_strict_type_rejects_self_approval() -> None:
    service = make_service(make_request("sales_chat.email_send"), frozenset({"sales_chat."}))
    with pytest.raises(ConflictError, match="separation of duties"):
        await decide(service, REQUESTER, "ok")


async def test_strict_type_refuses_the_requester_withdrawing_too() -> None:
    """Withdrawing your own request skips `approvals.decide` and the stamped
    scope (a seller taking back an email needs no manager). For a strict type
    the separation-of-duties rule is then the only guard, and it must hold for
    a rejection too: where rejecting is itself a step (BGĐ not approving a
    sample cancels the case), the requester would otherwise take it."""
    service = make_service(make_request("sales_chat.email_send"), frozenset({"sales_chat."}))
    with pytest.raises(ConflictError, match="separation of duties"):
        await service.decide(
            approval_id=uuid.UUID(int=10),
            approve=False,
            comment="rút lại",
            context=make_context(REQUESTER).model_copy(
                update={"roles": frozenset({"member"}), "scopes": frozenset()}
            ),
            authorization=ScopeAuthorizationService(),
        )


async def test_strict_type_requires_a_comment() -> None:
    service = make_service(make_request("sales_chat.email_send"), frozenset({"sales_chat."}))
    with pytest.raises(ConflictError, match="review comment"):
        await decide(service, APPROVER, "   ")


async def test_strict_type_accepts_another_approver_with_a_comment() -> None:
    service = make_service(make_request("sales_chat.email_send"), frozenset({"sales_chat."}))
    request = await decide(service, APPROVER, "đã kiểm tra nội dung")
    assert request.status.value == "approved"


async def test_non_strict_type_allows_self_approval_without_comment() -> None:
    service = make_service(make_request("demo.dispatch"), frozenset({"sales_chat."}))
    request = await decide(service, REQUESTER, "")
    assert request.status.value == "approved"


async def test_a_host_that_cannot_resume_records_nothing() -> None:
    """A decision is spendable once, so it must not survive a refused resume."""
    request = make_request("demo.dispatch", run_id=RUN_ID)
    repo = FakeApprovalRepo(request=request)
    runner = FakeRunner(hosted=False)
    service = make_service(request, frozenset(), runner=runner, repo=repo)

    with pytest.raises(ConflictError, match="does not run the graph"):
        await decide(service, APPROVER, "")

    assert request.status.value == "pending"
    assert repo.decisions == []
    assert runner.resumed == []


async def test_the_guard_asks_about_everything_resume_needs() -> None:
    """The guard and resume() must ask one question, not two different ones.

    resume() replays both the graph the run started on and that worker
    version's settings. While the guard asked only about the graph, retiring a
    worker version let the decision commit and then failed the resume.
    """
    request = make_request("demo.dispatch", run_id=RUN_ID)
    runner = FakeRunner(hosted=True)
    service = make_service(request, frozenset(), runner=runner)

    await decide(service, APPROVER, "")

    assert runner.asked == [("sales_chat", "1.0.0", "1.0.0")]


async def test_a_run_no_longer_waiting_records_nothing() -> None:
    """The other precondition resume() enforces, checked before the write."""
    request = make_request("demo.dispatch", run_id=RUN_ID)
    repo = FakeApprovalRepo(request=request)
    runner = FakeRunner(hosted=True)
    service = make_service(
        request, frozenset(), runner=runner, repo=repo, run_status=RunStatus.FAILED
    )

    with pytest.raises(ConflictError, match="not waiting for approval"):
        await decide(service, APPROVER, "")

    assert request.status.value == "pending"
    assert repo.decisions == []
    assert runner.resumed == []


async def test_the_owning_host_decides_and_resumes() -> None:
    request = make_request("demo.dispatch", run_id=RUN_ID)
    repo = FakeApprovalRepo(request=request)
    runner = FakeRunner(hosted=True)
    service = make_service(request, frozenset(), runner=runner, repo=repo)

    decided = await decide(service, APPROVER, "")

    assert decided.status.value == "approved"
    assert len(repo.decisions) == 1
    assert runner.resumed == [RUN_ID]


async def test_the_resumed_run_carries_the_requesters_authority() -> None:
    """Separation of duties makes the approver a different person every time.

    Reading roles/scopes from their AccessContext handed the requester's agent
    whatever the approver happened to hold — either too much power, or too
    little to finish the turn the approval had already been spent on.
    """
    request = make_request("demo.dispatch", run_id=RUN_ID)
    runner = FakeRunner(hosted=True)
    service = make_service(request, frozenset(), runner=runner)

    await decide(service, APPROVER, "")

    resumed = runner.contexts[0]
    assert resumed.scopes == REQUESTER_SCOPES
    assert resumed.roles == frozenset({"member"})
    assert resumed.plan_id == "starter"
    assert resumed.actor_id == REQUESTER
    # Clearance is authority too: retrieval turns it into the set of document
    # classifications the run may read, so losing it here shrinks what the
    # second half of the turn can see compared with the first.
    assert resumed.clearance == "confidential"
    # So is the owner roll-up. Without it the resumed half of the turn read
    # the whole workspace while the first half stayed inside the subtree.
    assert resumed.record_visibility == "restricted"
    assert resumed.visible_owners == REQUESTER_OWNERS
    # And autonomy, from the run's own stamp. Not re-resolved from the tenant's
    # ceiling today, and not the approver's: resumed at None it would ask about
    # everything; resumed at a level read fresh it would be allowed whatever the
    # ceiling happens to be now rather than what the run was started under.
    assert resumed.autonomy_level == REQUESTER_AUTONOMY
    assert resumed.approval_policy_version == "1.0.0"

    approver = make_context(APPROVER)
    assert not (resumed.scopes & approver.scopes)
    assert not (resumed.roles & approver.roles)
    # And the workspace, from the run's row: what the resumed graph reads and
    # writes is the run's workspace (approval-audit-and-workspace/02).
    assert resumed.workspace_id == WORKSPACE


@pytest.mark.parametrize("approve", [True, False])
async def test_the_resume_names_the_decider_from_their_verified_context(approve: bool) -> None:
    """A graph that records who decided reads it from here: the run resumes
    with the REQUESTER's authority, so the decider's identity has no other
    way in."""
    request = make_request("demo.dispatch", run_id=RUN_ID)
    runner = FakeRunner(hosted=True)
    service = make_service(request, frozenset(), runner=runner)

    await service.decide(
        approval_id=uuid.UUID(int=10),
        approve=approve,
        comment="đã xem",
        context=make_context(APPROVER),
        authorization=ScopeAuthorizationService(),
    )

    (payload,) = runner.payloads
    assert payload["decided_by"] == str(APPROVER)
    assert (payload["approved"], payload["comment"]) == (approve, "đã xem")
    # The run itself still resumes as the requester's.
    assert runner.contexts[0].actor_id == REQUESTER


async def test_an_interrupt_payload_naming_a_decider_cannot_override_the_real_one() -> None:
    """The approval's payload is the graph's interrupt value. A key named
    `decided_by` there is not where the decider comes from."""
    request = make_request("demo.dispatch", run_id=RUN_ID, payload={"decided_by": str(REQUESTER)})
    runner = FakeRunner(hosted=True)
    service = make_service(request, frozenset(), runner=runner)

    await decide(service, APPROVER, "")

    assert runner.payloads[0]["decided_by"] == str(APPROVER)


async def test_another_workspaces_approval_is_not_found_and_nothing_moves() -> None:
    """RLS narrows approvals by tenant only; the read narrows the workspace, and
    refuses before any scope check, write or resume."""
    request = make_request("demo.dispatch", run_id=RUN_ID)
    repo = FakeApprovalRepo(request=request)
    runner = FakeRunner(hosted=True)
    service = make_service(request, frozenset(), runner=runner, repo=repo)
    elsewhere = AccessContext(
        tenant_id=uuid.UUID(int=100),
        workspace_id=uuid.UUID(int=102),
        principal_id=APPROVER,
        roles=frozenset({"approver"}),
        scopes=frozenset({"approvals.decide"}),
        plan_id="professional",
    )

    with pytest.raises(NotFoundError, match="approval request not found"):
        await _decide_as(service, elsewhere, approve=True)

    assert request.status is ApprovalStatus.PENDING
    assert repo.decisions == []
    assert runner.asked == []
    assert runner.resumed == []


async def test_a_run_in_another_workspace_than_its_approval_is_not_resumed() -> None:
    """Fails closed if the two ever disagree: the run is read in the decider's
    (and so the approval's) workspace, and is not found there."""
    request = make_request("demo.dispatch", run_id=RUN_ID)
    repo = FakeApprovalRepo(request=request)
    runner = FakeRunner(hosted=True)
    service = make_service(
        request, frozenset(), runner=runner, repo=repo, run_workspace=uuid.UUID(int=102)
    )

    with pytest.raises(NotFoundError, match="run not found"):
        await decide(service, APPROVER, "")

    assert request.status is ApprovalStatus.PENDING
    assert repo.decisions == []
    assert runner.resumed == []


def context_without_the_right(principal: uuid.UUID) -> AccessContext:
    """A plain seller: may ask the agent for things, may not decide them."""
    return AccessContext(
        tenant_id=uuid.UUID(int=100),
        workspace_id=uuid.UUID(int=101),
        principal_id=principal,
        roles=frozenset({"sales"}),
        scopes=frozenset(),
        plan_id="professional",
    )


async def test_the_requester_can_withdraw_without_the_decide_right() -> None:
    """Rejecting your OWN pending request is taking it back, not deciding it."""
    service = make_service(make_request("sales_chat.crm.add_person_note"), frozenset())
    request = await service.decide(
        approval_id=uuid.UUID(int=10),
        approve=False,
        comment="thôi, tôi nhầm",
        context=context_without_the_right(REQUESTER),
        authorization=ScopeAuthorizationService(),
    )
    assert request.status is ApprovalStatus.REJECTED


async def test_the_requester_still_cannot_approve_their_own_request() -> None:
    service = make_service(make_request("sales_chat.crm.add_person_note"), frozenset())
    with pytest.raises(PermissionDeniedError):
        await service.decide(
            approval_id=uuid.UUID(int=10),
            approve=True,
            comment="ok",
            context=context_without_the_right(REQUESTER),
            authorization=ScopeAuthorizationService(),
        )


BOARD_SCOPE = "demo.approve.board"


def make_stamped_request(required_scope: str | None) -> ApprovalRequest:
    request = make_request("demo.dispatch", run_id=RUN_ID)
    request.required_scope = required_scope
    return request


def decider(principal: uuid.UUID, *extra_scopes: str) -> AccessContext:
    return AccessContext(
        tenant_id=uuid.UUID(int=100),
        workspace_id=uuid.UUID(int=101),
        principal_id=principal,
        roles=frozenset({"approver"}),
        scopes=frozenset({"approvals.decide", *extra_scopes}),
        plan_id="professional",
    )


async def _decide_as(
    service: ApproveAndResumeService, context: AccessContext, *, approve: bool
) -> ApprovalRequest:
    return await service.decide(
        approval_id=uuid.UUID(int=10),
        approve=approve,
        comment="",
        context=context,
        authorization=ScopeAuthorizationService(),
    )


@pytest.mark.parametrize("approve", [True, False], ids=["approve", "reject"])
async def test_the_decide_right_alone_does_not_find_a_stamped_request(approve: bool) -> None:
    """ADR 0004: `approvals.decide` is necessary, and for a stamped request not
    sufficient; since the amendment of 2026-10-07 who may not decide it and did
    not ask for it does not find it either. Not found before anything is
    written and before the run is looked at."""
    request = make_stamped_request(BOARD_SCOPE)
    repo = FakeApprovalRepo(request=request)
    runner = FakeRunner(hosted=True)
    service = make_service(request, frozenset(), runner=runner, repo=repo)

    with pytest.raises(NotFoundError):
        await _decide_as(service, decider(APPROVER), approve=approve)

    assert request.status is ApprovalStatus.PENDING
    assert request.version == 1
    assert repo.decisions == []
    assert runner.resumed == []
    assert runner.asked == []


async def test_the_decide_right_alone_cannot_approve_even_your_own_stamped_request() -> None:
    """The requester sees their stamped request, so the read lets them through;
    the gate behind it still refuses an approval by naming the stamp. Tested
    here directly, not through the read that hides the request from others
    (failure-modes #5)."""
    request = make_stamped_request(BOARD_SCOPE)
    repo = FakeApprovalRepo(request=request)
    runner = FakeRunner(hosted=True)
    service = make_service(request, frozenset(), runner=runner, repo=repo)

    with pytest.raises(PermissionDeniedError) as refused:
        await _decide_as(service, decider(REQUESTER), approve=True)

    assert refused.value.details["action"] == BOARD_SCOPE
    assert request.status is ApprovalStatus.PENDING
    assert request.version == 1
    assert repo.decisions == []
    assert runner.resumed == []
    # Refused before the run was even looked at: nothing past the gate ran.
    assert runner.asked == []


@pytest.mark.parametrize(
    ("principal", "error"),
    [(APPROVER, NotFoundError), (REQUESTER, PermissionDeniedError)],
    ids=["someone-else", "requester"],
)
async def test_the_stamped_scope_without_the_decide_right_is_not_enough(
    principal: uuid.UUID, error: type[Exception]
) -> None:
    """Both, not either: the stamp narrows `approvals.decide`, never replaces it.
    Someone else does not find the request; its requester finds it and is
    refused by the gate, which names `approvals.decide`."""
    request = make_stamped_request(BOARD_SCOPE)
    runner = FakeRunner(hosted=True)
    service = make_service(request, frozenset(), runner=runner)
    context = AccessContext(
        tenant_id=uuid.UUID(int=100),
        workspace_id=uuid.UUID(int=101),
        principal_id=principal,
        roles=frozenset({"board"}),
        scopes=frozenset({BOARD_SCOPE}),
        plan_id="professional",
    )

    with pytest.raises(error) as refused:
        await _decide_as(service, context, approve=True)

    if isinstance(refused.value, PermissionDeniedError):
        assert refused.value.details["action"] == "approvals.decide"
    assert runner.resumed == []


async def test_a_holder_of_the_stamped_scope_decides_and_resumes() -> None:
    request = make_stamped_request(BOARD_SCOPE)
    repo = FakeApprovalRepo(request=request)
    runner = FakeRunner(hosted=True)
    service = make_service(request, frozenset(), runner=runner, repo=repo)

    decided = await _decide_as(service, decider(APPROVER, BOARD_SCOPE), approve=True)

    assert decided.status is ApprovalStatus.APPROVED
    assert len(repo.decisions) == 1
    assert runner.resumed == [RUN_ID]


def platform_admin(principal: uuid.UUID, *extra_scopes: str) -> AccessContext:
    """`platform_admin` as the seed grants it: the role and `platform.admin`, so
    `approvals.decide` reaches it only through `ScopeAuthorizationService`'s
    admin rule."""
    return AccessContext(
        tenant_id=uuid.UUID(int=100),
        workspace_id=uuid.UUID(int=101),
        principal_id=principal,
        roles=frozenset({"platform_admin"}),
        scopes=frozenset({"platform.admin", *extra_scopes}),
        plan_id="professional",
    )


@pytest.mark.parametrize("approve", [True, False], ids=["approve", "reject"])
async def test_platform_admin_does_not_find_a_stamped_request(approve: bool) -> None:
    """ADR 0004 (2026-10-06): a platform operator is not the business's board. The
    admin rule still grants `approvals.decide`; the stamp needs the scope itself,
    and since the amendment of 2026-10-07 who may not decide a stamped request
    does not find it. Nothing written, the run not looked at."""
    request = make_stamped_request(BOARD_SCOPE)
    repo = FakeApprovalRepo(request=request)
    runner = FakeRunner(hosted=True)
    service = make_service(request, frozenset(), runner=runner, repo=repo)

    with pytest.raises(NotFoundError):
        await _decide_as(service, platform_admin(APPROVER), approve=approve)

    assert request.status is ApprovalStatus.PENDING
    assert repo.decisions == []
    assert runner.asked == []
    assert runner.resumed == []


async def test_platform_admin_does_not_pass_the_stamp_on_their_own_request() -> None:
    """The admin who raised a stamped request sees it, and the gate behind the
    read refuses their approval by naming the stamp: the admin rule never
    stands in for it. Refused before anything is written or looked at."""
    request = make_stamped_request(BOARD_SCOPE)
    repo = FakeApprovalRepo(request=request)
    runner = FakeRunner(hosted=True)
    service = make_service(request, frozenset(), runner=runner, repo=repo)

    with pytest.raises(PermissionDeniedError) as refused:
        await _decide_as(service, platform_admin(REQUESTER), approve=True)

    assert refused.value.message == "action not permitted"
    assert refused.value.details == {
        "action": BOARD_SCOPE,
        "resource_type": "approval_request",
        "resource_id": str(uuid.UUID(int=10)),
    }
    assert request.status is ApprovalStatus.PENDING
    assert request.version == 1
    assert repo.decisions == []
    assert runner.asked == []
    assert runner.resumed == []


async def test_platform_admin_holding_the_stamped_scope_decides() -> None:
    request = make_stamped_request(BOARD_SCOPE)
    runner = FakeRunner(hosted=True)
    service = make_service(request, frozenset(), runner=runner)

    decided = await _decide_as(service, platform_admin(APPROVER, BOARD_SCOPE), approve=True)

    assert decided.status is ApprovalStatus.APPROVED
    assert runner.resumed == [RUN_ID]


async def test_platform_admin_still_decides_an_unstamped_request() -> None:
    """No stamp, today's rule: the admin rule answers `approvals.decide`."""
    request = make_stamped_request(None)
    runner = FakeRunner(hosted=True)
    service = make_service(request, frozenset(), runner=runner)

    decided = await _decide_as(service, platform_admin(APPROVER), approve=True)

    assert decided.status is ApprovalStatus.APPROVED
    assert runner.resumed == [RUN_ID]


@pytest.mark.parametrize(
    ("required_scope", "context", "expected"),
    [
        (BOARD_SCOPE, decider(APPROVER, BOARD_SCOPE), True),
        (BOARD_SCOPE, decider(APPROVER), False),
        (BOARD_SCOPE, platform_admin(APPROVER), False),
        (BOARD_SCOPE, platform_admin(APPROVER, BOARD_SCOPE), True),
        (None, platform_admin(APPROVER), True),
        (None, decider(APPROVER), True),
        (None, context_without_the_right(APPROVER), False),
    ],
    ids=[
        "holder",
        "decide-right-only",
        "admin-without-stamp",
        "admin-with-stamp",
        "admin-unstamped",
        "decider-unstamped",
        "no-decide-right",
    ],
)
def test_may_decide_answers_as_decide_does(
    required_scope: str | None, context: AccessContext, expected: bool
) -> None:
    """The inbox's answer, from the same two checks the decision runs."""
    request = make_stamped_request(required_scope)
    service = make_service(request, frozenset())

    assert service.may_decide(request, context, ScopeAuthorizationService()) is expected


async def test_the_requester_withdraws_a_stamped_request_without_the_scope() -> None:
    """Taking back your own request is not deciding it (ADR 0004), stamped or not."""
    request = make_stamped_request(BOARD_SCOPE)
    runner = FakeRunner(hosted=True)
    service = make_service(request, frozenset(), runner=runner)

    withdrawn = await _decide_as(service, context_without_the_right(REQUESTER), approve=False)

    assert withdrawn.status is ApprovalStatus.REJECTED
    assert runner.resumed == [RUN_ID]


async def test_the_requester_cannot_approve_their_own_stamped_request_without_the_scope() -> None:
    request = make_stamped_request(BOARD_SCOPE)
    runner = FakeRunner(hosted=True)
    service = make_service(request, frozenset(), runner=runner)

    with pytest.raises(PermissionDeniedError):
        await _decide_as(service, decider(REQUESTER), approve=True)
    assert runner.resumed == []


async def test_a_bystander_without_the_right_cannot_reject_someone_elses() -> None:
    service = make_service(make_request("sales_chat.crm.add_person_note"), frozenset())
    with pytest.raises(PermissionDeniedError):
        await service.decide(
            approval_id=uuid.UUID(int=10),
            approve=False,
            comment="không liên quan tới tôi",
            context=context_without_the_right(APPROVER),
            authorization=ScopeAuthorizationService(),
        )


# ------------------------------------------- run-less approvals: the hook --


async def test_a_runless_decision_announces_itself_inside_the_transaction() -> None:
    """Nothing resumes a run-less approval, so a context hears of its decision
    only through this event. Added after the commit, a crash between the two
    would leave a decision nobody ever acts on."""
    request = make_request("memory.review")
    outbox = FakeOutbox()
    repo = FakeApprovalRepo(request=request)
    service = make_service(request, frozenset(), repo=repo, outbox=outbox)

    await decide(service, APPROVER, "đã đọc")

    assert outbox.added_after_commit == []
    [event] = outbox.events
    assert event.event_type == "memory.review.decided"
    assert event.aggregate_id == request.id
    assert event.tenant_id == request.tenant_id
    assert event.workspace_id == request.workspace_id
    assert event.actor_id == APPROVER
    assert event.payload == {
        "approval_id": str(request.id),
        "decision_id": str(repo.decisions[0].id),
        "outcome": "approved",
        "decided_by": str(APPROVER),
    }


async def test_a_rejection_announces_the_rejection() -> None:
    request = make_request("memory.review")
    outbox = FakeOutbox()
    service = make_service(request, frozenset(), outbox=outbox)

    await service.decide(
        approval_id=request.id,
        approve=False,
        comment="không đúng",
        context=make_context(APPROVER),
        authorization=ScopeAuthorizationService(),
    )

    assert [event.payload["outcome"] for event in outbox.events] == ["rejected"]


async def test_a_decision_that_resumes_a_run_announces_nothing() -> None:
    """The run is that decision's consequence; a second channel for it would be
    a second consumer acting on one decision."""
    request = make_request("demo.dispatch", run_id=RUN_ID)
    outbox = FakeOutbox()
    service = make_service(request, frozenset(), runner=FakeRunner(hosted=True), outbox=outbox)

    await decide(service, APPROVER, "")

    assert outbox.events == []


def _refuse(request: ApprovalRequest, context: AccessContext) -> None:
    raise PermissionDeniedError("not cleared for this approval")


async def test_a_guard_registered_for_the_type_can_refuse_and_nothing_is_recorded() -> None:
    request = make_request("memory.review")
    repo = FakeApprovalRepo(request=request)
    outbox = FakeOutbox()
    service = make_service(
        request, frozenset(), repo=repo, outbox=outbox, guards={"memory.review": _refuse}
    )

    with pytest.raises(PermissionDeniedError, match="not cleared"):
        await decide(service, APPROVER, "ok")

    assert request.status is ApprovalStatus.PENDING
    assert repo.decisions == []
    assert outbox.events == []


async def test_a_guard_for_another_type_is_not_asked() -> None:
    service = make_service(
        make_request("demo.dispatch"), frozenset(), guards={"memory.review": _refuse}
    )

    decided = await decide(service, APPROVER, "")

    assert decided.status is ApprovalStatus.APPROVED


# ---- the channel a decision came from, and what admitted it (ticket Z5) ------


@dataclass
class FakeAdmission:
    """A `DecisionAdmission` that records when it ran and what it saw."""

    refuse: bool = False
    seen: list[tuple[int, list[ApprovalDecision]]] = field(default_factory=list)
    repo: FakeApprovalRepo | None = None

    async def admit(
        self, uow: PlatformUnitOfWork, request: ApprovalRequest, context: AccessContext
    ) -> dict[str, object]:
        decided = [] if self.repo is None else list(self.repo.decisions)
        self.seen.append((request.version, decided))
        if self.refuse:
            raise ConflictError("not admitted")
        return {"code_id": "c-1", "message_id": "m-1"}


async def test_a_decision_records_its_channel_and_the_run_resumes_on_it() -> None:
    request = make_request("sales_chat.email_send", run_id=RUN_ID)
    repo = FakeApprovalRepo(request=request)
    runner = FakeRunner(hosted=True)
    service = make_service(request, frozenset(), runner=runner, repo=repo)

    await service.decide(
        approval_id=request.id,
        approve=True,
        comment="ok",
        context=make_context(APPROVER),
        authorization=ScopeAuthorizationService(),
        channel="zalo",
    )

    assert [d.channel for d in repo.decisions] == ["zalo"]
    assert [c.channel for c in runner.contexts] == ["zalo"]


async def test_the_web_is_the_channel_when_none_is_named() -> None:
    request = make_request("sales_chat.email_send", run_id=RUN_ID)
    repo = FakeApprovalRepo(request=request)
    runner = FakeRunner(hosted=True)
    audit = FakeAudit()
    service = make_service(request, frozenset(), runner=runner, repo=repo, audit=audit)

    await decide(service, APPROVER, "ok")

    assert [d.channel for d in repo.decisions] == ["web"]
    assert [c.channel for c in runner.contexts] == ["web"]
    # A web decision is audited as one, not as a channel decision.
    assert [event.action for event in audit.events] == [DECIDED_ACTION]


async def test_an_admission_runs_before_anything_is_written_and_its_refusal_writes_nothing() -> (
    None
):
    request = make_request("sales_chat.email_send", run_id=RUN_ID)
    repo = FakeApprovalRepo(request=request)
    runner = FakeRunner(hosted=True)
    audit = FakeAudit()
    outbox = FakeOutbox()
    service = make_service(
        request, frozenset(), runner=runner, repo=repo, audit=audit, outbox=outbox
    )
    admission = FakeAdmission(refuse=True, repo=repo)

    with pytest.raises(ConflictError, match="not admitted"):
        await service.decide(
            approval_id=request.id,
            approve=True,
            comment="ok",
            context=make_context(APPROVER),
            authorization=ScopeAuthorizationService(),
            channel="zalo",
            admission=admission,
        )

    # It saw the request undecided (version 1) and no decision written yet.
    assert admission.seen == [(1, [])]
    assert repo.decisions == []
    assert runner.resumed == []
    assert audit.events == []
    assert outbox.events == []


async def test_an_admission_runs_after_decides_own_checks() -> None:
    """A requester on a strict type is refused by `decide` itself, before the
    admission could consume anything."""
    request = make_request("sales_chat.email_send")
    service = make_service(request, frozenset({"sales_chat."}))
    admission = FakeAdmission()

    with pytest.raises(ConflictError, match="separation of duties"):
        await service.decide(
            approval_id=request.id,
            approve=True,
            comment="ok",
            context=make_context(REQUESTER),
            authorization=ScopeAuthorizationService(),
            admission=admission,
        )

    assert admission.seen == []


async def test_an_admitted_decision_is_audited_with_what_admitted_it() -> None:
    request = make_request("sales_chat.email_send")
    repo = FakeApprovalRepo(request=request)
    audit = FakeAudit()
    service = make_service(request, frozenset(), repo=repo, audit=audit)

    await service.decide(
        approval_id=request.id,
        approve=False,
        comment="không",
        context=make_context(APPROVER),
        authorization=ScopeAuthorizationService(),
        channel="zalo",
        admission=FakeAdmission(),
    )

    (event,) = audit.events
    (decision,) = repo.decisions
    assert event.action == CHANNEL_DECIDED_ACTION
    assert event.resource_id == str(request.id)
    assert event.actor_id.value == APPROVER
    assert event.details == {
        "code_id": "c-1",
        "message_id": "m-1",
        "channel": "zalo",
        "decision_id": str(decision.id),
        "outcome": "rejected",
        "approval_type": "sales_chat.email_send",
    }


# ---- every decision on the audit log (approval-audit-and-workspace/01) -------


@pytest.mark.parametrize(
    ("principal", "approve", "outcome", "withdrawn"),
    [
        (APPROVER, True, "approved", False),
        (APPROVER, False, "rejected", False),
        (REQUESTER, False, "rejected", True),
    ],
    ids=["approved", "rejected", "withdrawn"],
)
async def test_a_decision_is_audited_in_its_own_transaction_naming_the_decider(
    principal: uuid.UUID, approve: bool, outcome: str, withdrawn: bool
) -> None:
    request = make_request(
        "demo.dispatch", run_id=RUN_ID, payload={"amount": 9000, "customer": "x"}
    )
    repo = FakeApprovalRepo(request=request)
    audit = FakeAudit()
    service = make_service(
        request, frozenset(), runner=FakeRunner(hosted=True), repo=repo, audit=audit
    )

    await service.decide(
        approval_id=request.id,
        approve=approve,
        comment="ghi chú nghiệp vụ",
        context=make_context(principal),
        authorization=ScopeAuthorizationService(),
    )

    assert audit.added_after_commit == []
    (event,) = audit.events
    (decision,) = repo.decisions
    assert event.action == DECIDED_ACTION
    assert event.actor_id.value == principal
    assert (event.resource_type, event.resource_id) == ("approval_request", str(request.id))
    assert event.run_id == RUN_ID
    assert event.occurred_at == decision.decided_at
    assert event.details == {
        "approval_type": "demo.dispatch",
        "outcome": outcome,
        "decision_id": str(decision.id),
        "withdrawn": withdrawn,
    }
    # Neither the comment nor any key of the payload reaches the audit log.
    assert "ghi chú nghiệp vụ" not in str(event.details)
    assert not set(event.details) & {"comment", *request.payload}


async def _refused(service: ApproveAndResumeService, principal: uuid.UUID, comment: str) -> None:
    with pytest.raises((ConflictError, PermissionDeniedError, NotFoundError)):
        await service.decide(
            approval_id=uuid.UUID(int=10),
            approve=True,
            comment=comment,
            context=make_context(principal),
            authorization=ScopeAuthorizationService(),
        )


async def test_a_refused_decision_writes_no_audit() -> None:
    cases: list[
        tuple[ApprovalRequest, frozenset[str], uuid.UUID, str, dict[str, DecisionGuard]]
    ] = [
        # Strict: the requester, then a missing comment.
        (make_request("sales_chat.email_send"), frozenset({"sales_chat."}), REQUESTER, "ok", {}),
        (make_request("sales_chat.email_send"), frozenset({"sales_chat."}), APPROVER, " ", {}),
        # A per-type guard refuses.
        (make_request("memory.review"), frozenset(), APPROVER, "ok", {"memory.review": _refuse}),
    ]
    for request, prefixes, principal, comment, guards in cases:
        audit = FakeAudit()
        service = make_service(request, prefixes, audit=audit, guards=guards)
        await _refused(service, principal, comment)
        assert audit.events == [] and audit.added_after_commit == []

    # The run is not waiting for approval any more.
    audit = FakeAudit()
    service = make_service(
        make_request("demo.dispatch", run_id=RUN_ID),
        frozenset(),
        runner=FakeRunner(hosted=True),
        run_status=RunStatus.COMPLETED,
        audit=audit,
    )
    await _refused(service, APPROVER, "")
    assert audit.events == []

    # No `approvals.decide`, on someone else's request.
    audit = FakeAudit()
    service = make_service(make_request("demo.dispatch"), frozenset(), audit=audit)
    with pytest.raises(PermissionDeniedError):
        await service.decide(
            approval_id=uuid.UUID(int=10),
            approve=False,
            comment="",
            context=context_without_the_right(APPROVER),
            authorization=ScopeAuthorizationService(),
        )
    assert audit.events == []


async def test_a_channel_decision_keeps_its_own_audit_and_writes_one_row() -> None:
    request = make_request("sales_chat.email_send")
    audit = FakeAudit()
    service = make_service(request, frozenset(), audit=audit)

    await service.decide(
        approval_id=request.id,
        approve=True,
        comment="ok",
        context=make_context(APPROVER),
        authorization=ScopeAuthorizationService(),
        channel="zalo",
        admission=FakeAdmission(),
    )

    assert [event.action for event in audit.events] == [CHANNEL_DECIDED_ACTION]
