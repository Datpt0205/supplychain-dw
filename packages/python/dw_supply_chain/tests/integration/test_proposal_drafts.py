"""Integration: chat proposal drafts on the real database (zalo-channel ticket 04).

What only Postgres can prove: `proposal_drafts` is narrowed by tenant AND
workspace on read and write; creating a case consumes its draft in the same
transaction, only at the summarised, unexpired version, so two real
transactions carrying the same "Đồng ý" create exactly one case; a refused
code leaves the draft where it was; the retention sweep deletes expired drafts
of every tenant and nothing else; a removed membership takes its draft with it;
`updated_at` yields to a stated value; and `dw_app` cannot move a draft to
another person, workspace or channel.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls

from dw_kernel.errors import ConflictError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import SystemClock
from dw_platform.adapters.persistence import tables as platform_tables
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence import tables
from dw_supply_chain.adapters.persistence.product_case_repository import SqlProductCaseRepository
from dw_supply_chain.adapters.persistence.proposal_draft_repository import (
    SqlProposalDraftRepository,
    SqlProposalDraftRetention,
)
from dw_supply_chain.domain.product_development_case import (
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
)
from dw_supply_chain.domain.product_proposal import (
    DRAFT_TTL,
    ProposalDraft,
    ProposalDraftChangedError,
    ProposalField,
)

pytestmark = pytest.mark.integration

FIELDS = {
    ProposalField.PROPOSAL_CODE: "CH-28",
    ProposalField.PRODUCT_NAME: "chảo chống dính 28cm",
    ProposalField.CATEGORY: "Chảo",
}


@dataclass(frozen=True)
class _Db:
    sessions: async_sessionmaker[AsyncSession]
    migrator: AsyncEngine

    @property
    def drafts(self) -> SqlProposalDraftRepository:
        return SqlProposalDraftRepository(self.sessions)

    @property
    def cases(self) -> SqlProductCaseRepository:
        return SqlProductCaseRepository(self.sessions)


@pytest.fixture
async def db(db_urls: DatabaseUrls) -> AsyncIterator[_Db]:
    app = create_async_engine(db_urls.app, poolclass=NullPool)
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    yield _Db(async_sessionmaker(app, expire_on_commit=False), migrator)
    await app.dispose()
    await migrator.dispose()


async def _member(
    db: _Db, *, tenant: uuid.UUID | None = None, user: uuid.UUID | None = None
) -> AccessContext:
    """A person with a membership in a new workspace (of a new tenant unless
    named): the FK a draft hangs on."""
    user = user or uuid.uuid4()
    workspace = uuid.uuid4()
    async with db.migrator.begin() as conn:
        if tenant is None:
            tenant = uuid.uuid4()
            await conn.execute(
                sa.insert(platform_tables.tenants).values(
                    id=tenant, slug=f"t-{tenant.hex[:8]}", name="T"
                )
            )
        exists = await conn.scalar(
            sa.select(platform_tables.users.c.id).where(platform_tables.users.c.id == user)
        )
        if exists is None:
            await conn.execute(
                sa.insert(platform_tables.users).values(
                    id=user, subject=f"test|{user}", display_name="Người thử"
                )
            )
        await conn.execute(
            sa.insert(platform_tables.workspaces).values(
                id=workspace, tenant_id=tenant, slug=f"w-{workspace.hex[:8]}", name="W"
            )
        )
        await conn.execute(
            sa.insert(platform_tables.memberships).values(
                id=uuid.uuid4(), tenant_id=tenant, workspace_id=workspace, user_id=user
            )
        )
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=user,
        roles=frozenset(),
        scopes=frozenset(),
        plan_id="professional",
    )


async def _draft(
    db: _Db,
    context: AccessContext,
    *,
    version: int = 1,
    summarized: int | None = 1,
    expires_at: datetime | None = None,
) -> ProposalDraft:
    return await db.drafts.put(
        context,
        "zalo",
        fields=FIELDS,
        draft_version=version,
        summarized_version=summarized,
        expires_at=expires_at or datetime.now(UTC) + DRAFT_TTL,
        previous_version=None,
    )


def _case(context: AccessContext, code: str) -> ProductDevelopmentCase:
    return ProductDevelopmentCase.propose(
        id=ProductDevelopmentCaseId(uuid.uuid4()),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        proposal_code=code,
        product_name="chảo chống dính 28cm",
        category="Chảo",
        actor_id=context.principal_id,
    )


def _audit(context: AccessContext, case: ProductDevelopmentCase) -> AuditEvent:
    return AuditEvent(
        id=uuid.uuid4(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        actor_id=UserId(context.principal_id),
        action="supply_chain.product_case.propose",
        resource_type="product_dev_case",
        resource_id=str(case.id),
        occurred_at=SystemClock().now(),
    )


async def _add(db: _Db, context: AccessContext, draft: ProposalDraft, code: str) -> None:
    case = _case(context, code)
    await db.cases.add(context, case, audit=_audit(context, case), consume=draft.claim())


async def _cases_with(db: _Db, *codes: str) -> int:
    async with db.migrator.connect() as conn:
        found = await conn.scalar(
            sa.select(sa.func.count())
            .select_from(tables.product_dev_cases)
            .where(tables.product_dev_cases.c.proposal_code.in_(codes))
        )
    return int(found or 0)


async def _drafts_left(db: _Db, *ids: uuid.UUID) -> int:
    async with db.migrator.connect() as conn:
        found = await conn.scalar(
            sa.select(sa.func.count())
            .select_from(tables.proposal_drafts)
            .where(tables.proposal_drafts.c.id.in_(ids))
        )
    return int(found or 0)


def _code() -> str:
    return f"CH-{uuid.uuid4().hex[:8]}"


# ---- isolation --------------------------------------------------------------------


async def test_another_tenant_neither_reads_nor_changes_nor_deletes_a_draft(db: _Db) -> None:
    mine = await _member(db)
    draft = await _draft(db, mine)
    # The same person in another tenant, and someone else in the same workspace.
    elsewhere = await _member(db, user=mine.principal_id)
    neighbour = mine.model_copy(update={"principal_id": uuid.uuid4()})
    other_workspace = await _member(db, tenant=mine.tenant_id, user=mine.principal_id)

    for stranger in (elsewhere, neighbour, other_workspace):
        assert await db.drafts.open_draft(stranger, "zalo") is None
        with pytest.raises(ProposalDraftChangedError):
            await db.drafts.put(
                stranger,
                "zalo",
                fields={},
                draft_version=2,
                summarized_version=None,
                expires_at=datetime.now(UTC) + DRAFT_TTL,
                previous_version=1,
            )
        assert await db.drafts.discard(stranger, "zalo") is False

    # Below the repository: a transaction bound to the other tenant sees no row.
    async with tenant_session(db.sessions, TenantScope.from_access_context(elsewhere)) as session:
        seen = await session.scalar(
            sa.select(sa.func.count())
            .select_from(tables.proposal_drafts)
            .where(tables.proposal_drafts.c.id == draft.id)
        )
    assert seen == 0
    found = await db.drafts.open_draft(mine, "zalo")
    assert found is not None and found.fields == FIELDS and found.draft_version == 1


async def test_a_case_for_another_tenant_cannot_consume_this_draft(db: _Db) -> None:
    mine = await _member(db)
    draft = await _draft(db, mine)
    other = await _member(db)
    code = _code()

    with pytest.raises(ProposalDraftChangedError):
        await _add(db, other, draft, code)

    assert await _cases_with(db, code) == 0
    assert await _drafts_left(db, draft.id) == 1


# ---- consumed once, at the summarised version ----------------------------------------


async def test_two_real_transactions_with_the_same_dong_y_create_one_case(db: _Db) -> None:
    context = await _member(db)
    draft = await _draft(db, context)
    first, second = _code(), _code()

    outcomes = await asyncio.gather(
        _add(db, context, draft, first), _add(db, context, draft, second), return_exceptions=True
    )

    refused = [o for o in outcomes if isinstance(o, ProposalDraftChangedError)]
    assert len(refused) == 1, outcomes
    assert await _cases_with(db, first, second) == 1
    assert await _drafts_left(db, draft.id) == 0


async def test_a_version_nobody_was_shown_is_not_consumed(db: _Db) -> None:
    context = await _member(db)
    draft = await _draft(db, context, version=2, summarized=1)
    code = _code()

    with pytest.raises(ProposalDraftChangedError):
        await _add(db, context, draft, code)

    assert await _cases_with(db, code) == 0
    assert await _drafts_left(db, draft.id) == 1


async def test_an_expired_draft_is_not_consumed(db: _Db) -> None:
    context = await _member(db)
    draft = await _draft(db, context, expires_at=datetime.now(UTC) - timedelta(seconds=1))
    code = _code()

    with pytest.raises(ProposalDraftChangedError):
        await _add(db, context, draft, code)

    assert await _cases_with(db, code) == 0


async def test_a_taken_code_leaves_the_draft_where_it_was(db: _Db) -> None:
    context = await _member(db)
    taken = _code()
    first = _case(context, taken)
    await db.cases.add(context, first, audit=_audit(context, first))
    draft = await _draft(db, context)

    with pytest.raises(ConflictError) as raised:
        await _add(db, context, draft, taken)

    assert not isinstance(raised.value, ProposalDraftChangedError)
    assert await _drafts_left(db, draft.id) == 1


async def test_a_put_at_a_stale_version_is_refused(db: _Db) -> None:
    context = await _member(db)
    await _draft(db, context)
    with pytest.raises(ProposalDraftChangedError):
        await _draft(db, context)  # a second "first" draft for the same key
    with pytest.raises(ProposalDraftChangedError):
        await db.drafts.put(
            context,
            "zalo",
            fields=FIELDS,
            draft_version=3,
            summarized_version=None,
            expires_at=datetime.now(UTC) + DRAFT_TTL,
            previous_version=2,
        )


# ---- lifecycle ---------------------------------------------------------------------


async def test_the_sweep_deletes_expired_drafts_of_every_tenant_and_nothing_else(db: _Db) -> None:
    past = datetime.now(UTC) - timedelta(minutes=1)
    expired = [await _draft(db, await _member(db), expires_at=past) for _ in range(2)]
    live = await _draft(db, await _member(db))

    await SqlProposalDraftRetention(session_factory=db.sessions).prune()

    assert await _drafts_left(db, *(d.id for d in expired)) == 0
    assert await _drafts_left(db, live.id) == 1


async def test_the_sweep_without_its_drain_setting_deletes_nothing(db: _Db) -> None:
    """The drain policy is what lets the sweep see across tenants; a plain
    `dw_app` transaction deleting by expiry sees no row at all."""
    draft = await _draft(db, await _member(db), expires_at=datetime.now(UTC) - timedelta(minutes=1))
    async with db.sessions() as session, session.begin():
        await session.execute(
            sa.delete(tables.proposal_drafts).where(
                tables.proposal_drafts.c.expires_at <= sa.func.now()
            )
        )
    assert await _drafts_left(db, draft.id) == 1


async def test_a_removed_membership_takes_its_draft(db: _Db) -> None:
    context = await _member(db)
    draft = await _draft(db, context)
    async with db.migrator.begin() as conn:
        await conn.execute(
            sa.delete(platform_tables.memberships).where(
                platform_tables.memberships.c.workspace_id == context.workspace_id,
                platform_tables.memberships.c.user_id == context.principal_id,
            )
        )
    assert await _drafts_left(db, draft.id) == 0


async def test_updated_at_yields_to_a_stated_value(db: _Db) -> None:
    draft = await _draft(db, await _member(db))
    stated = datetime(2026, 1, 1, tzinfo=UTC)
    async with db.migrator.begin() as conn:
        await conn.execute(
            sa.update(tables.proposal_drafts)
            .where(tables.proposal_drafts.c.id == draft.id)
            .values(draft_version=2, updated_at=stated)
        )
        kept = await conn.scalar(
            sa.select(tables.proposal_drafts.c.updated_at).where(
                tables.proposal_drafts.c.id == draft.id
            )
        )
        await conn.execute(
            sa.update(tables.proposal_drafts)
            .where(tables.proposal_drafts.c.id == draft.id)
            .values(draft_version=3)
        )
        touched = await conn.scalar(
            sa.select(tables.proposal_drafts.c.updated_at).where(
                tables.proposal_drafts.c.id == draft.id
            )
        )
    assert kept == stated
    assert touched is not None and touched > stated


@pytest.mark.parametrize("column", ["user_id", "tenant_id", "workspace_id", "channel"])
async def test_the_application_cannot_move_a_draft(db: _Db, column: str) -> None:
    context = await _member(db)
    await _draft(db, context)
    value: object = "zalo" if column == "channel" else uuid.uuid4()
    with pytest.raises(DBAPIError, match="permission denied"):
        async with tenant_session(db.sessions, TenantScope.from_access_context(context)) as session:
            await session.execute(
                sa.update(tables.proposal_drafts)
                .where(tables.proposal_drafts.c.user_id == context.principal_id)
                .values({column: value})
            )
