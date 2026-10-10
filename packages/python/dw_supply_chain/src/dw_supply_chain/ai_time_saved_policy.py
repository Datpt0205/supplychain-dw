"""How many minutes each drafted paper saves a person (ticket
ai-automation/20): `configs/policies/supply_chain_ai_time_saved@1.0.0.yaml`,
minutes per document type and the share an edited draft still saves. A
tenant replaces the whole document through `PolicyOverridePort`, falling back
to the platform's, never to another tenant's. An estimate, read by the AI
acceptance report only; it decides nothing.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import PolicyOverridePort
from dw_supply_chain.domain.case_document import DocumentType

AI_TIME_SAVED_POLICY_ID = "supply_chain_ai_time_saved"


class SupplyChainAiTimeSaved(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    policy_id: str
    policy_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    edited_credit: float = Field(ge=0, le=1)
    minutes_per_draft: dict[DocumentType, int] = Field(default_factory=dict)

    def minutes(self) -> dict[str, int]:
        return {t.value: max(0, m) for t, m in self.minutes_per_draft.items()}


def load_supply_chain_ai_time_saved(path: Path) -> SupplyChainAiTimeSaved:
    return SupplyChainAiTimeSaved.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


async def resolve_ai_time_saved(
    context: AccessContext,
    policy_override_repo: PolicyOverridePort,
    platform_default: SupplyChainAiTimeSaved,
) -> SupplyChainAiTimeSaved:
    override = await policy_override_repo.get(context, AI_TIME_SAVED_POLICY_ID)
    if override is None:
        return platform_default
    return SupplyChainAiTimeSaved.model_validate(override)


__all__ = [
    "AI_TIME_SAVED_POLICY_ID",
    "SupplyChainAiTimeSaved",
    "load_supply_chain_ai_time_saved",
    "resolve_ai_time_saved",
]
