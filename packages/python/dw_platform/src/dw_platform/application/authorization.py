"""Scope-based authorization service (implements ``AuthorizationPort``).

Authorization is deliberately separate from entitlement (§15.1): this answers
"may this principal do this action", never "does the plan include it".
"""

from __future__ import annotations

from dataclasses import dataclass

from dw_kernel.errors import PermissionDeniedError
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.approval import APPROVALS_DECIDE, ApprovalRequest

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


@dataclass(frozen=True, slots=True)
class ApprovalAudience:
    """The caller, as deciding an approval and seeing it both read them (ADR 0004).

    One rule for the write and the reads: `may_decide` is the two checks
    `ApproveAndResumeService.decide` runs, and `may_see` is who a request is
    listed and served to. A stamped request (`required_scope`) is seen only by
    someone who may decide it and by its requester; to everyone else it is
    absent, the same answer as a request that never existed. An unstamped one
    is seen by every member who may read the inbox, as before. The repository's
    SQL filter is `may_see` in another language; `test_approval_visibility.py`
    holds the two together.
    """

    context: AccessContext
    # `approvals.decide` as `ScopeAuthorizationService` answers it, so the
    # admin rule applies here exactly as it does to the decision; the stamp
    # does not take that rule (`holds_stamped_scope`).
    holds_decide: bool

    @classmethod
    def of(
        cls, context: AccessContext, authorization: ScopeAuthorizationService
    ) -> ApprovalAudience:
        return cls(
            context=context, holds_decide=authorization.is_allowed(context, APPROVALS_DECIDE)
        )

    def may_decide(self, request: ApprovalRequest) -> bool:
        return self.holds_decide and holds_stamped_scope(self.context, request.required_scope)

    def may_see(self, request: ApprovalRequest) -> bool:
        return (
            request.required_scope is None
            or request.requested_by.value == self.context.principal_id
            or self.may_decide(request)
        )
