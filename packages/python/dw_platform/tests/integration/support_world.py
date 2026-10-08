"""A world for customer-granted support access tests (ADR 0024).

Fresh tenants, roles and people per test, built as the migrator; every
operation under test then runs as `dw_app` (customer side) or `dw_provisioner`
(operator side). Shared by the lifecycle tests and the support context tests.

Tenant A (flag `support_access` on) has ws1 and ws2; tenant B (flag on) has
wsB; tenant C has the flag off. In ws1 of A: a granter (role carrying
`support.grant` and `x.read`), a narrow granter (`support.grant` only), a
requester (`org_admin`, which carries `support.request`), a plain member (no
`support.*`). In ws2 of A: a granter of ws2 only. A staff member is listed as
support staff by the operator.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from dw_kernel.ports import FixedClock, Uuid4Generator
from dw_platform.adapters.persistence.membership_lookup import SqlMembershipLookup
from dw_platform.adapters.persistence.provisioning_repo import SqlProvisioningRepository
from dw_platform.adapters.persistence.scope_holders import SqlScopeHolders
from dw_platform.adapters.persistence.support_grants import SqlSupportGrantRepository
from dw_platform.application.access_context import AccessContext
from dw_platform.application.identity import context_from
from dw_platform.application.provisioning import ProvisioningContext, ProvisioningService
from dw_platform.application.support_access import (
    SupportGrantService,
    SupportScopeCatalog,
)

SET_KEY = "probe.read"
SET_SCOPES = frozenset({"x.read"})
ITEM = uuid.UUID(int=0x17E)
ISSUER = "https://issuer.test/realms/dw"


class ProbeDescriber:
    """A context's describer: names one item, knows nothing else."""

    async def describe(
        self, context: AccessContext, resource_type: str, resource_id: uuid.UUID
    ) -> str | None:
        return "Probe item" if resource_id == ITEM else None


def probe_catalog() -> SupportScopeCatalog:
    catalog = SupportScopeCatalog()
    catalog.register(SET_KEY, "Read probes", SET_SCOPES, {"workspace", "probe_item"})
    catalog.register_resource("probe_item", ProbeDescriber())
    catalog.freeze()
    return catalog


@dataclass
class SupportWorld:
    tag: str
    tenant_a: uuid.UUID
    ws1: uuid.UUID
    ws2: uuid.UUID
    tenant_b: uuid.UUID
    ws_b: uuid.UUID
    tenant_c: uuid.UUID
    ws_c: uuid.UUID
    granter_role: str
    users: dict[str, uuid.UUID] = field(default_factory=dict)

    def email(self, name: str) -> str:
        return f"{name}-{self.tag}@support.test"

    @property
    def operator(self) -> ProvisioningContext:
        return ProvisioningContext(principal_id=self.users["operator"])


async def build_world(migrator: AsyncEngine) -> SupportWorld:
    tag = uuid.uuid4().hex[:10]
    ids = [uuid.uuid4() for _ in range(7)]
    w = SupportWorld(
        tag=tag,
        tenant_a=ids[0],
        ws1=ids[1],
        ws2=ids[2],
        tenant_b=ids[3],
        ws_b=ids[4],
        tenant_c=ids[5],
        ws_c=ids[6],
        granter_role=f"probe_granter_{tag}",
    )
    narrow_role, plain_role = f"probe_narrow_{tag}", f"probe_plain_{tag}"
    people = (
        "granter",
        "granter2",
        "narrow",
        "requester",
        "plain",
        "granter_ws2",
        "b_granter",
        "c_granter",
        "staff",
        "other_staff",
        "not_staff",
        "operator",
    )
    w.users = {name: uuid.uuid4() for name in people}
    async with migrator.begin() as conn:
        for key, scopes in (
            (w.granter_role, '["support.grant", "x.read", "x.write"]'),
            (narrow_role, '["support.grant"]'),
            (plain_role, '["x.read"]'),
        ):
            await conn.execute(
                sa.text(
                    "INSERT INTO platform.roles (key, name, scopes)"
                    " VALUES (:k, :k, CAST(:s AS jsonb))"
                ),
                {"k": key, "s": scopes},
            )
        for tenant, ws_list, flagged in (
            (w.tenant_a, (w.ws1, w.ws2), True),
            (w.tenant_b, (w.ws_b,), True),
            (w.tenant_c, (w.ws_c,), False),
        ):
            await conn.execute(
                sa.text(
                    "INSERT INTO platform.tenants (id, slug, name, status)"
                    " VALUES (:id, :slug, :name, 'active')"
                ),
                {
                    "id": tenant,
                    "slug": f"sg-{tenant.hex[:12]}",
                    "name": f"Company {tenant.hex[:4]}",
                },
            )
            await conn.execute(
                sa.text(
                    "INSERT INTO platform.entitlements (id, tenant_id, plan_id, feature_overrides)"
                    " VALUES (:id, :t, 'professional', CAST(:f AS jsonb))"
                ),
                {
                    "id": uuid.uuid4(),
                    "t": tenant,
                    "f": '["support_access"]' if flagged else "[]",
                },
            )
            for n, ws in enumerate(ws_list):
                await conn.execute(
                    sa.text(
                        "INSERT INTO platform.workspaces (id, tenant_id, slug, name)"
                        " VALUES (:id, :t, :slug, :name)"
                    ),
                    {
                        "id": ws,
                        "t": tenant,
                        "slug": "main" if n == 0 else f"ws{n}",
                        "name": f"Workspace {n + 1}",
                    },
                )
        for name, user in w.users.items():
            await conn.execute(
                sa.text(
                    "INSERT INTO platform.users (id, subject, email, display_name)"
                    " VALUES (:id, :sub, :email, :name)"
                ),
                {"id": user, "sub": f"sg-{user}", "email": w.email(name), "name": name},
            )
        for tenant, ws, name, roles in (
            (w.tenant_a, w.ws1, "granter", [w.granter_role]),
            (w.tenant_a, w.ws1, "granter2", [w.granter_role]),
            (w.tenant_a, w.ws1, "narrow", [narrow_role]),
            (w.tenant_a, w.ws1, "requester", ["org_admin"]),
            (w.tenant_a, w.ws1, "plain", [plain_role]),
            (w.tenant_a, w.ws2, "granter_ws2", [w.granter_role]),
            (w.tenant_b, w.ws_b, "b_granter", [w.granter_role]),
            (w.tenant_c, w.ws_c, "c_granter", [w.granter_role]),
        ):
            await conn.execute(
                sa.text(
                    "INSERT INTO platform.memberships"
                    " (id, tenant_id, workspace_id, user_id, role_keys)"
                    " VALUES (:id, :t, :ws, :u, CAST(:r AS jsonb))"
                ),
                {
                    "id": uuid.uuid4(),
                    "t": tenant,
                    "ws": ws,
                    "u": w.users[name],
                    "r": "[" + ", ".join(f'"{r}"' for r in roles) + "]",
                },
            )
    return w


async def context_of(
    app_engine: AsyncEngine, world: SupportWorld, name: str, tenant: uuid.UUID, ws: uuid.UUID
) -> AccessContext:
    """The access context sign-in would build: the real lookup, the real flags."""
    lookup = SqlMembershipLookup(async_sessionmaker(app_engine, expire_on_commit=False))
    access = await lookup.find_access(f"sg-{world.users[name]}", ISSUER, tenant, ws)
    assert access is not None, name
    return context_from(access)


def grant_service(
    app_engine: AsyncEngine, clock: FixedClock, catalog: SupportScopeCatalog | None = None
) -> SupportGrantService:
    factory = async_sessionmaker(app_engine, expire_on_commit=False)
    return SupportGrantService(
        repo=SqlSupportGrantRepository(factory),
        member_scopes=SqlScopeHolders(factory),
        catalog=catalog or probe_catalog(),
        clock=clock,
        ids=Uuid4Generator(),
    )


def provisioning(provisioner_engine: AsyncEngine, clock: FixedClock) -> ProvisioningService:
    repo = SqlProvisioningRepository(async_sessionmaker(provisioner_engine, expire_on_commit=False))
    return ProvisioningService(repo=repo, clock=clock, ids=Uuid4Generator())
