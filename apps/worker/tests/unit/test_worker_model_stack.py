"""The worker builds its model stack with the API's builder (`model_stack`).

One builder for both composition roots, so a model call made in the worker (a
product's chat command) spends against the same per-run ledger and daily spend
guard, and is refused the same way in a deployed profile.
"""

from __future__ import annotations

from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_agent_runtime.adapters.model_stack import ModelStack
from dw_kernel.ports import SystemClock
from dw_observability.telemetry import NullTelemetry
from dw_worker.composition import build_model_stack_for, model_provider_config
from dw_worker.settings import WorkerSettings

pytestmark = pytest.mark.unit

_SESSIONS = cast(async_sessionmaker[AsyncSession], object())


def test_the_worker_maps_its_settings_onto_the_shared_builder() -> None:
    settings = WorkerSettings(  # type: ignore[call-arg]
        model_provider="openai_compatible",
        openai_api_key="sk-test",
        openai_base_url="https://gateway.example/v1",
        outbound_allowed_hosts=["gateway.example"],
    )
    config = model_provider_config(settings)
    assert config.provider == "openai_compatible"
    assert config.openai_base_url == "https://gateway.example/v1"
    assert config.outbound_allowed_hosts == ("gateway.example",)
    assert config.is_deployed is False


def test_the_worker_builds_one_gateway_and_one_ledger() -> None:
    stack = build_model_stack_for(
        WorkerSettings(model_provider="mock"),  # type: ignore[call-arg]
        _SESSIONS,
        clock=SystemClock(),
        telemetry=NullTelemetry(),
    )
    assert isinstance(stack, ModelStack)
    assert stack.gateway is not None and stack.budget is not None


def test_a_deployed_worker_is_refused_the_fixture_model() -> None:
    settings = WorkerSettings(  # type: ignore[call-arg]
        profile="production", model_provider="mock"
    )
    with pytest.raises(RuntimeError, match="mock"):
        build_model_stack_for(settings, _SESSIONS, clock=SystemClock(), telemetry=NullTelemetry())
