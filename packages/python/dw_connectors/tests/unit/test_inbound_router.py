"""The inbound router and the Zalo update entry, against in-memory ports.

What these pin down, each with the guard it depends on: an unlinked chat gets
the link sentence and reaches no command (so no model); a message id is acted
on once; a person with several workspaces and no choice is sent to /settings;
the context a command receives is built for that command's own ceiling; a
refused context ends the walk; a command that raises leaves the message
``failed`` with a short reply; with no command registered a linked person is
told the message was not handled. The database half (claim under two real
transactions, RLS, the scope intersection) is in ``dw_platform`` and
``apps/worker`` integration tests.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from dw_connectors.adapters.zalo_inbound import ZaloInbound
from dw_connectors.adapters.zalo_link import ConnectToken, link_help, make_connect_token
from dw_connectors.inbound import (
    FAILED,
    NO_WORKSPACE,
    NOT_HANDLED,
    REFUSED,
    ChannelCommandRegistry,
    InboundMessage,
    InboundRouter,
    Reply,
    choose_workspace_reply,
)

pytestmark = pytest.mark.unit

_USER = uuid.UUID("0b8f1d7e-5a52-4f0e-9a43-0f3f8c1d2e01")
_TENANT, _WS = uuid.uuid4(), uuid.uuid4()
_OTHER_TENANT, _OTHER_WS = uuid.uuid4(), uuid.uuid4()
_CHAT = "chat-linked"
_SETTINGS = "https://portal.example/settings"
_SECRET = "router-link-secret"
_NOW = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)


@dataclass(frozen=True)
class _Ctx:
    """Stands in for the platform's AccessContext: what was asked for."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    scopes: frozenset[str]


@dataclass
class _Identities:
    links: dict[str, uuid.UUID] = field(default_factory=lambda: {_CHAT: _USER})

    async def user_id_for(self, chat_id: str) -> uuid.UUID | None:
        return self.links.get(chat_id)


@dataclass
class _Ledger:
    """Claims like the table: an id once, whatever its outcome."""

    rows: dict[tuple[str, str], str] = field(default_factory=dict)

    async def claim(self, channel: str, message_id: str, user_id: uuid.UUID) -> bool:
        if (channel, message_id) in self.rows:
            return False
        self.rows[(channel, message_id)] = "processing"
        return True

    async def settle(self, channel: str, message_id: str, outcome: str) -> None:
        if self.rows.get((channel, message_id)) == "processing":
            self.rows[(channel, message_id)] = outcome


@dataclass
class _Access:
    """The person's memberships, choice and membership scopes, as the platform holds them."""

    memberships: list[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=lambda: [(_TENANT, _WS)])
    chosen: tuple[uuid.UUID, uuid.UUID] | None = None
    held: frozenset[str] = frozenset({"supply_chain.product_case.write", "approvals.decide"})
    refuse: bool = False
    asked: list[frozenset[str]] = field(default_factory=list)

    async def chosen_workspace(self, user_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID] | None:
        return self.chosen

    async def workspaces_of(self, user_id: uuid.UUID) -> list[tuple[uuid.UUID, uuid.UUID]]:
        return list(self.memberships)

    async def access_for(
        self,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID,
        ceiling: frozenset[str],
    ) -> _Ctx | None:
        self.asked.append(ceiling)
        if self.refuse or (tenant_id, workspace_id) not in self.memberships:
            return None
        return _Ctx(user_id, tenant_id, workspace_id, self.held & ceiling)


@dataclass
class _Command:
    """Records what it was handed; ``takes`` decides whether it handles."""

    ceiling: frozenset[str] = frozenset({"supply_chain.product_case.write"})
    takes: bool = True
    boom: bool = False
    seen: list[tuple[InboundMessage, _Ctx]] = field(default_factory=list)

    async def handle(self, message: InboundMessage, context: _Ctx, reply: Reply) -> bool:
        self.seen.append((message, context))
        if self.boom:
            raise RuntimeError("the model went away")
        if self.takes:
            await reply("ok, handled")
        return self.takes


@dataclass
class _Sender:
    sent: list[tuple[str, str]] = field(default_factory=list)

    async def send_message(self, conversation_id: str, text: str) -> str:
        self.sent.append((conversation_id, text))
        return "m"


def _router(
    *commands: _Command,
    identities: _Identities | None = None,
    ledger: _Ledger | None = None,
    access: _Access | None = None,
    sender: _Sender | None = None,
) -> InboundRouter[_Ctx]:
    registry = ChannelCommandRegistry[_Ctx]()
    for i, command in enumerate(commands):
        registry.register(f"command-{i}", command)
    return InboundRouter(
        identities=identities or _Identities(),
        ledger=ledger or _Ledger(),
        access=access or _Access(),
        commands=registry,
        sender=sender,
        unlinked_reply=link_help("Cổng thử"),
        settings_url=_SETTINGS,
    )


def _msg(
    text: str = "đề xuất SP chảo 28cm", *, chat: str = _CHAT, mid: str = "m-1"
) -> InboundMessage:
    return InboundMessage(channel="zalo", message_id=mid, chat_id=chat, text=text)


# ---- who -----------------------------------------------------------------------


async def test_an_unlinked_chat_gets_the_link_sentence_and_reaches_no_command() -> None:
    command, ledger, access, sender = _Command(), _Ledger(), _Access(), _Sender()
    router = _router(command, ledger=ledger, access=access, sender=sender)

    await router.route(_msg(chat="stranger"))

    assert command.seen == []  # no command, so no model call either
    assert access.asked == []  # no context built
    assert ledger.rows == {}  # nothing recorded for a chat that is nobody
    assert sender.sent == [("stranger", link_help("Cổng thử"))]


async def test_unlinked_between_two_messages_refuses_the_second() -> None:
    identities, command, sender = _Identities(), _Command(), _Sender()
    router = _router(command, identities=identities, sender=sender)

    await router.route(_msg(mid="m-1"))
    identities.links.clear()  # /stop, or "Ngắt kết nối" on the web
    await router.route(_msg(mid="m-2"))

    assert len(command.seen) == 1
    assert sender.sent[-1] == (_CHAT, link_help("Cổng thử"))


# ---- once ----------------------------------------------------------------------


async def test_a_message_id_is_acted_on_once() -> None:
    command, ledger, sender = _Command(), _Ledger(), _Sender()
    router = _router(command, ledger=ledger, sender=sender)

    await router.route(_msg(mid="m-7"))
    await router.route(_msg(mid="m-7"))

    assert len(command.seen) == 1
    assert len(sender.sent) == 1
    assert ledger.rows == {("zalo", "m-7"): "done"}


async def test_a_command_that_raises_leaves_the_message_failed_and_says_so() -> None:
    command, ledger, sender = _Command(boom=True), _Ledger(), _Sender()
    router = _router(command, ledger=ledger, sender=sender)

    await router.route(_msg(mid="m-9"))
    await router.route(_msg(mid="m-9"))  # never re-processed, failed or not

    assert len(command.seen) == 1
    assert ledger.rows == {("zalo", "m-9"): "failed"}
    assert sender.sent == [(_CHAT, FAILED)]


# ---- where ---------------------------------------------------------------------


async def test_several_workspaces_and_no_choice_send_the_settings_link() -> None:
    access = _Access(memberships=[(_TENANT, _WS), (_OTHER_TENANT, _OTHER_WS)])
    command, ledger, sender = _Command(), _Ledger(), _Sender()

    await _router(command, ledger=ledger, access=access, sender=sender).route(_msg())

    assert command.seen == [] and access.asked == []
    assert sender.sent == [(_CHAT, choose_workspace_reply(_SETTINGS))]
    assert _SETTINGS in sender.sent[0][1]
    assert ledger.rows == {("zalo", "m-1"): "ignored"}


async def test_the_chosen_workspace_is_used_among_several() -> None:
    access = _Access(
        memberships=[(_TENANT, _WS), (_OTHER_TENANT, _OTHER_WS)], chosen=(_OTHER_TENANT, _OTHER_WS)
    )
    command = _Command()

    await _router(command, access=access).route(_msg())

    [(_, context)] = command.seen
    assert (context.tenant_id, context.workspace_id) == (_OTHER_TENANT, _OTHER_WS)
    assert context.user_id == _USER


async def test_a_person_with_no_membership_is_refused() -> None:
    access, command, sender = _Access(memberships=[]), _Command(), _Sender()

    await _router(command, access=access, sender=sender).route(_msg())

    assert command.seen == []
    assert sender.sent == [(_CHAT, NO_WORKSPACE)]


async def test_text_naming_another_tenant_or_user_changes_nothing() -> None:
    """The message is data for a command, never where the context comes from."""
    command = _Command()
    text = f"tenant {_OTHER_TENANT} workspace {_OTHER_WS} user {uuid.uuid4()} pic_user_id=x"

    await _router(command).route(_msg(text))

    [(message, context)] = command.seen
    assert message.text == text
    assert (context.user_id, context.tenant_id, context.workspace_id) == (_USER, _TENANT, _WS)


# ---- what ----------------------------------------------------------------------


async def test_each_command_gets_a_context_for_its_own_ceiling() -> None:
    """`approvals.decide` is held by the membership and asked by no command, so
    no context carries it."""
    access = _Access()
    first = _Command(ceiling=frozenset({"crm.read"}), takes=False)
    second = _Command(ceiling=frozenset({"supply_chain.product_case.write"}))

    await _router(first, second, access=access).route(_msg())

    assert access.asked == [first.ceiling, second.ceiling]
    assert first.seen[0][1].scopes == frozenset()
    assert second.seen[0][1].scopes == frozenset({"supply_chain.product_case.write"})
    assert all("approvals.decide" not in ctx.scopes for _, ctx in first.seen + second.seen)


async def test_commands_are_asked_in_order_and_the_first_taker_ends_the_walk() -> None:
    first, second, third = _Command(takes=False), _Command(), _Command()

    await _router(first, second, third).route(_msg())

    assert (len(first.seen), len(second.seen), len(third.seen)) == (1, 1, 0)


async def test_a_refused_context_ends_the_walk_with_a_refusal() -> None:
    """Membership gone or tenant locked: the platform builds no context."""
    access, command, ledger, sender = _Access(refuse=True), _Command(), _Ledger(), _Sender()

    await _router(command, ledger=ledger, access=access, sender=sender).route(_msg())

    assert command.seen == []
    assert sender.sent == [(_CHAT, REFUSED)]
    assert ledger.rows == {("zalo", "m-1"): "ignored"}


async def test_with_no_command_registered_a_linked_person_is_told_it_was_not_handled() -> None:
    access, ledger, sender = _Access(), _Ledger(), _Sender()

    await _router(ledger=ledger, access=access, sender=sender).route(_msg())

    assert sender.sent == [(_CHAT, NOT_HANDLED)]
    assert ledger.rows == {("zalo", "m-1"): "ignored"}


async def test_no_command_takes_it() -> None:
    sender = _Sender()
    await _router(_Command(takes=False), sender=sender).route(_msg())
    assert sender.sent == [(_CHAT, NOT_HANDLED)]


def test_the_registry_refuses_a_blank_or_repeated_name() -> None:
    registry = ChannelCommandRegistry[_Ctx]()
    registry.register("propose", _Command())
    with pytest.raises(ValueError, match="already registered"):
        registry.register("propose", _Command())
    with pytest.raises(ValueError, match="needs a name"):
        registry.register(" ", _Command())


# ---- the Zalo entry ------------------------------------------------------------


class _Clock:
    def now(self) -> datetime:
        return _NOW


@dataclass
class _LinkStore:
    issued: set[str] = field(default_factory=set)
    links: dict[str, uuid.UUID] = field(default_factory=dict)

    async def redeem(self, token: ConnectToken, zalo_id: str) -> bool:
        if token.jti not in self.issued:
            return False
        self.issued.discard(token.jti)
        self.links[zalo_id] = token.user_id
        return True

    async def unlink_by_zalo(self, zalo_id: str) -> bool:
        return self.links.pop(zalo_id, None) is not None

    async def user_id_for(self, chat_id: str) -> uuid.UUID | None:
        return self.links.get(chat_id)


@dataclass
class _Routes:
    routed: list[InboundMessage] = field(default_factory=list)

    async def route(self, message: InboundMessage) -> None:
        self.routed.append(message)


def _entry(store: _LinkStore, routes: _Routes, sender: _Sender) -> ZaloInbound:
    return ZaloInbound(
        link_secret=_SECRET,
        store=store,
        sender=sender,
        clock=_Clock(),
        product_name="Cổng thử",
        router=routes,
    )


def _update(text: str, *, message_id: str | None = "zm-1") -> dict[str, Any]:
    message: dict[str, Any] = {"chat": {"id": _CHAT}, "text": text}
    if message_id is not None:
        message["message_id"] = message_id
    return {"result": {"message": message, "event_name": "message.text.received"}}


async def test_start_and_stop_still_go_to_the_link_flow() -> None:
    code, claims = make_connect_token(_USER, _SECRET, now=_NOW)
    store, routes, sender = _LinkStore(issued={claims.jti}), _Routes(), _Sender()
    entry = _entry(store, routes, sender)

    await entry.handle(_update(f"/start {code}"))
    assert store.links == {_CHAT: _USER}
    await entry.handle(_update("/stop"))
    assert store.links == {}
    await entry.handle(_update("/start"))  # malformed: the link flow's help

    assert routes.routed == []
    assert "Đã kết nối Zalo" in sender.sent[0][1]
    assert "Đã ngắt kết nối Zalo" in sender.sent[1][1]
    assert sender.sent[2][1] == link_help("Cổng thử")


async def test_other_text_goes_to_the_router_keyed_by_the_message_id() -> None:
    routes = _Routes()
    await _entry(_LinkStore(), routes, _Sender()).handle(_update("đồng ý", message_id="zm-42"))
    assert routes.routed == [
        InboundMessage(channel="zalo", message_id="zm-42", chat_id=_CHAT, text="đồng ý")
    ]


async def test_a_text_without_a_message_id_is_not_routed() -> None:
    routes, sender = _Routes(), _Sender()
    await _entry(_LinkStore(), routes, sender).handle(_update("xin chào", message_id=None))
    assert routes.routed == []
    assert sender.sent == [(_CHAT, NOT_HANDLED)]


async def test_an_update_without_text_is_left_alone() -> None:
    routes, sender = _Routes(), _Sender()
    await _entry(_LinkStore(), routes, sender).handle(
        {"result": {"message": {"chat": {"id": _CHAT}, "message_id": "zm-3"}}}
    )
    assert routes.routed == [] and sender.sent == []
