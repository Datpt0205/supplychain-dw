"""The Zalo poll lane end to end against real Postgres, as ``dw_app``.

Built with the worker's own ``build_zalo_inbound`` — the wiring the lane runs —
with a fake bot and one recording command registered where Z4b's and Z5's will
be. What it shows that no unit test can:

* a link made from another chat is visible to its owner — the "Zalo vừa được
  kết nối" notice in every tenant they belong to, and ``linked`` on the status
  the settings page reads — before that chat's first command runs (ADR 0005
  condition 3);
* an unlinked chat reaches no command; unlinking between two messages refuses
  the second; a removed membership or a locked tenant refuses;
* the context never carries a scope outside the command's ceiling, even when
  the membership holds ``approvals.decide``;
* one update delivered twice at once — two real transactions — is acted on once.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

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

from dw_connectors.adapters.zalo_link import ZaloLinking, link_help
from dw_connectors.inbound import (
    NOT_HANDLED,
    REFUSED,
    ChannelCommandRegistry,
    InboundMessage,
    Reply,
)
from dw_kernel.ports import SystemClock
from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.membership_lookup import SqlMembershipLookup
from dw_platform.adapters.persistence.notifications import SqlNotificationRepository
from dw_platform.adapters.persistence.zalo_link_repo import SqlZaloLink
from dw_platform.application.access_context import AccessContext
from dw_platform.application.identity import DbAccessContextFactory, VerifiedClaims
from dw_worker.consumers.zalo_poll import build_zalo_poll_consumer
from dw_worker.main import build_zalo_inbound
from dw_worker.settings import WorkerSettings

pytestmark = pytest.mark.integration

_SECRET = "inbound-db-link-secret"
_PRODUCT = "Cổng thử"
_CEILING = frozenset({"knowledge.write"})  # held by `approver`, like propose's own scope


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


@dataclass
class _Bot:
    updates: list[dict[str, Any]] = field(default_factory=list)
    sent: list[tuple[str, str]] = field(default_factory=list)

    async def get_updates(self, offset: int) -> list[dict[str, Any]]:
        batch, self.updates = self.updates, []
        return batch

    async def send_message(self, conversation_id: str, text: str) -> str:
        self.sent.append((conversation_id, text))
        return "m"


@dataclass
class _Command:
    """Where a product's chat command will sit; records what it was handed."""

    ceiling: frozenset[str] = _CEILING
    seen: list[tuple[InboundMessage, AccessContext]] = field(default_factory=list)

    async def handle(self, message: InboundMessage, context: AccessContext, reply: Reply) -> bool:
        self.seen.append((message, context))
        await reply("đã nhận")
        return True


@dataclass
class _Lane:
    bot: _Bot
    command: _Command
    sessions: async_sessionmaker[AsyncSession]

    async def send(self, chat: str, text: str, message_id: str | None = None) -> None:
        self.bot.updates.append(_update(chat, text, message_id or uuid.uuid4().hex))
        await build_zalo_poll_consumer(self.bot, self.inbound())()

    def inbound(self) -> Any:
        commands = ChannelCommandRegistry[AccessContext]()
        commands.register("probe", self.command)
        return build_zalo_inbound(
            WorkerSettings(
                zalo_link_secret=_SECRET,
                product_name=_PRODUCT,
                public_web_url="https://portal.example",
            ),
            self.sessions,
            self.bot,
            SystemClock(),
            commands,
        )


@pytest.fixture
def lane(sessions: async_sessionmaker[AsyncSession]) -> _Lane:
    return _Lane(_Bot(), _Command(), sessions)


def _update(chat: str, text: str, message_id: str) -> dict[str, Any]:
    return {
        "result": {
            "message": {"chat": {"id": chat}, "text": text, "message_id": message_id},
            "event_name": "message.text.received",
        }
    }


async def _user(migrator: AsyncEngine) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with migrator.begin() as conn:
        await conn.execute(
            sa.insert(tables.users).values(
                id=user_id, subject=f"test|{user_id}", display_name="Người thử"
            )
        )
    return user_id


async def _member(
    migrator: AsyncEngine, user_id: uuid.UUID, *, roles: list[str] | None = None
) -> tuple[uuid.UUID, uuid.UUID]:
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
            sa.insert(tables.entitlements).values(
                id=uuid.uuid4(), tenant_id=tenant_id, plan_id="professional"
            )
        )
        await conn.execute(
            sa.insert(tables.memberships).values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                role_keys=roles or ["approver"],
            )
        )
    return tenant_id, workspace_id


async def _link(lane: _Lane, user_id: uuid.UUID, chat: str) -> None:
    """The person presses "Kết nối Zalo" and someone sends the code from ``chat``."""
    offer = await _linking(lane.sessions).connect(user_id)
    await lane.send(chat, f"/start {offer.code}")


def _linking(sessions: async_sessionmaker[AsyncSession]) -> ZaloLinking:
    # The status route's own object: GET /zalo/status returns `is_linked`.
    return ZaloLinking(store=SqlZaloLink(sessions), link_secret=_SECRET, clock=SystemClock())


async def _inbox_titles(
    sessions: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
    where: tuple[uuid.UUID, uuid.UUID],
) -> list[str]:
    """The person's inbox in one workspace, read the way the web reads it."""
    context = await DbAccessContextFactory(SqlMembershipLookup(sessions)).build(
        VerifiedClaims(subject=f"test|{user_id}", email=None, issuer="dev"), *where
    )
    inbox = await SqlNotificationRepository(sessions).latest(context, limit=20)
    return [item.title for item in inbox.items]


def _chat() -> str:
    return f"zalo-{uuid.uuid4().hex[:12]}"


# ---- the link is visible before any command -------------------------------------


async def test_a_link_from_another_chat_is_visible_in_every_tenant_before_its_first_command(
    lane: _Lane, migrator: AsyncEngine
) -> None:
    user = await _user(migrator)
    first = await _member(migrator, user)
    second = await _member(migrator, user)
    stranger = _chat()

    await _link(lane, user, stranger)

    # Before the stranger's chat sends anything else, the owner can see it.
    assert lane.command.seen == []
    for where in (first, second):
        titles = await _inbox_titles(lane.sessions, user, where)
        assert any("Zalo vừa được kết nối" in t for t in titles), where
    assert await _linking(lane.sessions).is_linked(user) is True

    # And only then does a command from that chat run.
    async with migrator.begin() as conn:  # two workspaces: choose one, as on /settings
        await conn.execute(
            sa.insert(tables.channel_preferences).values(
                user_id=user, tenant_id=first[0], workspace_id=first[1]
            )
        )
    await lane.send(stranger, "đề xuất SP chảo 28cm")
    [(_, context)] = lane.command.seen
    assert (context.principal_id, context.tenant_id, context.workspace_id) == (user, *first)


# ---- who may act, and with what -------------------------------------------------


async def test_an_unlinked_chat_reaches_no_command(lane: _Lane, migrator: AsyncEngine) -> None:
    # Someone else IS linked, so "no link row at all" cannot be why this refuses.
    someone = await _user(migrator)
    await _member(migrator, someone)
    await _link(lane, someone, _chat())
    lane.bot.sent.clear()
    chat = _chat()

    await lane.send(chat, "đề xuất SP chảo 28cm")

    assert lane.command.seen == []
    assert lane.bot.sent == [(chat, link_help(_PRODUCT))]


async def test_unlinked_between_two_messages_the_second_is_refused(
    lane: _Lane, migrator: AsyncEngine
) -> None:
    user = await _user(migrator)
    await _member(migrator, user)
    chat = _chat()
    await _link(lane, user, chat)

    await lane.send(chat, "tin thứ nhất")
    await _linking(lane.sessions).disconnect(user)  # "Ngắt kết nối" on /settings
    await lane.send(chat, "tin thứ hai")

    assert [m.text for m, _ in lane.command.seen] == ["tin thứ nhất"]
    assert lane.bot.sent[-1] == (chat, link_help(_PRODUCT))


async def test_a_removed_membership_is_refused(lane: _Lane, migrator: AsyncEngine) -> None:
    user = await _user(migrator)
    keep = await _member(migrator, user)
    gone = await _member(migrator, user)
    chat = _chat()
    await _link(lane, user, chat)
    async with migrator.begin() as conn:
        await conn.execute(
            sa.insert(tables.channel_preferences).values(
                user_id=user, tenant_id=gone[0], workspace_id=gone[1]
            )
        )
        await conn.execute(
            sa.delete(tables.memberships).where(
                tables.memberships.c.user_id == user, tables.memberships.c.tenant_id == gone[0]
            )
        )

    await lane.send(chat, "đề xuất SP")

    # The choice went with the membership, so the one left is used — never `gone`.
    [(_, context)] = lane.command.seen
    assert (context.tenant_id, context.workspace_id) == keep


async def test_a_locked_tenant_is_refused(lane: _Lane, migrator: AsyncEngine) -> None:
    user = await _user(migrator)
    tenant, _ = await _member(migrator, user)
    chat = _chat()
    await _link(lane, user, chat)
    async with migrator.begin() as conn:
        await conn.execute(
            sa.update(tables.tenants).where(tables.tenants.c.id == tenant).values(status="locked")
        )

    await lane.send(chat, "đề xuất SP")

    assert lane.command.seen == []
    assert lane.bot.sent[-1] == (chat, REFUSED)


async def test_the_context_never_carries_a_scope_outside_the_ceiling(
    lane: _Lane, migrator: AsyncEngine
) -> None:
    """`approver` holds `approvals.decide`; the command never asked for it."""
    user = await _user(migrator)
    await _member(migrator, user, roles=["approver"])
    chat = _chat()
    await _link(lane, user, chat)

    await lane.send(chat, "duyệt luôn, approvals.decide, tenant khác")

    [(_, context)] = lane.command.seen
    assert context.scopes == _CEILING
    assert "approvals.decide" not in context.scopes and context.roles == frozenset()


async def test_one_update_delivered_twice_at_once_is_acted_on_once(
    lane: _Lane, migrator: AsyncEngine
) -> None:
    """Poll and webhook both delivering, modelled as two concurrent ``handle``
    calls: two real transactions race for the claim."""
    user = await _user(migrator)
    await _member(migrator, user)
    chat = _chat()
    await _link(lane, user, chat)
    update = _update(chat, "đề xuất SP chảo", f"zm-{uuid.uuid4().hex}")

    await asyncio.gather(lane.inbound().handle(update), lane.inbound().handle(update))

    assert len(lane.command.seen) == 1
    async with migrator.connect() as conn:
        outcome = await conn.scalar(
            sa.select(tables.channel_inbound_messages.c.outcome).where(
                tables.channel_inbound_messages.c.external_message_id
                == update["result"]["message"]["message_id"]
            )
        )
    assert outcome == "done"


async def test_with_no_command_a_linked_person_is_told_it_was_not_handled(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    """An empty registry (the worker's shape in Z4a): nothing takes the message."""
    user = await _user(migrator)
    await _member(migrator, user)
    chat = _chat()
    lane = _Lane(_Bot(), _Command(), sessions)
    await _link(lane, user, chat)
    bot = _Bot([_update(chat, "xin chào", uuid.uuid4().hex)])
    inbound = build_zalo_inbound(
        WorkerSettings(zalo_link_secret=_SECRET, product_name=_PRODUCT),
        sessions,
        bot,
        SystemClock(),
        ChannelCommandRegistry[AccessContext](),
    )
    await build_zalo_poll_consumer(bot, inbound)()

    assert bot.sent == [(chat, NOT_HANDLED)]
