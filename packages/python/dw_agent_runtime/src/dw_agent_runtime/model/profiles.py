"""Model profile configuration: routing + budgets per profile."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from dw_agent_runtime.registry import ConfigError
from dw_kernel.errors import NotFoundError
from dw_kernel.overlay import TenantOverlay


class Provider(StrEnum):
    """Providers this repo ships an adapter for. ``ModelRoute.provider`` stays
    ``str``: the adapter registry is an open seam."""

    MOCK = "mock"
    OPENAI_COMPATIBLE = "openai_compatible"
    OPENAI_RESPONSES = "openai_responses"


class ModelRoute(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    model: str
    timeout_seconds: int = Field(default=60, gt=0, le=600)
    reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = None
    # Chat routes on the Responses dialect only: ask the provider to return a
    # readable summary of the reasoning it already did and charged for. Off by
    # default because the summary is extra output tokens on every call, and it
    # is worth paying for only where a human watches the answer being written.
    reasoning_summary: Literal["auto", "concise", "detailed"] | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    # Embedding routes only: the vector width to build the index at. Required
    # there because it decides the Qdrant collection's shape, and a shape is not
    # something to discover at runtime from whatever the provider returned.
    dimensions: int | None = Field(default=None, gt=0)
    # The most input one call on this route is sent. Read by context compaction,
    # which splits what it summarises into calls no larger than this; a route
    # used as a summariser must declare it, and compaction refuses to build
    # otherwise rather than fall back to a number nobody chose.
    max_input_tokens: int | None = Field(default=None, gt=0)
    # What this route costs, so a run's cost ceiling is computable. Declared per
    # route because price is a property of the model and the provider serving
    # it. Absent means unpriced: such a route is bounded by the token ceiling
    # rather than the cost one, which the budget code says out loud.
    price_per_million_input: float | None = Field(default=None, ge=0.0)
    price_per_million_output: float | None = Field(default=None, ge=0.0)


class ModelBudgets(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_input_tokens_per_run: int = Field(default=120_000, gt=0)
    max_cost_usd_per_run: float = Field(default=2.0, gt=0)


class ModelProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    profile_id: str
    routing_policy_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    structured_extraction: ModelRoute
    reasoning: ModelRoute
    chat: ModelRoute | None = None
    deep_reasoning: ModelRoute | None = None
    fallback: ModelRoute | None = None
    # Retrieval, not generation. Absent means this profile cannot build an index,
    # which is deliberate for the mock profiles: an embedding route that silently
    # fell back to a default would produce a collection nobody chose the width of.
    embedding: ModelRoute | None = None
    budgets: ModelBudgets = ModelBudgets()

    @model_validator(mode="after")
    def _embedding_route_declares_its_width(self) -> ModelProfile:
        if self.embedding is not None and self.embedding.dimensions is None:
            raise ValueError("an embedding route must declare `dimensions`")
        return self


@dataclass
class ModelProfileRegistry:
    # Platform profiles plus per-tenant overrides. A customer on their own
    # model contract, or one whose data may not leave a region, routes through
    # its own profile rather than through a second deployment.
    _profiles: TenantOverlay[str, ModelProfile] = field(default_factory=TenantOverlay)

    def load_directory(self, directory: Path, *, tenant_id: UUID | None = None) -> None:
        for path in sorted(directory.glob("*.yaml")):
            self.load_file(path, tenant_id=tenant_id)

    def load_file(self, path: Path, *, tenant_id: UUID | None = None) -> ModelProfile:
        try:
            profile = ModelProfile.model_validate(yaml.safe_load(path.read_bytes()))
        except (yaml.YAMLError, ValidationError) as exc:
            raise ConfigError(f"model profile {path.name} invalid: {exc}") from exc
        self.register(profile, tenant_id=tenant_id)
        return profile

    def register(self, profile: ModelProfile, *, tenant_id: UUID | None = None) -> None:
        if self._profiles.existing(profile.profile_id, tenant_id=tenant_id) is not None:
            raise ConfigError(f"model profile already registered: {profile.profile_id}")
        self._profiles.put(profile.profile_id, profile, tenant_id=tenant_id)

    def resolve(self, profile_id: str, *, tenant_id: UUID | None = None) -> ModelProfile:
        profile = self._profiles.get(profile_id, tenant_id=tenant_id)
        if profile is None:
            raise NotFoundError("model profile not registered", details={"profile_id": profile_id})
        return profile
