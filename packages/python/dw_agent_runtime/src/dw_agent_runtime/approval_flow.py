"""Approve/reject a pending approval and resume its paused run.

Bridges platform approvals and the workflow runner: the human decision is
persisted first (aggregate invariant: decided exactly once), then the durable
run resumes with the decision payload.

An approval with no run has nothing to resume, so its decision is announced
instead: one outbox event, `decided_event_type(approval_type)`, written in the
decision's own transaction. A context that wants a consequence registers a
handler for its type where the worker is composed; this service never learns
which types exist.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC
from typing import Any

from dw_agent_runtime.adapters.run_store import RunRecord, RunStatus, SqlWorkerRunStore
from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.ports import WorkflowRunnerPort
from dw_kernel.autonomy import FAIL_CLOSED_LEVEL
from dw_kernel.errors import ConflictError, NotFoundError
from dw_kernel.ids import UserId
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.ports import PlatformUnitOfWorkFactory
from dw_platform.domain.approval import (
    APPROVALS_DECIDE,
    ApprovalDecision,
    ApprovalRequest,
    DecisionOutcome,
    decided_event_type,
)
from dw_platform.domain.outbox import OutboxEvent

DecisionGuard = Callable[[ApprovalRequest, AccessContext], None]
"""A condition one approval type puts on who may decide it, beyond the scope.

Raises to refuse. Registered by type at the composition root, the way strict
prefixes are, so a context with its own rule adds an entry rather than a branch
in `decide` (memory: the decider must be cleared for what they are deciding on).
"""

DECIDED_EVENT_SCHEMA = "1.0"


@dataclass
class ApproveAndResumeService:
    uow_factory: PlatformUnitOfWorkFactory
    runner: WorkflowRunnerPort
    run_store: SqlWorkerRunStore
    clock: UtcClock
    id_generator: IdGenerator
    strict_approval_prefixes: frozenset[str] = frozenset()
    # Keyed by the exact approval type. Checked where the decision is made,
    # before anything is written: a rule the inbox merely hides a button for is
    # a rule a direct POST walks past.
    decision_guards: Mapping[str, DecisionGuard] = field(default_factory=dict)

    def is_strict(self, approval_type: str) -> bool:
        """Whether this type demands a second person and a written reason.

        Public because the UI has to know before it offers a decision: a form
        that hardcoded the prefixes would be a second copy of this policy, and
        the one it had let approvers submit decisions the server always refused.
        """
        return approval_type.startswith(tuple(self.strict_approval_prefixes))

    def _enforce_strict_rules(
        self, request: ApprovalRequest, comment: str, context: AccessContext
    ) -> None:
        if request.requested_by.value == context.principal_id:
            raise ConflictError(
                "separation of duties: requester cannot approve their own request",
                details={
                    "approval_id": str(request.id),
                    "approval_type": request.approval_type,
                },
            )
        if not comment.strip():
            raise ConflictError(
                "this approval type requires a review comment",
                details={"approval_type": request.approval_type},
            )

    async def _resumable_run(
        self, context: AccessContext, request: ApprovalRequest
    ) -> RunRecord | None:
        if request.run_id is None:
            return None
        record = await self.run_store.get(
            self._run_context_for(context, request.run_id), request.run_id
        )
        if record.status is not RunStatus.WAITING_APPROVAL:
            raise ConflictError(
                "run is not waiting for approval",
                details={
                    "approval_id": str(request.id),
                    "run_id": str(request.run_id),
                    "status": record.status.value,
                },
            )
        if not self.runner.hosts(
            worker_id=record.worker_id,
            worker_version=record.worker_version,
            graph_version=record.graph_version,
        ):
            raise ConflictError(
                "this service does not run the graph that owns the approval",
                details={
                    "approval_id": str(request.id),
                    "worker_id": record.worker_id,
                    "worker_version": record.worker_version,
                    "graph_version": record.graph_version,
                },
            )
        return record

    async def decide(
        self,
        *,
        approval_id: uuid.UUID,
        approve: bool,
        comment: str,
        context: AccessContext,
        authorization: ScopeAuthorizationService,
        approved_action_ids: list[str] | None = None,
    ) -> ApprovalRequest:
        async with self.uow_factory(context) as uow:
            # Narrowed to the caller's workspace by the repository: RLS on
            # approval_requests narrows by tenant only. Another workspace's
            # request is not found, before any scope check, write or resume.
            request = await uow.approvals.get(approval_id, workspace_id=context.workspace_id)
            if request is None:
                raise NotFoundError(
                    "approval request not found", details={"approval_id": str(approval_id)}
                )
            # Approving is the decision that needs the right; WITHDRAWING your
            # own request is not. A seller who asks the assistant to do
            # something and then changes their mind must not have to find a
            # manager to take it back — without this the request sits pending
            # forever and its run stays parked (measured 2026-09-08: a sales
            # role got `permission_denied` on Reject as well as Approve).
            # The read moves above the gate so we know whose request it is;
            # it is tenant-scoped by RLS and workspace-scoped by the read above.
            if approve or request.requested_by.value != context.principal_id:
                await authorization.require(
                    context=context,
                    action=APPROVALS_DECIDE,
                    resource_type="approval_request",
                    resource_id=str(approval_id),
                )
                # Who may decide THIS request, stamped when it was raised
                # (ADR 0020) and read from the row, never from today's policy.
                # Through the same `require`, so the admin rule is the one
                # rule; and before any write or resume, so a refusal leaves
                # the request pending and the run parked.
                if request.required_scope is not None:
                    await authorization.require(
                        context=context,
                        action=request.required_scope,
                        resource_type="approval_request",
                        resource_id=str(approval_id),
                    )
            if self.is_strict(request.approval_type):
                self._enforce_strict_rules(request, comment, context)
            guard = self.decision_guards.get(request.approval_type)
            if guard is not None:
                guard(request, context)
            record = await self._resumable_run(context, request)

            decision = request.decide(
                decision_id=self.id_generator.new_uuid(),
                decided_by=UserId(context.principal_id),
                outcome=DecisionOutcome.APPROVED if approve else DecisionOutcome.REJECTED,
                decided_at=self.clock.now().astimezone(UTC),
                comment=comment,
            )
            await uow.approvals.save(request)
            await uow.approvals.add_decision(decision)
            if request.run_id is None:
                await uow.outbox.add(self._decided_event(request, decision))
            await uow.commit()

        if record is not None and request.run_id is not None:
            resume_payload: dict[str, Any] = {
                "approved": approve,
                "comment": comment,
                # Who decided, from the decider's verified context. The run
                # resumes with the requester's authority (below), so a graph
                # that records the decider has no other way to learn it; built
                # here, never copied from the approval's payload, which is the
                # graph's own interrupt value.
                "decided_by": str(context.principal_id),
            }
            if approved_action_ids is not None:
                resume_payload["approved_action_ids"] = approved_action_ids
            await self.runner.resume(
                run_context=RunContext(
                    run_id=request.run_id,
                    thread_id=record.thread_id,
                    tenant_id=context.tenant_id,
                    # The run's workspace, from its row, never the decider's:
                    # what the resumed graph reads and writes is the run's.
                    workspace_id=record.workspace_id,
                    actor_id=record.requested_by,
                    worker_id=record.worker_id,
                    worker_version=record.worker_version,
                    channel="web",
                    # Authority comes from the run, not from whoever is
                    # approving it. Separation of duties guarantees they are
                    # different people, so reading it from the approver's
                    # context handed their scopes to the requester's agent.
                    plan_id=record.actor_plan_id,
                    roles=record.actor_roles,
                    scopes=record.actor_scopes,
                    clearance=record.actor_clearance,
                    # Same reason as the roles and scopes above: the run
                    # resumes with the reach it started with, not with the
                    # approver's. Omitting these resumed with no owner limit
                    # at all, which widens rather than narrows.
                    record_visibility=record.actor_record_visibility,
                    visible_owners=record.actor_visible_owners,
                    # The autonomy the run started with, from its row — never
                    # re-resolved from the tenant's ceiling today, and never the
                    # approver's. A run started before 0006 has no stamp; it
                    # resumes at None, which the policy reads as ask-everything.
                    autonomy_level=record.autonomy_level,
                    autonomy_ceiling=record.autonomy_level or FAIL_CLOSED_LEVEL,
                    approval_policy_version=record.approval_policy_version,
                    trace_id=f"resume-{request.run_id.hex[:12]}",
                ),
                run_id=request.run_id,
                resume_payload=resume_payload,
            )
        return request

    def _decided_event(self, request: ApprovalRequest, decision: ApprovalDecision) -> OutboxEvent:
        """Identifiers and the outcome only. The comment and the request's payload
        stay where their readers are authorized: the outbox is read by a
        dispatcher that serves every tenant, not by a person with a scope."""
        return OutboxEvent(
            id=self.id_generator.new_uuid(),
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            event_type=decided_event_type(request.approval_type),
            schema_version=DECIDED_EVENT_SCHEMA,
            aggregate_id=request.id,
            occurred_at=decision.decided_at,
            payload={
                "approval_id": str(request.id),
                "decision_id": str(decision.id),
                "outcome": decision.outcome.value,
                "decided_by": str(decision.decided_by.value),
            },
            actor_id=decision.decided_by.value,
        )

    def _run_context_for(self, context: AccessContext, run_id: uuid.UUID) -> RunContext:
        return RunContext(
            run_id=run_id,
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor_id=context.principal_id,
            worker_id="unknown",
            worker_version="0.0.0",
            channel="web",
            plan_id=context.plan_id,
            roles=context.roles,
            scopes=context.scopes,
            clearance=context.clearance,
            trace_id=f"lookup-{run_id.hex[:12]}",
        )
