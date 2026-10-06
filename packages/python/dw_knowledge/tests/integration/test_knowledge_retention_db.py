"""The document sweep against real Postgres.

Three of these can only be answered by a database, which is why they are here
and not beside a fake: whether the cascade to `chunks` happens under row
security, whether the RESTRICT from `evidence` really would have blocked the
delete, and whether `app.worker_drain` opens rows across tenants.

The cited-document case is the one worth stating twice. It is not enough that
the sweep leaves the document alone — the point is that it leaves it alone
DELIBERATELY, by asking, rather than by crashing into a foreign key and rolling
the whole batch back. `test_a_cited_document_does_not_block_its_neighbours`
is what tells those two apart, and it fails on a sweep that omits the
`NOT EXISTS`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from runtime_harness import RuntimeUrls
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dw_knowledge import tables
from dw_knowledge.ports import IndexableChunk, TrustedSearchFilter, VectorHit
from dw_knowledge.retention import SqlKnowledgeRetention
from dw_platform.retention_policy import (
    AuditRetention,
    CheckpointRetention,
    KnowledgeRetention,
    RetentionClass,
    RetentionPolicy,
)

pytestmark = pytest.mark.integration

TENANT = uuid.UUID(int=0xDD00)
OTHER_TENANT = uuid.UUID(int=0xDD01)
WORKSPACE = uuid.UUID(int=0xDD02)
NOW = datetime(2026, 9, 18, tzinfo=UTC)
GRACE_DAYS = 30


@dataclass(frozen=True)
class _Clock:
    def now(self) -> datetime:
        return NOW


def _policy() -> RetentionPolicy:
    return RetentionPolicy(
        schema_version="1.0",
        policy_id="retention",
        policy_version="1.1.0",
        classes={"default": RetentionClass(days=730, description="thường")},
        knowledge=KnowledgeRetention(deleted_grace_days=GRACE_DAYS, orphan_evidence_grace_days=7),
        audit=AuditRetention(months_ahead=1, enforced=False, tables={}),
        checkpoints=CheckpointRetention(superseded_days=7, idle_thread_days=730),
        batch_limit=1000,
    )


class _RecordingIndex:
    """Records what the sweep asked the vector store to forget.

    A fake and not real Qdrant on purpose: what is under test here is that the
    sweep asks at all, and with which ids. That the adapter's `delete_document`
    really removes points is `test_soft_delete_index.py`'s question, answered
    against a running Qdrant.
    """

    def __init__(self) -> None:
        self.deleted: list[uuid.UUID] = []

    async def ensure_ready(self, vector_dimension: int) -> None: ...

    async def upsert(self, chunks: Sequence[IndexableChunk]) -> None: ...

    async def delete_document(self, document_id: uuid.UUID) -> None:
        self.deleted.append(document_id)

    async def tombstone_document(self, document_id: uuid.UUID) -> None: ...

    async def delete_by_tenant(self, tenant_id: uuid.UUID) -> None: ...

    async def search(
        self,
        vector: Sequence[float],
        trusted_filter: TrustedSearchFilter,
        top_k: int,
        extra_filters: Sequence[tuple[str, str]] = (),
        document_ids: Sequence[uuid.UUID] = (),
    ) -> list[VectorHit]:
        return []


@pytest.fixture
async def sweep(
    urls: RuntimeUrls,
) -> AsyncIterator[tuple[SqlKnowledgeRetention, async_sessionmaker[AsyncSession], _RecordingIndex]]:
    engine = create_async_engine(urls.app, poolclass=NullPool)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    index = _RecordingIndex()
    try:
        yield SqlKnowledgeRetention(sessions, _policy(), _Clock(), index), sessions, index
    finally:
        await engine.dispose()


async def _document(
    sessions: async_sessionmaker[AsyncSession],
    *,
    status: str,
    deleted_days_ago: int | None,
    tenant: uuid.UUID = TENANT,
    cited: bool = False,
) -> uuid.UUID:
    """One document, one chunk, and optionally the evidence that pins it."""
    document_id, chunk_id = uuid.uuid4(), uuid.uuid4()
    deleted_at = None if deleted_days_ago is None else NOW - timedelta(days=deleted_days_ago)
    async with sessions() as session, session.begin():
        await session.execute(
            sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant)}
        )
        await session.execute(
            sa.insert(tables.documents).values(
                id=document_id,
                tenant_id=tenant,
                workspace_id=WORKSPACE,
                title="hợp đồng",
                domain="shared",
                source_uri=f"s3://dw/{document_id}",
                classification="internal",
                source_version="1",
                created_by=uuid.uuid4(),
                created_at=NOW - timedelta(days=400),
                status=status,
                deleted_at=deleted_at,
            )
        )
        await session.execute(
            sa.insert(tables.chunks).values(
                id=chunk_id,
                tenant_id=tenant,
                workspace_id=WORKSPACE,
                document_id=document_id,
                seq=0,
                content="nội dung",
                start_offset=0,
                end_offset=8,
                provenance_hash="0" * 64,
                metadata={},
            )
        )
        if cited:
            await session.execute(
                sa.insert(tables.evidence).values(
                    evidence_id=uuid.uuid4(),
                    tenant_id=tenant,
                    workspace_id=WORKSPACE,
                    source_document_id=document_id,
                    chunk_id=chunk_id,
                    source_version="1",
                    relevance_score=0.9,
                    classification="internal",
                    provenance_hash="0" * 64,
                    created_at=NOW - timedelta(days=400),
                )
            )
    return document_id


async def _cite(sessions: async_sessionmaker[AsyncSession], document_id: uuid.UUID) -> None:
    """What a memory proposal does to a document: writes evidence against it."""
    async with sessions() as session, session.begin():
        await session.execute(
            sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(TENANT)}
        )
        await session.execute(
            sa.insert(tables.evidence).values(
                evidence_id=uuid.uuid4(),
                tenant_id=TENANT,
                workspace_id=WORKSPACE,
                source_document_id=document_id,
                source_version="1",
                relevance_score=0.9,
                classification="internal",
                provenance_hash="0" * 64,
                created_at=NOW,
            )
        )


async def _count(
    sessions: async_sessionmaker[AsyncSession],
    table: sa.Table,
    column: sa.Column[uuid.UUID],
    value: uuid.UUID,
) -> int:
    async with sessions() as session, session.begin():
        await session.execute(sa.text("SELECT set_config('app.worker_drain', 'on', true)"))
        found = await session.scalar(
            sa.select(sa.func.count()).select_from(table).where(column == value)
        )
        return found or 0


async def test_a_deleted_document_past_its_grace_is_removed(
    sweep: tuple[SqlKnowledgeRetention, async_sessionmaker[AsyncSession], _RecordingIndex],
) -> None:
    pruner, sessions, _index = sweep
    doomed = await _document(sessions, status="deleted", deleted_days_ago=GRACE_DAYS + 10)

    await pruner.prune()

    assert await _count(sessions, tables.documents, tables.documents.c.id, doomed) == 0


async def test_the_chunks_go_with_it(
    sweep: tuple[SqlKnowledgeRetention, async_sessionmaker[AsyncSession], _RecordingIndex],
) -> None:
    """The sweep names only `documents`; `chunks.document_id` is ON DELETE CASCADE
    and referential-integrity triggers run with row security off, so the chunks
    of every tenant follow. That is a claim about Postgres, so a database is what
    gets to confirm it — a sweep that had to delete chunks itself would need a
    second statement and a second policy."""
    pruner, sessions, _index = sweep
    doomed = await _document(sessions, status="deleted", deleted_days_ago=GRACE_DAYS + 10)

    await pruner.prune()

    assert await _count(sessions, tables.chunks, tables.chunks.c.document_id, doomed) == 0


async def test_a_deleted_document_inside_its_grace_is_kept(
    sweep: tuple[SqlKnowledgeRetention, async_sessionmaker[AsyncSession], _RecordingIndex],
) -> None:
    """The grace period is the window for changing your mind. A sweep that ignored
    `deleted_at` would make soft delete indistinguishable from hard delete."""
    pruner, sessions, _index = sweep
    recent = await _document(sessions, status="deleted", deleted_days_ago=GRACE_DAYS - 10)

    await pruner.prune()

    assert await _count(sessions, tables.documents, tables.documents.c.id, recent) == 1


async def test_a_live_document_is_never_swept_however_old(
    sweep: tuple[SqlKnowledgeRetention, async_sessionmaker[AsyncSession], _RecordingIndex],
) -> None:
    """Age is not the trigger — somebody deleting it is. A document nobody touched
    stays for ever, which is what "system of record" means."""
    pruner, sessions, _index = sweep
    ancient = await _document(sessions, status="active", deleted_days_ago=None)

    await pruner.prune()

    assert await _count(sessions, tables.documents, tables.documents.c.id, ancient) == 1


async def test_a_cited_document_does_not_block_its_neighbours(
    sweep: tuple[SqlKnowledgeRetention, async_sessionmaker[AsyncSession], _RecordingIndex],
) -> None:
    """The one that separates "asked and held back" from "crashed into a foreign key".

    `evidence -> documents` is RESTRICT, so a batch containing the cited document
    fails ENTIRELY — the uncited one beside it would survive too, and the sweep
    would report a rollback for ever while making no progress. Both assertions
    are needed: the first alone passes on the broken version.
    """
    pruner, sessions, _index = sweep
    cited = await _document(
        sessions, status="deleted", deleted_days_ago=GRACE_DAYS + 10, cited=True
    )
    uncited = await _document(sessions, status="deleted", deleted_days_ago=GRACE_DAYS + 10)

    await pruner.prune()

    assert await _count(sessions, tables.documents, tables.documents.c.id, cited) == 1
    assert await _count(sessions, tables.documents, tables.documents.c.id, uncited) == 0


async def test_the_sweep_reaches_every_tenant(
    sweep: tuple[SqlKnowledgeRetention, async_sessionmaker[AsyncSession], _RecordingIndex],
) -> None:
    """A sweep scoped to one tenant would need a list of tenants to iterate, and
    getting that list is the cross-tenant read the scoping exists to prevent."""
    pruner, sessions, _index = sweep
    mine = await _document(sessions, status="deleted", deleted_days_ago=GRACE_DAYS + 10)
    theirs = await _document(
        sessions, status="deleted", deleted_days_ago=GRACE_DAYS + 10, tenant=OTHER_TENANT
    )

    await pruner.prune()

    assert await _count(sessions, tables.documents, tables.documents.c.id, mine) == 0
    assert await _count(sessions, tables.documents, tables.documents.c.id, theirs) == 0


async def test_the_vectors_go_too(
    sweep: tuple[SqlKnowledgeRetention, async_sessionmaker[AsyncSession], _RecordingIndex],
) -> None:
    """Postgres is the system of record; Qdrant is where the text lives in a form
    a search can return. A pass that removed the rows and left the points would
    leave the document readable in the only store that no longer has anything to
    join against to discover it should be gone."""
    pruner, sessions, index = sweep
    doomed = await _document(sessions, status="deleted", deleted_days_ago=GRACE_DAYS + 10)

    await pruner.prune()

    assert doomed in index.deleted


async def test_a_cited_documents_vectors_stay(
    sweep: tuple[SqlKnowledgeRetention, async_sessionmaker[AsyncSession], _RecordingIndex],
) -> None:
    """The vector delete runs before the row delete, so it must be driven by the
    same citation test — otherwise a held-back document loses its points while
    keeping its rows, and the evidence still pointing at it cites text nothing
    can retrieve any more."""
    pruner, sessions, index = sweep
    cited = await _document(
        sessions, status="deleted", deleted_days_ago=GRACE_DAYS + 10, cited=True
    )

    await pruner.prune()

    assert cited not in index.deleted


async def test_a_superseded_document_is_left_alone(
    sweep: tuple[SqlKnowledgeRetention, async_sessionmaker[AsyncSession], _RecordingIndex],
) -> None:
    """A superseded document is the previous VERSION of one that is still live,
    and how many versions back to keep is a different question from how long a
    deletion takes to become final. It carries a `deleted_at` on some paths, so
    only the `status` test tells the two apart — without it this row would be
    expired under a policy key that never mentioned version history.
    """
    pruner, sessions, index = sweep
    old_version = await _document(sessions, status="superseded", deleted_days_ago=GRACE_DAYS + 10)

    await pruner.prune()

    assert await _count(sessions, tables.documents, tables.documents.c.id, old_version) == 1
    assert old_version not in index.deleted


async def test_a_citation_written_mid_pass_holds_back_only_that_document(
    sweep: tuple[SqlKnowledgeRetention, async_sessionmaker[AsyncSession], _RecordingIndex],
) -> None:
    """The gap between reading the candidates and deleting them is real.

    It has to be: the vector deletes happen in between, and a transaction held
    open across a network call is a lock held open across a network call. A
    memory proposed in that gap writes evidence against one of the candidates,
    and `evidence -> documents` is RESTRICT — so without the second citation test
    the whole batch would roll back and every other document would be stuck
    behind that one row.

    Staged through the vector index because that is exactly where the gap is: no
    private method is called, the race is driven by the sweep itself.
    """
    _pruner, sessions, _index = sweep
    late = await _document(sessions, status="deleted", deleted_days_ago=GRACE_DAYS + 10)
    ordinary = await _document(sessions, status="deleted", deleted_days_ago=GRACE_DAYS + 10)

    class _CitesDuringTheGap(_RecordingIndex):
        async def delete_document(self, document_id: uuid.UUID) -> None:
            await super().delete_document(document_id)
            if document_id == late:
                await _cite(sessions, late)

    racing = SqlKnowledgeRetention(sessions, _policy(), _Clock(), _CitesDuringTheGap())
    await racing.prune()

    assert await _count(sessions, tables.documents, tables.documents.c.id, late) == 1
    assert await _count(sessions, tables.documents, tables.documents.c.id, ordinary) == 0
