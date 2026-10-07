"""A decision on Zalo after a view on the portal (zalo-channel ticket 05) end to
end through the poll lane, against real Postgres as ``dw_app``.

Built from the worker's own wiring: ``build_zalo_inbound`` over the registry
``build_channel_commands`` fills, the decide command first
(``build_channel_decision_command``), the proposal command after it on the
mock model. The portal half is the view service the API route calls. No call
reaches the real Zalo API: the bot is a recording fake. What it shows that the
service-level suite cannot:

* the decide command answers before the proposal command, and a message that
  merely starts with "duyệt" never reaches the model;
* a case approval issued on the portal is decided by ``DUYỆT <mã>`` from the
  linked chat, through the router's claim, once — the same update delivered
  again is skipped by its message id;
* ``KHÔNG`` with a number but no reason gets the grammar back; ordinary
  sentences that start with "không" go on to the next command;
* without ``DW_APPROVAL_CODE_SECRET`` a decision is answered "not enabled".
"""

from __future__ import annotations

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

from dw_agent_runtime.adapters.mock_model import MockModelAdapter
from dw_agent_runtime.approval_codes import ApprovalViewService
from dw_agent_runtime.approval_flow import ApproveAndResumeService
from dw_agent_runtime.channel_decisions import APPROVE_HINT, REJECT_HINT
from dw_agent_runtime.model.prompts import RenderedPrompt
from dw_connectors.adapters.zalo_link import ZaloLinking
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_observability.telemetry import NullTelemetry
from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.approval_codes import SqlApprovalCodeStore
from dw_platform.adapters.persistence.uow import SqlPlatformUnitOfWorkFactory
from dw_platform.adapters.persistence.zalo_link_repo import SqlZaloLink
from dw_platform.application.access_context import AccessContext
from dw_platform.application.approval_codes import ApprovalSubjectVersions, DecisionCodeKey
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.entitlement import DEFAULT_PLANS, PlanEntitlementService
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence.product_case_repository import (
    SqlProductCaseRepository,
)
from dw_supply_chain.application.approval_subject import ProductCaseApprovalSubject
from dw_supply_chain.domain.product_development_case import (
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
)
from dw_supply_chain.presentation.zalo_proposal import no_permission
from dw_supply_chain.workflows.advance_product_case_graph import (
    APPROVAL_TYPE_PREFIX,
    BOD_REVIEW_CASE_KEY,
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

_LINK_SECRET = "decision-db-link-secret"
_CODE_SECRET = "decision-db-code-secret-0123456789"
_PROMPT = "supply_chain.product_proposal_understanding"
_BOD = "supply_chain.approve.bod"
_SIGNOFF = f"{APPROVAL_TYPE_PREFIX}signoff"


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
class _Lane:
    sessions: async_sessionmaker[AsyncSession]
    code_secret: str = _CODE_SECRET
    bot: _Bot = field(default_factory=_Bot)
    model_calls: list[str] = field(default_factory=list)
    inbound: Any = field(init=False)

    def answer(self, prompt: RenderedPrompt) -> dict[str, object]:
        self.model_calls.append(prompt.user)
        return {"kind": "unsupported"}

    def __post_init__(self) -> None:
        settings = WorkerSettings(
            zalo_link_secret=_LINK_SECRET,  # type: ignore[arg-type]
            approval_code_secret=self.code_secret,  # type: ignore[arg-type]
            product_name="Cổng thử",
            public_web_url="https://portal.example",
            model_provider="mock",
        )
        clock = SystemClock()
        stack = build_model_stack_for(
            settings, self.sessions, clock=clock, telemetry=NullTelemetry()
        )
        model = stack.gateway.adapters["mock"]
        assert isinstance(model, MockModelAdapter)
        model.register_builder(_PROMPT, "1.0.0", self.answer)
        commands = build_channel_commands(
            settings,
            self.sessions,
            gateway=build_one_call_gateway(
                stack,
                self.sessions,
                allowance=PlanEntitlementService(DEFAULT_PLANS),
                clock=clock,
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
        before = len(self.bot.sent)
        self.bot.updates.append(_update(chat, text, message_id or uuid.uuid4().hex))
        await build_zalo_poll_consumer(self.bot, self.inbound)()
        return self.bot.sent[-1][1] if len(self.bot.sent) > before else ""


def _update(chat: str, text: str, message_id: str) -> dict[str, Any]:
    return {
        "result": {
            "message": {"chat": {"id": chat}, "text": text, "message_id": message_id},
            "event_name": "message.text.received",
        }
    }


@dataclass(frozen=True)
class _Person:
    user: uuid.UUID
    tenant: uuid.UUID
    workspace: uuid.UUID
    chat: str

    def web(self) -> AccessContext:
        return AccessContext(
            tenant_id=self.tenant,
            workspace_id=self.workspace,
            principal_id=self.user,
            roles=frozenset({"approver", "sc_bod"}),
            scopes=frozenset({"approvals.read", "approvals.decide", _BOD}),
            plan_id="professional",
        )


async def _bod(lane: _Lane, migrator: AsyncEngine) -> _Person:
    user, tenant, workspace = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with migrator.begin() as conn:
        await conn.execute(
            sa.insert(tables.users).values(id=user, subject=f"test|{user}", display_name="BGĐ")
        )
        await conn.execute(
            sa.insert(tables.tenants).values(id=tenant, slug=f"t-{tenant.hex[:8]}", name="T")
        )
        await conn.execute(
            sa.insert(tables.workspaces).values(
                id=workspace, tenant_id=tenant, slug="main", name="Cung ứng HN"
            )
        )
        await conn.execute(
            sa.insert(tables.entitlements).values(
                id=uuid.uuid4(), tenant_id=tenant, plan_id="professional"
            )
        )
        await conn.execute(
            sa.insert(tables.memberships).values(
                id=uuid.uuid4(),
                tenant_id=tenant,
                workspace_id=workspace,
                user_id=user,
                role_keys=["approver", "sc_bod"],
            )
        )
    chat = f"zalo-{uuid.uuid4().hex[:12]}"
    offer = await ZaloLinking(
        store=SqlZaloLink(lane.sessions), link_secret=_LINK_SECRET, clock=SystemClock()
    ).connect(user)
    await lane.send(chat, f"/start {offer.code}")
    return _Person(user, tenant, workspace, chat)


async def _sign_off(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine, bod: _Person
) -> uuid.UUID:
    """A product case and a run-less step-9 sign-off on it, asked by someone else."""
    operator = AccessContext(
        tenant_id=bod.tenant,
        workspace_id=bod.workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset(),
        scopes=frozenset(),
        plan_id="professional",
    )
    case = ProductDevelopmentCase.propose(
        id=ProductDevelopmentCaseId(uuid.uuid4()),
        tenant_id=TenantId(bod.tenant),
        workspace_id=WorkspaceId(bod.workspace),
        proposal_code=f"DX-{uuid.uuid4().hex[:6]}",
        product_name="Chảo chống dính 28cm",
        category="Chảo",
        actor_id=operator.principal_id,
    )
    await SqlProductCaseRepository(sessions).add(
        operator,
        case,
        audit=AuditEvent(
            id=uuid.uuid4(),
            tenant_id=TenantId(bod.tenant),
            workspace_id=WorkspaceId(bod.workspace),
            actor_id=UserId(operator.principal_id),
            action="supply_chain.product_case.propose",
            resource_type="product_dev_case",
            resource_id=str(case.id),
            occurred_at=SystemClock().now(),
        ),
    )
    approval_id = uuid.uuid4()
    async with migrator.begin() as conn:
        await conn.execute(
            sa.insert(tables.approval_requests).values(
                id=approval_id,
                tenant_id=bod.tenant,
                workspace_id=bod.workspace,
                approval_type=_SIGNOFF,
                requested_by=operator.principal_id,
                reason="Ký duyệt hồ sơ",
                payload={BOD_REVIEW_CASE_KEY: str(case.id)},
                required_scope=_BOD,
            )
        )
    return approval_id


async def _view(
    sessions: async_sessionmaker[AsyncSession], bod: _Person, approval_id: uuid.UUID
) -> str:
    """The portal half, as the API composes it."""
    uow_factory = SqlPlatformUnitOfWorkFactory(sessions)
    subjects = ApprovalSubjectVersions()
    subjects.register(
        APPROVAL_TYPE_PREFIX, ProductCaseApprovalSubject(SqlProductCaseRepository(sessions))
    )
    views = ApprovalViewService(
        uow_factory=uow_factory,
        approval_flow=ApproveAndResumeService(
            uow_factory=uow_factory,
            runner=None,  # type: ignore[arg-type]
            run_store=None,  # type: ignore[arg-type]
            clock=SystemClock(),
            id_generator=Uuid4Generator(),
            strict_approval_prefixes=frozenset({APPROVAL_TYPE_PREFIX}),
        ),
        store=SqlApprovalCodeStore(sessions),
        subjects=subjects,
        chats=SqlZaloLink(sessions),
        key=DecisionCodeKey(_CODE_SECRET.encode()),
        clock=SystemClock(),
        ids=Uuid4Generator(),
    )
    outcome = await views.view(
        approval_id=approval_id,
        context=bod.web(),
        authorization=ScopeAuthorizationService(),
        comment="Hồ sơ đủ, đồng ý ký.",
        issue_code=True,
    )
    assert outcome.issued is not None, outcome.unavailable
    return outcome.issued.code


async def _status(migrator: AsyncEngine, approval_id: uuid.UUID) -> tuple[str, int]:
    async with migrator.connect() as conn:
        status = await conn.scalar(
            sa.text("SELECT status FROM platform.approval_requests WHERE id = :a"),
            {"a": approval_id},
        )
        decisions = await conn.scalar(
            sa.text(
                "SELECT count(*) FROM platform.approval_decisions"
                " WHERE request_id = :a AND channel = 'zalo'"
            ),
            {"a": approval_id},
        )
    return str(status), int(decisions or 0)


async def test_duyet_from_the_linked_chat_decides_a_viewed_sign_off_once(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    lane = _Lane(sessions)
    bod = await _bod(lane, migrator)
    approval_id = await _sign_off(sessions, migrator, bod)
    code = await _view(sessions, bod, approval_id)
    message_id = uuid.uuid4().hex

    reply = await lane.send(bod.chat, f"Duyệt {code}", message_id)

    assert reply == "Đã ghi quyết định DUYỆT. Kết quả xem trên cổng."
    assert await _status(migrator, approval_id) == ("approved", 1)
    # The same update again: the router's claim skips it, nothing is said.
    assert await lane.send(bod.chat, f"Duyệt {code}", message_id) == ""
    assert await _status(migrator, approval_id) == ("approved", 1)
    assert lane.model_calls == []
    # The code is in no message the bot sent.
    assert all(code not in text for _, text in lane.bot.sent)


async def test_words_that_look_like_a_decision_never_reach_the_model(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    lane = _Lane(sessions)
    bod = await _bod(lane, migrator)
    approval_id = await _sign_off(sessions, migrator, bod)

    assert await lane.send(bod.chat, "duyệt hết") == APPROVE_HINT
    assert await lane.send(bod.chat, "DUYỆT") == APPROVE_HINT
    assert await lane.send(bod.chat, "DUYỆT 1234") == APPROVE_HINT
    assert await lane.send(bod.chat, "KHÔNG 123456") == REJECT_HINT
    assert lane.model_calls == []
    assert await _status(migrator, approval_id) == ("pending", 0)
    # An ordinary sentence starting with "không" is not a decision: the next
    # command, the proposal, reads it (and refuses: BGĐ may not propose).
    assert await lane.send(bod.chat, "không biết mã đề xuất là gì") == no_permission("Cung ứng HN")


async def test_without_a_code_key_a_decision_is_answered_not_enabled(
    sessions: async_sessionmaker[AsyncSession], migrator: AsyncEngine
) -> None:
    lane = _Lane(sessions, code_secret="")
    bod = await _bod(lane, migrator)
    approval_id = await _sign_off(sessions, migrator, bod)

    reply = await lane.send(bod.chat, "DUYỆT 123456")

    assert reply == "Quyết qua Zalo chưa bật ở hệ thống này; anh/chị quyết trên cổng."
    assert await _status(migrator, approval_id) == ("pending", 0)
    assert lane.model_calls == []
