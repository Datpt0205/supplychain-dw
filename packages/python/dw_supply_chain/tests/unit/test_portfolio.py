"""Unit: `domain.portfolio` — what counts as needing attention, and how the
Control Tower groups active cases. Pure functions, so every signal is built
directly rather than computed from a clock."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from dw_kernel.ids import TenantId, WorkspaceId
from dw_supply_chain.domain.missing_update import MissingUpdateAssessment, MissingUpdateStatus
from dw_supply_chain.domain.po_case import CaseState, POCase, POCaseId
from dw_supply_chain.domain.portfolio import CaseHealth, summarize_portfolio
from dw_supply_chain.domain.sla_evaluation import SLAEvaluation, SLAEvaluationStatus

pytestmark = pytest.mark.unit

_AT = datetime(2026, 9, 1, tzinfo=UTC)


def _health(
    *,
    state: CaseState = CaseState.PRODUCTION,
    supplier_name: str = "Elmich Co.",
    sla_status: SLAEvaluationStatus = SLAEvaluationStatus.NOT_APPLICABLE,
    in_state_days: int = 1,
    update_status: MissingUpdateStatus = MissingUpdateStatus.ON_TRACK,
    silence_days: int = 1,
) -> CaseHealth:
    # `state` is set directly rather than walked through the transition
    # methods: nothing here depends on how the case got there, only on
    # where it is — the handler tests cover real cases.
    case = POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(uuid.uuid4()),
        workspace_id=WorkspaceId(uuid.uuid4()),
        po_reference="PO-0001",
        supplier_name=supplier_name,
        state=state,
        created_at=_AT,
    )
    return CaseHealth(
        case=case,
        sla=SLAEvaluation(
            status=sla_status,
            milestone=None,
            entered_current_state_at=_AT,
            age_days=in_state_days,
            threshold_days=None,
        ),
        missing_update=MissingUpdateAssessment(
            status=update_status, reference_at=_AT, age_days=silence_days
        ),
    )


# -- CaseHealth: the one definition of "needs attention" ----------------------


@pytest.mark.parametrize(
    ("sla_status", "breached"),
    [
        (SLAEvaluationStatus.BREACHED, True),
        (SLAEvaluationStatus.ON_TRACK, False),
        (SLAEvaluationStatus.NOT_APPLICABLE, False),
        # Pending business confirmation is never alerted on as if real.
        (SLAEvaluationStatus.NOT_EVALUABLE, False),
    ],
)
def test_only_a_breached_sla_counts_as_breached(
    sla_status: SLAEvaluationStatus, breached: bool
) -> None:
    health = _health(sla_status=sla_status)
    assert health.sla_breached is breached
    assert health.needs_attention is breached


@pytest.mark.parametrize(
    ("update_status", "overdue", "escalation"),
    [
        (MissingUpdateStatus.ON_TRACK, False, False),
        (MissingUpdateStatus.REMINDER_DUE, True, False),
        (MissingUpdateStatus.ESCALATION_DUE, True, True),
    ],
)
def test_reminder_and_escalation_both_count_as_overdue_only_escalation_as_escalation(
    update_status: MissingUpdateStatus, overdue: bool, escalation: bool
) -> None:
    health = _health(update_status=update_status)
    assert health.update_overdue is overdue
    assert health.escalation_due is escalation
    assert health.needs_attention is overdue


# -- summarize_portfolio -------------------------------------------------------


def test_an_empty_portfolio_summarizes_to_zeros() -> None:
    summary = summarize_portfolio([])
    assert summary.active_case_count == 0
    assert summary.sla_breached_count == 0
    assert summary.update_overdue_count == 0
    assert summary.by_state == ()
    assert summary.by_supplier == ()


def test_totals_count_each_signal_once_per_case() -> None:
    summary = summarize_portfolio(
        [
            _health(
                sla_status=SLAEvaluationStatus.BREACHED,
                update_status=MissingUpdateStatus.ESCALATION_DUE,
            ),
            _health(sla_status=SLAEvaluationStatus.BREACHED),
            _health(update_status=MissingUpdateStatus.REMINDER_DUE),
            _health(),
        ]
    )
    assert summary.active_case_count == 4
    assert summary.sla_breached_count == 2
    assert summary.update_overdue_count == 2


def test_by_state_reads_in_lifecycle_order_whatever_order_cases_arrive_in() -> None:
    summary = summarize_portfolio(
        [
            _health(state=CaseState.BLOCKED),
            _health(state=CaseState.IN_TRANSIT),
            _health(state=CaseState.WAITING_DEPOSIT),
            _health(state=CaseState.IN_TRANSIT),
        ]
    )
    assert [row.state for row in summary.by_state] == [
        CaseState.WAITING_DEPOSIT,
        CaseState.IN_TRANSIT,
        # Exception states come after the whole happy path, as declared.
        CaseState.BLOCKED,
    ]


def test_by_state_counts_signals_and_reports_the_longest_wait() -> None:
    summary = summarize_portfolio(
        [
            _health(
                state=CaseState.WAITING_DEPOSIT,
                sla_status=SLAEvaluationStatus.BREACHED,
                in_state_days=14,
            ),
            _health(
                state=CaseState.WAITING_DEPOSIT,
                sla_status=SLAEvaluationStatus.ON_TRACK,
                update_status=MissingUpdateStatus.REMINDER_DUE,
                in_state_days=3,
            ),
            _health(state=CaseState.PRODUCTION, in_state_days=40),
        ]
    )
    deposit = summary.by_state[0]
    assert deposit.state is CaseState.WAITING_DEPOSIT
    assert deposit.case_count == 2
    assert deposit.sla_breached_count == 1
    assert deposit.update_overdue_count == 1
    assert deposit.oldest_in_state_days == 14
    # Another state's longer wait never leaks into this row.
    assert summary.by_state[1].oldest_in_state_days == 40


def test_by_supplier_puts_the_most_overdue_updates_first() -> None:
    summary = summarize_portfolio(
        [
            _health(supplier_name="Quiet Co.", update_status=MissingUpdateStatus.REMINDER_DUE),
            _health(supplier_name="Quiet Co.", update_status=MissingUpdateStatus.REMINDER_DUE),
            _health(supplier_name="Busy Co."),
            _health(supplier_name="Busy Co."),
            _health(supplier_name="Busy Co."),
            _health(supplier_name="Late Co.", update_status=MissingUpdateStatus.ESCALATION_DUE),
        ]
    )
    # More cases (Busy Co.) is not the question — more cases gone quiet is.
    assert [row.supplier_name for row in summary.by_supplier] == [
        "Quiet Co.",
        "Late Co.",
        "Busy Co.",
    ]


def test_by_supplier_breaks_an_overdue_tie_on_escalations_then_name() -> None:
    summary = summarize_portfolio(
        [
            _health(supplier_name="B Co.", update_status=MissingUpdateStatus.REMINDER_DUE),
            _health(supplier_name="A Co.", update_status=MissingUpdateStatus.REMINDER_DUE),
            _health(supplier_name="C Co.", update_status=MissingUpdateStatus.ESCALATION_DUE),
        ]
    )
    assert [row.supplier_name for row in summary.by_supplier] == ["C Co.", "A Co.", "B Co."]


def test_by_supplier_counts_every_signal_and_the_longest_silence() -> None:
    summary = summarize_portfolio(
        [
            _health(
                supplier_name="Elmich Co.",
                update_status=MissingUpdateStatus.ESCALATION_DUE,
                silence_days=12,
            ),
            _health(
                supplier_name="Elmich Co.",
                sla_status=SLAEvaluationStatus.BREACHED,
                update_status=MissingUpdateStatus.REMINDER_DUE,
                silence_days=6,
            ),
            _health(supplier_name="Elmich Co.", silence_days=2),
        ]
    )
    (row,) = summary.by_supplier
    assert row.case_count == 3
    assert row.update_overdue_count == 2
    assert row.escalation_due_count == 1
    assert row.sla_breached_count == 1
    assert row.longest_silence_days == 12


def test_two_spellings_of_a_supplier_stay_two_rows() -> None:
    """No supplier master record exists yet — merging near-identical names
    would be a guess about which suppliers are the same company."""
    summary = summarize_portfolio(
        [_health(supplier_name="Elmich Co."), _health(supplier_name="elmich co.")]
    )
    assert sorted(row.supplier_name for row in summary.by_supplier) == [
        "Elmich Co.",
        "elmich co.",
    ]
