"""Knowledge gateway: THE ONLY place tenant/ACL filters are injected (§13.2).

Ingestion: artifact → object storage → chunks (PG) → vectors (Qdrant payload
per §13.3). Retrieval: AccessContext → TrustedSearchFilter → filtered vector
search → evidence pack with provenance.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_kernel.errors import InfrastructureError, PermissionDeniedError
from dw_kernel.pagination import CursorPosition, Page, PageRequest, build_page
from dw_kernel.ports import IdGenerator, UtcClock
from dw_knowledge import tables
from dw_knowledge.chunking import structure_aware_chunks
from dw_knowledge.contracts import (
    SEARCH_FILTER_KEYS,
    EvidenceChunk,
    EvidenceRef,
    SearchQuery,
    classifications_for_clearance,
)
from dw_knowledge.identity import chunk_id_for, doc_key_for, document_id_for
from dw_knowledge.ports import (
    EmbeddingPort,
    IndexableChunk,
    ObjectStoragePort,
    RerankCandidate,
    RerankPort,
    RerankResult,
    TrustedSearchFilter,
    VectorHit,
    VectorIndexPort,
)
from dw_observability.metrics import DW_RETRIEVAL_RERANK_SKIPPED_TOTAL
from dw_observability.telemetry import NullTelemetry, TelemetryPort
from dw_platform.adapters.persistence.keyset import after_position, newest_first
from dw_platform.application.access_context import AccessContext

_SET_TENANT = text("SELECT set_config('app.tenant_id', :tenant_id, true)")

logger = logging.getLogger("dw_knowledge.gateway")


# Bumped for structure-aware chunking + contextual embedding (Phase A).
INDEX_VERSION = "2026-07-25.structure-1"

# Publishing a scope="global" document makes it searchable and listable by every
# tenant, so it takes an explicit privilege — held only by a curator/admin
# context, never by an ordinary ingest. Kept as a scope string so it grants like
# any other capability on the verified AccessContext.
GLOBAL_PUBLISH_SCOPE = "knowledge.publish_global"

# How much of one document a full read returns. An hour of Vietnamese speech
# transcribes to roughly 55-60k characters, so this covers a normal meeting end
# to end; past it the caller is told the text was cut rather than handed a
# transcript that silently stops mid-sentence.
FULL_READ_CHARS = 60_000


class IngestDocumentCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    # What makes this "the same document" on a later ingest. Unset means the
    # title decides, which is right for a curated corpus and wrong for files
    # users upload against their own records - see identity.py.
    identity_key: str | None = Field(default=None, min_length=1)
    domain: str = "shared"
    classification: str = "internal"
    source_version: str = "1"
    content_type: str = "text/plain"
    scope: str = "tenant"  # "tenant" | "global" (global RLS/read wired in Phase B)
    # Business metadata the ingesting context wants back at retrieval time
    # (account_id, source_type, record_type, ...). Stored on the document row so
    # a reindex can rebuild the payload, and copied onto every chunk's point.
    extra: tuple[tuple[str, str], ...] = ()
    # Who may retrieve this. Default keeps the previous behaviour - visible to
    # the whole tenant - but a context that ingests personal or restricted
    # material can now narrow it instead of editing the gateway.
    acl_principals: tuple[str, ...] = ("tenant:*",)

    @field_validator("extra")
    @classmethod
    def _extra_keys_are_whitelisted(
        cls, value: tuple[tuple[str, str], ...]
    ) -> tuple[tuple[str, str], ...]:
        """Reject anything not searchable, which also excludes security fields.

        These keys become vector-payload keys. Without this check a caller could
        write ``tenant_id`` into the payload and, depending on merge order,
        describe a chunk as belonging to a tenant it does not.
        """
        unknown = sorted({key for key, _ in value} - SEARCH_FILTER_KEYS)
        if unknown:
            raise ValueError(
                f"unknown ingest metadata key(s): {unknown}. Allowed: {sorted(SEARCH_FILTER_KEYS)}"
            )
        return value


@dataclass(frozen=True)
class IngestedDocument:
    document_id: uuid.UUID
    chunk_count: int
    storage_key: str


@dataclass(frozen=True)
class DocumentInfo:
    """Read-model row for the knowledge inventory page."""

    document_id: uuid.UUID
    title: str
    domain: str
    classification: str
    source_version: str
    index_version: str | None
    chunk_count: int
    created_at: datetime
    scope: str = "tenant"
    # The business metadata the ingesting context attached. Exposed so a caller
    # can list "the files belonging to X" without reaching into the vector
    # payload, which only holds documents that indexed successfully.
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DocumentText:
    """One document read end to end, in chunk order."""

    document_id: uuid.UUID
    title: str
    text: str
    # Chunks actually joined into `text`, which is not the document's chunk
    # count once the ceiling bites.
    chunk_count: int
    # A transcript cut short still reads like a whole transcript. Minutes drawn
    # from one would be wrong about what the meeting decided and say so
    # nowhere, so the cut is a fact the caller is handed, not a detail.
    truncated: bool


def build_trusted_filter(context: AccessContext, domain: str) -> TrustedSearchFilter:
    """Derive mandatory constraints from the verified context ONLY."""
    allowed = classifications_for_clearance(context.clearance)
    principals = [f"user:{context.principal_id}", "tenant:*"]
    principals.extend(f"role:{role}" for role in sorted(context.roles))
    return TrustedSearchFilter(
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        domain=domain,
        allowed_classifications=allowed,
        acl_principals=tuple(principals),
    )


def _joined(
    document_id: uuid.UUID, title: str, contents: list[str], max_chars: int
) -> DocumentText:
    """Chunks back into one text, stopping at the ceiling rather than cutting.

    A chunk is dropped whole: half a chunk ends mid-sentence, and a model given
    that reads the fragment as the end of the document.
    """
    kept: list[str] = []
    used = 0
    for content in contents:
        if used + len(content) > max_chars:
            break
        kept.append(content)
        used += len(content)
    return DocumentText(
        document_id=document_id,
        title=title,
        text="\n\n".join(kept),
        chunk_count=len(kept),
        truncated=len(kept) < len(contents),
    )


@dataclass
class KnowledgeGateway:
    """Implements ``KnowledgeGatewayPort`` + ingestion."""

    session_factory: async_sessionmaker[AsyncSession]
    vector_index: VectorIndexPort
    embeddings: EmbeddingPort
    object_storage: ObjectStoragePort
    clock: UtcClock
    id_generator: IdGenerator
    reranker: RerankPort | None = None
    # Over-fetch this many x top_k for the reranker to reorder (recall->precision).
    rerank_fetch_multiplier: int = 4
    telemetry: TelemetryPort = field(default_factory=NullTelemetry)
    _ready: bool = field(default=False, init=False)

    async def ensure_ready(self) -> None:
        if not self._ready:
            await self.vector_index.ensure_ready(self.embeddings.dimension)
            self._ready = True

    # ------------------------------------------------------------ ingestion --
    async def ingest_document(
        self, command: IngestDocumentCommand, context: AccessContext
    ) -> IngestedDocument:
        # A scope="global" document crosses every tenant (search + list). Unlike
        # tenant_id/workspace_id — derived from the verified context — `scope`
        # arrives on the command, so without a gate any caller could publish a
        # document into every tenant. Require an explicit privilege; fail closed.
        if command.scope == "global" and GLOBAL_PUBLISH_SCOPE not in context.scopes:
            raise PermissionDeniedError(
                "publishing a global (cross-tenant) document requires the "
                f"'{GLOBAL_PUBLISH_SCOPE}' scope",
                details={"scope": command.scope},
            )
        await self.ensure_ready()
        # Deterministic id → re-ingesting the same logical document OVERWRITES it
        # (idempotent), instead of creating a duplicate (see identity.py).
        document_id = document_id_for(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            domain=command.domain,
            title=command.title,
            source_version=command.source_version,
            scope=command.scope,
            identity_key=command.identity_key,
        )
        storage_key = f"{context.tenant_id}/{context.workspace_id}/documents/{document_id}"
        source_uri = await self.object_storage.put_object(
            storage_key, command.content.encode("utf-8"), command.content_type
        )

        # Structure-aware, parent-child chunking; leaves carry section_path + global seq.
        result = structure_aware_chunks(command.content)
        leaves = result.chunks
        chunk_ids = [
            chunk_id_for(document_id=document_id, index_version=INDEX_VERSION, seq=leaf.seq)
            for leaf in leaves
        ]

        doc_key = doc_key_for(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            domain=command.domain,
            title=command.title,
            scope=command.scope,
            identity_key=command.identity_key,
        )
        now = self.clock.now()
        superseded_ids: list[uuid.UUID] = []
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_TENANT, {"tenant_id": str(context.tenant_id)})
            # A new version SUPERSEDES the previous current one (kept, not deleted).
            superseded = await session.execute(
                sa.update(tables.documents)
                .where(
                    tables.documents.c.doc_key == doc_key,
                    tables.documents.c.is_current.is_(True),
                    tables.documents.c.id != document_id,
                )
                .values(
                    is_current=False,
                    status="superseded",
                    effective_to=now,
                    superseded_by=document_id,
                )
                .returning(tables.documents.c.id)
            )
            superseded_ids = [row.id for row in superseded]
            if superseded_ids:
                await session.execute(
                    sa.update(tables.chunks)
                    .where(tables.chunks.c.document_id.in_(superseded_ids))
                    .values(status="superseded")
                )
            doc_stmt = pg_insert(tables.documents).values(
                id=document_id,
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                title=command.title,
                # Stored, not just used: a reindex rebuilds ids from these rows,
                # and it cannot recompute one whose identity lives only in the
                # caller that has since gone away.
                identity_key=command.identity_key,
                domain=command.domain,
                source_uri=source_uri,
                classification=command.classification,
                source_version=command.source_version,
                index_version=INDEX_VERSION,
                created_by=context.principal_id,
                created_at=now,
                doc_key=doc_key,
                status="active",
                is_current=True,
                effective_from=now,
                scope=command.scope,
                extra=dict(command.extra),
                acl_principals=list(command.acl_principals),
            )
            await session.execute(
                doc_stmt.on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        "title": doc_stmt.excluded.title,
                        "index_version": doc_stmt.excluded.index_version,
                        "classification": doc_stmt.excluded.classification,
                        "scope": doc_stmt.excluded.scope,
                        "extra": doc_stmt.excluded.extra,
                        # Re-ingesting a previously superseded/deleted version reactivates it.
                        "status": "active",
                        "is_current": True,
                        "effective_to": None,
                        "deleted_at": None,
                        "deleted_by": None,
                    },
                )
            )
            # Replace chunk set (handles shrink on re-ingest).
            await session.execute(
                sa.delete(tables.chunks).where(tables.chunks.c.document_id == document_id)
            )
            # One multi-row insert instead of a round-trip per chunk: a document
            # is hundreds of leaves, and the old loop paid a DB round-trip for
            # each one — the slowest step of an ingest after embedding.
            if leaves:
                await session.execute(
                    sa.insert(tables.chunks),
                    [
                        {
                            "id": chunk_id,
                            "tenant_id": context.tenant_id,
                            "workspace_id": context.workspace_id,
                            "document_id": document_id,
                            "seq": leaf.seq,
                            "content": leaf.content,
                            "start_offset": leaf.start_offset,
                            "end_offset": leaf.end_offset,
                            "provenance_hash": leaf.provenance_hash,
                            "metadata": {
                                "section_path": leaf.section_path,
                                "section_index": leaf.section_index,
                            },
                        }
                        for leaf, chunk_id in zip(leaves, chunk_ids, strict=True)
                    ],
                )

        # Superseded versions are kept but tombstoned so search skips them.
        for old_id in superseded_ids:
            await self.vector_index.tombstone_document(old_id)
        # Rebuild THIS version's vectors (delete stale then upsert deterministic points).
        await self.vector_index.delete_document(document_id)
        if leaves:
            # Embed the CONTEXTUAL text (breadcrumb prepended) for better retrieval;
            # store raw content for display/citation.
            vectors = await self.embeddings.embed([leaf.contextual_text for leaf in leaves])
            await self.vector_index.upsert(
                [
                    IndexableChunk(
                        chunk_id=chunk_id,
                        document_id=document_id,
                        tenant_id=context.tenant_id,
                        workspace_id=context.workspace_id,
                        domain=command.domain,
                        content=leaf.content,
                        classification=command.classification,
                        source_version=command.source_version,
                        index_version=INDEX_VERSION,
                        provenance_hash=leaf.provenance_hash,
                        acl_principals=command.acl_principals,
                        vector=tuple(vector),
                        section_path=leaf.section_path,
                        seq=leaf.seq,
                        scope=command.scope,
                        extra_payload=command.extra,
                    )
                    for leaf, chunk_id, vector in zip(leaves, chunk_ids, vectors, strict=True)
                ]
            )
        return IngestedDocument(
            document_id=document_id,
            chunk_count=len(leaves),
            storage_key=storage_key,
        )

    # -------------------------------------------------------------- listing --
    async def list_documents(
        self, context: AccessContext, request: PageRequest, *, domain: str | None = None
    ) -> Page[DocumentInfo]:
        """Tenant-scoped document inventory (RLS + explicit workspace filter).

        Newest first and resumable: ``request.after`` is the position the caller's
        previous page ended on, so an upload landing mid-run cannot push an older
        document past a boundary the caller has already read.

        ``domain`` narrows the listing in SQL. Without it a caller that
        wanted one domain took the newest ``limit`` rows of every domain and
        filtered afterwards - and once the research lanes had indexed a few
        thousand pages, the handful of uploaded attachments fell outside the
        window and read as "no files" (found 2026-08-27 on Gemadept). It must
        also be named in ``request.query`` so a cursor cannot be replayed across
        a change of domain.
        """
        # Same visibility constraints the full read applies (see read_document):
        # a global document may cross tenants, so listing it must still respect
        # the caller's clearance and the document's ACL — otherwise a restricted
        # global doc's title and business metadata leak to a tenant that could
        # never open it. Applied to the whole query so list and read agree.
        allowed = classifications_for_clearance(context.clearance)
        principals = [f"user:{context.principal_id}", "tenant:*"]
        principals.extend(f"role:{role}" for role in sorted(context.roles))
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_TENANT, {"tenant_id": str(context.tenant_id)})
            query = (
                sa.select(
                    tables.documents,
                    sa.select(sa.func.count())
                    .where(tables.chunks.c.document_id == tables.documents.c.id)
                    .scalar_subquery()
                    .label("chunk_count"),
                )
                .where(
                    # Own workspace OR any global (legal) doc — RLS permits the
                    # cross-tenant read only for scope='global' rows.
                    tables.visible_from_workspace(context.workspace_id),
                    tables.documents.c.status == "active",
                    tables.documents.c.classification.in_(allowed),
                    sa.or_(
                        *[
                            tables.documents.c.acl_principals.any(principal)
                            for principal in principals
                        ]
                    ),
                    after_position(
                        tables.documents.c.created_at, tables.documents.c.id, request.after
                    ),
                )
                .order_by(*newest_first(tables.documents.c.created_at, tables.documents.c.id))
                .limit(request.fetch_limit)
            )
            if domain is not None:
                query = query.where(tables.documents.c.domain == domain)
            rows = await session.execute(query)
            return build_page(
                [
                    DocumentInfo(
                        document_id=row.id,
                        title=row.title,
                        domain=row.domain,
                        classification=row.classification,
                        source_version=row.source_version,
                        index_version=row.index_version,
                        chunk_count=row.chunk_count,
                        created_at=row.created_at,
                        scope=row.scope,
                        extra={key: str(value) for key, value in (row.extra or {}).items()},
                    )
                    for row in rows
                ],
                request=request,
                position_of=lambda document: CursorPosition(
                    sort_value=document.created_at, tiebreaker=document.document_id
                ),
            )

    # ------------------------------------------------------------ full read --
    async def read_document(
        self,
        document_id: uuid.UUID,
        context: AccessContext,
        *,
        max_chars: int = FULL_READ_CHARS,
    ) -> DocumentText | None:
        """Every active chunk of one document, in order, or None.

        `search` answers "which passages bear on this question"; this answers
        the different question of "what does this file say". Minutes drawn from
        top-k passages of a transcript would be minutes of the parts that
        matched a query, not of the meeting.

        The constraints search derives from the context are applied here in
        SQL: RLS on tenant, the caller's workspace or a global document, the
        classifications this clearance allows, and an ACL principal the caller
        holds. `None` covers both "no such document" and "not yours" - a caller
        learns nothing about a document it may not read.
        """
        allowed = classifications_for_clearance(context.clearance)
        principals = [f"user:{context.principal_id}", "tenant:*"]
        principals.extend(f"role:{role}" for role in sorted(context.roles))
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_TENANT, {"tenant_id": str(context.tenant_id)})
            document = (
                await session.execute(
                    sa.select(
                        tables.documents.c.id,
                        tables.documents.c.title,
                    ).where(
                        tables.documents.c.id == document_id,
                        tables.documents.c.status == "active",
                        tables.visible_from_workspace(context.workspace_id),
                        tables.documents.c.classification.in_(allowed),
                        sa.or_(
                            *[
                                tables.documents.c.acl_principals.any(principal)
                                for principal in principals
                            ]
                        ),
                    )
                )
            ).first()
            if document is None:
                return None
            contents = (
                (
                    await session.execute(
                        sa.select(tables.chunks.c.content)
                        .where(
                            tables.chunks.c.document_id == document_id,
                            tables.chunks.c.status == "active",
                        )
                        .order_by(tables.chunks.c.seq)
                    )
                )
                .scalars()
                .all()
            )
        return _joined(document.id, document.title, list(contents), max_chars)

    # --------------------------------------------------------- soft delete --
    async def soft_delete_document(self, document_id: uuid.UUID, context: AccessContext) -> None:
        """Tombstone a document (kept for traceability; excluded from retrieval).

        ``dw_knowledge.retention.SqlKnowledgeRetention`` hard-removes it once the
        grace period in the retention policy is over and nothing cites it.
        """
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_TENANT, {"tenant_id": str(context.tenant_id)})
            await session.execute(
                sa.update(tables.documents)
                .where(tables.documents.c.id == document_id)
                .values(
                    status="deleted",
                    is_current=False,
                    deleted_at=self.clock.now(),
                    deleted_by=context.principal_id,
                )
            )
            await session.execute(
                sa.update(tables.chunks)
                .where(tables.chunks.c.document_id == document_id)
                .values(status="deleted", deleted_at=self.clock.now())
            )
        await self.vector_index.tombstone_document(document_id)

    # ------------------------------------------------------------ retrieval --
    async def search(self, query: SearchQuery, context: AccessContext) -> list[EvidenceChunk]:
        trusted_filter = build_trusted_filter(context, query.domain)
        vector = (await self.embeddings.embed([query.text]))[0]
        # Over-fetch when a reranker is present: dense recall then cross-encoder precision.
        fetch_k = query.top_k * self.rerank_fetch_multiplier if self.reranker else query.top_k
        # The document narrowing goes to the index with the trusted filter, so
        # it is applied before top-k and only ever intersects with it.
        hits = await self.vector_index.search(
            vector, trusted_filter, fetch_k, query.filters, query.document_ids
        )

        rerank_scores: dict[str, float] = {}
        ranked = (
            await _rerank_or_none(self.reranker, self.telemetry, query, hits)
            if self.reranker and hits
            else None
        )
        if ranked is None:
            hits = hits[: query.top_k]
        else:
            by_id = {str(h.chunk_id): h for h in hits}
            hits = [by_id[r.id] for r in ranked if r.id in by_id]
            rerank_scores = {r.id: r.score for r in ranked}

        evidence: list[EvidenceChunk] = []
        for hit in hits:
            # The reranker's score when it ranked, the vector's when it was
            # skipped: two scales, both in [0, 1]. A min_relevance above zero
            # would filter the two differently.
            score = rerank_scores.get(str(hit.chunk_id), hit.score)
            if score < query.min_relevance:
                continue
            evidence.append(
                EvidenceChunk(
                    content=hit.content,
                    evidence=EvidenceRef(
                        evidence_id=self.id_generator.new_uuid(),
                        source_document_id=hit.document_id,
                        source_version=hit.source_version,
                        chunk_id=hit.chunk_id,
                        quote=hit.content[:280],
                        relevance_score=max(0.0, min(1.0, score)),
                        classification=hit.classification,
                        provenance_hash=hit.provenance_hash,
                    ),
                )
            )
        return evidence


async def _rerank_or_none(
    reranker: RerankPort, telemetry: TelemetryPort, query: SearchQuery, hits: Sequence[VectorHit]
) -> list[RerankResult] | None:
    """The reranker's order, or None to keep the vector order.

    Reranking buys precision; it is not what makes retrieval correct. A hosted
    reranker that is down or answers nonsense therefore costs ranking quality,
    not the search that asked. The hits were already narrowed by the trusted
    filter before they got here, so falling back widens nothing.

    The call is a span of its own (its latency is the provider's), and a
    failure leaves that span in error and counts a skipped rerank, so an
    unranked search shows on the trace and the dashboard, not only in a log.
    """
    candidates = [RerankCandidate(id=str(h.chunk_id), text=h.content) for h in hits]
    try:
        with telemetry.span(
            "dw.knowledge.rerank", {"candidates": len(candidates), "top_k": query.top_k}
        ):
            return await reranker.rerank(query.text, candidates, query.top_k)
    except InfrastructureError as exc:
        telemetry.add_metric(
            DW_RETRIEVAL_RERANK_SKIPPED_TOTAL,
            1,
            {"error": exc.details.get("error", type(exc).__name__)},
        )
        logger.warning(
            "rerank skipped, keeping vector order: %s (%s)",
            type(exc).__name__,
            exc.details.get("error"),
            extra={
                "rerank_skipped": True,
                "error_type": type(exc).__name__,
                "error_cause": exc.details.get("error"),
                "status": exc.details.get("status"),
            },
        )
        return None
