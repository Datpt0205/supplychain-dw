"""Run evaluation datasets and write versioned reports.

Usage:
    uv run python scripts/run_evals.py --smoke              # all datasets
    uv run python scripts/run_evals.py --dataset evals/datasets/platform@1.2.0.json

Exit code is non-zero when any case fails or a dataset lacks full security
coverage (prompt injection, cross-tenant attack, missing evidence).

This script is the eval composition root: `grader_table` is the one place the
grader table is built, from the platform's graders plus each bounded context's,
the same way `apps/api/.../bootstrap/wiring.py` and `apps/worker/.../main.py`
are where a context joins the API and the worker. `dw_evals` imports no
context; a context registers its graders here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dw_evals.graders import Grader

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASETS_DIR = REPO_ROOT / "evals" / "datasets"
REPORTS_DIR = REPO_ROOT / "evals" / "reports"


def grader_table() -> dict[str, Grader]:
    """Every grader a dataset may name. Two tables claiming one name stop the
    run (`merge_graders` raises, naming it)."""
    from dw_evals.graders import GRADERS, merge_graders

    tables = [GRADERS]
    # ---- BOUNDED CONTEXT GRADERS REGISTER HERE ----------------------------
    from dw_supply_chain.testing.eval_graders import SUPPLY_CHAIN_GRADERS

    tables.append(SUPPLY_CHAIN_GRADERS)
    return merge_graders(*tables)


def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true", help="run every dataset in evals/datasets")
    parser.add_argument("--dataset", type=Path, help="run a single dataset file")
    args = parser.parse_args()

    from dw_evals.runner import load_dataset, run_dataset, write_report

    if args.dataset:
        paths = [args.dataset]
    else:
        paths = sorted(DATASETS_DIR.glob("*.json"))
        if not args.smoke:
            parser.error("pass --smoke or --dataset <file>")
    if not paths:
        print("ERROR: no datasets found under evals/datasets", file=sys.stderr)
        return 1

    graders = grader_table()
    exit_code = 0
    for path in paths:
        dataset = load_dataset(path)
        report = run_dataset(dataset, REPO_ROOT, graders)
        report_path = write_report(report, REPORTS_DIR)
        status = "PASS" if report.ok else "FAIL"
        print(
            f"[{status}] {report.dataset_id}@{report.dataset_version} "
            f"({report.worker_id}): {report.passed}/{report.total} cases, "
            f"security coverage {'ok' if report.security_coverage_ok else 'MISSING'} "
            f"→ {report_path.relative_to(REPO_ROOT)}"
        )
        for result in report.results:
            if not result.passed:
                print(f"    ✗ {result.case_id} [{result.category}] {result.details}")
        if not report.ok:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
