"""Run the model gate: a dataset's model tasks against one model profile, and a
pass/fail table per task (ticket ai-automation/06).

Usage:
    uv run python scripts/model_gate.py --profile qwen --mock
    uv run python scripts/model_gate.py --profile qwen          # live: calls the model

`--mock` grades the code with the dataset's own scripted readings: it shows the
script and the table work and is NEVER evidence for a route (its result says
`mode: mock` and goes to `evals/reports/<profile>.gate.json`, which nothing reads). A live run
calls the profile's routes (`structured_extraction` for a reading,
`reasoning` for a draft) through the same gateway the hosts build
(OPENAI_BASE_URL and OPENAI_API_KEY from the environment, never from a
committed file) and writes `evals/gates/<profile>.json`, which
`supply_chain_model_routes` reads when it loads: a task may move to the profile
only once its row there passes.

The dataset and the threshold come from the routes policy (one owner). Only
cases tagged with a model task (`task:extract.*`, `task:draft.*`) run: the
preparation cases grade code and need no model.

Each case is recorded too: its id, category, verdict, the grader's reason and
details, and every structured answer the model gave for it, so a failure is
read rather than guessed. Everything recorded passes `redact` first: account
numbers and similar identifiers, bearer tokens, and the provider key itself
never reach the file.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
LIVE_DIR = REPO_ROOT / "evals" / "gates"
# A mock result goes beside the eval reports, which git ignores and nothing reads.
MOCK_DIR = REPO_ROOT / "evals" / "reports"


class _NoUsage:
    """A gate run is not a tenant's spend: nothing is recorded."""

    async def record(self, *args: Any, **kwargs: Any) -> None:
        return None


def redact(value: Any, *, secrets: tuple[str, ...] = ()) -> Any:
    """`value` with every string masked of identifiers and secrets."""
    from dw_observability.redaction import REDACTED, redact_text
    from dw_supply_chain.domain.extraction import redact_identifiers

    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, REDACTED)
        return redact_identifiers(redact_text(value)).text
    if isinstance(value, dict):
        return {k: redact(v, secrets=secrets) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v, secrets=secrets) for v in value]
    return value


@dataclass
class Transcript:
    """The gateway a live run grades with, keeping each answer it returns
    (or the error a call raised) for the case being graded."""

    inner: Any
    outputs: list[Any] = field(default_factory=list)

    async def generate_structured(self, request: Any, output_type: Any, *, run_context: Any) -> Any:
        try:
            answer = await self.inner.generate_structured(
                request, output_type, run_context=run_context
            )
        except Exception as exc:
            self.outputs.append({"error": f"{type(exc).__name__}: {exc}"})
            raise
        self.outputs.append(answer.model_dump(mode="json"))
        return answer


def live_gateway(profile: str) -> Any:
    """The hosts' gateway for `profile`, on the environment's provider."""
    from dw_agent_runtime.adapters.model_stack import ModelProviderConfig, build_model_adapters
    from dw_agent_runtime.model.gateway import RoutingModelGateway
    from dw_agent_runtime.model.profiles import ModelProfileRegistry
    from dw_agent_runtime.model.prompts import load_shipped_prompts

    base_url, api_key = os.environ.get("OPENAI_BASE_URL"), os.environ.get("OPENAI_API_KEY")
    if not base_url or not api_key:
        raise SystemExit("a live gate needs OPENAI_BASE_URL and OPENAI_API_KEY in the environment")
    profiles = ModelProfileRegistry()
    profiles.load_directory(REPO_ROOT / "configs" / "models")
    adapters = build_model_adapters(
        ModelProviderConfig(
            provider="openai_compatible",
            model_profile=profile,
            profile="local",
            is_deployed=False,
            openai_api_key=api_key,
            openai_base_url=base_url,
        ),
        mock_fixtures_dir=REPO_ROOT / "evals" / "fixtures" / "mock_model",
    )
    return RoutingModelGateway(
        profiles=profiles,
        prompts=load_shipped_prompts(REPO_ROOT / "configs"),
        adapters=adapters,
        usage_recorder=_NoUsage(),
        default_profile=profiles.resolve(profile).profile_id,
    )


def _run_evals() -> ModuleType:
    """The eval composition root, loaded by path as `make` runs it: its grader
    table is the one the gate grades with."""
    spec = importlib.util.spec_from_file_location(
        "run_evals", Path(__file__).with_name("run_evals.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("run_evals", module)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, help="a profile of configs/models")
    parser.add_argument("--mock", action="store_true", help="scripted readings, not evidence")
    parser.add_argument("--out", type=Path, help="where the result JSON goes")
    args = parser.parse_args(argv)

    from dw_evals.gate import gate_result, gate_table, model_task_cases
    from dw_evals.graders import GraderContext
    from dw_evals.runner import CaseResult, EvalReport, load_dataset, run_dataset
    from dw_supply_chain.model_routes import SupplyChainModelRoutes
    from dw_supply_chain.policy_files import MODEL_ROUTES_POLICY_FILE

    run_evals = _run_evals()

    routes_policy = REPO_ROOT / "configs" / "policies" / MODEL_ROUTES_POLICY_FILE

    # Not `load_supply_chain_model_routes`: that checks the routes against the
    # gate results, and this run is what writes them.
    policy = SupplyChainModelRoutes.model_validate(
        yaml.safe_load(routes_policy.read_text(encoding="utf-8"))
    )
    dataset_id, version = policy.gate.dataset.split("@")
    dataset = model_task_cases(
        load_dataset(run_evals.DATASETS_DIR / f"{dataset_id}@{version}.json")
    )
    context = GraderContext(repo_root=REPO_ROOT, model_profile=args.profile)
    transcript = None if args.mock else Transcript(live_gateway(args.profile))
    context.model = transcript
    # One case at a time, so each answer is filed under the case that drew it.
    outputs: dict[str, list[Any]] = {}
    results: list[CaseResult] = []
    for case in dataset.cases:
        if transcript is not None:
            transcript.outputs = outputs.setdefault(case.case_id, [])
        one = dataset.model_copy(update={"cases": (case,)})
        results.extend(
            run_dataset(one, REPO_ROOT, run_evals.grader_table(), context=context).results
        )
    failed = sum(not r.passed for r in results)
    report = EvalReport(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        worker_id=dataset.worker_id,
        total=len(results),
        passed=len(results) - failed,
        failed=failed,
        security_coverage_ok=dataset.has_full_security_coverage(),
        results=tuple(results),
    )
    result = gate_result(
        report,
        dataset,
        profile=args.profile,
        mode="mock" if args.mock else "live",
        generated_at=datetime.now(UTC),
        outputs=outputs,
    )
    secrets = (os.environ.get("OPENAI_API_KEY", ""),)
    result = result.model_validate(
        {
            **result.model_dump(mode="json"),
            "cases": redact([c.model_dump(mode="json") for c in result.cases], secrets=secrets),
        }
    )
    out_dir = args.out or (MOCK_DIR if args.mock else LIVE_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / (f"{args.profile}.gate.json" if args.mock else f"{args.profile}.json")
    path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(gate_table(result, min_pass_rate=policy.gate.min_pass_rate, dataset=policy.gate.dataset))
    print(f"\n→ {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
