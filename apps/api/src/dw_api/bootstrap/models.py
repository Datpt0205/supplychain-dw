"""Model provider wiring: which adapters exist and what the gateway routes to.

Two shapes are wired here and they are not the same thing. The structured-output
path the gateway routes profile-by-profile is built by the shared
``dw_agent_runtime.adapters.model_stack`` (the worker builds its own with it);
this module only maps the settings onto it (``model_provider_config``).
``ChatModelFactory`` is the tool-calling path an agent loop runs on. A host can
have one without the other, so each is built and returned separately.
"""

from __future__ import annotations

from dw_agent_runtime.adapters.chat_model import (
    ChatModelFactory,
    MockChatModelFactory,
    OpenAICompatibleChatModelFactory,
)
from dw_agent_runtime.adapters.model_stack import (
    MockModelForbiddenError,
    ModelProviderConfig,
)
from dw_agent_runtime.model.copy import RuntimeCopy
from dw_agent_runtime.model.profiles import ModelProfileRegistry
from dw_api.settings import ApiSettings
from dw_kernel.net_guard import ensure_allowed_outbound_url


def checked_base_url(settings: ApiSettings, url: str) -> str:
    """SSRF guard: a provider endpoint must be public outside local development."""
    return ensure_allowed_outbound_url(
        url,
        allow_private=settings.outbound_allow_private(),
        allowed_hosts=tuple(settings.outbound_allowed_hosts),
    )


def model_provider_config(settings: ApiSettings) -> ModelProviderConfig:
    """This process's settings, read for the shared model builder
    (`dw_agent_runtime.adapters.model_stack`), which the worker calls too."""
    return ModelProviderConfig(
        provider=settings.model_provider,
        model_profile=settings.model_profile,
        profile=settings.profile,
        is_deployed=settings.is_deployed,
        openai_api_key=settings.openai_api_key,
        openai_base_url=settings.openai_base_url,
        openai_structured_mode=settings.openai_structured_mode,
        openai_strict_schema=settings.openai_strict_schema,
        outbound_allowed_hosts=tuple(settings.outbound_allowed_hosts),
    )


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
