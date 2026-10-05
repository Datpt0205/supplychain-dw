"""Control Tower portfolio view (DW-SC-03) —
counts over deterministic signals, never a score.

Answers two of the strategy doc's own Control Tower questions directly: "Top 10 bottleneck
hiện tại là gì?" (active cases grouped by the state they are sitting in) and
"NCC nào đang có nhiều PO chậm update nhất?" (grouped by supplier, ordered by
exactly that count). Every number here is a count of cases or a day figure
`evaluate_sla`/`missing_update_status` already computed — nothing is weighted,
blended or ranked by a formula someone would have to agree on first. Which
row is the real bottleneck is the reader's call; this module only makes sure
the facts they read it from are the same ones the Attention Queue and the
Case Workspace show.

Pure computation, no I/O — the handler loads, this module summarizes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from dw_supply_chain.domain.missing_update import MissingUpdateAssessment, MissingUpdateStatus
from dw_supply_chain.domain.po_case import CaseState, POCase
from dw_supply_chain.domain.sla_evaluation import SLAEvaluation, SLAEvaluationStatus
from dw_supply_chain.domain.supplier_update import SupplierUpdate


@dataclass(frozen=True, slots=True)
class CaseHealth:
    """One active case with both deterministic signals evaluated — the unit
    the Attention Queue filters, the Control Tower counts and the daily brief
    groups. What counts as "SLA breached", "update overdue" and "a delay the
    supplier reported" is decided here, once, so no two views can disagree
    about which cases need attention."""

    case: POCase
    sla: SLAEvaluation
    missing_update: MissingUpdateAssessment
    # The case's most recent supplier update, if it has ever had one.
    latest_update: SupplierUpdate | None = None

    @property
    def reported_delay_days(self) -> int | None:
        """The delay the supplier's LATEST update reports, if that update
        reports one and passed its own evidence check. An update still
        waiting for confirmation (low confidence, or a `source_ref` not
        found in the message) is not an official statement, so it reports
        nothing here: no source, no inferred status. A later update that
        reports no delay supersedes an earlier one that did."""
        update = self.latest_update
        if update is None or update.requires_confirmation:
            return None
        delay_days = update.extraction.delay_days
        return delay_days if delay_days is not None and delay_days > 0 else None

    @property
    def sla_breached(self) -> bool:
        return self.sla.status is SLAEvaluationStatus.BREACHED

    @property
    def update_overdue(self) -> bool:
        return self.missing_update.status is not MissingUpdateStatus.ON_TRACK

    @property
    def escalation_due(self) -> bool:
        return self.missing_update.status is MissingUpdateStatus.ESCALATION_DUE

    @property
    def needs_attention(self) -> bool:
        return self.sla_breached or self.update_overdue


@dataclass(frozen=True, slots=True)
class StateSummary:
    """Active cases currently sitting in one state. `oldest_in_state_days`
    is the longest any of them has been there — the same `age_days`
    `evaluate_sla` reports for every state, mapped to an SLA or not."""

    state: CaseState
    case_count: int
    sla_breached_count: int
    update_overdue_count: int
    oldest_in_state_days: int


@dataclass(frozen=True, slots=True)
class SupplierSummary:
    """Active cases for one supplier. Grouped by `POCase.supplier_name`
    exactly as stored — there is no supplier master record yet, so two
    spellings of one supplier are two rows, never merged by a guess."""

    supplier_name: str
    case_count: int
    update_overdue_count: int
    escalation_due_count: int
    sla_breached_count: int
    longest_silence_days: int


@dataclass(frozen=True, slots=True)
class PortfolioSummary:
    active_case_count: int
    sla_breached_count: int
    update_overdue_count: int
    # Lifecycle order (`CaseState` declaration order: the happy path, then
    # the exception states) — reads like the pipeline itself, states with no
    # active case omitted.
    by_state: tuple[StateSummary, ...]
    # Most cases with an overdue supplier update first, then most due for
    # escalation, then by name so equal rows keep a stable order.
    by_supplier: tuple[SupplierSummary, ...]


_LIFECYCLE_POSITION = {state: position for position, state in enumerate(CaseState)}


def _state_summary(state: CaseState, healths: Sequence[CaseHealth]) -> StateSummary:
    return StateSummary(
        state=state,
        case_count=len(healths),
        sla_breached_count=sum(health.sla_breached for health in healths),
        update_overdue_count=sum(health.update_overdue for health in healths),
        oldest_in_state_days=max(health.sla.age_days for health in healths),
    )


def _supplier_summary(supplier_name: str, healths: Sequence[CaseHealth]) -> SupplierSummary:
    return SupplierSummary(
        supplier_name=supplier_name,
        case_count=len(healths),
        update_overdue_count=sum(health.update_overdue for health in healths),
        escalation_due_count=sum(health.escalation_due for health in healths),
        sla_breached_count=sum(health.sla_breached for health in healths),
        longest_silence_days=max(health.missing_update.age_days for health in healths),
    )


def summarize_portfolio(healths: Sequence[CaseHealth]) -> PortfolioSummary:
    by_state: dict[CaseState, list[CaseHealth]] = {}
    by_supplier: dict[str, list[CaseHealth]] = {}
    for health in healths:
        by_state.setdefault(health.case.state, []).append(health)
        by_supplier.setdefault(health.case.supplier_name, []).append(health)

    states = sorted(by_state, key=_LIFECYCLE_POSITION.__getitem__)
    suppliers = sorted(
        (_supplier_summary(name, group) for name, group in by_supplier.items()),
        key=lambda row: (-row.update_overdue_count, -row.escalation_due_count, row.supplier_name),
    )
    return PortfolioSummary(
        active_case_count=len(healths),
        sla_breached_count=sum(health.sla_breached for health in healths),
        update_overdue_count=sum(health.update_overdue for health in healths),
        by_state=tuple(_state_summary(state, by_state[state]) for state in states),
        by_supplier=tuple(suppliers),
    )
