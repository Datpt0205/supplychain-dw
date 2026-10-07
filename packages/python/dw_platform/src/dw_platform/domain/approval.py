"""Approval aggregate: human decisions gating critical side effects (§11.3).

Invariant: a request is decided exactly once; decisions are immutable facts.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from dw_kernel.errors import ConflictError
from dw_kernel.ids import TenantId, UserId, WorkspaceId

# The scope every decision needs (`ApproveAndResumeService.decide`), besides a
# request's own `required_scope`. Named once: whoever asks "who could decide
# this?" (the inbox's `can_decide`) must ask the question decide enforces.
APPROVALS_DECIDE = "approvals.decide"


def decided_event_type(approval_type: str) -> str:
    """The outbox event a decision on a run-less approval of this type announces.

    One function because two packages name it: the approval flow that writes the
    event and the context that registers a handler for it. Spelt twice, a rename
    on one side leaves the handler waiting for an event nobody sends.
    """
    return f"{approval_type}.decided"


def approval_link(approval_id: uuid.UUID, workspace_id: uuid.UUID) -> str:
    """The portal page of one approval, relative to the web app, for a
    notification's `link` (and so for the Zalo message Z2 sends from it).

    The workspace rides along so the page can switch to it after sign-in when
    the viewer's last workspace was another one; the page still checks the
    viewer belongs to it, and the API still answers only within the active
    workspace. One function, so every notice links the page the same way.
    """
    return f"/approvals/{approval_id}?workspace={workspace_id}"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class DecisionOutcome(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    """Immutable record of one human decision."""

    id: uuid.UUID
    request_id: uuid.UUID
    tenant_id: TenantId
    workspace_id: WorkspaceId
    decided_by: UserId
    outcome: DecisionOutcome
    comment: str
    decided_at: datetime
    # Where the decision was made: `web`, or `zalo` after a portal view (ADR 0014).
    channel: str = "web"


@dataclass(slots=True)
class ApprovalRequest:
    """A pending question for a human; pauses the workflow that raised it."""

    id: uuid.UUID
    tenant_id: TenantId
    workspace_id: WorkspaceId
    approval_type: str
    requested_by: UserId
    reason: str
    payload: dict[str, object] = field(default_factory=dict)
    run_id: uuid.UUID | None = None
    # The scope a decider must hold besides `approvals.decide` (ADR 0004).
    # Stamped once, when the request is raised, from what the node read in
    # policy then; never re-derived, so a policy changed while the request
    # waits does not change who may decide it. None: `approvals.decide` alone.
    required_scope: str | None = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime | None = None
    decided_at: datetime | None = None
    version: int = 1

    def _require_pending(self) -> None:
        if self.status is not ApprovalStatus.PENDING:
            raise ConflictError(
                "approval request already decided",
                details={"request_id": str(self.id), "status": self.status.value},
            )

    def decide(
        self,
        *,
        decision_id: uuid.UUID,
        decided_by: UserId,
        outcome: DecisionOutcome,
        decided_at: datetime,
        comment: str = "",
        channel: str = "web",
    ) -> ApprovalDecision:
        """Apply a human decision; returns the immutable decision record."""
        self._require_pending()
        self.status = (
            ApprovalStatus.APPROVED
            if outcome is DecisionOutcome.APPROVED
            else ApprovalStatus.REJECTED
        )
        self.decided_at = decided_at
        self.version += 1
        return ApprovalDecision(
            id=decision_id,
            request_id=self.id,
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            decided_by=decided_by,
            outcome=outcome,
            comment=comment,
            decided_at=decided_at,
            channel=channel,
        )

    def cancel(self) -> None:
        self._require_pending()
        self.status = ApprovalStatus.CANCELLED
        self.version += 1
