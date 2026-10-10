"""How a tenant's item codes and SKU codes are made (ticket ai-automation/13;
ADR 0018 amendment 2, QE-11).

`configs/policies/supply_chain_item_code_rule@1.0.0.yaml` ships `rule: null`:
the platform proposes no item code for anyone, and step 9's preparation then
proposes only SKUs under a code a person types. A tenant replaces the whole
document through `PolicyOverridePort` (`PUT /supply-chain/item-code-rule`,
`supply_chain.action_duties.write`), falling back to the platform's, never to
another tenant's. Elmich's (`scripts/elmich_item_code_rule_override.yaml`) is
provisional and permissive until it answers QE-11.

The rule itself, and the code it gives next, are `domain.item_coding`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import PolicyOverridePort
from dw_supply_chain.domain.item_coding import ItemCodeRule

__all__ = [
    "ITEM_CODE_RULE_POLICY_ID",
    "SupplyChainItemCodeRule",
    "load_supply_chain_item_code_rule",
    "resolve_item_code_rule",
]

ITEM_CODE_RULE_POLICY_ID = "supply_chain_item_code_rule"


class SupplyChainItemCodeRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    policy_id: Literal["supply_chain_item_code_rule"]
    policy_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    # None: no item code is proposed (no guess); SKUs still are, under the
    # code a person types.
    rule: ItemCodeRule | None = None


def load_supply_chain_item_code_rule(path: Path) -> SupplyChainItemCodeRule:
    return SupplyChainItemCodeRule.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


async def resolve_item_code_rule(
    context: AccessContext,
    policy_override_repo: PolicyOverridePort,
    platform_default: SupplyChainItemCodeRule,
) -> SupplyChainItemCodeRule:
    """The tenant's own rule if it set one (re-validated whole), the
    platform's (none) otherwise."""
    override = await policy_override_repo.get(context, ITEM_CODE_RULE_POLICY_ID)
    if override is None:
        return platform_default
    return SupplyChainItemCodeRule.model_validate(override)
