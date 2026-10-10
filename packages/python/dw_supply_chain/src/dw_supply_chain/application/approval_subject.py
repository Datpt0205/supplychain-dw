"""The version of the product case an approval decides on (zalo-channel ticket 05).

Satisfies the platform's `ApprovalSubjectVersionPort` structurally for every
approval type under `supply_chain.product_action.` — BGĐ's review at step 6
and the sign-off at step 9 — registered by that prefix at each composition
root. A decision taken in a chat after a view on the portal is accepted only if
the case is still the version the view saw (ADR 0014 point 4): a case changed
since (a new sample round, a cancellation, any step) asks for a new view.

The case is named by the approval payload's `product_dev_case_id`, which the
review graph writes from the case itself, and read under the caller's own
context, so another workspace's case is not found and gives no version.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from dw_platform.application.access_context import AccessContext
from dw_platform.domain.approval import ApprovalRequest
from dw_supply_chain.domain.product_development_case import (
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
)
from dw_supply_chain.workflows.advance_product_case_graph import BOD_REVIEW_CASE_KEY


class ProductCaseLookupPort(Protocol):
    async def get(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> ProductDevelopmentCase | None: ...


@dataclass(frozen=True)
class ProductCaseApprovalSubject:
    cases: ProductCaseLookupPort

    async def version_of(self, context: AccessContext, request: ApprovalRequest) -> str | None:
        raw = request.payload.get(BOD_REVIEW_CASE_KEY)
        try:
            case_id = uuid.UUID(str(raw))
        except ValueError:
            return None
        case = await self.cases.get(context, ProductDevelopmentCaseId(case_id))
        return None if case is None else str(case.version)
