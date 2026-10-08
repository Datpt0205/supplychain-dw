"""Integration: the in-app inbox (migration 855ae928c3fa).

One person's notifications are theirs alone: not a colleague's in the same
workspace, not another tenant's. A sender may deliver again without
notifying anyone twice. A link cannot leave the app. The application may
mark a notification read and nothing else, and old ones are pruned by the
database's own window, never a caller's.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from pg_harness import DatabaseUrls
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dw_kernel.errors import NotFoundError
from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.identity_provisioning import SqlIdentityBootstrap
from dw_platform.adapters.persistence.notifications import (
    SqlNotificationRepository,
    SqlNotificationRetention,
)
from dw_platform.application.access_context import AccessContext
from dw_platform.application.notifications import NotificationService
from dw_platform.testing.seed_env import seed_test_env

pytestmark = pytest.mark.integration

ALPHA = uuid.UUID("d6b43d0e-c3c6-5dbc-bc08-150621bd9a5d")
ALPHA_WS = uuid.UUID("64764894-718d-5558-ba17-9a2949214063")
BETA = uuid.UUID("6634f09a-d1d7-54a6-aa23-f3f018f41f28")
BETA_WS = uuid.UUID("eda6af16-a0c4-55d3-be0b-163414390572")
LINK = "/approvals/5c0ffee0-0000-4000-8000-000000000001"


@dataclass(frozen=True)
class _Identity:
    subject: str
    email: str | None
    issuer: str = "https://issuer.test/realms/dw"
    name: str | None = None
    auth_methods: frozenset[str] = frozenset()
    acr: str | None = None


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


async def _member(engine: AsyncEngine, tenant: uuid.UUID = ALPHA) -> AccessContext:
    workspace = ALPHA_WS if tenant == ALPHA else BETA_WS
    view = await SqlIdentityBootstrap(
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        default_tenant_id=tenant,
        default_workspace_id=workspace,
        # A member of the workspace: delivery skips anyone who is not.
        auto_provision_default_membership=True,
    ).bootstrap(
        _Identity(subject=f"sub-{uuid.uuid4()}", email=f"{uuid.uuid4().hex[:8]}@inbox.test")
    )
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=view.principal_id,
        roles=frozenset({"member"}),
        scopes=frozenset(),
        plan_id="professional",
    )


def _repo(engine: AsyncEngine) -> SqlNotificationRepository:
    return SqlNotificationRepository(async_sessionmaker(engine, expire_on_commit=False))


async def _deliver(
    engine: AsyncEngine,
    sender: AccessContext,
    *recipients: uuid.UUID,
    key: str,
    link: str | None = LINK,
) -> None:
    await _repo(engine).deliver(
        sender,
        recipients=recipients,
        source_key=key,
        title="Nhắc NCC cập nhật: PO-2026-007",
        body="Kangaroo im lặng 2 ngày.",
        link=link,
    )


async def test_a_notification_reaches_its_recipients_and_nobody_else(
    app_engine: AsyncEngine,
) -> None:
    an, binh, chi = [await _member(app_engine) for _ in range(3)]
    key = f"test:{uuid.uuid4()}"
    await _deliver(app_engine, an, an.principal_id, binh.principal_id, key=key)
    service = NotificationService(_repo(app_engine))

    for member in (an, binh):
        inbox = await service.latest(member)
        assert [n.link for n in inbox.items] == [LINK]
        assert inbox.unread == 1
    # A colleague in the same workspace who was not addressed sees nothing.
    assert (await service.latest(chi)).items == ()


async def test_delivering_again_notifies_nobody_twice(app_engine: AsyncEngine) -> None:
    an = await _member(app_engine)
    key = f"test:{uuid.uuid4()}"
    for _ in range(3):
        await _deliver(app_engine, an, an.principal_id, an.principal_id, key=key)
    assert (await NotificationService(_repo(app_engine)).latest(an)).unread == 1


async def test_a_notification_that_is_someone_elses_reads_as_never_existing(
    app_engine: AsyncEngine,
) -> None:
    an, binh = await _member(app_engine), await _member(app_engine)
    beta = await _member(app_engine, BETA)
    await _deliver(app_engine, an, an.principal_id, key=f"test:{uuid.uuid4()}")
    service = NotificationService(_repo(app_engine))
    (mine,) = (await service.latest(an)).items

    for stranger in (binh, beta):
        with pytest.raises(NotFoundError):
            await service.mark_read(stranger, mine.id)
        await service.mark_all_read(stranger)
        assert (await service.latest(stranger)).items == ()
    assert (await service.latest(an)).unread == 1

    await service.mark_read(an, mine.id)
    assert (await service.latest(an)).unread == 0


async def test_an_unscoped_connection_reads_no_inbox(app_engine: AsyncEngine) -> None:
    an = await _member(app_engine)
    await _deliver(app_engine, an, an.principal_id, key=f"test:{uuid.uuid4()}")
    async with app_engine.begin() as conn:
        # The tenant alone, no principal: the recipient policy lets nothing through.
        await conn.execute(
            sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(ALPHA)}
        )
        assert await conn.scalar(sa.text("SELECT count(*) FROM platform.notifications")) == 0


@pytest.mark.parametrize(
    "link", ["javascript:alert(1)", "//evil.example/x", "https://evil.example", "approvals/x"]
)
async def test_a_link_cannot_leave_the_app(app_engine: AsyncEngine, link: str) -> None:
    an = await _member(app_engine)
    with pytest.raises(IntegrityError, match="ck_notifications_link"):
        await _deliver(app_engine, an, an.principal_id, key=f"test:{uuid.uuid4()}", link=link)


async def test_the_application_may_mark_read_and_nothing_else(app_engine: AsyncEngine) -> None:
    an = await _member(app_engine)
    await _deliver(app_engine, an, an.principal_id, key=f"test:{uuid.uuid4()}")
    for statement in (
        "UPDATE platform.notifications SET title = 'rewritten'",
        "UPDATE platform.notifications SET link = '/elsewhere'",
        "DELETE FROM platform.notifications",
    ):
        with pytest.raises(ProgrammingError, match="permission denied"):
            async with app_engine.begin() as conn:
                await conn.execute(
                    sa.text(
                        "SELECT set_config('app.tenant_id', :t, true),"
                        " set_config('app.user_id', :u, true)"
                    ),
                    {"t": str(ALPHA), "u": str(an.principal_id)},
                )
                await conn.execute(sa.text(statement))


async def test_pruning_removes_what_is_older_than_ninety_days_only(
    app_engine: AsyncEngine, migrator_engine: AsyncEngine
) -> None:
    an = await _member(app_engine)
    old, fresh = f"test:{uuid.uuid4()}", f"test:{uuid.uuid4()}"
    await _deliver(app_engine, an, an.principal_id, key=old)
    await _deliver(app_engine, an, an.principal_id, key=fresh)
    async with migrator_engine.begin() as conn:
        await conn.execute(
            sa.update(tables.notifications)
            .where(tables.notifications.c.source_key == old)
            .values(created_at=sa.text("now() - interval '91 days'"))
        )

    await SqlNotificationRetention(async_sessionmaker(app_engine)).prune()

    async with migrator_engine.connect() as conn:
        left = set(
            (
                await conn.execute(
                    sa.select(tables.notifications.c.source_key).where(
                        tables.notifications.c.source_key.in_([old, fresh])
                    )
                )
            ).scalars()
        )
    assert left == {fresh}


def _system(tenant: uuid.UUID = ALPHA, workspace: uuid.UUID = ALPHA_WS) -> AccessContext:
    """How a worker sends: a principal that is nobody's inbox."""
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset(),
        scopes=frozenset(),
        plan_id="professional",
    )


async def test_a_worker_delivers_to_people_other_than_itself(app_engine: AsyncEngine) -> None:
    """The case that broke first: an INSERT ... ON CONFLICT is also held to
    the SELECT policy, so a direct insert could only address the sender."""
    an, binh = await _member(app_engine), await _member(app_engine)
    await _deliver(
        app_engine, _system(), an.principal_id, binh.principal_id, key=f"test:{uuid.uuid4()}"
    )
    service = NotificationService(_repo(app_engine))
    assert [(await service.latest(m)).unread for m in (an, binh)] == [1, 1]


async def test_a_non_member_is_skipped_not_notified(
    app_engine: AsyncEngine, migrator_engine: AsyncEngine
) -> None:
    """Only members of the workspace are addressed: someone from another
    tenant gets no row at all, not merely one they cannot see."""
    an = await _member(app_engine)
    beta = await _member(app_engine, BETA)
    key = f"test:{uuid.uuid4()}"
    await _deliver(app_engine, _system(), an.principal_id, beta.principal_id, key=key)

    async with migrator_engine.connect() as conn:
        addressed = (
            (
                await conn.execute(
                    sa.select(tables.notifications.c.recipient_user_id).where(
                        tables.notifications.c.source_key == key
                    )
                )
            )
            .scalars()
            .all()
        )
    assert addressed == [an.principal_id]


async def test_delivery_stays_inside_the_bound_tenant(app_engine: AsyncEngine) -> None:
    """The tenant is the bound one, never a parameter: naming another
    tenant's workspace is refused, and nothing lands there."""
    beta = await _member(app_engine, BETA)
    with pytest.raises(ProgrammingError, match="inside the bound tenant"):
        await _deliver(
            app_engine, _system(ALPHA, BETA_WS), beta.principal_id, key=f"test:{uuid.uuid4()}"
        )
    assert (await NotificationService(_repo(app_engine)).latest(beta)).items == ()


async def test_the_application_cannot_insert_around_the_door(app_engine: AsyncEngine) -> None:
    an = await _member(app_engine)
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with app_engine.begin() as conn:
            await conn.execute(
                sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(ALPHA)}
            )
            await conn.execute(
                sa.insert(tables.notifications).values(
                    id=uuid.uuid4(),
                    tenant_id=ALPHA,
                    workspace_id=ALPHA_WS,
                    recipient_user_id=an.principal_id,
                    source_key="direct",
                    title="x",
                )
            )
