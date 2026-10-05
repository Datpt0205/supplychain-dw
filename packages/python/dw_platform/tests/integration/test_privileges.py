"""Integration: what the application role may and may not do.

A schema with no grants passes every health check and fails on the first real
query — the probe runs ``SELECT 1``, which needs no table privilege. That is
exactly how a baseline built with ``pg_dump --no-privileges`` shipped once:
green everywhere, and an application that could not read a row.

So the privilege model is asserted, not assumed. Three claims:

* ``dw_app`` can read and write the tables it serves;
* ``dw_app`` CANNOT rewrite the audit log — append-only is a grant, not a
  convention somebody could code around;
* ``dw_app`` cannot touch the provisioning record, which belongs to a different
  role making a different decision.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from pg_harness import DatabaseUrls
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool
from test_rls_coverage import tenant_schemas

pytestmark = pytest.mark.integration


@pytest.fixture
async def app_engine(db_urls: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    """A connection as the runtime role, not as the migrator.

    The migrator holds BYPASSRLS and owns every object, so a test that connects
    as the migrator proves nothing about what the application is allowed to do.
    """
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_the_application_can_read_the_tables_it_serves(app_engine: AsyncEngine) -> None:
    async with app_engine.connect() as conn:
        for table in (
            "platform.tenants",
            "platform.workspaces",
            "platform.users",
            "platform.memberships",
            "knowledge.documents",
            "memory.items",
        ):
            # The count is irrelevant; being allowed to ask is the assertion.
            await conn.execute(sa.text(f"SELECT count(*) FROM {table}"))


async def test_the_application_can_enter_every_tenant_schema(db_urls: DatabaseUrls) -> None:
    """Without USAGE on its schema no table grant is reachable: the first real
    query fails with "permission denied for schema" while `SELECT 1` stays green.

    The schemas come from the catalog — `tenant_schemas`, the same discovery the
    RLS coverage test uses — not from a list here. `0001_platform_grants.sql`
    grants USAGE on the schemas that existed when it was written; a schema added
    later needs its own grant, and a hand-kept list would be blind to exactly
    that schema.
    """
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with migrator.connect() as conn:
            schemas = await tenant_schemas(conn)
            result = await conn.execute(
                sa.text(
                    "SELECT s FROM unnest(CAST(:schemas AS text[])) AS s"
                    " WHERE NOT has_schema_privilege('dw_app', s, 'USAGE')"
                    " ORDER BY s"
                ),
                {"schemas": schemas},
            )
            unreachable = result.scalars().all()
    finally:
        await migrator.dispose()
    assert unreachable == [], f"dw_app has no USAGE on tenant schemas: {unreachable}"


async def test_the_application_cannot_rewrite_the_audit_log(app_engine: AsyncEngine) -> None:
    """Append-only, enforced by the database.

    An UPDATE that the code would never issue is still an UPDATE the database
    must refuse: credentials reach further than the code that was reviewed.
    """
    async with app_engine.connect() as conn:
        with pytest.raises(Exception, match="permission denied"):
            await conn.execute(sa.text("UPDATE platform.audit_events SET action = 'x'"))
        await conn.rollback()
        with pytest.raises(Exception, match="permission denied"):
            await conn.execute(sa.text("DELETE FROM platform.audit_events"))


_CATALOGUE = ("platform.roles", "platform.permission_sets")


async def test_the_application_cannot_rewrite_the_role_catalogue(app_engine: AsyncEngine) -> None:
    """Which scopes a membership carries changes in migrations alone.

    No application code writes these tables, so a write under application
    credentials could only be someone widening a role past a
    separation-of-duty rule (migration 648e2f7c3edb).
    """
    async with app_engine.connect() as conn:
        for table in _CATALOGUE:
            await conn.execute(sa.text(f"SELECT count(*) FROM {table}"))
            await conn.rollback()
            for statement in (
                f"UPDATE {table} SET scopes = scopes || '[\"platform.admin\"]'::jsonb",
                f"DELETE FROM {table}",
                f"INSERT INTO {table} (key, name, scopes) VALUES ('probe', 'Probe', '[]')",
            ):
                with pytest.raises(Exception, match="permission denied"):
                    await conn.execute(sa.text(statement))
                await conn.rollback()


async def test_no_application_role_can_write_the_role_catalogue(db_urls: DatabaseUrls) -> None:
    """Asked of the catalog, so every verb and every runtime role is covered,
    `dw_provisioner` included: it is granted SELECT on roles and no more."""
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with migrator.connect() as conn:
            writable = (
                await conn.execute(
                    sa.text(
                        """
                        SELECT r.rolname, t.tbl, v.verb
                        FROM pg_roles r
                        CROSS JOIN unnest(CAST(:tables AS text[])) AS t(tbl)
                        CROSS JOIN unnest(ARRAY['INSERT', 'UPDATE', 'DELETE', 'TRUNCATE'])
                            AS v(verb)
                        WHERE r.rolname IN ('dw_app', 'dw_provisioner', 'dw_agent_ro')
                          AND has_table_privilege(r.rolname, t.tbl, v.verb)
                        """
                    ),
                    {"tables": list(_CATALOGUE)},
                )
            ).all()
    finally:
        await migrator.dispose()
    assert writable == []


async def test_the_application_cannot_read_the_provisioning_record(
    app_engine: AsyncEngine,
) -> None:
    async with app_engine.connect() as conn:
        with pytest.raises(Exception, match="permission denied"):
            await conn.execute(sa.text("SELECT count(*) FROM platform.provisioning_audit"))


async def test_a_table_added_later_is_readable_without_a_new_grant(
    app_engine: AsyncEngine, db_urls: DatabaseUrls
) -> None:
    """Default privileges carry, so "somebody forgot the GRANT" cannot ship.

    Written as a real table created by the migrator, because that is the case
    that goes wrong: every later migration adds tables, and each one relying on
    a hand-written grant is one release away from the failure above.
    """
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with migrator.begin() as conn:
            await conn.execute(sa.text("CREATE TABLE platform.grant_probe (id uuid PRIMARY KEY)"))
        async with app_engine.connect() as conn:
            await conn.execute(sa.text("SELECT count(*) FROM platform.grant_probe"))
        async with migrator.begin() as conn:
            await conn.execute(sa.text("DROP TABLE platform.grant_probe"))
    finally:
        await migrator.dispose()


async def test_the_application_may_only_mark_a_link_nonce_used(db_urls: DatabaseUrls) -> None:
    """`platform.channel_link_nonces` (migration cf66605631d7): `dw_app` issues,
    consumes and prunes nonces, and the consume may set `used_at` and nothing
    else — never move a nonce to another user or stretch its expiry. Asked of the
    catalog, so a migration that dropped the column-level grant goes red here."""
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with migrator.connect() as conn:

            async def table(verb: str) -> bool:
                return bool(
                    await conn.scalar(
                        sa.text(
                            "SELECT has_table_privilege('dw_app',"
                            " 'platform.channel_link_nonces', :verb)"
                        ),
                        {"verb": verb},
                    )
                )

            async def column(name: str, verb: str) -> bool:
                return bool(
                    await conn.scalar(
                        sa.text(
                            "SELECT has_column_privilege('dw_app',"
                            " 'platform.channel_link_nonces', :col, :verb)"
                        ),
                        {"col": name, "verb": verb},
                    )
                )

            assert await table("SELECT")
            assert await table("INSERT")
            assert await table("DELETE")
            assert not await table("UPDATE")  # no table-wide UPDATE
            assert not await table("TRUNCATE")
            assert await column("used_at", "UPDATE")
            for name in ("jti", "channel", "user_id", "expires_at", "created_at"):
                assert not await column(name, "UPDATE"), name
    finally:
        await migrator.dispose()


async def test_the_application_may_only_record_a_decision_on_an_approval(
    db_urls: DatabaseUrls,
) -> None:
    """`platform.approval_requests` (migration 5d3965984679): a decision writes
    `status`, `decided_at` and `version`, and nothing else may move. Above all
    `required_scope`, the stamp of who may decide (ADR 0020). Asked of the
    catalog, so a later blanket GRANT that restored table-wide UPDATE goes red."""
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with migrator.connect() as conn:

            async def table(verb: str) -> bool:
                return bool(
                    await conn.scalar(
                        sa.text(
                            "SELECT has_table_privilege('dw_app',"
                            " 'platform.approval_requests', :verb)"
                        ),
                        {"verb": verb},
                    )
                )

            async def column(name: str, verb: str) -> bool:
                return bool(
                    await conn.scalar(
                        sa.text(
                            "SELECT has_column_privilege('dw_app',"
                            " 'platform.approval_requests', :col, :verb)"
                        ),
                        {"col": name, "verb": verb},
                    )
                )

            assert await table("SELECT")
            assert await table("INSERT")
            # The offboarding lane deletes every table dw_app may DELETE.
            assert await table("DELETE")
            assert not await table("UPDATE")  # no table-wide UPDATE
            for name in ("status", "decided_at", "version"):
                assert await column(name, "UPDATE"), name
            for name in (
                "required_scope",
                "approval_type",
                "requested_by",
                "payload",
                "tenant_id",
                "workspace_id",
                "run_id",
            ):
                assert not await column(name, "UPDATE"), name
    finally:
        await migrator.dispose()
