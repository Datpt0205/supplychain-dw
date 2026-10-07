"""Integration: the channel outbox's database half (migration 5a25154e0296).

Rows enter by one door, `platform.deliver_notification`, and only for a
recipient the notification was inserted for who holds a chat link; a repeat of
the `source_key` queues nothing. The table is workspace-narrowed: another
tenant, and another workspace of the same tenant, neither read nor change a
row. `dw_app` cannot insert or delete one, nor touch anything but its delivery
state. Pruning removes finished rows past 90 days, never a pending one.
Offboarding exports the tenant's rows and leaves none behind.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from pg_harness import DatabaseUrls
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.channel_deliveries import (
    DeliveryScope,
    SqlChannelDeliveryRetention,
    SqlChannelOutbox,
)
from dw_platform.adapters.persistence.notifications import SqlNotificationRepository
from dw_platform.adapters.persistence.offboarding import SqlTenantOffboarding
from dw_platform.application.access_context import AccessContext

pytestmark = pytest.mark.integration

_d = tables.channel_deliveries


@pytest.fixture
async def migrator(db_urls: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(db_urls.migrator, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def app(db_urls: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield engine
    await engine.dispose()


@dataclass(frozen=True)
class _Place:
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID

    def sender(self) -> AccessContext:
        """How a worker sends: a principal that is nobody's inbox."""
        return AccessContext(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            principal_id=uuid.uuid4(),
            roles=frozenset(),
            scopes=frozenset(),
            plan_id="professional",
        )


async def _tenant(migrator: AsyncEngine, workspaces: int = 1) -> list[_Place]:
    tenant_id = uuid.uuid4()
    places = [_Place(tenant_id, uuid.uuid4()) for _ in range(workspaces)]
    async with migrator.begin() as conn:
        await conn.execute(
            sa.insert(tables.tenants).values(id=tenant_id, slug=f"t-{tenant_id.hex[:8]}", name="T")
        )
        for i, place in enumerate(places):
            await conn.execute(
                sa.insert(tables.workspaces).values(
                    id=place.workspace_id, tenant_id=tenant_id, slug=f"w{i}", name=f"W{i}"
                )
            )
    return places


async def _person(migrator: AsyncEngine, *places: _Place, zalo: str | None = None) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with migrator.begin() as conn:
        await conn.execute(
            sa.insert(tables.users).values(id=user_id, subject=f"test|{user_id}", display_name="N")
        )
        for place in places:
            await conn.execute(
                sa.insert(tables.memberships).values(
                    id=uuid.uuid4(),
                    tenant_id=place.tenant_id,
                    workspace_id=place.workspace_id,
                    user_id=user_id,
                    role_keys=["member"],
                )
            )
        if zalo is not None:
            await conn.execute(
                sa.insert(tables.external_identities).values(
                    id=uuid.uuid4(), user_id=user_id, issuer="zalo", subject=zalo, provider="zalo"
                )
            )
    return user_id


async def _notify(app: AsyncEngine, place: _Place, *recipients: uuid.UUID, key: str) -> None:
    await SqlNotificationRepository(async_sessionmaker(app, expire_on_commit=False)).deliver(
        place.sender(),
        recipients=recipients,
        source_key=key,
        title="PO-2026-007 chờ bạn duyệt",
        body="Thân thông báo không ra kênh.",
        link="/approvals/x",
    )


async def _rows(migrator: AsyncEngine, key: str) -> list[sa.Row[tuple[uuid.UUID, str, str]]]:
    async with migrator.connect() as conn:
        return list(
            (
                await conn.execute(
                    sa.select(_d.c.recipient_user_id, _d.c.status, _d.c.title).where(
                        _d.c.source_key == key
                    )
                )
            ).all()
        )


# ---- the one door -----------------------------------------------------------------


async def test_a_linked_recipient_is_queued_and_an_unlinked_one_is_not(
    app: AsyncEngine, migrator: AsyncEngine
) -> None:
    (place,) = await _tenant(migrator)
    linked = await _person(migrator, place, zalo=f"z-{uuid.uuid4()}")
    unlinked = await _person(migrator, place)
    key = f"test:{uuid.uuid4()}"

    await _notify(app, place, linked, unlinked, key=key)

    assert [(r.recipient_user_id, r.status) for r in await _rows(migrator, key)] == [
        (linked, "pending")
    ]


async def test_notifying_twice_queues_one_delivery(app: AsyncEngine, migrator: AsyncEngine) -> None:
    (place,) = await _tenant(migrator)
    linked = await _person(migrator, place, zalo=f"z-{uuid.uuid4()}")
    key = f"test:{uuid.uuid4()}"
    for _ in range(3):
        await _notify(app, place, linked, linked, key=key)

    assert len(await _rows(migrator, key)) == 1
    async with migrator.connect() as conn:
        inbox = await conn.scalar(
            sa.select(sa.func.count())
            .select_from(tables.notifications)
            .where(tables.notifications.c.source_key == key)
        )
    assert inbox == 1


async def test_a_linked_non_member_gets_no_delivery(
    app: AsyncEngine, migrator: AsyncEngine
) -> None:
    """The notification's own membership check comes first: no inbox row, no
    delivery, even with a chat link."""
    (place,) = await _tenant(migrator)
    (elsewhere,) = await _tenant(migrator)
    outsider = await _person(migrator, elsewhere, zalo=f"z-{uuid.uuid4()}")
    key = f"test:{uuid.uuid4()}"
    await _notify(app, place, outsider, key=key)
    assert await _rows(migrator, key) == []


async def test_the_application_cannot_create_delete_or_rewrite_a_delivery(
    app: AsyncEngine, migrator: AsyncEngine
) -> None:
    (place,) = await _tenant(migrator)
    linked = await _person(migrator, place, zalo=f"z-{uuid.uuid4()}")
    await _notify(app, place, linked, key=f"test:{uuid.uuid4()}")
    for statement in (
        "INSERT INTO platform.channel_deliveries (id, tenant_id, workspace_id,"
        " recipient_user_id, channel, source_key, title)"
        " VALUES (gen_random_uuid(), :t, :w, :u, 'zalo', 'forged', 'x')",
        "DELETE FROM platform.channel_deliveries",
        "UPDATE platform.channel_deliveries SET title = 'rewritten'",
        "UPDATE platform.channel_deliveries SET recipient_user_id = :u",
        "UPDATE platform.channel_deliveries SET link = '/elsewhere'",
    ):
        with pytest.raises(ProgrammingError, match="permission denied"):
            async with app.begin() as conn:
                await conn.execute(
                    sa.text(
                        "SELECT set_config('app.tenant_id', :t, true),"
                        " set_config('app.workspace_id', :w, true)"
                    ),
                    {"t": str(place.tenant_id), "w": str(place.workspace_id)},
                )
                await conn.execute(
                    sa.text(statement),
                    {"t": place.tenant_id, "w": place.workspace_id, "u": linked},
                )


async def test_the_application_may_settle_a_delivery_and_nothing_else(
    db_urls: DatabaseUrls, migrator: AsyncEngine
) -> None:
    """Asked of the catalog, so a migration dropping a column grant goes red."""
    async with migrator.connect() as conn:

        async def table(verb: str) -> bool:
            return bool(
                await conn.scalar(
                    sa.text(
                        "SELECT has_table_privilege('dw_app', 'platform.channel_deliveries', :v)"
                    ),
                    {"v": verb},
                )
            )

        async def column(col: str) -> bool:
            return bool(
                await conn.scalar(
                    sa.text(
                        "SELECT has_column_privilege('dw_app', 'platform.channel_deliveries',"
                        " :c, 'UPDATE')"
                    ),
                    {"c": col},
                )
            )

        assert await table("SELECT")
        for verb in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            assert not await table(verb), verb
        for col in ("status", "attempts", "next_attempt_at", "external_message_id", "last_error"):
            assert await column(col), col
        for col in (
            "id",
            "tenant_id",
            "workspace_id",
            "recipient_user_id",
            "channel",
            "source_key",
            "title",
            "link",
            "created_at",
        ):
            assert not await column(col), col
        for fn in (
            "platform.channel_delivery_scopes_due(text)",
            "platform.prune_channel_deliveries()",
        ):
            assert await conn.scalar(
                sa.text("SELECT has_function_privilege('dw_app', :f, 'EXECUTE')"), {"f": fn}
            ), fn
            assert not await conn.scalar(
                sa.text("SELECT has_function_privilege('public', :f, 'EXECUTE')"), {"f": fn}
            ), fn


# ---- RLS ----------------------------------------------------------------------------


async def _visible(app: AsyncEngine, tenant: uuid.UUID, workspace: uuid.UUID, key: str) -> int:
    async with app.begin() as conn:
        await conn.execute(
            sa.text(
                "SELECT set_config('app.tenant_id', :t, true),"
                " set_config('app.workspace_id', :w, true)"
            ),
            {"t": str(tenant), "w": str(workspace)},
        )
        return int(
            await conn.scalar(
                sa.select(sa.func.count()).select_from(_d).where(_d.c.source_key == key)
            )
            or 0
        )


async def test_another_tenant_or_workspace_neither_reads_nor_changes_a_delivery(
    app: AsyncEngine, migrator: AsyncEngine
) -> None:
    here, sibling = await _tenant(migrator, workspaces=2)
    (other,) = await _tenant(migrator)
    linked = await _person(migrator, here, zalo=f"z-{uuid.uuid4()}")
    key = f"test:{uuid.uuid4()}"
    await _notify(app, here, linked, key=key)

    assert await _visible(app, here.tenant_id, here.workspace_id, key) == 1
    assert await _visible(app, sibling.tenant_id, sibling.workspace_id, key) == 0
    assert await _visible(app, other.tenant_id, other.workspace_id, key) == 0
    # Naming the right workspace under the wrong tenant opens nothing either.
    assert await _visible(app, other.tenant_id, here.workspace_id, key) == 0

    for place in (sibling, other):
        async with app.begin() as conn:
            await conn.execute(
                sa.text(
                    "SELECT set_config('app.tenant_id', :t, true),"
                    " set_config('app.workspace_id', :w, true)"
                ),
                {"t": str(place.tenant_id), "w": str(place.workspace_id)},
            )
            changed = await conn.execute(
                sa.update(_d).where(_d.c.source_key == key).values(status="cancelled")
            )
            assert changed.rowcount == 0
    assert [r.status for r in await _rows(migrator, key)] == ["pending"]


async def test_the_scopes_function_names_ids_only_and_only_due_pending_rows(
    app: AsyncEngine, migrator: AsyncEngine
) -> None:
    (place,) = await _tenant(migrator)
    linked = await _person(migrator, place, zalo=f"z-{uuid.uuid4()}")
    key = f"test:{uuid.uuid4()}"
    await _notify(app, place, linked, key=key)
    outbox = SqlChannelOutbox(async_sessionmaker(app, expire_on_commit=False))
    mine = DeliveryScope(place.tenant_id, place.workspace_id)

    assert mine in await outbox.due_scopes("zalo")
    async with migrator.begin() as conn:
        await conn.execute(
            sa.update(_d)
            .where(_d.c.source_key == key)
            .values(next_attempt_at=sa.text("now() + interval '1 hour'"))
        )
    assert mine not in await outbox.due_scopes("zalo")


async def test_a_claim_under_one_scope_never_sees_another_tenants_row(
    app: AsyncEngine, migrator: AsyncEngine
) -> None:
    """Even asked for the other tenant's workspace id by name, the claim binds
    the scope's own tenant, and RLS hands back nothing that is not its own."""
    (mine,) = await _tenant(migrator)
    (theirs,) = await _tenant(migrator)
    linked = await _person(migrator, theirs, zalo=f"z-{uuid.uuid4()}")
    await _notify(app, theirs, linked, key=f"test:{uuid.uuid4()}")
    outbox = SqlChannelOutbox(async_sessionmaker(app, expire_on_commit=False))

    async with outbox.claim_next(
        "zalo", DeliveryScope(mine.tenant_id, theirs.workspace_id)
    ) as claim:
        assert claim is None


# ---- pruning and offboarding -------------------------------------------------------


async def test_pruning_removes_finished_rows_past_ninety_days_never_a_pending_one(
    app: AsyncEngine, migrator: AsyncEngine
) -> None:
    (place,) = await _tenant(migrator)
    linked = await _person(migrator, place, zalo=f"z-{uuid.uuid4()}")
    keys = {name: f"test:{name}:{uuid.uuid4()}" for name in ("old_sent", "old_pending", "fresh")}
    for key in keys.values():
        await _notify(app, place, linked, key=key)
    async with migrator.begin() as conn:
        for name, status in (("old_sent", "sent"), ("old_pending", "pending")):
            await conn.execute(
                sa.update(_d)
                .where(_d.c.source_key == keys[name])
                .values(
                    status=status,
                    external_message_id="m" if status == "sent" else None,
                    created_at=sa.text("now() - interval '91 days'"),
                )
            )
        await conn.execute(
            sa.update(_d).where(_d.c.source_key == keys["fresh"]).values(status="failed")
        )

    await SqlChannelDeliveryRetention(async_sessionmaker(app)).prune()

    left = {name for name, key in keys.items() if await _rows(migrator, key)}
    assert left == {"old_pending", "fresh"}


async def test_offboarding_exports_the_tenants_deliveries_and_leaves_none(
    app: AsyncEngine, migrator: AsyncEngine
) -> None:
    here, sibling = await _tenant(migrator, workspaces=2)
    (other,) = await _tenant(migrator)
    a = await _person(migrator, here, sibling, zalo=f"z-{uuid.uuid4()}")
    b = await _person(migrator, other, zalo=f"z-{uuid.uuid4()}")
    mine = [f"test:{uuid.uuid4()}", f"test:{uuid.uuid4()}"]
    theirs = f"test:{uuid.uuid4()}"
    await _notify(app, here, a, key=mine[0])
    await _notify(app, sibling, a, key=mine[1])
    await _notify(app, other, b, key=theirs)
    offboarding = SqlTenantOffboarding(async_sessionmaker(app, expire_on_commit=False))

    exported = await offboarding.export_rows(here.tenant_id)
    (table,) = [t for t in exported if (t.schema, t.table) == ("platform", "channel_deliveries")]
    assert {row["source_key"] for row in table.rows} == set(mine)

    await offboarding.purge_rows(here.tenant_id)
    assert [await _rows(migrator, key) for key in mine] == [[], []]
    assert len(await _rows(migrator, theirs)) == 1


async def test_a_title_or_link_the_inbox_refuses_never_reaches_the_queue(
    app: AsyncEngine, migrator: AsyncEngine
) -> None:
    """The door is one statement: a notification refused by its own CHECK
    queues no delivery either."""
    (place,) = await _tenant(migrator)
    linked = await _person(migrator, place, zalo=f"z-{uuid.uuid4()}")
    key = f"test:{uuid.uuid4()}"
    with pytest.raises(DBAPIError):
        await SqlNotificationRepository(async_sessionmaker(app)).deliver(
            place.sender(),
            recipients=[linked],
            source_key=key,
            title="x",
            body="",
            link="https://evil.example",
        )
    assert await _rows(migrator, key) == []
