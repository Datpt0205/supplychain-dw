"""Which model profile each Supply Chain model task runs on (ADR 0025 point 10;
ticket ai-automation/06).

A task is one model-backed reading (`extract.<doc_type>`, one per
`EXTRACTION_SPECS` entry) or one model-written draft (`draft.<kind>`,
`DRAFTING_TASKS`, tickets ai-automation/07-10). A task not routed runs on the process's own profile
(`luna` for Elmich). A route to a profile in `ungated_profiles` is the
deployment's choice; a route to any other profile (a Qwen, say) is refused when
the policy loads unless that profile's LIVE run of the gate dataset passed the
task (`evals/gates/<profile>.json`, written by `scripts/model_gate.py`): no
security case failed and at least `gate.min_pass_rate` of the task's cases
passed. The threshold has one owner, this policy; the result file holds scores,
not a verdict.

Platform-wide (`policy_files.MODEL_ROUTES_POLICY_FILE`), read by the worker
that runs the extraction and drafting lanes. A tenant override comes with a
tenant that asks for one: the gate is about the model, not the tenant.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dw_agent_runtime.model.gates import ModelGateResult, load_gate_results
from dw_kernel.errors import ConfigError
from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.extraction import EXTRACTION_SPECS

__all__ = [
    "DRAFTING_TASKS",
    "MODEL_ROUTES_POLICY_ID",
    "MODEL_TASKS",
    "PROPOSAL_LIST_TASK",
    "SAMPLE_EVALUATION_TASK",
    "SUPPLIER_MESSAGE_TASK",
    "SupplyChainModelRoutes",
    "extraction_task",
    "load_supply_chain_model_routes",
]

MODEL_ROUTES_POLICY_ID = "supply_chain_model_routes"


def extraction_task(doc_type: DocumentType) -> str:
    return f"extract.{doc_type.value}"


# A list of proposed products, read by its own lane (ticket ai-automation/08).
PROPOSAL_LIST_TASK = extraction_task(DocumentType.PROPOSAL_LIST)
# The drafts a model writes, each graded by its own gate cases.
SUPPLIER_MESSAGE_TASK = "draft.supplier_message"
SAMPLE_EVALUATION_TASK = "draft.sample_evaluation"
DRAFTING_TASKS = frozenset({SUPPLIER_MESSAGE_TASK, SAMPLE_EVALUATION_TASK})

# Every model task of this context, by name: what a route and a gate case name.
MODEL_TASKS = (
    frozenset(extraction_task(t) for t in EXTRACTION_SPECS)
    | frozenset({PROPOSAL_LIST_TASK})
    | DRAFTING_TASKS
)


class ModelGate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # The dataset a profile is gated on, `id@version`.
    dataset: str = Field(pattern=r"^[a-z0-9_]+@\d+\.\d+\.\d+$")
    min_pass_rate: float = Field(gt=0, le=1)


class SupplyChainModelRoutes(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    policy_id: str
    policy_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    ungated_profiles: frozenset[str] = Field(min_length=1)
    gate: ModelGate
    routes: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _only_tasks_that_exist(self) -> SupplyChainModelRoutes:
        unknown = sorted(set(self.routes) - MODEL_TASKS)
        if unknown:
            raise ValueError(f"routes name tasks that do not exist: {unknown}")
        return self

    def gated_routes(self) -> dict[str, str]:
        return {t: p for t, p in self.routes.items() if p not in self.ungated_profiles}

    def require_gates(self, results: Mapping[str, ModelGateResult]) -> None:
        """Refuse, naming them, the routes to a profile that has not passed
        the task on the gate dataset in a live run."""
        failing = sorted(
            f"{task} -> {profile}"
            for task, profile in self.gated_routes().items()
            if (result := results.get(profile)) is None
            or not result.passed(
                task, dataset=self.gate.dataset, min_pass_rate=self.gate.min_pass_rate
            )
        )
        if failing:
            raise ConfigError(
                "a task may run on an ungated profile only after it passed the model gate: "
                + ", ".join(failing)
            )

    def profile_for(self, task: str) -> str | None:
        """The profile a task runs on, or None for the process's own."""
        if task not in MODEL_TASKS:
            raise ValueError(f"{task} is not a model task")
        return self.routes.get(task)

    def extraction_routes(self) -> dict[DocumentType, str]:
        """The profile of each extraction task that has a route."""
        return {
            doc_type: self.routes[extraction_task(doc_type)]
            for doc_type in EXTRACTION_SPECS
            if extraction_task(doc_type) in self.routes
        }


def load_supply_chain_model_routes(path: Path, gates_dir: Path) -> SupplyChainModelRoutes:
    """The policy, checked against the gate results committed in `gates_dir`."""
    routes = SupplyChainModelRoutes.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    routes.require_gates(load_gate_results(gates_dir))
    return routes
