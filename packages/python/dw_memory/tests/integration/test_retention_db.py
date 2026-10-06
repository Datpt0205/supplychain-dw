"""Retention against real Postgres: what goes, what stays, and what must never go.

`retention_policy` was a column every row wrote and nothing read. These prove it
is now load-bearing — and prove the two cases where deleting would be the bug:
a class held for a legal obligation, and a class this build has never heard of.

Against a database because the sweep runs under `app.worker_drain`, and whether
that GUC actually opens the rows is a question only the policy engine answers.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from runtime_harness import RuntimeUrls
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dw_memory import tables
from dw_memory.adapters.qdrant_ranker import QdrantMemoryRanker
from dw_memory.retention import SqlMemoryRetention
from dw_platform.retention_policy import (
    AuditRetention,
    KnowledgeRetention,
    RetentionClass,
    RetentionPolicy,
)

pytestmark = pytest.mark.integration

TENANT = uuid.UUID(int=0xEE00)
OTHER_TENANT = uuid.UUID(int=0xEE01)
WORKSPACE = uuid.UUID(int=0xEE02)
NOW = datetime(2026, 9, 18, tzinfo=UTC)


@dataclass(frozen=True)
class _Clock:
    def now(self) -> datetime:
        return NOW


def _policy(**overrides: object) -> RetentionPolicy:
    fields: dict[str, object] = {
        "schema_version": "1.0",
        "policy_id": "retention",
        "policy_version": "1.0.0",
        "classes": {
            "default": RetentionClass(days=730, description="thường"),
            "ephemeral": RetentionClass(days=30, description="ngắn"),
            "legal_hold": RetentionClass(days=None, description="giữ vô hạn"),
        },
        "knowledge": KnowledgeRetention(deleted_grace_days=30, orphan_evidence_grace_days=7),
        "audit": AuditRetention(months_ahead=1, enforced=False, tables={}),
        "checkpoints": {"superseded_days": 7, "idle_thread_days": 730},
        "batch_limit": 1000,
    }
    fields.update(overrides)
    return RetentionPolicy.model_validate(fields)


@pytest.fixture
async def sweep(
    urls: RuntimeUrls,
) -> AsyncIterator[tuple[SqlMemoryRetention, async_sessionmaker[AsyncSession]]]:
    engine = create_async_engine(urls.app, poolclass=NullPool)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield SqlMemoryRetention(sessions, _policy(), _Clock(), vector_index=None), sessions
    finally:
        await engine.dispose()


async def _memory(
    sessions: async_sessionmaker[AsyncSession],
    *,
    retention: str,
    age_days: int,
    tenant: uuid.UUID = TENANT,
) -> uuid.UUID:
    memory_id, run_id = uuid.uuid4(), uuid.uuid4()
    async with sessions() as session, session.begin():
        await session.execute(
            sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant)}
        )
        # A real run: `fk_items_created_by_run_id_worker_runs` refuses a memory
        # that names a run nobody made. Another Mốc 4 constraint catching the
        # fixture rather than the code.
        await session.execute(
            sa.text(
                "INSERT INTO platform.worker_runs"
                " (id, thread_id, tenant_id, workspace_id, worker_id, worker_version,"
                "  graph_version, requested_by)"
                " VALUES (:r, :r, :t, :w, 'demo', '1.0.0', '1.0.0', gen_random_uuid())"
            ),
            {"r": run_id, "t": tenant, "w": WORKSPACE},
        )
        await session.execute(
            sa.insert(tables.items).values(
                memory_id=memory_id,
                tenant_id=tenant,
                workspace_id=WORKSPACE,
                worker_id="demo",
                memory_type="semantic",
                subject_refs=[],
                content="x",
                structured_facts={},
                # Non-empty: `ck_items_provenance_refs` refuses a memory that
                # cites nothing, which is the constraint doing its job. Shaped
                # like a real ref but pointing nowhere — retention never walks it,
                # and `item_evidence` is what ties a citation to a real row.
                provenance_refs=[{"evidence_id": str(uuid.uuid4())}],
                confidence=0.9,
                classification="internal",
                valid_from=NOW - timedelta(days=age_days),
                retention_policy=retention,
                memory_schema_version="1.0.0",
                created_by_run_id=run_id,
                created_at=NOW - timedelta(days=age_days),
            )
        )
    return memory_id


async def _alive(sessions: async_sessionmaker[AsyncSession], memory_id: uuid.UUID) -> bool:
    async with sessions() as session, session.begin():
        await session.execute(sa.text("SELECT set_config('app.worker_drain', 'on', true)"))
        found = await session.execute(
            sa.select(tables.items.c.memory_id).where(tables.items.c.memory_id == memory_id)
        )
        return found.first() is not None


async def test_a_memory_past_its_term_is_deleted(
    sweep: tuple[SqlMemoryRetention, async_sessionmaker[AsyncSession]],
) -> None:
    pruner, sessions = sweep
    old = await _memory(sessions, retention="ephemeral", age_days=40)

    await pruner.prune()

    assert not await _alive(sessions, old)


async def test_a_memory_inside_its_term_is_kept(
    sweep: tuple[SqlMemoryRetention, async_sessionmaker[AsyncSession]],
) -> None:
    pruner, sessions = sweep
    recent = await _memory(sessions, retention="ephemeral", age_days=10)

    await pruner.prune()

    assert await _alive(sessions, recent)


async def test_legal_hold_is_never_swept_however_old(
    sweep: tuple[SqlMemoryRetention, async_sessionmaker[AsyncSession]],
) -> None:
    """A class with no term is not "a very long term". Deleting something held
    for a legal obligation is the one mistake here that cannot be undone."""
    pruner, sessions = sweep
    ancient = await _memory(sessions, retention="legal_hold", age_days=10_000)

    await pruner.prune()

    assert await _alive(sessions, ancient)


async def test_the_sweep_only_touches_classes_the_policy_names(
    sweep: tuple[SqlMemoryRetention, async_sessionmaker[AsyncSession]],
) -> None:
    """A row naming a class this build has never heard of is kept.

    Named for what actually protects it: `prune` iterates the classes IN the
    policy and deletes by exact match, so an unknown name is never asked about.
    A mutation proved that — making `cutoff_for` fall back to `default` changed
    nothing here, because `cutoff_for` is never called with this name. The guard
    inside it is defence for a caller that does not exist yet, and it has its own
    unit test rather than a claim on this one.
    """
    pruner, sessions = sweep
    unknown = await _memory(sessions, retention="from_a_later_release", age_days=10_000)

    await pruner.prune()

    assert await _alive(sessions, unknown)


async def test_the_sweep_reaches_every_tenant(
    sweep: tuple[SqlMemoryRetention, async_sessionmaker[AsyncSession]],
) -> None:
    """A sweep scoped to one tenant would need a list of tenants to iterate, and
    getting that list is the cross-tenant read the scoping exists to prevent."""
    pruner, sessions = sweep
    mine = await _memory(sessions, retention="ephemeral", age_days=40)
    theirs = await _memory(sessions, retention="ephemeral", age_days=40, tenant=OTHER_TENANT)

    await pruner.prune()

    assert not await _alive(sessions, mine)
    assert not await _alive(sessions, theirs)


async def test_the_batch_ceiling_bounds_one_pass(
    urls: RuntimeUrls,
    sweep: tuple[SqlMemoryRetention, async_sessionmaker[AsyncSession]],
) -> None:
    """A DELETE holding a year of rows locks the table while somebody is using
    it. The sweep runs hourly; what it misses this hour goes next hour."""
    _pruner, sessions = sweep
    ids = [await _memory(sessions, retention="ephemeral", age_days=40) for _ in range(3)]
    engine = create_async_engine(urls.app, poolclass=NullPool)
    try:
        small = SqlMemoryRetention(
            async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False),
            _policy(batch_limit=2),
            _Clock(),
            vector_index=None,
        )
        await small.prune()
    finally:
        await engine.dispose()

    survivors = [i for i in ids if await _alive(sessions, i)]
    assert len(survivors) == 1, "the ceiling must bound one pass, not be ignored"


async def _evidence(
    sessions: async_sessionmaker[AsyncSession],
    *,
    age_days: int,
    cited_by: uuid.UUID | None,
    tenant: uuid.UUID = TENANT,
) -> uuid.UUID:
    """One evidence row on its own document, optionally linked to a memory.

    A real document because `evidence -> documents` is a foreign key; the sweep
    never walks it, but the fixture cannot pretend it away.
    """
    evidence_id, document_id = uuid.uuid4(), uuid.uuid4()
    async with sessions() as session, session.begin():
        await session.execute(
            sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant)}
        )
        await session.execute(
            sa.text(
                "INSERT INTO knowledge.documents"
                " (id, tenant_id, workspace_id, title, source_uri, created_by, created_at)"
                " VALUES (:d, :t, :w, 'tài liệu', 's3://dw/x', gen_random_uuid(), :c)"
            ),
            {"d": document_id, "t": tenant, "w": WORKSPACE, "c": NOW - timedelta(days=400)},
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
                "t": tenant,
                "w": WORKSPACE,
                "d": document_id,
                "h": "0" * 64,
                "c": NOW - timedelta(days=age_days),
            },
        )
        if cited_by is not None:
            await session.execute(
                sa.insert(tables.item_evidence).values(
                    memory_id=cited_by, evidence_id=evidence_id, tenant_id=tenant
                )
            )
    return evidence_id


async def _evidence_alive(
    sessions: async_sessionmaker[AsyncSession], evidence_id: uuid.UUID
) -> bool:
    async with sessions() as session, session.begin():
        await session.execute(sa.text("SELECT set_config('app.worker_drain', 'on', true)"))
        found = await session.scalar(
            sa.text("SELECT 1 FROM knowledge.evidence WHERE evidence_id = :e"),
            {"e": evidence_id},
        )
        return found is not None


async def test_evidence_no_memory_cites_any_more_is_deleted(
    sweep: tuple[SqlMemoryRetention, async_sessionmaker[AsyncSession]],
) -> None:
    """Not tidiness: `evidence -> documents` is RESTRICT, so every surviving row
    pins its document for good and the document grace period in the policy would
    be a promise the schema cannot keep."""
    pruner, sessions = sweep
    orphan = await _evidence(sessions, age_days=40, cited_by=None)

    await pruner.prune()

    assert not await _evidence_alive(sessions, orphan)


async def test_evidence_a_live_memory_cites_is_kept(
    sweep: tuple[SqlMemoryRetention, async_sessionmaker[AsyncSession]],
) -> None:
    """The proof a memory rests on outlives nothing while the memory is there.
    Deleting it would turn a citation into an assertion."""
    pruner, sessions = sweep
    keeper = await _memory(sessions, retention="legal_hold", age_days=10_000)
    cited = await _evidence(sessions, age_days=10_000, cited_by=keeper)

    await pruner.prune()

    assert await _evidence_alive(sessions, cited)


async def test_evidence_inside_its_grace_is_kept_even_uncited(
    sweep: tuple[SqlMemoryRetention, async_sessionmaker[AsyncSession]],
) -> None:
    """Evidence and its `item_evidence` link are written in one transaction today,
    so an unlinked row is genuinely unlinked. The window is what keeps that from
    being load-bearing for a writer that later splits the two."""
    pruner, sessions = sweep
    fresh = await _evidence(sessions, age_days=2, cited_by=None)

    await pruner.prune()

    assert await _evidence_alive(sessions, fresh)


async def test_expiring_a_memory_frees_the_evidence_it_cited(
    sweep: tuple[SqlMemoryRetention, async_sessionmaker[AsyncSession]],
) -> None:
    """The whole chain in one pass: the memory expires, `item_evidence` follows
    through its cascade, and the evidence that is now uncited goes with it. This
    is what makes a document that was cited once eventually deletable."""
    pruner, sessions = sweep
    expiring = await _memory(sessions, retention="ephemeral", age_days=40)
    freed = await _evidence(sessions, age_days=40, cited_by=expiring)

    await pruner.prune()

    assert not await _alive(sessions, expiring)
    assert not await _evidence_alive(sessions, freed)


# ---------------------------------------------------------------- vectors --
#
# A memory's point in the ranker's collection is an embedding of its content.
# Deleting the row and keeping the point keeps the content in the one store
# nobody sweeps.


async def _has_point(ranker: QdrantMemoryRanker, memory_id: uuid.UUID) -> bool:
    found = await ranker.client.retrieve(ranker.collection, ids=[str(memory_id)])
    return bool(found)


async def test_an_expired_memory_loses_its_vector_and_a_live_one_keeps_it(
    sweep: tuple[SqlMemoryRetention, async_sessionmaker[AsyncSession]],
    ranker: QdrantMemoryRanker,
    indexed: Callable[..., Awaitable[uuid.UUID]],
) -> None:
    _pruner, sessions = sweep
    expired = await _memory(sessions, retention="ephemeral", age_days=40)
    live = await _memory(sessions, retention="ephemeral", age_days=10)
    # Waits until each point is visible: `index` upserts with wait=False, and a
    # point not yet applied would let "the expired one has no point" pass with
    # the purge removed.
    for memory_id in (expired, live):
        await indexed("Ký ngày 10/10.", tenant=TENANT, workspace=WORKSPACE, memory_id=memory_id)
    pruner = SqlMemoryRetention(sessions, _policy(), _Clock(), vector_index=ranker)

    await pruner.prune()

    assert not await _alive(sessions, expired)
    assert not await _has_point(ranker, expired), "the row went; its embedding must too"
    assert await _has_point(ranker, live)


async def test_a_vector_store_that_is_down_does_not_keep_an_expired_row(
    sweep: tuple[SqlMemoryRetention, async_sessionmaker[AsyncSession]],
) -> None:
    """The term is the commitment. A Qdrant outage costs a stray point, which
    offboarding still removes; it must not keep a row past its term."""
    _pruner, sessions = sweep
    expired = await _memory(sessions, retention="ephemeral", age_days=40)

    @dataclass
    class _Down:
        async def delete(self, memory_ids: object) -> None:
            raise RuntimeError("qdrant xuống")

        async def delete_by_tenant(self, tenant_id: uuid.UUID) -> None:
            raise RuntimeError("qdrant xuống")

    await SqlMemoryRetention(sessions, _policy(), _Clock(), vector_index=_Down()).prune()

    assert not await _alive(sessions, expired)
