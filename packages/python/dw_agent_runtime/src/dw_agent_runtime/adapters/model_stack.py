"""The one place a process builds its model gateway, spend ledger and usage
recorders.

The API calls models in its requests and runs; the worker calls them in lanes no
request ever touched (the Zalo poll lane reads a chat message with one). Both
must spend against the same per-run ceiling, record into the same daily spend
guard, and refuse once the tenant's plan day is spent. Two builders agree only
until one of them is edited, so there is one — the same reason
`dw_knowledge.adapters.embedding_factory` exists for embedders.

It takes a `ModelProviderConfig`, not a settings object: each process has its
own settings class, and mapping it is the composition root's business.

What it builds:

- the provider adapters (`build_model_adapters`), with the SSRF guard on the
  endpoint and the mock refused in a deployed profile;
- ONE `RunBudgetLedger` for the process, shared by the gateway, an agent's
  budget middleware and the runner that frees a run's entry;
- the usage recorders: the daily spend guard and telemetry, behind the
  composite that lets no recorder take a call down;
- `ModelStack.one_call(allowance)`: the gateway for a caller whose run is one
  call, which checks the plan's daily allowance first and frees its ledger
  entry after (`SingleCallModelGateway`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_agent_runtime.adapters.mock_model import MockModelAdapter
from dw_agent_runtime.adapters.openai_compatible import OpenAICompatibleAdapter
from dw_agent_runtime.adapters.openai_responses import OpenAIResponsesAdapter
from dw_agent_runtime.adapters.spend_guard import SqlSpendGuardRecorder
from dw_agent_runtime.adapters.telemetry_usage import TelemetryUsageRecorder
from dw_agent_runtime.adapters.usage_recorders import CompositeUsageRecorder
from dw_agent_runtime.allowance import DailyAllowance
from dw_agent_runtime.model.budget import RunBudgetLedger
from dw_agent_runtime.model.gateway import (
    ModelProviderAdapter,
    RoutingModelGateway,
    UsageRecorderPort,
)
from dw_agent_runtime.model.profiles import ModelProfileRegistry, Provider
from dw_agent_runtime.model.prompts import PromptRegistry
from dw_agent_runtime.model.single_call import SingleCallModelGateway
from dw_kernel.net_guard import ensure_allowed_outbound_url
from dw_kernel.ports import SystemClock, UtcClock
from dw_kernel.resilience import CircuitBreaker
from dw_observability.telemetry import TelemetryPort


class MockModelForbiddenError(RuntimeError):
    """A deployed profile asked for the fixture model."""


@dataclass(frozen=True)
class ModelProviderConfig:
    """What a process's settings say about its model provider."""

    # "mock" (fixtures, local/test only) or anything else for a real gateway.
    provider: str
    # Which profile a request naming none runs on.
    model_profile: str
    profile: str
    is_deployed: bool
    openai_api_key: str | None
    openai_base_url: str | None
    openai_structured_mode: str = "json_schema"
    openai_strict_schema: bool = True
    outbound_allowed_hosts: tuple[str, ...] = field(default_factory=tuple)

    @property
    def allow_private_endpoints(self) -> bool:
        """Local/test may call a localhost provider; a deployed profile never does."""
        return not self.is_deployed


def build_model_adapters(
    config: ModelProviderConfig, *, mock_fixtures_dir: Path
) -> dict[str, ModelProviderAdapter]:
    adapters: dict[str, ModelProviderAdapter] = {}
    if config.provider == "mock":
        if config.is_deployed:
            raise MockModelForbiddenError(
                f"the mock model provider is forbidden in the {config.profile} profile"
            )
        adapters[Provider.MOCK] = MockModelAdapter(fixtures_dir=mock_fixtures_dir)
    if config.openai_api_key and config.openai_base_url:
        # SSRF guard: a provider endpoint must be public outside local development.
        base_url = ensure_allowed_outbound_url(
            config.openai_base_url,
            allow_private=config.allow_private_endpoints,
            allowed_hosts=config.outbound_allowed_hosts,
        )
        adapters[Provider.OPENAI_COMPATIBLE] = OpenAICompatibleAdapter(
            base_url=base_url,
            api_key=config.openai_api_key,
            structured_mode=config.openai_structured_mode,
            breaker=CircuitBreaker(clock=SystemClock(), name="model.openai_compatible"),
        )
        # Responses-dialect adapter (real OpenAI only): same credentials, but
        # /v1/responses returns the model's reasoning summary for visible
        # thinking. Profiles opt in with `provider: openai_responses`.
        adapters[Provider.OPENAI_RESPONSES] = OpenAIResponsesAdapter(
            base_url=base_url,
            api_key=config.openai_api_key,
            strict_schema=config.openai_strict_schema,
            breaker=CircuitBreaker(clock=SystemClock(), name="model.openai_responses"),
        )
    if config.is_deployed and Provider.OPENAI_COMPATIBLE not in adapters:
        raise RuntimeError(f"the {config.profile} profile requires a real model provider")
    return adapters


@dataclass(frozen=True)
class ModelStack:
    gateway: RoutingModelGateway
    # ONE per process. Two ledgers would split a run's spend so neither half
    # reaches the ceiling.
    budget: RunBudgetLedger
    usage_recorder: UsageRecorderPort

    def one_call(self, allowance: DailyAllowance) -> SingleCallModelGateway:
        """The gateway for a caller whose run is this one call: the plan's daily
        allowance checked before it, the ledger entry freed after it."""
        return SingleCallModelGateway(inner=self.gateway, ledger=self.budget, allowance=allowance)


def build_model_stack(
    config: ModelProviderConfig,
    *,
    profiles: ModelProfileRegistry,
    prompts: PromptRegistry,
    session_factory: async_sessionmaker[AsyncSession],
    clock: UtcClock,
    telemetry: TelemetryPort,
    mock_fixtures_dir: Path,
) -> ModelStack:
    # `tenant_daily_spend_guard` is one row per tenant per day, read by nothing
    # but the plan's spend check. The composite stays because a recorder that
    # raises must not take the call down.
    recorder = CompositeUsageRecorder(
        [
            SqlSpendGuardRecorder(session_factory=session_factory, clock=clock),
            TelemetryUsageRecorder(telemetry),
        ]
    )
    budget = RunBudgetLedger()
    gateway = RoutingModelGateway(
        profiles=profiles,
        prompts=prompts,
        adapters=build_model_adapters(config, mock_fixtures_dir=mock_fixtures_dir),
        usage_recorder=recorder,
        # Resolved here so a profile id nobody registered fails at startup,
        # not on the first model call.
        default_profile=profiles.resolve(config.model_profile).profile_id,
        budget=budget,
    )
    return ModelStack(gateway=gateway, budget=budget, usage_recorder=recorder)
