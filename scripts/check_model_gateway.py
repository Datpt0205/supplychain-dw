"""Probe the configured model gateway with one live call per generation route.

Usage (reads DW_MODEL_PROVIDER, DW_API_MODEL_PROFILE, OPENAI_BASE_URL and
OPENAI_API_KEY from the environment; `make check-model` exports `.env`):
    make check-model

Built the way the API and the worker build it (the shared `build_model_adapters`
of `dw_agent_runtime.adapters.model_stack`, the profiles under `configs/models`,
`default_profile` from the settings), so a pass here means the processes' own
model calls reach the model this deployment configured. The key
is never printed; the endpoint is shown by host only.

Exit code is non-zero when any route fails or echoes the wrong token.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[1]

# Inline rather than under configs/prompts: a probe of the wiring, not an
# artifact a run uses, so it has no place in the release manifest.
_PROMPT = {
    "schema_version": "1.0",
    "prompt_id": "platform.check_model",
    "version": "1.0.0",
    "system": "You answer with a single JSON object and nothing else.",
    "template": 'Return {{"echo": "<token>"}} where <token> is exactly: {token}',
    "variables": frozenset({"token"}),
}


class Echo(BaseModel):
    echo: str


async def probe() -> int:
    from dw_agent_runtime.adapters.model_stack import build_model_adapters
    from dw_agent_runtime.contracts import RunContext
    from dw_agent_runtime.model.gateway import InMemoryUsageRecorder, RoutingModelGateway
    from dw_agent_runtime.model.profiles import ModelProfileRegistry, ModelRoute
    from dw_agent_runtime.model.prompts import PromptArtifact, PromptRegistry
    from dw_agent_runtime.ports import ModelRequest, RouteKind
    from dw_api.bootstrap.models import model_provider_config
    from dw_api.bootstrap.paths import MOCK_MODEL_FIXTURES
    from dw_api.settings import ApiSettings

    settings = ApiSettings()
    profiles = ModelProfileRegistry()
    profiles.load_directory(REPO_ROOT / "configs" / "models")
    profile = profiles.resolve(settings.model_profile)
    prompts = PromptRegistry()
    prompts.register(PromptArtifact.model_validate(_PROMPT))
    recorder = InMemoryUsageRecorder()
    gateway = RoutingModelGateway(
        profiles=profiles,
        prompts=prompts,
        adapters=build_model_adapters(
            model_provider_config(settings), mock_fixtures_dir=MOCK_MODEL_FIXTURES
        ),
        usage_recorder=recorder,
        default_profile=profile.profile_id,
    )
    host = urlsplit(settings.openai_base_url or "").hostname or "(no OPENAI_BASE_URL)"
    print(f"provider={settings.model_provider} profile={profile.profile_id} endpoint={host}")

    failures = 0
    routes: dict[RouteKind, ModelRoute] = {
        "structured_extraction": profile.structured_extraction,
        "reasoning": profile.reasoning,
    }
    for kind, route in routes.items():
        token = uuid.uuid4().hex[:8]
        run_context = RunContext(
            run_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            actor_id=uuid.uuid4(),
            worker_id="platform.check_model",
            worker_version="1.0.0",
            channel="web",
            plan_id="probe",
            roles=frozenset(),
            scopes=frozenset(),
            trace_id="check-model",
        )
        request = ModelRequest(
            task=kind,
            prompt_id="platform.check_model",
            prompt_version="1.0.0",
            variables={"token": token},
            route_kind=kind,
        )
        label = f"{kind:<22} {route.provider}/{route.model}"
        try:
            output = await gateway.generate_structured(request, Echo, run_context=run_context)
        except Exception as exc:  # a probe reports every failure and moves on
            failures += 1
            message = str(exc)
            if settings.openai_api_key:  # never let a provider echo the key
                message = message.replace(settings.openai_api_key, "***")
            print(f"FAIL {label}: {type(exc).__name__}: {message}")
            continue
        _run, usage = recorder.records[-1]
        verdict = "ok  " if output.echo == token else "FAIL"
        failures += verdict == "FAIL"
        print(
            f"{verdict} {label} answered by {usage.model}: in={usage.input_tokens} "
            f"out={usage.output_tokens} cost=${usage.cost_usd:.5f} {usage.latency_ms:.0f}ms"
        )
    return 1 if failures else 0


def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    return asyncio.run(probe())


if __name__ == "__main__":
    raise SystemExit(main())
