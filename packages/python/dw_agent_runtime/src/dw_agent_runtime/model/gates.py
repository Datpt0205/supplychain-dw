"""A model gate's result: how one model profile did on one eval dataset, per
task (ticket ai-automation/06; upstream candidate).

Written by `scripts/model_gate.py` (`dw_evals.gate` computes it), committed under
`evals/gates/<profile>.json`, and read where a context decides which profile a
task may run on: a task leaves the profiles a deployment trusts outright only
for a profile whose LIVE run of the gate dataset passed that task. A mock run
exercises the script and is never evidence (`ModelGateResult.passed` refuses it).

The threshold is not stored here as a verdict: the reader passes its own, so
the policy that names the threshold is the one owner of it.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

GateMode = Literal["live", "mock"]


class TaskScore(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    # Security cases (prompt injection, cross-tenant, missing evidence) that failed.
    security_failed: int = Field(ge=0)


class GateCase(BaseModel):
    """One graded case of a run: what the grader said and what the model
    answered (redacted by the writer), so a failure can be read, not guessed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(min_length=1)
    task: str = Field(min_length=1)
    category: str = Field(min_length=1)
    passed: bool
    # The grader's reason and details when it failed; None when it passed.
    reason: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    # Each structured answer the model gave for the case, in order (an error's
    # text when a call failed); empty for a case refused before any call.
    outputs: tuple[Any, ...] = ()


class ModelGateResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: str = Field(min_length=1)
    dataset: str = Field(pattern=r"^[a-z0-9_]+@\d+\.\d+\.\d+$")
    mode: GateMode
    generated_at: datetime
    tasks: dict[str, TaskScore]
    # Per case, for reading a result; the verdict reads `tasks` only.
    cases: tuple[GateCase, ...] = ()

    def passed(self, task: str, *, dataset: str, min_pass_rate: float) -> bool:
        """Whether `task` may run on this profile: a live run of `dataset`,
        every case of the task graded, no security case failed, and at least
        `min_pass_rate` of its cases passed."""
        score = self.tasks.get(task)
        return (
            self.mode == "live"
            and self.dataset == dataset
            and score is not None
            and score.total > 0
            and score.security_failed == 0
            and score.passed >= min_pass_rate * score.total
        )


def load_gate_results(directory: Path) -> dict[str, ModelGateResult]:
    """Every result in `directory`, by profile; none when it does not exist."""
    if not directory.is_dir():
        return {}
    results: dict[str, ModelGateResult] = {}
    for path in sorted(directory.glob("*.json")):
        result = ModelGateResult.model_validate(json.loads(path.read_text(encoding="utf-8")))
        results[result.profile] = result
    return results
