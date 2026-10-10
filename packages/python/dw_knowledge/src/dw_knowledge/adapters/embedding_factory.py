"""The one place an embedder is built, for every process that embeds.

The API embeds the question and the worker embeds the chunks (and the memory
index). They must use the same model, and a width check cannot prove it: a model
swapped at the same width passes `ensure_ready` and turns retrieval into noise
without anything failing. Two builders reading the same route agree only until
one of them is edited, so there is one.

It takes the provider and the RESOLVED route, not a settings object: each
process has its own settings class, and which profile names the route is the
composition root's business.
"""

from __future__ import annotations

from typing import Protocol

from dw_kernel.errors import ConfigError
from dw_knowledge.ports import EmbeddingPort

__all__ = ["EmbeddingRoute", "build_embeddings"]


class EmbeddingRoute(Protocol):
    """What this builder reads from a model profile's `embedding` route.

    Declared here rather than importing the runtime's `ModelRoute`, which
    satisfies it: knowledge does not depend on the agent runtime.
    """

    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int | None: ...

    @property
    def timeout_seconds(self) -> int: ...


def build_embeddings(
    provider: str,
    route: EmbeddingRoute | None,
    *,
    base_url: str | None,
    api_key: str | None,
    profile_id: str,
) -> EmbeddingPort:
    if provider == "openai_compatible":
        from dw_knowledge.adapters.openai_embedding import OpenAICompatibleEmbeddingAdapter

        # The model id and the vector width are one decision, so both come from
        # the profile's route and never from a runtime default.
        if route is None or route.dimensions is None:
            raise ConfigError(
                "model profile declares no embedding route with dimensions",
                details={"profile_id": profile_id},
            )
        if not base_url or not api_key:
            raise ConfigError(
                "openai_compatible embeddings need OPENAI_BASE_URL and OPENAI_API_KEY"
            )
        return OpenAICompatibleEmbeddingAdapter(
            base_url=base_url,
            api_key=api_key,
            model=route.model,
            _dimension=route.dimensions,
            timeout=float(route.timeout_seconds),
        )
    if provider == "hash":
        # Deterministic hashing. Retrieval "works" and returns stable neighbours,
        # so the plumbing is testable, but the vectors carry no meaning.
        from dw_knowledge.adapters.hash_embedding import HashEmbeddingAdapter

        return HashEmbeddingAdapter()
    # A provider this build does not know (a retired one, a typo) must not
    # quietly become the meaningless hash vectors.
    raise ConfigError(
        "unknown embedding provider; use 'openai_compatible' or 'hash'",
        details={"embedding_provider": provider},
    )
