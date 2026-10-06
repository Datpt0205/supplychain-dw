"""Probe the configured reranker with one live call.

Usage (reads DW_API_RERANK_* from the environment; `make check-rerank` exports
`.env`):
    make check-rerank

Built the way the API builds it (`build_reranker` over `ApiSettings`), so a pass
here means the API's own search reaches the reranker this deployment
configured. One Vietnamese query and three documents, one of them the answer:
a reranker that works ranks it first. The key is never printed; the endpoint is
shown by host only.

Exit code is non-zero when the call fails, no reranker is configured, or the
answer is not ranked first.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
import time
from urllib.parse import urlsplit

import httpx

_QUERY = "Chính sách đổi trả hàng trong bao nhiêu ngày?"
_DOCUMENTS = {
    "answer": "Khách hàng được đổi trả sản phẩm trong vòng 30 ngày kể từ ngày mua.",
    "nearby": "Thời gian bảo hành chảo chống dính là 12 tháng.",
    "unrelated": "Hà Nội là thủ đô của Việt Nam.",
}


class _StatusRecorder(httpx.AsyncBaseTransport):
    """httpx's own network transport, keeping the status the provider answered."""

    def __init__(self) -> None:
        self._inner = httpx.AsyncHTTPTransport()
        self.statuses: list[int] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        self.statuses.append(response.status_code)
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


async def probe() -> int:
    from dw_api.bootstrap.knowledge import build_reranker
    from dw_api.settings import ApiSettings
    from dw_knowledge.ports import RerankCandidate

    settings = ApiSettings()
    host = urlsplit(settings.rerank_base_url or "").hostname or "(no DW_API_RERANK_BASE_URL)"
    print(f"provider={settings.rerank_provider} model={settings.rerank_model} endpoint={host}")
    try:
        reranker = build_reranker(settings)
    except Exception as exc:  # a probe reports the failure, it does not trace it
        print(f"FAIL configuration: {type(exc).__name__}: {exc}")
        return 1
    if reranker is None:
        print("FAIL no reranker configured (DW_API_RERANK_PROVIDER=none)")
        return 1

    from dw_knowledge.adapters.cohere_rerank import CohereCompatibleRerankAdapter

    if not isinstance(reranker, CohereCompatibleRerankAdapter):
        print(f"FAIL this probe knows no reranker of type {type(reranker).__name__}")
        return 1
    # The API's adapter as built, with only its transport swapped for one that
    # records what the provider actually answered.
    recorder = _StatusRecorder()
    reranker = dataclasses.replace(reranker, transport=recorder)
    candidates = [RerankCandidate(id=key, text=text) for key, text in _DOCUMENTS.items()]
    started = time.perf_counter()
    try:
        ranked = await reranker.rerank(_QUERY, candidates, top_k=len(candidates))
    except Exception as exc:  # a probe reports every failure
        details = getattr(exc, "details", {})
        print(f"FAIL status={_status(recorder)} {type(exc).__name__}: {exc} {details}")
        return 1
    latency_ms = (time.perf_counter() - started) * 1000

    print(f"query: {_QUERY}")
    for position, result in enumerate(ranked, start=1):
        print(f"  {position}. {result.score:.4f}  [{result.id}] {_DOCUMENTS[result.id]}")
    ok = bool(ranked) and ranked[0].id == "answer"
    print(f"{'ok  ' if ok else 'FAIL'} status={_status(recorder)} latency={latency_ms:.0f}ms")
    return 0 if ok else 1


def _status(recorder: _StatusRecorder) -> str:
    """The provider's status, or "none" when no answer came back at all."""
    return ",".join(str(code) for code in recorder.statuses) or "none"


def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    return asyncio.run(probe())


if __name__ == "__main__":
    raise SystemExit(main())
