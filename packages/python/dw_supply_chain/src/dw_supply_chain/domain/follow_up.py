"""Follow-ups: the work a deterministic signal hands to a person.

A reminder, an escalation or an SLA breach is already computed for every
active case (`portfolio.CaseHealth`, the one assessment the Attention Queue,
the Control Tower and the daily brief share). This module decides which of
those become a follow-up and how one occurrence is told from the next. Nothing
here is a model's judgment, and nothing is invented: a follow-up exists only
while its signal does.

An occurrence is an *episode*. A supplier's silence is one episode from its
reference point (the last update, or the case's creation) until the next
update. An SLA breach is one episode per milestone and entry into its state.
The same episode never opens twice; a new one opens again, since a supplier who
wrote and then went quiet again is a new chase.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from dw_supply_chain.domain.missing_update import MissingUpdateStatus
from dw_supply_chain.domain.po_case import POCase, reference_label
from dw_supply_chain.domain.portfolio import CaseHealth

# What `platform.notifications` holds; its CHECKs refuse longer, so a drift
# between the two is a loud refusal, never a silent cut elsewhere.
_TITLE_LIMIT = 200
_BODY_LIMIT = 2000

_MILESTONE_LABELS = {
    "deposit": "đặt cọc",
    "port_arrival": "về cảng",
    "payment": "thanh toán",
    "warehouse_receipt": "nhập kho",
}


class FollowUpKind(StrEnum):
    UPDATE_REMINDER = "update_reminder"
    UPDATE_ESCALATION = "update_escalation"
    SLA_BREACH = "sla_breach"


class FollowUpStatus(StrEnum):
    OPEN = "open"
    # A person said it is handled.
    DONE = "done"
    # The sweep found the signal gone (an update arrived, the case moved on).
    RESOLVED = "resolved"


@dataclass(frozen=True, slots=True)
class FollowUpDue:
    """One signal on one case that a person should act on, now."""

    case: POCase
    kind: FollowUpKind
    episode: str
    days: int
    limit_days: int | None = None
    milestone: str | None = None

    @property
    def key(self) -> tuple[str, FollowUpKind, str]:
        return (str(self.case.id.value), self.kind, self.episode)


def follow_ups_due(health: CaseHealth) -> list[FollowUpDue]:
    """What this case's signals ask of a person right now.

    An escalation replaces the reminder of the same silence rather than
    joining it: once the silence is long enough to escalate, "remind the
    supplier" is no longer the work."""
    due: list[FollowUpDue] = []
    silence = health.missing_update
    kind = {
        MissingUpdateStatus.REMINDER_DUE: FollowUpKind.UPDATE_REMINDER,
        MissingUpdateStatus.ESCALATION_DUE: FollowUpKind.UPDATE_ESCALATION,
    }.get(silence.status)
    if kind is not None:
        due.append(
            FollowUpDue(
                case=health.case,
                kind=kind,
                episode=silence.reference_at.isoformat(),
                days=silence.age_days,
            )
        )
    if health.sla_breached and health.sla.milestone is not None:
        due.append(
            FollowUpDue(
                case=health.case,
                kind=FollowUpKind.SLA_BREACH,
                episode=f"{health.sla.milestone}@{health.sla.entered_current_state_at.isoformat()}",
                days=health.sla.age_days,
                limit_days=health.sla.threshold_days,
                milestone=health.sla.milestone,
            )
        )
    return due


@dataclass(frozen=True, slots=True)
class FollowUpMessage:
    title: str
    body: str


def follow_up_message(
    *,
    kind: FollowUpKind,
    po_reference: str | None,
    supplier_name: str,
    days: int,
    limit_days: int | None,
    milestone: str | None,
) -> FollowUpMessage:
    """What the people who must act are told. The PO reference and supplier
    name are the case's own stored text, shown as text."""
    po_reference = reference_label(po_reference)
    if kind is FollowUpKind.UPDATE_REMINDER:
        title = f"Nhắc NCC cập nhật: {po_reference}"
        body = f"{supplier_name} chưa gửi cập nhật {days} ngày."
    elif kind is FollowUpKind.UPDATE_ESCALATION:
        title = f"Leo thang: {po_reference} không có cập nhật {days} ngày"
        body = f"{supplier_name} im lặng quá hạn leo thang. Cần người phụ trách xử lý."
    else:
        label = _MILESTONE_LABELS.get(milestone or "", milestone or "")
        title = f"Trễ SLA {label}: {po_reference}"
        body = f"{supplier_name}: {days} ngày ở bước này, hạn {limit_days} ngày."
    return FollowUpMessage(title=title[:_TITLE_LIMIT], body=body[:_BODY_LIMIT])
