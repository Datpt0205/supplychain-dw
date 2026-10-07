"""The daily management brief — what needs handling this morning, grouped
and ordered, never a list of every case.

Every group is a deterministic signal over records this context already
keeps: an SLA overrun, a supplier gone quiet, a case in an exception state,
money waiting on the buyer's side, an approval nobody has decided, a delay a
supplier officially reported, a case that moved in the last day. Nothing
here is inferred, weighted or scored. Which kind of signal reads first is
the tenant's call (`brief_policy.SupplyChainBriefPolicy.signal_order`);
within a kind, the larger group reads first.

Stage 1 (ticket 08) adds four groups of product-development cases, read from
the same records the case page and the follow-up sweep read: a stage-1 SLA
overrun, a case waiting for BGĐ's review, one waiting for its sign-off, and
the sample rounds closed today (Đạt, Cần chỉnh sửa, Hủy), "today" being the
Vietnamese calendar day. They are shown only to a caller who may read product
cases; otherwise the brief says it did not look (`product_cases_visible`).

Pure computation, no I/O — the handler loads, this module groups.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum

from dw_supply_chain.domain.po_case import CaseState, CaseTransition, POCase
from dw_supply_chain.domain.portfolio import CaseHealth
from dw_supply_chain.domain.product_development_case import (
    ProductDevelopmentCase,
    ProductDevState,
    SampleRound,
)
from dw_supply_chain.domain.sla_evaluation import SLAEvaluation, SLAEvaluationStatus

# How far back "changed recently" looks. A daily brief covers a day; this is
# the meaning of the word, not a tunable threshold.
CHANGE_WINDOW_HOURS = 24

# How many cases of a group a reader is shown. The brief is a morning read,
# not the list itself: `total` still counts every case, and each group links
# to the full list. What the page shows and what a summary is written from
# are the same cases, so this is the one place that says how many.
ENTRIES_SHOWN = 10

# Vietnam keeps one offset all year (no daylight saving), so a fixed offset is
# the calendar day Elmich works in: "evaluated today" starts at 00:00 here.
VIETNAM = timezone(timedelta(hours=7))


def local_day_start(now: datetime) -> datetime:
    """00:00 of `now`'s day in Vietnam, as an aware datetime."""
    return now.astimezone(VIETNAM).replace(hour=0, minute=0, second=0, microsecond=0)


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
    # Stage 1 (ticket 08): product-development cases.
    PRODUCT_SLA_BREACHED = "product_sla_breached"
    PRODUCT_AWAITING_BOD = "product_awaiting_bod"
    PRODUCT_AWAITING_SIGNOFF = "product_awaiting_signoff"
    SAMPLE_EVALUATED_TODAY = "sample_evaluated_today"


# The groups whose cases are product-development cases (`product_entries`).
PRODUCT_SIGNALS = frozenset(
    {
        BriefSignal.PRODUCT_SLA_BREACHED,
        BriefSignal.PRODUCT_AWAITING_BOD,
        BriefSignal.PRODUCT_AWAITING_SIGNOFF,
        BriefSignal.SAMPLE_EVALUATED_TODAY,
    }
)
# News, not a task: what changed, not what someone has to act on. Neither
# counts towards the flagged figures.
NEWS_SIGNALS = frozenset({BriefSignal.CHANGED_RECENTLY, BriefSignal.SAMPLE_EVALUATED_TODAY})

# A product case waiting on an approval, by the approval it waits on.
_AWAITING_SIGNAL: dict[ProductDevState, BriefSignal] = {
    ProductDevState.PENDING_BOD_REVIEW: BriefSignal.PRODUCT_AWAITING_BOD,
    ProductDevState.PENDING_SIGNOFF: BriefSignal.PRODUCT_AWAITING_SIGNOFF,
}


# Exception states a person has to act on, each its own signal so a tenant
# can rank "blocked" above "waiting on an outside party".
_EXCEPTION_SIGNAL: dict[CaseState, BriefSignal] = {
    CaseState.BLOCKED: BriefSignal.CASE_BLOCKED,
    CaseState.MANUAL_REVIEW: BriefSignal.MANUAL_REVIEW,
    CaseState.WAITING_EXTERNAL: BriefSignal.WAITING_EXTERNAL,
    CaseState.REWORK: BriefSignal.REWORK,
}

# States where the next move is the buyer's own (creating the PO after ĐẶT
# HÀNG, paying the deposit, paying the balance), not the supplier's. Each is
# its own group, qualified by the state: "Chờ tạo PO" is the one Cung ứng
# (the holders of `create_po`'s duty) acts on.
WAITING_ON_US_STATES: tuple[CaseState, ...] = (
    CaseState.ORDER_REQUESTED,
    CaseState.WAITING_DEPOSIT,
    CaseState.WAITING_PAYMENT,
)


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
class ProductBriefEntry:
    """One product-development case in one stage-1 group. `days` is how long
    it has been in its state (with `limit_days`, the SLA it overran); a sample
    group's entry carries the round that closed today instead."""

    case: ProductDevelopmentCase
    days: int | None = None
    limit_days: int | None = None
    sample_round: SampleRound | None = None


@dataclass(frozen=True, slots=True)
class ProductHealth:
    """An active product case with its stage-1 SLA evaluation, under its own
    stamped Category (`sla_evaluation.evaluate_product_sla`)."""

    case: ProductDevelopmentCase
    sla: SLAEvaluation


@dataclass(frozen=True, slots=True)
class ClosedRound:
    """A sample round closed in the window, with the case it belongs to."""

    case: ProductDevelopmentCase
    sample_round: SampleRound


@dataclass(frozen=True, slots=True)
class StageOneSnapshot:
    """What the brief reads of stage 1 in one workspace: its active cases and
    the sample rounds closed since the start of the Vietnamese day."""

    active: tuple[ProductHealth, ...]
    closed_today: tuple[ClosedRound, ...]


@dataclass(frozen=True, slots=True)
class BriefGroup:
    signal: BriefSignal
    # The SLA milestone for `SLA_BREACHED` and `PRODUCT_SLA_BREACHED`, the
    # state for `WAITING_ON_US`, the round's result for
    # `SAMPLE_EVALUATED_TODAY`, otherwise None.
    qualifier: str | None
    # PO cases; empty for a stage-1 group, whose cases are `product_entries`.
    entries: tuple[BriefEntry, ...]
    # Every case the signal holds for. Equal to `len(entries)` except for
    # approvals, where only the newest are read.
    total: int
    # The one state every case in the group is in, when a state is what
    # defines the group (the exception states, waiting on us) — so a reader
    # can open exactly that list without restating which signal means which
    # state.
    state: CaseState | None = None
    # A stage-1 group's cases (`PRODUCT_SIGNALS`), and the one product state
    # that defines it when one does.
    product_entries: tuple[ProductBriefEntry, ...] = ()
    product_state: ProductDevState | None = None

    @property
    def shown_entries(self) -> tuple[BriefEntry, ...]:
        """The longest-standing `ENTRIES_SHOWN` cases — what a reader sees."""
        return self.entries[:ENTRIES_SHOWN]

    @property
    def shown_product_entries(self) -> tuple[ProductBriefEntry, ...]:
        """The first `ENTRIES_SHOWN` product cases, in the group's order."""
        return self.product_entries[:ENTRIES_SHOWN]

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
    # Distinct PO cases in any group that asks for action — every group but
    # the news ones (`NEWS_SIGNALS`).
    flagged_case_count: int
    groups: tuple[BriefGroup, ...]
    # False when the caller may not read approvals: the group is then absent
    # because it was not shown, not because nothing is pending.
    approvals_visible: bool
    # The same for stage 1: False when the caller may not read product cases;
    # the two counts below are then 0 because nothing was looked at.
    product_cases_visible: bool = False
    active_product_case_count: int = 0
    flagged_product_case_count: int = 0

    def group(self, key: str) -> BriefGroup | None:
        return next((group for group in self.groups if group.key == key), None)


def _by_days(entries: list[BriefEntry]) -> tuple[BriefEntry, ...]:
    """Longest-standing first; a stable tiebreak on the PO reference (a case
    awaiting its PO has none and sorts first among equals)."""
    return tuple(
        sorted(
            entries,
            key=lambda entry: (
                -(entry.days if entry.days is not None else -1),
                entry.case.po_reference or "",
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


def _product_by_days(entries: list[ProductBriefEntry]) -> tuple[ProductBriefEntry, ...]:
    """Longest-standing first, then by proposal code."""
    return tuple(
        sorted(
            entries,
            key=lambda entry: (
                -(entry.days if entry.days is not None else -1),
                entry.case.proposal_code,
            ),
        )
    )


def _by_pic(entries: list[ProductBriefEntry]) -> tuple[ProductBriefEntry, ...]:
    """Together by PIC (Elmich's report reads "theo PIC"), then by code."""
    return tuple(
        sorted(
            entries,
            key=lambda entry: (
                str(entry.case.pic_user_id),
                entry.case.proposal_code,
                entry.sample_round.round_no if entry.sample_round is not None else 0,
            ),
        )
    )


def stage_one_groups(snapshot: StageOneSnapshot) -> list[BriefGroup]:
    """The stage-1 groups of one workspace, unordered: what `compose_brief`
    places among the PO groups, and what the daily report counts."""
    buckets: dict[tuple[BriefSignal, str | None], list[ProductBriefEntry]] = {}
    for health in snapshot.active:
        case, sla = health.case, health.sla
        if sla.status is SLAEvaluationStatus.BREACHED:
            buckets.setdefault((BriefSignal.PRODUCT_SLA_BREACHED, sla.milestone), []).append(
                ProductBriefEntry(case=case, days=sla.age_days, limit_days=sla.threshold_days)
            )
        awaiting = _AWAITING_SIGNAL.get(case.state)
        if awaiting is not None:
            buckets.setdefault((awaiting, None), []).append(
                ProductBriefEntry(case=case, days=sla.age_days)
            )
    for closed in snapshot.closed_today:
        result = closed.sample_round.result
        # Only closed rounds are read, and closing a round sets its result.
        assert result is not None
        buckets.setdefault((BriefSignal.SAMPLE_EVALUATED_TODAY, result.value), []).append(
            ProductBriefEntry(case=closed.case, sample_round=closed.sample_round)
        )
    awaited_state = {signal: state for state, signal in _AWAITING_SIGNAL.items()}
    return [
        BriefGroup(
            signal=signal,
            qualifier=qualifier,
            entries=(),
            total=len(entries),
            product_entries=(
                _by_pic(entries)
                if signal is BriefSignal.SAMPLE_EVALUATED_TODAY
                else _product_by_days(entries)
            ),
            product_state=awaited_state.get(signal),
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
    stage_one: StageOneSnapshot | None = None,
) -> DailyBrief:
    """`approvals` is None when the caller may not read approvals — never
    the same as "none pending", and the brief says which. `stage_one` is
    None, the same way, when the caller may not read product cases."""
    groups = _health_groups(healths)
    if stage_one is not None:
        groups.extend(stage_one_groups(stage_one))

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
    asking = [group for group in ordered if group.signal not in NEWS_SIGNALS]
    flagged = {entry.case.id.value for group in asking for entry in group.entries}
    flagged_products = {entry.case.id.value for group in asking for entry in group.product_entries}
    return DailyBrief(
        generated_at=now,
        active_case_count=len(healths),
        flagged_case_count=len(flagged),
        groups=tuple(ordered),
        approvals_visible=approvals is not None,
        product_cases_visible=stage_one is not None,
        active_product_case_count=len(stage_one.active) if stage_one is not None else 0,
        flagged_product_case_count=len(flagged_products),
    )
