"""Every tenant table has RLS — asked of the catalog, not of the migrations.

`scripts/verify_invariants.py` reads migration TEXT, which is the right thing
for a check that runs without a database and catches a `CREATE TABLE` that
forgot its policy. It cannot see a table the text never names.

A partition is exactly that table. `ALTER TABLE ... ENABLE ROW LEVEL SECURITY`
on a partitioned parent applies its policies to rows reached THROUGH the parent;
a partition addressed by its own name uses its own settings. Both default
partitions had none, and `dw_app` holds SELECT on them, so this returned every
tenant's rows with no `app.tenant_id` set at all:

    SELECT count(*) FROM platform.audit_events_default;

Found by trying it. This test is what makes the next one fail loudly — including
a partition added months from now, which Postgres will not police on its own.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
import sqlalchemy as sa
from pg_harness import DatabaseUrls
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.integration

# Every table with a `tenant_id` column, in whatever schema it lives. No schema
# is named: a hand-kept list of schemas is blind to the one added after it, the
# same blindness as the text checker above, one level up. `pg_*` is dropped
# whole because Postgres refuses to create a schema with that prefix, so the
# exclusion hides nothing a migration can make — it removes `pg_catalog`,
# `pg_toast*` and other sessions' `pg_temp_*`.
_TENANT_TABLES = sa.text(
    """
    SELECT n.nspname AS schema, c.relname AS name,
           c.relrowsecurity AS enabled, c.relforcerowsecurity AS forced
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r', 'p')
      AND NOT starts_with(n.nspname, 'pg_')
      AND n.nspname <> 'information_schema'
      AND EXISTS (
          SELECT 1 FROM pg_attribute a
          WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
      )
    ORDER BY 1, 2
    """
)

# The floor discovery must clear, not the list: the schemas the platform itself
# ships. A discovery query gone wrong fails here instead of returning nothing
# and passing every check built on top of it.
_PLATFORM_SCHEMAS = frozenset({"platform", "knowledge", "memory"})


async def tenant_tables(conn: AsyncConnection | AsyncSession) -> Sequence[sa.Row[Any]]:
    """Every tenant table the catalog holds — the one answer to "which tables,
    in which schemas", shared with `test_privileges.py`."""
    rows = (await conn.execute(_TENANT_TABLES)).all()
    assert rows, "found no tenant tables at all — the query is wrong, not the schema"
    missed = _PLATFORM_SCHEMAS - {r.schema for r in rows}
    assert not missed, f"schema discovery missed {sorted(missed)} — the query is wrong"
    return rows


async def tenant_schemas(conn: AsyncConnection | AsyncSession) -> list[str]:
    return sorted({r.schema for r in await tenant_tables(conn)})


_POLICIES = sa.text(
    "SELECT schemaname, tablename, policyname, qual, with_check"
    " FROM pg_policies WHERE schemaname = ANY(:schemas)"
)

# The trusted per-transaction settings a policy may narrow by. All three are set
# by the backend from a verified context and none can be supplied by a caller.
#
# `app.tenant_id` is the ordinary one. `app.principal_id` exists because identity
# bootstrap has to read your own membership BEFORE a tenant is resolved — there
# is no tenant to filter by yet. `app.worker_drain` is the background drain's
# deliberate escape hatch, set only by a process draining queues across tenants.
_TRUSTED_SETTINGS = (
    "current_setting('app.tenant_id'",
    "current_setting('app.principal_id'",
    "current_setting('app.worker_drain'",
)

# Policies that narrow by no setting at all. Each needs a reason, and the reason
# is what a reviewer checks — not the entry's existence. A new policy that
# isolates nothing fails this suite until somebody writes down why it should not.
_UNSCOPED_ON_PURPOSE: dict[tuple[str, str], str] = {
    ("documents", "knowledge_global_read_documents"): (
        "scope='global' is a shared corpus every tenant may read by design; "
        "publishing into it takes the knowledge.publish_global scope"
    ),
}


@pytest.fixture
async def session(db_urls: DatabaseUrls) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(db_urls.migrator, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as opened:
            yield opened
    finally:
        await engine.dispose()


async def test_rls_covers_every_tenant_table(session: AsyncSession) -> None:
    """Including partitions, which the text-based checker cannot see — and
    including schemas nobody listed. The tables come from the catalog, every
    non-system schema with a `tenant_id` column, because a hand-kept tuple of
    schema names goes blind to the next schema exactly the way the text checker
    went blind to partitions: a table there without RLS would stay green."""
    rows = await tenant_tables(session)

    missing = [f"{r.schema}.{r.name}" for r in rows if not r.enabled]
    forced_off = [f"{r.schema}.{r.name}" for r in rows if r.enabled and not r.forced]

    assert missing == [], f"tenant tables without RLS enabled: {missing}"
    # FORCE is what stops the owning role reading straight past the policy, and
    # the owner is who runs migrations and maintenance.
    assert forced_off == [], f"tenant tables with RLS but not FORCEd: {forced_off}"


async def test_every_tenant_table_actually_has_a_policy(session: AsyncSession) -> None:
    """RLS with no policy denies everything, which is safe and unusable — and
    RLS with a policy on the parent only is what this suite exists to catch."""
    rows = await tenant_tables(session)
    schemas = sorted({r.schema for r in rows})
    policed = {
        (r.schemaname, r.tablename)
        for r in (await session.execute(_POLICIES, {"schemas": schemas})).all()
    }

    without = [f"{r.schema}.{r.name}" for r in rows if (r.schema, r.name) not in policed]

    assert without == [], f"tenant tables with no policy of their own: {without}"


async def test_a_partition_cannot_be_read_around_its_parent(
    db_urls: DatabaseUrls, session: AsyncSession
) -> None:
    """The leak itself, reproduced end to end.

    Written as `dw_app` over its OWN connection rather than `SET ROLE`: the
    migrator cannot assume that role, and more to the point the runtime never
    does either — it connects as `dw_app`, which is the identity whose reach
    this is about.
    """
    tenant = uuid.uuid4()
    await session.execute(
        sa.text(
            "INSERT INTO platform.audit_events"
            " (id, tenant_id, workspace_id, actor_id, action, resource_type,"
            "  resource_id, occurred_at)"
            " VALUES (gen_random_uuid(), :t, gen_random_uuid(), gen_random_uuid(),"
            "         'rls.probe', 'probe', 'x', now())"
        ),
        {"t": tenant},
    )
    await session.commit()

    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            # No `app.tenant_id` set at all — the state a connection is in before
            # anything scopes it, and the state a bug would leave it in.
            through_parent = (
                await conn.execute(sa.text("SELECT count(*) FROM platform.audit_events"))
            ).scalar_one()
            direct = (
                await conn.execute(sa.text("SELECT count(*) FROM platform.audit_events_default"))
            ).scalar_one()
    finally:
        await engine.dispose()

    assert through_parent == 0
    assert direct == 0, "the partition returned rows the parent refused"


async def test_every_policy_actually_consults_the_tenant_setting(
    session: AsyncSession,
) -> None:
    """Existence is not isolation.

    `USING (true)` is a policy. So is one filtering on a column nobody sets. What
    isolates is a predicate reading a setting the backend controls — asserted on
    the read side AND the write side, because a policy that reads correctly and
    writes freely lets one tenant insert rows into another's table.

    Three settings count, not one: identity bootstrap narrows by principal
    because no tenant is resolved yet, and the background drain narrows by its
    own flag. Anything narrowing by none of them needs a written reason.
    """
    rows = (await session.execute(_POLICIES, {"schemas": await tenant_schemas(session)})).all()
    assert rows, "no policies at all — the query is wrong, not the schema"

    def narrows(predicate: str | None) -> bool:
        return predicate is not None and any(s in predicate for s in _TRUSTED_SETTINGS)

    blind_reads = [
        f"{r.schemaname}.{r.tablename}.{r.policyname}"
        for r in rows
        if not narrows(r.qual) and (r.tablename, r.policyname) not in _UNSCOPED_ON_PURPOSE
    ]
    blind_writes = [
        f"{r.schemaname}.{r.tablename}.{r.policyname}"
        for r in rows
        if r.with_check is not None and not narrows(r.with_check)
    ]

    assert blind_reads == [], (
        f"policies narrowing by no trusted setting and with no written reason: {blind_reads}"
    )
    assert blind_writes == [], f"policies that do not filter writes: {blind_writes}"


async def test_a_connection_that_never_scopes_itself_reads_nothing(
    db_urls: DatabaseUrls, session: AsyncSession
) -> None:
    """The contract any language has to honour to be allowed near this database.

    `dw_app` does not bypass RLS, so a connection that never sets
    `app.tenant_id` should see zero rows — whatever wrote them, and whatever
    language is asking. This is what makes the database, rather than one
    application, the place tenant isolation lives.
    """
    tenant = uuid.uuid4()
    await session.execute(
        sa.text(
            "INSERT INTO platform.audit_events"
            " (id, tenant_id, workspace_id, actor_id, action, resource_type,"
            "  resource_id, occurred_at)"
            " VALUES (gen_random_uuid(), :t, gen_random_uuid(), gen_random_uuid(),"
            "         'rls.unscoped', 'probe', 'x', now())"
        ),
        {"t": tenant},
    )
    await session.commit()

    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            seen = (
                await conn.execute(
                    sa.text("SELECT count(*) FROM platform.audit_events WHERE action = :a"),
                    {"a": "rls.unscoped"},
                )
            ).scalar_one()
            # And the row IS there — asserted through a role that may bypass, so
            # the zero above is isolation rather than an empty table.
            assert seen == 0, "an unscoped connection read a tenant's rows"
    finally:
        await engine.dispose()

    still_there = (
        await session.execute(
            sa.text("SELECT count(*) FROM platform.audit_events WHERE action = :a"),
            {"a": "rls.unscoped"},
        )
    ).scalar_one()
    assert still_there == 1, "the probe row vanished, so the zero above proved nothing"


# --- The workspace scope: a setting that widens, so it is never trusted alone --
#
# A context may narrow its tables by workspace as well as tenant. The offboarding
# lane sets `app.workspace_scope = 'tenant'` to read every workspace of the one
# tenant it is working on, and nothing else sets it. A policy may read the scope
# only as the alternative to its workspace clause, inside an AND whose other side
# is the tenant clause:
#
#     tenant_id = <app.tenant_id> AND (workspace_id = <app.workspace_id>
#                                      OR <app.workspace_scope> = 'tenant')
#
# Read anywhere else, it opens a tenant's rows to any transaction that sets it.
# The rules below apply to every schema `tenant_schemas` discovers.
_TENANT_SETTING = "current_setting('app.tenant_id'"
_WORKSPACE_SETTING = "current_setting('app.workspace_id'"
_SCOPE_SETTING = "current_setting('app.workspace_scope'"


def _closing(expr: str, opening: int) -> int:
    """Index of the parenthesis closing the one at `opening`, skipping quoted
    literals (Postgres doubles a quote inside one)."""
    depth, i, quoted = 0, opening, False
    while i < len(expr):
        c = expr[i]
        if quoted:
            if c == "'" and expr[i + 1 : i + 2] == "'":
                i += 1
            elif c == "'":
                quoted = False
        elif c == "'":
            quoted = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise AssertionError(f"unbalanced policy expression: {expr}")


def _top_level(expr: str) -> tuple[str | None, list[str]]:
    """The outermost operator of a policy expression and its operands.

    Postgres prints a policy fully parenthesised (measured on 16: `a AND b OR c`
    comes back as `((a AND b) OR c)`), so once the parentheses wrapping the whole
    expression come off, the operators left at depth zero are all one operator.
    The operator is None for a single term.
    """
    s = expr.strip()
    while s.startswith("(") and _closing(s, 0) == len(s) - 1:
        s = s[1:-1].strip()
    operator: str | None = None
    parts: list[str] = []
    depth, start, i, quoted = 0, 0, 0, False
    while i < len(s):
        c = s[i]
        if quoted:
            if c == "'" and s[i + 1 : i + 2] == "'":
                i += 1
            elif c == "'":
                quoted = False
        elif c == "'":
            quoted = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif depth == 0:
            word = next((w for w in (" AND ", " OR ") if s.startswith(w, i)), None)
            if word is not None:
                found = word.strip()
                assert operator in (None, found), f"mixed operators at one level: {expr}"
                operator = found
                parts.append(s[start:i].strip())
                i += len(word)
                start = i
                continue
        i += 1
    parts.append(s[start:].strip())
    return operator, parts


def scope_outside_a_tenant_clause(policies: Sequence[sa.Row[Any]]) -> list[str]:
    """Policies that read the scope anywhere but beside a pure tenant clause."""
    bad = []
    for r in policies:
        for expr in (r.qual, r.with_check):
            if expr is None or _SCOPE_SETTING not in expr:
                continue
            operator, parts = _top_level(expr)
            tenant_clause = [
                p
                for p in parts
                if _TENANT_SETTING in p and _SCOPE_SETTING not in p and _top_level(p)[0] != "OR"
            ]
            if operator != "AND" or not tenant_clause:
                bad.append(f"{r.schemaname}.{r.tablename}.{r.policyname}")
                break
    return bad


def workspace_tables_blind_to_scope(policies: Sequence[sa.Row[Any]]) -> list[str]:
    """Tables narrowed by workspace with no policy reading the scope: the
    offboarding lane would export none of their rows, delete none, and the purge
    of the tenant's workspaces would then CASCADE them away unexported."""

    def reads(r: sa.Row[Any], setting: str) -> bool:
        return any(e is not None and setting in e for e in (r.qual, r.with_check))

    narrowed = {f"{r.schemaname}.{r.tablename}" for r in policies if reads(r, _WORKSPACE_SETTING)}
    scoped = {f"{r.schemaname}.{r.tablename}" for r in policies if reads(r, _SCOPE_SETTING)}
    return sorted(narrowed - scoped)


async def test_the_workspace_scope_only_ever_sits_beside_a_tenant_clause(
    session: AsyncSession,
) -> None:
    rows = (await session.execute(_POLICIES, {"schemas": await tenant_schemas(session)})).all()
    assert scope_outside_a_tenant_clause(rows) == [], (
        "policies reading app.workspace_scope outside `tenant AND (workspace OR scope)`"
    )


async def test_a_workspace_narrowed_table_lets_offboarding_read_every_workspace(
    session: AsyncSession,
) -> None:
    rows = (await session.execute(_POLICIES, {"schemas": await tenant_schemas(session)})).all()
    assert workspace_tables_blind_to_scope(rows) == [], (
        "tables narrowed by app.workspace_id with no policy reading app.workspace_scope"
    )


_TENANT = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
_WORKSPACE = "workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
_SCOPE = "current_setting('app.workspace_scope', true) = 'tenant'"


async def test_the_scope_rules_catch_the_wrong_shapes(session: AsyncSession) -> None:
    """The two rules above passed vacuously while no table was narrowed by
    workspace (`supply_chain.case_documents` is the first, migration
    f6a8142a6cd2), and a pass on the right shape still proves nothing about
    catching a wrong one. These are real Postgres-printed policies of each
    shape, made and rolled back in one transaction."""
    shapes = {
        "spec": f"{_TENANT} AND ({_WORKSPACE} OR {_SCOPE})",
        "tenant_or_scope": f"{_TENANT} OR {_SCOPE}",
        "unparenthesised": f"{_TENANT} AND {_WORKSPACE} OR {_SCOPE}",
        "weak_tenant": f"({_TENANT} OR {_SCOPE}) AND ({_WORKSPACE} OR {_SCOPE})",
        "scope_alone": _SCOPE,
        # A tenant clause widened by something other than the scope.
        "loose_tenant": f"({_TENANT} OR workspace_id IS NULL) AND ({_WORKSPACE} OR {_SCOPE})",
    }
    probe, blind = "public.scope_rules_probe", "public.scope_rules_blind"
    try:
        for table in (probe, blind):
            await session.execute(
                sa.text(f"CREATE TABLE {table} (tenant_id uuid, workspace_id uuid)")
            )
        for name, using in shapes.items():
            await session.execute(sa.text(f"CREATE POLICY {name} ON {probe} USING ({using})"))
        await session.execute(
            sa.text(
                f"CREATE POLICY write_side ON {probe} FOR INSERT WITH CHECK ({_TENANT} OR {_SCOPE})"
            )
        )
        await session.execute(
            sa.text(f"CREATE POLICY ws_only ON {blind} USING ({_TENANT} AND {_WORKSPACE})")
        )
        rows = [
            r
            for r in (await session.execute(_POLICIES, {"schemas": ["public"]})).all()
            if r.tablename.startswith("scope_rules_")
        ]

        assert sorted(scope_outside_a_tenant_clause(rows)) == [
            "public.scope_rules_probe.loose_tenant",
            "public.scope_rules_probe.scope_alone",
            "public.scope_rules_probe.tenant_or_scope",
            "public.scope_rules_probe.unparenthesised",
            "public.scope_rules_probe.weak_tenant",
            "public.scope_rules_probe.write_side",
        ]
        assert workspace_tables_blind_to_scope(rows) == ["public.scope_rules_blind"]
    finally:
        await session.rollback()
