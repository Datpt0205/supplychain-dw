"""What R&D measures on a sample round, and what passes (ticket
ai-automation/09).

`configs/policies/supply_chain_sample_criteria@1.0.0.yaml`: a `default` list
of criteria and, per Category key, a list that replaces it. A criterion is a
`number` (a unit and an inclusive `min`/`max`, either optional) or a `check`
(R&D types pass or fail). A tenant replaces the whole document through
`PolicyOverridePort`, falling back to the platform's, never to another
tenant's. Checked when it loads: keys unique per list, a number criterion has
a bound, a check criterion has none.

The criterion itself, and `verdict`, the one comparison, are
`domain.sample_criteria`.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import PolicyOverridePort
from dw_supply_chain.domain.sample_criteria import SampleCriterion

__all__ = [
    "SAMPLE_CRITERIA_POLICY_ID",
    "SupplyChainSampleCriteria",
    "load_supply_chain_sample_criteria",
    "resolve_sample_criteria",
]

SAMPLE_CRITERIA_POLICY_ID = "supply_chain_sample_criteria"


def _unique(criteria: tuple[SampleCriterion, ...], where: str) -> None:
    keys = [c.key for c in criteria]
    if len(set(keys)) != len(keys):
        raise ValueError(f"{where}: a criterion key is listed twice")
    if not criteria:
        raise ValueError(f"{where}: no criterion")


class SupplyChainSampleCriteria(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    policy_id: str
    policy_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    default: tuple[SampleCriterion, ...] = Field(max_length=50)
    by_category: dict[str, tuple[SampleCriterion, ...]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _each_list_well_formed(self) -> SupplyChainSampleCriteria:
        _unique(self.default, "default")
        for category, criteria in self.by_category.items():
            _unique(criteria, category)
        return self

    def for_category(self, category: str) -> tuple[SampleCriterion, ...]:
        return self.by_category.get(category, self.default)


def load_supply_chain_sample_criteria(path: Path) -> SupplyChainSampleCriteria:
    return SupplyChainSampleCriteria.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )


async def resolve_sample_criteria(
    context: AccessContext,
    policy_override_repo: PolicyOverridePort,
    platform_default: SupplyChainSampleCriteria,
) -> SupplyChainSampleCriteria:
    """The tenant's own criteria if it set them (re-validated whole), the
    platform's otherwise."""
    override = await policy_override_repo.get(context, SAMPLE_CRITERIA_POLICY_ID)
    if override is None:
        return platform_default
    return SupplyChainSampleCriteria.model_validate(override)
