"""A model gate from an eval report: each case tagged `task:<name>` scores
toward that task (ticket ai-automation/06; upstream candidate).

`model_task_cases` keeps the cases a model is graded on (a task tag naming a
model task: `task:extract.*` reads a file, `task:draft.*` writes a draft);
`gate_result` turns a report into the per-task scores
`dw_agent_runtime.model.gates.ModelGateResult` stores;
`gate_table` prints it with the reader's threshold, so the table and the
loader agree because both ask `ModelGateResult.passed`.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from dw_agent_runtime.model.gates import GateMode, ModelGateResult, TaskScore
from dw_evals.dataset import SECURITY_CATEGORIES, EvalCase, EvalDataset
from dw_evals.runner import EvalReport

TASK_TAG = "task:"
# A task a model does: reading a file into fields, or writing a draft.
MODEL_TASK_PREFIXES = ("extract.", "draft.")


def task_of(case: EvalCase) -> str | None:
    return next((t[len(TASK_TAG) :] for t in sorted(case.tags) if t.startswith(TASK_TAG)), None)


def model_task_cases(dataset: EvalDataset) -> EvalDataset:
    """The cases a model profile is graded on."""
    kept = tuple(
        c for c in dataset.cases if (task := task_of(c)) and task.startswith(MODEL_TASK_PREFIXES)
    )
    return dataset.model_copy(update={"cases": kept})


def gate_result(
    report: EvalReport,
    dataset: EvalDataset,
    *,
    profile: str,
    mode: GateMode,
    generated_at: datetime,
) -> ModelGateResult:
    tasks = {c.case_id: task_of(c) for c in dataset.cases}
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for result in report.results:
        task = tasks.get(result.case_id)
        if task is None:
            continue
        score = totals[task]
        score[0] += 1
        score[1] += int(result.passed)
        score[2] += int(not result.passed and result.category in SECURITY_CATEGORIES)
    return ModelGateResult(
        profile=profile,
        dataset=f"{dataset.dataset_id}@{dataset.dataset_version}",
        mode=mode,
        generated_at=generated_at,
        tasks={
            task: TaskScore(total=t, passed=p, security_failed=s)
            for task, (t, p, s) in sorted(totals.items())
        },
    )


def gate_table(result: ModelGateResult, *, min_pass_rate: float, dataset: str) -> str:
    """Markdown: one row per task, and whether a route may use the profile."""
    lines = [
        f"Model gate: `{result.profile}` on `{result.dataset}` ({result.mode}),"
        f" threshold {min_pass_rate:.0%}, no security case may fail",
        "",
        "| Task | Passed | Security failed | Gate |",
        "| ---- | ------ | --------------- | ---- |",
    ]
    # A mock run says what a live one with the same scores would get, and that
    # it is not one: `ModelGateResult.passed` refuses every mock result.
    scored = result if result.mode == "live" else result.model_copy(update={"mode": "live"})
    for task, score in result.tasks.items():
        verdict = (
            "pass" if scored.passed(task, dataset=dataset, min_pass_rate=min_pass_rate) else "FAIL"
        )
        if result.mode != "live":
            verdict += " (mock: not evidence)"
        lines.append(
            f"| {task} | {score.passed}/{score.total} | {score.security_failed} | {verdict} |"
        )
    return "\n".join(lines)
