"""A criterion R&D measures a sample against, and its verdict (ticket
ai-automation/09). The tenant's list of them is the policy
`supply_chain_sample_criteria` (`sample_criteria_policy`); `verdict` is the
one comparison every reader of a round uses.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dw_supply_chain.domain.extraction import parse_number

CHECK_VALUES = frozenset({"pass", "fail"})


class CriterionKind(StrEnum):
    NUMBER = "number"
    CHECK = "check"


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    # Nothing measured this round.
    UNMEASURED = "unmeasured"


class SampleCriterion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    label: str = Field(min_length=1, max_length=200)
    kind: CriterionKind
    unit: str | None = Field(default=None, max_length=20)
    min: Decimal | None = None
    max: Decimal | None = None

    @model_validator(mode="after")
    def _bounds_fit_the_kind(self) -> SampleCriterion:
        if self.kind is CriterionKind.NUMBER:
            if self.min is None and self.max is None:
                raise ValueError(f"{self.key}: a number criterion needs min or max")
            if self.min is not None and self.max is not None and self.min > self.max:
                raise ValueError(f"{self.key}: min is above max")
        elif self.min is not None or self.max is not None or self.unit is not None:
            raise ValueError(f"{self.key}: a check criterion has no unit or bounds")
        return self

    @property
    def standard(self) -> str:
        """The standard as the record prints it."""
        if self.kind is CriterionKind.CHECK:
            return "Đạt"
        unit = f" {self.unit}" if self.unit else ""
        if self.min is not None and self.max is not None:
            return f"{self.min} đến {self.max}{unit}"
        if self.min is not None:
            return f"≥ {self.min}{unit}"
        return f"≤ {self.max}{unit}"

    def accepts(self, raw: str) -> str | None:
        """The canonical value a person typed, or None when it is not one
        this criterion takes (a number, or pass / fail)."""
        text = raw.strip()
        if self.kind is CriterionKind.CHECK:
            return text if text in CHECK_VALUES else None
        number = parse_number(text)
        return None if number is None else str(number)

    def verdict(self, value: str | None) -> Verdict:
        if value is None:
            return Verdict.UNMEASURED
        if self.kind is CriterionKind.CHECK:
            return Verdict.PASS if value == "pass" else Verdict.FAIL
        number = Decimal(value)
        if self.min is not None and number < self.min:
            return Verdict.FAIL
        if self.max is not None and number > self.max:
            return Verdict.FAIL
        return Verdict.PASS


__all__ = ["CHECK_VALUES", "CriterionKind", "SampleCriterion", "Verdict"]
