"""The worker refuses an embedding provider it does not know.

Read as the default it would ingest with hash vectors (no meaning) into a
collection the API searches with real ones, which no later check catches.
"""

from __future__ import annotations

import pytest

from dw_agent_runtime.registry import ConfigError
from dw_worker.composition import build_embeddings
from dw_worker.settings import WorkerSettings

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("provider", ["tei", "openai"])
def test_an_unknown_embedding_provider_is_refused_not_hashed(provider: str) -> None:
    with pytest.raises(ConfigError) as raised:
        build_embeddings(WorkerSettings(embedding_provider=provider))
    assert raised.value.details == {"embedding_provider": provider}


def test_hash_embeddings_still_build_locally() -> None:
    from dw_knowledge.adapters.hash_embedding import HashEmbeddingAdapter

    assert isinstance(
        build_embeddings(WorkerSettings(embedding_provider="hash")), HashEmbeddingAdapter
    )


def test_a_blank_env_line_means_the_default_as_it_does_in_compose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DW_WORKER_EMBEDDING_PROVIDER", "")
    assert WorkerSettings().embedding_provider == "hash"


def test_the_api_and_the_worker_build_the_same_embedder_from_one_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The API embeds the question, the worker embeds the chunks: one answer.

    Built from the variables compose hands both processes, the two adapters must
    name the same model at the same width, or retrieval compares vectors from two
    different spaces and nothing fails.
    """
    from pathlib import Path

    from dw_agent_runtime.model.profiles import ModelProfileRegistry
    from dw_api.bootstrap.knowledge import build_embeddings as api_build_embeddings
    from dw_api.settings import ApiSettings
    from dw_knowledge.adapters.openai_embedding import OpenAICompatibleEmbeddingAdapter

    monkeypatch.setenv("DW_API_EMBEDDING_PROVIDER", "openai_compatible")
    monkeypatch.setenv("DW_WORKER_EMBEDDING_PROVIDER", "openai_compatible")
    monkeypatch.setenv("DW_API_MODEL_PROFILE", "gateway")
    monkeypatch.delenv("DW_WORKER_MODEL_PROFILE", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.invalid/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-key")
    profiles = ModelProfileRegistry()
    profiles.load_directory(Path(__file__).resolve().parents[4] / "configs" / "models")

    api = api_build_embeddings(ApiSettings(), profiles)
    worker = build_embeddings(WorkerSettings())

    assert isinstance(api, OpenAICompatibleEmbeddingAdapter)
    assert isinstance(worker, OpenAICompatibleEmbeddingAdapter)
    assert (api.model, api.dimension, api.timeout) == (
        worker.model,
        worker.dimension,
        worker.timeout,
    )
    assert api.model == profiles.resolve("gateway").embedding.model  # type: ignore[union-attr]
