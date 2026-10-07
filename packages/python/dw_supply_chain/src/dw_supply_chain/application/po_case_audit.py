"""The audit event of one thing done to a PO case.

Its own module because two layers write it: the PO commands
(`application.handlers`) and the approval-gated step graph
(`workflows.advance_case_graph`), which `handlers` also reads the approval
prefix from. One format, `supply_chain.po_case.<action>` on resource
`po_case`, for both — the PO twin of `product_case_audit`.
"""

from __future__ import annotations

from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.domain.po_case import POCaseId

PO_CASE_RESOURCE = "po_case"
_AUDIT_PREFIX = "supply_chain.po_case."


def po_case_audit(
    context: AccessContext,
    ids: IdGenerator,
    clock: UtcClock,
    case_id: POCaseId,
    action: str,
    details: dict[str, object],
) -> AuditEvent:
    """The audit event of one thing done to a PO case, by `context`'s
    principal, in `context`'s tenant and workspace — never the case's own
    fields, so an event cannot name a tenant the caller is not in."""
    return AuditEvent(
        id=ids.new_uuid(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        actor_id=UserId(context.principal_id),
        action=f"{_AUDIT_PREFIX}{action}",
        resource_type=PO_CASE_RESOURCE,
        resource_id=str(case_id),
        occurred_at=clock.now(),
        details=details,
    )
