"""Qdrant vector index adapter (payload multitenancy + tenant index).

Search is impossible without a TrustedSearchFilter — the filter argument is not
optional and the tenant/workspace conditions are always appended here from it,
never from caller-supplied values.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient, models

from dw_kernel.errors import InfrastructureError
from dw_knowledge.contracts import DEFAULT_COLLECTION, SHARED_DOMAIN
from dw_knowledge.ports import IndexableChunk, TrustedSearchFilter, VectorHit

__all__ = ["DEFAULT_COLLECTION", "QdrantVectorIndexAdapter"]

# account_id is indexed alongside the security fields because intel narrows
# nearly every search to one account; an unindexed payload filter costs a full
# scan of the tenant's points.
_KEYWORD_PAYLOAD_INDEXES = (
    "workspace_id",
    "domain",
    "classification",
    "acl_principals",
    "account_id",
    # Every attachment query narrows by it, so an unindexed one would scan the
    # tenant's whole point set on each question about a file.
    "attachment_scope",
    # A search that names documents narrows by it before top-k, as do the
    # per-document delete and tombstone.
    "source_document_id",
)


def _is_missing_collection(exc: BaseException) -> bool:
    """Qdrant answers 404 "Collection ... doesn't exist" for an unknown name.

    Matched on the message rather than the exception class because the client
    raises the same ``UnexpectedResponse`` for every HTTP error.
    """
    text = str(exc).lower()
    return "doesn't exist" in text or "not found" in text or "404" in text


@dataclass
class QdrantVectorIndexAdapter:
    """Implements ``VectorIndexPort``."""

    client: AsyncQdrantClient
    collection: str = DEFAULT_COLLECTION

    async def ensure_ready(self, vector_dimension: int) -> None:
        """Create the collection if it is absent, then reconcile its payload indexes.

        A dimension mismatch raises instead of recreating. Recreating drops every
        tenant's vectors, and it would be triggered by nothing more than a config
        value: during a rolling deploy, two processes reading different
        dimensions delete each other's collection in a loop rather than in a
        window. Changing the embedding width is a migration - a new collection
        name, then a reindex - not a side effect of a restart.
        """
        existing = await self._existing_dimension()
        if existing is None:
            await self._create_collection(vector_dimension)
        elif existing != vector_dimension:
            raise InfrastructureError(
                "Qdrant collection was built for a different embedding width",
                details={
                    "collection": self.collection,
                    "collection_dimension": existing,
                    "embedding_dimension": vector_dimension,
                },
            )
        # Reconciled on every call, not only after a create: a payload index
        # added to this adapter later would otherwise never exist on a
        # collection that already does, and an unindexed keyword filter is a
        # full scan of the tenant's points.
        await self._ensure_payload_indexes()

    async def _existing_dimension(self) -> int | None:
        """The collection's vector width, or None when it does not exist yet."""
        try:
            if not await self.client.collection_exists(self.collection):
                return None
            info = await self.client.get_collection(self.collection)
            size = getattr(info.config.params.vectors, "size", None)
        except Exception as exc:
            raise InfrastructureError(
                "failed to read the Qdrant collection",
                details={"collection": self.collection, "error": type(exc).__name__},
            ) from exc
        return int(size) if size is not None else None

    async def _create_collection(self, vector_dimension: int) -> None:
        try:
            await self.client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(
                    size=vector_dimension, distance=models.Distance.COSINE
                ),
                # Scalar quantization keeps RAM/latency low on modest hardware.
                quantization_config=models.ScalarQuantization(
                    scalar=models.ScalarQuantizationConfig(
                        type=models.ScalarType.INT8, always_ram=True
                    )
                ),
            )
        except Exception as exc:
            raise InfrastructureError(
                "failed to create the Qdrant collection",
                details={"collection": self.collection, "error": type(exc).__name__},
            ) from exc

    async def _ensure_payload_indexes(self) -> None:
        """Idempotent - Qdrant treats a repeated create as an update."""
        try:
            await self.client.create_payload_index(
                self.collection,
                field_name="tenant_id",
                field_schema=models.KeywordIndexParams(
                    type=models.KeywordIndexType.KEYWORD, is_tenant=True
                ),
            )
            for field in _KEYWORD_PAYLOAD_INDEXES:
                await self.client.create_payload_index(
                    self.collection,
                    field_name=field,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
            await self.client.create_payload_index(
                self.collection,
                field_name="is_deleted",
                field_schema=models.PayloadSchemaType.BOOL,
            )
        except Exception as exc:
            raise InfrastructureError(
                "failed to prepare Qdrant payload indexes",
                details={"collection": self.collection, "error": type(exc).__name__},
            ) from exc

    async def upsert(self, chunks: Sequence[IndexableChunk]) -> None:
        if not chunks:
            return
        points = [
            models.PointStruct(
                id=str(chunk.chunk_id),
                vector=list(chunk.vector),
                payload={
                    # Business metadata FIRST so the trusted fields below always
                    # win a key collision. The ingest command also rejects
                    # reserved keys, but a payload built by dict order cannot be
                    # made unsafe by a future caller that skips that path.
                    **dict(chunk.extra_payload),
                    "tenant_id": str(chunk.tenant_id),
                    "workspace_id": str(chunk.workspace_id),
                    "domain": chunk.domain,
                    "resource_id": str(chunk.document_id),
                    "source_document_id": str(chunk.document_id),
                    "chunk_id": str(chunk.chunk_id),
                    "acl_principals": list(chunk.acl_principals),
                    "classification": chunk.classification,
                    "source_version": chunk.source_version,
                    "index_version": chunk.index_version,
                    "provenance_hash": chunk.provenance_hash,
                    "content": chunk.content,
                    "section_path": chunk.section_path,
                    "seq": chunk.seq,
                    "scope": chunk.scope,
                    "is_deleted": False,
                },
            )
            for chunk in chunks
        ]
        try:
            await self.client.upsert(self.collection, points=points, wait=True)
        except Exception as exc:
            raise InfrastructureError(
                "failed to upsert vectors", details={"error": type(exc).__name__}
            ) from exc

    def _document_filter(self, document_id: uuid.UUID) -> models.Filter:
        return models.Filter(
            must=[
                models.FieldCondition(
                    key="source_document_id",
                    match=models.MatchValue(value=str(document_id)),
                )
            ]
        )

    async def delete_document(self, document_id: uuid.UUID) -> None:
        try:
            await self.client.delete(
                self.collection,
                points_selector=models.FilterSelector(filter=self._document_filter(document_id)),
                wait=True,
            )
        except Exception as exc:
            raise InfrastructureError(
                "failed to delete document vectors", details={"error": type(exc).__name__}
            ) from exc

    def _tenant_filter(self, tenant_id: uuid.UUID) -> models.Filter:
        return models.Filter(
            must=[
                models.FieldCondition(
                    key="tenant_id",
                    match=models.MatchValue(value=str(tenant_id)),
                )
            ]
        )

    async def delete_by_tenant(self, tenant_id: uuid.UUID) -> None:
        try:
            await self.client.delete(
                self.collection,
                points_selector=models.FilterSelector(filter=self._tenant_filter(tenant_id)),
                wait=True,
            )
        except Exception as exc:
            raise InfrastructureError(
                "failed to delete tenant vectors", details={"error": type(exc).__name__}
            ) from exc

    async def tombstone_document(self, document_id: uuid.UUID) -> None:
        try:
            await self.client.set_payload(
                self.collection,
                payload={"is_deleted": True},
                points=models.FilterSelector(filter=self._document_filter(document_id)),
                wait=True,
            )
        except Exception as exc:
            raise InfrastructureError(
                "failed to tombstone document vectors", details={"error": type(exc).__name__}
            ) from exc

    async def search(
        self,
        vector: Sequence[float],
        trusted_filter: TrustedSearchFilter,
        top_k: int,
        extra_filters: Sequence[tuple[str, str]] = (),
        document_ids: Sequence[uuid.UUID] = (),
    ) -> list[VectorHit]:
        # Mandatory constraints come EXCLUSIVELY from the trusted filter.
        conditions: list[models.FieldCondition] = [
            models.FieldCondition(
                key="classification",
                match=models.MatchAny(any=list(trusted_filter.allowed_classifications)),
            ),
            models.FieldCondition(
                key="acl_principals",
                match=models.MatchAny(any=list(trusted_filter.acl_principals)),
            ),
        ]
        # Always emitted. Skipping it when the caller asked for "shared" made
        # that value mean "every domain", so the default query read every corpus
        # the tenant owns - including files a user attached to one CRM record.
        conditions.append(
            models.FieldCondition(
                key="domain",
                match=models.MatchAny(any=sorted({trusted_filter.domain, SHARED_DOMAIN})),
            )
        )
        # Business narrowing joins the same `must` list, so it can only ever
        # intersect with the mandatory constraints - never replace one.
        conditions.extend(
            models.FieldCondition(key=key, match=models.MatchValue(value=value))
            for key, value in extra_filters
        )
        # Asking for particular documents is the same kind of narrowing, and it
        # has to happen here rather than on the hits: `limit` below is applied
        # to whatever this filter admits.
        if document_ids:
            conditions.append(
                models.FieldCondition(
                    key="source_document_id",
                    match=models.MatchAny(any=[str(d) for d in document_ids]),
                )
            )
        # Tenant isolation OR global (legal) scope: a point is visible if it is in
        # the caller's tenant+workspace, OR it is a cross-tenant global document.
        own_tenant = models.Filter(
            must=[
                models.FieldCondition(
                    key="tenant_id",
                    match=models.MatchValue(value=str(trusted_filter.tenant_id)),
                ),
                models.FieldCondition(
                    key="workspace_id",
                    match=models.MatchValue(value=str(trusted_filter.workspace_id)),
                ),
            ]
        )
        scope_or_tenant = [
            own_tenant,
            models.FieldCondition(key="scope", match=models.MatchValue(value="global")),
        ]
        try:
            response = await self.client.query_points(
                self.collection,
                query=list(vector),
                query_filter=models.Filter(
                    must=list(conditions),
                    should=scope_or_tenant,  # at least one → (own tenant) OR (global)
                    # Soft-deleted / superseded points are never retrieved.
                    must_not=[
                        models.FieldCondition(key="is_deleted", match=models.MatchValue(value=True))
                    ],
                ),
                limit=top_k,
                with_payload=True,
            )
        except Exception as exc:
            if _is_missing_collection(exc):
                # Nothing has been indexed here yet. That is an empty result,
                # not a broken system: on a fresh install the first question
                # asked before the first upload would otherwise fail the whole
                # turn, because InfrastructureError is not an error the agent is
                # allowed to answer for.
                return []
            raise InfrastructureError(
                "vector search failed", details={"error": type(exc).__name__}
            ) from exc

        hits: list[VectorHit] = []
        for point in response.points:
            payload = point.payload or {}
            hits.append(
                VectorHit(
                    chunk_id=_uuid(payload.get("chunk_id")),
                    document_id=_uuid(payload.get("source_document_id")),
                    score=float(point.score),
                    content=str(payload.get("content", "")),
                    classification=str(payload.get("classification", "internal")),
                    source_version=str(payload.get("source_version", "1")),
                    provenance_hash=str(payload.get("provenance_hash", "")),
                )
            )
        return hits


def _uuid(value: object) -> uuid.UUID:
    return uuid.UUID(str(value))
