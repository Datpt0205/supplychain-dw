"""Integration: the fixed-set text columns of memory refuse a value off the set.

`memory_type`, `classification` and `decision` held any text. The service only
writes enum values, but a service is not what a second writer, a repair script
or a bug goes through (`failure-modes.md` #7), and `classification` is what
recall's clearance filter reads: a row saying `public` is a fact nobody can
recall, or one a later reader ranks by guessing.

Written as the migrator on purpose. It bypasses RLS and owns the tables, so
the only thing left between it and a bad row is the constraint under test.

The CHECKs are a second copy of three sets the code owns (`MemoryType`,
`WriteDecision`, the clearance ladder). A copy that cannot be avoided has to
disagree loudly, so the last test reads the constraints back from the catalog
and compares them with the code.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from runtime_harness import RuntimeUrls
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from dw_knowledge.contracts import CLASSIFICATIONS
from dw_memory import tables
from dw_memory.contracts import MemoryType, WriteDecision

pytestmark = pytest.mark.integration

TENANT = uuid.UUID(int=0xCE00)
WORKSPACE = uuid.UUID(int=0xCE01)


@pytest.fixture
async def migrator(urls: RuntimeUrls) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(urls.migrator, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _run(engine: AsyncEngine) -> uuid.UUID:
    run_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO platform.worker_runs"
                " (id, thread_id, tenant_id, workspace_id, worker_id, worker_version,"
                "  graph_version, requested_by)"
                " VALUES (:id, :id, :t, :w, 'demo', '1.0.0', '1.0.0', :actor)"
            ),
            {"id": run_id, "t": TENANT, "w": WORKSPACE, "actor": uuid.uuid4()},
        )
    return run_id


def _item(run_id: uuid.UUID, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "memory_id": uuid.uuid4(),
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "worker_id": "demo",
        "memory_type": "semantic",
        "content": "x",
        "provenance_refs": [{"evidence_id": "x"}],
        "confidence": 0.5,
        "classification": "internal",
        "valid_from": sa.func.now(),
        "retention_policy": "default",
        "memory_schema_version": "1.0.0",
        "created_by_run_id": run_id,
    }
    row.update(overrides)
    return row


def _candidate(run_id: uuid.UUID, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "worker_id": "demo",
        "memory_type": "semantic",
        "content": "x",
        "confidence": 0.5,
        "classification": "internal",
        "decision": "review",
        "created_by_run_id": run_id,
    }
    row.update(overrides)
    return row


async def _insert(engine: AsyncEngine, table: str, row: dict[str, object]) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.insert(getattr(tables, table)).values(**row))


@pytest.mark.parametrize(
    ("table", "column", "value", "constraint"),
    [
        ("items", "memory_type", "bogus", "ck_items_memory_type"),
        ("items", "classification", "public", "ck_items_classification"),
        ("write_candidates", "memory_type", "bogus", "ck_write_candidates_memory_type"),
        ("write_candidates", "classification", "public", "ck_write_candidates_classification"),
        ("write_candidates", "decision", "maybe", "ck_write_candidates_decision"),
    ],
)
async def test_a_value_off_the_set_is_refused_by_name(
    migrator: AsyncEngine, table: str, column: str, value: str, constraint: str
) -> None:
    run_id = await _run(migrator)
    build = _item if table == "items" else _candidate
    # The well-formed row goes in first, so a refusal below is the column's and
    # not some other constraint this fixture happens to trip.
    await _insert(migrator, table, build(run_id))

    with pytest.raises(sa.exc.IntegrityError, match=constraint):
        await _insert(migrator, table, build(run_id, **{column: value}))


def _allowed(definition: str) -> set[str]:
    """The literals of `col = ANY (ARRAY['a'::text, ...])` as Postgres prints it."""
    return set(re.findall(r"'([^']*)'::text", definition))


async def test_the_checks_hold_exactly_the_sets_the_code_owns(migrator: AsyncEngine) -> None:
    """A value added to `MemoryType` and not to the CHECK is a memory the
    database refuses in production; one dropped from the enum and left in the
    CHECK is a row the code can no longer read. Either goes red here."""
    async with migrator.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(c.oid) AS definition"
                    " FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace"
                    " WHERE n.nspname = 'memory' AND c.contype = 'c'"
                )
            )
        ).all()
    found = {row.conname: _allowed(row.definition) for row in rows}

    memory_types = {member.value for member in MemoryType}
    classifications = set(CLASSIFICATIONS)
    assert found.get("ck_items_memory_type") == memory_types
    assert found.get("ck_write_candidates_memory_type") == memory_types
    assert found.get("ck_items_classification") == classifications
    assert found.get("ck_write_candidates_classification") == classifications
    assert found.get("ck_write_candidates_decision") == {member.value for member in WriteDecision}
