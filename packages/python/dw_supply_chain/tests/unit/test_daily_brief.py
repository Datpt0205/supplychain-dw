"""Unit: `domain.daily_brief` — which case lands in which group, in what
order, and what the brief claims when it could not look. Pure functions, so
every signal is built directly rather than computed from a clock."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from dw_kernel.ids import TenantId, WorkspaceId
from dw_supply_chain.domain.daily_brief import (
    BriefSignal,
    PendingApprovalsSeen,
    PendingCaseApproval,
    RecentChange,
    compose_brief,
)
from dw_supply_chain.domain.missing_update import MissingUpdateAssessment, MissingUpdateStatus
from dw_supply_chain.domain.po_case import CaseState, CaseTransition, POCase, POCaseId
from dw_supply_chain.domain.portfolio import CaseHealth
from dw_supply_chain.domain.sla_evaluation import SLAEvaluation, SLAEvaluationStatus
from dw_supply_chain.domain.supplier_update import (
    SupplierEventType,
    SupplierUpdate,
    SupplierUpdateExtraction,
    SupplierUpdateId,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 28, 8, tzinfo=UTC)
_ORDER = tuple(BriefSignal)


def _case(*, state: CaseState = CaseState.PRODUCTION, po_reference: str = "PO-1") -> POCase:
    return POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(uuid.uuid4()),
        workspace_id=WorkspaceId(uuid.uuid4()),
        po_reference=po_reference,
        supplier_name="Elmich Co.",
        state=state,
        created_at=_NOW - timedelta(days=30),
    )


def _update(
    case: POCase, *, delay_days: int | None = 7, requires_confirmation: bool = False
) -> SupplierUpdate:
    return SupplierUpdate(
        id=SupplierUpdateId(uuid.uuid4()),
        tenant_id=case.tenant_id,
        workspace_id=case.workspace_id,
        po_case_id=case.id,
        raw_text="we will be delayed",
        extraction=SupplierUpdateExtraction(
            event_type=SupplierEventType.PRODUCTION_DELAY,
            delay_days=delay_days,
            reason="component shortage",
            proposed_action="wait",
            confidence=0.9,
            source_ref="delayed",
        ),
        requires_confirmation=requires_confirmation,
        created_at=_NOW - timedelta(days=1),
    )


def _health(
    case: POCase | None = None,
    *,
    sla_status: SLAEvaluationStatus = SLAEvaluationStatus.NOT_APPLICABLE,
    milestone: str | None = None,
    in_state_days: int = 1,
    threshold_days: int | None = None,
    update_status: MissingUpdateStatus = MissingUpdateStatus.ON_TRACK,
    silence_days: int = 1,
    latest_update: SupplierUpdate | None = None,
) -> CaseHealth:
    return CaseHealth(
        case=case or _case(),
        sla=SLAEvaluation(
            status=sla_status,
            milestone=milestone,
            entered_current_state_at=_NOW - timedelta(days=in_state_days),
            age_days=in_state_days,
            threshold_days=threshold_days,
        ),
        missing_update=MissingUpdateAssessment(
            status=update_status, reference_at=_NOW, age_days=silence_days
        ),
        latest_update=latest_update,
    )


def _brief(
    healths: list[CaseHealth],
    *,
    recent: list[RecentChange] | None = None,
    approvals: PendingApprovalsSeen | None = None,
    order: tuple[BriefSignal, ...] = _ORDER,
) -> object:
    return compose_brief(
        healths,
        recent_changes=recent or [],
        approvals=approvals,
        signal_order=order,
        now=_NOW,
    )


def _keys(brief: object) -> list[str]:
    return [group.key for group in brief.groups]  # type: ignore[attr-defined]


# -- which group a case lands in ---------------------------------------------


def test_a_quiet_case_is_escalation_or_reminder_never_both() -> None:
    escalate = _health(update_status=MissingUpdateStatus.ESCALATION_DUE, silence_days=14)
    remind = _health(update_status=MissingUpdateStatus.REMINDER_DUE, silence_days=6)
    brief = _brief([escalate, remind])

    assert _keys(brief) == ["update_escalation_due", "update_reminder_due"]
    escalation = brief.group("update_escalation_due")  # type: ignore[attr-defined]
    assert [e.case for e in escalation.entries] == [escalate.case]
    assert escalation.entries[0].days == 14


def test_an_sla_breach_is_grouped_by_its_milestone_with_the_limit_it_overran() -> None:
    deposit_a = _health(
        sla_status=SLAEvaluationStatus.BREACHED,
        milestone="deposit",
        in_state_days=12,
        threshold_days=10,
    )
    deposit_b = _health(
        sla_status=SLAEvaluationStatus.BREACHED,
        milestone="deposit",
        in_state_days=15,
        threshold_days=10,
    )
    payment = _health(
        sla_status=SLAEvaluationStatus.BREACHED,
        milestone="payment",
        in_state_days=11,
        threshold_days=10,
    )
    brief = _brief([deposit_a, payment, deposit_b])

    # Same signal: the larger group reads first.
    assert _keys(brief) == ["sla_breached:deposit", "sla_breached:payment"]
    deposit = brief.group("sla_breached:deposit")  # type: ignore[attr-defined]
    assert deposit.total == 2
    assert deposit.state is None  # a milestone, not a state, defines it
    assert [(e.days, e.limit_days) for e in deposit.entries] == [(15, 10), (12, 10)]


@pytest.mark.parametrize(
    ("status", "grouped"),
    [
        (SLAEvaluationStatus.ON_TRACK, False),
        (SLAEvaluationStatus.NOT_APPLICABLE, False),
        # A milestone still pending business confirmation is never alerted on.
        (SLAEvaluationStatus.NOT_EVALUABLE, False),
        (SLAEvaluationStatus.BREACHED, True),
    ],
)
def test_only_a_breached_sla_makes_a_group(status: SLAEvaluationStatus, grouped: bool) -> None:
    brief = _brief([_health(sla_status=status, milestone="deposit", threshold_days=10)])
    assert (brief.group("sla_breached:deposit") is not None) is grouped  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("state", "key"),
    [
        (CaseState.BLOCKED, "case_blocked"),
        (CaseState.MANUAL_REVIEW, "manual_review"),
        (CaseState.WAITING_EXTERNAL, "waiting_external"),
        (CaseState.REWORK, "rework"),
        (CaseState.WAITING_DEPOSIT, "waiting_on_us:waiting_deposit"),
        (CaseState.WAITING_PAYMENT, "waiting_on_us:waiting_payment"),
    ],
)
def test_a_state_that_needs_someone_lands_in_its_own_group(state: CaseState, key: str) -> None:
    health = _health(_case(state=state), in_state_days=9)
    brief = _brief([health])
    group = brief.group(key)  # type: ignore[attr-defined]
    assert group is not None
    assert group.entries[0].days == 9  # days in that state
    # The state that defines the group, so a reader can open that list.
    assert group.state is state


def test_a_case_moving_normally_makes_no_group() -> None:
    assert _keys(_brief([_health(_case(state=CaseState.PRODUCTION))])) == []


# -- a reported delay: the supplier's latest official word ---------------------


def test_the_latest_official_update_reporting_a_delay_is_a_signal() -> None:
    case = _case()
    brief = _brief([_health(case, latest_update=_update(case, delay_days=7))])
    group = brief.group("supplier_reported_delay")  # type: ignore[attr-defined]
    assert group is not None
    assert group.entries[0].days == 7


@pytest.mark.parametrize(
    ("delay_days", "requires_confirmation"),
    [
        # Not confirmed: low confidence or a source not found in the message.
        (7, True),
        # The latest word reports no delay, whatever an earlier one said.
        (None, False),
        (0, False),
    ],
)
def test_an_unconfirmed_or_delay_free_latest_update_reports_nothing(
    delay_days: int | None, requires_confirmation: bool
) -> None:
    case = _case()
    update = _update(case, delay_days=delay_days, requires_confirmation=requires_confirmation)
    health = _health(case, latest_update=update)
    assert health.reported_delay_days is None
    assert _brief([health]).group("supplier_reported_delay") is None  # type: ignore[attr-defined]


# -- approvals: shown, absent, or not looked at --------------------------------


def test_approvals_the_caller_may_not_read_are_not_looked_at_and_the_brief_says_so() -> None:
    brief = _brief([], approvals=None)
    assert brief.approvals_visible is False  # type: ignore[attr-defined]
    assert _keys(brief) == []


def test_no_pending_approval_is_no_group_but_the_brief_did_look() -> None:
    brief = _brief([], approvals=PendingApprovalsSeen(total=0, newest=()))
    assert brief.approvals_visible is True  # type: ignore[attr-defined]
    assert _keys(brief) == []


def test_the_approval_count_is_every_pending_one_not_just_those_read() -> None:
    case = _case()
    approval = PendingCaseApproval(
        case=case, action="cancel", requested_at=_NOW - timedelta(days=3)
    )
    brief = _brief([], approvals=PendingApprovalsSeen(total=7, newest=(approval,)))
    group = brief.group("approval_pending")  # type: ignore[attr-defined]
    assert group.total == 7
    assert [(e.case, e.days, e.approval_action) for e in group.entries] == [(case, 3, "cancel")]


# -- recent changes: news, not a task ----------------------------------------


def _change(case: POCase, hours_ago: int) -> RecentChange:
    return RecentChange(
        case=case,
        transition=CaseTransition(
            from_state=CaseState.PRODUCTION,
            to_state=CaseState.QC,
            reason=None,
            occurred_at=_NOW - timedelta(hours=hours_ago),
        ),
    )


def test_recent_changes_read_newest_first_and_are_not_counted_as_flagged() -> None:
    older, newer = _case(po_reference="PO-A"), _case(po_reference="PO-B")
    brief = _brief([], recent=[_change(older, 20), _change(newer, 2)])
    group = brief.group("changed_recently")  # type: ignore[attr-defined]
    assert [e.case.po_reference for e in group.entries] == ["PO-B", "PO-A"]
    assert brief.flagged_case_count == 0  # type: ignore[attr-defined]


# -- order and counts --------------------------------------------------------


def test_groups_follow_the_policy_order_whatever_it_is() -> None:
    blocked = _health(_case(state=CaseState.BLOCKED))
    escalate = _health(update_status=MissingUpdateStatus.ESCALATION_DUE)
    healths = [blocked, escalate]

    default = _brief(healths)
    reversed_order = _brief(healths, order=tuple(reversed(_ORDER)))

    assert _keys(default) == ["update_escalation_due", "case_blocked"]
    assert _keys(reversed_order) == ["case_blocked", "update_escalation_due"]


def test_a_signal_the_order_does_not_name_still_reads_last_rather_than_vanish() -> None:
    blocked = _health(_case(state=CaseState.BLOCKED))
    escalate = _health(update_status=MissingUpdateStatus.ESCALATION_DUE)
    order = tuple(s for s in _ORDER if s is not BriefSignal.UPDATE_ESCALATION_DUE)
    assert _keys(_brief([blocked, escalate], order=order)) == [
        "case_blocked",
        "update_escalation_due",
    ]


def test_a_case_in_two_groups_is_flagged_once() -> None:
    case = _case(state=CaseState.BLOCKED)
    health = _health(case, update_status=MissingUpdateStatus.ESCALATION_DUE)
    brief = _brief([health, _health()])
    assert _keys(brief) == ["update_escalation_due", "case_blocked"]
    assert brief.flagged_case_count == 1  # type: ignore[attr-defined]
    assert brief.active_case_count == 2  # type: ignore[attr-defined]


def test_entries_read_longest_standing_first_with_a_stable_tiebreak() -> None:
    a = _health(_case(state=CaseState.BLOCKED, po_reference="PO-B"), in_state_days=3)
    b = _health(_case(state=CaseState.BLOCKED, po_reference="PO-A"), in_state_days=3)
    c = _health(_case(state=CaseState.BLOCKED, po_reference="PO-C"), in_state_days=9)
    group = _brief([a, b, c]).group("case_blocked")  # type: ignore[attr-defined]
    assert [e.case.po_reference for e in group.entries] == ["PO-C", "PO-A", "PO-B"]
