"""Model provider wiring: which adapters exist and what the gateway routes to.

Two shapes are built here and they are not the same thing. ``ModelProviderAdapter``
is the structured-output path the gateway routes profile-by-profile;
``ChatModelFactory`` is the tool-calling path an agent loop runs on. A host can
have one without the other, so each is built and returned separately.
"""

from __future__ import annotations

from dw_agent_runtime.adapters.chat_model import (
    ChatModelFactory,
    MockChatModelFactory,
    OpenAICompatibleChatModelFactory,
)
from dw_agent_runtime.adapters.mock_model import MockModelAdapter
from dw_agent_runtime.adapters.openai_compatible import OpenAICompatibleAdapter
from dw_agent_runtime.adapters.openai_responses import OpenAIResponsesAdapter
from dw_agent_runtime.model.copy import RuntimeCopy
from dw_agent_runtime.model.gateway import ModelProviderAdapter
from dw_agent_runtime.model.profiles import ModelProfileRegistry, Provider
from dw_api.bootstrap.paths import MOCK_MODEL_FIXTURES
from dw_api.settings import ApiSettings
from dw_kernel.net_guard import ensure_allowed_outbound_url
from dw_kernel.ports import SystemClock
from dw_kernel.resilience import CircuitBreaker


class MockModelForbiddenError(RuntimeError):
    """A deployed profile asked for the fixture model."""


def checked_base_url(settings: ApiSettings, url: str) -> str:
    """SSRF guard: a provider endpoint must be public outside local development."""
    return ensure_allowed_outbound_url(
        url,
        allow_private=settings.outbound_allow_private(),
        allowed_hosts=tuple(settings.outbound_allowed_hosts),
    )


def build_model_adapters(settings: ApiSettings) -> dict[str, ModelProviderAdapter]:
    adapters: dict[str, ModelProviderAdapter] = {}
    if settings.model_provider == "mock":
        if settings.is_deployed:
            raise MockModelForbiddenError(
                f"the mock model provider is forbidden in the {settings.profile} profile"
            )
        adapters[Provider.MOCK] = MockModelAdapter(fixtures_dir=MOCK_MODEL_FIXTURES)
    if settings.openai_api_key and settings.openai_base_url:
        base_url = checked_base_url(settings, settings.openai_base_url)
        adapters[Provider.OPENAI_COMPATIBLE] = OpenAICompatibleAdapter(
            base_url=base_url,
            api_key=settings.openai_api_key,
            structured_mode=settings.openai_structured_mode,
            breaker=CircuitBreaker(clock=SystemClock(), name="model.openai_compatible"),
        )
        # Responses-dialect adapter (real OpenAI only): same credentials, but
        # /v1/responses returns the model's reasoning summary for visible
        # thinking. Profiles opt in with `provider: openai_responses`.
        adapters[Provider.OPENAI_RESPONSES] = OpenAIResponsesAdapter(
            base_url=base_url,
            api_key=settings.openai_api_key,
            strict_schema=settings.openai_strict_schema,
            breaker=CircuitBreaker(clock=SystemClock(), name="model.openai_responses"),
        )
    if settings.is_deployed and Provider.OPENAI_COMPATIBLE not in adapters:
        raise RuntimeError(f"the {settings.profile} profile requires a real model provider")
    return adapters


def build_chat_model_factory(
    settings: ApiSettings, profiles: ModelProfileRegistry, copy: RuntimeCopy
) -> ChatModelFactory | None:
    """The tool-calling model an agent loop runs on.

    ``None`` when no provider is configured: a graph with no model fails on its
    first question, and leaving it unregistered is how a host answers "I do not
    run that worker" instead of failing per request.
    """
    if settings.model_provider == "mock":
        if settings.is_deployed:
            raise MockModelForbiddenError(
                f"the mock model provider is forbidden in the {settings.profile} profile"
            )
        return MockChatModelFactory(mock_reply=copy.mock_reply)
    if not (settings.openai_api_key and settings.openai_base_url):
        return None
    return OpenAICompatibleChatModelFactory(
        profiles=profiles,
        base_url=checked_base_url(settings, settings.openai_base_url),
        api_key=settings.openai_api_key,
    )
