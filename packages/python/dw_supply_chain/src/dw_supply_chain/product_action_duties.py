"""Which duty each step on a product-development case belongs to, per tenant.

The product case's own policy, beside `action_duties.py`'s PO one and never
merged into it: the two enums share five action names, so a single document
keyed by name would let a PO override decide who may cancel a product case.
Same mechanism otherwise: the platform default ships in
`configs/policies/supply_chain_product_action_duties@1.3.0.yaml`, a tenant
replaces it whole through `PolicyOverridePort`, and taking a step requires the
scope of its duty (`application.handlers.duty_scope`). The duties themselves
are `CaseDuty`'s, because roles are granted in duty terms.

Keyed by the steps a PERSON takes: `GRAPH_ONLY_ACTIONS` (BGĐ's two outcomes of
step 6) have no duty, because no person takes them; who decides the approval
behind them is the approval's stamped `required_scope`
(`supply_chain_product_approvals`). A key for one of them is refused, since
nothing would read it, and an override stored before they existed stays valid.

Steps added after a version (`STEPS_ADDED_AFTER`: steps 7 and 8 in 1.1.0, S3;
step 9's four in 1.2.0, S4; ĐẶT HÀNG in 1.3.0, S5): a tenant override stored at an older version was
written before they existed and so never decided who takes them.
`from_stored` gives those steps, and only those, the platform's duty when
such a document leaves them out; everything the tenant did decide stands. A
document claiming the current version, and every new override (the PUT
validates whole), must name them all.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dw_supply_chain.action_duties import CaseDuty
from dw_supply_chain.domain.product_development_case import (
    GRAPH_ONLY_ACTIONS,
    IMPORT_ONLY_ACTIONS,
    ProductAction,
)

__all__ = [
    "PRODUCT_ACTION_DUTIES_POLICY_ID",
    "STEPS_ADDED_AFTER",
    "SupplyChainProductActionDuties",
    "load_supply_chain_product_action_duties",
]

# The document's own `policy_id`, and the key its tenant override is stored under.
PRODUCT_ACTION_DUTIES_POLICY_ID = "supply_chain_product_action_duties"

_STEPS_7_8 = frozenset({ProductAction.COMPLETE_PROFILE, ProductAction.CONFIRM_WITH_SUPPLIER})
_STEP_9 = frozenset(
    {
        ProductAction.ISSUE_ITEM_CODE,
        ProductAction.ADD_SKU,
        ProductAction.REMOVE_SKU,
        ProductAction.SUBMIT_FOR_SIGNOFF,
    }
)
_ORDER = frozenset({ProductAction.PLACE_ORDER})
# For each older version a tenant override may be stored at, the steps a
# person takes that it did not have: such an override cannot have decided them.
STEPS_ADDED_AFTER: Mapping[str, frozenset[ProductAction]] = {
    "1.0.0": _STEPS_7_8 | _STEP_9 | _ORDER,
    "1.1.0": _STEP_9 | _ORDER,
    "1.2.0": _ORDER,
}


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
            if a not in GRAPH_ONLY_ACTIONS
            and a not in IMPORT_ONLY_ACTIONS
            and a not in self.action_duties
        )
        if missing:
            raise ValueError(
                f"action_duties must give every product action a duty (missing: {missing})"
            )
        # A duty for a step only the review graph applies is read by nothing
        # (failure-modes #1): it would look like a say over who approves.
        unread = sorted(
            a.value
            for a in self.action_duties
            if a in GRAPH_ONLY_ACTIONS or a in IMPORT_ONLY_ACTIONS
        )
        if unread:
            raise ValueError(
                "action_duties may not name a step only the review graph applies; "
                f"who decides it is the approval's required_scope (named: {unread})"
            )
        return self

    def duty_for(self, action: ProductAction) -> CaseDuty:
        return self.action_duties[action]

    @classmethod
    def from_stored(cls, stored: Mapping[str, object], platform_default: Self) -> Self:
        """A tenant's stored override, validated whole. One stored at an
        older version that leaves out a step added after it takes the
        platform's duty for that step; any other gap is refused, as it always
        was."""
        duties = stored.get("action_duties")
        added = STEPS_ADDED_AFTER.get(str(stored.get("policy_version")))
        if added is None or not isinstance(duties, Mapping):
            return cls.model_validate(stored)
        predated = {
            action.value: platform_default.duty_for(action).value
            for action in added
            if action.value not in duties
        }
        return cls.model_validate({**stored, "action_duties": {**duties, **predated}})


def load_supply_chain_product_action_duties(path: Path) -> SupplyChainProductActionDuties:
    return SupplyChainProductActionDuties.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )
