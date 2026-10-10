"""Offboarding against a real Qdrant: a tenant that leaves takes its memory vectors.

The SQL purge already deletes `memory.items`; each row's point in the memory
collection is an embedding of its content, and until this lane deleted those
too the text a tenant asked to have removed lived on in the one store the purge
never named. Against a real store because "a filter on `tenant_id` deletes one
tenant and spares the other" is a claim about Qdrant, not about this code.

Everything except the memory collection is a fake: the Postgres half and the
buckets have their own tests, and what is under test is that this lane asks the
memory store to forget the tenant at all.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from pg_test_db import load_env
from qdrant_client import AsyncQdrantClient

from dw_knowledge.adapters.hash_embedding import HashEmbeddingAdapter
from dw_memory.adapters.qdrant_ranker import QdrantMemoryRanker
from dw_worker.consumers.offboarding import TenantOffboardingLane

pytestmark = pytest.mark.integration

LEAVING = uuid.UUID(int=0xAB00)
STAYING = uuid.UUID(int=0xAB01)
WORKSPACE = uuid.UUID(int=0xAB02)


@dataclass
class _Store:
    async def claim_requested(self) -> list[uuid.UUID]:
        raise NotImplementedError("not exercised by lane.run")

    async def export_rows(self, tenant_id: uuid.UUID) -> list[Any]:
        return []

    async def purge_rows(self, tenant_id: uuid.UUID) -> None:
        return None

    async def mark_status(
        self,
        tenant_id: uuid.UUID,
        status: str,
        *,
        export_key: str | None = None,
        error: str | None = None,
    ) -> None:
        return None


@dataclass
class _Bucket:
    objects: dict[str, bytes] = field(default_factory=dict)

    async def put_object(self, key: str, data: bytes, content_type: str) -> str:
        self.objects[key] = data
        return key

    async def get_object(self, key: str) -> bytes:
        return self.objects[key]

    async def list_objects(self, prefix: str) -> list[str]:
        return [key for key in self.objects if key.startswith(prefix)]

    async def delete_object(self, key: str) -> None:
        self.objects.pop(key, None)


@dataclass
class _KnowledgeIndex:
    async def delete_by_tenant(self, tenant_id: uuid.UUID) -> None:
        return None


@dataclass(frozen=True)
class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 10, 6, tzinfo=UTC)


@pytest.fixture
async def memory_store() -> AsyncIterator[QdrantMemoryRanker]:
    url = load_env().get("QDRANT_URL", "http://127.0.0.1:6333")
    client = AsyncQdrantClient(url=url)
    collection = f"dw_memory_offboarding_{uuid.uuid4().hex[:8]}"
    store = QdrantMemoryRanker(
        client=client, embedder=HashEmbeddingAdapter(), collection=collection
    )
    try:
        await store.ensure_ready()
    except Exception as exc:  # pragma: no cover - environment, not logic
        await client.close()
        pytest.fail(f"Qdrant unreachable at {url} — run `make infra-up`. Error: {exc}")
    try:
        yield store
    finally:
        await client.delete_collection(collection)
        await client.close()


async def _points(store: QdrantMemoryRanker, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    found = await store.client.retrieve(store.collection, ids=[str(i) for i in ids])
    return {uuid.UUID(str(point.id)) for point in found}


async def test_offboarding_deletes_the_leaving_tenants_memory_vectors_and_no_others(
    memory_store: QdrantMemoryRanker,
) -> None:
    ids: dict[uuid.UUID, list[uuid.UUID]] = {LEAVING: [], STAYING: []}
    for tenant in (LEAVING, STAYING):
        for n in range(3):
            memory_id = uuid.uuid4()
            await memory_store.index(
                memory_id=memory_id,
                content=f"Điều đã học số {n}.",
                tenant_id=tenant,
                workspace_id=WORKSPACE,
                worker_id="demo",
            )
            ids[tenant].append(memory_id)
    lane = TenantOffboardingLane(
        store=_Store(),
        artifacts=_Bucket(),
        exports=_Bucket(),
        attachments=_Bucket(),
        case_documents=_Bucket(),
        vector_index=_KnowledgeIndex(),
        memory_vectors=memory_store,
        clock=_Clock(),
    )

    await lane.run(LEAVING)

    assert await _points(memory_store, ids[LEAVING]) == set()
    assert await _points(memory_store, ids[STAYING]) == set(ids[STAYING])
