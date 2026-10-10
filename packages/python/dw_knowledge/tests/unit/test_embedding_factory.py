"""One answer to "which model embeds this deployment's text".

The API embeds the question and the worker embeds the chunks. Two builders that
each read the profile route agree only until one of them is edited: a model
swapped at the same width passes every dimension check and turns retrieval into
noise, with nothing failing.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

from dw_kernel.errors import ConfigError
from dw_knowledge.adapters.embedding_factory import build_embeddings
from dw_knowledge.adapters.hash_embedding import HashEmbeddingAdapter
from dw_knowledge.adapters.openai_embedding import OpenAICompatibleEmbeddingAdapter

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[5]
FACTORY = REPO_ROOT / "packages/python/dw_knowledge/src/dw_knowledge/adapters/embedding_factory.py"
EMBEDDING_ADAPTERS = {"OpenAICompatibleEmbeddingAdapter", "HashEmbeddingAdapter"}


@dataclass(frozen=True)
class _Route:
    model: str = "text-embedding-3-large"
    dimensions: int | None = 3072
    timeout_seconds: int = 120


def _build(provider: str = "openai_compatible", route: _Route | None = None) -> object:
    return build_embeddings(
        provider,
        _Route() if route is None else route,
        base_url="https://gateway.invalid/v1",
        api_key="unit-test-key",
        profile_id="gateway",
    )


def test_the_route_decides_model_width_and_timeout() -> None:
    adapter = _build()
    assert isinstance(adapter, OpenAICompatibleEmbeddingAdapter)
    assert (adapter.model, adapter.dimension, adapter.timeout) == (
        "text-embedding-3-large",
        3072,
        120.0,
    )


@pytest.mark.parametrize("provider", ["tei", "openai"])
def test_an_unknown_provider_is_refused_not_hashed(provider: str) -> None:
    with pytest.raises(ConfigError) as raised:
        _build(provider)
    assert raised.value.details == {"embedding_provider": provider}


def test_a_route_without_dimensions_is_refused() -> None:
    with pytest.raises(ConfigError):
        _build(route=_Route(dimensions=None))


def test_a_profile_without_an_embedding_route_is_refused() -> None:
    with pytest.raises(ConfigError) as raised:
        build_embeddings(
            "openai_compatible", None, base_url="https://g.invalid", api_key="k", profile_id="luna"
        )
    assert raised.value.details == {"profile_id": "luna"}


@pytest.mark.parametrize(
    ("base_url", "api_key"), [(None, "k"), ("https://g.invalid", None), ("", "")]
)
def test_the_gateway_needs_a_url_and_a_key(base_url: str | None, api_key: str | None) -> None:
    with pytest.raises(ConfigError):
        build_embeddings(
            "openai_compatible", _Route(), base_url=base_url, api_key=api_key, profile_id="gateway"
        )


def test_hash_needs_no_route() -> None:
    assert isinstance(
        build_embeddings("hash", None, base_url=None, api_key=None, profile_id="x"),
        HashEmbeddingAdapter,
    )


def test_only_the_factory_constructs_an_embedding_adapter() -> None:
    """A second builder is exactly a second place to construct one of these."""
    sources = [
        *REPO_ROOT.glob("apps/*/src/**/*.py"),
        *REPO_ROOT.glob("packages/python/*/src/**/*.py"),
    ]
    constructing = []
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in EMBEDDING_ADAPTERS
            ):
                constructing.append(path.resolve())
    assert constructing, "the scan found nothing - it is not looking where the code is"
    assert set(constructing) == {FACTORY.resolve()}
