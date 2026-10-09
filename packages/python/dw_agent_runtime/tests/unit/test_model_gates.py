"""Unit: what a model gate result proves (ticket ai-automation/06)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from dw_agent_runtime.model.gates import ModelGateResult, TaskScore, load_gate_results

pytestmark = pytest.mark.unit

DATASET = "supply_chain_preparation@1.0.0"
TASK = "extract.sample_evaluation"


def _result(
    *,
    mode: str = "live",
    dataset: str = DATASET,
    total: int = 9,
    passed: int = 9,
    security_failed: int = 0,
) -> ModelGateResult:
    return ModelGateResult.model_validate(
        {
            "profile": "qwen",
            "dataset": dataset,
            "mode": mode,
            "generated_at": datetime(2026, 10, 9, tzinfo=UTC),
            "tasks": {TASK: TaskScore(total=total, passed=passed, security_failed=security_failed)},
        }
    )


def test_a_live_run_that_passed_every_case_passes() -> None:
    assert _result().passed(TASK, dataset=DATASET, min_pass_rate=1.0)


@pytest.mark.parametrize(
    "result",
    [
        _result(mode="mock"),
        _result(dataset="supply_chain_preparation@0.9.0"),
        _result(passed=8),
        _result(passed=9, security_failed=1),
        _result(total=0, passed=0),
    ],
    ids=["mock", "other dataset", "below threshold", "security failed", "no case"],
)
def test_anything_less_is_not_evidence(result: ModelGateResult) -> None:
    assert not result.passed(TASK, dataset=DATASET, min_pass_rate=1.0)


def test_a_task_the_run_did_not_grade_does_not_pass() -> None:
    assert not _result().passed("extract.supplier_quotation", dataset=DATASET, min_pass_rate=0.5)


def test_results_are_read_by_profile_and_a_missing_directory_is_none(tmp_path: Path) -> None:
    assert load_gate_results(tmp_path / "absent") == {}
    (tmp_path / "qwen.json").write_text(_result().model_dump_json(), encoding="utf-8")
    assert set(load_gate_results(tmp_path)) == {"qwen"}
