"""Which retrieval components the API builds, and which settings it refuses.

An unknown provider must stop startup: read as the default, a retired value
like "tei" would quietly become hash vectors (no meaning) or no reranking.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dw_agent_runtime.model.profiles import ModelProfileRegistry
from dw_agent_runtime.registry import ConfigError
from dw_api.bootstrap.knowledge import build_embeddings, build_reranker
from dw_api.settings import ApiSettings
from dw_kernel.net_guard import OutboundUrlError
from dw_knowledge.adapters.cohere_rerank import CohereCompatibleRerankAdapter

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[4]


def _settings(**overrides: object) -> ApiSettings:
    base: dict[str, object] = {
        "embedding_provider": "hash",
        "rerank_provider": "none",
        "rerank_base_url": None,
        "rerank_api_key": None,
    }
    base.update(overrides)
    return ApiSettings(**base)  # type: ignore[arg-type]


def _profiles() -> ModelProfileRegistry:
    profiles = ModelProfileRegistry()
    profiles.load_directory(REPO_ROOT / "configs" / "models")
    return profiles


def test_no_reranker_by_default() -> None:
    assert build_reranker(_settings()) is None


def test_cohere_compatible_builds_the_hosted_adapter() -> None:
    reranker = build_reranker(
        _settings(
            rerank_provider="cohere_compatible",
            rerank_base_url="https://rerank.invalid/v1",
            rerank_api_key="unit-test-key",
            rerank_model="bge-reranker-v2-m3",
            rerank_timeout_seconds=5,
        )
    )
    assert isinstance(reranker, CohereCompatibleRerankAdapter)
    assert reranker.base_url == "https://rerank.invalid/v1"
    assert reranker.model == "bge-reranker-v2-m3"
    assert reranker.timeout == 5


@pytest.mark.parametrize(
    "missing", [{"rerank_base_url": None}, {"rerank_api_key": None}, {"rerank_api_key": ""}]
)
def test_cohere_compatible_without_url_or_key_is_refused(missing: dict[str, object]) -> None:
    complete: dict[str, object] = {
        "rerank_provider": "cohere_compatible",
        "rerank_base_url": "https://rerank.invalid/v1",
        "rerank_api_key": "unit-test-key",
    }
    settings = _settings(**(complete | missing))
    with pytest.raises(ConfigError):
        build_reranker(settings)


_HOSTED: dict[str, object] = {
    "rerank_provider": "cohere_compatible",
    "rerank_api_key": "unit-test-key",
}


@pytest.mark.parametrize(
    "url", ["rerank.invalid/v1", "ftp://rerank.invalid/v1", "https://user:pw@rerank.invalid/v1"]
)
def test_a_base_url_the_client_cannot_use_is_refused_at_startup(url: str) -> None:
    # Accepted, each of these fails every call, and the gateway would answer
    # every search unranked with only a log line to say so.
    with pytest.raises(OutboundUrlError):
        build_reranker(_settings(**_HOSTED, rerank_base_url=url))


def test_a_deployed_reranker_is_refused_over_plain_http() -> None:
    settings = _settings(
        **_HOSTED,
        profile="uat",
        rerank_base_url="http://rerank.invalid/v1",
        outbound_allowed_hosts=["rerank.invalid"],
    )
    with pytest.raises(ConfigError) as raised:
        build_reranker(settings)
    assert raised.value.details == {"scheme": "http"}


def test_a_deployed_reranker_over_https_builds() -> None:
    settings = _settings(
        **_HOSTED,
        profile="uat",
        rerank_base_url="https://rerank.invalid/v1",
        outbound_allowed_hosts=["rerank.invalid"],
    )
    assert isinstance(build_reranker(settings), CohereCompatibleRerankAdapter)


@pytest.mark.parametrize("provider", ["tei", "cohere"])
def test_an_unknown_rerank_provider_is_refused(provider: str) -> None:
    with pytest.raises(ConfigError) as raised:
        build_reranker(_settings(rerank_provider=provider))
    assert raised.value.details == {"rerank_provider": provider}


@pytest.mark.parametrize("provider", ["tei", "openai"])
def test_an_unknown_embedding_provider_is_refused_not_hashed(provider: str) -> None:
    with pytest.raises(ConfigError) as raised:
        build_embeddings(_settings(embedding_provider=provider), _profiles())
    assert raised.value.details == {"embedding_provider": provider}


def test_hash_embeddings_still_build_locally() -> None:
    from dw_knowledge.adapters.hash_embedding import HashEmbeddingAdapter

    assert isinstance(build_embeddings(_settings(), _profiles()), HashEmbeddingAdapter)


def test_a_blank_env_line_means_the_default_as_it_does_in_compose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DW_API_EMBEDDING_PROVIDER", "")
    monkeypatch.setenv("DW_API_RERANK_PROVIDER", "")
    settings = ApiSettings()
    assert settings.embedding_provider == "hash"
    assert settings.rerank_provider == "none"
