"""Versioned document templates, per tenant (upstream candidate; supply-chain
ticket ai-automation/03).

A template is two files under `configs/doc_templates/<domain>/`: the document
itself (`<template_id>@<version>.docx`, placeholders written `{{field}}`, a
table row repeated per item written `{{field.column}}`) and its declaration
(`<template_id>@<version>.yaml`: title, the document type it produces, and
each field with its kind). The same shape as prompts and tool specs, for the
same reasons: immutable once released (a change is a new version), pinned in
the release manifest, and resolved through `TenantOverlay`, so a tenant's own
form is an override loaded at run time from storage, never a branch and never
a deploy. Resolution falls back to the platform, never sideways to another
tenant.

Checked when loaded, not when first rendered: the declaration's fields must be
exactly the placeholders the document carries (`TemplateInspectorPort`), so a
field nobody renders, or a placeholder nobody fills, refuses the file by name.
A document carrying macros is refused too.

How the bytes become a document is a port (`DocumentRendererPort`): rendered in
process today (`adapters.docx_templates`), replaceable by a sandboxed renderer
later without touching a caller. Field values are data: a value that spells
`{{other}}` is printed as written, never expanded.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol, Self
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from dw_kernel.errors import ConfigError, NotFoundError
from dw_kernel.overlay import TenantOverlay

_NAME = r"^[a-z][a-z0-9_]{0,63}$"


class TemplateFieldKind(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    TABLE = "table"


class TemplateColumn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=_NAME)
    label: str = Field(min_length=1, max_length=200)
    kind: TemplateFieldKind = TemplateFieldKind.TEXT

    @model_validator(mode="after")
    def _not_a_table(self) -> Self:
        if self.kind is TemplateFieldKind.TABLE:
            raise ValueError(f"column {self.name!r} cannot itself be a table")
        return self


class TemplateField(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=_NAME)
    label: str = Field(min_length=1, max_length=200)
    kind: TemplateFieldKind = TemplateFieldKind.TEXT
    required: bool = Field(default=False, strict=True)
    columns: tuple[TemplateColumn, ...] | None = None

    @model_validator(mode="after")
    def _columns_only_for_a_table(self) -> Self:
        if (self.kind is TemplateFieldKind.TABLE) != (self.columns is not None):
            raise ValueError(f"field {self.name!r}: a table has columns, nothing else does")
        if self.columns is not None:
            names = [c.name for c in self.columns]
            if not names or len(set(names)) != len(names):
                raise ValueError(f"table {self.name!r} needs distinct columns")
        return self

    def placeholders(self) -> frozenset[str]:
        if self.columns is None:
            return frozenset({self.name})
        return frozenset(f"{self.name}.{c.name}" for c in self.columns)


class DocTemplateSpec(BaseModel):
    """A template's declaration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    template_id: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    title: str = Field(min_length=1, max_length=200)
    # The document type the rendered file is stored as, in the owning context's
    # own vocabulary (this module does not know the list).
    doc_type: str = Field(pattern=_NAME)
    description: str = ""
    fields: tuple[TemplateField, ...] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _unique_fields(self) -> Self:
        names = [f.name for f in self.fields]
        if len(set(names)) != len(names):
            raise ValueError("field names must be unique")
        return self

    @property
    def ref(self) -> str:
        return f"{self.template_id}@{self.version}"

    @property
    def file_name(self) -> str:
        return f"{self.ref}.docx"

    def placeholders(self) -> frozenset[str]:
        return frozenset().union(*(f.placeholders() for f in self.fields))

    def field(self, name: str) -> TemplateField | None:
        return next((f for f in self.fields if f.name == name), None)


@dataclass(frozen=True, slots=True)
class LoadedDocTemplate:
    spec: DocTemplateSpec
    docx: bytes
    # Over the declaration and the document both: what a release pins.
    checksum: str


def template_checksum(spec_bytes: bytes, docx: bytes) -> str:
    """Line endings in the declaration do not change the checksum (a Windows
    checkout writes CRLF); the document's bytes are hashed as stored."""
    digest = hashlib.sha256()
    digest.update(spec_bytes.replace(b"\r\n", b"\n"))
    digest.update(b"\0")
    digest.update(docx)
    return digest.hexdigest()


class TemplateInspectorPort(Protocol):
    """What a document file's placeholders are. Raises `ConfigError` for a
    file that cannot be a template (macros, a placeholder split so a renderer
    could not find it)."""

    def placeholders(self, docx: bytes) -> frozenset[str]: ...


@dataclass(frozen=True, slots=True)
class RenderedFile:
    data: bytes
    content_type: str
    extension: str


# A field's value as a renderer takes it: text already formatted by the caller,
# a table's rows, or None for a value nobody has filled.
TemplateValue = str | Sequence[Mapping[str, str | None]] | None


class DocumentRendererPort(Protocol):
    def render(
        self, template: LoadedDocTemplate, values: Mapping[str, TemplateValue]
    ) -> RenderedFile: ...


@dataclass
class DocTemplateRegistry:
    inspector: TemplateInspectorPort
    _templates: TenantOverlay[tuple[str, str], LoadedDocTemplate] = field(
        default_factory=TenantOverlay
    )

    def load_directory(
        self, directory: Path, *, tenant_id: UUID | None = None
    ) -> list[LoadedDocTemplate]:
        loaded = []
        for path in sorted(directory.rglob("*.yaml")):
            docx_path = path.with_suffix(".docx")
            if not docx_path.exists():
                raise ConfigError(f"template {path.name}: no {docx_path.name} beside it")
            loaded.append(
                self.load_bytes(path.read_bytes(), docx_path.read_bytes(), tenant_id=tenant_id)
            )
        return loaded

    def load_bytes(
        self, spec_bytes: bytes, docx: bytes, *, tenant_id: UUID | None = None
    ) -> LoadedDocTemplate:
        try:
            spec = DocTemplateSpec.model_validate(yaml.safe_load(spec_bytes))
        except (yaml.YAMLError, ValidationError) as exc:
            raise ConfigError(f"template declaration invalid: {exc}") from exc
        found = self.inspector.placeholders(docx)
        declared = spec.placeholders()
        if found != declared:
            raise ConfigError(
                f"template {spec.ref}: placeholders {sorted(found)} != declared {sorted(declared)}"
            )
        loaded = LoadedDocTemplate(
            spec=spec, docx=docx, checksum=template_checksum(spec_bytes, docx)
        )
        self.register(loaded, tenant_id=tenant_id)
        return loaded

    def register(self, loaded: LoadedDocTemplate, *, tenant_id: UUID | None = None) -> None:
        key = (loaded.spec.template_id, loaded.spec.version)
        # Within one layer only: a tenant overriding a platform template is the
        # feature; two files claiming one version in a layer is the mistake.
        if self._templates.existing(key, tenant_id=tenant_id) is not None:
            raise ConfigError(f"template already registered: {loaded.spec.ref}")
        self._templates.put(key, loaded, tenant_id=tenant_id)

    def has_override(self, template_id: str, version: str, *, tenant_id: UUID) -> bool:
        return self._templates.existing((template_id, version), tenant_id=tenant_id) is not None

    def resolve(
        self, template_id: str, version: str, *, tenant_id: UUID | None = None
    ) -> LoadedDocTemplate:
        """The tenant's own version if it has one, the platform's otherwise."""
        loaded = self._templates.get((template_id, version), tenant_id=tenant_id)
        if loaded is None:
            raise NotFoundError(
                "document template not registered",
                details={"template_id": template_id, "version": version},
            )
        return loaded

    def platform_templates(self) -> Iterator[LoadedDocTemplate]:
        """The platform layer only: what a deployment ships, whoever asks."""
        return self._templates.platform_values()
