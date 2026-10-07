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

A follow-up is about a PO case or, from stage-1 ticket 06, a product-development
case (`FollowUpSubject`). A product case has no supplier updates, so only its
SLA breaches become follow-ups. The subject carries the case's PIC, whom the
routing policy may name as a recipient beside the scopes (`pic`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from dw_supply_chain.domain.case_document import CaseKind
from dw_supply_chain.domain.missing_update import MissingUpdateStatus
from dw_supply_chain.domain.po_case import POCase, reference_label
from dw_supply_chain.domain.portfolio import CaseHealth
from dw_supply_chain.domain.product_development_case import ProductDevelopmentCase
from dw_supply_chain.domain.sla_evaluation import SLAEvaluation, SLAEvaluationStatus

# What `platform.notifications` holds; its CHECKs refuse longer, so a drift
# between the two is a loud refusal, never a silent cut elsewhere.
_TITLE_LIMIT = 200
_BODY_LIMIT = 2000

_MILESTONE_LABELS = {
    "sample_collection": "lấy mẫu",
    "sample_testing": "test mẫu",
    "bod_review": "BGĐ duyệt mẫu",
    "bm04": "BM04",
    "supplier_confirmation": "thống nhất với NCC",
    "item_coding": "tạo mã hàng",
    "signoff": "trình ký",
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
class FollowUpSubject:
    """The case a follow-up is about: which kind, which case, its workspace
    (the follow-up's own), and its PIC as stamped on the case now."""

    case_kind: CaseKind
    case_id: uuid.UUID
    workspace_id: uuid.UUID
    # None for a PO case opened before PICs existed (ticket 05).
    pic_user_id: uuid.UUID | None

    @classmethod
    def of_po_case(cls, case: POCase) -> FollowUpSubject:
        return cls(
            case_kind=CaseKind.PO,
            case_id=case.id.value,
            workspace_id=case.workspace_id.value,
            pic_user_id=case.pic_user_id,
        )

    @classmethod
    def of_product_case(cls, case: ProductDevelopmentCase) -> FollowUpSubject:
        return cls(
            case_kind=CaseKind.PRODUCT,
            case_id=case.id.value,
            workspace_id=case.workspace_id.value,
            pic_user_id=case.pic_user_id,
        )


FollowUpKey = tuple[CaseKind, str, FollowUpKind, str]


def follow_up_key(
    case_kind: CaseKind, case_id: uuid.UUID, kind: FollowUpKind, episode: str
) -> FollowUpKey:
    """One episode of one signal on one case: what opens once."""
    return (case_kind, str(case_id), kind, episode)


@dataclass(frozen=True, slots=True)
class FollowUpDue:
    """One signal on one case that a person should act on, now."""

    subject: FollowUpSubject
    kind: FollowUpKind
    episode: str
    days: int
    limit_days: int | None = None
    milestone: str | None = None

    @property
    def key(self) -> FollowUpKey:
        return follow_up_key(self.subject.case_kind, self.subject.case_id, self.kind, self.episode)


def _sla_breach_due(subject: FollowUpSubject, sla: SLAEvaluation) -> list[FollowUpDue]:
    if sla.status is not SLAEvaluationStatus.BREACHED or sla.milestone is None:
        return []
    return [
        FollowUpDue(
            subject=subject,
            kind=FollowUpKind.SLA_BREACH,
            episode=f"{sla.milestone}@{sla.entered_current_state_at.isoformat()}",
            days=sla.age_days,
            limit_days=sla.threshold_days,
            milestone=sla.milestone,
        )
    ]


def follow_ups_due(health: CaseHealth) -> list[FollowUpDue]:
    """What this PO case's signals ask of a person right now.

    An escalation replaces the reminder of the same silence rather than
    joining it: once the silence is long enough to escalate, "remind the
    supplier" is no longer the work."""
    subject = FollowUpSubject.of_po_case(health.case)
    due: list[FollowUpDue] = []
    silence = health.missing_update
    kind = {
        MissingUpdateStatus.REMINDER_DUE: FollowUpKind.UPDATE_REMINDER,
        MissingUpdateStatus.ESCALATION_DUE: FollowUpKind.UPDATE_ESCALATION,
    }.get(silence.status)
    if kind is not None:
        due.append(
            FollowUpDue(
                subject=subject,
                kind=kind,
                episode=silence.reference_at.isoformat(),
                days=silence.age_days,
            )
        )
    due.extend(_sla_breach_due(subject, health.sla))
    return due


def product_follow_ups_due(case: ProductDevelopmentCase, sla: SLAEvaluation) -> list[FollowUpDue]:
    """What a product-development case asks of a person: its SLA breach, if
    any. Stage 1 has no supplier updates, so no reminder or escalation."""
    return _sla_breach_due(FollowUpSubject.of_product_case(case), sla)


@dataclass(frozen=True, slots=True)
class FollowUpMessage:
    title: str
    body: str


def follow_up_message(
    *,
    kind: FollowUpKind,
    case_kind: CaseKind,
    reference: str | None,
    supplier_name: str | None,
    days: int,
    limit_days: int | None,
    milestone: str | None,
) -> FollowUpMessage:
    """What the people who must act are told. The title names the case by its
    identifier only (the PO reference, a product case's proposal code), since
    the title is what leaves for a linked chat (Z2, QE-20); the supplier's name
    stays in the in-app body. Both are the case's own stored text, shown as
    text."""
    named = (
        reference_label(reference) if case_kind is CaseKind.PO else f"hồ sơ phát triển {reference}"
    )
    if kind is FollowUpKind.UPDATE_REMINDER:
        title = f"Nhắc NCC cập nhật: {named}"
        body = f"{supplier_name} chưa gửi cập nhật {days} ngày."
    elif kind is FollowUpKind.UPDATE_ESCALATION:
        title = f"Leo thang: {named} không có cập nhật {days} ngày"
        body = f"{supplier_name} im lặng quá hạn leo thang. Cần người phụ trách xử lý."
    else:
        label = _MILESTONE_LABELS.get(milestone or "", milestone or "")
        title = f"Trễ SLA {label}: {named}"
        lead = f"{supplier_name}: " if supplier_name else ""
        body = f"{lead}{days} ngày ở bước này, hạn {limit_days} ngày."
    return FollowUpMessage(title=title[:_TITLE_LIMIT], body=body[:_BODY_LIMIT])
