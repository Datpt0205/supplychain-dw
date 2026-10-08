"""Read-only questions through a linked chat (zalo-channel ticket 06, Z6) end to
end through the poll lane, against real Postgres as ``dw_app``.

Built from the worker's own wiring (``build_channel_commands``,
``build_zalo_inbound``, the one-call gateway over ``build_model_stack_for``); the
model is the mock provider of that stack, given scripted readings. What it shows
that no unit test can:

* a PO of another tenant, or of another workspace of the same tenant, reads
  exactly as one that does not exist — RLS, not a filter in code, hides it
  (`po_cases` narrowed by workspace in `62cdcf3bf2d2`, port ticket 04);
* the chat answers what the web's ``POST /case-query`` answers for the same
  person and question, in a tenant with restricted record visibility (the
  context built from the linked chat against the one built at sign-in);
* a request to change a case changes nothing, whoever asks;
* a proposer's question goes through the proposal reading to the question
  command; the plan's runs-per-day is checked on this path before the model.
"""

from __future__ import annotations

import html
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
import sqlalchemy as sa
from pg_test_db import DatabaseUrls
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from dw_agent_runtime.adapters.mock_model import MockModelAdapter
from dw_agent_runtime.model.prompts import RenderedPrompt
from dw_connectors.adapters.zalo_link import ZaloLinking
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_observability.telemetry import NullTelemetry
from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.membership_lookup import SqlMembershipLookup
from dw_platform.adapters.persistence.zalo_link_repo import SqlZaloLink
from dw_platform.application.access_context import AccessContext
from dw_platform.application.entitlement import DEFAULT_PLANS, PlanEntitlementService
from dw_platform.application.identity import context_from
from dw_supply_chain.adapters.persistence import tables as sc_tables
from dw_supply_chain.presentation.zalo_case_query import (
    NO_READ,
    ZaloCaseQueryCommand,
    read_only_hint,
)
from dw_supply_chain.presentation.zalo_proposal import NOT_UNDERSTOOD
from dw_supply_chain.workflows.case_query_understanding import (
    PROMPT_ID as CASE_QUERY_PROMPT,
)
from dw_supply_chain.workflows.case_query_understanding import (
    PROMPT_VERSION as CASE_QUERY_VERSION,
)
from dw_supply_chain.workflows.product_proposal_understanding import (
    PROMPT_ID as PROPOSAL_PROMPT,
)
from dw_supply_chain.workflows.product_proposal_understanding import (
    PROMPT_VERSION as PROPOSAL_VERSION,
)
from dw_worker.composition import REPO_ROOT, build_model_stack_for
from dw_worker.consumers.supply_chain import (
    PRODUCT_STRICT_APPROVAL_PREFIXES,
    build_product_review_runner,
    product_approval_subjects,
)
from dw_worker.consumers.zalo_poll import build_zalo_poll_consumer
from dw_worker.main import (
    build_channel_commands,
    build_channel_decision_command,
    build_one_call_gateway,
    build_zalo_inbound,
)
from dw_worker.settings import WorkerSettings

pytestmark = pytest.mark.integration

_SECRET = "case-query-db-link-secret"
_WEB = "https://portal.example"
_INPUT = re.compile(r'<input name="[a-z_]+">\s*(.*?)\s*</input>', re.DOTALL)


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
    runs: int | None = None

    def runs_per_day(self, plan_id: str) -> int | None:
        return self.runs

    def spend_usd_per_day(self, plan_id: str) -> None:
        return None


def _question(prompt: RenderedPrompt) -> str:
    found = _INPUT.search(prompt.user)
    assert found is not None
    # The registry escapes what it contains; the person typed the unescaped text.
    return html.unescape(found.group(1))


@dataclass
class _Lane:
    sessions: async_sessionmaker[AsyncSession]
    allowance: Any = field(default_factory=lambda: PlanEntitlementService(DEFAULT_PLANS))
    bot: _Bot = field(default_factory=_Bot)
    # Question text -> the case-query reading the scripted model returns.
    readings: dict[str, dict[str, object]] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    inbound: Any = field(init=False)
    command: ZaloCaseQueryCommand = field(init=False)

    def read_question(self, prompt: RenderedPrompt) -> dict[str, object]:
        self.calls.append(prompt.prompt_id)
        return self.readings.get(_question(prompt), {"kind": "unsupported"})

    def read_proposal(self, prompt: RenderedPrompt) -> dict[str, object]:
        # A proposer's every message here is a question about existing cases.
        self.calls.append(prompt.prompt_id)
        return {"kind": "question"}

    def __post_init__(self) -> None:
        settings = WorkerSettings(
            zalo_link_secret=_SECRET,
            product_name="Cổng thử",
            public_web_url=_WEB,
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
        model.register_builder(CASE_QUERY_PROMPT, CASE_QUERY_VERSION, self.read_question)
        model.register_builder(PROPOSAL_PROMPT, PROPOSAL_VERSION, self.read_proposal)
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
                subjects=product_approval_subjects(self.sessions),
                strict_approval_prefixes=PRODUCT_STRICT_APPROVAL_PREFIXES,
                ids=Uuid4Generator(),
                clock=clock,
            ),
            ids=Uuid4Generator(),
            clock=clock,
        )
        names = [name for name, _ in commands.commands()]
        # Z4a's order: decide, the open conversation, then the question.
        assert names == [
            "approval_decision",
            "supply_chain.product_proposal",
            "supply_chain.case_query",
        ]
        command = commands.commands()[-1][1]
        assert isinstance(command, ZaloCaseQueryCommand)
        self.command = command
        self.inbound = build_zalo_inbound(settings, self.sessions, self.bot, clock, commands)

    async def send(self, chat: str, text: str) -> str:
        self.bot.updates.append(
            {
                "result": {
                    "message": {
                        "chat": {"id": chat},
                        "message_id": uuid.uuid4().hex,
                        "text": text,
                    },
                    "event_name": "message.text.received",
                }
            }
        )
        await build_zalo_poll_consumer(self.bot, self.inbound)()
        return self.bot.sent[-1][1]


@dataclass(frozen=True)
class _Person:
    user: uuid.UUID
    tenant: uuid.UUID
    workspace: uuid.UUID
    chat: str


async def _tenant(
    migrator: AsyncEngine, *, visibility: str = "open"
) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_id, workspace_id = uuid.uuid4(), uuid.uuid4()
    async with migrator.begin() as conn:
        await conn.execute(
            sa.insert(tables.tenants).values(
                id=tenant_id,
                slug=f"t-{tenant_id.hex[:8]}",
                name="T",
                record_visibility=visibility,
            )
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
    return tenant_id, workspace_id


async def _workspace(migrator: AsyncEngine, tenant_id: uuid.UUID) -> uuid.UUID:
    workspace_id = uuid.uuid4()
    async with migrator.begin() as conn:
        await conn.execute(
            sa.insert(tables.workspaces).values(
                id=workspace_id, tenant_id=tenant_id, slug=f"w-{workspace_id.hex[:6]}", name="W2"
            )
        )
    return workspace_id


async def _linked(
    lane: _Lane,
    migrator: AsyncEngine,
    tenant: uuid.UUID,
    workspace: uuid.UUID,
    roles: list[str],
) -> _Person:
    user = uuid.uuid4()
    async with migrator.begin() as conn:
        await conn.execute(
            sa.insert(tables.users).values(
                id=user, subject=f"test|{user}", display_name="Người hỏi"
            )
        )
        await conn.execute(
            sa.insert(tables.memberships).values(
                id=uuid.uuid4(),
                tenant_id=tenant,
                workspace_id=workspace,
                user_id=user,
                role_keys=roles,
            )
        )
    chat = f"zalo-{uuid.uuid4().hex[:12]}"
    offer = await ZaloLinking(
        store=SqlZaloLink(lane.sessions), link_secret=_SECRET, clock=SystemClock()
    ).connect(user)
    await lane.send(chat, f"/start {offer.code}")
    return _Person(user, tenant, workspace, chat)


async def _po(
    migrator: AsyncEngine,
    tenant: uuid.UUID,
    workspace: uuid.UUID,
    reference: str,
    *,
    supplier: str = "Sunhouse Co.",
    state: str = "waiting_deposit",
    pic: uuid.UUID | None = None,
) -> uuid.UUID:
    case_id = uuid.uuid4()
    suppliers = sc_tables.suppliers
    async with migrator.begin() as conn:
        # The workspace's supplier master record (a0035e9faf32), which a case
        # must name; reused when this name was already written.
        await conn.execute(
            pg_insert(suppliers)
            .values(id=uuid.uuid4(), tenant_id=tenant, workspace_id=workspace, name=supplier)
            .on_conflict_do_nothing()
        )
        supplier_id = await conn.scalar(
            sa.select(suppliers.c.id).where(
                suppliers.c.tenant_id == tenant,
                suppliers.c.workspace_id == workspace,
                suppliers.c.name == supplier,
            )
        )
        await conn.execute(
            sa.insert(sc_tables.po_cases).values(
                id=case_id,
                tenant_id=tenant,
                workspace_id=workspace,
                po_reference=reference,
                supplier_id=supplier_id,
                supplier_name=supplier,
                state=state,
                order_kind="reorder",
                pic_user_id=pic,
            )
        )
    return case_id


def _ref() -> str:
    return f"PO-{uuid.uuid4().hex[:6]}"


_READER = ["sc_viewer"]


# ---- what the asker cannot see does not exist ---------------------------------------


async def test_a_po_of_another_tenant_reads_exactly_as_one_that_does_not_exist(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    lane = _Lane(sessions)
    tenant_a, ws_a = await _tenant(migrator)
    tenant_b, ws_b = await _tenant(migrator)
    asker = await _linked(lane, migrator, tenant_a, ws_a, _READER)
    foreign, missing = _ref(), _ref()
    await _po(migrator, tenant_b, ws_b, foreign, supplier="Their Secret Co.")
    for ref in (foreign, missing):
        lane.readings[f"{ref} đang ở đâu?"] = {"kind": "open_case", "po_reference_mention": ref}

    seen_foreign = await lane.send(asker.chat, f"{foreign} đang ở đâu?")
    seen_missing = await lane.send(asker.chat, f"{missing} đang ở đâu?")

    assert seen_foreign == f"Không tìm thấy PO «{foreign}»."
    assert seen_foreign == seen_missing.replace(missing, foreign)


async def test_another_workspace_of_the_same_tenant_is_not_seen(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    lane = _Lane(sessions)
    tenant, w1 = await _tenant(migrator)
    w2 = await _workspace(migrator, tenant)
    asker = await _linked(lane, migrator, tenant, w2, _READER)
    in_w1 = _ref()
    await _po(migrator, tenant, w1, in_w1)
    lane.readings[f"{in_w1} thế nào?"] = {"kind": "open_case", "po_reference_mention": in_w1}
    lane.readings["Cho tôi các PO"] = {"kind": "list_cases"}

    assert await lane.send(asker.chat, f"{in_w1} thế nào?") == f"Không tìm thấy PO «{in_w1}»."
    assert await lane.send(asker.chat, "Cho tôi các PO") == "Không có PO nào khớp."
    # The web's `POST /case-query` for the same person: the same handler,
    # under the context sign-in builds for W2.
    access = await SqlMembershipLookup(sessions).find_access(
        f"test|{asker.user}", "dev", tenant, w2
    )
    assert access is not None
    web = await lane.command.answer.handle(context_from(access), "Cho tôi các PO")
    assert web.cases == ()


async def test_the_chat_answers_what_the_web_answers_for_the_same_person_and_question(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    """Restricted record visibility: the context the chat builds (linked user,
    cut to the read ceiling) and the one sign-in builds read the same cases.
    Supply Chain's PO lists are workspace-wide on the web too, so both name
    the colleague's case; the point is that they cannot disagree."""
    lane = _Lane(sessions)
    tenant, ws = await _tenant(migrator, visibility="restricted")
    asker = await _linked(lane, migrator, tenant, ws, _READER)
    colleague = await _linked(lane, migrator, tenant, ws, ["sc_operator"])
    own, theirs = _ref(), _ref()
    await _po(migrator, tenant, ws, own, pic=asker.user)
    await _po(migrator, tenant, ws, theirs, pic=colleague.user)
    question = "Cho tôi các PO"
    lane.readings[question] = {"kind": "list_cases"}

    chat_reply = await lane.send(asker.chat, question)
    access = await SqlMembershipLookup(sessions).find_access(
        f"test|{asker.user}", "dev", tenant, ws
    )
    assert access is not None
    web = await lane.command.answer.handle(context_from(access), question)

    chat_refs = set(re.findall(r"^- (PO-\w+) ", chat_reply, re.MULTILINE))
    assert chat_refs == {case.po_reference for case in web.cases}
    assert chat_refs == {own, theirs}


# ---- nothing changes ---------------------------------------------------------------------


async def test_a_change_request_changes_nothing_even_for_someone_who_could(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    """An operator holds the step duties; asked in the chat, the proposal
    reading hands it on as a question and the read-only command answers that
    the chat only asks. The case, its history and the approvals are as they were."""
    lane = _Lane(sessions)
    tenant, ws = await _tenant(migrator)
    operator = await _linked(lane, migrator, tenant, ws, ["sc_operator", "approver"])
    ref = _ref()
    case_id = await _po(migrator, tenant, ws, ref, state="waiting_deposit")

    reply = await lane.send(operator.chat, f"chuyển {ref} sang đã cọc")

    assert reply == f"{NOT_UNDERSTOOD}\n{read_only_hint(_WEB)}"
    assert lane.calls == [PROPOSAL_PROMPT, CASE_QUERY_PROMPT]
    async with migrator.connect() as conn:
        state = await conn.scalar(
            sa.select(sc_tables.po_cases.c.state).where(sc_tables.po_cases.c.id == case_id)
        )
        transitions = await conn.scalar(
            sa.select(sa.func.count())
            .select_from(sc_tables.po_case_state_transitions)
            .where(sc_tables.po_case_state_transitions.c.po_case_id == case_id)
        )
        approvals = await conn.scalar(
            sa.select(sa.func.count())
            .select_from(tables.approval_requests)
            .where(tables.approval_requests.c.tenant_id == tenant)
        )
    assert (state, transitions, approvals) == ("waiting_deposit", 0, 0)


# ---- who may ask, and the plan's day -------------------------------------------------


async def test_a_reader_is_answered_with_states_and_links(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    lane = _Lane(sessions)
    tenant, ws = await _tenant(migrator)
    asker = await _linked(lane, migrator, tenant, ws, _READER)
    ref = _ref()
    case_id = await _po(migrator, tenant, ws, ref, state="in_transit")
    lane.readings[f"{ref} bao giờ về?"] = {"kind": "open_case", "po_reference_mention": ref}

    reply = await lane.send(asker.chat, f"{ref} bao giờ về?")

    assert reply.endswith(
        f"- {ref} — Sunhouse Co.: Đang vận chuyển {_WEB}/supply-chain/po-cases/{case_id}"
    )
    assert lane.calls == [CASE_QUERY_PROMPT]  # a reader is not read as a proposal


async def test_someone_without_the_read_scope_is_told_so_before_the_model(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    lane = _Lane(sessions)
    tenant, ws = await _tenant(migrator)
    asker = await _linked(lane, migrator, tenant, ws, ["approver"])

    assert await lane.send(asker.chat, "Cho tôi các PO") == NO_READ
    assert lane.calls == []


async def test_a_plan_with_no_runs_left_today_is_said_before_the_model(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    lane = _Lane(sessions, allowance=_Plan(runs=0))
    tenant, ws = await _tenant(migrator)
    asker = await _linked(lane, migrator, tenant, ws, _READER)

    reply = await lane.send(asker.chat, "Cho tôi các PO")

    assert "hôm nay đã dùng hết số lượt chạy của gói" in reply
    assert "chưa hiểu" not in reply
    assert lane.calls == []


async def test_the_question_command_never_holds_more_than_the_read_scope(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    """The context the router builds for it from a membership that holds
    writes, duties and approvals.decide: the two read scopes, no role."""
    lane = _Lane(sessions)
    tenant, ws = await _tenant(migrator)
    operator = await _linked(lane, migrator, tenant, ws, ["sc_operator", "approver"])
    access = await SqlMembershipLookup(sessions).find_linked_access(
        operator.user, tenant, ws, lane.command.ceiling
    )
    assert access is not None
    context: AccessContext = context_from(access)
    assert context.scopes == {"supply_chain.po_case.read", "supply_chain.product_case.read"}
    assert context.roles == frozenset()


# ---- product-development cases (stage-1 ticket 08) ------------------------------------


async def _product(
    migrator: AsyncEngine,
    tenant: uuid.UUID,
    workspace: uuid.UUID,
    code: str,
    *,
    pic: uuid.UUID,
    name: str = "Chảo chống dính 28",
) -> uuid.UUID:
    case_id = uuid.uuid4()
    async with migrator.begin() as conn:
        await conn.execute(
            sa.insert(sc_tables.product_dev_cases).values(
                id=case_id,
                tenant_id=tenant,
                workspace_id=workspace,
                proposal_code=code,
                product_name=name,
                category="chao",
                pic_user_id=pic,
                created_by=pic,
                state="sample_testing",
            )
        )
    return case_id


async def test_a_product_case_is_found_only_in_the_askers_own_workspace(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    """Over RLS: the asker's own code is answered; the same tenant's other
    workspace and another tenant (sharing the asker's workspace id is not
    possible, so its own) read as codes that do not exist."""
    lane = _Lane(sessions)
    tenant, w1 = await _tenant(migrator)
    w2 = await _workspace(migrator, tenant)
    other_tenant, other_ws = await _tenant(migrator)
    asker = await _linked(lane, migrator, tenant, w1, _READER)
    mine, next_door, foreign = (f"SP-{uuid.uuid4().hex[:6]}" for _ in range(3))
    own_id = await _product(migrator, tenant, w1, mine, pic=asker.user)
    await _product(migrator, tenant, w2, next_door, pic=asker.user, name="Bí mật W2")
    await _product(migrator, other_tenant, other_ws, foreign, pic=asker.user, name="Bí mật B")
    for code in (mine, next_door, foreign):
        lane.readings[f"hồ sơ {code} tới đâu?"] = {
            "kind": "open_product_case",
            "proposal_code_mention": code,
        }

    own = await lane.send(asker.chat, f"hồ sơ {mine} tới đâu?")
    assert f"- {mine} — Chảo chống dính 28: Đang test mẫu" in own
    assert f"/supply-chain/product-cases/{own_id}" in own
    for code in (next_door, foreign):
        reply = await lane.send(asker.chat, f"hồ sơ {code} tới đâu?")
        assert reply == f"Không tìm thấy hồ sơ phát triển «{code}»."
        assert "Bí mật" not in reply


async def test_a_named_pic_resolves_through_the_platform_directory(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    lane = _Lane(sessions)
    tenant, ws = await _tenant(migrator)
    asker = await _linked(lane, migrator, tenant, ws, _READER)
    colleague = await _linked(lane, migrator, tenant, ws, ["sc_operator"])
    async with migrator.begin() as conn:
        await conn.execute(
            sa.update(tables.users)
            .where(tables.users.c.id == colleague.user)
            .values(display_name="Nguyễn Thị Lan")
        )
    theirs = f"SP-{uuid.uuid4().hex[:6]}"
    mine = f"SP-{uuid.uuid4().hex[:6]}"
    await _product(migrator, tenant, ws, theirs, pic=colleague.user)
    await _product(migrator, tenant, ws, mine, pic=asker.user)
    lane.readings["hồ sơ của Nguyễn Thị Lan"] = {
        "kind": "list_product_cases",
        "pic_mention": "Nguyễn Thị Lan",
    }

    reply = await lane.send(asker.chat, "hồ sơ của Nguyễn Thị Lan")

    assert theirs in reply and mine not in reply
