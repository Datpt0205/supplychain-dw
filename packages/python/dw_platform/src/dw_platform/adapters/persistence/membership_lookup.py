"""SQL implementation of the membership lookup used to build AccessContext.

The requested tenant becomes the RLS context for the lookup transaction itself:
if the caller is not a member, RLS + the membership predicate return no rows and
access is denied. The client-requested tenant is only ever used as a filter that
must be CONFIRMED by data, never trusted directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.hierarchy_repo import SUBTREE_QUERY
from dw_platform.application.authorization import PLATFORM_ADMIN_ROLE
from dw_platform.application.identity import MembershipAccess

_SET_LOOKUP_CONTEXT = text("SELECT set_config('app.tenant_id', :tenant_id, true)")
# Leadership scope: sees every record in the workspace even in a restricted
# tenant (ADR-003), so the roll-up never narrows a director's record lists.
_RECORDS_ALL_READ = "crm.records.all.read"


async def effective_scopes(
    session: AsyncSession, role_keys: frozenset[str], permission_set_keys: frozenset[str]
) -> frozenset[str]:
    """A membership's scopes: its roles' and its permission sets', unioned.

    Permission Sets (ADR-001 Phase 3) are additive scopes attached to a
    membership on top of its role, so a set grants a capability without a new
    role. The one reading of that rule on this side of the database; the
    access context and "who in this workspace holds X" both ask it."""
    scopes: set[str] = set()
    if role_keys:
        for (role_scopes,) in await session.execute(
            sa.select(tables.roles.c.scopes).where(tables.roles.c.key.in_(role_keys))
        ):
            scopes.update(role_scopes)
    if permission_set_keys:
        for (set_scopes,) in await session.execute(
            sa.select(tables.permission_sets.c.scopes).where(
                tables.permission_sets.c.key.in_(permission_set_keys)
            )
        ):
            scopes.update(set_scopes)
    return frozenset(scopes)


@dataclass(frozen=True)
class SqlMembershipLookup:
    """Implements ``MembershipLookupPort``."""

    session_factory: async_sessionmaker[AsyncSession]

    async def find_access(
        self,
        subject: str,
        issuer: str,
        tenant_id: UUID,
        workspace_id: UUID,
    ) -> MembershipAccess | None:
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_LOOKUP_CONTEXT, {"tenant_id": str(tenant_id)})

            # The verified identity resolves to a platform user either directly
            # (dev tokens store the subject on users.subject) or via an external
            # identity mapping (Keycloak sub -> platform user).
            candidate_users = (
                sa.select(tables.users.c.id)
                .where(tables.users.c.subject == subject)
                .union(
                    sa.select(tables.external_identities.c.user_id).where(
                        tables.external_identities.c.issuer == issuer,
                        tables.external_identities.c.subject == subject,
                        # A linked chat is where to send, not who is signing in.
                        tables.external_identities.c.provider.not_in(tables.CHANNEL_LINK_PROVIDERS),
                    )
                )
            )

            # Membership and the tenant's status in one round-trip. A locked
            # tenant is frozen: even a valid member is denied an AccessContext, so
            # the Platform Operator's lock (ADR-002) actually bites instead of
            # only setting a column. Joining `tenants` here reads its status
            # alongside the membership rather than paying a second query for a
            # column keyed on the same tenant.
            membership_row = (
                await session.execute(
                    sa.select(
                        tables.memberships.c.user_id,
                        tables.memberships.c.role_keys,
                        tables.memberships.c.permission_set_keys,
                        tables.memberships.c.clearance,
                        tables.tenants.c.status.label("tenant_status"),
                        tables.tenants.c.record_visibility,
                        tables.tenants.c.max_autonomy_level,
                    )
                    .select_from(
                        tables.memberships.join(
                            tables.tenants, tables.tenants.c.id == tables.memberships.c.tenant_id
                        )
                    )
                    .where(
                        tables.memberships.c.user_id.in_(candidate_users),
                        tables.memberships.c.tenant_id == tenant_id,
                        tables.memberships.c.workspace_id == workspace_id,
                    )
                )
            ).first()
            # Fail closed: no membership, or a tenant that is not active, denies —
            # an unreadable/absent status is not "active" and denies too.
            if membership_row is None or membership_row.tenant_status != "active":
                return None

            role_keys = frozenset(membership_row.role_keys)
            scopes = set(
                await effective_scopes(
                    session, role_keys, frozenset(membership_row.permission_set_keys)
                )
            )

            entitlement_row = (
                await session.execute(
                    sa.select(
                        tables.entitlements.c.plan_id,
                        tables.entitlements.c.feature_overrides,
                        tables.plans.c.features,
                    )
                    .select_from(
                        tables.entitlements.join(
                            tables.plans,
                            tables.entitlements.c.plan_id == tables.plans.c.plan_id,
                        )
                    )
                    .where(tables.entitlements.c.tenant_id == tenant_id)
                )
            ).first()
            if entitlement_row is None:
                return None  # tenant without a plan: fail closed

            feature_flags = frozenset(entitlement_row.features) | frozenset(
                entitlement_row.feature_overrides
            )

            # Record-visibility roll-up (ADR-003): in a restricted tenant a caller
            # without the sees-everything scope is limited to the records owned by
            # their subtree (self + reports). None means no limit — an open tenant
            # or leadership. Computed once here and carried on the context so read
            # paths filter on it rather than re-walking the tree per query.
            #
            # "Sees everything" mirrors the authorization port, not just the literal
            # scope: a platform admin bypasses every scope check (authorization.py),
            # so it must bypass the roll-up too — otherwise an admin in a restricted
            # tenant is narrowed to its own (usually empty) subtree and sees nothing.
            sees_everything = _RECORDS_ALL_READ in scopes or PLATFORM_ADMIN_ROLE in role_keys
            visible_owners: frozenset[UUID] | None = None
            if membership_row.record_visibility == "restricted" and not sees_everything:
                subtree = await session.execute(
                    SUBTREE_QUERY,
                    {
                        "tenant": str(tenant_id),
                        "ws": str(workspace_id),
                        "root": str(membership_row.user_id),
                    },
                )
                visible_owners = frozenset(row.user_id for row in subtree)

            return MembershipAccess(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                principal_id=membership_row.user_id,
                roles=role_keys,
                scopes=frozenset(scopes),
                groups=frozenset(),
                clearance=membership_row.clearance,
                plan_id=entitlement_row.plan_id,
                feature_flags=feature_flags,
                record_visibility=membership_row.record_visibility,
                visible_owners=visible_owners,
                max_autonomy_level=membership_row.max_autonomy_level,
            )
