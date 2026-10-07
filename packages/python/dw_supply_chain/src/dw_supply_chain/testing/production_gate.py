"""Step 13's gate for tests that do not exercise it (slice PK).

The platform's rule (no pre-production test required) over no recorded
sub-flow: what every PO case had before this slice. A test of the gate itself
builds `ProductionGateResolver` over real or recorded stores instead.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.application.production_gate import ProductionGateResolver
from dw_supply_chain.domain.packaging_design import PackagingDesign
from dw_supply_chain.packaging_policy import SupplyChainPackagingPolicy

PLATFORM_DEFAULT_PACKAGING = SupplyChainPackagingPolicy(
    schema_version="1.0",
    policy_id="supply_chain_packaging",
    policy_version="1.0.0",
    require_pre_production_test=False,
)


class _NoDesigns:
    async def get(self, context: AccessContext, po_case_id: uuid.UUID) -> PackagingDesign | None:
        return None


class _NoOverrides:
    async def get(self, context: AccessContext, policy_id: str) -> dict[str, object] | None:
        return None

    async def put(
        self,
        context: AccessContext,
        policy_id: str,
        content: Mapping[str, object],
        *,
        audit: AuditEvent,
    ) -> None:
        raise NotImplementedError("not exercised by the open production gate")


def open_production_gate() -> ProductionGateResolver:
    return ProductionGateResolver(
        designs=_NoDesigns(),
        policy_override_repo=_NoOverrides(),
        platform_default=PLATFORM_DEFAULT_PACKAGING,
    )
