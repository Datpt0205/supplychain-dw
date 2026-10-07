"""Unit: every reader of a PO case's state or reference, given a case awaiting
its PO (`order_requested`, no reference yet; stage-1 ticket 05, ADR 0017)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from dw_kernel.ids import TenantId, WorkspaceId
from dw_supply_chain.domain.brief_summary import BriefSummaryDraft, ground_summary
from dw_supply_chain.domain.daily_brief import BriefSignal, compose_brief
from dw_supply_chain.domain.delay_impact import impacted_milestones
from dw_supply_chain.domain.follow_up import FollowUpKind, follow_up_message
from dw_supply_chain.domain.missing_update import (
    MissingUpdateStatus,
    UpdateCadence,
    missing_update_status,
)
from dw_supply_chain.domain.po_case import (
    CaseState,
    POCase,
    POCaseId,
    POCaseLine,
    reference_label,
)
from dw_supply_chain.domain.portfolio import CaseHealth
from dw_supply_chain.domain.sla_evaluation import SLAEvaluationStatus, evaluate_sla
from dw_supply_chain.sla_policy import load_supply_chain_sla_policy

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 7, 8, tzinfo=UTC)
_SLA = load_supply_chain_sla_policy(
    Path(__file__).resolve().parents[5] / "configs" / "policies" / "supply_chain_sla@1.2.0.yaml"
)


def _awaiting(*, days: int = 30) -> POCase:
    case = POCase.requested(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(uuid.uuid4()),
        workspace_id=WorkspaceId(uuid.uuid4()),
        supplier_name="NCC Minh Long",
        product_dev_case_id=uuid.uuid4(),
        pic_user_id=uuid.uuid4(),
        category="Nồi",
        lines=(POCaseLine(sku_id=uuid.uuid4(), quantity=10),),
    )
    case.created_at = _NOW - timedelta(days=days)
    return case


def _health(case: POCase) -> CaseHealth:
    assert case.created_at is not None
    return CaseHealth(
        case=case,
        sla=evaluate_sla(
            state=case.state, entered_current_state_at=case.created_at, now=_NOW, policy=_SLA
        ),
        missing_update=missing_update_status(
            state=case.state,
            case_created_at=case.created_at,
            last_supplier_update_at=None,
            now=_NOW,
            cadence=UpdateCadence(reminder_after_days=1, escalation_after_days=2),
        ),
        latest_update=None,
    )


def test_a_case_awaiting_its_po_is_never_due_a_supplier_reminder() -> None:
    """No PO yet, so no supplier holds an order to chase, however long."""
    assessment = missing_update_status(
        state=CaseState.ORDER_REQUESTED,
        case_created_at=_NOW - timedelta(days=60),
        last_supplier_update_at=None,
        now=_NOW,
        cadence=UpdateCadence(reminder_after_days=1, escalation_after_days=2),
    )
    assert assessment.status is MissingUpdateStatus.ON_TRACK


def test_a_case_awaiting_its_po_has_no_sla_yet() -> None:
    """Its own milestone waits for Elmich (QE-01)."""
    evaluation = evaluate_sla(
        state=CaseState.ORDER_REQUESTED,
        entered_current_state_at=_NOW - timedelta(days=60),
        now=_NOW,
        policy=_SLA,
    )
    assert evaluation.status is SLAEvaluationStatus.NOT_APPLICABLE


def test_the_brief_groups_cases_awaiting_their_po_as_waiting_on_us_and_nowhere_else() -> None:
    older, newer = _awaiting(days=9), _awaiting(days=2)
    brief = compose_brief(
        [_health(newer), _health(older)],
        recent_changes=[],
        approvals=None,
        signal_order=tuple(BriefSignal),
        now=_NOW,
    )

    (group,) = brief.groups
    assert (group.key, group.state, group.total) == (
        "waiting_on_us:order_requested",
        CaseState.ORDER_REQUESTED,
        2,
    )
    # Longest-standing first; a case without a reference sorts fine.
    assert [entry.case.id for entry in group.entries] == [older.id, newer.id]
    assert [entry.days for entry in group.entries] == [9, 2]
    assert _health(older).needs_attention is False


def test_a_summary_of_a_group_without_references_is_grounded_on_its_figures() -> None:
    brief = compose_brief(
        [_health(_awaiting(days=4))],
        recent_changes=[],
        approvals=None,
        signal_order=tuple(BriefSignal),
        now=_NOW,
    )
    draft = BriefSummaryDraft.model_validate(
        {
            "sentences": [
                {
                    "text": "1 hồ sơ chờ tạo PO đã 4 ngày.",
                    "group_keys": ["waiting_on_us:order_requested"],
                },
                {"text": "PO-999 chờ 4 ngày.", "group_keys": ["waiting_on_us:order_requested"]},
            ]
        }
    )

    summary = ground_summary(draft, brief)

    assert [s.text for s in summary.sentences] == ["1 hồ sơ chờ tạo PO đã 4 ngày."]
    assert summary.dropped == 1


def test_a_case_awaiting_its_po_has_no_milestone_ahead_to_delay() -> None:
    assert impacted_milestones(_awaiting()) == []


def test_a_message_about_a_case_without_a_reference_says_so_and_invents_none() -> None:
    message = follow_up_message(
        kind=FollowUpKind.UPDATE_REMINDER,
        po_reference=None,
        supplier_name="NCC Minh Long",
        days=3,
        limit_days=None,
        milestone=None,
    )
    assert message.title == "Nhắc NCC cập nhật: Chưa có số PO"
    assert reference_label(None) == "Chưa có số PO"
    assert reference_label("PO-1") == "PO-1"
