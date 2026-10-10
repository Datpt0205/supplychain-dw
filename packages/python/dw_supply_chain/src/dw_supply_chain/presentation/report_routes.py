"""Reports (ticket ai-automation/20): the weekly report for BGĐ, its AI
summary, the supplier scorecard and the AI acceptance metrics.

`GET /reports/weekly?day=` — the week's figures, each naming its cases.
`POST /reports/weekly/summary?day=` — the AI's summary of that week, only the
sentences whose numbers are the figures they cite. Read-only: no key.
`GET /reports/suppliers` — per supplier, counted by code.
`GET /reports/ai-acceptance?days=` — how the drafts the lanes wrote ended, and
an estimate of the minutes saved.

The request names a day or a window only; never a tenant, a workspace or a
case.
"""

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from dw_platform.application.access_context import AccessContext
from dw_supply_chain.application.reports import (
    ACCEPTANCE_DAYS,
    GetAiAcceptance,
    GetSupplierScorecard,
    GetWeeklyReport,
    SummarizeWeeklyReport,
    WeeklyReport,
)

AccessContextResolver = Callable[..., object]


class FigureView(BaseModel):
    key: str
    label: str
    value: int
    # The cases counted (at most 20 named): a proposal code or a PO number.
    cases: list[str]


class WeeklyReportView(BaseModel):
    week_start: dt.date
    week_end: dt.date
    figures: list[FigureView]


class FigureCitationView(BaseModel):
    key: str
    label: str


class SummarySentenceView(BaseModel):
    text: str
    cites: list[FigureCitationView]


class WeeklySummaryView(BaseModel):
    report: WeeklyReportView
    # AI-written; each kept only because its numbers are the figures it cites.
    sentences: list[SummarySentenceView]
    dropped: int


class SupplierScoreView(BaseModel):
    supplier: str
    po_total: int
    po_open: int
    po_completed: int
    qc_reworks: int
    rounds_passed: int
    rounds_revised: int
    rounds_rejected: int
    lines_differing: int
    # Rounds passed of rounds closed; null before any closed.
    sample_pass_rate: float | None


class AcceptanceRowView(BaseModel):
    doc_type: str
    drafted: int
    as_is: int
    edited: int
    rejected: int
    open: int
    # An estimate, from the tenant's minutes per paper.
    minutes_saved: int


class AiAcceptanceView(BaseModel):
    since: dt.datetime
    policy_version: str
    rows: list[AcceptanceRowView]


def _report_view(report: WeeklyReport) -> WeeklyReportView:
    return WeeklyReportView(
        week_start=report.start.date(),
        week_end=(report.end - dt.timedelta(days=1)).date(),
        figures=[
            FigureView(key=f.key, label=f.label, value=f.value, cases=list(f.cases))
            for f in report.figures
        ],
    )


@dataclass(frozen=True)
class ReportHandlers:
    weekly: GetWeeklyReport
    summary: SummarizeWeeklyReport
    suppliers: GetSupplierScorecard
    acceptance: GetAiAcceptance


def build_report_router(
    handlers: ReportHandlers, *, resolve_access_context: AccessContextResolver
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/supply-chain", tags=["supply_chain"])
    require_access_context = Annotated[AccessContext, Depends(resolve_access_context)]

    @router.get("/reports/weekly", response_model=WeeklyReportView)
    async def get_weekly_report(
        context: require_access_context, day: dt.date | None = None
    ) -> WeeklyReportView:
        return _report_view(await handlers.weekly.handle(context, day))

    @router.post("/reports/weekly/summary", response_model=WeeklySummaryView)
    async def summarize_weekly_report(
        context: require_access_context, day: dt.date | None = None
    ) -> WeeklySummaryView:
        summary = await handlers.summary.handle(context, day)
        labels = {f"figure:{f.key}": f.label for f in summary.report.figures}
        return WeeklySummaryView(
            report=_report_view(summary.report),
            sentences=[
                SummarySentenceView(
                    text=s.text,
                    cites=[FigureCitationView(key=k, label=labels.get(k, k)) for k in s.cites],
                )
                for s in summary.sentences
            ],
            dropped=summary.dropped,
        )

    @router.get("/reports/suppliers", response_model=list[SupplierScoreView])
    async def get_supplier_scorecard(context: require_access_context) -> list[SupplierScoreView]:
        return [
            SupplierScoreView(
                supplier=s.record.supplier,
                po_total=s.record.po_total,
                po_open=s.record.po_open,
                po_completed=s.record.po_completed,
                qc_reworks=s.record.qc_reworks,
                rounds_passed=s.record.rounds_passed,
                rounds_revised=s.record.rounds_revised,
                rounds_rejected=s.record.rounds_rejected,
                lines_differing=s.record.lines_differing,
                sample_pass_rate=s.sample_pass_rate,
            )
            for s in await handlers.suppliers.handle(context)
        ]

    @router.get("/reports/ai-acceptance", response_model=AiAcceptanceView)
    async def get_ai_acceptance(
        context: require_access_context,
        days: Annotated[int, Query(ge=1, le=366)] = ACCEPTANCE_DAYS,
    ) -> AiAcceptanceView:
        report = await handlers.acceptance.handle(context, days=days)
        return AiAcceptanceView(
            since=report.since,
            policy_version=report.policy_version,
            rows=[
                AcceptanceRowView(
                    doc_type=r.doc_type,
                    drafted=r.drafted,
                    as_is=r.as_is,
                    edited=r.edited,
                    rejected=r.rejected,
                    open=r.open,
                    minutes_saved=r.minutes_saved,
                )
                for r in report.rows
            ],
        )

    return router


__all__ = ["ReportHandlers", "build_report_router"]
