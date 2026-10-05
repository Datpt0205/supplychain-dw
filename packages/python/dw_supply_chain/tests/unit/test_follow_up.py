"""Which signals become follow-ups, how an episode is told apart, what is said,
and the routing policy's own rules."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from dw_kernel.ids import TenantId, WorkspaceId
from dw_supply_chain.domain.follow_up import (
    FollowUpKind,
    follow_up_message,
    follow_ups_due,
)
from dw_supply_chain.domain.missing_update import MissingUpdateAssessment, MissingUpdateStatus
from dw_supply_chain.domain.po_case import CaseState, POCase, POCaseId
from dw_supply_chain.domain.portfolio import CaseHealth
from dw_supply_chain.domain.sla_evaluation import SLAEvaluation, SLAEvaluationStatus
from dw_supply_chain.follow_up_policy import (
    SupplyChainFollowUpPolicy,
    load_supply_chain_follow_up_policy,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[5]
POLICY_PATH = REPO_ROOT / "configs" / "policies" / "supply_chain_follow_ups@1.0.0.yaml"
_NOW = datetime(2026, 9, 28, tzinfo=UTC)
_CASE = POCase(
    id=POCaseId(uuid.uuid4()),
    tenant_id=TenantId(uuid.uuid4()),
    workspace_id=WorkspaceId(uuid.uuid4()),
    po_reference="PO-2026-007",
    supplier_name="Kangaroo",
    state=CaseState.WAITING_DEPOSIT,
    created_at=_NOW - timedelta(days=5),
)


def _health(
    *, silence: MissingUpdateStatus = MissingUpdateStatus.ON_TRACK, breached: bool = False
) -> CaseHealth:
    return CaseHealth(
        case=_CASE,
        sla=SLAEvaluation(
            status=SLAEvaluationStatus.BREACHED if breached else SLAEvaluationStatus.ON_TRACK,
            milestone="deposit",
            entered_current_state_at=_NOW - timedelta(days=3),
            age_days=3,
            threshold_days=1,
        ),
        missing_update=MissingUpdateAssessment(
            status=silence, reference_at=_NOW - timedelta(days=4), age_days=4
        ),
    )


def test_a_healthy_case_hands_nobody_anything() -> None:
    assert follow_ups_due(_health()) == []


@pytest.mark.parametrize(
    ("silence", "kind"),
    [
        (MissingUpdateStatus.REMINDER_DUE, FollowUpKind.UPDATE_REMINDER),
        (MissingUpdateStatus.ESCALATION_DUE, FollowUpKind.UPDATE_ESCALATION),
    ],
)
def test_one_silence_is_either_a_reminder_or_an_escalation_never_both(
    silence: MissingUpdateStatus, kind: FollowUpKind
) -> None:
    (due,) = follow_ups_due(_health(silence=silence))
    assert due.kind is kind
    # The episode is the silence's reference point: the same for both kinds,
    # so an escalation is recognisably the same silence the reminder was.
    assert due.episode == (_NOW - timedelta(days=4)).isoformat()
    assert due.days == 4


def test_a_breach_is_an_episode_of_its_milestone_and_its_entry_into_the_state() -> None:
    (due,) = follow_ups_due(_health(breached=True))
    assert due.kind is FollowUpKind.SLA_BREACH
    assert due.episode == f"deposit@{(_NOW - timedelta(days=3)).isoformat()}"
    assert (due.milestone, due.days, due.limit_days) == ("deposit", 3, 1)


def test_both_signals_on_one_case_are_two_follow_ups() -> None:
    kinds = {
        d.kind
        for d in follow_ups_due(_health(silence=MissingUpdateStatus.REMINDER_DUE, breached=True))
    }
    assert kinds == {FollowUpKind.UPDATE_REMINDER, FollowUpKind.SLA_BREACH}


def test_the_message_names_the_case_and_stays_within_what_the_inbox_holds() -> None:
    message = follow_up_message(
        kind=FollowUpKind.SLA_BREACH,
        po_reference="PO-" + "9" * 400,
        supplier_name="K" * 3000,
        days=3,
        limit_days=1,
        milestone="deposit",
    )
    assert message.title.startswith("Trễ SLA đặt cọc: PO-999")
    assert len(message.title) == 200
    assert len(message.body) == 2000


# ---- routing policy -----------------------------------------------------------


def test_the_shipped_routing_hands_reminders_to_the_coordinator_and_the_rest_to_the_owner_too() -> (
    None
):
    policy = load_supply_chain_follow_up_policy(POLICY_PATH)
    coordinator, owner = "supply_chain.supplier_update.write", "supply_chain.sla_policy.write"
    assert policy.recipient_scopes(FollowUpKind.UPDATE_REMINDER) == {coordinator}
    assert policy.recipient_scopes(FollowUpKind.UPDATE_ESCALATION) == {coordinator, owner}
    assert policy.recipient_scopes(FollowUpKind.SLA_BREACH) == {coordinator, owner}


def _routing(recipients: dict[str, list[str]]) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "policy_id": "supply_chain_follow_ups",
        "policy_version": "1.0.0",
        "recipients": recipients,
    }


@pytest.mark.parametrize(
    "recipients",
    [
        # A kind left out: an escalation nobody would see.
        {
            "update_reminder": ["supply_chain.x.write"],
            "update_escalation": ["supply_chain.x.write"],
        },
        # A kind routed to nobody.
        {
            "update_reminder": [],
            "update_escalation": ["supply_chain.x.write"],
            "sla_breach": ["supply_chain.x.write"],
        },
    ],
)
def test_every_kind_must_reach_someone(recipients: dict[str, list[str]]) -> None:
    with pytest.raises(ValidationError, match="at least one recipient"):
        SupplyChainFollowUpPolicy.model_validate(_routing(recipients))


def test_recipients_are_supply_chain_scopes_only() -> None:
    with pytest.raises(ValidationError, match="Supply Chain scopes"):
        SupplyChainFollowUpPolicy.model_validate(
            _routing(
                {
                    "update_reminder": ["platform.members.write"],
                    "update_escalation": ["supply_chain.x.write"],
                    "sla_breach": ["supply_chain.x.write"],
                }
            )
        )


def test_an_unknown_kind_is_refused() -> None:
    with pytest.raises(ValidationError):
        SupplyChainFollowUpPolicy.model_validate(
            _routing(
                {
                    "update_reminder": ["supply_chain.x.write"],
                    "update_escalation": ["supply_chain.x.write"],
                    "sla_breach": ["supply_chain.x.write"],
                    "whenever": ["supply_chain.x.write"],
                }
            )
        )
