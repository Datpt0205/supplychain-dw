"""FastAPI dependency that turns a request into a trusted AccessContext.

Flow: bearer token → TokenVerifierPort → requested tenant/workspace headers →
membership confirmation in DB → AccessContext. Nothing client-supplied is
trusted without verification.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from dw_api.bootstrap import ApiContainer
from dw_kernel.errors import InfrastructureError, PermissionDeniedError
from dw_kernel.http_auth import TENANT_HEADER, WORKSPACE_HEADER, bearer_token, uuid_header
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import VerifiedIdentity
from dw_platform.application.provisioning import ProvisioningContext
from dw_platform.application.support_access import SupportRefusal, support_access_refused

# A support staff member names the customer's grant they act under (ADR 0024).
SUPPORT_GRANT_HEADER = "X-DW-Support-Grant"


def get_container(request: Request) -> ApiContainer:
    container: ApiContainer = request.app.state.container
    return container


async def get_verified_identity(
    request: Request,
    container: Annotated[ApiContainer, Depends(get_container)],
) -> VerifiedIdentity:
    """Verify the bearer token only — no tenant/workspace membership required.

    Used by /auth/bootstrap, which answers "who am I and which workspaces can I
    enter?" before a workspace has been selected.
    """
    if container.token_verifier is None:
        raise InfrastructureError(
            "authentication is not configured",
            details={"hint": "set DW_API_DATABASE_URL and DW_API_DEV_SECRET (or OIDC)"},
        )
    return await container.token_verifier.verify(bearer_token(request.headers.get("Authorization")))


RequireVerifiedIdentity = Annotated[VerifiedIdentity, Depends(get_verified_identity)]


async def get_access_context(
    request: Request,
    container: Annotated[ApiContainer, Depends(get_container)],
) -> AccessContext:
    # Default deny for support (ADR 0024): a route that has not opted in with
    # RequireAccessContextOrSupport refuses a support context outright, rather
    # than relying on the grant's scopes, which only stop routes that check one.
    if request.headers.get(SUPPORT_GRANT_HEADER) is not None:
        raise support_access_refused(
            SupportRefusal.CONTEXT_NOT_ALLOWED,
            "this route is not open to support access",
        )
    if container.token_verifier is None or container.access_context_factory is None:
        raise InfrastructureError(
            "authentication is not configured",
            details={"hint": "set DW_API_DATABASE_URL and DW_API_DEV_SECRET (or OIDC)"},
        )

    identity = await container.token_verifier.verify(
        bearer_token(request.headers.get("Authorization"))
    )
    return await container.access_context_factory.build(
        identity,
        uuid_header(TENANT_HEADER, request.headers.get(TENANT_HEADER)),
        uuid_header(WORKSPACE_HEADER, request.headers.get(WORKSPACE_HEADER)),
    )


RequireAccessContext = Annotated[AccessContext, Depends(get_access_context)]


async def get_access_context_or_support(
    request: Request,
    container: Annotated[ApiContainer, Depends(get_container)],
) -> AccessContext:
    """A member's context, or a support staff member's under a customer's grant.

    Only for the routes in ``SUPPORT_ALLOWED_ROUTES`` (composition root); a
    test fails when this dependency appears anywhere else. With the header,
    tenant and workspace come from the grant, never from the request's own
    headers, and every request appends one `support.access` to the customer's
    audit trail.
    """
    raw = request.headers.get(SUPPORT_GRANT_HEADER)
    if raw is None:
        return await get_access_context(request, container)
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    # The list is enforced here as well as by the route-table test: a route
    # given this dependency without its entry still refuses support.
    if (request.method, template) not in container.support_allowed_routes:
        raise support_access_refused(
            SupportRefusal.CONTEXT_NOT_ALLOWED,
            "this route is not open to support access",
        )
    grant_id = uuid_header(SUPPORT_GRANT_HEADER, raw)
    if (
        container.token_verifier is None
        or container.identity_bootstrap is None
        or container.support_access is None
        or container.support_access_audit is None
        or grant_id is None
    ):
        raise InfrastructureError("support access is not configured")
    identity = await container.token_verifier.verify(
        bearer_token(request.headers.get("Authorization"))
    )
    view = await container.identity_bootstrap.bootstrap(identity)
    context = await container.support_access.build(
        principal_id=view.principal_id,
        auth_methods=identity.auth_methods,
        grant_id=grant_id,
    )
    await container.support_access_audit.record_access(
        container.support_access.access_event(
            context,
            method=request.method,
            route=template or request.url.path,
            path_ids={name: str(value) for name, value in request.path_params.items()},
        )
    )
    return context


RequireAccessContextOrSupport = Annotated[AccessContext, Depends(get_access_context_or_support)]


async def get_provisioning_context(
    request: Request,
    container: Annotated[ApiContainer, Depends(get_container)],
) -> ProvisioningContext:
    """Verify the token, resolve the identity, and require Platform Operator.

    Tenant-less by design (ADR-002): no tenant/workspace headers. A non-operator
    can never obtain this context, so every /platform route is gated at the door.
    """
    if (
        container.token_verifier is None
        or container.identity_bootstrap is None
        or container.provisioning is None
    ):
        raise InfrastructureError(
            "provisioning is not configured",
            details={"hint": "set DW_PROVISIONER_DATABASE_URL"},
        )
    identity = await container.token_verifier.verify(
        bearer_token(request.headers.get("Authorization"))
    )
    view = await container.identity_bootstrap.bootstrap(identity)
    if not view.is_platform_operator:
        raise PermissionDeniedError(
            "platform operator required", details={"action": "platform.provision"}
        )
    return ProvisioningContext(principal_id=view.principal_id)


RequireProvisioningContext = Annotated[ProvisioningContext, Depends(get_provisioning_context)]
