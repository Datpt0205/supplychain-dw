"""Integration: blank-DB migration, RLS isolation, default-deny, append-only audit."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from pg_harness import DatabaseUrls
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from dw_platform.adapters.persistence import tables

pytestmark = pytest.mark.integration

TENANT_A = uuid.UUID(int=0xA)
TENANT_B = uuid.UUID(int=0xB)
WORKSPACE_A = uuid.UUID(int=0xA1)
WORKSPACE_B = uuid.UUID(int=0xB1)
NOW = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)

# Every table the runtime role may read that is governed by a tenant policy.
# Read from the catalogue rather than listed, so a table added by a later
# migration is covered without anyone remembering to add it here.
# `has_table_privilege` only after the policy rows are chosen: its arguments are
# `pg_class` columns, so the planner may push it below the join and call it on
# every relation, `pg_toast`'s included, which `dw_app` may not resolve
# ("permission denied for schema pg_toast"). Which plan it picks depends on the
# catalog's statistics, so the bare query passed or failed with test order.
# `OFFSET 0` keeps the subquery a fence the planner does not flatten.
_TENANT_SCOPED_TABLES = """
    SELECT schemaname, tablename
    FROM (
        SELECT DISTINCT schemaname, tablename
        FROM pg_policies
        WHERE policyname LIKE 'tenant_isolation_%'
        OFFSET 0
    ) AS policies
    WHERE has_table_privilege('dw_app', format('%I.%I', schemaname, tablename), 'SELECT')
    ORDER BY schemaname, tablename
"""

_UNSAFE_POLICIES = """
    SELECT schemaname, tablename, policyname
    FROM pg_policies
    WHERE coalesce(qual, '') || coalesce(with_check, '') LIKE '%current_setting%'
      AND coalesce(qual, '') || coalesce(with_check, '') LIKE '%uuid%'
      AND coalesce(qual, '') || coalesce(with_check, '') NOT LIKE '%NULLIF%'
    ORDER BY schemaname, tablename, policyname
"""


@pytest.fixture
async def app_engine(db_urls: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield engine
    await engine.dispose()


async def _set_tenant(conn: AsyncConnection, tenant_id: uuid.UUID) -> None:
    await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)})


async def _seed_tenant_fixture(
    engine: AsyncEngine, tenant_id: uuid.UUID, workspace_id: uuid.UUID, slug: str
) -> None:
    """Create tenant + workspace + one approval request as dw_app under RLS."""
    async with engine.connect() as conn:
        async with conn.begin():
            await _set_tenant(conn, tenant_id)
            await conn.execute(
                sa.insert(tables.tenants).values(id=tenant_id, slug=slug, name=slug),
            )
            await conn.execute(
                sa.insert(tables.workspaces).values(
                    id=workspace_id, tenant_id=tenant_id, slug="main", name="Main"
                )
            )
            await conn.execute(
                sa.insert(tables.approval_requests).values(
                    id=uuid.uuid5(tenant_id, "approval-1"),
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    approval_type="test.approval",
                    requested_by=uuid.uuid4(),
                    reason="fixture",
                )
            )


async def test_rls_tenant_isolation(app_engine: AsyncEngine) -> None:
    await _seed_tenant_fixture(app_engine, TENANT_A, WORKSPACE_A, "iso-tenant-a")
    await _seed_tenant_fixture(app_engine, TENANT_B, WORKSPACE_B, "iso-tenant-b")

    async with app_engine.connect() as conn:
        async with conn.begin():
            await _set_tenant(conn, TENANT_A)
            rows = (await conn.execute(sa.select(tables.approval_requests.c.tenant_id))).all()
            assert rows, "tenant A must see its own rows"
            assert {row.tenant_id for row in rows} == {TENANT_A}

        async with conn.begin():
            await _set_tenant(conn, TENANT_B)
            rows = (await conn.execute(sa.select(tables.approval_requests.c.tenant_id))).all()
            assert {row.tenant_id for row in rows} == {TENANT_B}, "tenant B must never see A"


async def test_rls_default_deny_without_context(app_engine: AsyncEngine) -> None:
    async with app_engine.connect() as conn:
        async with conn.begin():
            rows = (await conn.execute(sa.select(tables.approval_requests))).all()
            assert rows == [], "no tenant context must mean zero rows (fail closed)"
            tenants = (await conn.execute(sa.select(tables.tenants))).all()
            assert tenants == []


async def test_every_tenant_policy_denies_an_emptied_context(app_engine: AsyncEngine) -> None:
    """An emptied tenant GUC must match nothing, not raise.

    A fresh connection has never seen ``app.tenant_id``, so the test above reads
    NULL. A pooled one has: Postgres keeps a custom GUC as the empty string once
    ``SET LOCAL`` releases it, and that is what the next caller binding no tenant
    actually sees. A policy that casts it without ``NULLIF`` raises there, which
    is how the outbox dispatcher found this.
    """
    async with app_engine.connect() as conn, conn.begin():
        targets = (await conn.execute(text(_TENANT_SCOPED_TABLES))).all()
    assert len(targets) >= 16, "expected every tenant-scoped table to carry a policy"

    failures: list[str] = []
    for row in targets:
        name = f"{row.schemaname}.{row.tablename}"
        # One transaction per table: an error aborts the whole transaction, and
        # a cascade of "current transaction is aborted" hides which table broke.
        async with app_engine.connect() as conn, conn.begin():
            await conn.execute(text("SELECT set_config('app.tenant_id', '', true)"))
            try:
                count = (
                    await conn.execute(
                        text(f'SELECT count(*) FROM "{row.schemaname}"."{row.tablename}"')
                    )
                ).scalar_one()
            except sa.exc.DBAPIError as exc:
                failures.append(f"{name} raised instead of denying: {exc}")
            else:
                if count:
                    failures.append(f"{name} returned {count} rows to a caller with no tenant")
    assert not failures, "\n".join(failures)


async def test_no_tenant_policy_casts_the_guc_without_null_safety(
    db_urls: DatabaseUrls,
) -> None:
    """The rule in .claude/rules/database.md, asserted rather than trusted."""
    engine = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            unsafe = (await conn.execute(text(_UNSAFE_POLICIES))).all()
    finally:
        await engine.dispose()

    assert not unsafe, "policies must read the tenant GUC through NULLIF: " + ", ".join(
        f"{row.schemaname}.{row.tablename}.{row.policyname}" for row in unsafe
    )


async def test_rls_blocks_cross_tenant_write(app_engine: AsyncEngine) -> None:
    """Context = tenant B, but row claims tenant A → WITH CHECK must reject."""
    async with app_engine.connect() as conn:
        async with conn.begin():
            await _set_tenant(conn, TENANT_B)
            with pytest.raises(sa.exc.DBAPIError) as excinfo:
                await conn.execute(
                    sa.insert(tables.approval_requests).values(
                        id=uuid.uuid4(),
                        tenant_id=TENANT_A,  # spoof attempt
                        workspace_id=WORKSPACE_A,
                        approval_type="attack.cross_tenant",
                        requested_by=uuid.uuid4(),
                        reason="should fail",
                    )
                )
            assert "row-level security" in str(excinfo.value).lower()


async def test_audit_is_append_only_for_app_role(app_engine: AsyncEngine) -> None:
    event_id = uuid.uuid4()
    async with app_engine.connect() as conn:
        async with conn.begin():
            await _set_tenant(conn, TENANT_A)
            await conn.execute(
                sa.insert(tables.audit_events).values(
                    id=event_id,
                    tenant_id=TENANT_A,
                    workspace_id=WORKSPACE_A,
                    actor_id=uuid.uuid4(),
                    action="test.audit",
                    resource_type="test",
                    resource_id="r-1",
                    occurred_at=NOW,
                )
            )

    async with app_engine.connect() as conn:
        async with conn.begin():
            await _set_tenant(conn, TENANT_A)
            with pytest.raises(sa.exc.DBAPIError) as update_err:
                await conn.execute(
                    sa.update(tables.audit_events)
                    .where(tables.audit_events.c.id == event_id)
                    .values(action="tampered")
                )
            assert "permission denied" in str(update_err.value).lower()

    async with app_engine.connect() as conn:
        async with conn.begin():
            await _set_tenant(conn, TENANT_A)
            with pytest.raises(sa.exc.DBAPIError) as delete_err:
                await conn.execute(
                    sa.delete(tables.audit_events).where(tables.audit_events.c.id == event_id)
                )
            assert "permission denied" in str(delete_err.value).lower()
