"""How a request names its caller and its tenant, in one place.

Two HTTP front doors read the same headers, and they had drifted: one required
the exact bytes ``Bearer `` and accepted an empty token, the other compared the
scheme case-insensitively and rejected a blank one. RFC 7235 makes the scheme
case-insensitive, so the same legal request was accepted by one and refused by
the other.

Pure string handling, so both apps can share it without either depending on the
other or on a web framework.
"""

from __future__ import annotations

import uuid

from dw_kernel.errors import TenantContextMissingError, UnauthenticatedError

BEARER_SCHEME = "bearer"
TENANT_HEADER = "X-Tenant-Id"
WORKSPACE_HEADER = "X-Workspace-Id"


def bearer_token(authorization: str | None) -> str:
    """The token from an ``Authorization`` header value, or refuse the request."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != BEARER_SCHEME or not token.strip():
        raise UnauthenticatedError("missing bearer token")
    return token.strip()


def uuid_header(name: str, raw: str | None) -> uuid.UUID | None:
    """Parse a tenant/workspace header, refusing anything that is not a UUID."""
    if raw is None:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise TenantContextMissingError(f"{name} is not a valid UUID") from exc
