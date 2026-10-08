"""Audit event: append-only fact record.

Immutability is enforced twice: frozen dataclass here, and the runtime DB role
has no UPDATE/DELETE grant on the audit table.

**A background lane is its own actor.** `actor_id` is NOT NULL and a lane has
no person behind it, so it is a fixed id derived from the lane's registry name
(`system_actor`), and `details.actor` reads `system:<lane>`. Never a human's id:
the log would say a person did what a sweep did. `lane_audit_event` is the one
way a lane builds its event (ADR 0011).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from dw_kernel.ids import TenantId, UserId, WorkspaceId


@dataclass(frozen=True, slots=True)
class AuditEvent:
    id: uuid.UUID
    tenant_id: TenantId
    workspace_id: WorkspaceId
    actor_id: UserId
    action: str
    resource_type: str
    resource_id: str
    occurred_at: datetime
    run_id: uuid.UUID | None = None
    policy_decision: str | None = None
    trace_id: str | None = None
    details: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.action.strip():
            raise ValueError("audit action must not be blank")
        if self.occurred_at.tzinfo is None:
            raise ValueError("audit occurred_at must be timezone-aware")


# Fixed forever: an auditor filters on the ids derived from it.
SYSTEM_ACTOR_NAMESPACE = uuid.UUID("6f1d3c2a-8e4b-5c7d-9a10-2b3c4d5e6f70")
_LANE_NAME = re.compile(r"[a-z][a-z0-9_]*")


def system_actor_label(lane: str) -> str:
    """How the lane reads in `details.actor`: `system:<lane>`."""
    if not _LANE_NAME.fullmatch(lane):
        raise ValueError(f"lane name must be a worker registry name, got {lane!r}")
    return f"system:{lane}"


def system_actor(lane: str) -> UserId:
    """The actor id of a background lane, by its worker registry name.

    A name-based UUID (version 5): the same lane is the same actor on every
    host and every release, and it can never equal a user's id, which is
    minted at random (version 4).
    """
    return UserId(uuid.uuid5(SYSTEM_ACTOR_NAMESPACE, system_actor_label(lane)))


def lane_audit_event(
    *,
    lane: str,
    event_id: uuid.UUID,
    tenant_id: TenantId,
    workspace_id: WorkspaceId,
    action: str,
    resource_type: str,
    resource_id: str,
    occurred_at: datetime,
    details: dict[str, object] | None = None,
    run_id: uuid.UUID | None = None,
) -> AuditEvent:
    """An audit event written by a background lane, never in a person's name."""
    return AuditEvent(
        id=event_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_id=system_actor(lane),
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        occurred_at=occurred_at,
        run_id=run_id,
        # Last, so a caller cannot label the lane as somebody else.
        details={**(details or {}), "actor": system_actor_label(lane)},
    )
