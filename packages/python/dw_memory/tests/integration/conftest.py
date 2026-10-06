"""One migrated database for every memory integration file.

The fixture used to live inside `test_memory_service_db.py`, which was fine
while it had one reader. A second file needing it would otherwise copy the
recreate-and-migrate dance, and two copies of "how a test database is built"
drift the first time the harness changes.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from qdrant_client import AsyncQdrantClient
from runtime_harness import TEST_DB, RuntimeUrls, recreate_database, run_migrations, runtime_urls

from dw_knowledge.adapters.hash_embedding import HashEmbeddingAdapter
from dw_memory.adapters.qdrant_ranker import QdrantMemoryRanker


@pytest.fixture(scope="session")
def urls() -> RuntimeUrls:
    resolved = runtime_urls()
    try:
        asyncio.run(recreate_database(resolved.admin, TEST_DB))
    except Exception as exc:  # pragma: no cover - environment, not logic
        pytest.fail(f"Postgres unreachable — run `make infra-up`. Error: {exc}")
    result = run_migrations(resolved.migrator)
    if result.returncode != 0:
        pytest.fail(f"alembic upgrade failed:\n{result.stderr}")
    return resolved


@pytest.fixture
async def ranker() -> AsyncIterator[QdrantMemoryRanker]:
    """A real Qdrant ranker on a collection of its own, dropped afterwards.

    The URL comes from the repo's `.env` like the database's does. It used to
    read `QDRANT_URL` from the process environment with a 6333 default, and on
    a machine whose Qdrant listens elsewhere every test using it skipped — a
    guard that cannot run is a guard that cannot fail. Unreachable is a failure
    for the same reason it is for Postgres above: both are `make infra-up`.
    """
    url = runtime_urls().qdrant_url
    client = AsyncQdrantClient(url=url)
    # Its own collection per test: these write points, and a leftover from a
    # previous run would make the assertions read someone else's data.
    collection = f"dw_memory_test_{uuid.uuid4().hex[:8]}"
    built = QdrantMemoryRanker(
        client=client, embedder=HashEmbeddingAdapter(), collection=collection
    )
    try:
        await built.ensure_ready()
    except Exception as exc:  # pragma: no cover - environment, not logic
        await client.close()
        pytest.fail(f"Qdrant unreachable at {url} — run `make infra-up`. Error: {exc}")
    try:
        yield built
    finally:
        await client.delete_collection(collection)
        await client.close()


type Indexer = Callable[..., Awaitable[uuid.UUID]]


@pytest.fixture
def indexed(ranker: QdrantMemoryRanker) -> Indexer:
    """Index a memory and return its id once Qdrant shows the point.

    `index` upserts with `wait=False` on purpose — the worker must not block
    on the store — so a read straight after can miss it. Measured: six upserts
    then a query returned nothing, and the same query two seconds later
    returned all of them. A test that reads what it just indexed waits here.
    """

    async def index(
        content: str,
        *,
        tenant: uuid.UUID,
        workspace: uuid.UUID,
        worker_id: str = "demo",
        memory_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        memory_id = memory_id or uuid.uuid4()
        await ranker.index(
            memory_id=memory_id,
            content=content,
            tenant_id=tenant,
            workspace_id=workspace,
            worker_id=worker_id,
        )
        for _ in range(200):
            if await ranker.client.retrieve(ranker.collection, ids=[str(memory_id)]):
                return memory_id
            await asyncio.sleep(0.05)
        pytest.fail(f"point {memory_id} never became visible in {ranker.collection}")

    return index
