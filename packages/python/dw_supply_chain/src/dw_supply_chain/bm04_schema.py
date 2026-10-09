"""What a BM04 profile's `attributes` may hold, per tenant (ADR 0026, E15).

BM04 is a form in the app. Its typed columns (unit price, currency, MOQ, lead
time, Incoterm) are fixed because code computes with them; everything else
(specifications, variants, packaging) is `attributes`, checked against this
schema. The platform default (`configs/policies/supply_chain_bm04_schema@1.0.0.yaml`)
is neutral; a tenant replaces it whole through `PolicyOverridePort`, the way
every other Supply Chain policy is overridden, and the schema is read where a
profile is saved, never cached.

A schema may not declare a field the typed columns own: a tenant field named
`unit_price` would be a price outside the commercial scope.

Placed at the package's top level, like `packaging_policy.py`: a versioned
artifact's schema and parser, touching the filesystem.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dw_kernel.errors import DomainError
from dw_supply_chain.domain.commercial import PRICE_FIELDS

__all__ = [
    "BM04_SCHEMA_POLICY_ID",
    "Bm04Field",
    "Bm04FieldKind",
    "SupplyChainBm04Schema",
    "load_supply_chain_bm04_schema",
]

# The document's own `policy_id`, and the key its tenant override is stored under.
BM04_SCHEMA_POLICY_ID = "supply_chain_bm04_schema"

# The typed columns of `product_profiles`, and every price field: a schema
# field may not take one of these names.
RESERVED_KEYS = (
    frozenset({"unit_price", "currency", "moq", "lead_time_days", "incoterm", "price"})
    | PRICE_FIELDS
)

_TEXT_MAX = 2000


class Bm04FieldKind(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    CHOICE = "choice"


class Bm04Field(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    label: str = Field(min_length=1, max_length=200)
    kind: Bm04FieldKind
    required: bool = Field(default=False, strict=True)
    unit: str | None = Field(default=None, max_length=20)
    max_length: int | None = Field(default=None, gt=0, le=_TEXT_MAX)
    options: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.key in RESERVED_KEYS:
            raise ValueError(f"field {self.key!r} is a typed column or a price; not an attribute")
        if self.kind is Bm04FieldKind.CHOICE:
            if not self.options or len(set(self.options)) != len(self.options):
                raise ValueError(f"choice field {self.key!r} needs distinct options")
        elif self.options is not None:
            raise ValueError(f"only a choice field has options ({self.key!r})")
        if self.max_length is not None and self.kind is not Bm04FieldKind.TEXT:
            raise ValueError(f"only a text field has max_length ({self.key!r})")
        return self


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


class SupplyChainBm04Schema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    policy_id: str = Field(pattern=f"^{BM04_SCHEMA_POLICY_ID}$")
    policy_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    fields: tuple[Bm04Field, ...] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _unique_keys(self) -> Self:
        keys = [f.key for f in self.fields]
        if len(set(keys)) != len(keys):
            raise ValueError("field keys must be unique")
        return self

    def check(self, attributes: Mapping[str, Any]) -> dict[str, Any]:
        """`attributes` as stored, or a 422 naming every field that is wrong.

        Unknown keys are refused, not dropped: a value nobody's schema names is
        a value nobody reads. A missing optional field is left out; a blank
        text is treated as missing."""
        by_key = {f.key: f for f in self.fields}
        errors: list[dict[str, str]] = [
            {"field": key, "error": "unknown_field"} for key in attributes if key not in by_key
        ]
        clean: dict[str, Any] = {}
        for spec in self.fields:
            value = attributes.get(spec.key)
            if isinstance(value, str):
                value = value.strip() or None
            if value is None:
                if spec.required:
                    errors.append({"field": spec.key, "error": "required"})
                continue
            problem = _problem(spec, value)
            if problem is not None:
                errors.append({"field": spec.key, "error": problem})
                continue
            clean[spec.key] = value
        if errors:
            raise DomainError("BM04 không khớp biểu mẫu của công ty", details={"errors": errors})
        return clean


def _problem(spec: Bm04Field, value: object) -> str | None:
    match spec.kind:
        case Bm04FieldKind.TEXT:
            if not isinstance(value, str):
                return "not_text"
            if len(value) > (spec.max_length or _TEXT_MAX):
                return "too_long"
        case Bm04FieldKind.NUMBER:
            if not _is_number(value):
                return "not_number"
        case Bm04FieldKind.INTEGER:
            if not isinstance(value, int) or isinstance(value, bool):
                return "not_integer"
        case Bm04FieldKind.BOOLEAN:
            if not isinstance(value, bool):
                return "not_boolean"
        case Bm04FieldKind.CHOICE:
            if value not in (spec.options or ()):
                return "not_an_option"
    return None


def load_supply_chain_bm04_schema(path: Path) -> SupplyChainBm04Schema:
    return SupplyChainBm04Schema.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
