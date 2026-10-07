"""Which duty each case action belongs to, per tenant — who may take which
step.

Every transition used to be one scope, `supply_chain.po_case.write`, so
"only Finance confirms a payment" could not be said. Now each `CaseAction`
belongs to one `CaseDuty`, and taking the action requires that duty's scope
(`application.handlers.duty_scope`). Roles grant duties, and the database's
separation-of-duties rules keep conflicting duties (ordering and paying,
receiving and paying) out of one membership.

Which department owns which step differs per company, so the mapping is a
policy. The platform default ships in
`configs/policies/supply_chain_action_duties@1.2.0.yaml`, and a tenant
replaces it through the same `PolicyOverridePort` as the SLA policy and the
approval matrix. The set of duties is fixed here, because roles are granted
in duty terms.

The steps of step 12's colour, packaging and pre-production sub-flow
(`PackagingAction`, slice PK) are PO-case steps too, so they are keys of the
same mapping (1.2.0): one answer to "who may take this step on a PO case".

Placed at the package's top level, like `approval_matrix.py`: a versioned
artifact's schema and parser, touching the filesystem.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dw_supply_chain.domain.packaging_design import PackagingAction
from dw_supply_chain.domain.po_case import CaseAction

__all__ = [
    "CaseDuty",
    "POStep",
    "SupplyChainActionDuties",
    "load_supply_chain_action_duties",
]

# Every step a person takes on a PO case: its transitions and step 12's sub-flow.
POStep = CaseAction | PackagingAction


class CaseDuty(StrEnum):
    # Placing and steering the order with the supplier.
    ORDERING = "ordering"
    # Money leaving the company: deposit and final payment.
    FINANCE = "finance"
    # Inspecting the goods.
    QC = "qc"
    # Moving the goods: arrival at port.
    LOGISTICS = "logistics"
    # Taking the goods in.
    WAREHOUSE = "warehouse"
    # Raising and clearing an exception: blocked, manual review, waiting on
    # an outside party.
    EXCEPTIONS = "exceptions"
    # R&D: receiving and testing samples, asking for revisions (stage 1,
    # `product_action_duties`). No PO step needs it; the PO policy requires
    # every PO step a duty, not every duty a step, so PO overrides are untouched.
    RND = "rnd"
    # TP Cung ứng: confirming the product with the supplier (step 8, S3).
    # Like `rnd`, no PO step needs it.
    SUPPLY_LEAD = "supply_lead"


class SupplyChainActionDuties(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    policy_id: str
    policy_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    action_duties: dict[POStep, CaseDuty]

    @model_validator(mode="after")
    def _every_action_has_a_duty(self) -> SupplyChainActionDuties:
        # An action with no duty would be one nobody could take, or, read
        # permissively, one anybody could. Neither is a policy; refuse it.
        steps: list[POStep] = [*CaseAction, *PackagingAction]
        missing = sorted(step.value for step in steps if step not in self.action_duties)
        if missing:
            raise ValueError(
                f"action_duties must give every case action a duty (missing: {missing})"
            )
        return self

    def duty_for(self, action: POStep) -> CaseDuty:
        return self.action_duties[action]


def load_supply_chain_action_duties(path: Path) -> SupplyChainActionDuties:
    return SupplyChainActionDuties.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
