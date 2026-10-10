import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_kernel.errors import InfrastructureError
from dw_kernel.ports import FixedClock, SequentialIdGenerator
from dw_knowledge.adapters.memory_index import InMemoryVectorIndexAdapter
from dw_knowledge.chunking import chunk_text
from dw_knowledge.contracts import SearchQuery
from dw_knowledge.gateway import KnowledgeGateway, build_trusted_filter
from dw_knowledge.ports import (
    IndexableChunk,
    RerankCandidate,
    RerankResult,
    TrustedSearchFilter,
    VectorHit,
)
from dw_observability.metrics import DW_RETRIEVAL_RERANK_SKIPPED_TOTAL
from dw_observability.telemetry import RecordingTelemetry
from dw_platform.application.access_context import AccessContext

pytestmark = pytest.mark.unit

NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)


def make_context(**overrides: object) -> AccessContext:
    defaults: dict[str, object] = {
        "tenant_id": uuid.uuid4(),
        "workspace_id": uuid.uuid4(),
        "principal_id": uuid.uuid4(),
        "roles": frozenset({"member"}),
        "scopes": frozenset({"knowledge.read"}),
        "plan_id": "professional",
    }
    defaults.update(overrides)
    return AccessContext(**defaults)


# --- chunking ---------------------------------------------------------------


def test_chunking_is_deterministic_and_offset_preserving() -> None:
    text = "\n\n".join(f"Đoạn văn số {i}. " + "nội dung " * 40 for i in range(6))
    first = chunk_text(text)
    second = chunk_text(text)
    assert first == second
    assert len(first) > 1
    for chunk in first:
        assert text[chunk.start_offset : chunk.end_offset].strip() == chunk.content
        assert len(chunk.provenance_hash) == 64


def test_chunking_rejects_bad_params() -> None:
    with pytest.raises(ValueError):
        chunk_text("x", max_chars=0)
    with pytest.raises(ValueError):
        chunk_text("x", max_chars=100, overlap_chars=100)


def test_chunking_empty_text() -> None:
    assert chunk_text("   \n  ") == []


# --- trusted filter ----------------------------------------------------------


def test_trusted_filter_comes_only_from_context() -> None:
    context = make_context(clearance="internal")
    trusted = build_trusted_filter(context, "demo")
    assert trusted.tenant_id == context.tenant_id
    assert trusted.workspace_id == context.workspace_id
    assert trusted.allowed_classifications == ("internal",)
    assert f"user:{context.principal_id}" in trusted.acl_principals
    assert "role:member" in trusted.acl_principals


def test_clearance_mapping_fails_closed_for_unknown_values() -> None:
    trusted = build_trusted_filter(make_context(clearance="galactic"), "shared")
    assert trusted.allowed_classifications == ("internal",)


def test_confidential_clearance_widens_but_never_restricted() -> None:
    trusted = build_trusted_filter(make_context(clearance="confidential"), "shared")
    assert "restricted" not in trusted.allowed_classifications


# --- gateway search always injects the filter --------------------------------


@dataclass
class CapturingIndex:
    captured: list[TrustedSearchFilter] = field(default_factory=list)
    captured_filters: list[tuple[tuple[str, str], ...]] = field(default_factory=list)
    captured_documents: list[tuple[uuid.UUID, ...]] = field(default_factory=list)
    hits: list[VectorHit] = field(default_factory=list)

    async def ensure_ready(self, vector_dimension: int) -> None: ...

    async def upsert(self, chunks) -> None: ...

    async def delete_document(self, document_id: uuid.UUID) -> None: ...

    async def tombstone_document(self, document_id: uuid.UUID) -> None: ...

    async def delete_by_tenant(self, tenant_id: uuid.UUID) -> None: ...

    async def search(self, vector, trusted_filter, top_k, extra_filters=(), document_ids=()):
        self.captured.append(trusted_filter)
        self.captured_filters.append(tuple(extra_filters))
        self.captured_documents.append(tuple(document_ids))
        # Honours the port: a document narrowing is applied BEFORE the limit.
        hits = [h for h in self.hits if not document_ids or h.document_id in document_ids]
        return hits[:top_k]


@dataclass
class FakeEmbedding:
    @property
    def dimension(self) -> int:
        return 4

    async def embed(self, texts):
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


@dataclass
class FakeStorage:
    async def put_object(self, key: str, data: bytes, content_type: str) -> str:
        return f"s3://fake/{key}"

    async def get_object(self, key: str) -> bytes:
        return b""

    async def list_objects(self, prefix: str) -> list[str]:
        return []

    async def delete_object(self, key: str) -> None:
        return None


def make_gateway(index: CapturingIndex) -> KnowledgeGateway:
    return KnowledgeGateway(
        session_factory=cast("async_sessionmaker[AsyncSession]", None),  # search never touches SQL
        vector_index=index,
        embeddings=FakeEmbedding(),
        object_storage=FakeStorage(),
        clock=FixedClock(NOW),
        id_generator=SequentialIdGenerator(),
    )


async def test_search_always_carries_tenant_filter_from_context() -> None:
    index = CapturingIndex()
    gateway = make_gateway(index)
    context = make_context()

    await gateway.search(SearchQuery(text="chính sách mua hàng"), context)

    assert len(index.captured) == 1
    trusted = index.captured[0]
    assert trusted.tenant_id == context.tenant_id
    assert trusted.workspace_id == context.workspace_id
    # There is no code path for the caller to override these: SearchQuery has
    # no such fields (extra="forbid") and the gateway rebuilds the filter from
    # AccessContext on every call.


async def test_search_passes_business_filters_through_to_the_index() -> None:
    """Whitelisted narrowing reaches the adapter alongside the trusted filter."""
    index = CapturingIndex()
    gateway = make_gateway(index)
    account_id = str(uuid.uuid4())

    await gateway.search(
        SearchQuery(text="doanh thu", filters=(("account_id", account_id),)),
        make_context(),
    )

    assert index.captured_filters == [(("account_id", account_id),)]
    assert index.captured[0].tenant_id is not None


async def test_search_applies_min_relevance_and_document_filter() -> None:
    doc_a, doc_b = uuid.uuid4(), uuid.uuid4()

    def hit(document_id: uuid.UUID, score: float) -> VectorHit:
        return VectorHit(
            chunk_id=uuid.uuid4(),
            document_id=document_id,
            score=score,
            content="nội dung",
            classification="internal",
            source_version="1",
            provenance_hash="a" * 64,
        )

    index = CapturingIndex(hits=[hit(doc_a, 0.9), hit(doc_b, 0.9), hit(doc_a, 0.1)])
    gateway = make_gateway(index)

    results = await gateway.search(
        SearchQuery(text="q", min_relevance=0.5, document_ids=(doc_a,)),
        make_context(),
    )
    assert len(results) == 1
    assert results[0].evidence.source_document_id == doc_a
    assert results[0].evidence.relevance_score == 0.9


async def test_search_hands_the_requested_documents_to_the_index() -> None:
    """The narrowing travels WITH the trusted filter, so it runs before top-k.

    Applied after the index returned its top-k, a document whose chunks rank
    below the tenant's global top-k came back with fewer results, or none.
    """
    index = CapturingIndex()
    gateway = make_gateway(index)
    context = make_context()
    wanted = uuid.uuid4()

    await gateway.search(SearchQuery(text="q", document_ids=(wanted,)), context)

    assert index.captured_documents == [(wanted,)]
    # Beside the trusted filter, never instead of it.
    assert index.captured[0].tenant_id == context.tenant_id


async def test_a_requested_document_below_the_global_top_k_still_fills_top_k() -> None:
    """Thirty chunks of another document out-rank all five of D's.

    Run against the in-memory adapter, which promises the Qdrant adapter's
    semantics; the Qdrant twin of this test is in the integration suite.
    """
    context = make_context()
    index = InMemoryVectorIndexAdapter()
    noise, wanted = uuid.uuid4(), uuid.uuid4()

    def chunk(document_id: uuid.UUID, vector: tuple[float, ...]) -> IndexableChunk:
        return IndexableChunk(
            chunk_id=uuid.uuid4(),
            document_id=document_id,
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            domain="shared",
            content="nội dung",
            classification="internal",
            source_version="1",
            index_version="1",
            provenance_hash="a" * 64,
            acl_principals=("tenant:*",),
            vector=vector,
        )

    await index.upsert([chunk(noise, (1.0, 0.0, 0.0, 0.0)) for _ in range(30)])
    await index.upsert([chunk(wanted, (0.6, 0.8, 0.0, 0.0)) for _ in range(5)])
    gateway = make_gateway(cast(CapturingIndex, index))

    results = await gateway.search(SearchQuery(text="q", top_k=5, document_ids=(wanted,)), context)

    assert len(results) == 5
    assert {r.evidence.source_document_id for r in results} == {wanted}


# --- reranking: a precision step, never a reason for search to fail ----------


def _hit(content: str, score: float) -> VectorHit:
    return VectorHit(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        score=score,
        content=content,
        classification="internal",
        source_version="1",
        provenance_hash="a" * 64,
    )


@dataclass
class ReversingReranker:
    """Ranks the vector order backwards, so its effect is visible."""

    async def rerank(
        self, query: str, candidates: Sequence[RerankCandidate], top_k: int
    ) -> list[RerankResult]:
        return [RerankResult(id=c.id, score=0.8) for c in reversed(candidates)][:top_k]


@dataclass
class DownReranker:
    calls: int = 0

    async def rerank(
        self, query: str, candidates: Sequence[RerankCandidate], top_k: int
    ) -> list[RerankResult]:
        self.calls += 1
        raise InfrastructureError(
            "rerank request failed", details={"error": "ConnectTimeout", "model": "m"}
        )


async def test_search_uses_the_reranker_order_and_scores() -> None:
    hits = [_hit("first", 0.9), _hit("second", 0.7), _hit("third", 0.5)]
    index = CapturingIndex(hits=hits)
    telemetry = RecordingTelemetry()
    gateway = replace(make_gateway(index), reranker=ReversingReranker(), telemetry=telemetry)

    results = await gateway.search(SearchQuery(text="q", top_k=2), make_context())

    assert [r.content for r in results] == ["third", "second"]
    assert [r.evidence.relevance_score for r in results] == [0.8, 0.8]
    assert telemetry.spans == [("dw.knowledge.rerank", {"candidates": 3, "top_k": 2})]
    assert telemetry.metrics == []


async def test_a_reranker_that_is_down_keeps_the_vector_order(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hits = [_hit("first", 0.9), _hit("second", 0.7), _hit("third", 0.5)]
    index = CapturingIndex(hits=hits)
    reranker = DownReranker()
    telemetry = RecordingTelemetry()
    gateway = replace(make_gateway(index), reranker=reranker, telemetry=telemetry)
    context = make_context()

    with caplog.at_level(logging.WARNING, logger="dw_knowledge.gateway"):
        results = await gateway.search(SearchQuery(text="q", top_k=2), context)

    assert reranker.calls == 1
    assert [r.content for r in results] == ["first", "second"]
    assert [r.evidence.relevance_score for r in results] == [0.9, 0.7]
    (record,) = [r for r in caplog.records if getattr(r, "rerank_skipped", False)]
    assert record.levelno == logging.WARNING
    assert record.__dict__["error_type"] == "InfrastructureError"
    assert record.__dict__["error_cause"] == "ConnectTimeout"
    # Not only a log line: the trace has the call and the dashboard the skip.
    assert [name for name, _ in telemetry.spans] == ["dw.knowledge.rerank"]
    assert telemetry.metrics == [
        (DW_RETRIEVAL_RERANK_SKIPPED_TOTAL, 1, {"error": "ConnectTimeout"})
    ]
    # The fallback sits after the filtered search; the trusted filter is the
    # one the context dictated, whatever the reranker did.
    (trusted,) = index.captured
    assert trusted.tenant_id == context.tenant_id
    assert trusted.workspace_id == context.workspace_id
