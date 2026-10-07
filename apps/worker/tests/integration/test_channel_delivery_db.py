"""The `channel_delivery` lane end to end against real Postgres, as ``dw_app``.

The lane the worker registers, over ``SqlChannelOutbox`` and ``SqlZaloLink``,
with a fake chat in place of Zalo (never the real API). What it shows that the
unit tests cannot:

* a notification to a linked member goes out once, with names from the
  database and never the notification's body, and one audit row says so;
* a link removed before the send cancels the row and sends nothing;
* a passing failure then a success: two attempts, one ``sent``; an unreachable
  chat: one attempt, ``failed``; the ceiling: ``failed``. Each with exactly
  one final audit row;
* two workers at once send each row once (``FOR UPDATE SKIP LOCKED``), and a
  worker finds the next row while another holds one, without waiting on it;
* a row of another tenant is delivered under that tenant's own names, and no
  tenant's tick touches it.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
import sqlalchemy as sa
from pg_test_db import DatabaseUrls
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from dw_connectors.ports import ChatRecipientUnreachableError
from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.channel_deliveries import SqlChannelOutbox
from dw_platform.adapters.persistence.notifications import SqlNotificationRepository
from dw_platform.adapters.persistence.zalo_link_repo import SqlZaloLink
from dw_platform.application.access_context import AccessContext
from dw_worker.consumers.channel_delivery import MAX_ATTEMPTS, build_channel_delivery_consumer

pytestmark = pytest.mark.integration

_d = tables.channel_deliveries
_WEB = "https://portal.example"
_BODY = "DUYỆT 482193 — giá 12.500.000đ"  # what must never leave through the chat


@pytest.fixture
async def migrator(worker_db: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(worker_db.migrator, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def sessions(worker_db: DatabaseUrls) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(worker_db.app, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
async def _no_backlog(migrator: AsyncEngine) -> None:
    """Each test starts with nothing due, so a lane run here sends only what
    this test queued (the database is shared by the session)."""
    async with migrator.begin() as conn:
        await conn.execute(sa.update(_d).where(_d.c.status == "pending").values(status="cancelled"))


@dataclass
class _Chat:
    fail_with: list[Exception] = field(default_factory=list)
    sent: list[tuple[str, str]] = field(default_factory=list)
    hold: asyncio.Event | None = None
    entered: asyncio.Event = field(default_factory=asyncio.Event)

    async def send_message(self, conversation_id: str, text: str) -> str:
        self.entered.set()
        if self.hold is not None:
            await self.hold.wait()
        if self.fail_with:
            raise self.fail_with.pop(0)
        self.sent.append((conversation_id, text))
        return f"m{len(self.sent)}"


@dataclass(frozen=True)
class _Place:
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    tenant_name: str


async def _place(migrator: AsyncEngine) -> _Place:
    place = _Place(uuid.uuid4(), uuid.uuid4(), f"Công ty {uuid.uuid4().hex[:4]}")
    async with migrator.begin() as conn:
        await conn.execute(
            sa.insert(tables.tenants).values(
                id=place.tenant_id, slug=f"t-{place.tenant_id.hex[:8]}", name=place.tenant_name
            )
        )
        await conn.execute(
            sa.insert(tables.workspaces).values(
                id=place.workspace_id, tenant_id=place.tenant_id, slug="main", name="Cung ứng"
            )
        )
    return place


async def _linked_member(migrator: AsyncEngine, place: _Place) -> tuple[uuid.UUID, str]:
    user_id, chat = uuid.uuid4(), f"z-{uuid.uuid4().hex}"
    async with migrator.begin() as conn:
        await conn.execute(
            sa.insert(tables.users).values(id=user_id, subject=f"test|{user_id}", display_name="N")
        )
        await conn.execute(
            sa.insert(tables.memberships).values(
                id=uuid.uuid4(),
                tenant_id=place.tenant_id,
                workspace_id=place.workspace_id,
                user_id=user_id,
                role_keys=["member"],
            )
        )
        await conn.execute(
            sa.insert(tables.external_identities).values(
                id=uuid.uuid4(), user_id=user_id, issuer="zalo", subject=chat, provider="zalo"
            )
        )
    return user_id, chat


async def _notify(
    sessions: async_sessionmaker[AsyncSession], place: _Place, *recipients: uuid.UUID
) -> str:
    key = f"test:{uuid.uuid4()}"
    await SqlNotificationRepository(sessions).deliver(
        AccessContext(
            tenant_id=place.tenant_id,
            workspace_id=place.workspace_id,
            principal_id=uuid.uuid4(),
            roles=frozenset(),
            scopes=frozenset(),
            plan_id="professional",
        ),
        recipients=recipients,
        source_key=key,
        title="PO-2026-007 chờ bạn duyệt",
        body=_BODY,
        link="/approvals/7",
    )
    return key


def _lane(sessions: async_sessionmaker[AsyncSession], chat: _Chat, batch: int = 20):  # type: ignore[no-untyped-def]
    return build_channel_delivery_consumer(
        SqlChannelOutbox(sessions),
        channel="zalo",
        address_of=SqlZaloLink(sessions).zalo_id_for,
        sender=chat,
        web_url=_WEB,
        batch_per_scope=batch,
    )


@dataclass(frozen=True)
class _State:
    status: str
    attempts: int
    last_error: str | None
    external_message_id: str | None
    audits: tuple[str, ...]


async def _state(migrator: AsyncEngine, key: str) -> _State:
    a = tables.audit_events
    async with migrator.connect() as conn:
        row = (
            await conn.execute(
                sa.select(
                    _d.c.id, _d.c.status, _d.c.attempts, _d.c.last_error, _d.c.external_message_id
                ).where(_d.c.source_key == key)
            )
        ).one()
        audits = (
            await conn.execute(
                sa.select(a.c.action).where(
                    a.c.resource_type == "channel_delivery", a.c.resource_id == str(row.id)
                )
            )
        ).scalars()
        return _State(
            row.status, row.attempts, row.last_error, row.external_message_id, tuple(audits)
        )


async def _make_due(migrator: AsyncEngine, key: str) -> None:
    async with migrator.begin() as conn:
        await conn.execute(
            sa.update(_d).where(_d.c.source_key == key).values(next_attempt_at=sa.func.now())
        )


async def test_a_linked_member_is_sent_names_title_and_link_once_and_never_the_body(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    place = await _place(migrator)
    user, chat_id = await _linked_member(migrator, place)
    key = await _notify(sessions, place, user)
    chat = _Chat()

    await _lane(sessions, chat)()
    await _lane(sessions, chat)()

    assert chat.sent == [
        (
            chat_id,
            f"[{place.tenant_name} · Cung ứng]\nPO-2026-007 chờ bạn duyệt\n{_WEB}/approvals/7",
        )
    ]
    assert "482193" not in chat.sent[0][1]
    assert await _state(migrator, key) == _State("sent", 1, None, "m1", ("channel_delivery.sent",))


async def test_a_link_removed_before_the_send_cancels_it_and_sends_nothing(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    place = await _place(migrator)
    user, _ = await _linked_member(migrator, place)
    key = await _notify(sessions, place, user)
    await SqlZaloLink(sessions).unlink_by_user(user)
    chat = _Chat()

    await _lane(sessions, chat)()

    assert chat.sent == []
    assert await _state(migrator, key) == _State(
        "cancelled", 0, "recipient_unlinked", None, ("channel_delivery.cancelled",)
    )


async def test_a_passing_failure_then_success_is_two_attempts_and_one_send(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    place = await _place(migrator)
    user, _ = await _linked_member(migrator, place)
    key = await _notify(sessions, place, user)
    chat = _Chat(fail_with=[RuntimeError("zalo request failed: 502")])

    await _lane(sessions, chat)()
    first = await _state(migrator, key)
    assert (first.status, first.attempts, first.audits) == ("pending", 1, ())
    assert first.last_error == "RuntimeError: zalo request failed: 502"
    await _lane(sessions, chat)()  # not due yet: nothing happens
    assert len(chat.sent) == 0

    await _make_due(migrator, key)
    await _lane(sessions, chat)()
    assert len(chat.sent) == 1
    assert await _state(migrator, key) == _State("sent", 2, None, "m1", ("channel_delivery.sent",))


async def test_an_unreachable_chat_is_one_attempt_and_failed(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    place = await _place(migrator)
    user, _ = await _linked_member(migrator, place)
    key = await _notify(sessions, place, user)

    await _lane(sessions, _Chat(fail_with=[ChatRecipientUnreachableError("blocked")]))()

    state = await _state(migrator, key)
    assert (state.status, state.attempts, state.audits) == (
        "failed",
        1,
        ("channel_delivery.failed",),
    )
    assert state.last_error is not None and state.last_error.startswith("recipient_unreachable")


async def test_the_ceiling_fails_the_row_with_one_final_audit(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    place = await _place(migrator)
    user, _ = await _linked_member(migrator, place)
    key = await _notify(sessions, place, user)
    chat = _Chat(fail_with=[RuntimeError("down")] * MAX_ATTEMPTS)

    for _ in range(MAX_ATTEMPTS):
        await _lane(sessions, chat)()
        await _make_due(migrator, key)
    await _lane(sessions, chat)()  # a sixth tick finds nothing to try

    state = await _state(migrator, key)
    assert (state.status, state.attempts, state.audits) == (
        "failed",
        MAX_ATTEMPTS,
        ("channel_delivery.failed",),
    )
    assert state.last_error is not None and state.last_error.startswith("attempts_exhausted")
    assert chat.sent == []


async def test_two_workers_at_once_send_each_row_once(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    place = await _place(migrator)
    people = [await _linked_member(migrator, place) for _ in range(6)]
    keys = [await _notify(sessions, place, user) for user, _ in people]
    gate = asyncio.Event()
    chat = _Chat(hold=gate)

    workers = [asyncio.create_task(_lane(sessions, chat)()) for _ in range(2)]
    await asyncio.sleep(0.3)  # both are inside a claim or past it
    gate.set()
    await asyncio.gather(*workers)

    assert sorted(c for c, _ in chat.sent) == sorted(c for _, c in people)
    assert [(await _state(migrator, k)).status for k in keys] == ["sent"] * 6


async def test_a_held_row_is_skipped_not_waited_on(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    place = await _place(migrator)
    (first, _), (second, second_chat) = [await _linked_member(migrator, place) for _ in range(2)]
    await _notify(sessions, place, first)
    await _notify(sessions, place, second)
    gate = asyncio.Event()
    holding = _Chat(hold=gate)

    one = asyncio.create_task(_lane(sessions, holding, batch=1)())
    await asyncio.wait_for(holding.entered.wait(), timeout=5)  # row 1 locked, mid-send
    other = _Chat()
    try:
        await asyncio.wait_for(_lane(sessions, other, batch=1)(), timeout=3)
    finally:
        gate.set()
        await one
    assert [c for c, _ in other.sent] == [second_chat]


async def test_another_tenants_row_goes_out_under_its_own_names_only(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    mine, theirs = await _place(migrator), await _place(migrator)
    me, my_chat = await _linked_member(migrator, mine)
    them, their_chat = await _linked_member(migrator, theirs)
    await _notify(sessions, mine, me)
    await _notify(sessions, theirs, them)
    chat = _Chat()

    await _lane(sessions, chat)()

    by_chat = dict(chat.sent)
    assert by_chat[my_chat].startswith(f"[{mine.tenant_name} ·")
    assert by_chat[their_chat].startswith(f"[{theirs.tenant_name} ·")
    assert len(chat.sent) == 2


@pytest.mark.parametrize("change", ["membership_removed", "tenant_locked"])
async def test_someone_who_lost_the_workspace_after_the_notice_is_sent_nothing(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine, change: str
) -> None:
    """Asked again at send time, in the claim's own transaction: a member
    removed, or a tenant locked, between the notice and the send."""
    place = await _place(migrator)
    user, _ = await _linked_member(migrator, place)
    key = await _notify(sessions, place, user)
    async with migrator.begin() as conn:
        if change == "membership_removed":
            await conn.execute(
                sa.delete(tables.memberships).where(tables.memberships.c.user_id == user)
            )
        else:
            await conn.execute(
                sa.update(tables.tenants)
                .where(tables.tenants.c.id == place.tenant_id)
                .values(status="locked")
            )
    chat = _Chat()

    await _lane(sessions, chat)()

    assert chat.sent == []
    assert await _state(migrator, key) == _State(
        "cancelled", 0, "recipient_not_member", None, ("channel_delivery.cancelled",)
    )
