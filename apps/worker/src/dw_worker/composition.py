"""Worker composition root: wires the knowledge ingest pipeline from settings.

Mirrors the API's knowledge wiring but on the worker side, where the parse
layer lives. Only the composition root imports concrete adapters
(Clean/Hexagonal rule); consumers receive already-wired ports.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from dw_agent_runtime.model.profiles import ModelProfileRegistry, ModelRoute
from dw_kernel.errors import InfrastructureError
from dw_kernel.ports import SystemClock, Uuid7Generator
from dw_knowledge.adapters.api_parsers import (
    DeepgramTranscriptParser,
    GatewayFileParser,
    PlaintextAttachmentParser,
)
from dw_knowledge.adapters.composite_parser import CompositeParser
from dw_knowledge.attachment_policy import load_attachment_policy
from dw_knowledge.gateway import KnowledgeGateway
from dw_knowledge.ingest_jobs import IngestJobStore
from dw_knowledge.ports import DocumentParserPort, EmbeddingPort, ObjectStoragePort, VectorIndexPort
from dw_worker.settings import WorkerSettings

if TYPE_CHECKING:
    from minio import Minio

    from dw_supply_chain.adapters.storage.minio_case_documents import MinioCaseDocumentStorage

# The image sets DW_REPO_ROOT=/app; outside a container the checkout root is
# four levels up from this file.
REPO_ROOT = Path(os.environ.get("DW_REPO_ROOT", str(Path(__file__).resolve().parents[4])))
ATTACHMENT_POLICY = "attachment_ingest@1.1.0.yaml"


@dataclass
class IngestComponents:
    engine: AsyncEngine
    gateway: KnowledgeGateway
    job_store: IngestJobStore
    parser: DocumentParserPort
    object_storage: ObjectStoragePort


def _build_minio_client(settings: WorkerSettings) -> Minio:
    from minio import Minio

    assert settings.s3_endpoint_url is not None
    endpoint = settings.s3_endpoint_url.replace("http://", "").replace("https://", "")
    return Minio(
        endpoint,
        access_key=settings.s3_access_key or "",
        secret_key=settings.s3_secret_key or "",
        secure=settings.s3_endpoint_url.startswith("https://"),
    )


def build_object_storage(settings: WorkerSettings) -> ObjectStoragePort:
    """Public: a context's own lane may stage artifacts in the same bucket."""
    from dw_knowledge.adapters.minio_storage import MinioObjectStorageAdapter

    return MinioObjectStorageAdapter(
        client=_build_minio_client(settings), bucket=settings.s3_bucket
    )


def build_export_bucket(settings: WorkerSettings) -> ObjectStoragePort:
    """Tenant offboarding's export destination — the `dw-exports` bucket
    already used elsewhere, not a new one made for this feature."""
    from dw_knowledge.adapters.minio_storage import MinioObjectStorageAdapter

    return MinioObjectStorageAdapter(
        client=_build_minio_client(settings), bucket=settings.s3_bucket_exports
    )


def build_feedback_bucket(settings: WorkerSettings) -> ObjectStoragePort:
    """The same bucket `dw_api`'s feedback attachments live in — offboarding
    reads and deletes from it, `dw_knowledge`'s generic adapter is reused
    rather than depending on `dw_api`'s own (see
    `dw_worker.consumers.offboarding.BucketPort`'s docstring)."""
    from dw_knowledge.adapters.minio_storage import MinioObjectStorageAdapter

    return MinioObjectStorageAdapter(
        client=_build_minio_client(settings), bucket=settings.feedback_bucket
    )


def build_case_documents_bucket(settings: WorkerSettings) -> ObjectStoragePort:
    """Supply Chain's case-documents bucket, in the shape offboarding reads
    every bucket through (the same reuse as `build_feedback_bucket`)."""
    from dw_knowledge.adapters.minio_storage import MinioObjectStorageAdapter

    return MinioObjectStorageAdapter(
        client=_build_minio_client(settings), bucket=settings.case_documents_bucket
    )


def build_case_document_storage(settings: WorkerSettings) -> MinioCaseDocumentStorage:
    """The same bucket through Supply Chain's own adapter, which lists with
    each object's last-modified time: what the orphan sweep needs."""
    from dw_supply_chain.adapters.storage.minio_case_documents import MinioCaseDocumentStorage

    return MinioCaseDocumentStorage(
        client=_build_minio_client(settings), bucket=settings.case_documents_bucket
    )


def build_embeddings(settings: WorkerSettings) -> EmbeddingPort:
    """Public because two lanes need it: knowledge ingestion, and the memory
    index. A second builder would be a second answer to "which model embeds
    this deployment's text", and the copy nobody edits keeps the old model."""
    """The index's shape comes from config, never from a runtime default.

    The model id and the vector width are one decision, so they live together on
    the profile's embedding route rather than half in YAML and half in an env
    var that a second process could disagree about.
    """
    if settings.embedding_provider == "openai_compatible":
        from dw_knowledge.adapters.openai_embedding import OpenAICompatibleEmbeddingAdapter

        route = _embedding_route(settings)
        assert route.dimensions is not None  # enforced by ModelProfile
        return OpenAICompatibleEmbeddingAdapter(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            model=route.model,
            _dimension=route.dimensions,
            timeout=float(route.timeout_seconds),
        )
    if settings.embedding_provider == "tei" and settings.embed_url:
        from dw_knowledge.adapters.tei_embedding import TeiEmbeddingAdapter

        return TeiEmbeddingAdapter(base_url=settings.embed_url, _dimension=settings.embed_dimension)
    from dw_knowledge.adapters.hash_embedding import HashEmbeddingAdapter

    return HashEmbeddingAdapter()


def _embedding_route(settings: WorkerSettings) -> ModelRoute:
    profiles = ModelProfileRegistry()
    profiles.load_directory(REPO_ROOT / "configs" / "models")
    profile = profiles.resolve(settings.model_profile)
    if profile.embedding is None:
        raise InfrastructureError(
            "the model profile declares no embedding route",
            details={"profile_id": settings.model_profile},
        )
    if not settings.openai_base_url or not settings.openai_api_key:
        raise InfrastructureError(
            "openai_compatible embeddings need OPENAI_BASE_URL and OPENAI_API_KEY"
        )
    return profile.embedding


def build_vector_index(settings: WorkerSettings) -> VectorIndexPort:
    if settings.qdrant_url:
        from qdrant_client import AsyncQdrantClient

        from dw_knowledge.adapters.qdrant_index import QdrantVectorIndexAdapter

        return QdrantVectorIndexAdapter(
            client=AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key),
            collection=settings.qdrant_collection,
        )
    from dw_knowledge.adapters.memory_index import InMemoryVectorIndexAdapter

    return InMemoryVectorIndexAdapter()


def build_parser(settings: WorkerSettings) -> DocumentParserPort:
    """Route by extension: plaintext in process, everything else to an API.

    Public because a context's own lane reads PDFs through the same parser: a
    report an agent fetches and the same report a person uploads must chunk
    identically, or a search returns two shapes of one document.

    Order matters only in that each parser answers `supports` for its own kinds;
    the policy is the single place that decides which kind a file is.
    """
    policy = load_attachment_policy(REPO_ROOT / "configs" / "policies" / ATTACHMENT_POLICY)
    parsers: list[DocumentParserPort] = [PlaintextAttachmentParser(policy=policy)]

    if settings.openai_base_url and settings.openai_api_key:
        parsers.append(
            GatewayFileParser(
                base_url=settings.openai_base_url,
                api_key=settings.openai_api_key,
                model=settings.extraction_model,
                policy=policy,
            )
        )
    if settings.deepgram_api_key:
        parsers.append(DeepgramTranscriptParser(api_key=settings.deepgram_api_key, policy=policy))
    # A missing key is not silently a missing format: CompositeParser raises
    # "no parser supports ..." and the job records that as its error, which is
    # what the upload status shows.
    return CompositeParser(parsers=parsers)


def build_ingest_components(settings: WorkerSettings) -> IngestComponents | None:
    """Return wired ingest components, or ``None`` if infra is not configured."""
    if not (settings.database_url and settings.s3_endpoint_url):
        return None

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    clock = SystemClock()
    id_generator = Uuid7Generator()
    storage = build_object_storage(settings)

    gateway = KnowledgeGateway(
        session_factory=session_factory,
        vector_index=build_vector_index(settings),
        embeddings=build_embeddings(settings),
        object_storage=storage,
        clock=clock,
        id_generator=id_generator,
    )
    job_store = IngestJobStore(
        session_factory=session_factory,
        clock=clock,
        id_generator=id_generator,
        retry_backoff_seconds=settings.ingest_retry_backoff_seconds,
    )
    return IngestComponents(
        engine=engine,
        gateway=gateway,
        job_store=job_store,
        parser=build_parser(settings),
        object_storage=storage,
    )
