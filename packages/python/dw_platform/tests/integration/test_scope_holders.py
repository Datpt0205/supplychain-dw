"""Integration: who holds a scope in a workspace, asked without an `AccessContext`.

`SqlScopeHolders.holding` (who should be told) and `.holds` (may this person,
now) read memberships through one query and `effective_scopes`, so a node
re-checking a decider at apply time and a lane addressing holders cannot
disagree with each other or with the access context. Real PostgreSQL, the
`dw_app` role, two tenants; tenant A has ws1 and ws2.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from pg_harness import DatabaseUrls
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dw_platform.adapters.persistence.scope_holders import SqlScopeHolders

pytestmark = pytest.mark.integration

SCOPE = "x.do"


@dataclass(frozen=True)
class World:
    tenant_a: uuid.UUID
    ws1: uuid.UUID
    ws2: uuid.UUID
    tenant_b: uuid.UUID
    ws_b: uuid.UUID
    by_role: uuid.UUID  # x.do by role at ws1; another role at ws2
    by_set: uuid.UUID  # x.do by permission set at ws1
    plain: uuid.UUID  # member of ws1 without x.do
    ws2_only: uuid.UUID  # x.do at ws2 only
    outsider: uuid.UUID  # no membership in tenant A
    b_holder: uuid.UUID  # x.do at tenant B's workspace

    @property
    def tenant_a_members(self) -> tuple[uuid.UUID, ...]:
        return (self.by_role, self.by_set, self.plain, self.ws2_only, self.outsider)


@pytest.fixture
async def migrator(db_urls: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(db_urls.migrator, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def world(migrator: AsyncEngine) -> World:
    """Fresh tenants, roles and people per test, so a test that locks tenant A
    cannot change what another test sees."""
    tag = uuid.uuid4().hex[:10]
    doer, other, doer_set = f"probe_doer_{tag}", f"probe_other_{tag}", f"probe_set_{tag}"
    w = World(*(uuid.uuid4() for _ in range(11)))
    async with migrator.begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO platform.roles (key, name, scopes) VALUES"
                " (:doer, 'Doer', CAST(:s AS jsonb)), (:other, 'Other', '[\"y.do\"]')"
            ),
            {"doer": doer, "other": other, "s": f'["{SCOPE}"]'},
        )
        await conn.execute(
            sa.text(
                "INSERT INTO platform.permission_sets (key, name, scopes)"
                " VALUES (:k, 'Set', CAST(:s AS jsonb))"
            ),
            {"k": doer_set, "s": f'["{SCOPE}"]'},
        )
        for tenant, slug in ((w.tenant_a, "a"), (w.tenant_b, "b")):
            await conn.execute(
                sa.text(
                    "INSERT INTO platform.tenants (id, slug, name, status)"
                    " VALUES (:id, :slug, :slug, 'active')"
                ),
                {"id": tenant, "slug": f"sh-{slug}-{tag}"},
            )
        for ws, tenant, slug in (
            (w.ws1, w.tenant_a, "ws1"),
            (w.ws2, w.tenant_a, "ws2"),
            (w.ws_b, w.tenant_b, "wsb"),
        ):
            await conn.execute(
                sa.text(
                    "INSERT INTO platform.workspaces (id, tenant_id, slug, name)"
                    " VALUES (:id, :t, :slug, :slug)"
                ),
                {"id": ws, "t": tenant, "slug": slug},
            )
        for user in (*w.tenant_a_members, w.b_holder):
            await conn.execute(
                sa.text(
                    "INSERT INTO platform.users (id, subject, display_name)"
                    " VALUES (:id, :sub, 'Probe')"
                ),
                {"id": user, "sub": f"sh-{user}"},
            )
        for tenant, ws, user, roles, sets in (
            (w.tenant_a, w.ws1, w.by_role, [doer], []),
            (w.tenant_a, w.ws2, w.by_role, [other], []),
            (w.tenant_a, w.ws1, w.by_set, [other], [doer_set]),
            (w.tenant_a, w.ws1, w.plain, [other], []),
            (w.tenant_a, w.ws2, w.ws2_only, [doer], []),
            (w.tenant_b, w.ws_b, w.b_holder, [doer], []),
        ):
            await conn.execute(
                sa.text(
                    "INSERT INTO platform.memberships"
                    " (id, tenant_id, workspace_id, user_id, role_keys, permission_set_keys)"
                    " VALUES (:id, :t, :ws, :u, CAST(:r AS jsonb), CAST(:p AS jsonb))"
                ),
                {
                    "id": uuid.uuid4(),
                    "t": tenant,
                    "ws": ws,
                    "u": user,
                    "r": _json(roles),
                    "p": _json(sets),
                },
            )
    return w


def _json(keys: list[str]) -> str:
    return "[" + ", ".join(f'"{k}"' for k in keys) + "]"


@pytest.fixture
async def holders(db_urls: DatabaseUrls) -> AsyncIterator[SqlScopeHolders]:
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield SqlScopeHolders(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


async def _lock(migrator: AsyncEngine, tenant: uuid.UUID) -> None:
    async with migrator.begin() as conn:
        await conn.execute(
            sa.text("UPDATE platform.tenants SET status = 'locked' WHERE id = :id"), {"id": tenant}
        )


# ---- holds -----------------------------------------------------------------


async def test_holds_answers_per_workspace(world: World, holders: SqlScopeHolders) -> None:
    w = world
    assert await holders.holds(w.tenant_a, w.ws1, w.by_role, SCOPE) is True
    # The same person in ws2, where their role does not carry the scope.
    assert await holders.holds(w.tenant_a, w.ws2, w.by_role, SCOPE) is False
    # No membership in ws1 at all.
    assert await holders.holds(w.tenant_a, w.ws1, w.ws2_only, SCOPE) is False
    assert await holders.holds(w.tenant_a, w.ws1, w.outsider, SCOPE) is False
    # A member of ws1 whose roles do not carry it.
    assert await holders.holds(w.tenant_a, w.ws1, w.plain, SCOPE) is False


async def test_a_scope_from_a_permission_set_counts(world: World, holders: SqlScopeHolders) -> None:
    assert await holders.holds(world.tenant_a, world.ws1, world.by_set, SCOPE) is True


async def test_an_empty_scope_is_never_held(world: World, holders: SqlScopeHolders) -> None:
    assert await holders.holds(world.tenant_a, world.ws1, world.by_role, "") is False


async def test_a_locked_tenant_has_no_holders(
    world: World, holders: SqlScopeHolders, migrator: AsyncEngine
) -> None:
    w = world
    await _lock(migrator, w.tenant_a)
    assert await holders.holds(w.tenant_a, w.ws1, w.by_role, SCOPE) is False
    assert await holders.holding(w.tenant_a, w.ws1, frozenset({SCOPE})) == []


async def test_another_tenant_cannot_ask_about_tenant_a(
    world: World, holders: SqlScopeHolders
) -> None:
    """RLS bounds the rows to the bound tenant, and the predicate says so again."""
    w = world
    assert await holders.holds(w.tenant_b, w.ws1, w.by_role, SCOPE) is False
    assert await holders.holding(w.tenant_b, w.ws1, frozenset({SCOPE})) == []


async def test_a_pooled_connection_carries_no_tenant_onward(
    world: World, db_urls: DatabaseUrls
) -> None:
    """One connection, reused: the second call (tenant B) must not see tenant
    A's members, and the connection holds no tenant once returned."""
    w = world
    engine = create_async_engine(db_urls.app, pool_size=1, max_overflow=0)
    try:
        pooled = SqlScopeHolders(async_sessionmaker(engine, expire_on_commit=False))
        assert await pooled.holding(w.tenant_a, w.ws1, frozenset({SCOPE})) == sorted(
            [w.by_role, w.by_set]
        )
        assert await pooled.holds(w.tenant_a, w.ws1, w.by_role, SCOPE) is True
        assert await pooled.holding(w.tenant_b, w.ws1, frozenset({SCOPE})) == []
        assert await pooled.holds(w.tenant_b, w.ws1, w.by_role, SCOPE) is False
        assert await pooled.holding(w.tenant_b, w.ws_b, frozenset({SCOPE})) == [w.b_holder]
        async with engine.connect() as conn:
            leftover = await conn.scalar(sa.text("SELECT current_setting('app.tenant_id', true)"))
        assert leftover in (None, "")
    finally:
        await engine.dispose()


# ---- holding ---------------------------------------------------------------


async def test_holding_lists_the_holders_of_one_workspace(
    world: World, holders: SqlScopeHolders
) -> None:
    w = world
    found = await holders.holding(w.tenant_a, w.ws1, frozenset({SCOPE}))
    assert found == sorted([w.by_role, w.by_set])
    assert w.ws2_only not in found  # holds it in ws2 only
    assert w.plain not in found  # member of ws1, no scope


async def test_holding_in_another_workspace_excludes_ws1_only_holders(
    world: World, holders: SqlScopeHolders
) -> None:
    w = world
    found = await holders.holding(w.tenant_a, w.ws2, frozenset({SCOPE}))
    assert found == [w.ws2_only]
    assert w.by_set not in found


async def test_holding_nothing_asks_nothing(world: World, holders: SqlScopeHolders) -> None:
    assert await holders.holding(world.tenant_a, world.ws1, frozenset()) == []


async def test_holding_and_holds_agree_for_every_member(
    world: World, holders: SqlScopeHolders
) -> None:
    """The one-owner check: "who is told" and "who may" give one answer."""
    w = world
    for ws in (w.ws1, w.ws2):
        for scope in (SCOPE, "y.do"):
            listed = set(await holders.holding(w.tenant_a, ws, frozenset({scope})))
            for user in w.tenant_a_members:
                assert (user in listed) == await holders.holds(w.tenant_a, ws, user, scope), (
                    ws,
                    scope,
                    user,
                )


# ---- members ---------------------------------------------------------------


async def test_members_keeps_only_those_who_belong_to_the_workspace(
    world: World, holders: SqlScopeHolders
) -> None:
    """A named person (a case's PIC) is told only while they belong where the
    case is: another workspace's member and someone outside the tenant drop."""
    w = world
    asked = frozenset({w.by_role, w.plain, w.ws2_only, w.outsider, w.b_holder})
    assert await holders.members(w.tenant_a, w.ws1, asked) == {w.by_role, w.plain}
    assert await holders.members(w.tenant_a, w.ws2, asked) == {w.by_role, w.ws2_only}
    assert await holders.members(w.tenant_a, w.ws1, frozenset()) == frozenset()


async def test_members_answers_nothing_across_tenants_or_for_a_locked_one(
    world: World, holders: SqlScopeHolders, migrator: AsyncEngine
) -> None:
    w = world
    asked = frozenset({w.by_role, w.plain, w.b_holder})
    # Tenant B bound, tenant A's workspace named: nothing, not an error.
    assert await holders.members(w.tenant_b, w.ws1, asked) == frozenset()
    await _lock(migrator, w.tenant_a)
    assert await holders.members(w.tenant_a, w.ws1, asked) == frozenset()
