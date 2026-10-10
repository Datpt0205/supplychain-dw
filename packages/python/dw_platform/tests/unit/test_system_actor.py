"""Who a background lane is, on the audit log.

`audit_events.actor_id` is NOT NULL, and a lane has no person behind it. Two
lanes filled it with a human id instead (the delivery's recipient; nobody, for
the retention sweeps, which wrote no row at all), so the log said a person did
what a sweep did. A lane's actor is a fixed id derived from its name.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from dw_kernel.ids import TenantId, WorkspaceId
from dw_platform.domain.audit import (
    SYSTEM_ACTOR_NAMESPACE,
    lane_audit_event,
    system_actor,
    system_actor_label,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 8, tzinfo=UTC)


def test_a_lane_is_always_the_same_actor_and_no_other_lane_is() -> None:
    assert system_actor("retention") == system_actor("retention")
    assert system_actor("retention") != system_actor("retention_knowledge")
    # Pinned: the id is what an auditor filters on, so it never changes.
    assert system_actor("retention").value == uuid.uuid5(SYSTEM_ACTOR_NAMESPACE, "system:retention")


def test_a_lane_actor_can_never_be_a_users_id() -> None:
    # Users are minted by uuid4; a name-based uuid5 cannot equal one.
    assert system_actor("channel_delivery").value.version == 5


@pytest.mark.parametrize("lane", ["", "Retention", "system:retention", "a b", "x-y"])
def test_a_lane_name_is_a_registry_name(lane: str) -> None:
    with pytest.raises(ValueError, match="lane"):
        system_actor(lane)


def test_a_lane_event_names_the_lane_and_cannot_be_relabelled() -> None:
    event = lane_audit_event(
        lane="retention",
        event_id=uuid.uuid4(),
        tenant_id=TenantId(uuid.uuid4()),
        workspace_id=WorkspaceId(uuid.uuid4()),
        action="memory.item.expired",
        resource_type="memory_item",
        resource_id="m-1",
        occurred_at=NOW,
        details={"actor": "a person", "class": "default"},
    )

    assert event.actor_id == system_actor("retention")
    assert event.details == {"class": "default", "actor": system_actor_label("retention")}
    assert system_actor_label("retention") == "system:retention"
