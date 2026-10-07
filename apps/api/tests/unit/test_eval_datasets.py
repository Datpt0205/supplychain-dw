"""Every dataset passes with the grader table `make eval-smoke` builds.

Here rather than in `dw_evals`: the table is the eval composition root's
(`scripts/run_evals.py`, `grader_table`) and includes the bounded contexts'
graders, which `dw_evals` must not import. This package already depends on every
context, so it can load the script and run the same table the script runs.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from dw_evals.dataset import EvalDataset
from dw_evals.runner import load_dataset, run_dataset

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[4]
DATASETS = sorted((REPO_ROOT / "evals" / "datasets").glob("*.json"))


def _load_script() -> ModuleType:  # scripts/ is loaded by path, as make runs it
    spec = importlib.util.spec_from_file_location(
        "run_evals", REPO_ROOT / "scripts" / "run_evals.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_evals"] = module
    spec.loader.exec_module(module)
    return module


def test_every_bounded_context_dataset_is_found() -> None:
    assert {p.name for p in DATASETS} >= {"platform@1.1.0.json"}
    assert any(p.name.startswith("supply_chain@") for p in DATASETS)


@pytest.mark.parametrize("path", DATASETS, ids=lambda p: p.stem)
def test_smoke_dataset_passes(path: Path) -> None:
    report = run_dataset(load_dataset(path), REPO_ROOT, _load_script().grader_table())
    failures = [r for r in report.results if not r.passed]
    assert not failures, f"failing cases: {[(f.case_id, f.details) for f in failures]}"
    assert report.ok


def test_a_grader_name_the_table_lacks_fails_its_case() -> None:
    dataset = EvalDataset.model_validate(
        {
            "dataset_id": "unregistered",
            "dataset_version": "1.0.0",
            "worker_id": "dw.test",
            "cases": [
                {
                    "case_id": "unregistered",
                    "category": "normal",
                    "description": "a context grader nobody registered",
                    "input_ref": "evals/fixtures/cases/pf_side_effect.json",
                    "expected_ref": "evals/expected/pf_side_effect.json",
                    "grader": "acme.not_registered",
                }
            ],
        }
    )
    report = run_dataset(dataset, REPO_ROOT, _load_script().grader_table())
    assert report.results[0].details["reason"] == "unknown grader"
