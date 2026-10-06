"""In-memory vector index (working adapter for local/test without Qdrant).

Implements the SAME trusted-filter semantics as the Qdrant adapter — tenant,
workspace, classification, ACL and domain constraints all apply. Not durable:
suitable only for tests and infra-less local runs (never production).
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from dw_knowledge.contracts import SHARED_DOMAIN
from dw_knowledge.ports import IndexableChunk, TrustedSearchFilter, VectorHit


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


@dataclass
class InMemoryVectorIndexAdapter:
    """Implements ``VectorIndexPort`` in process memory."""

    _chunks: dict[str, IndexableChunk] = field(default_factory=dict)
    _tombstoned: set[uuid.UUID] = field(default_factory=set)

    async def ensure_ready(self, vector_dimension: int) -> None:
        return None

    async def upsert(self, chunks: Sequence[IndexableChunk]) -> None:
        for chunk in chunks:
            self._chunks[str(chunk.chunk_id)] = chunk
            self._tombstoned.discard(chunk.document_id)  # re-index reactivates

    async def delete_document(self, document_id: uuid.UUID) -> None:
        for key in [k for k, c in self._chunks.items() if c.document_id == document_id]:
            del self._chunks[key]
        self._tombstoned.discard(document_id)

    async def tombstone_document(self, document_id: uuid.UUID) -> None:
        self._tombstoned.add(document_id)

    async def delete_by_tenant(self, tenant_id: uuid.UUID) -> None:
        for key in [k for k, c in self._chunks.items() if c.tenant_id == tenant_id]:
            del self._chunks[key]

    async def search(
        self,
        vector: Sequence[float],
        trusted_filter: TrustedSearchFilter,
        top_k: int,
        extra_filters: Sequence[tuple[str, str]] = (),
        document_ids: Sequence[uuid.UUID] = (),
    ) -> list[VectorHit]:
        wanted = set(document_ids)
        principals = set(trusted_filter.acl_principals)
        allowed = set(trusted_filter.allowed_classifications)
        hits: list[VectorHit] = []
        for chunk in self._chunks.values():
            if chunk.document_id in self._tombstoned:
                continue
            # Applied first and as a conjunction: a business filter can only
            # ever drop a chunk the trusted checks below would have allowed.
            payload = dict(chunk.extra_payload)
            if any(payload.get(key) != value for key, value in extra_filters):
                continue
            if wanted and chunk.document_id not in wanted:
                continue
            # Own tenant+workspace OR a cross-tenant global (legal) document.
            own = (
                chunk.tenant_id == trusted_filter.tenant_id
                and chunk.workspace_id == trusted_filter.workspace_id
            )
            if not (own or chunk.scope == "global"):
                continue
            if chunk.classification not in allowed:
                continue
            if not (set(chunk.acl_principals) & principals):
                continue
            if chunk.domain not in (trusted_filter.domain, SHARED_DOMAIN):
                continue
            hits.append(
                VectorHit(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    score=_cosine(vector, chunk.vector),
                    content=chunk.content,
                    classification=chunk.classification,
                    source_version=chunk.source_version,
                    provenance_hash=chunk.provenance_hash,
                )
            )
        hits.sort(key=lambda h: -h.score)
        return hits[:top_k]
