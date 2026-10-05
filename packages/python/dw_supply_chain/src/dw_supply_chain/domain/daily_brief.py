"""The daily management brief — what needs handling this morning, grouped
and ordered, never a list of every case.

Every group is a deterministic signal over records this context already
keeps: an SLA overrun, a supplier gone quiet, a case in an exception state,
money waiting on the buyer's side, an approval nobody has decided, a delay a
supplier officially reported, a case that moved in the last day. Nothing
here is inferred, weighted or scored. Which kind of signal reads first is
the tenant's call (`brief_policy.SupplyChainBriefPolicy.signal_order`);
within a kind, the larger group reads first.

Pure computation, no I/O — the handler loads, this module groups.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from dw_supply_chain.domain.po_case import CaseState, CaseTransition, POCase
from dw_supply_chain.domain.portfolio import CaseHealth

# How far back "changed recently" looks. A daily brief covers a day; this is
# the meaning of the word, not a tunable threshold.
CHANGE_WINDOW_HOURS = 24

# How many cases of a group a reader is shown. The brief is a morning read,
# not the list itself: `total` still counts every case, and each group links
# to the full list. What the page shows and what a summary is written from
# are the same cases, so this is the one place that says how many.
ENTRIES_SHOWN = 10


class BriefSignal(StrEnum):
    UPDATE_ESCALATION_DUE = "update_escalation_due"
    SLA_BREACHED = "sla_breached"
    CASE_BLOCKED = "case_blocked"
    APPROVAL_PENDING = "approval_pending"
    MANUAL_REVIEW = "manual_review"
    SUPPLIER_REPORTED_DELAY = "supplier_reported_delay"
    UPDATE_REMINDER_DUE = "update_reminder_due"
    WAITING_EXTERNAL = "waiting_external"
    REWORK = "rework"
    WAITING_ON_US = "waiting_on_us"
    CHANGED_RECENTLY = "changed_recently"


# Exception states a person has to act on, each its own signal so a tenant
# can rank "blocked" above "waiting on an outside party".
_EXCEPTION_SIGNAL: dict[CaseState, BriefSignal] = {
    CaseState.BLOCKED: BriefSignal.CASE_BLOCKED,
    CaseState.MANUAL_REVIEW: BriefSignal.MANUAL_REVIEW,
    CaseState.WAITING_EXTERNAL: BriefSignal.WAITING_EXTERNAL,
    CaseState.REWORK: BriefSignal.REWORK,
}

# States where the next move is the buyer's own (paying the deposit, paying
# the balance), not the supplier's.
WAITING_ON_US_STATES: tuple[CaseState, ...] = (CaseState.WAITING_DEPOSIT, CaseState.WAITING_PAYMENT)


@dataclass(frozen=True, slots=True)
class PendingCaseApproval:
    """One undecided approval on a case, resolved to the case it concerns."""

    case: POCase
    action: str
    requested_at: datetime


@dataclass(frozen=True, slots=True)
class PendingApprovalsSeen:
    """The pending approvals the brief may show. `total` is every pending
    Supply Chain approval in the tenant; `newest` is the slice that was read
    and resolved to a case, so `total` can exceed `len(newest)`."""

    total: int
    newest: tuple[PendingCaseApproval, ...]


@dataclass(frozen=True, slots=True)
class RecentChange:
    """A case's latest transition inside the change window."""

    case: POCase
    transition: CaseTransition


@dataclass(frozen=True, slots=True)
class BriefEntry:
    """One case in one group, with the figure that put it there: days over
    or in a state, days of silence, days of delay reported, days an approval
    has waited. `limit_days` is the SLA it overran; `transition` and
    `approval_action` are set only by their own signals."""

    case: POCase
    days: int | None = None
    limit_days: int | None = None
    transition: CaseTransition | None = None
    approval_action: str | None = None


@dataclass(frozen=True, slots=True)
class BriefGroup:
    signal: BriefSignal
    # The SLA milestone for `SLA_BREACHED`, the state for `WAITING_ON_US`,
    # otherwise None.
    qualifier: str | None
    entries: tuple[BriefEntry, ...]
    # Every case the signal holds for. Equal to `len(entries)` except for
    # approvals, where only the newest are read.
    total: int
    # The one state every case in the group is in, when a state is what
    # defines the group (the exception states, waiting on us) — so a reader
    # can open exactly that list without restating which signal means which
    # state.
    state: CaseState | None = None

    @property
    def shown_entries(self) -> tuple[BriefEntry, ...]:
        """The longest-standing `ENTRIES_SHOWN` cases — what a reader sees."""
        return self.entries[:ENTRIES_SHOWN]

    @property
    def key(self) -> str:
        """Stable within one brief: what a summary cites a group by."""
        if self.qualifier is None:
            return self.signal.value
        return f"{self.signal.value}:{self.qualifier}"


@dataclass(frozen=True, slots=True)
class DailyBrief:
    generated_at: datetime
    active_case_count: int
    # Distinct cases in any group that asks for action — every group except
    # CHANGED_RECENTLY, which is news, not a task.
    flagged_case_count: int
    groups: tuple[BriefGroup, ...]
    # False when the caller may not read approvals: the group is then absent
    # because it was not shown, not because nothing is pending.
    approvals_visible: bool

    def group(self, key: str) -> BriefGroup | None:
        return next((group for group in self.groups if group.key == key), None)


def _by_days(entries: list[BriefEntry]) -> tuple[BriefEntry, ...]:
    """Longest-standing first; a stable tiebreak on the PO reference."""
    return tuple(
        sorted(
            entries,
            key=lambda entry: (
                -(entry.days if entry.days is not None else -1),
                entry.case.po_reference,
            ),
        )
    )


def _health_groups(healths: Sequence[CaseHealth]) -> list[BriefGroup]:
    buckets: dict[tuple[BriefSignal, str | None], list[BriefEntry]] = {}
    states: dict[tuple[BriefSignal, str | None], CaseState] = {}

    def add(
        signal: BriefSignal,
        qualifier: str | None,
        entry: BriefEntry,
        *,
        state: CaseState | None = None,
    ) -> None:
        buckets.setdefault((signal, qualifier), []).append(entry)
        if state is not None:
            states[(signal, qualifier)] = state

    for health in healths:
        case = health.case
        if health.escalation_due:
            add(
                BriefSignal.UPDATE_ESCALATION_DUE,
                None,
                BriefEntry(case=case, days=health.missing_update.age_days),
            )
        elif health.update_overdue:
            add(
                BriefSignal.UPDATE_REMINDER_DUE,
                None,
                BriefEntry(case=case, days=health.missing_update.age_days),
            )
        if health.sla_breached:
            add(
                BriefSignal.SLA_BREACHED,
                health.sla.milestone,
                BriefEntry(
                    case=case, days=health.sla.age_days, limit_days=health.sla.threshold_days
                ),
            )
        exception_signal = _EXCEPTION_SIGNAL.get(case.state)
        if exception_signal is not None:
            add(
                exception_signal,
                None,
                BriefEntry(case=case, days=health.sla.age_days),
                state=case.state,
            )
        if case.state in WAITING_ON_US_STATES:
            add(
                BriefSignal.WAITING_ON_US,
                case.state.value,
                BriefEntry(case=case, days=health.sla.age_days),
                state=case.state,
            )
        delay_days = health.reported_delay_days
        if delay_days is not None:
            add(BriefSignal.SUPPLIER_REPORTED_DELAY, None, BriefEntry(case=case, days=delay_days))

    return [
        BriefGroup(
            signal=signal,
            qualifier=qualifier,
            entries=_by_days(entries),
            total=len(entries),
            state=states.get((signal, qualifier)),
        )
        for (signal, qualifier), entries in buckets.items()
    ]


def compose_brief(
    healths: Sequence[CaseHealth],
    *,
    recent_changes: Sequence[RecentChange],
    approvals: PendingApprovalsSeen | None,
    signal_order: Sequence[BriefSignal],
    now: datetime,
) -> DailyBrief:
    """`approvals` is None when the caller may not read approvals — never
    the same as "none pending", and the brief says which."""
    groups = _health_groups(healths)

    if approvals is not None and approvals.total > 0:
        groups.append(
            BriefGroup(
                signal=BriefSignal.APPROVAL_PENDING,
                qualifier=None,
                entries=_by_days(
                    [
                        BriefEntry(
                            case=approval.case,
                            days=(now - approval.requested_at).days,
                            approval_action=approval.action,
                        )
                        for approval in approvals.newest
                    ]
                ),
                total=approvals.total,
            )
        )

    if recent_changes:
        groups.append(
            BriefGroup(
                signal=BriefSignal.CHANGED_RECENTLY,
                qualifier=None,
                entries=tuple(
                    BriefEntry(case=change.case, transition=change.transition)
                    for change in sorted(
                        recent_changes,
                        key=lambda change: change.transition.occurred_at,
                        reverse=True,
                    )
                ),
                total=len(recent_changes),
            )
        )

    # The policy lists every signal (its own validator refuses one that does
    # not); a signal it somehow lacks still reads, last, rather than vanish.
    position = {signal: index for index, signal in enumerate(signal_order)}
    ordered = sorted(
        groups,
        key=lambda group: (
            position.get(group.signal, len(position)),
            -group.total,
            group.qualifier or "",
        ),
    )
    flagged = {
        entry.case.id.value
        for group in ordered
        if group.signal is not BriefSignal.CHANGED_RECENTLY
        for entry in group.entries
    }
    return DailyBrief(
        generated_at=now,
        active_case_count=len(healths),
        flagged_case_count=len(flagged),
        groups=tuple(ordered),
        approvals_visible=approvals is not None,
    )
