"""SqlTenantOffboarding against real Postgres: catalog discovery, FK order, isolation.

Seeds through raw SQL rather than importing `dw_memory`/`dw_knowledge` table
objects — `dw_platform` declares no dependency on either (checked before
writing this), and `SqlTenantOffboarding` itself never imports them either.
That is the property under test as much as export/purge behavior is: this
file couldn't cheat by reaching for a table object even if it wanted to.

Each test seeds its own fresh tenant/workspace pair rather than sharing module
constants: `db_urls` is session-scoped (one database for the whole file), and
seeding the same ids twice collides on `uq_workspaces_tenant_slug`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from pg_harness import DatabaseUrls
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dw_platform.adapters.persistence.offboarding import SqlTenantOffboarding

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 22, tzinfo=UTC)


@dataclass(frozen=True)
class _Tenant:
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID


def _new_tenant() -> _Tenant:
    return _Tenant(tenant_id=uuid.uuid4(), workspace_id=uuid.uuid4())


@pytest.fixture
async def sessions(db_urls: DatabaseUrls) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def _seed_tenant(sessions: async_sessionmaker[AsyncSession], t: _Tenant) -> None:
    """A tenant + workspace, plus a chain that exercises every FK-RESTRICT
    edge purge order depends on:
    worker_runs <- items <- item_evidence -> evidence <- (chunks, documents).
    """
    run_id, memory_id, evidence_id, document_id = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    async with sessions() as session, session.begin():
        await session.execute(
            sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(t.tenant_id)}
        )
        await session.execute(
            sa.text("INSERT INTO platform.tenants (id, slug, name) VALUES (:t, :slug, :slug)"),
            {"t": t.tenant_id, "slug": f"off-{t.tenant_id.hex[-10:]}"},
        )
        await session.execute(
            sa.text(
                "INSERT INTO platform.workspaces (id, tenant_id, slug, name)"
                " VALUES (:w, :t, 'main', 'Main')"
            ),
            {"w": t.workspace_id, "t": t.tenant_id},
        )
        await session.execute(
            sa.text(
                "INSERT INTO platform.worker_runs"
                " (id, thread_id, tenant_id, workspace_id, worker_id, worker_version,"
                "  graph_version, requested_by)"
                " VALUES (:r, :r, :t, :w, 'demo', '1.0.0', '1.0.0', gen_random_uuid())"
            ),
            {"r": run_id, "t": t.tenant_id, "w": t.workspace_id},
        )
        await session.execute(
            sa.text(
                "INSERT INTO knowledge.documents"
                " (id, tenant_id, workspace_id, title, source_uri, created_by, created_at)"
                " VALUES (:d, :t, :w, 'tài liệu', 's3://dw/x', gen_random_uuid(), :c)"
            ),
            {"d": document_id, "t": t.tenant_id, "w": t.workspace_id, "c": NOW},
        )
        await session.execute(
            sa.text(
                "INSERT INTO knowledge.evidence"
                " (evidence_id, tenant_id, workspace_id, source_document_id, source_version,"
                "  relevance_score, classification, provenance_hash, created_at)"
                " VALUES (:e, :t, :w, :d, '1', 0.9, 'internal', :h, :c)"
            ),
            {
                "e": evidence_id,
                "t": t.tenant_id,
                "w": t.workspace_id,
                "d": document_id,
                "h": "0" * 64,
                "c": NOW,
            },
        )
        await session.execute(
            sa.text(
                "INSERT INTO memory.items"
                " (memory_id, tenant_id, workspace_id, worker_id, memory_type, subject_refs,"
                "  content, structured_facts, provenance_refs, confidence, classification,"
                "  valid_from, retention_policy, memory_schema_version, created_by_run_id,"
                "  created_at)"
                " VALUES (:m, :t, :w, 'demo', 'semantic', '[]', 'x', '{}',"
                "  :refs, 0.9, 'internal', :c, 'default', '1.0.0', :r, :c)"
            ),
            {
                "m": memory_id,
                "t": t.tenant_id,
                "w": t.workspace_id,
                "refs": f'[{{"evidence_id": "{uuid.uuid4()}"}}]',
                "c": NOW,
                "r": run_id,
            },
        )
        await session.execute(
            sa.text(
                "INSERT INTO memory.item_evidence (memory_id, evidence_id, tenant_id)"
                " VALUES (:m, :e, :t)"
            ),
            {"m": memory_id, "e": evidence_id, "t": t.tenant_id},
        )
        # Append-only: dw_app has no DELETE here (0001_platform_grants.sql).
        # purge_rows must skip it entirely, not fail trying.
        await session.execute(
            sa.text(
                "INSERT INTO platform.audit_events"
                " (id, tenant_id, workspace_id, actor_id, action, resource_type,"
                "  resource_id, details, occurred_at)"
                " VALUES (gen_random_uuid(), :t, :w, gen_random_uuid(), 'test.seed',"
                "  'test', 'r-1', '{}', :c)"
            ),
            {"t": t.tenant_id, "w": t.workspace_id, "c": NOW},
        )


async def _count(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID, schema: str, table: str
) -> int:
    async with sessions() as session, session.begin():
        await session.execute(
            sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)}
        )
        return (
            await session.scalar(
                sa.text(f'SELECT count(*) FROM "{schema}"."{table}" WHERE tenant_id = :t'),
                {"t": str(tenant_id)},
            )
        ) or 0


async def test_catalog_discovery_sees_a_table_added_after_this_class_was_written(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """`tenant_daily_spend_guard` (Ops hardening Phase 3) is not named anywhere
    in offboarding.py — proof the discovery is real, not a list that happens
    to be current today."""
    offboarding = SqlTenantOffboarding(session_factory=sessions)
    async with sessions() as session, session.begin():
        exportable = await offboarding._exportable_tables(session)
        purgeable = await offboarding._purgeable_tables(session)
    assert ("platform", "tenant_daily_spend_guard") in exportable
    assert ("platform", "tenant_daily_spend_guard") in purgeable
    assert ("platform", "tenant_offboarding_requests") not in exportable, (
        "the request row IS the job; it must never purge itself"
    )
    assert ("platform", "tenants") not in exportable, (
        "tenants carries a tenant_isolation_% policy but no tenant_id column"
    )
    assert ("platform", "audit_events") in exportable, "the tenant may still export its own audit"
    assert ("platform", "audit_events") not in purgeable, (
        "audit_events is append-only (dw_app has no DELETE); its own retention"
        " term governs it, not offboarding"
    )


async def test_export_rows_returns_only_this_tenants_data(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    mine, theirs = _new_tenant(), _new_tenant()
    await _seed_tenant(sessions, mine)
    await _seed_tenant(sessions, theirs)
    offboarding = SqlTenantOffboarding(session_factory=sessions)

    exported = await offboarding.export_rows(mine.tenant_id)

    by_table = {(t.schema, t.table): t.rows for t in exported}
    workspace_rows = by_table[("platform", "workspaces")]
    assert [str(r["tenant_id"]) for r in workspace_rows] == [str(mine.tenant_id)]
    item_rows = by_table[("memory", "items")]
    assert [str(r["tenant_id"]) for r in item_rows] == [str(mine.tenant_id)]


async def test_purge_rows_respects_fk_order_and_leaves_the_other_tenant_alone(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    mine, theirs = _new_tenant(), _new_tenant()
    await _seed_tenant(sessions, mine)
    await _seed_tenant(sessions, theirs)
    offboarding = SqlTenantOffboarding(session_factory=sessions)

    await offboarding.purge_rows(mine.tenant_id)  # must not raise a FK violation

    for schema, table in (
        ("memory", "items"),
        ("memory", "item_evidence"),
        ("knowledge", "evidence"),
        ("knowledge", "documents"),
        ("platform", "worker_runs"),
        ("platform", "workspaces"),
    ):
        assert await _count(sessions, mine.tenant_id, schema, table) == 0, (
            f"{schema}.{table} not purged"
        )
        assert await _count(sessions, theirs.tenant_id, schema, table) == 1, (
            f"{schema}.{table} lost the other tenant's row too"
        )

    assert await _count(sessions, mine.tenant_id, "platform", "audit_events") == 1, (
        "audit_events is append-only; purge must not touch it even for the tenant being offboarded"
    )


async def _seed_checkpoint(sessions: async_sessionmaker[AsyncSession], t: _Tenant) -> None:
    """One run checkpoint and one pending write: the verbatim conversation a
    thread leaves behind, which must go with the tenant."""
    thread = uuid.uuid4()
    async with sessions() as session, session.begin():
        await session.execute(
            sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(t.tenant_id)}
        )
        await session.execute(
            sa.text(
                "INSERT INTO platform.run_checkpoints"
                " (thread_id, checkpoint_ns, checkpoint_id, tenant_id, workspace_id,"
                "  type, checkpoint, metadata)"
                " VALUES (:th, '', 'c1', :t, :w, 'msgpack', '\\x00', '\\x00')"
            ),
            {"th": thread, "t": t.tenant_id, "w": t.workspace_id},
        )
        await session.execute(
            sa.text(
                "INSERT INTO platform.run_checkpoint_writes"
                " (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, tenant_id,"
                "  workspace_id, channel, type, value)"
                " VALUES (:th, '', 'c1', 'task', 0, :t, :w, 'messages', 'msgpack', '\\x00')"
            ),
            {"th": thread, "t": t.tenant_id, "w": t.workspace_id},
        )


async def test_purge_deletes_the_tenants_run_checkpoints(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Pinned, not built: the lane finds both checkpoint tables from the catalog
    (a `tenant_isolation_%` policy and `dw_app` holding DELETE), so nothing here
    names them. This is what goes red if either stops being true."""
    mine, theirs = _new_tenant(), _new_tenant()
    for t in (mine, theirs):
        await _seed_tenant(sessions, t)
        await _seed_checkpoint(sessions, t)

    await SqlTenantOffboarding(session_factory=sessions).purge_rows(mine.tenant_id)

    for table in ("run_checkpoints", "run_checkpoint_writes"):
        assert await _count(sessions, mine.tenant_id, "platform", table) == 0, (
            f"platform.{table} kept the offboarded tenant's conversation"
        )
        assert await _count(sessions, theirs.tenant_id, "platform", table) == 1, (
            f"platform.{table} lost the other tenant's row too"
        )


async def test_purge_records_an_audit_event_before_deleting_anything(
    sessions: async_sessionmaker[AsyncSession], db_urls: DatabaseUrls
) -> None:
    """The one delete-adjacent thing in this pass that cannot be undone gets
    its own permanent record — in `audit_events`, which the purge it belongs
    to can never itself remove."""
    mine = _new_tenant()
    await _seed_tenant(sessions, mine)  # seeds one audit_events row of its own
    request_id = await _file_request(db_urls, mine.tenant_id)
    offboarding = SqlTenantOffboarding(session_factory=sessions)

    await offboarding.purge_rows(mine.tenant_id)

    async with sessions() as session, session.begin():
        await session.execute(
            sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(mine.tenant_id)}
        )
        row = (
            await session.execute(
                sa.text(
                    "SELECT actor_id, resource_id FROM platform.audit_events"
                    " WHERE action = 'tenant.offboarding.purged' AND tenant_id = :t"
                ),
                {"t": str(mine.tenant_id)},
            )
        ).one()
        requested_by = await session.scalar(
            sa.text("SELECT requested_by FROM platform.tenant_offboarding_requests WHERE id = :r"),
            {"r": request_id},
        )
    assert row.resource_id == str(mine.tenant_id)
    assert row.actor_id == requested_by, "the audit row credits the operator who requested this"


async def _file_request(db_urls: DatabaseUrls, tenant_id: uuid.UUID) -> uuid.UUID:
    """dw_app has no INSERT on this table (migration 5d9d89ffc716) - insert as
    the migrator, the same way the real row already exists by the time a
    worker lane sees it (the operator's INSERT happens through
    dw_provisioner, which bypasses RLS)."""
    request_id = uuid.uuid4()
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with migrator.begin() as conn:
            await conn.execute(
                sa.text(
                    "INSERT INTO platform.tenant_offboarding_requests"
                    " (id, tenant_id, requested_by) VALUES (:r, :t, gen_random_uuid())"
                ),
                {"r": request_id, "t": tenant_id},
            )
    finally:
        await migrator.dispose()
    return request_id


async def test_purge_never_touches_the_offboarding_request_itself(
    sessions: async_sessionmaker[AsyncSession], db_urls: DatabaseUrls
) -> None:
    mine = _new_tenant()
    await _seed_tenant(sessions, mine)
    request_id = await _file_request(db_urls, mine.tenant_id)

    offboarding = SqlTenantOffboarding(session_factory=sessions)
    await offboarding.purge_rows(mine.tenant_id)

    async with sessions() as session, session.begin():
        await session.execute(
            sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(mine.tenant_id)}
        )
        found = await session.scalar(
            sa.text("SELECT 1 FROM platform.tenant_offboarding_requests WHERE id = :r"),
            {"r": request_id},
        )
    assert found == 1, "the request row must survive its own purge pass"


async def test_another_tenant_cannot_read_the_request_through_ordinary_rls(
    sessions: async_sessionmaker[AsyncSession], db_urls: DatabaseUrls
) -> None:
    """Through `dw_app` directly, RLS on, no drain — the path an application
    request actually takes. `claim_requested`'s `app.worker_drain` escape
    hatch is for the worker's own poll, never reachable from a request, and
    this proves the ordinary policy still holds without it."""
    mine, theirs = _new_tenant(), _new_tenant()
    await _seed_tenant(sessions, mine)
    await _seed_tenant(sessions, theirs)
    await _file_request(db_urls, mine.tenant_id)

    async with sessions() as session, session.begin():
        await session.execute(
            sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(theirs.tenant_id)}
        )
        rows = (
            await session.execute(
                sa.text("SELECT tenant_id FROM platform.tenant_offboarding_requests")
            )
        ).all()

    assert rows == [], "tenant B must see nothing of tenant A's offboarding request"


async def test_claim_requested_moves_status_and_returns_the_tenant(
    sessions: async_sessionmaker[AsyncSession], db_urls: DatabaseUrls
) -> None:
    mine = _new_tenant()
    await _seed_tenant(sessions, mine)
    await _file_request(db_urls, mine.tenant_id)
    offboarding = SqlTenantOffboarding(session_factory=sessions)

    claimed = await offboarding.claim_requested()

    assert mine.tenant_id in claimed
    async with sessions() as session, session.begin():
        await session.execute(
            sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(mine.tenant_id)}
        )
        status = await session.scalar(
            sa.text("SELECT status FROM platform.tenant_offboarding_requests WHERE tenant_id = :t"),
            {"t": str(mine.tenant_id)},
        )
    assert status == "exporting"


async def test_claim_requested_never_claims_the_same_request_twice(
    sessions: async_sessionmaker[AsyncSession], db_urls: DatabaseUrls
) -> None:
    mine = _new_tenant()
    await _seed_tenant(sessions, mine)
    await _file_request(db_urls, mine.tenant_id)
    offboarding = SqlTenantOffboarding(session_factory=sessions)

    first = await offboarding.claim_requested()
    second = await offboarding.claim_requested()

    assert mine.tenant_id in first
    assert mine.tenant_id not in second, "a claimed request must not be claimed again"


async def _age_the_request(db_urls: DatabaseUrls, tenant_id: uuid.UUID, minutes_ago: int) -> None:
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with migrator.begin() as conn:
            await conn.execute(
                sa.text(
                    "UPDATE platform.tenant_offboarding_requests"
                    " SET updated_at = now() - make_interval(mins => :m)"
                    " WHERE tenant_id = :t"
                ),
                {"m": minutes_ago, "t": tenant_id},
            )
    finally:
        await migrator.dispose()


async def test_a_request_stuck_in_progress_past_the_stale_window_is_reclaimed(
    sessions: async_sessionmaker[AsyncSession], db_urls: DatabaseUrls
) -> None:
    """A worker that claimed a request and then died leaves it at
    'exporting'/'purging' forever otherwise — nothing else ever moves a
    request off 'requested', so a dead worker's claim has to expire."""
    mine = _new_tenant()
    await _seed_tenant(sessions, mine)
    await _file_request(db_urls, mine.tenant_id)
    offboarding = SqlTenantOffboarding(session_factory=sessions)
    await offboarding.claim_requested()  # -> 'exporting', updated_at = now()
    await _age_the_request(db_urls, mine.tenant_id, minutes_ago=31)

    reclaimed = await offboarding.claim_requested()

    assert mine.tenant_id in reclaimed


async def test_a_request_still_in_progress_within_the_window_is_left_alone(
    sessions: async_sessionmaker[AsyncSession], db_urls: DatabaseUrls
) -> None:
    """The other side of the same window: a live worker still inside it must
    not have its claim stolen by another poll tick."""
    mine = _new_tenant()
    await _seed_tenant(sessions, mine)
    await _file_request(db_urls, mine.tenant_id)
    offboarding = SqlTenantOffboarding(session_factory=sessions)
    await offboarding.claim_requested()
    await _age_the_request(db_urls, mine.tenant_id, minutes_ago=5)

    reclaimed = await offboarding.claim_requested()

    assert mine.tenant_id not in reclaimed


async def test_mark_status_reports_progress_on_the_tenants_own_row(
    sessions: async_sessionmaker[AsyncSession], db_urls: DatabaseUrls
) -> None:
    mine = _new_tenant()
    await _seed_tenant(sessions, mine)
    await _file_request(db_urls, mine.tenant_id)
    offboarding = SqlTenantOffboarding(session_factory=sessions)
    await offboarding.claim_requested()

    await offboarding.mark_status(mine.tenant_id, "completed", export_key="x/exports/y.zip")

    async with sessions() as session, session.begin():
        await session.execute(
            sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(mine.tenant_id)}
        )
        row = (
            await session.execute(
                sa.text(
                    "SELECT status, export_key FROM platform.tenant_offboarding_requests"
                    " WHERE tenant_id = :t"
                ),
                {"t": str(mine.tenant_id)},
            )
        ).one()
    assert row.status == "completed"
    assert row.export_key == "x/exports/y.zip"


# --- A table narrowed by workspace as well as tenant -------------------------
#
# No platform table is narrowed by workspace; a context's may be, with the policy
# shape `test_rls_coverage.py` holds them to. The probe below is that shape, made
# by the migrator for one test and dropped after it. While it exists it passes
# every guard of `test_rls_coverage.py` and `test_privileges.py` (RLS enabled and
# FORCEd, a `tenant_isolation_` policy on both sides, USAGE for dw_app), because
# `db_urls` is one database for the whole session.

_PROBE_SCHEMA = "offboarding_ws_probe"
_PROBE = f"{_PROBE_SCHEMA}.rows"
_PROBE_POLICY = (
    "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    " AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
    "      OR current_setting('app.workspace_scope', true) = 'tenant')"
)


@dataclass(frozen=True)
class _Probe:
    a: uuid.UUID  # two workspaces, one row in each
    b: uuid.UUID  # one workspace, one row


@pytest.fixture
async def ws_probe(db_urls: DatabaseUrls) -> AsyncIterator[_Probe]:
    a, b = uuid.uuid4(), uuid.uuid4()
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with migrator.begin() as conn:
            for statement in (
                f"CREATE SCHEMA {_PROBE_SCHEMA}",
                f"CREATE TABLE {_PROBE} (tenant_id uuid NOT NULL, workspace_id uuid NOT NULL,"
                " label text NOT NULL)",
                f"ALTER TABLE {_PROBE} ENABLE ROW LEVEL SECURITY",
                f"ALTER TABLE {_PROBE} FORCE ROW LEVEL SECURITY",
                f"CREATE POLICY tenant_isolation_rows ON {_PROBE}"
                f" USING ({_PROBE_POLICY}) WITH CHECK ({_PROBE_POLICY})",
                f"GRANT USAGE ON SCHEMA {_PROBE_SCHEMA} TO dw_app",
                f"GRANT SELECT, DELETE ON {_PROBE} TO dw_app",
            ):
                await conn.execute(sa.text(statement))
            await conn.execute(
                sa.text(
                    f"INSERT INTO {_PROBE} VALUES"
                    " (:a, gen_random_uuid(), 'a1'), (:a, gen_random_uuid(), 'a2'),"
                    " (:b, gen_random_uuid(), 'b1')"
                ),
                {"a": a, "b": b},
            )
        yield _Probe(a=a, b=b)
    finally:
        async with migrator.begin() as conn:
            await conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {_PROBE_SCHEMA} CASCADE"))
        await migrator.dispose()


async def _probe_labels(db_urls: DatabaseUrls, tenant_id: uuid.UUID) -> set[str]:
    """Through the migrator, which bypasses RLS: what is really in the table."""
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with migrator.connect() as conn:
            rows = await conn.execute(
                sa.text(f"SELECT label FROM {_PROBE} WHERE tenant_id = :t"), {"t": tenant_id}
            )
            return {row.label for row in rows}
    finally:
        await migrator.dispose()


async def test_export_reads_every_workspace_of_the_tenant(
    sessions: async_sessionmaker[AsyncSession], ws_probe: _Probe
) -> None:
    exported = await SqlTenantOffboarding(sessions).export_rows(ws_probe.a)

    (probe,) = [t for t in exported if (t.schema, t.table) == (_PROBE_SCHEMA, "rows")]
    assert {row["label"] for row in probe.rows} == {"a1", "a2"}


async def test_purge_deletes_in_every_workspace_and_leaves_the_other_tenant(
    db_urls: DatabaseUrls, sessions: async_sessionmaker[AsyncSession], ws_probe: _Probe
) -> None:
    await SqlTenantOffboarding(sessions).purge_rows(ws_probe.a)

    assert await _probe_labels(db_urls, ws_probe.a) == set()
    assert await _probe_labels(db_urls, ws_probe.b) == {"b1"}


async def test_the_workspace_scope_never_crosses_tenants(
    sessions: async_sessionmaker[AsyncSession], ws_probe: _Probe
) -> None:
    """Tenant B with the scope set sees all of its own rows and none of A's."""
    async with sessions() as session, session.begin():
        await session.execute(
            sa.text(
                "SELECT set_config('app.tenant_id', :t, true),"
                "       set_config('app.workspace_scope', 'tenant', true)"
            ),
            {"t": str(ws_probe.b)},
        )
        labels = {
            row.label for row in await session.execute(sa.text(f"SELECT label FROM {_PROBE}"))
        }
    assert labels == {"b1"}


async def test_the_workspace_scope_ends_with_the_transaction(
    db_urls: DatabaseUrls, ws_probe: _Probe
) -> None:
    """One pooled connection: the next borrower must not inherit the scope."""
    engine = create_async_engine(db_urls.app, pool_size=1, max_overflow=0)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            before = (await session.execute(sa.text("SELECT pg_backend_pid()"))).scalar_one()
        await SqlTenantOffboarding(factory).export_rows(ws_probe.a)
        async with factory() as session:
            after = (await session.execute(sa.text("SELECT pg_backend_pid()"))).scalar_one()
            scope = (
                await session.execute(
                    sa.text("SELECT current_setting('app.workspace_scope', true)")
                )
            ).scalar_one()
    finally:
        await engine.dispose()

    assert after == before, "a second connection was used, so this proves nothing"
    assert scope in (None, ""), f"the next transaction inherited app.workspace_scope={scope!r}"
