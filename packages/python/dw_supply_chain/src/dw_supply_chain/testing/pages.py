"""Paging for in-memory fakes, honouring the repositories' keyset contract.

A fake that answered a paged port with every row at once would pass a test the
real repository fails (failure-modes #3); these page the way the SQL does:
newest first, strictly past the cursor, one surplus row deciding `next_cursor`.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Any, Protocol

from dw_kernel.pagination import (
    CursorPosition,
    Page,
    PageQuery,
    PageRequest,
    build_page,
    page_request,
)


def newest_first_page[ItemT](
    items: Sequence[ItemT],
    request: PageRequest,
    position_of: Callable[[ItemT], CursorPosition],
) -> Page[ItemT]:
    """`items` in any order, paged newest first by `position_of`."""
    ordered = sorted(
        items, key=lambda item: (position_of(item).sort_value, position_of(item).tiebreaker)
    )
    ordered.reverse()
    if request.after is not None:
        after = request.after
        ordered = [
            item
            for item in ordered
            if (position_of(item).sort_value, position_of(item).tiebreaker)
            < (after.sort_value, after.tiebreaker)
        ]
    return build_page(ordered[: request.fetch_limit], request=request, position_of=position_of)


class _Timed(Protocol):
    @property
    def occurred_at(self) -> datetime: ...


def history_page[TimedT: _Timed](history: Sequence[TimedT], request: PageRequest) -> Page[TimedT]:
    """A case's history kept oldest first (as a fake appends it), paged newest
    first. The row id the SQL breaks ties with is the entry's position here,
    stable while the history only grows."""
    tiebreak = {id(entry): uuid.UUID(int=index) for index, entry in enumerate(history)}
    return newest_first_page(
        history,
        request,
        lambda entry: CursorPosition(sort_value=entry.occurred_at, tiebreaker=tiebreak[id(entry)]),
    )


class _Case(Protocol):
    @property
    def created_at(self) -> datetime | None: ...
    @property
    def id(self) -> Any: ...


def case_position(case: _Case) -> CursorPosition:
    """Where a case sits in a newest-first list: `(created_at, id)`, as the
    case lists' SQL orders it."""
    assert case.created_at is not None  # a stored case carries it
    return CursorPosition(sort_value=case.created_at, tiebreaker=case.id.value)


_WHOLE = PageQuery(key="testing.whole_listing")


async def oldest_first[ItemT](
    fetch: Callable[[PageRequest], Awaitable[Page[ItemT]]],
) -> list[ItemT]:
    """Every page of a newest-first listing, oldest first: a whole history,
    for an assertion about it. Small pages, so a test also walks the cursor."""
    found: list[ItemT] = []
    cursor: str | None = None
    while True:
        page = await fetch(page_request(limit=_WALK_PAGE, cursor=cursor, query=_WHOLE))
        found.extend(page.items)
        if page.next_cursor is None:
            return list(reversed(found))
        cursor = page.next_cursor


# Small, so a history of a few steps already spans more than one page.
_WALK_PAGE = 2
