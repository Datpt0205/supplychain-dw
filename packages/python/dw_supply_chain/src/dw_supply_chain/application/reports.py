"""The weekly report for BGĐ, the supplier scorecard and the AI acceptance
metrics (ticket ai-automation/20), read by code from the caller's workspace.

- **Who:** the reads of both kinds of case (`po_case.read`,
  `product_case.read`), checked before anything is read; every count under the
  caller's tenant AND workspace (RLS). No price is counted or shown.
- **What:** `domain.reports` turns the records into figures; every weekly
  figure names the cases it counts.
- **The summary** (`SummarizeWeeklyReport`): one grounded call
  (`summarize_weekly_report@1.0.0`, task `draft.weekly_report`), the figures
  as the prompt's one untrusted variable; a sentence is kept only when every
  number in it is one its cited figures write. A model answer that fits no
  schema is no summary; a provider or budget failure is raised as what it is.
- Nothing is written.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.ports import ModelGateway, ModelOutputInvalidError
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import AuthorizationPort, PolicyOverridePort
from dw_supply_chain.ai_time_saved_policy import SupplyChainAiTimeSaved, resolve_ai_time_saved
from dw_supply_chain.application.handlers import PO_CASE_READ, PRODUCT_CASE_READ, WORKER_VERSION
from dw_supply_chain.domain.daily_brief import VIETNAM
from dw_supply_chain.domain.grounded_writing import KeptSentence, ground_sentences
from dw_supply_chain.domain.reports import (
    Acceptance,
    CaseMove,
    DraftVersion,
    Figure,
    SupplierRecord,
    SupplierScore,
    WeeklySummaryWriting,
    acceptance,
    scorecard,
    week_of,
    weekly_figures,
)
from dw_supply_chain.workflows.grounded_writing import WritingPrompt, write_with_evidence

SUMMARY_PROMPT = WritingPrompt("supply_chain.summarize_weekly_report", "1.0.0")
_WORKER_ID = "supply_chain.weekly_report"
_RESOURCE = "supply_chain_report"
# How far back the AI acceptance report looks by default.
ACCEPTANCE_DAYS = 90


class ReportReadsPort(Protocol):
    """Counts of the caller's workspace (RLS), read only."""

    async def product_moves(
        self, context: AccessContext, start: datetime, end: datetime
    ) -> list[CaseMove]: ...

    async def po_moves(
        self, context: AccessContext, start: datetime, end: datetime
    ) -> list[CaseMove]: ...

    async def po_created(self, context: AccessContext, start: datetime, end: datetime) -> list[str]:
        """The PO numbers of the cases created in the window."""
        ...

    async def open_states(
        self, context: AccessContext
    ) -> tuple[Mapping[str, int], Mapping[str, int]]:
        """Open product cases and open PO cases, counted by state."""
        ...

    async def supplier_records(self, context: AccessContext) -> list[SupplierRecord]: ...

    async def draft_versions(self, context: AccessContext, since: datetime) -> list[DraftVersion]:
        """Every version of the drafts whose lineage began since `since`, with
        its decision."""
        ...


async def _require_reads(authz: AuthorizationPort, context: AccessContext) -> None:
    for scope in (PO_CASE_READ, PRODUCT_CASE_READ):
        await authz.require(context=context, action=scope, resource_type=_RESOURCE)


@dataclass(frozen=True, slots=True)
class WeeklyReport:
    start: datetime
    end: datetime
    figures: tuple[Figure, ...]


@dataclass(frozen=True)
class GetWeeklyReport:
    reads: ReportReadsPort
    authz: AuthorizationPort
    clock: UtcClock

    async def handle(self, context: AccessContext, day: date | None = None) -> WeeklyReport:
        await _require_reads(self.authz, context)
        start, end = week_of(day or self.clock.now().astimezone(VIETNAM).date())
        products, pos = await self.reads.open_states(context)
        figures = weekly_figures(
            await self.reads.product_moves(context, start, end),
            await self.reads.po_moves(context, start, end),
            await self.reads.po_created(context, start, end),
            products,
            pos,
        )
        return WeeklyReport(start, end, tuple(figures))


@dataclass(frozen=True, slots=True)
class WeeklySummary:
    report: WeeklyReport
    sentences: tuple[KeptSentence, ...]
    dropped: int


@dataclass(frozen=True)
class SummarizeWeeklyReport:
    get: GetWeeklyReport
    gateway: ModelGateway
    ids: IdGenerator
    model_profile: str | None = None

    async def handle(self, context: AccessContext, day: date | None = None) -> WeeklySummary:
        report = await self.get.handle(context, day)
        evidence = [f.evidence() for f in report.figures]
        run_id = self.ids.new_uuid()
        try:
            writing = await write_with_evidence(
                self.gateway,
                RunContext(
                    run_id=run_id,
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    actor_id=context.principal_id,
                    worker_id=_WORKER_ID,
                    worker_version=WORKER_VERSION,
                    channel="web",
                    plan_id=context.plan_id,
                    roles=context.roles,
                    scopes=context.scopes,
                    trace_id=str(run_id),
                    subject_ref=f"weekly_report:{report.start.date().isoformat()}",
                ),
                SUMMARY_PROMPT,
                WeeklySummaryWriting,
                task={
                    "purpose": "weekly_report",
                    "week_start": report.start.date().isoformat(),
                    "week_end": (report.end - timedelta(days=1)).date().isoformat(),
                },
                evidence=evidence,
                model_profile=self.model_profile,
            )
        except ModelOutputInvalidError:
            writing = WeeklySummaryWriting()
        grounded = ground_sentences(writing.sentences, evidence)
        return WeeklySummary(report, grounded.kept, len(grounded.dropped))


@dataclass(frozen=True)
class GetSupplierScorecard:
    reads: ReportReadsPort
    authz: AuthorizationPort

    async def handle(self, context: AccessContext) -> list[SupplierScore]:
        await _require_reads(self.authz, context)
        return scorecard(await self.reads.supplier_records(context))


@dataclass(frozen=True, slots=True)
class AiAcceptanceReport:
    since: datetime
    rows: tuple[Acceptance, ...]
    policy_version: str


@dataclass(frozen=True)
class GetAiAcceptance:
    reads: ReportReadsPort
    authz: AuthorizationPort
    policy_override_repo: PolicyOverridePort
    platform_default_time_saved: SupplyChainAiTimeSaved
    clock: UtcClock

    async def handle(
        self, context: AccessContext, *, days: int = ACCEPTANCE_DAYS
    ) -> AiAcceptanceReport:
        await _require_reads(self.authz, context)
        policy = await resolve_ai_time_saved(
            context, self.policy_override_repo, self.platform_default_time_saved
        )
        since = self.clock.now() - timedelta(days=max(1, min(days, 366)))
        rows = acceptance(
            await self.reads.draft_versions(context, since),
            policy.minutes(),
            policy.edited_credit,
        )
        return AiAcceptanceReport(since, tuple(rows), policy.policy_version)


__all__ = [
    "ACCEPTANCE_DAYS",
    "AiAcceptanceReport",
    "GetAiAcceptance",
    "GetSupplierScorecard",
    "GetWeeklyReport",
    "ReportReadsPort",
    "SummarizeWeeklyReport",
    "WeeklyReport",
    "WeeklySummary",
]
