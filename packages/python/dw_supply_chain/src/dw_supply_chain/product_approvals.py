"""Who may decide each approval a product-development case raises, per tenant
(stage-1 ticket 02, ADR 0016 and ADR 0020).

The platform default ships in
`configs/policies/supply_chain_product_approvals@1.0.0.yaml`; a tenant replaces
it whole through `PolicyOverridePort`. It is read once, when an approval is
raised, and its `required_scope` is stamped on the approval row: deciding reads
the stamp, never this policy, so a change reaches only approvals raised after
it (ADR 0020, "a past decision must not change").

Shaped by step: `bod_review` (step 6) today; step 9's sign-off steps join as
their own fields in S4. The scope's SHAPE has one owner, the CHECK on
`platform.approval_requests.required_scope` (ADR 0020 amendment 4): a malformed
value fails the approval's insert, the run ends failed and no request exists,
so this schema asks only that the value be present and not blank.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "PRODUCT_APPROVALS_POLICY_ID",
    "ApprovalStep",
    "SupplyChainProductApprovals",
    "load_supply_chain_product_approvals",
]

# The document's own `policy_id`, and the key its tenant override is stored under.
PRODUCT_APPROVALS_POLICY_ID = "supply_chain_product_approvals"


class ApprovalStep(BaseModel):
    """One approval step: the scope its decider must hold besides
    `approvals.decide`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    required_scope: str = Field(min_length=1, pattern=r"\S")


class SupplyChainProductApprovals(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    policy_id: str
    policy_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    # Step 6: BGĐ reviews a passed sample.
    bod_review: ApprovalStep


def load_supply_chain_product_approvals(path: Path) -> SupplyChainProductApprovals:
    return SupplyChainProductApprovals.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )
