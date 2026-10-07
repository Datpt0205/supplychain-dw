"""Who may decide each approval a product-development case raises, per tenant
(stage-1 tickets 02 and 04, ADR 0016 and ADR 0020).

The platform default ships in
`configs/policies/supply_chain_product_approvals@1.1.0.yaml`; a tenant replaces
it whole through `PolicyOverridePort`. It is read once, when an approval is
raised, and its `required_scope` is stamped on the approval row: deciding reads
the stamp, never this policy, so a change reaches only approvals raised after
it (ADR 0020, "a past decision must not change").

Shaped by step: `bod_review` (step 6), and `signoff` (step 9, from 1.1.0), the
ordered list of sign-off steps. The whole list is read when a case is
submitted and travels with the sign-off run, so a case already waiting keeps
the order it was submitted under. The scope's SHAPE has one owner, the CHECK on
`platform.approval_requests.required_scope` (ADR 0020 amendment 4): a malformed
value fails the approval's insert, the run ends failed and no request exists,
so this schema asks only that the value be present and not blank.

A tenant override stored at 1.0.0 predates `signoff` and never decided it:
`from_stored` gives it the platform's sign-off, and only that.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "PRODUCT_APPROVALS_POLICY_ID",
    "ApprovalStep",
    "SignoffStep",
    "SupplyChainProductApprovals",
    "load_supply_chain_product_approvals",
]

# The document's own `policy_id`, and the key its tenant override is stored under.
PRODUCT_APPROVALS_POLICY_ID = "supply_chain_product_approvals"

_VERSION_BEFORE_SIGNOFF = "1.0.0"


class ApprovalStep(BaseModel):
    """One approval step: the scope its decider must hold besides
    `approvals.decide`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    required_scope: str = Field(min_length=1, pattern=r"\S")


class SignoffStep(ApprovalStep):
    """One step of the sign-off, in order: its key (stamped on the approval's
    payload, read back by the case page), what a person calls it, and who
    decides it."""

    step: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    label: str = Field(min_length=1, max_length=60, pattern=r"\S")


class SupplyChainProductApprovals(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    policy_id: str
    policy_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    # Step 6: BGĐ reviews a passed sample.
    bod_review: ApprovalStep
    # Step 9: the sign-off steps, decided one after another in this order.
    signoff: tuple[SignoffStep, ...] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def _each_signoff_step_once(self) -> SupplyChainProductApprovals:
        keys = [s.step for s in self.signoff]
        if len(set(keys)) != len(keys):
            raise ValueError(f"signoff names a step twice: {keys}")
        return self

    @classmethod
    def from_stored(cls, stored: Mapping[str, object], platform_default: Self) -> Self:
        """A tenant's stored override, validated whole. One stored at 1.0.0
        without `signoff` takes the platform's; anything else must be whole."""
        if stored.get("policy_version") != _VERSION_BEFORE_SIGNOFF or "signoff" in stored:
            return cls.model_validate(stored)
        return cls.model_validate(
            {**stored, "signoff": [s.model_dump() for s in platform_default.signoff]}
        )


def load_supply_chain_product_approvals(path: Path) -> SupplyChainProductApprovals:
    return SupplyChainProductApprovals.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )
