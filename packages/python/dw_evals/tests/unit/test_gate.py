"""Unit: a model gate from an eval report (ticket ai-automation/06)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dw_evals.dataset import EvalDataset
from dw_evals.gate import gate_result, gate_table, model_task_cases
from dw_evals.runner import CaseResult, EvalReport

pytestmark = pytest.mark.unit


def _case(case_id: str, category: str, task: str | None) -> dict[str, object]:
    return {
        "case_id": case_id,
        "category": category,
        "description": case_id,
        "input_ref": "x",
        "expected_ref": "y",
        "grader": "g",
        "tags": [f"task:{task}"] if task else [],
    }


DATASET = EvalDataset.model_validate(
    {
        "dataset_id": "prep",
        "dataset_version": "1.0.0",
        "worker_id": "w",
        "cases": [
            _case("a", "normal", "extract.sample_evaluation"),
            _case("b", "prompt_injection", "extract.sample_evaluation"),
            _case("c", "normal", "prepare.sample_testing"),
            _case("d", "normal", None),
        ],
    }
)


def _report(passed: dict[str, bool]) -> EvalReport:
    results = tuple(
        CaseResult(case_id=c.case_id, category=c.category, grader="g", passed=passed[c.case_id])
        for c in model_task_cases(DATASET).cases
    )
    return EvalReport(
        dataset_id="prep",
        dataset_version="1.0.0",
        worker_id="w",
        total=len(results),
        passed=sum(r.passed for r in results),
        failed=sum(not r.passed for r in results),
        security_coverage_ok=False,
        results=results,
    )


def test_only_model_task_cases_are_graded() -> None:
    assert [c.case_id for c in model_task_cases(DATASET).cases] == ["a", "b"]


def test_a_failed_security_case_is_counted_as_such() -> None:
    result = gate_result(
        _report({"a": True, "b": False}),
        model_task_cases(DATASET),
        profile="qwen",
        mode="live",
        generated_at=datetime(2026, 10, 9, tzinfo=UTC),
    )
    score = result.tasks["extract.sample_evaluation"]
    assert (score.total, score.passed, score.security_failed) == (2, 1, 1)
    assert result.dataset == "prep@1.0.0"
    assert "| FAIL |" in gate_table(result, min_pass_rate=0.5, dataset="prep@1.0.0")


def test_a_mock_table_says_it_is_not_evidence() -> None:
    result = gate_result(
        _report({"a": True, "b": True}),
        model_task_cases(DATASET),
        profile="qwen",
        mode="mock",
        generated_at=datetime(2026, 10, 9, tzinfo=UTC),
    )
    assert "pass (mock: not evidence)" in gate_table(
        result, min_pass_rate=1.0, dataset="prep@1.0.0"
    )
    assert not result.passed("extract.sample_evaluation", dataset="prep@1.0.0", min_pass_rate=1.0)
