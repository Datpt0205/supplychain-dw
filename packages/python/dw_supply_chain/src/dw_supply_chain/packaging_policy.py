"""Whether a PO case needs a passed pre-production test to enter production,
per tenant (slice PK, step 12-13).

The platform default (`configs/policies/supply_chain_packaging@1.1.0.yaml`)
says no, so every case and every tenant behaves as before this slice; a tenant
turns the rule on through the same `PolicyOverridePort` as its other policies
(Elmich's: `scripts/elmich_packaging_override.yaml`). Read where step 13 is
taken (`application.packaging_designs.production_gate`), never cached.

Placed at the package's top level, like `action_duties.py`: a versioned
artifact's schema and parser, touching the filesystem.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "PACKAGING_POLICY_ID",
    "SupplyChainPackagingPolicy",
    "load_supply_chain_packaging_policy",
]

# The document's own `policy_id`, and the key its tenant override is stored under.
PACKAGING_POLICY_ID = "supply_chain_packaging"


class SupplyChainPackagingPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    policy_id: str = Field(pattern=f"^{PACKAGING_POLICY_ID}$")
    policy_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    # Strict: a document saying "yes" as a string is refused, not read as true.
    require_pre_production_test: bool = Field(strict=True)
    # 1.1.0 (ADR 0028; ticket ai-automation/16): MKT's two steps stand between
    # the colour and the design. Absent from an override stored before 1.1.0,
    # which therefore keeps the flow it chose then.
    require_packaging_content: bool = Field(default=False, strict=True)


def load_supply_chain_packaging_policy(path: Path) -> SupplyChainPackagingPolicy:
    return SupplyChainPackagingPolicy.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )
