import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from dw_agent_runtime.adapters.run_store import RunRecord, RunStatus
from dw_agent_runtime.approval_flow import ApproveAndResumeService
from dw_agent_runtime.contracts import RunContext
from dw_kernel.autonomy import AutonomyLevel
from dw_kernel.errors import ConflictError, NotFoundError, PermissionDeniedError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import FixedClock, SequentialIdGenerator
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.ports import PlatformUnitOfWork
from dw_platform.domain.approval import ApprovalDecision, ApprovalRequest, ApprovalStatus

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)
REQUESTER = uuid.UUID(int=1)
APPROVER = uuid.UUID(int=2)
WORKSPACE = uuid.UUID(int=101)


@dataclass
class FakeApprovalRepo:
    """Honours the port: another workspace's request is not found."""

    request: ApprovalRequest
    decisions: list[ApprovalDecision] = field(default_factory=list)

    async def get(
        self, approval_id: uuid.UUID, *, workspace_id: uuid.UUID
    ) -> ApprovalRequest | None:
        found = approval_id == self.request.id and workspace_id == self.request.workspace_id.value
        return self.request if found else None

    async def save(self, request: ApprovalRequest) -> None: ...

    async def add_decision(self, decision: ApprovalDecision) -> None:
        self.decisions.append(decision)


@dataclass
class FakeUoW:
    approvals: FakeApprovalRepo

    async def __aenter__(self) -> "FakeUoW":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    async def commit(self) -> None: ...

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
) -> ApproveAndResumeService:
    resolved = repo or FakeApprovalRepo(request=request)

    def uow_factory(context: AccessContext) -> PlatformUnitOfWork:
        return cast(PlatformUnitOfWork, FakeUoW(approvals=resolved))

    return ApproveAndResumeService(
        uow_factory=uow_factory,
        runner=cast(Any, runner),
        run_store=cast(Any, FakeRunStore(status=run_status, workspace_id=run_workspace)),
        clock=FixedClock(NOW),
        id_generator=SequentialIdGenerator(),
        strict_approval_prefixes=prefixes,
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
    """A graph that records who decided (the product case's BGĐ review)
    reads it from here: the run resumes with the REQUESTER's authority, so
    the decider's identity has no other way in."""
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


BOD_SCOPE = "supply_chain.approve.bod"


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
async def test_the_decide_right_alone_cannot_decide_a_stamped_request(approve: bool) -> None:
    """ADR 0020: `approvals.decide` is necessary, and for a stamped request not
    sufficient. Refused before anything is written and before the run resumes."""
    request = make_stamped_request(BOD_SCOPE)
    repo = FakeApprovalRepo(request=request)
    runner = FakeRunner(hosted=True)
    service = make_service(request, frozenset(), runner=runner, repo=repo)

    with pytest.raises(PermissionDeniedError) as refused:
        await _decide_as(service, decider(APPROVER), approve=approve)

    assert refused.value.details["action"] == BOD_SCOPE
    assert request.status is ApprovalStatus.PENDING
    assert request.version == 1
    assert repo.decisions == []
    assert runner.resumed == []
    # Refused before the run was even looked at: nothing past the gate ran.
    assert runner.asked == []


async def test_the_stamped_scope_without_the_decide_right_is_not_enough() -> None:
    """Both, not either: the stamp narrows `approvals.decide`, never replaces it."""
    request = make_stamped_request(BOD_SCOPE)
    runner = FakeRunner(hosted=True)
    service = make_service(request, frozenset(), runner=runner)
    context = AccessContext(
        tenant_id=uuid.UUID(int=100),
        workspace_id=uuid.UUID(int=101),
        principal_id=APPROVER,
        roles=frozenset({"sc_bod"}),
        scopes=frozenset({BOD_SCOPE}),
        plan_id="professional",
    )

    with pytest.raises(PermissionDeniedError) as refused:
        await _decide_as(service, context, approve=True)

    assert refused.value.details["action"] == "approvals.decide"
    assert runner.resumed == []


async def test_a_holder_of_the_stamped_scope_decides_and_resumes() -> None:
    request = make_stamped_request(BOD_SCOPE)
    repo = FakeApprovalRepo(request=request)
    runner = FakeRunner(hosted=True)
    service = make_service(request, frozenset(), runner=runner, repo=repo)

    decided = await _decide_as(service, decider(APPROVER, BOD_SCOPE), approve=True)

    assert decided.status is ApprovalStatus.APPROVED
    assert len(repo.decisions) == 1
    assert runner.resumed == [RUN_ID]


async def test_platform_admin_passes_the_stamped_scope_through_the_same_rule() -> None:
    """The stamp is checked by `authorization.require`, so the one admin rule in
    `ScopeAuthorizationService.is_allowed` applies to it as to every scope.
    Pinned so that a second, stricter check written beside it shows up here."""
    request = make_stamped_request(BOD_SCOPE)
    runner = FakeRunner(hosted=True)
    service = make_service(request, frozenset(), runner=runner)
    context = AccessContext(
        tenant_id=uuid.UUID(int=100),
        workspace_id=uuid.UUID(int=101),
        principal_id=APPROVER,
        roles=frozenset({"platform_admin"}),
        scopes=frozenset(),
        plan_id="professional",
    )

    decided = await _decide_as(service, context, approve=True)

    assert decided.status is ApprovalStatus.APPROVED
    assert runner.resumed == [RUN_ID]


async def test_the_requester_withdraws_a_stamped_request_without_the_scope() -> None:
    """Taking back your own request is not deciding it (ADR 0020), stamped or not."""
    request = make_stamped_request(BOD_SCOPE)
    runner = FakeRunner(hosted=True)
    service = make_service(request, frozenset(), runner=runner)

    withdrawn = await _decide_as(service, context_without_the_right(REQUESTER), approve=False)

    assert withdrawn.status is ApprovalStatus.REJECTED
    assert runner.resumed == [RUN_ID]


async def test_the_requester_cannot_approve_their_own_stamped_request_without_the_scope() -> None:
    request = make_stamped_request(BOD_SCOPE)
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
