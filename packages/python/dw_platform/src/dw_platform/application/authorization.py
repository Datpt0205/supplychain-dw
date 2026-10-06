"""Scope-based authorization service (implements ``AuthorizationPort``).

Authorization is deliberately separate from entitlement (§15.1): this answers
"may this principal do this action", never "does the plan include it".
"""

from __future__ import annotations

from dataclasses import dataclass

from dw_kernel.errors import PermissionDeniedError
from dw_platform.application.access_context import AccessContext

PLATFORM_ADMIN_ROLE = "platform_admin"


def holds_stamped_scope(context: AccessContext, required_scope: str | None) -> bool:
    """Whether `context` may decide a request stamped with `required_scope` (ADR 0004).

    The one owner of this rule, read by the decision (`ApproveAndResumeService.decide`)
    and by the inbox that offers it, so the two cannot disagree. The caller must hold
    the scope itself: no role stands in for it, `platform_admin` included (ADR 0004,
    2026-10-06). A platform operator is not the business's board, and a stamped
    approval is a business decision. Unstamped (None) asks nothing beyond
    `approvals.decide`, which `ScopeAuthorizationService` still answers.
    """
    return required_scope is None or context.has_scope(required_scope)


def permission_denied(
    *, action: str, resource_type: str, resource_id: str | None = None
) -> PermissionDeniedError:
    """The refusal every scope check here raises, so a caller sees one shape."""
    return PermissionDeniedError(
        "action not permitted",
        details={
            "action": action,
            "resource_type": resource_type,
            **({"resource_id": resource_id} if resource_id else {}),
        },
    )


@dataclass(frozen=True)
class ScopeAuthorizationService:
    """RBAC via scopes resolved from roles at context-build time.

    ABAC refinements (ownership, department, classification) layer on top as
    domain policy objects in each bounded context; this service is the baseline
    route/tool gate.
    """

    admin_role: str = PLATFORM_ADMIN_ROLE

    async def require(
        self,
        *,
        context: AccessContext,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
    ) -> None:
        if self.is_allowed(context, action):
            return
        raise permission_denied(action=action, resource_type=resource_type, resource_id=resource_id)

    def is_allowed(self, context: AccessContext, action: str) -> bool:
        if self.admin_role in context.roles:
            return True
        return action in context.scopes
