"""Missing Update Detection — a staleness check, no model call.

The strategy doc's own framing applies most literally here: "Workflow engine
quyết định state; AI hỗ trợ exception/reasoning." Whether a case has gone
too long without a supplier update is a threshold comparison, not a
judgment — there is nothing for a model to reason about.

The thresholds are the tenant's, not this module's: `UpdateCadence` comes
from the SLA policy's `supplier_update` block (the doc names the shape of
the rule, "configured_threshold", and no number). The platform default is
a short test value Đạt asked for (SLA policy 1.2.0).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from dw_supply_chain.domain.po_case import TERMINAL_STATES, CaseState


@dataclass(frozen=True, slots=True)
class UpdateCadence:
    """How many days a case may go without a supplier update before a
    reminder is due, and before an escalation is."""

    reminder_after_days: int
    escalation_after_days: int


class MissingUpdateStatus(StrEnum):
    ON_TRACK = "on_track"
    REMINDER_DUE = "reminder_due"
    ESCALATION_DUE = "escalation_due"


@dataclass(frozen=True, slots=True)
class MissingUpdateAssessment:
    """`status` plus the numbers a UI needs to explain it ("12 ngày im lặng"),
    not just the enum — the reference point and its age are always reported,
    even for a terminal case, since "last heard from 45 days ago" is still a
    fact worth showing on a closed case, only the status is pinned ON_TRACK.
    """

    status: MissingUpdateStatus
    reference_at: datetime
    age_days: int


def missing_update_status(
    *,
    state: CaseState,
    case_created_at: datetime,
    last_supplier_update_at: datetime | None,
    now: datetime,
    cadence: UpdateCadence,
) -> MissingUpdateAssessment:
    """Whether this case has gone quiet for long enough to chase.

    A terminal case (`COMPLETED`/`CANCELLED`) is always `ON_TRACK` — there is
    nothing left to update it about, so silence is not staleness. Every
    other state, including the interrupt states, is checked: `BLOCKED`/
    `WAITING_EXTERNAL`/`MANUAL_REVIEW` are exactly the states where "has
    anyone said anything since" matters most, not less.

    The reference point is the most recent supplier update, or the case's
    own creation time if none has ever arrived — a case with zero updates is
    not automatically safe, it is the most overdue kind.
    """
    reference = last_supplier_update_at or case_created_at
    age_days = (now - reference).days

    # Awaiting its PO, no supplier holds an order yet: nobody to chase
    # (ticket 05). Its own group in the brief says what is due instead.
    if state in TERMINAL_STATES or state is CaseState.ORDER_REQUESTED:
        status = MissingUpdateStatus.ON_TRACK
    elif age_days >= cadence.escalation_after_days:
        status = MissingUpdateStatus.ESCALATION_DUE
    elif age_days >= cadence.reminder_after_days:
        status = MissingUpdateStatus.REMINDER_DUE
    else:
        status = MissingUpdateStatus.ON_TRACK

    return MissingUpdateAssessment(status=status, reference_at=reference, age_days=age_days)
