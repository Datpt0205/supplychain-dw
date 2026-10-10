"""What memory needs from outside itself.

One port, declared here rather than imported from the knowledge package, for the
reason `CLAUDE.md` gives: the consumer states what it needs and the composition
root satisfies it. Memory owns whether a fact may be stored; it does not own the
source material that fact came from, and it must not learn how that material is
kept in order to check a citation against it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from dw_knowledge.contracts import EvidenceRef

__all__ = ["EvidenceStorePort"]


class EvidenceStorePort(Protocol):
    """Checks that cited evidence is real, and records it.

    Takes the caller's session because the evidence and the memory it justifies
    commit together or not at all. A memory whose evidence rolled back is the
    dangling citation the whole chain exists to prevent.

    Raises rather than returning a verdict: there is no useful "store it anyway".
    That includes a reference to a document this workspace may not draw on.

    Returns the most restrictive classification among the cited documents, read
    from the documents themselves and never from the references, which carry
    only what the producer wrote down. The memory is refused if it claims less.
    """

    async def record(
        self,
        session: AsyncSession,
        refs: Sequence[EvidenceRef],
        *,
        tenant_id: UUID,
        workspace_id: UUID,
    ) -> str: ...
