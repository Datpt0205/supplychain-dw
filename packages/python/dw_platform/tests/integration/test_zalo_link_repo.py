"""Integration: the Zalo link store against real Postgres, as ``dw_app``.

What the unit tests cannot show: that a nonce is consumed by ONE conditional
UPDATE (two ``/start`` with the same token in flight link once), that the
database's clock decides expiry, that "one Zalo per user, one user per Zalo"
holds under the real constraints, that a Zalo identity row never resolves a
login to a user, and that every link, relink and unlink leaves an audit row and
an inbox message in each of the person's tenants, in the transaction that made it.
"""

from __future__ import annotations

import asyncio
import secrets
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from pg_harness import DatabaseUrls
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.identity_provisioning import SqlIdentityBootstrap
from dw_platform.adapters.persistence.membership_lookup import SqlMembershipLookup
from dw_platform.adapters.persistence.zalo_link_repo import (
    SqlChannelLinkNonceRetention,
    SqlZaloLink,
)

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class _Claims:
    """What a verified connect token claims (``dw_connectors``' ``ConnectToken``)."""

    user_id: uuid.UUID
    jti: str
    expires_at: datetime


def _claims(user_id: uuid.UUID, *, expires_in: timedelta = timedelta(minutes=15)) -> _Claims:
    return _Claims(user_id, secrets.token_hex(8), datetime.now(UTC) + expires_in)


@pytest.fixture
async def migrator_engine(db_urls: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(db_urls.migrator, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def app_engine(db_urls: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
def store(app_engine: AsyncEngine) -> SqlZaloLink:
    return SqlZaloLink(async_sessionmaker(app_engine, expire_on_commit=False))


async def _bare_user(migrator: AsyncEngine) -> uuid.UUID:
    """A platform user with no membership anywhere."""
    user_id = uuid.uuid4()
    async with migrator.begin() as conn:
        await conn.execute(
            sa.insert(tables.users).values(
                id=user_id, subject=f"test|{user_id}", display_name="Người thử"
            )
        )
    return user_id


async def _join_new_tenant(migrator: AsyncEngine, user_id: uuid.UUID) -> uuid.UUID:
    """Stand up a fresh tenant and workspace with ``user_id`` as a member."""
    tenant_id, workspace_id = uuid.uuid4(), uuid.uuid4()
    async with migrator.begin() as conn:
        await conn.execute(
            sa.insert(tables.tenants).values(id=tenant_id, slug=f"t-{tenant_id.hex[:8]}", name="T")
        )
        await conn.execute(
            sa.insert(tables.workspaces).values(
                id=workspace_id, tenant_id=tenant_id, slug="main", name="Main"
            )
        )
        await conn.execute(
            sa.insert(tables.memberships).values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                role_keys=["member"],
            )
        )
    return tenant_id


async def _user(migrator: AsyncEngine) -> uuid.UUID:
    """A user who is a member of one tenant, as anyone who reached Settings is."""
    user_id = await _bare_user(migrator)
    await _join_new_tenant(migrator, user_id)
    return user_id


async def _trail(migrator: AsyncEngine, user_id: uuid.UUID) -> list[tuple[uuid.UUID, str, str]]:
    """``(tenant, action, actor)`` of every link audit row about ``user_id``."""
    a = tables.audit_events
    async with migrator.connect() as conn:
        rows = await conn.execute(
            sa.select(a.c.tenant_id, a.c.action, a.c.details).where(
                a.c.resource_type == "channel_link", a.c.resource_id == str(user_id)
            )
        )
        return sorted((r.tenant_id, r.action, r.details["actor"]) for r in rows)


async def _inbox(migrator: AsyncEngine, user_id: uuid.UUID) -> list[tuple[uuid.UUID, str]]:
    """``(tenant, title)`` of every notification addressed to ``user_id``."""
    n = tables.notifications
    async with migrator.connect() as conn:
        rows = await conn.execute(
            sa.select(n.c.tenant_id, n.c.title).where(n.c.recipient_user_id == user_id)
        )
        return sorted((r.tenant_id, r.title) for r in rows)


def _chat() -> str:
    return f"zalo-{uuid.uuid4().hex[:12]}"


async def _zalo_rows(migrator: AsyncEngine) -> set[tuple[uuid.UUID, str]]:
    async with migrator.connect() as conn:
        rows = await conn.execute(
            sa.select(
                tables.external_identities.c.user_id, tables.external_identities.c.subject
            ).where(tables.external_identities.c.provider == "zalo")
        )
        return {(r.user_id, r.subject) for r in rows}


# ---- the single-use token -------------------------------------------------------


async def test_an_issued_token_links_once_and_only_once(
    store: SqlZaloLink, migrator_engine: AsyncEngine
) -> None:
    user, chat = await _user(migrator_engine), _chat()
    token = _claims(user)
    await store.issue_nonce(token)

    assert await store.redeem(token, chat) is True
    assert await store.zalo_id_for(user) == chat

    # The same token again, from another chat: refused, and the link stands.
    assert await store.redeem(token, _chat()) is False
    assert await store.zalo_id_for(user) == chat


async def test_a_token_that_was_never_issued_is_refused(
    store: SqlZaloLink, migrator_engine: AsyncEngine
) -> None:
    user = await _user(migrator_engine)
    assert await store.redeem(_claims(user), _chat()) is False
    assert await store.zalo_id_for(user) is None


async def test_an_expired_nonce_is_refused_by_the_databases_clock(
    store: SqlZaloLink, migrator_engine: AsyncEngine
) -> None:
    user = await _user(migrator_engine)
    token = _claims(user, expires_in=-timedelta(seconds=1))
    await store.issue_nonce(token)

    assert await store.redeem(token, _chat()) is False
    assert await store.zalo_id_for(user) is None


async def test_a_nonce_redeems_only_for_the_user_it_was_issued_to(
    store: SqlZaloLink, migrator_engine: AsyncEngine
) -> None:
    owner, intruder = await _user(migrator_engine), await _user(migrator_engine)
    token = _claims(owner)
    await store.issue_nonce(token)

    forged = _Claims(intruder, token.jti, token.expires_at)
    assert await store.redeem(forged, _chat()) is False
    assert await store.zalo_id_for(intruder) is None
    # Still good for its owner.
    assert await store.redeem(token, _chat()) is True


async def test_two_concurrent_starts_with_one_token_link_exactly_once(
    store: SqlZaloLink, migrator_engine: AsyncEngine
) -> None:
    """Two transactions in flight with the same token. A read-then-update would
    let both pass; one conditional UPDATE lets exactly one through. Repeated,
    because one round might happen to serialise on its own."""
    for _ in range(5):
        user = await _user(migrator_engine)
        token = _claims(user)
        await store.issue_nonce(token)
        first, second = _chat(), _chat()

        results = await asyncio.gather(store.redeem(token, first), store.redeem(token, second))

        assert sorted(results) == [False, True]
        winner = first if results[0] else second
        assert await store.zalo_id_for(user) == winner
        assert {s for u, s in await _zalo_rows(migrator_engine) if u == user} == {winner}


# ---- one Zalo per user, one user per Zalo ---------------------------------------


async def test_relinking_a_user_replaces_their_old_chat(
    store: SqlZaloLink, migrator_engine: AsyncEngine
) -> None:
    user, old, new = await _user(migrator_engine), _chat(), _chat()
    for chat in (old, new):
        token = _claims(user)
        await store.issue_nonce(token)
        assert await store.redeem(token, chat) is True

    assert {s for u, s in await _zalo_rows(migrator_engine) if u == user} == {new}


async def test_a_chat_linked_by_a_second_user_moves_to_them(
    store: SqlZaloLink, migrator_engine: AsyncEngine
) -> None:
    """The chat proved control of itself by sending the code: it now belongs to
    whoever's code it sent last, and the earlier user is no longer linked."""
    alice, bob, chat = await _user(migrator_engine), await _user(migrator_engine), _chat()
    for user in (alice, bob):
        token = _claims(user)
        await store.issue_nonce(token)
        assert await store.redeem(token, chat) is True

    assert await store.zalo_id_for(alice) is None
    assert await store.zalo_id_for(bob) == chat


async def test_the_database_refuses_two_zalo_chats_for_one_user(
    app_engine: AsyncEngine, migrator_engine: AsyncEngine
) -> None:
    user = await _user(migrator_engine)
    rows = [
        {
            "id": uuid.uuid4(),
            "user_id": user,
            "issuer": "zalo",
            "subject": _chat(),
            "provider": "zalo",
        }
        for _ in range(2)
    ]
    with pytest.raises(IntegrityError, match="uq_external_identities_user_id_provider"):
        async with app_engine.begin() as conn:
            await conn.execute(sa.insert(tables.external_identities), rows)


async def test_the_database_refuses_one_zalo_chat_for_two_users(
    app_engine: AsyncEngine, migrator_engine: AsyncEngine
) -> None:
    chat = _chat()
    rows = [
        {
            "id": uuid.uuid4(),
            "user_id": await _user(migrator_engine),
            "issuer": "zalo",
            "subject": chat,
            "provider": "zalo",
        }
        for _ in range(2)
    ]
    with pytest.raises(IntegrityError, match="uq_external_identities_issuer_subject"):
        async with app_engine.begin() as conn:
            await conn.execute(sa.insert(tables.external_identities), rows)


# ---- unlinking ------------------------------------------------------------------


async def test_unlinking_by_user_touches_only_that_user(
    store: SqlZaloLink, migrator_engine: AsyncEngine
) -> None:
    alice, bob = await _user(migrator_engine), await _user(migrator_engine)
    chats = {}
    for user in (alice, bob):
        token, chats[user] = _claims(user), _chat()
        await store.issue_nonce(token)
        await store.redeem(token, chats[user])

    await store.unlink_by_user(alice)

    assert await store.zalo_id_for(alice) is None
    assert await store.zalo_id_for(bob) == chats[bob]


async def test_stop_unlinks_the_chat_and_says_whether_it_was_linked(
    store: SqlZaloLink, migrator_engine: AsyncEngine
) -> None:
    user, chat = await _user(migrator_engine), _chat()
    token = _claims(user)
    await store.issue_nonce(token)
    await store.redeem(token, chat)

    assert await store.unlink_by_zalo(chat) is True
    assert await store.unlink_by_zalo(chat) is False
    assert await store.zalo_id_for(user) is None


async def test_deleting_a_user_deletes_their_nonces(
    store: SqlZaloLink, migrator_engine: AsyncEngine
) -> None:
    user = await _user(migrator_engine)
    await store.issue_nonce(_claims(user))
    async with migrator_engine.begin() as conn:
        await conn.execute(sa.delete(tables.users).where(tables.users.c.id == user))
        remaining = await conn.scalar(
            sa.select(sa.func.count())
            .select_from(tables.channel_link_nonces)
            .where(tables.channel_link_nonces.c.user_id == user)
        )
    assert remaining == 0


# ---- retention ------------------------------------------------------------------


async def test_retention_deletes_nonces_a_day_past_expiry_and_keeps_the_rest(
    store: SqlZaloLink, app_engine: AsyncEngine, migrator_engine: AsyncEngine
) -> None:
    user = await _user(migrator_engine)
    stale = _claims(user, expires_in=-timedelta(days=1, minutes=1))
    recent = _claims(user, expires_in=-timedelta(hours=1))
    live = _claims(user)
    for token in (stale, recent, live):
        await store.issue_nonce(token)

    await SqlChannelLinkNonceRetention(async_sessionmaker(app_engine)).prune()

    async with migrator_engine.connect() as conn:
        left = set(
            await conn.scalars(
                sa.select(tables.channel_link_nonces.c.jti).where(
                    tables.channel_link_nonces.c.user_id == user
                )
            )
        )
    assert left == {recent.jti, live.jti}


# ---- a delivery address is not a login ------------------------------------------


async def test_a_zalo_identity_never_resolves_a_membership(
    store: SqlZaloLink, app_engine: AsyncEngine, migrator_engine: AsyncEngine
) -> None:
    """A row with ``issuer='zalo'`` says where to send, not who signs in. Even a
    verified token whose issuer and subject matched it exactly must not turn
    into the linked user's access."""
    user = await _user(migrator_engine)
    tenant_id, workspace_id = uuid.uuid4(), uuid.uuid4()
    async with migrator_engine.begin() as conn:
        await conn.execute(
            sa.insert(tables.tenants).values(id=tenant_id, slug=f"t-{tenant_id.hex[:8]}", name="T")
        )
        await conn.execute(
            sa.insert(tables.workspaces).values(
                id=workspace_id, tenant_id=tenant_id, slug="main", name="Main"
            )
        )
        await conn.execute(
            sa.insert(tables.memberships).values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user,
                role_keys=["member"],
            )
        )
        plan_id = await conn.scalar(sa.select(tables.plans.c.plan_id).limit(1))
        await conn.execute(
            sa.insert(tables.entitlements).values(
                id=uuid.uuid4(), tenant_id=tenant_id, plan_id=plan_id
            )
        )
    chat = _chat()
    token = _claims(user)
    await store.issue_nonce(token)
    await store.redeem(token, chat)

    lookup = SqlMembershipLookup(async_sessionmaker(app_engine, expire_on_commit=False))
    assert await lookup.find_access(chat, "zalo", tenant_id, workspace_id) is None
    # The same user through their real login still resolves.
    assert await lookup.find_access(f"test|{user}", "any-issuer", tenant_id, workspace_id)


async def test_a_bootstrapped_zalo_identity_never_signs_in_as_the_linked_user(
    store: SqlZaloLink, app_engine: AsyncEngine, migrator_engine: AsyncEngine
) -> None:
    """The sign-in path, not only the membership lookup: a verified identity whose
    issuer and subject equal a linked chat must not resolve to the linked user.
    Guarded by ``provider NOT IN CHANNEL_LINK_PROVIDERS`` in ``_resolve_user``."""

    @dataclass(frozen=True)
    class _Identity:
        subject: str
        issuer: str = "zalo"
        email: str | None = None
        auth_methods: frozenset[str] = frozenset()
        acr: str | None = None

    user, chat = await _user(migrator_engine), _chat()
    token = _claims(user)
    await store.issue_nonce(token)
    assert await store.redeem(token, chat) is True

    bootstrap = SqlIdentityBootstrap(
        async_sessionmaker(app_engine, expire_on_commit=False),
        default_tenant_id=uuid.uuid4(),
        default_workspace_id=uuid.uuid4(),
    )
    view = await bootstrap.bootstrap(_Identity(subject=chat))

    assert view.principal_id != user
    assert view.memberships == ()


# ---- every link change leaves a trace the person can see ------------------------


async def test_a_link_is_audited_and_announced_in_every_tenant_of_the_user(
    store: SqlZaloLink, migrator_engine: AsyncEngine
) -> None:
    user = await _bare_user(migrator_engine)
    first, second = (
        await _join_new_tenant(migrator_engine, user),
        await _join_new_tenant(migrator_engine, user),
    )
    stranger = await _join_new_tenant(migrator_engine, await _bare_user(migrator_engine))
    chat = _chat()
    token = _claims(user)
    await store.issue_nonce(token)

    assert await store.redeem(token, chat) is True

    trail = await _trail(migrator_engine, user)
    assert trail == sorted(
        [(first, "channel_link.linked", "zalo_bot"), (second, "channel_link.linked", "zalo_bot")]
    )
    assert stranger not in {tenant for tenant, _, _ in trail}
    inbox = await _inbox(migrator_engine, user)
    assert {tenant for tenant, _ in inbox} == {first, second}
    assert all("kết nối" in title for _, title in inbox)
    # The chat id itself is never written to the audit trail.
    async with migrator_engine.connect() as conn:
        details = await conn.scalars(
            sa.select(tables.audit_events.c.details).where(
                tables.audit_events.c.resource_id == str(user)
            )
        )
        assert all(chat not in str(d) for d in details)


async def test_a_refused_start_leaves_no_trace(
    store: SqlZaloLink, migrator_engine: AsyncEngine
) -> None:
    user = await _user(migrator_engine)
    token = _claims(user)
    await store.issue_nonce(token)
    assert await store.redeem(token, _chat()) is True
    before = await _trail(migrator_engine, user), await _inbox(migrator_engine, user)

    assert await store.redeem(token, _chat()) is False

    assert (await _trail(migrator_engine, user), await _inbox(migrator_engine, user)) == before


async def test_a_user_with_no_membership_cannot_link_and_keeps_the_token(
    store: SqlZaloLink, migrator_engine: AsyncEngine
) -> None:
    """No tenant to record the link in, so no link; the refusal rolls the
    consume back with it."""
    user = await _bare_user(migrator_engine)
    token = _claims(user)
    await store.issue_nonce(token)

    assert await store.redeem(token, _chat()) is False
    assert await store.zalo_id_for(user) is None
    async with migrator_engine.connect() as conn:
        used_at = await conn.scalar(
            sa.select(tables.channel_link_nonces.c.used_at).where(
                tables.channel_link_nonces.c.jti == token.jti
            )
        )
    assert used_at is None


async def test_the_user_a_chat_moved_away_from_is_told(
    store: SqlZaloLink, migrator_engine: AsyncEngine
) -> None:
    """Someone else's code sent from my chat takes the chat off my account: I
    must learn that from my own inbox, not from a missing notification."""
    alice, bob, chat = await _user(migrator_engine), await _user(migrator_engine), _chat()
    for user in (alice, bob):
        token = _claims(user)
        await store.issue_nonce(token)
        assert await store.redeem(token, chat) is True

    assert [a for _, a, _ in await _trail(migrator_engine, alice)] == [
        "channel_link.linked",
        "channel_link.moved_to_other_user",
    ]
    assert [a for _, a, _ in await _trail(migrator_engine, bob)] == ["channel_link.linked"]
    assert len(await _inbox(migrator_engine, alice)) == 2


async def test_unlinking_is_audited_with_who_acted(
    store: SqlZaloLink, migrator_engine: AsyncEngine
) -> None:
    by_web, by_bot = await _user(migrator_engine), await _user(migrator_engine)
    chats = {}
    for user in (by_web, by_bot):
        token, chats[user] = _claims(user), _chat()
        await store.issue_nonce(token)
        await store.redeem(token, chats[user])

    await store.unlink_by_user(by_web)
    assert await store.unlink_by_zalo(chats[by_bot]) is True

    assert ("channel_link.unlinked", "web") in {
        (a, actor) for _, a, actor in await _trail(migrator_engine, by_web)
    }
    assert ("channel_link.unlinked", "zalo_bot") in {
        (a, actor) for _, a, actor in await _trail(migrator_engine, by_bot)
    }
    # Unlinking what is not linked records nothing.
    before = await _trail(migrator_engine, by_web)
    await store.unlink_by_user(by_web)
    assert await _trail(migrator_engine, by_web) == before
