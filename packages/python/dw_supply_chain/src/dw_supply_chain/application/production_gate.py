"""Step 13's gate: may this PO case go from `pre_production` into `production`?

The one place the answer is assembled (slice PK): the tenant's packaging policy
(`require_pre_production_test`, its override or the platform's) and the case's
pre-production test. Both doors to step 13 ask it — `AdvancePOCase` for a step
taken directly and the approval graph's apply node for one a person approved —
and `POCase.start_production` cannot be called without its answer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import PolicyOverridePort
from dw_supply_chain.application.ports import PackagingDesignReaderPort
from dw_supply_chain.domain.packaging_design import PreProductionTest, ProductionGate
from dw_supply_chain.packaging_policy import PACKAGING_POLICY_ID, SupplyChainPackagingPolicy


async def resolve_packaging_policy(
    context: AccessContext,
    policy_override_repo: PolicyOverridePort,
    platform_default: SupplyChainPackagingPolicy,
) -> SupplyChainPackagingPolicy:
    """The tenant's own packaging policy if it has set one, the platform's
    otherwise; a stored override is re-validated whole on every read."""
    override = await policy_override_repo.get(context, PACKAGING_POLICY_ID)
    if override is None:
        return platform_default
    return SupplyChainPackagingPolicy.model_validate(override)


@dataclass(frozen=True)
class ProductionGateResolver:
    designs: PackagingDesignReaderPort
    policy_override_repo: PolicyOverridePort
    platform_default: SupplyChainPackagingPolicy

    async def for_case(self, context: AccessContext, po_case_id: uuid.UUID) -> ProductionGate:
        policy = await resolve_packaging_policy(
            context, self.policy_override_repo, self.platform_default
        )
        design = await self.designs.get(context, po_case_id)
        return ProductionGate(
            required=policy.require_pre_production_test,
            test=design.pre_production_test if design else PreProductionTest.PENDING,
        )
