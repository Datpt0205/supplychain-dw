"""The hosted rerank dialect, pinned without a live call.

What must hold: the body names ``documents`` (a ``texts`` body is a 400 from
the provider), the key travels only in the Authorization header, each result
index maps back to the candidate it was sent as, and a failure says what kind
of failure it was without carrying the key or the provider's body.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import replace

import httpx
import pytest

from dw_kernel.errors import InfrastructureError
from dw_knowledge.adapters.cohere_rerank import CohereCompatibleRerankAdapter
from dw_knowledge.ports import RerankCandidate

pytestmark = pytest.mark.unit

KEY = "sk-test-not-a-real-key"
CANDIDATES = [
    RerankCandidate(id="chunk-a", text="Bảo hành 12 tháng."),
    RerankCandidate(id="chunk-b", text="Đổi trả trong 30 ngày."),
    RerankCandidate(id="chunk-c", text="Hà Nội là thủ đô."),
]


def _adapter() -> CohereCompatibleRerankAdapter:
    return CohereCompatibleRerankAdapter(
        base_url="https://rerank.invalid/v1/", api_key=KEY, model="bge-reranker-v2-m3"
    )


def _patch_client(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    real_init = httpx.AsyncClient.__init__

    def fake_init(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
        kwargs["transport"] = httpx.MockTransport(handler)
        real_init(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx.AsyncClient, "__init__", fake_init)


def _results(*rows: tuple[int, float]) -> dict[str, object]:
    return {
        "id": "r1",
        "model": "bge-reranker-v2-m3",
        "object": "rerank",
        "results": [{"index": i, "relevance_score": s, "document": None} for i, s in rows],
    }


async def test_request_shape_and_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_results((1, 0.9), (0, 0.2), (2, 0.01)))

    _patch_client(monkeypatch, handler)
    await _adapter().rerank("đổi trả mấy ngày?", CANDIDATES, top_k=2)

    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == "https://rerank.invalid/v1/rerank"
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    body = json.loads(request.content.decode("utf-8"))
    assert body == {
        "model": "bge-reranker-v2-m3",
        "query": "đổi trả mấy ngày?",
        "documents": [c.text for c in CANDIDATES],
        "top_n": 2,
    }
    assert KEY not in request.content.decode("utf-8")


async def test_indexes_map_back_to_candidates_best_first(monkeypatch: pytest.MonkeyPatch) -> None:
    # Deliberately unsorted: the order is the provider's habit, not a contract.
    _patch_client(
        monkeypatch,
        lambda _r: httpx.Response(200, json=_results((2, 0.01), (1, 0.9), (0, 0.2))),
    )

    ranked = await _adapter().rerank("q", CANDIDATES, top_k=2)

    assert [(r.id, r.score) for r in ranked] == [("chunk-b", 0.9), ("chunk-a", 0.2)]


@pytest.mark.parametrize("bad_index", [3, -1])
async def test_an_index_that_was_never_sent_is_refused(
    monkeypatch: pytest.MonkeyPatch, bad_index: int
) -> None:
    _patch_client(
        monkeypatch, lambda _r: httpx.Response(200, json=_results((0, 0.5), (bad_index, 0.9)))
    )

    with pytest.raises(InfrastructureError) as raised:
        await _adapter().rerank("q", CANDIDATES, top_k=3)
    assert raised.value.details["index"] == bad_index


async def test_a_repeated_index_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, lambda _r: httpx.Response(200, json=_results((0, 0.5), (0, 0.4))))

    with pytest.raises(InfrastructureError):
        await _adapter().rerank("q", CANDIDATES, top_k=3)


async def test_http_error_carries_status_but_not_key_or_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": f"invalid key {KEY}"})

    _patch_client(monkeypatch, handler)

    with pytest.raises(InfrastructureError) as raised:
        await _adapter().rerank("q", CANDIDATES, top_k=3)

    assert raised.value.details == {
        "model": "bge-reranker-v2-m3",
        "error": "HTTPStatusError",
        "status": 401,
    }
    assert KEY not in str(raised.value)
    assert KEY not in repr(raised.value.details)


async def test_transport_error_is_an_infrastructure_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    _patch_client(monkeypatch, handler)

    with pytest.raises(InfrastructureError) as raised:
        await _adapter().rerank("q", CANDIDATES, top_k=3)
    assert raised.value.details["error"] == "ConnectTimeout"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json={"detail": "no results key"}),
        httpx.Response(200, json={"results": [{"index": 0}]}),
    ],
)
async def test_an_unreadable_answer_is_an_infrastructure_error(
    monkeypatch: pytest.MonkeyPatch, response: httpx.Response
) -> None:
    _patch_client(monkeypatch, lambda _r: response)

    with pytest.raises(InfrastructureError) as raised:
        await _adapter().rerank("q", CANDIDATES, top_k=3)
    assert KEY not in repr(raised.value.details)


async def test_nothing_to_rerank_means_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    _patch_client(monkeypatch, handler)

    assert await _adapter().rerank("q", [], top_k=5) == []


@pytest.mark.parametrize("rows", [(), ((1, 0.9),)])
async def test_fewer_results_than_asked_is_a_failed_rerank(
    monkeypatch: pytest.MonkeyPatch, rows: tuple[tuple[int, float], ...]
) -> None:
    # Taken as an answer, the chunks it left out would drop out of the search
    # rather than keep their vector order.
    _patch_client(monkeypatch, lambda _r: httpx.Response(200, json=_results(*rows)))

    with pytest.raises(InfrastructureError) as raised:
        await _adapter().rerank("q", CANDIDATES, top_k=2)
    assert raised.value.details["error"] == "missing_results"
    assert raised.value.details["asked"] == 2


async def test_the_timeout_bounds_the_whole_call() -> None:
    # A provider that answers slower than the deadline. MockTransport applies
    # none of httpx's per-phase timeouts, so only a deadline on the call as a
    # whole can stop this one.
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json=_results((0, 0.9), (1, 0.5), (2, 0.1)))

    adapter = replace(_adapter(), timeout=0.05, transport=httpx.MockTransport(slow))
    started = time.perf_counter()

    with pytest.raises(InfrastructureError) as raised:
        await adapter.rerank("q", CANDIDATES, top_k=3)

    assert raised.value.details["error"] == "TimeoutError"
    assert time.perf_counter() - started < 2
