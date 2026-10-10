"""The Qdrant side of memory ranking: write a vector, ask for an order.

`MemoryRankerPort` was a port nobody satisfied, which is the shape this
repository keeps producing — a capability that reads as present and has no
implementation behind it. This is the implementation.

What it is allowed to get wrong, and what it is not:

- **Wrong order is survivable.** `MemoryService` uses this to sort rows the SQL
  already chose, so a miss, a stale point or an outage costs nothing but a worse
  ordering. The service falls back to confidence order and logs it.
- **Wrong tenant is not.** The filter carries tenant, workspace and worker on
  every search, and they are arguments rather than optional narrowing — a filter
  that can be forgotten is one that will be. Defence in depth, since the SQL is
  the real boundary, but the depth is the point.

Its own collection, not the knowledge one. Sharing would mean one width for two
different kinds of text and a payload flag standing between a customer's
documents and what an agent concluded about them — a flag is a poor boundary and
a separate collection costs nothing.

Indexing is best-effort by design: it runs on the worker after a memory is
already committed, so a Qdrant that is down must not roll back a fact the system
has decided to keep. The memory is still correct and still recalled; only its
position in a long list is unranked until something reindexes it.

Deleting is not best-effort, and raises: retention, supersession and tenant
offboarding each decide what a failure means for them, and an adapter that
swallowed it would make the offboarding lane report a tenant gone whose
embeddings are still here.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from qdrant_client import AsyncQdrantClient, models

from dw_knowledge.ports import EmbeddingPort

logger = logging.getLogger("dw_memory.qdrant_ranker")

__all__ = ["DEFAULT_MEMORY_COLLECTION", "QdrantMemoryRanker"]

DEFAULT_MEMORY_COLLECTION = "dw_memory"

# Fields every search narrows by. Indexed in Qdrant because an unindexed payload
# filter is a full scan of the collection, and this runs on the agent's path.
_FILTER_FIELDS = ("tenant_id", "workspace_id", "worker_id")


@dataclass
class QdrantMemoryRanker:
    """Implements `dw_memory.ranking.MemoryRankerPort` and `MemoryVectorPurgePort`."""

    client: AsyncQdrantClient
    embedder: EmbeddingPort
    collection: str = DEFAULT_MEMORY_COLLECTION
    _ready: bool = field(default=False, init=False)

    async def ensure_ready(self) -> None:
        """Create the collection and its payload indexes if they are absent.

        A width mismatch raises rather than recreating, the same rule the
        knowledge index keeps: recreating drops every tenant's vectors, and
        during a rolling deploy two processes reading different widths would
        delete each other's collection in a loop. Changing the embedding model
        is a new collection name and a reindex, not a restart.
        """
        existing = await self._existing_width()
        if existing is None:
            await self.client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(
                    size=self.embedder.dimension, distance=models.Distance.COSINE
                ),
            )
        elif existing != self.embedder.dimension:
            raise ValueError(
                f"Qdrant collection {self.collection!r} was built for width {existing}, "
                f"and this embedder produces {self.embedder.dimension}. "
                "Use a new collection name and reindex."
            )
        for name in _FILTER_FIELDS:
            await self.client.create_payload_index(
                collection_name=self.collection,
                field_name=name,
                field_schema=models.PayloadSchemaType.KEYWORD,
                wait=True,
            )
        self._ready = True

    async def _existing_width(self) -> int | None:
        if not await self.client.collection_exists(self.collection):
            return None
        info = await self.client.get_collection(self.collection)
        params = info.config.params.vectors
        return params.size if isinstance(params, models.VectorParams) else None

    async def index(
        self,
        *,
        memory_id: uuid.UUID,
        content: str,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID,
        worker_id: str,
    ) -> None:
        """Record one memory's vector. Never raises to its caller.

        Called after the memory is committed. A store that is down must not undo
        a fact the system already decided to keep, and the only cost of skipping
        is that this memory sorts with the ones nothing has an opinion about.
        """
        try:
            if not self._ready:
                await self.ensure_ready()
            [vector] = await self.embedder.embed([content])
            await self.client.upsert(
                collection_name=self.collection,
                points=[
                    models.PointStruct(
                        id=str(memory_id),
                        vector=vector,
                        payload={
                            "tenant_id": str(tenant_id),
                            "workspace_id": str(workspace_id),
                            "worker_id": worker_id,
                        },
                    )
                ],
                wait=False,
            )
        except Exception:
            logger.warning(
                "could not index a memory for ranking; it stays recallable but unranked",
                extra={"memory_id": str(memory_id), "worker_id": worker_id},
                exc_info=True,
            )

    async def nearest(
        self,
        query: str,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID,
        worker_id: str,
        candidate_ids: Sequence[uuid.UUID],
    ) -> tuple[uuid.UUID, ...]:
        """The recalled ids, most like the question first.

        Only `candidate_ids` are searched: the id condition sits beside the
        tenant, workspace and worker conditions rather than replacing them, so a
        caller handing over a foreign id still gets nothing back for it.

        Raises on failure rather than returning nothing: an empty tuple is a
        real answer meaning "no opinion about any of these", and a caller cannot
        tell that apart from an outage. `MemoryService` catches and falls back,
        which is where that decision belongs.
        """
        if not candidate_ids:
            return ()
        [vector] = await self.embedder.embed([query])
        found = await self.client.query_points(
            collection_name=self.collection,
            query=vector,
            limit=len(candidate_ids),
            with_payload=False,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="tenant_id", match=models.MatchValue(value=str(tenant_id))
                    ),
                    models.FieldCondition(
                        key="workspace_id", match=models.MatchValue(value=str(workspace_id))
                    ),
                    models.FieldCondition(
                        key="worker_id", match=models.MatchValue(value=worker_id)
                    ),
                    models.HasIdCondition(has_id=[str(i) for i in candidate_ids]),
                ]
            ),
        )
        return tuple(uuid.UUID(str(point.id)) for point in found.points)

    async def delete(self, memory_ids: Sequence[uuid.UUID]) -> None:
        """Delete these memories' points. Raises on failure; see the port."""
        if not memory_ids or not await self.client.collection_exists(self.collection):
            return
        await self.client.delete(
            collection_name=self.collection,
            points_selector=models.PointIdsList(points=[str(i) for i in memory_ids]),
            wait=True,
        )

    async def delete_by_tenant(self, tenant_id: uuid.UUID) -> None:
        """Delete every point of one tenant. Raises on failure; see the port.

        A collection that was never created holds nothing to delete, which is
        a finished purge rather than an error.
        """
        if not await self.client.collection_exists(self.collection):
            return
        await self.client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="tenant_id", match=models.MatchValue(value=str(tenant_id))
                        )
                    ]
                )
            ),
            wait=True,
        )
