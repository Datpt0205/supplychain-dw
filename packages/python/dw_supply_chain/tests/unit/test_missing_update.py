from datetime import UTC, datetime, timedelta

import pytest

from dw_supply_chain.domain.missing_update import (
    MissingUpdateStatus,
    UpdateCadence,
    missing_update_status,
)
from dw_supply_chain.domain.po_case import CaseState

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 24, tzinfo=UTC)
_CREATED = _NOW - timedelta(days=30)
_CADENCE = UpdateCadence(reminder_after_days=5, escalation_after_days=10)
REMINDER_AFTER_DAYS = _CADENCE.reminder_after_days
ESCALATION_AFTER_DAYS = _CADENCE.escalation_after_days


def _status(*, state: CaseState, last_update_days_ago: int | None) -> MissingUpdateStatus:
    last = _NOW - timedelta(days=last_update_days_ago) if last_update_days_ago is not None else None
    assessment = missing_update_status(
        state=state,
        case_created_at=_CREATED,
        last_supplier_update_at=last,
        now=_NOW,
        cadence=_CADENCE,
    )
    return assessment.status


def test_fresh_update_is_on_track() -> None:
    status = _status(state=CaseState.PRODUCTION, last_update_days_ago=0)
    assert status is MissingUpdateStatus.ON_TRACK


def test_just_under_the_reminder_threshold_is_on_track() -> None:
    assert (
        _status(state=CaseState.PRODUCTION, last_update_days_ago=REMINDER_AFTER_DAYS - 1)
        is MissingUpdateStatus.ON_TRACK
    )


def test_reaching_the_reminder_threshold_is_reminder_due() -> None:
    assert (
        _status(state=CaseState.PRODUCTION, last_update_days_ago=REMINDER_AFTER_DAYS)
        is MissingUpdateStatus.REMINDER_DUE
    )


def test_just_under_the_escalation_threshold_is_still_reminder_due() -> None:
    assert (
        _status(state=CaseState.PRODUCTION, last_update_days_ago=ESCALATION_AFTER_DAYS - 1)
        is MissingUpdateStatus.REMINDER_DUE
    )


def test_reaching_the_escalation_threshold_is_escalation_due() -> None:
    assert (
        _status(state=CaseState.PRODUCTION, last_update_days_ago=ESCALATION_AFTER_DAYS)
        is MissingUpdateStatus.ESCALATION_DUE
    )


def test_no_supplier_update_ever_falls_back_to_case_creation_time() -> None:
    # _CREATED is 30 days before _NOW, well past the escalation threshold.
    status = _status(state=CaseState.PRODUCTION, last_update_days_ago=None)
    assert status is MissingUpdateStatus.ESCALATION_DUE


@pytest.mark.parametrize("state", [CaseState.COMPLETED, CaseState.CANCELLED])
def test_terminal_states_are_always_on_track_no_matter_how_stale(state: CaseState) -> None:
    assert _status(state=state, last_update_days_ago=None) is MissingUpdateStatus.ON_TRACK


@pytest.mark.parametrize(
    "state",
    [CaseState.BLOCKED, CaseState.WAITING_EXTERNAL, CaseState.MANUAL_REVIEW, CaseState.REWORK],
)
def test_interrupt_states_are_still_checked_for_staleness(state: CaseState) -> None:
    status = _status(state=state, last_update_days_ago=ESCALATION_AFTER_DAYS)
    assert status is MissingUpdateStatus.ESCALATION_DUE


def test_reference_and_age_are_reported_even_for_a_terminal_case() -> None:
    assessment = missing_update_status(
        state=CaseState.COMPLETED,
        case_created_at=_CREATED,
        last_supplier_update_at=_NOW - timedelta(days=45),
        now=_NOW,
        cadence=_CADENCE,
    )
    assert assessment.status is MissingUpdateStatus.ON_TRACK
    assert assessment.age_days == 45
    assert assessment.reference_at == _NOW - timedelta(days=45)


def test_age_days_reflects_the_chosen_reference_point() -> None:
    assessment = missing_update_status(
        state=CaseState.PRODUCTION,
        case_created_at=_CREATED,
        last_supplier_update_at=_NOW - timedelta(days=3),
        now=_NOW,
        cadence=_CADENCE,
    )
    assert assessment.age_days == 3
    assert assessment.reference_at == _NOW - timedelta(days=3)


def test_the_cadence_decides_not_a_constant() -> None:
    """A case created today is due a reminder at once under a 0-day cadence."""
    assessment = missing_update_status(
        state=CaseState.PRODUCTION,
        case_created_at=_NOW,
        last_supplier_update_at=None,
        now=_NOW,
        cadence=UpdateCadence(reminder_after_days=0, escalation_after_days=1),
    )
    assert assessment.status is MissingUpdateStatus.REMINDER_DUE
