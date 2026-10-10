"""Integration: run checkpoints are pruned on the retention term, and only those.

What only a real database shows: the sweep runs across tenants under the drain,
sees runs through `worker_drain_worker_runs`, keeps every checkpoint of a thread
with a run still waiting, and deletes nothing it was not asked to.

Every thread here is on a tenant of its own, and rows are aged relative to the
real clock rather than a fixed date: `urls` is one database for the whole
session. Other files write checkpoints at `now()` under the shared tenants and
assert on what those tenants can see (`test_checkpoints_are_tenant_isolated`
counts tenant B's rows — measured: it went red while this file wrote under
TENANT_B), so this file writes under none of their ids, and ages nothing of
theirs past the term.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from runtime_harness import REPO_ROOT, RuntimeUrls
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dw_agent_runtime.adapters.checkpoint_retention import SqlCheckpointRetention
from dw_platform.retention_policy import RetentionPolicy, load_retention_policy

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC)
POLICY = load_retention_policy(Path(REPO_ROOT) / "configs" / "policies" / "retention@1.7.0.yaml")
SUPERSEDED = POLICY.checkpoints.superseded_days
IDLE = POLICY.checkpoints.idle_thread_days


@dataclass(frozen=True)
class _Clock:
    def now(self) -> datetime:
        return NOW


@pytest.fixture
async def sessions(urls: RuntimeUrls) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(urls.app, poolclass=NullPool)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def migrator(urls: RuntimeUrls) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Counts as the migrator, which reads past RLS: a count through `dw_app`
    would be scoped by the very policies under test."""
    engine = create_async_engine(urls.migrator, poolclass=NullPool)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def _thread(
    sessions: async_sessionmaker[AsyncSession],
    *,
    tenant: uuid.UUID | None = None,
    statuses: tuple[str, ...] = ("completed",),
    ages_days: tuple[int, ...] = (30, 29, 28, 27, 26),
) -> uuid.UUID:
    """A thread with one run per status and one checkpoint (plus a write) per age,
    on a tenant of its own unless one is given."""
    thread = uuid.uuid4()
    tenant = tenant or uuid.uuid4()
    workspace = uuid.uuid4()
    async with sessions() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant)}
        )
        for status in statuses:
            await session.execute(
                text(
                    "INSERT INTO platform.worker_runs"
                    " (id, thread_id, tenant_id, workspace_id, worker_id, worker_version,"
                    "  graph_version, status, requested_by, created_at, updated_at)"
                    " VALUES (gen_random_uuid(), :th, :t, :w, 'demo', '1.0.0', '1.0.0',"
                    "  :s, gen_random_uuid(), now(), now())"
                ),
                {"th": thread, "t": tenant, "w": workspace, "s": status},
            )
        for i, age in enumerate(ages_days):
            # Monotonic ids, the way LangGraph's are: the newest sorts last.
            checkpoint_id = f"ckpt-{i:04d}"
            await session.execute(
                text(
                    "INSERT INTO platform.run_checkpoints"
                    " (thread_id, checkpoint_ns, checkpoint_id, tenant_id, workspace_id,"
                    "  type, checkpoint, metadata, created_at)"
                    " VALUES (:th, '', :c, :t, :w, 'msgpack', '\\x00', '\\x00', :at)"
                ),
                {
                    "th": thread,
                    "c": checkpoint_id,
                    "t": tenant,
                    "w": workspace,
                    "at": NOW - timedelta(days=age),
                },
            )
            await session.execute(
                text(
                    "INSERT INTO platform.run_checkpoint_writes"
                    " (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, tenant_id,"
                    "  workspace_id, channel, type, value)"
                    " VALUES (:th, '', :c, 'task', 0, :t, :w, 'messages', 'msgpack', '\\x00')"
                ),
                {"th": thread, "c": checkpoint_id, "t": tenant, "w": workspace},
            )
    return thread


async def _left(
    migrator: async_sessionmaker[AsyncSession], thread: uuid.UUID
) -> tuple[list[str], list[str]]:
    async with migrator() as session:
        checkpoints = (
            await session.scalars(
                text(
                    "SELECT checkpoint_id FROM platform.run_checkpoints"
                    " WHERE thread_id = :th ORDER BY checkpoint_id"
                ),
                {"th": thread},
            )
        ).all()
        writes = (
            await session.scalars(
                text(
                    "SELECT checkpoint_id FROM platform.run_checkpoint_writes"
                    " WHERE thread_id = :th ORDER BY checkpoint_id"
                ),
                {"th": thread},
            )
        ).all()
    return list(checkpoints), list(writes)


def _sweep(
    sessions: async_sessionmaker[AsyncSession], policy: RetentionPolicy = POLICY
) -> SqlCheckpointRetention:
    return SqlCheckpointRetention(session_factory=sessions, policy=policy, clock=_Clock())


async def _drain(sweep: SqlCheckpointRetention) -> None:
    """Passes until nothing is left to delete: other files' old rows, if any,
    must not decide whether this file's rows were reached in one batch."""
    for _ in range(50):
        await sweep.prune()


async def test_a_finished_thread_keeps_only_its_newest_checkpoint(
    sessions: async_sessionmaker[AsyncSession], migrator: async_sessionmaker[AsyncSession]
) -> None:
    thread = await _thread(sessions)

    await _drain(_sweep(sessions))

    assert await _left(migrator, thread) == (["ckpt-0004"], ["ckpt-0004"])


async def test_a_thread_idle_past_its_term_goes_entirely(
    sessions: async_sessionmaker[AsyncSession], migrator: async_sessionmaker[AsyncSession]
) -> None:
    thread = await _thread(sessions, ages_days=(IDLE + 50, IDLE + 40, IDLE + 1))

    await _drain(_sweep(sessions))

    assert await _left(migrator, thread) == ([], [])


@pytest.mark.parametrize("waiting", ["waiting_approval", "running", "pending"])
async def test_a_thread_with_a_run_still_waiting_loses_nothing(
    sessions: async_sessionmaker[AsyncSession],
    migrator: async_sessionmaker[AsyncSession],
    waiting: str,
) -> None:
    """A finished earlier run beside it does not make it settled: the paused
    run resumes from these rows, idle term or not."""
    thread = await _thread(
        sessions, statuses=("completed", waiting), ages_days=(IDLE + 50, IDLE + 40, IDLE + 1)
    )

    await _drain(_sweep(sessions))

    kept = ["ckpt-0000", "ckpt-0001", "ckpt-0002"]
    assert await _left(migrator, thread) == (kept, kept)


async def test_checkpoints_within_the_term_are_left_alone(
    sessions: async_sessionmaker[AsyncSession], migrator: async_sessionmaker[AsyncSession]
) -> None:
    fresh = SUPERSEDED - 1
    thread = await _thread(sessions, ages_days=(fresh, fresh, fresh))

    await _drain(_sweep(sessions))

    kept = ["ckpt-0000", "ckpt-0001", "ckpt-0002"]
    assert await _left(migrator, thread) == (kept, kept)


async def test_the_cross_tenant_pass_deletes_only_by_its_conditions(
    sessions: async_sessionmaker[AsyncSession], migrator: async_sessionmaker[AsyncSession]
) -> None:
    """One pass, two tenants: A's expired rows go, B's rows that are within
    the term or behind a waiting run stay — the drain widens what the pass
    sees, never what it may delete."""
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    mine = await _thread(sessions, tenant=tenant_a)
    theirs_fresh = await _thread(sessions, tenant=tenant_b, ages_days=(2, 1))
    theirs_waiting = await _thread(
        sessions, tenant=tenant_b, statuses=("waiting_approval",), ages_days=(IDLE + 5, 30)
    )

    await _drain(_sweep(sessions))

    assert await _left(migrator, mine) == (["ckpt-0004"], ["ckpt-0004"])
    both = ["ckpt-0000", "ckpt-0001"]
    assert await _left(migrator, theirs_fresh) == (both, both)
    assert await _left(migrator, theirs_waiting) == (both, both)


async def test_a_thread_with_no_run_row_at_all_is_kept(
    sessions: async_sessionmaker[AsyncSession], migrator: async_sessionmaker[AsyncSession]
) -> None:
    """The price of failing closed: "no unfinished run" with no run visible is
    also what a missing drain policy looks like, so a thread is pruned only when
    a finished run is visible too. Offboarding still removes these."""
    thread = await _thread(sessions, statuses=(), ages_days=(IDLE + 5, IDLE + 1))

    await _drain(_sweep(sessions))

    both = ["ckpt-0000", "ckpt-0001"]
    assert await _left(migrator, thread) == (both, both)


async def test_one_pass_deletes_at_most_one_batch(
    sessions: async_sessionmaker[AsyncSession], migrator: async_sessionmaker[AsyncSession]
) -> None:
    """Bounded, oldest first: a backlog drains over passes instead of in one
    DELETE that locks the table while a run is writing to it."""
    await _drain(_sweep(sessions))  # anything another test left eligible
    thread = await _thread(sessions)

    await _sweep(sessions, POLICY.model_copy(update={"batch_limit": 2})).prune()

    left, _ = await _left(migrator, thread)
    assert left == ["ckpt-0002", "ckpt-0003", "ckpt-0004"]
