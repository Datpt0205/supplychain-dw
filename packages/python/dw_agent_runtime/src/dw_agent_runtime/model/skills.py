"""Versioned skills: process knowledge a prompt declares (platform ADR 0025;
upstream candidate; supply-chain ticket ai-automation/04).

A skill is what a domain expert would tell a new colleague before the task: the
steps of a process, what each document holds, the rules a label must meet. It
is written once, reviewed like a prompt, versioned like a prompt, and pinned in
the release manifest. A prompt names the skills it needs (`skills:
[id@range]`); the registry places them in the prompt's SYSTEM part as trusted
reference text, never through an `<input>` variable, which is for data.

`configs/skills/<domain>/<skill_id>@<version>.yaml`: `title`, `body`,
`applies_to` (the prompt ids that may declare it). Checked when loaded:
`applies_to` names prompts that exist, and a prompt may declare only a skill
that names it; anything else refuses the start by name.

Resolution goes through `TenantOverlay`: a tenant's own version of a skill if
it has one that satisfies the range, the platform's otherwise, never another
tenant's.

A range is an exact version (`1.2.0`) or a caret (`^1.2.0`: the same major,
this version or later). The newest satisfying version wins.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from dw_kernel.errors import ConfigError, NotFoundError
from dw_kernel.overlay import TenantOverlay

_SKILL_ID = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
_REF = re.compile(
    r"^(?P<id>[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+)@(?P<caret>\^?)(?P<version>\d+\.\d+\.\d+)$"
)


def _semver(version: str) -> tuple[int, int, int]:
    major, minor, patch = (int(part) for part in version.split("."))
    return major, minor, patch


@dataclass(frozen=True, slots=True)
class SkillRef:
    """`skill_id@1.2.0` or `skill_id@^1.2.0`."""

    skill_id: str
    version: str
    caret: bool

    @classmethod
    def parse(cls, raw: str) -> Self:
        match = _REF.fullmatch(raw)
        if match is None:
            raise ValueError(f"skill reference {raw!r} is not id@version or id@^version")
        return cls(skill_id=match["id"], version=match["version"], caret=bool(match["caret"]))

    def admits(self, version: str) -> bool:
        if not self.caret:
            return version == self.version
        wanted, offered = _semver(self.version), _semver(version)
        return offered[0] == wanted[0] and offered >= wanted

    def __str__(self) -> str:
        return f"{self.skill_id}@{'^' if self.caret else ''}{self.version}"


class SkillArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    skill_id: str = Field(pattern=_SKILL_ID)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)
    applies_to: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _distinct_prompts(self) -> Self:
        if len(set(self.applies_to)) != len(self.applies_to):
            raise ValueError(f"skill {self.skill_id}: applies_to names a prompt twice")
        return self

    @property
    def ref(self) -> str:
        return f"{self.skill_id}@{self.version}"


@dataclass(frozen=True, slots=True)
class LoadedSkill:
    artifact: SkillArtifact
    checksum: str


@dataclass
class SkillRegistry:
    _skills: TenantOverlay[tuple[str, str], LoadedSkill] = field(default_factory=TenantOverlay)

    def load_directory(self, directory: Path, *, tenant_id: UUID | None = None) -> None:
        for path in sorted(directory.rglob("*.yaml")):
            self.load_file(path, tenant_id=tenant_id)

    def load_file(self, path: Path, *, tenant_id: UUID | None = None) -> LoadedSkill:
        return self.load_bytes(path.read_bytes(), name=path.name, tenant_id=tenant_id)

    def load_bytes(
        self, raw: bytes, *, name: str = "<bytes>", tenant_id: UUID | None = None
    ) -> LoadedSkill:
        try:
            artifact = SkillArtifact.model_validate(yaml.safe_load(raw))
        except (yaml.YAMLError, ValidationError) as exc:
            raise ConfigError(f"skill {name} invalid: {exc}") from exc
        loaded = LoadedSkill(
            artifact=artifact,
            checksum=hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest(),
        )
        key = (artifact.skill_id, artifact.version)
        if self._skills.existing(key, tenant_id=tenant_id) is not None:
            raise ConfigError(f"skill already registered: {artifact.ref}")
        self._skills.put(key, loaded, tenant_id=tenant_id)
        return loaded

    def _newest(self, ref: SkillRef, candidates: Iterable[LoadedSkill]) -> LoadedSkill | None:
        fitting = [
            s
            for s in candidates
            if s.artifact.skill_id == ref.skill_id and ref.admits(s.artifact.version)
        ]
        return max(fitting, key=lambda s: _semver(s.artifact.version), default=None)

    def resolve(self, ref: SkillRef, *, tenant_id: UUID | None = None) -> LoadedSkill:
        """The tenant's newest satisfying version if it has one, else the
        platform's newest. Never another tenant's."""
        if tenant_id is not None:
            own = self._newest(ref, self._skills.tenant_values(tenant_id))
            if own is not None:
                return own
        platform = self._newest(ref, self._skills.platform_values())
        if platform is None:
            raise NotFoundError("skill not registered", details={"skill": str(ref)})
        return platform

    def platform_skills(self) -> Iterator[LoadedSkill]:
        return self._skills.platform_values()

    def check_applies_to(self, prompt_ids: frozenset[str]) -> None:
        """Every prompt a platform skill names exists."""
        for loaded in self.platform_skills():
            missing = sorted(set(loaded.artifact.applies_to) - prompt_ids)
            if missing:
                raise ConfigError(
                    f"skill {loaded.artifact.ref} applies_to prompts that do not exist: {missing}"
                )
