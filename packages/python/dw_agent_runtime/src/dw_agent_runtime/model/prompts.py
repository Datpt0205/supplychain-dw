"""Versioned prompt bundles loaded from configs/prompts.

A prompt artifact is immutable: changing wording means a new version file.
Rendering is strict — missing or unknown variables fail fast.

**Every variable is untrusted unless the template says otherwise.** The registry
wraps each value it interpolates in ``<input name="...">`` with ``&``, ``<`` and
``>`` escaped, so a value cannot close its block and continue as the prompt's
own words. Containment used to be each template's job: a template that forgot
the block, or a document carrying ``</input>``, was enough. A template opts a
variable out, by name and with the reason, only for a value code builds (a
date, an identifier); ``raw_variables`` is that list.
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from dw_agent_runtime.registry import ConfigError
from dw_kernel.errors import DomainError, NotFoundError
from dw_kernel.overlay import TenantOverlay

_VARIABLE_PATTERN = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
UNTRUSTED_TAG = "input"


def contain_untrusted(name: str, value: str) -> str:
    """The one form an untrusted value takes inside a prompt.

    Escaped rather than stripped: the model still reads what the document said,
    and nothing in it can open or close a block. ``name`` is a template
    placeholder, which the pattern above limits to ``[a-z0-9_]``.
    """
    escaped = html.escape(value, quote=False)
    return f'<{UNTRUSTED_TAG} name="{name}">\n{escaped}\n</{UNTRUSTED_TAG}>'


class PromptArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    prompt_id: str
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    description: str = ""
    system: str
    template: str
    variables: frozenset[str] = frozenset()
    # Variables interpolated as written, each with the reason it is safe to:
    # a value code builds, never one a request, a document or a model wrote.
    raw_variables: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _raw_is_declared_and_justified(self) -> PromptArtifact:
        unknown = set(self.raw_variables) - set(self.variables)
        if unknown:
            raise ValueError(f"raw_variables not among variables: {sorted(unknown)}")
        unjustified = [name for name, why in self.raw_variables.items() if not why.strip()]
        if unjustified:
            raise ValueError(f"raw variable without a reason: {sorted(unjustified)}")
        # The registry wraps. A template doing it too nests one block in another
        # and teaches the next author that containment is the template's job.
        if f"<{UNTRUSTED_TAG}" in self.template or f"</{UNTRUSTED_TAG}" in self.template:
            raise ValueError(f"template must not write <{UNTRUSTED_TAG}> itself")
        return self

    def declared_placeholders(self) -> frozenset[str]:
        return frozenset(_VARIABLE_PATTERN.findall(self.template))


@dataclass(frozen=True)
class RenderedPrompt:
    prompt_id: str
    version: str
    system: str
    user: str
    checksum: str


@dataclass
class PromptRegistry:
    # Platform prompts plus per-tenant overrides: a customer whose wording,
    # rules or examples differ gets its own artifact, not a branch.
    _prompts: TenantOverlay[tuple[str, str], tuple[PromptArtifact, str]] = field(
        default_factory=TenantOverlay
    )

    def load_directory(self, directory: Path, *, tenant_id: UUID | None = None) -> None:
        for path in sorted(directory.rglob("*.yaml")):
            self.load_file(path, tenant_id=tenant_id)

    def load_file(self, path: Path, *, tenant_id: UUID | None = None) -> PromptArtifact:
        raw = path.read_bytes()
        try:
            artifact = PromptArtifact.model_validate(yaml.safe_load(raw))
        except (yaml.YAMLError, ValidationError) as exc:
            raise ConfigError(f"prompt artifact {path.name} invalid: {exc}") from exc

        placeholders = artifact.declared_placeholders()
        if placeholders != artifact.variables:
            raise ConfigError(
                f"prompt {artifact.prompt_id}@{artifact.version}: declared variables "
                f"{sorted(artifact.variables)} != template placeholders {sorted(placeholders)}"
            )
        self.register(artifact, checksum=hashlib.sha256(raw).hexdigest(), tenant_id=tenant_id)
        return artifact

    def register(
        self,
        artifact: PromptArtifact,
        *,
        checksum: str | None = None,
        tenant_id: UUID | None = None,
    ) -> None:
        key = (artifact.prompt_id, artifact.version)
        # Within one layer only: a tenant overriding a platform prompt is the
        # feature; two files claiming one version inside a layer is the mistake.
        if self._prompts.existing(key, tenant_id=tenant_id) is not None:
            raise ConfigError(f"prompt already registered: {key[0]}@{key[1]}")
        digest = checksum or hashlib.sha256(artifact.model_dump_json().encode()).hexdigest()
        self._prompts.put(key, (artifact, digest), tenant_id=tenant_id)

    def has(self, prompt_id: str, version: str) -> bool:
        """Whether the platform layer holds this version.

        The platform layer only: a tenant's override is a variation of a prompt
        the platform ships, and resolution falls back to the platform, so a
        version only some tenant holds is one every other tenant cannot render.
        """
        return self._prompts.existing((prompt_id, version)) is not None

    def render(
        self,
        prompt_id: str,
        version: str,
        variables: dict[str, str],
        *,
        tenant_id: UUID | None = None,
    ) -> RenderedPrompt:
        entry = self._prompts.get((prompt_id, version), tenant_id=tenant_id)
        if entry is None:
            raise NotFoundError(
                "prompt version not registered",
                details={"prompt_id": prompt_id, "version": version},
            )
        artifact, checksum = entry
        provided = frozenset(variables)
        if provided != artifact.variables:
            raise DomainError(
                "prompt variables mismatch",
                details={
                    "prompt_id": prompt_id,
                    "expected": sorted(artifact.variables),
                    "provided": sorted(provided),
                },
            )
        return RenderedPrompt(
            prompt_id=prompt_id,
            version=version,
            system=artifact.system,
            user=artifact.template.format(
                **{
                    name: value
                    if name in artifact.raw_variables
                    else contain_untrusted(name, value)
                    for name, value in variables.items()
                }
            ),
            checksum=checksum,
        )
