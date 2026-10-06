"""Integration: the memory ranker against a real Qdrant.

An adapter that has never run against the thing it adapts is the failure this
repository has counted twice: what a library actually does is a fact about the
outside world, and only running it settles them. Two of those facts here — that
a payload filter on three keyword fields discriminates, and that `query_points`
returns ids in similarity order — are both things the documentation asserts and
neither is worth believing untested.

The `ranker` and `indexed` fixtures are in `conftest.py`: the retention and service tests need
the same real store to prove that what deletes a row deletes its point.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence

import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import ResponseHandlingException

from dw_knowledge.adapters.hash_embedding import HashEmbeddingAdapter
from dw_memory.adapters.qdrant_ranker import QdrantMemoryRanker

pytestmark = [pytest.mark.integration]

TENANT = uuid.UUID(int=0xDD00)
OTHER_TENANT = uuid.UUID(int=0xDD01)
WORKSPACE = uuid.UUID(int=0xDD02)

Indexer = Callable[..., Awaitable[uuid.UUID]]


async def _stored(ranker: QdrantMemoryRanker, ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
    """Which of `ids` Qdrant still holds a point for, read back from the store."""
    points = await ranker.client.retrieve(
        ranker.collection, ids=[str(i) for i in ids], with_payload=False
    )
    return {uuid.UUID(str(point.id)) for point in points}


async def test_the_closest_memory_comes_first(ranker: QdrantMemoryRanker, indexed: Indexer) -> None:
    meeting = await indexed(
        "Khách muốn gặp vào buổi sáng thứ Hai", tenant=TENANT, workspace=WORKSPACE
    )
    contract = await indexed(
        "Hợp đồng sẽ ký trước ngày 20 tháng 10", tenant=TENANT, workspace=WORKSPACE
    )

    order = await ranker.nearest(
        "bao giờ ký hợp đồng",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        worker_id="demo",
        candidate_ids=(meeting, contract),
    )

    assert order, "the store returned nothing at all"
    assert order[0] == contract


async def test_another_tenants_memory_is_never_returned(
    ranker: QdrantMemoryRanker, indexed: Indexer
) -> None:
    """The SQL is the real boundary; this is the depth behind it. A filter that
    can be forgotten is one that will be, so it is asserted here too."""
    theirs = await indexed(
        "Hợp đồng sẽ ký trước ngày 20 tháng 10", tenant=OTHER_TENANT, workspace=WORKSPACE
    )

    # Even handed the id itself: the tenant condition is not replaced by the
    # id condition, it sits beside it.
    order = await ranker.nearest(
        "bao giờ ký hợp đồng",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        worker_id="demo",
        candidate_ids=(theirs,),
    )

    assert theirs not in order
    assert order == ()


async def test_another_workers_memory_is_never_returned(
    ranker: QdrantMemoryRanker, indexed: Indexer
) -> None:
    mine = await indexed(
        "Hợp đồng sẽ ký trước ngày 20 tháng 10", tenant=TENANT, workspace=WORKSPACE
    )

    order = await ranker.nearest(
        "bao giờ ký hợp đồng",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        worker_id="another_worker",
        candidate_ids=(mine,),
    )

    assert order == ()


async def test_indexing_a_store_that_is_down_does_not_raise() -> None:
    """A vector store that is down must not undo a fact already committed."""
    broken = QdrantMemoryRanker(
        client=AsyncQdrantClient(url="http://127.0.0.1:1"),
        embedder=HashEmbeddingAdapter(),
        collection="never",
    )

    await broken.index(
        memory_id=uuid.uuid4(),
        content="gì đó",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        worker_id="demo",
    )


async def test_a_width_mismatch_refuses_rather_than_dropping_everyones_vectors(
    ranker: QdrantMemoryRanker,
) -> None:
    """Recreating would be triggered by nothing more than a config value, and
    during a rolling deploy two processes would delete each other's collection."""
    wider = QdrantMemoryRanker(
        client=ranker.client,
        embedder=HashEmbeddingAdapter(_dimension=128),
        collection=ranker.collection,
    )

    with pytest.raises(ValueError, match="reindex"):
        await wider.ensure_ready()


# ------------------------------------------------- only what SQL recalled --


async def test_only_the_ids_sql_recalled_are_ranked_and_all_of_them(
    ranker: QdrantMemoryRanker, indexed: Indexer
) -> None:
    """The ranker reorders the rows recall found; it does not search the worker.

    Six memories about something else match the question word for word, three
    recalled ones do not. Asked over the whole worker with `limit=3`, Qdrant
    hands back the six decoys' best three, none of which recall found — and
    `rank_by` then silently keeps the old order, a ranker doing nothing.
    """
    question = "bao giờ ký hợp đồng"
    for _ in range(6):
        await indexed(question, tenant=TENANT, workspace=WORKSPACE)
    recalled = [
        await indexed(text, tenant=TENANT, workspace=WORKSPACE)
        for text in ("Khách thích cà phê", "Văn phòng ở tầng 3", "Hợp đồng 20 tháng 10")
    ]

    order = await ranker.nearest(
        question,
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        worker_id="demo",
        candidate_ids=recalled,
    )

    assert set(order) == set(recalled)
    assert len(order) == len(recalled)


async def test_no_candidates_asks_the_store_nothing() -> None:
    """An empty recall has nothing to order; it must not cost a search — here
    against a store that is down, so a call would raise."""
    broken = QdrantMemoryRanker(
        client=AsyncQdrantClient(url="http://127.0.0.1:1"),
        embedder=HashEmbeddingAdapter(),
        collection="never",
    )

    order = await broken.nearest(
        "câu hỏi", tenant_id=TENANT, workspace_id=WORKSPACE, worker_id="demo", candidate_ids=()
    )

    assert order == ()


# ------------------------------------------------------------- deleting --


async def test_delete_removes_exactly_the_points_it_names(
    ranker: QdrantMemoryRanker, indexed: Indexer
) -> None:
    gone = await indexed("Ký ngày 10/10.", tenant=TENANT, workspace=WORKSPACE)
    kept = await indexed("Ký ngày 20/10.", tenant=TENANT, workspace=WORKSPACE)

    await ranker.delete([gone])

    assert await _stored(ranker, [gone, kept]) == {kept}


async def test_delete_by_tenant_empties_one_tenant_and_spares_the_other(
    ranker: QdrantMemoryRanker, indexed: Indexer
) -> None:
    mine = [await indexed(f"Của tôi {n}.", tenant=TENANT, workspace=WORKSPACE) for n in range(3)]
    theirs = [
        await indexed(f"Của họ {n}.", tenant=OTHER_TENANT, workspace=WORKSPACE) for n in range(3)
    ]

    await ranker.delete_by_tenant(TENANT)

    assert await _stored(ranker, mine + theirs) == set(theirs)


async def test_deleting_from_a_collection_never_made_is_already_done(
    ranker: QdrantMemoryRanker,
) -> None:
    """A deployment that never indexed a memory has nothing to purge, and an
    offboarding that failed on that would leave a tenant half-removed."""
    fresh = QdrantMemoryRanker(
        client=ranker.client,
        embedder=HashEmbeddingAdapter(),
        collection=f"dw_memory_absent_{uuid.uuid4().hex[:8]}",
    )

    await fresh.delete([uuid.uuid4()])
    await fresh.delete_by_tenant(TENANT)


async def test_a_delete_against_a_store_that_is_down_raises() -> None:
    """Unlike `index`, which runs after a commit and must not undo it, a
    delete reports failure: whether that stops the caller is the caller's call."""
    broken = QdrantMemoryRanker(
        client=AsyncQdrantClient(url="http://127.0.0.1:1"),
        embedder=HashEmbeddingAdapter(),
        collection="never",
    )

    with pytest.raises(ResponseHandlingException):
        await broken.delete_by_tenant(TENANT)
