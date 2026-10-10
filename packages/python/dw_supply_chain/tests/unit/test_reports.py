"""Unit: the weekly report, its summary, the supplier scorecard and AI
acceptance (ticket ai-automation/20).

The real handlers over `testing.reports`, the shipped prompt rendered through
the real registry and the shipped minutes-saved policy: every number is
code's and names its cases, the summary keeps only sentences whose numbers are
the figures they cite, and nothing of another workspace is counted.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from dw_agent_runtime.model.prompts import load_shipped_prompts
from dw_agent_runtime.ports import ModelOutputInvalidError
from dw_kernel.errors import PermissionDeniedError
from dw_supply_chain.application.handlers import PO_CASE_READ, PRODUCT_CASE_READ
from dw_supply_chain.domain.daily_brief import VIETNAM
from dw_supply_chain.domain.grounded_writing import CitedSentence
from dw_supply_chain.domain.reports import (
    CaseMove,
    DraftVersion,
    SupplierRecord,
    WeeklySummaryWriting,
    acceptance,
    scorecard,
    week_of,
)
from dw_supply_chain.testing.extraction import ScriptedGateway
from dw_supply_chain.testing.purchase_orders import NOW
from dw_supply_chain.testing.reports import SHIPPED_TIME_SAVED, ReportWorld

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[5]
PROMPTS = load_shipped_prompts(REPO_ROOT / "configs")
READS = frozenset({PO_CASE_READ, PRODUCT_CASE_READ})
IN_WEEK = NOW - timedelta(days=1)


def _seed(world: ReportWorld, scope: Any = None) -> None:
    s = scope or world.scope
    reads = world.reads
    reads.product += [
        (s, IN_WEEK, CaseMove("DX-1", "proposed", "propose")),
        (s, IN_WEEK, CaseMove("DX-2", "proposed", "import")),
        (s, IN_WEEK, CaseMove("DX-1", "pending_bod_review", "pass_sample")),
        (s, IN_WEEK, CaseMove("DX-3", "revision_requested", "request_revision")),
        # Last week: not this week's.
        (s, IN_WEEK - timedelta(days=9), CaseMove("DX-9", "proposed", "propose")),
    ]
    reads.po += [
        (s, IN_WEEK, CaseMove("PO-1", "production")),
        (s, IN_WEEK, CaseMove("PO-2", "rework")),
        (s, IN_WEEK, CaseMove("PO-3", "completed")),
    ]
    reads.created += [(s, IN_WEEK, "PO-4")]
    reads.product_states += [(s, "sample_testing"), (s, "proposed")]
    reads.po_states += [(s, "production"), (s, "qc"), (s, "rework")]


def _figures(world: ReportWorld, **kw: Any) -> dict[str, Any]:
    report = asyncio.run(world.weekly().handle(world.context(READS, **kw)))
    return {f.key: f for f in report.figures}


def test_a_week_runs_monday_to_monday_in_vietnam() -> None:
    start, end = week_of(date(2026, 10, 10))
    assert (start, end - start) == (datetime(2026, 10, 5, tzinfo=VIETNAM), timedelta(days=7))
    assert week_of(date(2026, 10, 5))[0] == start
    assert week_of(date(2026, 10, 12))[0] == end


def test_the_weekly_figures_count_the_weeks_history_and_name_the_cases() -> None:
    world = ReportWorld()
    _seed(world)
    figures = _figures(world)
    assert (figures["proposed"].value, figures["proposed"].cases) == (2, ("DX-1", "DX-2"))
    assert figures["samples_passed"].cases == ("DX-1",)
    assert figures["revisions"].value == 1
    assert figures["po_created"].cases == ("PO-4",)
    assert (figures["production_started"].value, figures["qc_reworks"].cases) == (1, ("PO-2",))
    assert figures["received"].cases == ("PO-3",)
    assert (figures["open_products"].value, figures["open_pos"].value) == (2, 3)


def test_another_workspaces_records_are_never_counted() -> None:
    world = ReportWorld()
    _seed(world, scope=(world.tenant_id, uuid.uuid4()))
    figures = _figures(world)
    assert all(f.value == 0 for f in figures.values())


def test_a_report_needs_the_reads_of_both_kinds_of_case() -> None:
    world = ReportWorld()
    for scopes in (frozenset({PO_CASE_READ}), frozenset({PRODUCT_CASE_READ})):
        with pytest.raises(PermissionDeniedError):
            asyncio.run(world.weekly().handle(world.context(scopes)))
        with pytest.raises(PermissionDeniedError):
            asyncio.run(world.suppliers().handle(world.context(scopes)))
        with pytest.raises(PermissionDeniedError):
            asyncio.run(world.acceptance().handle(world.context(scopes)))


def _summarize(writing: Any) -> Any:
    world = ReportWorld(gateway=ScriptedGateway(PROMPTS, answer=writing))
    _seed(world)
    return world, asyncio.run(world.summary().handle(world.context(READS)))


def test_the_summary_keeps_only_sentences_whose_numbers_are_the_figures_cited() -> None:
    writing = WeeklySummaryWriting(
        sentences=[
            CitedSentence(text="Tuần này có 2 hồ sơ SP mới.", cites=["figure:proposed"]),
            CitedSentence(text="QC trả 5 PO làm lại.", cites=["figure:qc_reworks"]),
            CitedSentence(text="Doanh số tăng mạnh.", cites=["figure:revenue"]),
            CitedSentence(text="Mọi thứ ổn.", cites=[]),
        ]
    )
    world, summary = _summarize(writing)
    assert [s.text for s in summary.sentences] == ["Tuần này có 2 hồ sơ SP mới."]
    assert summary.dropped == 3
    (sent,) = world.gateway.sent
    assert '"key": "figure:proposed"' in sent.user


def test_a_summary_that_fits_no_schema_is_no_summary() -> None:
    _, summary = _summarize(ModelOutputInvalidError("not the schema"))
    assert summary.sentences == () and summary.report.figures


def test_the_scorecard_counts_each_supplier_and_orders_by_volume_not_score() -> None:
    world = ReportWorld()
    world.reads.suppliers += [
        (world.scope, SupplierRecord("B", po_total=1, rounds_passed=1, rounds_revised=1)),
        (world.scope, SupplierRecord("Z", po_total=3, qc_reworks=2, lines_differing=4)),
        ((world.tenant_id, uuid.uuid4()), SupplierRecord("Khác", po_total=9)),
    ]
    scores = asyncio.run(world.suppliers().handle(world.context(READS)))
    assert [s.record.supplier for s in scores] == ["Z", "B"]
    assert scores[1].sample_pass_rate == 0.5 and scores[0].sample_pass_rate is None
    assert scorecard([]) == []


def _versions(*rows: tuple[str, str, int, str | None]) -> list[DraftVersion]:
    return [DraftVersion(*r) for r in rows]


def test_acceptance_counts_each_lineage_once_by_how_it_ended() -> None:
    rows = acceptance(
        _versions(
            ("sample_evaluation", "a", 1, "confirmed"),
            ("sample_evaluation", "b", 1, None),
            ("sample_evaluation", "b", 2, "confirmed"),
            ("sample_evaluation", "c", 1, "rejected"),
            ("sample_evaluation", "d", 1, None),
            ("purchase_order", "e", 1, "confirmed"),
        ),
        {"sample_evaluation": 30, "purchase_order": 20},
        0.5,
    )
    by_type = {r.doc_type: r for r in rows}
    se = by_type["sample_evaluation"]
    assert (se.drafted, se.as_is, se.edited, se.rejected, se.open) == (4, 1, 1, 1, 1)
    assert se.minutes_saved == 45
    assert by_type["purchase_order"].minutes_saved == 20


def test_acceptance_reads_the_window_and_the_shipped_minutes() -> None:
    world = ReportWorld()
    old = NOW - timedelta(days=200)
    world.reads.drafts += [
        (world.scope, NOW - timedelta(days=3), DraftVersion("bod_submission", "x", 1, "confirmed")),
        (world.scope, old, DraftVersion("bod_submission", "y", 1, "confirmed")),
        (
            (world.tenant_id, uuid.uuid4()),
            NOW,
            DraftVersion("bod_submission", "z", 1, "confirmed"),
        ),
    ]
    report = asyncio.run(world.acceptance().handle(world.context(READS)))
    (row,) = report.rows
    assert (row.drafted, row.minutes_saved) == (1, SHIPPED_TIME_SAVED.minutes()["bod_submission"])
    assert report.policy_version == "1.0.0"


def test_the_shipped_minutes_name_only_types_the_lanes_draft() -> None:
    from dw_supply_chain.domain.document_draft import DRAFT_TEMPLATES

    assert set(SHIPPED_TIME_SAVED.minutes_per_draft) <= set(DRAFT_TEMPLATES)
