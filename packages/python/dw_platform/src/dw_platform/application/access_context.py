"""Trusted access context built at the boundary.

The context is constructed only from a verified token plus database membership.
It is passed explicitly; domain/application code never reads tenant or user
identity from globals, headers or model output.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from dw_kernel.autonomy import FAIL_CLOSED_LEVEL, AutonomyLevel


class SupportScope(BaseModel):
    """The customer's grant a support context was built from (ADR 0024)."""

    model_config = ConfigDict(frozen=True)

    grant_id: UUID
    code: str
    resource_type: str
    resource_id: UUID | None
    scope_set_key: str


class AccessContext(BaseModel):
    """Immutable, verified identity + tenancy + entitlement snapshot."""

    model_config = ConfigDict(frozen=True)

    tenant_id: UUID
    workspace_id: UUID
    principal_id: UUID
    roles: frozenset[str]
    groups: frozenset[str] = frozenset()
    scopes: frozenset[str] = frozenset()
    clearance: str = "internal"
    plan_id: str
    feature_flags: frozenset[str] = frozenset()
    # Whether this tenant enforces the hierarchy roll-up on record reads (ADR-003).
    # "open" (default) = whole workspace visible; "restricted" = subtree only.
    record_visibility: str = "open"
    # The owners whose records this caller may see when the tenant is restricted:
    # self + everyone reporting up to them. None means "no record-owner limit"
    # (an open tenant, or a caller who holds the sees-everything scope). Resolved
    # once by the lookup; read paths filter on it instead of re-deriving the tree.
    visible_owners: frozenset[UUID] | None = None
    # The most autonomy this tenant lets any of its workers run at. Defaults to
    # the most restrictive level, not the most permissive: the factory always
    # sets it from the tenant, so the default is only ever reached by a context
    # built some other way — and one that cannot say what the tenant allows must
    # not be treated as allowing everything. `record_visibility` above defaults
    # the other way; this deliberately does not follow it.
    max_autonomy_level: AutonomyLevel = FAIL_CLOSED_LEVEL
    # Set only on a support context (ADR 0024): a support staff member working
    # under a customer's grant, with no role and the grant's stamped scopes.
    # Never cached, never built from a membership.
    support: SupportScope | None = None

    @field_validator("plan_id", "clearance")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def has_any_role(self, *roles: str) -> bool:
        return any(role in self.roles for role in roles)

    def has_feature(self, flag: str) -> bool:
        return flag in self.feature_flags
