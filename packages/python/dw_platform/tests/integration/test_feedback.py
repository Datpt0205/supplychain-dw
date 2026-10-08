"""Integration: member feedback, under RLS.

Anyone in a workspace may submit; the inbox read is tenant-confined by RLS. The
isolation test is the point: a row written for another tenant is invisible to
this tenant's inbox even though both live in one ``platform.feedback`` table.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from pg_harness import DatabaseUrls
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dw_kernel.pagination import PageQuery, PageRequest
from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.identity_provisioning import SqlIdentityBootstrap
from dw_platform.adapters.persistence.uow import SqlPlatformUnitOfWorkFactory
from dw_platform.application.access_context import AccessContext
from dw_platform.testing.seed_env import seed_test_env

pytestmark = pytest.mark.integration

# Seeded tenant-alpha / its workspace (uuid5, matches dw_platform.testing.seed_env).
ALPHA = uuid.UUID("d6b43d0e-c3c6-5dbc-bc08-150621bd9a5d")
ALPHA_WS = uuid.UUID("64764894-718d-5558-ba17-9a2949214063")


@pytest.fixture
async def app_engine(db_urls: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    await seed_test_env(db_urls.migrator)
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def migrator_engine(db_urls: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(db_urls.migrator, poolclass=NullPool)
    yield engine
    await engine.dispose()


@dataclass(frozen=True)
class _Identity:
    subject: str
    email: str | None
    issuer: str = "https://issuer.test/realms/dw"
    name: str | None = None
    auth_methods: frozenset[str] = frozenset()
    acr: str | None = None


async def _sign_in(engine: AsyncEngine, email: str, name: str) -> uuid.UUID:
    bootstrap = SqlIdentityBootstrap(
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        default_tenant_id=ALPHA,
        default_workspace_id=ALPHA_WS,
    )
    view = await bootstrap.bootstrap(
        _Identity(subject=f"sub-{uuid.uuid4()}", email=email, name=name)
    )
    return view.principal_id


def _member(principal: uuid.UUID, tenant: uuid.UUID = ALPHA) -> AccessContext:
    """A plain member — submitting feedback needs no special scope."""
    return AccessContext(
        tenant_id=tenant,
        workspace_id=ALPHA_WS,
        principal_id=principal,
        roles=frozenset({"member"}),
        scopes=frozenset({"crm.account.read"}),
        plan_id="professional",
    )


def _factory(engine: AsyncEngine) -> SqlPlatformUnitOfWorkFactory:
    return SqlPlatformUnitOfWorkFactory(async_sessionmaker(engine, expire_on_commit=False))


async def test_member_submits_and_admin_reads_it_back(app_engine: AsyncEngine) -> None:
    author = await _sign_in(app_engine, "voice@fpt.com", "Voice Of Customer")
    factory = _factory(app_engine)

    async with factory(_member(author)) as uow:
        await uow.feedback.add(
            feedback_id=uuid.uuid4(),
            tenant_id=ALPHA,
            workspace_id=ALPHA_WS,
            author_id=author,
            category="idea",
            message="Add a dark mode.",
        )
        await uow.commit()

    async with factory(_member(author)) as uow:
        items = (
            await uow.feedback.list_page(
                PageRequest(limit=50, after=None, query=PageQuery(key="test.feedback"))
            )
        ).items

    assert len(items) == 1
    assert items[0].category == "idea"
    assert items[0].message == "Add a dark mode."
    assert items[0].author_name == "Voice Of Customer"


async def test_feedback_does_not_cross_tenants(
    app_engine: AsyncEngine, migrator_engine: AsyncEngine
) -> None:
    author = await _sign_in(app_engine, "mine@fpt.com", "My Tenant")
    other = await _sign_in(app_engine, "theirs@fpt.com", "Other Tenant")
    factory = _factory(app_engine)

    async with factory(_member(author)) as uow:
        await uow.feedback.add(
            feedback_id=uuid.uuid4(),
            tenant_id=ALPHA,
            workspace_id=ALPHA_WS,
            author_id=author,
            category="bug",
            message="Mine.",
        )
        await uow.commit()

    # A row belonging to a different tenant, written past RLS via the migrator.
    other_tenant = uuid.uuid4()
    async with async_sessionmaker(migrator_engine, expire_on_commit=False)() as session:
        await session.execute(
            sa.insert(tables.feedback).values(
                id=uuid.uuid4(),
                tenant_id=other_tenant,
                workspace_id=uuid.uuid4(),
                author_id=other,
                category="bug",
                message="Theirs.",
            )
        )
        await session.commit()

    async with factory(_member(author)) as uow:
        items = (
            await uow.feedback.list_page(
                PageRequest(limit=50, after=None, query=PageQuery(key="test.feedback"))
            )
        ).items

    messages = [i.message for i in items]
    # What this test is about is the tenant boundary, not the row count: the
    # sibling test above writes to the SAME tenant and the database is recreated
    # once per session, so pinning the whole list asserts test order instead of
    # isolation. "Theirs." must never appear; "Mine." always must.
    assert "Mine." in messages
    assert "Theirs." not in messages
    assert all(i.author_name != "Other Tenant" for i in items)
