"""The audit event of one thing done to a product-development case.

Its own module because two layers write it: the step command
(`application.product_cases`) and the BGĐ review graph
(`workflows.advance_product_case_graph`), which `product_cases` also reads the
review's approval type from. One format, `supply_chain.product_case.<action>`
on resource `product_dev_case`, for both.
"""

from __future__ import annotations

from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.domain.product_development_case import ProductDevelopmentCase

PRODUCT_CASE_RESOURCE = "product_dev_case"
_AUDIT_PREFIX = "supply_chain.product_case."


def product_case_audit(
    context: AccessContext,
    ids: IdGenerator,
    clock: UtcClock,
    case: ProductDevelopmentCase,
    action: str,
    details: dict[str, object],
) -> AuditEvent:
    """The audit event of one thing done to a case, by `context`'s principal:
    `supply_chain.product_case.<action>`. The step command and the review
    graph both write theirs through it."""
    return AuditEvent(
        id=ids.new_uuid(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        actor_id=UserId(context.principal_id),
        action=f"{_AUDIT_PREFIX}{action}",
        resource_type=PRODUCT_CASE_RESOURCE,
        resource_id=str(case.id),
        occurred_at=clock.now(),
        details=details,
    )
