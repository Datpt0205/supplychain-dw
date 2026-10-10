"""Retrieval stack: embeddings, reranking, vector index.

Each is chosen by configuration and every choice has a working fallback, so a
developer gets a running retrieval path with no external services while a
deployment gets the real one. The fallbacks are not production components and
say so — ``validate_for_profile`` refuses them outside local development.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from dw_agent_runtime.model.profiles import ModelProfileRegistry
from dw_agent_runtime.registry import ConfigError
from dw_api.bootstrap.models import checked_base_url
from dw_api.settings import ApiSettings
from dw_knowledge.adapters.embedding_factory import build_embeddings as shared_build_embeddings
from dw_knowledge.ports import EmbeddingPort, RerankPort, VectorIndexPort


def build_embeddings(settings: ApiSettings, profiles: ModelProfileRegistry) -> EmbeddingPort:
    """The question is embedded by the same builder the worker embeds chunks with.

    Retrieval must embed the query exactly as ingestion embedded the chunks, so
    both read the profile's route through `dw_knowledge`'s one builder.
    """
    return shared_build_embeddings(
        settings.embedding_provider,
        profiles.resolve(settings.model_profile).embedding,
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
        profile_id=settings.model_profile,
    )


def build_reranker(settings: ApiSettings) -> RerankPort | None:
    """None means the vector order stands, which is what "none" asks for."""
    if settings.rerank_provider == "none":
        return None
    if settings.rerank_provider == "cohere_compatible":
        if not settings.rerank_base_url or not settings.rerank_api_key:
            raise ConfigError(
                "cohere_compatible reranking needs DW_API_RERANK_BASE_URL and DW_API_RERANK_API_KEY"
            )
        # Checked here, not on the first search: a URL the client cannot use
        # fails every call, and the gateway would turn each failure into a
        # quietly unranked search with only a log line to show for it.
        if settings.is_deployed and urlsplit(settings.rerank_base_url).scheme != "https":
            raise ConfigError(
                "a deployed reranker is reached over https; the key travels in a header",
                details={"scheme": urlsplit(settings.rerank_base_url).scheme or "<none>"},
            )
        base_url = checked_base_url(settings, settings.rerank_base_url)
        from dw_knowledge.adapters.cohere_rerank import CohereCompatibleRerankAdapter

        return CohereCompatibleRerankAdapter(
            base_url=base_url,
            api_key=settings.rerank_api_key,
            model=settings.rerank_model,
            timeout=settings.rerank_timeout_seconds,
        )
    raise ConfigError(
        "unknown rerank provider; use 'cohere_compatible' or 'none'",
        details={"rerank_provider": settings.rerank_provider},
    )


def build_vector_index(settings: ApiSettings) -> VectorIndexPort:
    if settings.qdrant_url:
        from qdrant_client import AsyncQdrantClient

        from dw_knowledge.adapters.qdrant_index import QdrantVectorIndexAdapter

        return QdrantVectorIndexAdapter(
            client=AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key),
            collection=settings.qdrant_collection,
        )
    # In-memory, and therefore never durable: the index dies with the process.
    from dw_knowledge.adapters.memory_index import InMemoryVectorIndexAdapter

    return InMemoryVectorIndexAdapter()
