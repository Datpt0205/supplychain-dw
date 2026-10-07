"""Integration: view receipts and decision codes, the database half (migration
e399be8c0a2d, channels Z5, ADR 0007).

As `dw_app`, against the catalog's own policies: a person's codes are read
across tenants by `app.principal_id` and by nobody else; with no principal and
no tenant nothing is read; another tenant or workspace reads and writes
nothing; consuming a code is one conditional UPDATE that only its owner's id
passes and that two racing transactions pass once; wrong tries lock at five;
the sweep removes codes a day old and nothing younger; the CHECKs refuse what
the code never writes.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

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
from dw_platform.adapters.persistence.approval_codes import (
    SqlApprovalCodeRetention,
    SqlApprovalCodeStore,
    SqlDecisionCodeLedger,
)
from dw_platform.adapters.persistence.tenant_session import TenantScope, bind_tenant
from dw_platform.application.access_context import AccessContext
from dw_platform.application.approval_codes import (
    CodeState,
    DecisionCodeKey,
    NewDecisionCode,
    ViewReceipt,
)

pytestmark = pytest.mark.integration

_KEY = DecisionCodeKey(b"platform-z5-code-secret-0123456789")


@pytest.fixture
async def migrator(db_urls: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(db_urls.migrator, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def sessions(db_urls: DatabaseUrls) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@dataclass(frozen=True)
class _Issued:
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    approval_id: uuid.UUID
    user_id: uuid.UUID
    code_id: uuid.UUID
    receipt_id: uuid.UUID

    def context(self) -> AccessContext:
        return AccessContext(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            principal_id=self.user_id,
            roles=frozenset(),
            plan_id="professional",
        )


async def _place(migrator: AsyncEngine) -> tuple[uuid.UUID, uuid.UUID]:
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
    return tenant_id, workspace_id


async def _issue(
    migrator: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
    where: tuple[uuid.UUID, uuid.UUID],
    *,
    user_id: uuid.UUID | None = None,
    code: str = "135790",
) -> _Issued:
    """A pending approval, a person, and a code issued to them through the
    store the API uses."""
    user_id = user_id or uuid.uuid4()
    approval_id = uuid.uuid4()
    async with migrator.begin() as conn:
        exists = await conn.scalar(
            sa.select(sa.func.count()).select_from(tables.users).where(tables.users.c.id == user_id)
        )
        if not exists:
            await conn.execute(
                sa.insert(tables.users).values(
                    id=user_id, subject=f"test|{user_id}", display_name="Người duyệt"
                )
            )
        await conn.execute(
            sa.insert(tables.approval_requests).values(
                id=approval_id,
                tenant_id=where[0],
                workspace_id=where[1],
                approval_type="demo.review",
                requested_by=uuid.uuid4(),
                reason="r",
                payload={},
            )
        )
    issued = _Issued(where[0], where[1], approval_id, user_id, uuid.uuid4(), uuid.uuid4())
    await SqlApprovalCodeStore(sessions).record_view(
        issued.context(),
        ViewReceipt(
            id=issued.receipt_id,
            tenant_id=where[0],
            workspace_id=where[1],
            approval_id=approval_id,
            user_id=user_id,
            approval_version=1,
            subject_version="v1",
        ),
        NewDecisionCode(
            id=issued.code_id,
            code_hash=_KEY.digest(approval_id, user_id, code),
            comment="nhận xét",
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        ),
    )
    return issued


async def _consume(
    sessions: async_sessionmaker[AsyncSession], issued: _Issued, user_id: uuid.UUID
) -> bool:
    async with sessions() as session, session.begin():
        await bind_tenant(session, TenantScope(issued.tenant_id, issued.workspace_id, user_id))
        return await SqlDecisionCodeLedger(session).consume(issued.code_id, user_id)


async def _count(
    sessions: async_sessionmaker[AsyncSession], table: str, settings: dict[str, str]
) -> int:
    async with sessions() as session, session.begin():
        for name, value in settings.items():
            await session.execute(
                sa.text("SELECT set_config(:n, :v, true)"), {"n": name, "v": value}
            )
        return int(await session.scalar(sa.text(f"SELECT count(*) FROM platform.{table}")) or 0)


# ---- reading ----------------------------------------------------------------


async def test_a_person_reads_their_own_codes_in_every_tenant_and_nobody_elses(
    migrator: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    alpha, beta = await _place(migrator), await _place(migrator)
    mine_here = await _issue(migrator, sessions, alpha)
    mine_there = await _issue(migrator, sessions, beta, user_id=mine_here.user_id)
    theirs = await _issue(migrator, sessions, alpha)
    store = SqlApprovalCodeStore(sessions)

    mine = await store.codes_of(mine_here.user_id)

    assert {c.id for c in mine} == {mine_here.code_id, mine_there.code_id}
    assert {c.state for c in mine} == {CodeState.OPEN}
    # The store's WHERE names the user; the policy decides on its own too.
    assert (
        await _count(
            sessions,
            "approval_decision_codes",
            {"app.principal_id": str(theirs.user_id)},
        )
        == 1
    )


async def test_without_a_principal_or_a_tenant_nothing_is_read(
    migrator: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _issue(migrator, sessions, await _place(migrator))

    assert await _count(sessions, "approval_decision_codes", {}) == 0
    assert await _count(sessions, "approval_view_receipts", {}) == 0


async def test_another_tenant_or_workspace_reads_no_code_and_no_receipt(
    migrator: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    alpha = await _place(migrator)
    other_workspace = uuid.uuid4()
    async with migrator.begin() as conn:
        await conn.execute(
            sa.insert(tables.workspaces).values(
                id=other_workspace, tenant_id=alpha[0], slug="other", name="Other"
            )
        )
    beta = await _place(migrator)
    issued = await _issue(migrator, sessions, alpha)

    for tenant, workspace in ((beta[0], beta[1]), (alpha[0], other_workspace)):
        scope = {"app.tenant_id": str(tenant), "app.workspace_id": str(workspace)}
        assert await _count(sessions, "approval_decision_codes", scope) == 0
        assert await _count(sessions, "approval_view_receipts", scope) == 0
    own = {"app.tenant_id": str(alpha[0]), "app.workspace_id": str(alpha[1])}
    assert await _count(sessions, "approval_view_receipts", own) >= 1
    # Another tenant's scope cannot consume it either.
    async with sessions() as session, session.begin():
        await bind_tenant(session, TenantScope(beta[0], beta[1], issued.user_id))
        assert not await SqlDecisionCodeLedger(session).consume(issued.code_id, issued.user_id)


# ---- consuming ----------------------------------------------------------------


async def test_only_the_owner_consumes_a_code_and_only_once(
    migrator: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    issued = await _issue(migrator, sessions, await _place(migrator))

    assert not await _consume(sessions, issued, uuid.uuid4())
    assert await _consume(sessions, issued, issued.user_id)
    assert not await _consume(sessions, issued, issued.user_id)


async def test_an_expired_code_is_not_consumed(
    migrator: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    issued = await _issue(migrator, sessions, await _place(migrator))
    async with migrator.begin() as conn:
        await conn.execute(
            sa.text(
                "UPDATE platform.approval_decision_codes"
                " SET created_at = now() - interval '20 minutes',"
                "     expires_at = now() - interval '10 minutes' WHERE id = :c"
            ),
            {"c": issued.code_id},
        )

    assert not await _consume(sessions, issued, issued.user_id)
    (stored,) = await SqlApprovalCodeStore(sessions).codes_of(issued.user_id)
    assert stored.state is CodeState.EXPIRED


async def test_two_transactions_racing_consume_a_code_once(
    migrator: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The first consumes and holds its transaction open; the second, started
    meanwhile, waits on the row and finds it used once the first commits. A
    read then a write would let the second through: it reads the row before
    the first commits."""
    issued = await _issue(migrator, sessions, await _place(migrator))

    async with sessions() as first, first.begin():
        await bind_tenant(first, TenantScope(issued.tenant_id, issued.workspace_id, issued.user_id))
        assert await SqlDecisionCodeLedger(first).consume(issued.code_id, issued.user_id)
        second = asyncio.create_task(_consume(sessions, issued, issued.user_id))
        await asyncio.sleep(0.5)
        assert not second.done(), "the second consume did not wait for the first"

    assert await second is False


# ---- wrong tries --------------------------------------------------------------


async def test_the_fifth_wrong_try_locks_every_open_code_of_that_person_only(
    migrator: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    alpha, beta = await _place(migrator), await _place(migrator)
    here = await _issue(migrator, sessions, alpha)
    there = await _issue(migrator, sessions, beta, user_id=here.user_id, code="246802")
    bystander = await _issue(migrator, sessions, alpha)
    store = SqlApprovalCodeStore(sessions)

    for _ in range(4):
        assert await store.record_wrong_try(here.user_id) == 0
    assert await store.record_wrong_try(here.user_id) == 2

    assert {c.state for c in await store.codes_of(here.user_id)} == {CodeState.LOCKED}
    assert {c.state for c in await store.codes_of(bystander.user_id)} == {CodeState.OPEN}
    assert not await _consume(sessions, there, there.user_id)


async def test_a_new_view_revokes_the_open_code_it_replaces(
    migrator: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    first = await _issue(migrator, sessions, await _place(migrator))
    store = SqlApprovalCodeStore(sessions)
    second_id = uuid.uuid4()
    receipt_id = uuid.uuid4()
    await store.record_view(
        first.context(),
        ViewReceipt(
            id=receipt_id,
            tenant_id=first.tenant_id,
            workspace_id=first.workspace_id,
            approval_id=first.approval_id,
            user_id=first.user_id,
            approval_version=1,
            subject_version="v1",
        ),
        NewDecisionCode(
            id=second_id,
            code_hash=_KEY.digest(first.approval_id, first.user_id, "975310"),
            comment="",
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        ),
    )

    states = {c.id: c.state for c in await store.codes_of(first.user_id)}
    assert states == {first.code_id: CodeState.REISSUED, second_id: CodeState.OPEN}


# ---- housekeeping and shape ---------------------------------------------------


async def test_the_sweep_removes_codes_a_day_old_and_nothing_younger(
    migrator: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    old = await _issue(migrator, sessions, await _place(migrator))
    young = await _issue(migrator, sessions, await _place(migrator))
    async with migrator.begin() as conn:
        await conn.execute(
            sa.text(
                "UPDATE platform.approval_decision_codes"
                " SET created_at = now() - interval '25 hours',"
                "     expires_at = now() - interval '24 hours' WHERE id = :c"
            ),
            {"c": old.code_id},
        )

    await SqlApprovalCodeRetention(sessions).prune()

    async with migrator.connect() as conn:
        left = set(
            (
                await conn.execute(
                    sa.text("SELECT id FROM platform.approval_decision_codes WHERE id IN (:a, :b)"),
                    {"a": old.code_id, "b": young.code_id},
                )
            ).scalars()
        )
    assert left == {young.code_id}


@pytest.mark.parametrize(
    "change",
    [
        "failed_attempts = 5",
        "failed_attempts = 6, revoked_at = now(), revoked_reason = 'locked'",
        "revoked_reason = 'locked'",
        "revoked_at = now(), revoked_reason = 'forgotten'",
        "used_at = now(), revoked_at = now(), revoked_reason = 'reissued'",
        "code_hash = '\\x00'::bytea",
        "expires_at = created_at",
    ],
)
async def test_the_checks_refuse_a_code_the_application_never_writes(
    migrator: AsyncEngine, sessions: async_sessionmaker[AsyncSession], change: str
) -> None:
    issued = await _issue(migrator, sessions, await _place(migrator))
    with pytest.raises(DBAPIError, match="ck_approval_decision_codes"):
        async with migrator.begin() as conn:
            await conn.execute(
                sa.text(f"UPDATE platform.approval_decision_codes SET {change} WHERE id = :c"),
                {"c": issued.code_id},
            )


async def test_a_decision_row_names_a_known_channel_and_old_rows_read_web(
    migrator: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    issued = await _issue(migrator, sessions, await _place(migrator))
    async with migrator.begin() as conn:
        await conn.execute(
            sa.insert(tables.approval_decisions).values(
                id=uuid.uuid4(),
                request_id=issued.approval_id,
                tenant_id=issued.tenant_id,
                workspace_id=issued.workspace_id,
                decided_by=issued.user_id,
                outcome="approved",
                decided_at=datetime.now(UTC),
            )
        )
        assert (
            await conn.scalar(
                sa.text("SELECT channel FROM platform.approval_decisions WHERE request_id = :a"),
                {"a": issued.approval_id},
            )
            == "web"
        )
    with pytest.raises(DBAPIError, match="ck_approval_decisions_channel"):
        async with migrator.begin() as conn:
            await conn.execute(
                sa.text(
                    "UPDATE platform.approval_decisions SET channel = 'sms' WHERE request_id = :a"
                ),
                {"a": issued.approval_id},
            )


async def test_a_receipt_names_only_an_approval_of_its_own_workspace(
    migrator: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    alpha, beta = await _place(migrator), await _place(migrator)
    issued = await _issue(migrator, sessions, alpha)
    with pytest.raises(DBAPIError, match="fk_approval_view_receipts_tenant_id_approval_requests"):
        async with migrator.begin() as conn:
            await conn.execute(
                sa.insert(tables.approval_view_receipts).values(
                    id=uuid.uuid4(),
                    tenant_id=beta[0],
                    workspace_id=beta[1],
                    approval_id=issued.approval_id,
                    user_id=issued.user_id,
                    approval_version=1,
                )
            )
