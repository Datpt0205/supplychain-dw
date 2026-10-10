"""Integration: the platform half of inbound chat commands, as ``dw_app`` on Postgres.

Three things the router's unit tests cannot show:

* a message id is claimed once even when two deliveries race in two real
  transactions, and an old id is pruned after seven days;
* ``platform.channel_preferences`` is the person's own row: a connection that
  binds no principal reads nothing, and principal A neither reads nor writes
  B's row; the row goes with the membership;
* the context a linked person's command runs with is that person's membership,
  in a tenant that is active, with the scopes cut to the command's ceiling and
  no role — and a linked chat still resolves no sign-in.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from pg_harness import DatabaseUrls
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.channel_inbound import (
    SqlChannelInboundLedger,
    SqlChannelInboundRetention,
)
from dw_platform.adapters.persistence.channel_preferences import SqlChannelPreferences
from dw_platform.adapters.persistence.membership_lookup import SqlMembershipLookup
from dw_platform.application.channel_access import LinkedUserAccess

pytestmark = pytest.mark.integration

Sessions = async_sessionmaker[AsyncSession]

_PROPOSE = frozenset({"knowledge.write"})  # a stand-in ceiling held by `member`


@pytest.fixture
async def migrator(db_urls: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(db_urls.migrator, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def app_engine(db_urls: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
def sessions(app_engine: AsyncEngine) -> Sessions:
    return async_sessionmaker(app_engine, expire_on_commit=False)


async def _user(migrator: AsyncEngine) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with migrator.begin() as conn:
        await conn.execute(
            sa.insert(tables.users).values(
                id=user_id, subject=f"test|{user_id}", display_name="Người thử"
            )
        )
    return user_id


async def _tenant(migrator: AsyncEngine, *, status: str = "active") -> tuple[uuid.UUID, uuid.UUID]:
    tenant_id, workspace_id = uuid.uuid4(), uuid.uuid4()
    async with migrator.begin() as conn:
        await conn.execute(
            sa.insert(tables.tenants).values(
                id=tenant_id, slug=f"t-{tenant_id.hex[:8]}", name="T", status=status
            )
        )
        await conn.execute(
            sa.insert(tables.workspaces).values(
                id=workspace_id, tenant_id=tenant_id, slug="main", name="Main"
            )
        )
        await conn.execute(
            sa.insert(tables.entitlements).values(
                id=uuid.uuid4(), tenant_id=tenant_id, plan_id="professional"
            )
        )
    return tenant_id, workspace_id


async def _join(
    migrator: AsyncEngine,
    user_id: uuid.UUID,
    where: tuple[uuid.UUID, uuid.UUID],
    *,
    roles: list[str] | None = None,
    permission_sets: list[str] | None = None,
) -> None:
    async with migrator.begin() as conn:
        await conn.execute(
            sa.insert(tables.memberships).values(
                id=uuid.uuid4(),
                tenant_id=where[0],
                workspace_id=where[1],
                user_id=user_id,
                role_keys=roles or ["member"],
                permission_set_keys=permission_sets or [],
            )
        )


async def _leave(
    migrator: AsyncEngine, user_id: uuid.UUID, where: tuple[uuid.UUID, uuid.UUID]
) -> None:
    m = tables.memberships
    async with migrator.begin() as conn:
        await conn.execute(
            sa.delete(m).where(
                m.c.user_id == user_id, m.c.tenant_id == where[0], m.c.workspace_id == where[1]
            )
        )


# ---- claim once -----------------------------------------------------------------


async def test_two_deliveries_racing_in_two_transactions_claim_once(
    sessions: Sessions,
    migrator: AsyncEngine,
) -> None:
    user = await _user(migrator)
    ledger = SqlChannelInboundLedger(sessions)
    message_id = f"zm-{uuid.uuid4().hex}"

    results = await asyncio.gather(*(ledger.claim("zalo", message_id, user) for _ in range(5)))

    assert sorted(results) == [False, False, False, False, True]
    await ledger.settle("zalo", message_id, "done")
    await ledger.settle("zalo", message_id, "failed")  # a settled id is not relabelled
    assert await ledger.claim("zalo", message_id, user) is False
    async with migrator.connect() as conn:
        outcome = await conn.scalar(
            sa.select(tables.channel_inbound_messages.c.outcome).where(
                tables.channel_inbound_messages.c.external_message_id == message_id
            )
        )
    assert outcome == "done"


async def test_an_outcome_outside_the_fixed_set_is_refused(
    sessions: Sessions,
    migrator: AsyncEngine,
) -> None:
    user = await _user(migrator)
    ledger = SqlChannelInboundLedger(sessions)
    message_id = f"zm-{uuid.uuid4().hex}"
    await ledger.claim("zalo", message_id, user)
    with pytest.raises(DBAPIError, match="ck_channel_inbound_messages_outcome"):
        await ledger.settle("zalo", message_id, "approved")


async def test_ids_older_than_seven_days_are_pruned_and_newer_ones_kept(
    sessions: Sessions,
    migrator: AsyncEngine,
) -> None:
    user = await _user(migrator)
    ledger = SqlChannelInboundLedger(sessions)
    old, fresh = f"zm-old-{uuid.uuid4().hex}", f"zm-new-{uuid.uuid4().hex}"
    await ledger.claim("zalo", old, user)
    await ledger.claim("zalo", fresh, user)
    t = tables.channel_inbound_messages
    async with migrator.begin() as conn:
        await conn.execute(
            sa.update(t)
            .where(t.c.external_message_id == old)
            .values(received_at=sa.func.now() - sa.text("interval '8 days'"))
        )

    await SqlChannelInboundRetention(sessions).prune()

    async with migrator.connect() as conn:
        left = set(
            (
                await conn.execute(
                    sa.select(t.c.external_message_id).where(
                        t.c.external_message_id.in_([old, fresh])
                    )
                )
            ).scalars()
        )
    assert left == {fresh}


# ---- the person's own workspace choice ------------------------------------------


async def test_the_choice_is_readable_only_by_its_own_principal(
    sessions: Sessions,
    migrator: AsyncEngine,
    app_engine: AsyncEngine,
) -> None:
    alice, bob = await _user(migrator), await _user(migrator)
    ws = await _tenant(migrator)
    await _join(migrator, alice, ws)
    await _join(migrator, bob, ws)
    preferences = SqlChannelPreferences(sessions)
    assert await preferences.choose(alice, *ws) is True
    assert await preferences.chosen(alice) == ws
    assert await preferences.chosen(bob) is None  # Alice's row is not Bob's

    p = tables.channel_preferences
    async with app_engine.connect() as conn:
        # No principal bound: the state a connection is in before anything
        # scopes it. The row exists (asserted below), and this reads none.
        unscoped = await conn.scalar(sa.select(sa.func.count()).select_from(p))
        await conn.rollback()
        await conn.execute(
            sa.text("SELECT set_config('app.principal_id', :p, true)"), {"p": str(bob)}
        )
        seen_by_bob = await conn.scalar(
            sa.select(sa.func.count()).select_from(p).where(p.c.user_id == alice)
        )
        changed = await conn.execute(
            sa.update(p).where(p.c.user_id == alice).values(workspace_id=ws[1])
        )
        assert changed.rowcount == 0
        await conn.rollback()
    async with migrator.connect() as conn:
        there = await conn.scalar(
            sa.select(sa.func.count()).select_from(p).where(p.c.user_id == alice)
        )
    assert unscoped == 0
    assert seen_by_bob == 0
    assert there == 1


async def test_a_principal_cannot_write_a_row_for_someone_else(
    migrator: AsyncEngine, app_engine: AsyncEngine
) -> None:
    alice, bob = await _user(migrator), await _user(migrator)
    ws = await _tenant(migrator)
    await _join(migrator, alice, ws)
    async with app_engine.connect() as conn:
        await conn.execute(
            sa.text("SELECT set_config('app.principal_id', :p, true)"), {"p": str(bob)}
        )
        with pytest.raises(DBAPIError, match="row-level security"):
            await conn.execute(
                sa.insert(tables.channel_preferences).values(
                    user_id=alice, tenant_id=ws[0], workspace_id=ws[1]
                )
            )
        await conn.rollback()


async def test_a_workspace_i_do_not_belong_to_cannot_be_chosen(
    sessions: Sessions,
    migrator: AsyncEngine,
) -> None:
    alice = await _user(migrator)
    mine, theirs = await _tenant(migrator), await _tenant(migrator)
    await _join(migrator, alice, mine)
    preferences = SqlChannelPreferences(sessions)

    assert await preferences.choose(alice, *theirs) is False
    assert await preferences.choose(alice, mine[0], theirs[1]) is False  # mixed pair
    assert await preferences.chosen(alice) is None


async def test_the_choice_goes_with_the_membership(
    sessions: Sessions,
    migrator: AsyncEngine,
) -> None:
    alice = await _user(migrator)
    first, second = await _tenant(migrator), await _tenant(migrator)
    await _join(migrator, alice, first)
    await _join(migrator, alice, second)
    preferences = SqlChannelPreferences(sessions)
    await preferences.choose(alice, *second)
    assert sorted(await preferences.memberships(alice)) == sorted([first, second])

    await _leave(migrator, alice, second)

    assert await preferences.chosen(alice) is None
    assert await preferences.memberships(alice) == [first]


# ---- the context a command runs with --------------------------------------------


def _access(sessions: Sessions) -> LinkedUserAccess:
    return LinkedUserAccess(SqlChannelPreferences(sessions), SqlMembershipLookup(sessions))


async def test_the_context_holds_the_membership_cut_to_the_ceiling_and_no_role(
    sessions: Sessions,
    migrator: AsyncEngine,
) -> None:
    """`approver` grants `approvals.decide`, and `approver_boost` grants it again;
    a command that does not ask for it does not get it."""
    alice = await _user(migrator)
    ws = await _tenant(migrator)
    await _join(migrator, alice, ws, roles=["approver"], permission_sets=["approver_boost"])

    context = await _access(sessions).access_for(alice, *ws, _PROPOSE | {"cases.write"})

    assert context is not None
    assert (context.principal_id, context.tenant_id, context.workspace_id) == (alice, *ws)
    assert context.scopes == _PROPOSE  # held AND asked; the other is asked, not held
    assert "approvals.decide" not in context.scopes
    assert context.roles == frozenset()
    assert context.plan_id == "professional"


async def test_a_platform_admin_role_does_not_ride_in_past_the_ceiling(
    sessions: Sessions,
    migrator: AsyncEngine,
) -> None:
    """`platform_admin` passes every scope check (`ScopeAuthorizationService`),
    so a context cut to a ceiling must not carry it."""
    admin = await _user(migrator)
    ws = await _tenant(migrator)
    await _join(migrator, admin, ws, roles=["platform_admin"])

    context = await _access(sessions).access_for(admin, *ws, _PROPOSE)

    assert context is not None
    assert context.roles == frozenset() and context.scopes == frozenset()


async def test_a_locked_tenant_is_refused(
    sessions: Sessions,
    migrator: AsyncEngine,
) -> None:
    alice = await _user(migrator)
    ws = await _tenant(migrator, status="locked")
    await _join(migrator, alice, ws)
    assert await _access(sessions).access_for(alice, *ws, _PROPOSE) is None


async def test_a_removed_membership_is_refused(
    sessions: Sessions,
    migrator: AsyncEngine,
) -> None:
    alice = await _user(migrator)
    ws = await _tenant(migrator)
    await _join(migrator, alice, ws)
    access = _access(sessions)
    assert await access.access_for(alice, *ws, _PROPOSE) is not None

    await _leave(migrator, alice, ws)

    assert await access.access_for(alice, *ws, _PROPOSE) is None


async def test_another_persons_workspace_is_refused(
    sessions: Sessions,
    migrator: AsyncEngine,
) -> None:
    alice, bob = await _user(migrator), await _user(migrator)
    alices, bobs = await _tenant(migrator), await _tenant(migrator)
    await _join(migrator, alice, alices)
    await _join(migrator, bob, bobs)
    assert await _access(sessions).access_for(alice, *bobs, _PROPOSE) is None


async def test_a_linked_chat_still_resolves_no_sign_in(
    sessions: Sessions,
    migrator: AsyncEngine,
) -> None:
    """Z1's guarantee, kept: the linked-user path is a separate door, and the
    sign-in lookup still refuses a `zalo` identity row."""
    alice = await _user(migrator)
    ws = await _tenant(migrator)
    await _join(migrator, alice, ws)
    chat = f"zalo-{uuid.uuid4().hex[:12]}"
    async with migrator.begin() as conn:
        await conn.execute(
            sa.insert(tables.external_identities).values(
                id=uuid.uuid4(), user_id=alice, issuer="zalo", subject=chat, provider="zalo"
            )
        )

    assert await SqlMembershipLookup(sessions).find_access(chat, "zalo", *ws) is None
    assert await _access(sessions).access_for(alice, *ws, _PROPOSE) is not None
