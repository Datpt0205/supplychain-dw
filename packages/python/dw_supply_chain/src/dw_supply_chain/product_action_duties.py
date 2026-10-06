"""Which duty each step on a product-development case belongs to, per tenant.

The product case's own policy, beside `action_duties.py`'s PO one and never
merged into it: the two enums share five action names, so a single document
keyed by name would let a PO override decide who may cancel a product case.
Same mechanism otherwise: the platform default ships in
`configs/policies/supply_chain_product_action_duties@1.0.0.yaml`, a tenant
replaces it whole through `PolicyOverridePort`, and taking a step requires the
scope of its duty (`application.handlers.duty_scope`). The duties themselves
are `CaseDuty`'s, because roles are granted in duty terms.

Keyed by the steps a PERSON takes: `GRAPH_ONLY_ACTIONS` (BGĐ's two outcomes of
step 6) have no duty, because no person takes them; who decides the approval
behind them is the approval's stamped `required_scope`
(`supply_chain_product_approvals`). A key for one of them is refused, since
nothing would read it, and an override stored before they existed stays valid.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dw_supply_chain.action_duties import CaseDuty
from dw_supply_chain.domain.product_development_case import GRAPH_ONLY_ACTIONS, ProductAction

__all__ = [
    "PRODUCT_ACTION_DUTIES_POLICY_ID",
    "SupplyChainProductActionDuties",
    "load_supply_chain_product_action_duties",
]

# The document's own `policy_id`, and the key its tenant override is stored under.
PRODUCT_ACTION_DUTIES_POLICY_ID = "supply_chain_product_action_duties"


class SupplyChainProductActionDuties(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    policy_id: str
    policy_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    action_duties: dict[ProductAction, CaseDuty]

    @model_validator(mode="after")
    def _every_step_a_person_takes_has_a_duty(self) -> SupplyChainProductActionDuties:
        # A step with no duty is one nobody could take, or, read permissively,
        # one anybody could. Refuse it, as the PO policy does.
        missing = sorted(
            a.value
            for a in ProductAction
            if a not in GRAPH_ONLY_ACTIONS and a not in self.action_duties
        )
        if missing:
            raise ValueError(
                f"action_duties must give every product action a duty (missing: {missing})"
            )
        # A duty for a step only the review graph applies is read by nothing
        # (failure-modes #1): it would look like a say over who approves.
        unread = sorted(a.value for a in self.action_duties if a in GRAPH_ONLY_ACTIONS)
        if unread:
            raise ValueError(
                "action_duties may not name a step only the review graph applies; "
                f"who decides it is the approval's required_scope (named: {unread})"
            )
        return self

    def duty_for(self, action: ProductAction) -> CaseDuty:
        return self.action_duties[action]


def load_supply_chain_product_action_duties(path: Path) -> SupplyChainProductActionDuties:
    return SupplyChainProductActionDuties.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )
