"""Which remembered facts reach the model when there are more than fit.

Recall narrows by tenant, workspace, worker, clearance and validity — five
conditions, each a boundary, each with a negative test. What it could not do is
choose WHICH twelve of ninety live facts about one account are the twelve worth
sending. It ordered by confidence, which answers "what were we surest of", not
"what is this about".

The design decision that matters here is that similarity **ranks** and never
**filters**. The rows are exactly the rows the SQL already returned; the vector
store only says what order to read them in, and anything it has never seen goes
last rather than disappearing. Three consequences, and they are the reason to
build it this way:

- An index that is empty, stale, or poisoned cannot produce a WRONG answer. It
  can only produce a worse ORDER. Every authorization decision stays in the
  query that already has mutation-proven tests.
- Memories written before the index existed still surface. Filtering on ids from
  the store would have made them vanish silently, which is the worst shape a
  regression can take.
- The store is asked about those rows and no others. `nearest` takes the ids
  the SQL recalled and orders exactly them; it never searches the worker's whole
  collection, where memories about other subjects would crowd out the ones
  recall found and leave `rank_by` with nothing to apply.

The index is still kept in step with the rows, because a point is an embedding
of a memory's content: when retention deletes a row, supersession closes it, or
a tenant leaves, `MemoryVectorPurgePort` deletes the point too. That is a
data-lifecycle duty, not a correctness one — a stray point can no longer reach
an answer, but it is the tenant's content in a store nobody else sweeps.

So this module is honestly a nice-to-have on top of a correct answer, and it is
built so it can never become load-bearing by accident.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from typing import Protocol

logger = logging.getLogger("dw_memory.ranking")

__all__ = ["MemoryRankerPort", "MemoryVectorPurgePort", "rank_by"]


class MemoryRankerPort(Protocol):
    """Orders a tenant's memory ids by similarity to a question.

    `candidate_ids` are the rows recall already chose, and the answer orders
    those and nothing else. Required, with no default: the bound that keeps the
    store from answering a different question has to be impossible to leave off.

    Tenancy is a parameter and not optional: the store is asked only for one
    tenant's points. That is defence in depth rather than the boundary — the
    boundary is the SQL — but a filter that can be forgotten is one that will be.
    """

    async def nearest(
        self,
        query: str,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID,
        worker_id: str,
        candidate_ids: Sequence[uuid.UUID],
    ) -> tuple[uuid.UUID, ...]: ...


class MemoryVectorPurgePort(Protocol):
    """Deletes memory vectors whose rows are gone or closed.

    Separate from `MemoryRankerPort`: recall only orders, and retention,
    supersession and offboarding only delete. Both raise on failure; each
    caller decides whether that stops it.
    """

    async def delete(self, memory_ids: Sequence[uuid.UUID]) -> None: ...

    async def delete_by_tenant(self, tenant_id: uuid.UUID) -> None: ...


def rank_by[ItemT](
    order: Sequence[uuid.UUID], items: Sequence[tuple[uuid.UUID, ItemT]]
) -> list[ItemT]:
    """Reorder `items` to follow `order`; anything unranked keeps its place, last.

    Stable on purpose, and `sorted` already is: rows the ranker has never seen
    keep the order the query chose — confidence, then recency — which is a
    sensible answer for a fact it has no opinion about. Sorting them among
    themselves would replace a considered order with an arbitrary one.
    """
    position = {memory_id: index for index, memory_id in enumerate(order)}
    unranked = len(position)
    return [value for _, value in sorted(items, key=lambda pair: position.get(pair[0], unranked))]
