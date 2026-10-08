"""The runner grades with the table it is given and nothing else.

Running every dataset with the table `make eval-smoke` builds lives in
`apps/api/tests/unit/test_eval_datasets.py`: that table includes the bounded
contexts' graders, which this package must not import."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dw_evals.dataset import EvalDataset
from dw_evals.graders import GRADERS, GraderContext, GradeResult, merge_graders
from dw_evals.runner import load_dataset, run_dataset

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[5]
DATASETS_DIR = REPO_ROOT / "evals" / "datasets"
DATASETS = sorted(DATASETS_DIR.glob("*.json"))


def test_datasets_exist() -> None:
    assert {p.name for p in DATASETS} >= {"platform@1.2.0.json"}


@pytest.mark.parametrize("path", DATASETS, ids=lambda p: p.stem)
def test_dataset_has_full_security_coverage(path: Path) -> None:
    assert load_dataset(path).has_full_security_coverage()


def test_platform_dataset_passes_with_the_platform_table() -> None:
    report = run_dataset(load_dataset(DATASETS_DIR / "platform@1.2.0.json"), REPO_ROOT, GRADERS)
    assert report.ok, [(r.case_id, r.details) for r in report.results if not r.passed]


def _two_case_dataset() -> EvalDataset:
    case = {
        "category": "normal",
        "description": "",
        "input_ref": "evals/fixtures/cases/pf_side_effect.json",
        "expected_ref": "evals/expected/pf_side_effect.json",
    }
    return EvalDataset.model_validate(
        {
            "dataset_id": "table",
            "dataset_version": "1.0.0",
            "worker_id": "dw.test",
            "cases": [
                {**case, "case_id": "fake", "grader": "x.ok"},
                {**case, "case_id": "platform", "grader": "runtime.side_effect_approval"},
            ],
        }
    )


def _always_ok(
    ctx: GraderContext, input_data: dict[str, Any], expected: dict[str, Any]
) -> GradeResult:
    return GradeResult.ok()


def test_the_table_passed_in_is_the_only_one_consulted() -> None:
    report = run_dataset(_two_case_dataset(), REPO_ROOT, {"x.ok": _always_ok})
    by_id = {r.case_id: r for r in report.results}
    assert by_id["fake"].passed
    # A platform grader the caller did not pass does not exist for this run.
    assert not by_id["platform"].passed
    assert by_id["platform"].details["reason"] == "unknown grader"


def test_merge_graders_refuses_a_name_two_tables_claim() -> None:
    with pytest.raises(ValueError, match=r"runtime\.prompt_injection"):
        merge_graders(GRADERS, {"runtime.prompt_injection": _always_ok})
    assert set(merge_graders(GRADERS, {"x.ok": _always_ok})) == set(GRADERS) | {"x.ok"}


def test_failing_grader_is_reported_not_raised(tmp_path: Path) -> None:
    dataset = EvalDataset.model_validate(
        {
            "dataset_id": "broken",
            "dataset_version": "1.0.0",
            "worker_id": "dw.test",
            "cases": [
                {
                    "case_id": "unknown-grader",
                    "category": "failure",
                    "description": "grader does not exist",
                    "input_ref": "evals/fixtures/cases/pf_side_effect.json",
                    "expected_ref": "evals/expected/pf_side_effect.json",
                    "grader": "does.not.exist",
                }
            ],
        }
    )
    report = run_dataset(dataset, REPO_ROOT, GRADERS)
    assert report.failed == 1
    assert report.results[0].details["reason"] == "unknown grader"


def test_case_ref_cannot_escape_repository() -> None:
    dataset = EvalDataset.model_validate(
        json.loads(
            json.dumps(
                {
                    "dataset_id": "escape",
                    "dataset_version": "1.0.0",
                    "worker_id": "dw.test",
                    "cases": [
                        {
                            "case_id": "escape",
                            "category": "failure",
                            "description": "path traversal attempt",
                            "input_ref": "../outside.json",
                            "expected_ref": "evals/expected/pf_side_effect.json",
                            "grader": "runtime.side_effect_approval",
                        }
                    ],
                }
            )
        )
    )
    report = run_dataset(dataset, REPO_ROOT, GRADERS)
    assert report.failed == 1  # grader raised → failing case, never a crash
