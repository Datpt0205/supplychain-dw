"""Integration: a decision sent from a chat after a view on the portal.

ADR 0007 with its 2026-10-06 amendment, channels Z5. Real Postgres
as `dw_app`: the view service the API route calls, the decision service the
worker's chat command calls, `ApproveAndResumeService.decide` between them, and
the real membership lookup the chat context is built from. Every refusal leaves
the approval pending, writes no decision, leaves the code as it was (unless it
was already used), writes one refusal audit (when a code of the sender's was
matched, so there is a tenant to write it under) and answers its own sentence.

Fresh tenants, people and approvals per test, so no test reads another's rows.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from runtime_harness import RuntimeUrls
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from dw_agent_runtime.adapters.checkpoint import SqlAlchemyCheckpointSaver
from dw_agent_runtime.adapters.langgraph_runner import LangGraphWorkflowRunner
from dw_agent_runtime.adapters.run_store import RunStatus, SqlWorkerRunStore
from dw_agent_runtime.approval_codes import ApprovalViewService, CodeUnavailable
from dw_agent_runtime.approval_flow import CHANNEL_DECIDED_ACTION, ApproveAndResumeService
from dw_agent_runtime.autonomy import AutonomyApprovalPolicy
from dw_agent_runtime.channel_decisions import (
    REFUSED_ACTION,
    ChannelApprovalDecisionService,
    ChannelDecisionOutcome,
    ParsedDecision,
    Refusal,
)
from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.model.budget import RunBudgetLedger
from dw_agent_runtime.registry import GraphRegistry, WorkerRegistry
from dw_agent_runtime.testing.demo_graph import DEMO_WORKER_YAML, build_demo_graph
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.approval_codes import SqlApprovalCodeStore
from dw_platform.adapters.persistence.channel_preferences import SqlChannelPreferences
from dw_platform.adapters.persistence.membership_lookup import SqlMembershipLookup
from dw_platform.adapters.persistence.uow import SqlPlatformUnitOfWorkFactory
from dw_platform.adapters.persistence.zalo_link_repo import SqlZaloLink
from dw_platform.application.access_context import AccessContext
from dw_platform.application.approval_codes import (
    MAX_WRONG_TRIES,
    ApprovalSubjectVersions,
    DecisionCodeKey,
    NewDecisionCode,
    ViewReceipt,
)
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.channel_access import LinkedUserAccess
from dw_platform.domain.approval import ApprovalRequest

pytestmark = pytest.mark.integration

_SECRET = b"z5-integration-secret-0123456789"
BOARD = "demo.approve.board"
_ROLE = "z5_demo_board"
STRICT = "demo.strict.review"
LOOSE = "demo.loose.review"
WEB_ONLY = "other.review"


@dataclass
class _Versions:
    """The subject-version port, answering from a dict a test can change."""

    current: dict[uuid.UUID, str] = field(default_factory=dict)

    async def version_of(self, context: AccessContext, request: ApprovalRequest) -> str | None:
        return self.current.get(request.id)


class _NoRuns:
    """These approvals have no run; a resume would be a bug."""

    def hosts(self, **_: Any) -> bool:
        return False

    async def resume(self, **_: Any) -> None:
        raise AssertionError("a run-less approval resumed something")


@dataclass
class _Person:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    chat_id: str

    def web(self, *scopes: str) -> AccessContext:
        """The signed-in session's context, as the API builds it."""
        return AccessContext(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            principal_id=self.user_id,
            roles=frozenset({"approver"}),
            scopes=frozenset({"approvals.read", "approvals.decide", BOARD, *scopes}),
            plan_id="professional",
        )


class World:
    def __init__(self, urls: RuntimeUrls, runner: Any = None) -> None:
        self.migrator = create_async_engine(urls.migrator, poolclass=NullPool)
        self.app = create_async_engine(urls.app, poolclass=NullPool)
        self.sessions = async_sessionmaker(self.app, class_=AsyncSession, expire_on_commit=False)
        self.versions = _Versions()
        self.subjects = ApprovalSubjectVersions()
        self.subjects.register("demo.", self.versions)
        ids, clock = Uuid4Generator(), SystemClock()
        self.uow_factory = SqlPlatformUnitOfWorkFactory(self.sessions)
        self.run_store = SqlWorkerRunStore(self.sessions, stale_run_after_seconds=3600)
        self.flow = ApproveAndResumeService(
            uow_factory=self.uow_factory,
            runner=runner or _NoRuns(),  # type: ignore[arg-type]
            run_store=self.run_store,
            clock=clock,
            id_generator=ids,
            strict_approval_prefixes=frozenset({"demo.strict."}),
        )
        self.store = SqlApprovalCodeStore(self.sessions)
        key = DecisionCodeKey(_SECRET)
        self.key = key
        self.views = ApprovalViewService(
            uow_factory=self.uow_factory,
            approval_flow=self.flow,
            store=self.store,
            subjects=self.subjects,
            chats=SqlZaloLink(self.sessions),
            key=key,
            clock=clock,
            ids=ids,
        )
        self.service = ChannelApprovalDecisionService(
            approval_flow=self.flow,
            authorization=ScopeAuthorizationService(),
            store=self.store,
            subjects=self.subjects,
            access=LinkedUserAccess(
                preferences=SqlChannelPreferences(self.sessions),
                lookup=SqlMembershipLookup(self.sessions),
            ),
            key=key,
            clock=clock,
            ids=ids,
        )

    async def dispose(self) -> None:
        await self.migrator.dispose()
        await self.app.dispose()

    # ---- setup, as the migrator ------------------------------------------

    async def tenant(self) -> tuple[uuid.UUID, uuid.UUID]:
        tenant_id, workspace_id = uuid.uuid4(), uuid.uuid4()
        async with self.migrator.begin() as conn:
            await conn.execute(
                sa.text(
                    "INSERT INTO platform.roles (key, name, scopes)"
                    " VALUES (:k, 'Z5 board', CAST(:s AS jsonb)) ON CONFLICT (key) DO NOTHING"
                ),
                {"k": _ROLE, "s": f'["{BOARD}"]'},
            )
            await conn.execute(
                sa.insert(tables.tenants).values(
                    id=tenant_id, slug=f"t-{tenant_id.hex[:8]}", name="T"
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

    async def person(
        self,
        where: tuple[uuid.UUID, uuid.UUID],
        *,
        roles: tuple[str, ...] = ("approver", _ROLE),
        linked: bool = True,
    ) -> _Person:
        user_id = uuid.uuid4()
        chat_id = f"chat-{user_id.hex[:12]}"
        async with self.migrator.begin() as conn:
            await conn.execute(
                sa.insert(tables.users).values(
                    id=user_id, subject=f"test|{user_id}", display_name="Người duyệt"
                )
            )
            await conn.execute(
                sa.insert(tables.memberships).values(
                    id=uuid.uuid4(),
                    tenant_id=where[0],
                    workspace_id=where[1],
                    user_id=user_id,
                    role_keys=list(roles),
                )
            )
            if linked:
                await conn.execute(
                    sa.insert(tables.external_identities).values(
                        id=uuid.uuid4(),
                        user_id=user_id,
                        issuer="zalo",
                        subject=chat_id,
                        provider="zalo",
                    )
                )
        return _Person(user_id, where[0], where[1], chat_id)

    async def approval(
        self,
        where: tuple[uuid.UUID, uuid.UUID],
        *,
        approval_type: str = STRICT,
        requested_by: uuid.UUID | None = None,
        required_scope: str | None = BOARD,
        version: str = "v1",
    ) -> uuid.UUID:
        approval_id = uuid.uuid4()
        async with self.migrator.begin() as conn:
            await conn.execute(
                sa.insert(tables.approval_requests).values(
                    id=approval_id,
                    tenant_id=where[0],
                    workspace_id=where[1],
                    approval_type=approval_type,
                    requested_by=requested_by or uuid.uuid4(),
                    reason="Duyệt thử",
                    payload={},
                    required_scope=required_scope,
                )
            )
        self.versions.current[approval_id] = version
        return approval_id

    async def scalar(self, sql: str, **params: Any) -> Any:
        async with self.migrator.connect() as conn:
            return await conn.scalar(sa.text(sql), params)

    async def execute(self, sql: str, **params: Any) -> None:
        async with self.migrator.begin() as conn:
            await conn.execute(sa.text(sql), params)

    # ---- the two halves ----------------------------------------------------

    async def view(
        self, person: _Person, approval_id: uuid.UUID, comment: str = "Đã xem hồ sơ, đồng ý."
    ) -> str:
        outcome = await self.views.view(
            approval_id=approval_id,
            context=person.web(),
            authorization=ScopeAuthorizationService(),
            comment=comment,
            issue_code=True,
        )
        assert outcome.unavailable is None, outcome.unavailable
        assert outcome.issued is not None
        return outcome.issued.code

    async def send(
        self,
        person: _Person,
        code: str,
        *,
        approve: bool = True,
        reason: str = "",
        message_id: str | None = None,
    ) -> ChannelDecisionOutcome:
        return await self.service.decide(
            user_id=person.user_id,
            channel="zalo",
            message_id=message_id or uuid.uuid4().hex,
            chat_id=person.chat_id,
            decision=ParsedDecision(approve=approve, code=code, reason=reason),
        )

    # ---- what is in the database -------------------------------------------

    async def status(self, approval_id: uuid.UUID) -> str:
        return str(
            await self.scalar(
                "SELECT status FROM platform.approval_requests WHERE id = :a", a=approval_id
            )
        )

    async def decisions(self, approval_id: uuid.UUID) -> list[tuple[str, str, str]]:
        async with self.migrator.connect() as conn:
            rows = await conn.execute(
                sa.text(
                    "SELECT outcome, channel, comment FROM platform.approval_decisions"
                    " WHERE request_id = :a"
                ),
                {"a": approval_id},
            )
            return [(r.outcome, r.channel, r.comment) for r in rows]

    async def code_rows(self, approval_id: uuid.UUID) -> list[dict[str, Any]]:
        async with self.migrator.connect() as conn:
            rows = await conn.execute(
                sa.text(
                    "SELECT id, used_at, revoked_reason, failed_attempts, code_hash"
                    " FROM platform.approval_decision_codes WHERE approval_id = :a"
                    " ORDER BY created_at"
                ),
                {"a": approval_id},
            )
            return [dict(r._mapping) for r in rows]

    async def audits(self, approval_id: uuid.UUID, action: str) -> list[dict[str, Any]]:
        async with self.migrator.connect() as conn:
            rows = await conn.execute(
                sa.text(
                    "SELECT details FROM platform.audit_events"
                    " WHERE resource_id = :a AND action = :act ORDER BY occurred_at"
                ),
                {"a": str(approval_id), "act": action},
            )
            return [dict(r.details) for r in rows]


@pytest.fixture
async def world(urls: RuntimeUrls) -> AsyncIterator[World]:
    w = World(urls)
    yield w
    await w.dispose()


async def _refused_cleanly(
    world: World,
    approval_id: uuid.UUID,
    outcome: ChannelDecisionOutcome,
    reason: Refusal,
    *,
    code_used: bool = False,
    audited: bool = True,
) -> None:
    """What every refusal leaves: nothing decided, the code as it was, one audit."""
    assert outcome.decided is False
    assert outcome.reason is reason, outcome
    assert await world.status(approval_id) == "pending"
    assert await world.decisions(approval_id) == []
    rows = await world.code_rows(approval_id)
    if rows:
        assert (rows[-1]["used_at"] is not None) is code_used
    refusals = await world.audits(approval_id, REFUSED_ACTION)
    if audited:
        assert [r["reason"] for r in refusals] == [reason.value]
    else:
        assert refusals == []


# ---- the way through ----------------------------------------------------------


async def test_a_view_then_duyet_decides_once_from_zalo_with_its_audit(world: World) -> None:
    where = await world.tenant()
    bod = await world.person(where)
    approval_id = await world.approval(where)

    code = await world.view(bod, approval_id, comment="Mẫu đạt, đồng ý phát triển.")
    message_id = uuid.uuid4().hex
    outcome = await world.send(bod, code, message_id=message_id)

    assert outcome.decided is True
    assert outcome.reply == "Đã ghi quyết định DUYỆT. Kết quả xem trên cổng."
    assert await world.status(approval_id) == "approved"
    assert await world.decisions(approval_id) == [
        ("approved", "zalo", "Mẫu đạt, đồng ý phát triển.")
    ]
    (row,) = await world.code_rows(approval_id)
    assert row["used_at"] is not None
    (audit,) = await world.audits(approval_id, CHANNEL_DECIDED_ACTION)
    assert audit["channel"] == "zalo"
    assert audit["outcome"] == "approved"
    assert audit["approval_version"] == 1
    assert audit["subject_version"] == "v1"
    assert audit["code_id"] == str(row["id"])
    assert audit["message_id"] == message_id
    assert {"receipt_id", "decision_id", "chat_reference"} <= set(audit)
    assert bod.chat_id not in str(audit)


async def test_khong_records_the_portal_comment_then_the_reason(world: World) -> None:
    where = await world.tenant()
    bod = await world.person(where)
    approval_id = await world.approval(where)
    code = await world.view(bod, approval_id, comment="Đã xem mẫu.")

    outcome = await world.send(bod, code, approve=False, reason="Màu chưa đúng mẫu chuẩn")

    assert outcome.decided is True
    assert await world.status(approval_id) == "rejected"
    assert await world.decisions(approval_id) == [
        ("rejected", "zalo", "Đã xem mẫu.\nMàu chưa đúng mẫu chuẩn")
    ]


async def test_the_code_never_leaves_the_response(world: World) -> None:
    """Only its HMAC is stored; the digits are in no decision, audit or
    notification row."""
    where = await world.tenant()
    bod = await world.person(where)
    approval_id = await world.approval(where)
    code = await world.view(bod, approval_id)
    await world.send(bod, code)

    (row,) = await world.code_rows(approval_id)
    assert bytes(row["code_hash"]) == world.key.digest(approval_id, bod.user_id, code)
    assert code.encode() not in bytes(row["code_hash"])
    for table, column in (
        ("audit_events", "details::text"),
        ("approval_decisions", "comment"),
        ("notifications", "title || coalesce(body, '') || coalesce(link, '')"),
        ("channel_deliveries", "title || coalesce(link, '')"),
        ("approval_decision_codes", "comment"),
    ):
        found = await world.scalar(
            f"SELECT count(*) FROM platform.{table} WHERE {column} LIKE :c",
            c=f"%{code}%",
        )
        assert found == 0, table


# ---- the code -------------------------------------------------------------


async def test_another_persons_code_gets_the_same_sentence_as_a_typo(world: World) -> None:
    where = await world.tenant()
    owner = await world.person(where)
    other = await world.person(where)
    approval_id = await world.approval(where)
    code = await world.view(owner, approval_id)

    theirs = await world.send(other, code)
    typo = await world.send(other, "000000" if code != "000000" else "111111")

    assert theirs.reason is Refusal.WRONG_CODE
    assert theirs.reply == typo.reply == "Mã không đúng hoặc đã hết hạn."
    # No code of the sender's matched: nothing to write a tenant's audit under.
    await _refused_cleanly(world, approval_id, theirs, Refusal.WRONG_CODE, audited=False)
    # The owner's code still works: another person's wrong tries are not theirs.
    assert (await world.code_rows(approval_id))[0]["failed_attempts"] == 0
    assert (await world.send(owner, code)).decided is True


async def test_a_code_from_another_tenant_is_a_wrong_code(world: World) -> None:
    alpha = await world.tenant()
    beta = await world.tenant()
    owner = await world.person(alpha)
    stranger = await world.person(beta)
    approval_id = await world.approval(alpha)
    code = await world.view(owner, approval_id)

    outcome = await world.send(stranger, code)

    await _refused_cleanly(world, approval_id, outcome, Refusal.WRONG_CODE, audited=False)


async def test_a_code_decides_its_own_approval_and_no_other(world: World) -> None:
    where = await world.tenant()
    bod = await world.person(where)
    first = await world.approval(where)
    second = await world.approval(where)
    first_code = await world.view(bod, first)
    second_code = await world.view(bod, second)
    assert first_code != second_code

    assert (await world.send(bod, first_code)).decided is True

    assert await world.status(first) == "approved"
    assert await world.status(second) == "pending"
    (row,) = await world.code_rows(second)
    assert row["used_at"] is None and row["revoked_reason"] is None


async def test_without_a_view_there_is_no_code_to_type(world: World) -> None:
    """A decider who never opened the approval has no code: a guess is a
    wrong code, and nothing is decided."""
    where = await world.tenant()
    bod = await world.person(where)
    approval_id = await world.approval(where)

    outcome = await world.send(bod, "123456")

    await _refused_cleanly(world, approval_id, outcome, Refusal.WRONG_CODE, audited=False)


async def test_an_expired_code_is_refused_and_says_so(world: World) -> None:
    where = await world.tenant()
    bod = await world.person(where)
    approval_id = await world.approval(where)
    code = await world.view(bod, approval_id)
    await world.execute(
        "UPDATE platform.approval_decision_codes SET created_at = now() - interval '11 minutes',"
        " expires_at = now() - interval '1 minute' WHERE approval_id = :a",
        a=approval_id,
    )

    outcome = await world.send(bod, code)

    await _refused_cleanly(world, approval_id, outcome, Refusal.EXPIRED)
    assert outcome.reply == "Mã đã hết hạn. Mở lại yêu cầu trên cổng để lấy mã mới."


async def test_a_used_code_does_not_decide_twice(world: World) -> None:
    where = await world.tenant()
    bod = await world.person(where)
    approval_id = await world.approval(where)
    code = await world.view(bod, approval_id)
    assert (await world.send(bod, code)).decided is True

    again = await world.send(bod, code)

    assert again.reason is Refusal.USED
    assert len(await world.decisions(approval_id)) == 1
    assert [r["reason"] for r in await world.audits(approval_id, REFUSED_ACTION)] == ["code_used"]


async def test_opening_again_replaces_the_code(world: World) -> None:
    where = await world.tenant()
    bod = await world.person(where)
    approval_id = await world.approval(where)
    old = await world.view(bod, approval_id)
    new = await world.view(bod, approval_id)

    outcome = await world.send(bod, old)

    if old == new:  # one in a million: the digits repeat across a re-issue
        pytest.skip("the re-issued code drew the same digits")
    await _refused_cleanly(world, approval_id, outcome, Refusal.REISSUED)
    assert (await world.send(bod, new)).decided is True


async def test_the_fifth_wrong_try_locks_the_code(world: World) -> None:
    where = await world.tenant()
    bod = await world.person(where)
    approval_id = await world.approval(where)
    code = await world.view(bod, approval_id)
    wrong = "000000" if code != "000000" else "111111"

    for _ in range(MAX_WRONG_TRIES - 1):
        assert (await world.send(bod, wrong)).reason is Refusal.WRONG_CODE
    (row,) = await world.code_rows(approval_id)
    assert row["failed_attempts"] == MAX_WRONG_TRIES - 1
    assert row["revoked_reason"] is None

    assert (await world.send(bod, wrong)).reason is Refusal.WRONG_CODE
    (row,) = await world.code_rows(approval_id)
    assert row["revoked_reason"] == "locked"

    sixth = await world.send(bod, code)

    await _refused_cleanly(world, approval_id, sixth, Refusal.LOCKED)


# ---- the approval and its subject ------------------------------------------


async def test_an_approval_changed_since_the_view_is_refused(world: World) -> None:
    where = await world.tenant()
    bod = await world.person(where)
    approval_id = await world.approval(where)
    code = await world.view(bod, approval_id)
    await world.execute(
        "UPDATE platform.approval_requests SET version = version + 1 WHERE id = :a",
        a=approval_id,
    )

    outcome = await world.send(bod, code)

    await _refused_cleanly(world, approval_id, outcome, Refusal.STALE)
    assert "mở lại liên kết" in outcome.reply.lower()


async def test_a_subject_changed_since_the_view_is_refused(world: World) -> None:
    where = await world.tenant()
    bod = await world.person(where)
    approval_id = await world.approval(where)
    code = await world.view(bod, approval_id)
    world.versions.current[approval_id] = "v2"

    outcome = await world.send(bod, code)

    await _refused_cleanly(world, approval_id, outcome, Refusal.STALE)


async def test_a_decision_made_on_the_web_meanwhile_is_named(world: World) -> None:
    where = await world.tenant()
    bod = await world.person(where)
    approval_id = await world.approval(where)
    code = await world.view(bod, approval_id)
    await world.flow.decide(
        approval_id=approval_id,
        approve=True,
        comment="Trên web.",
        context=bod.web(),
        authorization=ScopeAuthorizationService(),
    )

    outcome = await world.send(bod, code)

    assert outcome.reason is Refusal.ALREADY_DECIDED
    assert "trên cổng web" in outcome.reply
    assert await world.decisions(approval_id) == [("approved", "web", "Trên web.")]
    (row,) = await world.code_rows(approval_id)
    assert row["used_at"] is None


# ---- who ------------------------------------------------------------------


async def test_a_scope_removed_after_the_view_is_refused_as_on_the_web(world: World) -> None:
    where = await world.tenant()
    bod = await world.person(where)
    approval_id = await world.approval(where)
    code = await world.view(bod, approval_id)
    await world.execute(
        "UPDATE platform.memberships SET role_keys = CAST(:r AS jsonb) WHERE user_id = :u",
        r='["approver"]',
        u=bod.user_id,
    )

    outcome = await world.send(bod, code)

    await _refused_cleanly(world, approval_id, outcome, Refusal.NO_PERMISSION)


async def test_the_requester_is_refused_on_a_type_the_web_would_let_them_decide(
    world: World,
) -> None:
    """Not strict, so the web lets a requester approve; the chat never does.
    The portal issues no code to the requester; one put in the table anyway
    is refused by the service."""
    where = await world.tenant()
    requester = await world.person(where)
    approval_id = await world.approval(where, approval_type=LOOSE, requested_by=requester.user_id)

    portal = await world.views.view(
        approval_id=approval_id,
        context=requester.web(),
        authorization=ScopeAuthorizationService(),
        issue_code=True,
    )
    assert portal.unavailable is CodeUnavailable.REQUESTER
    assert portal.issued is None

    code = "424242"
    await world.store.record_view(
        requester.web(),
        ViewReceipt(
            id=uuid.uuid4(),
            tenant_id=where[0],
            workspace_id=where[1],
            approval_id=approval_id,
            user_id=requester.user_id,
            approval_version=1,
            subject_version="v1",
        ),
        NewDecisionCode(
            id=uuid.uuid4(),
            code_hash=world.key.digest(approval_id, requester.user_id, code),
            comment="",
            expires_at=SystemClock().now() + timedelta(minutes=10),
        ),
    )

    outcome = await world.send(requester, code)

    await _refused_cleanly(world, approval_id, outcome, Refusal.REQUESTER)


async def test_a_strict_type_without_a_comment_issues_no_code_and_decides_nothing(
    world: World,
) -> None:
    where = await world.tenant()
    bod = await world.person(where)
    approval_id = await world.approval(where)

    portal = await world.views.view(
        approval_id=approval_id,
        context=bod.web(),
        authorization=ScopeAuthorizationService(),
        comment="   ",
        issue_code=True,
    )
    assert portal.unavailable is CodeUnavailable.COMMENT_REQUIRED
    assert portal.issued is None
    assert await world.code_rows(approval_id) == []


# ---- what the portal offers ------------------------------------------------


async def test_the_portal_says_why_it_offers_no_code(world: World) -> None:
    where = await world.tenant()
    bod = await world.person(where)
    unlinked = await world.person(where, linked=False)
    plain = await world.person(where, roles=("approver",))
    web_only = await world.approval(where, approval_type=WEB_ONLY)
    approval_id = await world.approval(where)

    async def reason(person: _Person, target: uuid.UUID, *scopes: str) -> CodeUnavailable | None:
        context = (
            person.web()
            if not scopes
            else person.web().model_copy(update={"scopes": frozenset(scopes)})
        )
        outcome = await world.views.view(
            approval_id=target,
            context=context,
            authorization=ScopeAuthorizationService(),
            comment="x",
            issue_code=True,
        )
        return outcome.unavailable

    assert await reason(bod, web_only) is CodeUnavailable.WEB_ONLY
    assert await reason(unlinked, approval_id) is CodeUnavailable.NOT_LINKED
    # Holds `approvals.decide` without the stamp: the approval is not even
    # served to them (ADR 0004 amendment), as on the web.
    with pytest.raises(Exception, match="not found"):
        await reason(plain, approval_id, "approvals.read", "approvals.decide")
    # Every open is a receipt, whatever was offered.
    receipts = await world.scalar(
        "SELECT count(*) FROM platform.approval_view_receipts WHERE approval_id IN (:a, :b)",
        a=web_only,
        b=approval_id,
    )
    assert receipts == 2


# ---- twice at once --------------------------------------------------------


async def test_the_same_command_twice_at_once_decides_once(world: World) -> None:
    """Two deliveries with different message ids, racing: one decision. The
    code is consumed by one conditional UPDATE, so the second waits on the
    row and finds it used."""
    where = await world.tenant()
    bod = await world.person(where)
    approval_id = await world.approval(where)
    code = await world.view(bod, approval_id)

    first, second = await asyncio.gather(world.send(bod, code), world.send(bod, code))

    assert sorted([first.decided, second.decided]) == [False, True]
    loser = first if not first.decided else second
    assert loser.reason in {Refusal.USED, Refusal.ALREADY_DECIDED}
    assert len(await world.decisions(approval_id)) == 1
    assert len(await world.audits(approval_id, CHANNEL_DECIDED_ACTION)) == 1


# ---- a run resumes ----------------------------------------------------------


@dataclass
class _Spy:
    inner: LangGraphWorkflowRunner
    channels: list[str] = field(default_factory=list)

    def hosts(self, **kwargs: Any) -> bool:
        return self.inner.hosts(**kwargs)

    async def resume(self, *, run_context: RunContext, **kwargs: Any) -> Any:
        self.channels.append(run_context.channel)
        return await self.inner.resume(run_context=run_context, **kwargs)


async def test_a_decision_from_zalo_resumes_the_run_with_channel_zalo(
    urls: RuntimeUrls, tmp_path: Path
) -> None:
    config = tmp_path / "demo_approval.yaml"
    config.write_text(DEMO_WORKER_YAML, encoding="utf-8")
    probe = World(urls)
    where = await probe.tenant()
    requester = await probe.person(where, roles=("member",))
    bod = await probe.person(where)
    await probe.dispose()

    engine: AsyncEngine = create_async_engine(urls.app, poolclass=NullPool)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    graphs = GraphRegistry()
    graphs.register("demo_approval", "1.0.0", build_demo_graph)
    workers = WorkerRegistry(graph_registry=graphs)
    workers.load_file(config)
    runner = LangGraphWorkflowRunner(
        worker_registry=workers,
        graph_registry=graphs,
        checkpoint_saver=SqlAlchemyCheckpointSaver(sessions),
        run_store=SqlWorkerRunStore(sessions, stale_run_after_seconds=3600),
        uow_factory=SqlPlatformUnitOfWorkFactory(sessions),
        clock=SystemClock(),
        id_generator=Uuid4Generator(),
        allowance=_Unmetered(),
        budget=RunBudgetLedger(),
        approval_policy=AutonomyApprovalPolicy(),
    )
    spy = _Spy(runner)
    world = World(urls, runner=spy)
    try:
        run = RunContext(
            run_id=uuid.uuid4(),
            tenant_id=where[0],
            workspace_id=where[1],
            actor_id=requester.user_id,
            worker_id="demo_approval",
            worker_version="1.0.0",
            channel="web",
            plan_id="professional",
            roles=frozenset({"member"}),
            scopes=frozenset({"demo.write", "demo.read"}),
            trace_id="z5-resume",
            autonomy_ceiling="A4",
            autonomy_level="A4",
        )
        await runner.start(run_context=run, input_payload={"subject": "mẫu"})
        record = await world.run_store.get(run, run.run_id)
        assert record.status is RunStatus.WAITING_APPROVAL
        approval_id = record.approval_request_id
        assert approval_id is not None
        world.versions.current[approval_id] = "v1"

        code = await world.view(bod, approval_id)
        outcome = await world.send(bod, code)

        assert outcome.decided is True
        assert spy.channels == ["zalo"]
        assert (await world.run_store.get(run, run.run_id)).status is RunStatus.COMPLETED
        assert await world.decisions(approval_id) == [("approved", "zalo", "Đã xem hồ sơ, đồng ý.")]
    finally:
        await world.dispose()
        await engine.dispose()


class _Unmetered:
    def runs_per_day(self, plan_id: str) -> int | None:
        return None

    def spend_usd_per_day(self, plan_id: str) -> Any:
        return None
