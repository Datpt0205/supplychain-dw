"""The Zalo proposal (zalo-channel ticket 04, Z4b) end to end through the poll
lane, against real Postgres as ``dw_app``.

Built from the worker's own wiring: ``build_zalo_inbound`` with the registry
``build_channel_commands`` fills, over the gateway ``build_one_call_gateway``
makes from ``build_model_stack_for`` — the model builder the API uses too. The
model is the mock provider of that stack, given a scripted answer. What it shows
that no unit test can:

* a linked person's chat proposal, confirmed with "Đồng ý", creates exactly one
  case in their own workspace with them as PIC, under the real authorization
  and the real ceiling (a ceiling that disagreed with the handler would refuse);
* the same "Đồng ý" delivered twice at once creates one case — through the
  message-id claim when the id repeats, and through the guarded draft consume
  when it does not;
* the plan's runs-per-day and spend-per-day are checked on THIS path, before
  the model is called (failure-modes #5);
* unlinked or removed between turns, nothing is created.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
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

from dw_agent_runtime.adapters.mock_model import MockModelAdapter
from dw_agent_runtime.adapters.runtime_tables import tenant_daily_spend_guard
from dw_agent_runtime.model.prompts import RenderedPrompt
from dw_connectors.adapters.zalo_inbound import NO_PHOTOS
from dw_connectors.adapters.zalo_link import ZaloLinking, link_help
from dw_connectors.inbound import NO_WORKSPACE
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_observability.telemetry import NullTelemetry
from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.zalo_link_repo import SqlZaloLink
from dw_platform.application.entitlement import DEFAULT_PLANS, PlanEntitlementService
from dw_supply_chain.adapters.persistence import tables as sc_tables
from dw_supply_chain.presentation.zalo_case_query import NO_READ
from dw_supply_chain.presentation.zalo_proposal import CONFIRM_HINT
from dw_supply_chain.workflows.product_proposal_understanding import (
    PROMPT_VERSION as PROPOSAL_VERSION,
)
from dw_worker.composition import REPO_ROOT, build_model_stack_for
from dw_worker.consumers.supply_chain import build_product_review_runner
from dw_worker.consumers.zalo_poll import build_zalo_poll_consumer
from dw_worker.main import (
    build_channel_commands,
    build_channel_decision_command,
    build_one_call_gateway,
    build_zalo_inbound,
)
from dw_worker.settings import WorkerSettings

pytestmark = pytest.mark.integration

_SECRET = "proposal-db-link-secret"
_PRODUCT = "Cổng thử"
_PROMPT = "supply_chain.product_proposal_understanding"


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


@dataclass(frozen=True)
class _Plan:
    """A plan catalogue with the two limits a test names."""

    runs: int | None = None
    spend: Decimal | None = None

    def runs_per_day(self, plan_id: str) -> int | None:
        return self.runs

    def spend_usd_per_day(self, plan_id: str) -> Decimal | None:
        return self.spend


@dataclass
class _Lane:
    sessions: async_sessionmaker[AsyncSession]
    allowance: Any = field(default_factory=lambda: PlanEntitlementService(DEFAULT_PLANS))
    bot: _Bot = field(default_factory=_Bot)
    model: MockModelAdapter = field(init=False)
    inbound: Any = field(init=False)
    # The code the scripted model reads out of a proposal message.
    code: str = ""

    def answer(self, prompt: RenderedPrompt) -> dict[str, object]:
        """The mock provider's reading: a full proposal when the message has one."""
        if "chảo chống dính 28cm" in prompt.user:
            return {
                "kind": "propose_product",
                "proposal_code": self.code,
                "product_name": "chảo chống dính 28cm",
                "category": "Chảo",
            }
        return {"kind": "unsupported"}

    def __post_init__(self) -> None:
        settings = WorkerSettings(
            zalo_link_secret=_SECRET,
            product_name=_PRODUCT,
            public_web_url="https://portal.example",
            model_provider="mock",
            model_profile="balanced",
            openai_api_key="",
            openai_base_url="",
        )
        clock = SystemClock()
        stack = build_model_stack_for(
            settings, self.sessions, clock=clock, telemetry=NullTelemetry()
        )
        model = stack.gateway.adapters["mock"]
        assert isinstance(model, MockModelAdapter)
        model.register_builder(_PROMPT, PROPOSAL_VERSION, self.answer)
        self.model = model
        commands = build_channel_commands(
            settings,
            self.sessions,
            gateway=build_one_call_gateway(
                stack, self.sessions, allowance=self.allowance, clock=clock
            ),
            decisions=build_channel_decision_command(
                settings,
                self.sessions,
                runner=build_product_review_runner(
                    self.sessions,
                    configs_dir=REPO_ROOT / "configs",
                    ids=Uuid4Generator(),
                    clock=clock,
                    telemetry=NullTelemetry(),
                    release_manifest_ref=None,
                ),
                ids=Uuid4Generator(),
                clock=clock,
            ),
            ids=Uuid4Generator(),
            clock=clock,
        )
        self.inbound = build_zalo_inbound(settings, self.sessions, self.bot, clock, commands)

    async def send(self, chat: str, text: str, message_id: str | None = None) -> str:
        self.bot.updates.append(_update(chat, text, message_id or uuid.uuid4().hex))
        await build_zalo_poll_consumer(self.bot, self.inbound)()
        return self.bot.sent[-1][1]


def _update(chat: str, text: str | None, message_id: str) -> dict[str, Any]:
    message: dict[str, Any] = {"chat": {"id": chat}, "message_id": message_id}
    if text is not None:
        message["text"] = text
    return {"result": {"message": message, "event_name": "message.text.received"}}


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
    migrator: AsyncEngine, user_id: uuid.UUID, roles: list[str]
) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_id, workspace_id = uuid.uuid4(), uuid.uuid4()
    async with migrator.begin() as conn:
        await conn.execute(
            sa.insert(tables.tenants).values(id=tenant_id, slug=f"t-{tenant_id.hex[:8]}", name="T")
        )
        await conn.execute(
            sa.insert(tables.workspaces).values(
                id=workspace_id, tenant_id=tenant_id, slug="main", name="Cung ứng HN"
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
                role_keys=roles,
            )
        )
    return tenant_id, workspace_id


def _linking(sessions: async_sessionmaker[AsyncSession]) -> ZaloLinking:
    return ZaloLinking(store=SqlZaloLink(sessions), link_secret=_SECRET, clock=SystemClock())


@dataclass(frozen=True)
class _Person:
    user: uuid.UUID
    tenant: uuid.UUID
    workspace: uuid.UUID
    chat: str


async def _linked(lane: _Lane, migrator: AsyncEngine, roles: list[str] | None = None) -> _Person:
    """A person whose membership also grants approvals.decide (`approver`)."""
    user = await _user(migrator)
    tenant, workspace = await _member(migrator, user, roles or ["sc_operator", "approver"])
    chat = f"zalo-{uuid.uuid4().hex[:12]}"
    offer = await _linking(lane.sessions).connect(user)
    await lane.send(chat, f"/start {offer.code}")
    return _Person(user, tenant, workspace, chat)


def _proposal(lane: _Lane, code: str) -> str:
    lane.code = code
    return f"đề xuất SP chảo chống dính 28cm, mã {code}, nhóm Chảo"


async def _cases(migrator: AsyncEngine, code: str) -> list[sa.Row[Any]]:
    async with migrator.connect() as conn:
        return list(
            (
                await conn.execute(
                    sa.select(sc_tables.product_dev_cases).where(
                        sc_tables.product_dev_cases.c.proposal_code == code
                    )
                )
            ).all()
        )


async def _drafts(migrator: AsyncEngine, user: uuid.UUID) -> int:
    async with migrator.connect() as conn:
        found = await conn.scalar(
            sa.select(sa.func.count())
            .select_from(sc_tables.proposal_drafts)
            .where(sc_tables.proposal_drafts.c.user_id == user)
        )
    return int(found or 0)


def _code() -> str:
    return f"CH-{uuid.uuid4().hex[:6]}"


# ---- the proposal --------------------------------------------------------------------


async def test_a_confirmed_chat_proposal_creates_one_case_with_the_linked_person_as_pic(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    lane = _Lane(sessions)
    person = await _linked(lane, migrator)
    code = _code()

    summary = await lane.send(person.chat, _proposal(lane, code))
    assert f"Mã đề xuất: {code}" in summary and "«Cung ứng HN»" in summary
    assert CONFIRM_HINT in summary
    assert await _cases(migrator, code) == []

    created = await lane.send(person.chat, "dong y")

    [case] = await _cases(migrator, code)
    assert (case.tenant_id, case.workspace_id) == (person.tenant, person.workspace)
    assert case.pic_user_id == person.user and case.created_by == person.user
    assert str(case.id) in created
    assert await _drafts(migrator, person.user) == 0
    async with migrator.connect() as conn:
        details = await conn.scalar(
            sa.select(tables.audit_events.c.details).where(
                tables.audit_events.c.resource_id == str(case.id),
                tables.audit_events.c.action == "supply_chain.product_case.propose",
            )
        )
    assert details is not None and details["origin"]["channel"] == "zalo"
    assert person.chat not in str(details)


async def test_ok_and_duoc_after_the_summary_create_nothing(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    lane = _Lane(sessions)
    person = await _linked(lane, migrator)
    code = _code()
    await lane.send(person.chat, _proposal(lane, code))

    for text in ("ok", "được"):
        await lane.send(person.chat, text)

    assert await _cases(migrator, code) == []
    assert await _drafts(migrator, person.user) == 1


async def test_someone_who_may_neither_propose_nor_read_gets_a_refusal_and_no_draft(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    """Not a proposer: the proposal command hands the message on, and the
    read-only question command (ticket 06) refuses someone who cannot read
    either — both before any model call."""
    lane = _Lane(sessions)
    person = await _linked(lane, migrator, roles=["approver"])
    calls_before = len(lane.model.calls)

    reply = await lane.send(person.chat, _proposal(lane, _code()))

    assert reply == NO_READ
    assert await _drafts(migrator, person.user) == 0
    assert len(lane.model.calls) == calls_before


# ---- once ----------------------------------------------------------------------------


async def test_the_same_dong_y_delivered_twice_at_once_creates_one_case(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    lane = _Lane(sessions)
    person = await _linked(lane, migrator)
    code = _code()
    await lane.send(person.chat, _proposal(lane, code))
    update = _update(person.chat, "Đồng ý", f"zm-{uuid.uuid4().hex}")
    sent_before = len(lane.bot.sent)

    await asyncio.gather(lane.inbound.handle(update), lane.inbound.handle(update))

    assert len(await _cases(migrator, code)) == 1
    # The second delivery never reached the command: one reply, the creation.
    [(_, reply)] = lane.bot.sent[sent_before:]
    assert reply.startswith("Đã tạo hồ sơ")


async def test_two_dong_y_with_different_ids_still_create_one_case(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    """The message-id claim cannot help here; the guarded consume of the
    draft in the case's own transaction does."""
    lane = _Lane(sessions)
    person = await _linked(lane, migrator)
    code = _code()
    await lane.send(person.chat, _proposal(lane, code))

    await asyncio.gather(
        lane.inbound.handle(_update(person.chat, "Đồng ý", f"zm-{uuid.uuid4().hex}")),
        lane.inbound.handle(_update(person.chat, "đồng ý", f"zm-{uuid.uuid4().hex}")),
    )

    assert len(await _cases(migrator, code)) == 1
    assert await _drafts(migrator, person.user) == 0


# ---- the plan's day, on this path ----------------------------------------------------


async def test_a_plan_with_no_runs_left_today_is_refused_before_the_model(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    lane = _Lane(sessions, allowance=_Plan(runs=0))
    person = await _linked(lane, migrator)
    calls_before = len(lane.model.calls)

    reply = await lane.send(person.chat, _proposal(lane, _code()))

    assert "hôm nay đã dùng hết số lượt chạy của gói" in reply
    assert "chưa hiểu" not in reply
    assert len(lane.model.calls) == calls_before
    assert await _drafts(migrator, person.user) == 0


async def test_a_tenant_past_its_daily_spend_is_refused_before_the_model(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    lane = _Lane(sessions, allowance=_Plan(spend=Decimal("1.00")))
    person = await _linked(lane, migrator)
    async with migrator.begin() as conn:
        await conn.execute(
            sa.insert(tenant_daily_spend_guard).values(
                tenant_id=person.tenant,
                spend_date=datetime.now(UTC).date(),
                spend_usd=Decimal("1.50"),
                updated_at=datetime.now(UTC),
            )
        )
    calls_before = len(lane.model.calls)

    reply = await lane.send(person.chat, _proposal(lane, _code()))

    assert "hôm nay đã dùng hết trần chi tiêu của gói" in reply
    assert len(lane.model.calls) == calls_before


# ---- who, between turns --------------------------------------------------------------


async def test_unlinked_between_summary_and_dong_y_creates_nothing(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    lane = _Lane(sessions)
    person = await _linked(lane, migrator)
    code = _code()
    await lane.send(person.chat, _proposal(lane, code))
    await _linking(sessions).disconnect(person.user)

    assert await lane.send(person.chat, "Đồng ý") == link_help(_PRODUCT)
    assert await _cases(migrator, code) == []


async def test_a_membership_removed_between_summary_and_dong_y_creates_nothing(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    lane = _Lane(sessions)
    person = await _linked(lane, migrator)
    code = _code()
    await lane.send(person.chat, _proposal(lane, code))
    async with migrator.begin() as conn:
        await conn.execute(
            sa.delete(tables.memberships).where(tables.memberships.c.user_id == person.user)
        )

    assert await lane.send(person.chat, "Đồng ý") == NO_WORKSPACE
    assert await _cases(migrator, code) == []
    assert await _drafts(migrator, person.user) == 0  # gone with the membership


async def test_a_photo_is_answered_and_nothing_is_stored(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    lane = _Lane(sessions)
    person = await _linked(lane, migrator)
    message_id = f"zm-{uuid.uuid4().hex}"

    lane.bot.updates.append(_update(person.chat, None, message_id))
    await build_zalo_poll_consumer(lane.bot, lane.inbound)()

    assert lane.bot.sent[-1] == (person.chat, NO_PHOTOS)
    assert await _drafts(migrator, person.user) == 0
    async with migrator.connect() as conn:
        claimed = await conn.scalar(
            sa.select(sa.func.count())
            .select_from(tables.channel_inbound_messages)
            .where(tables.channel_inbound_messages.c.external_message_id == message_id)
        )
    assert claimed == 0
