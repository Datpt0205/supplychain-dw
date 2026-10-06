"""Records the evidence a memory cites — after checking it is real.

`EvidenceRef` validates its own shape: a 64-character hex `provenance_hash`, a
relevance score in range, UUIDs where UUIDs belong. None of that is a claim about
the world. Nothing recomputed the hash, nothing compared it to a stored chunk, and
there was no foreign key on `source_document_id`, so a caller could hand over a
syntactically perfect reference to source material that never existed and have it
written as the justification for a stored fact. The repository's own eval grader
does exactly that with `provenance_hash="b" * 64` — legitimately, because it is
testing a policy in memory, but it shows how little the shape proves.

So this checks the reference against the chunk it names before writing it:

- The chunk must exist, in this tenant. RLS is already set on the session, so a
  chunk in another tenant is simply not there.
- Its `provenance_hash` must equal the one the reference carries. That hash is
  computed over the chunk's content at ingest, so a match means the quote came
  from material this deployment actually holds.
- Its `document_id` must equal the reference's `source_document_id`, or the
  citation points at one document while quoting another.
- Its document must be one the memory's workspace may draw on: its own, or a
  global one — `tables.visible_from_workspace`, the rule listing and full read
  apply. RLS stops at the tenant, so without this one team's material became
  another team's remembered fact.

The classification recorded is the DOCUMENT's, never the reference's. The
reference's copy is whatever the producer wrote down; the document row is the
owner of that fact. `record` returns the most restrictive of them, so the
caller can refuse a memory that claims to be less sensitive than its sources.

A reference with no `chunk_id` is refused rather than recorded unverified.
`EvidenceRef` allows it and the retrieval gateway never produces one — and
accepting it would be the whole loophole: name a real document, invent the hash,
and nothing could tell. Evidence that cannot be checked is not evidence, and
widening this later is a deliberate decision rather than an oversight today.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from dw_kernel.errors import DomainError
from dw_kernel.ports import UtcClock
from dw_knowledge import tables
from dw_knowledge.contracts import EvidenceRef, classification_rank

__all__ = ["SqlEvidenceStore"]


@dataclass(frozen=True)
class SqlEvidenceStore:
    """Implements ``dw_memory.ports.EvidenceStorePort`` over ``knowledge.evidence``."""

    clock: UtcClock

    async def record(
        self,
        session: AsyncSession,
        refs: Sequence[EvidenceRef],
        *,
        tenant_id: UUID,
        workspace_id: UUID,
    ) -> str:
        """Verify every reference, write them, and return the strictest
        classification among the documents cited. Raises before writing anything.

        Runs in the caller's session on purpose: the evidence and the memory it
        justifies are one fact, and a memory that committed while its evidence
        rolled back would be exactly the dangling citation this exists to prevent.
        """
        if not refs:
            raise DomainError("a memory cannot be stored without evidence")

        without_chunk = [str(ref.evidence_id) for ref in refs if ref.chunk_id is None]
        if without_chunk:
            raise DomainError(
                "evidence must name the chunk it came from",
                details={"evidence_ids": ", ".join(sorted(without_chunk))},
            )

        rows = (
            await session.execute(
                sa.select(
                    tables.chunks.c.id,
                    tables.chunks.c.document_id,
                    tables.chunks.c.provenance_hash,
                    tables.documents.c.classification,
                    tables.visible_from_workspace(workspace_id).label("visible"),
                )
                .join(tables.documents, tables.documents.c.id == tables.chunks.c.document_id)
                .where(tables.chunks.c.id.in_([ref.chunk_id for ref in refs]))
            )
        ).all()
        stored = {row.id: row for row in rows}

        for ref in refs:
            chunk = stored.get(ref.chunk_id)
            if chunk is None:
                raise DomainError(
                    "evidence cites a chunk this tenant does not have",
                    details={"chunk_id": str(ref.chunk_id)},
                )
            if chunk.provenance_hash != ref.provenance_hash:
                raise DomainError(
                    "evidence hash does not match the chunk it cites",
                    details={"chunk_id": str(ref.chunk_id)},
                )
            if chunk.document_id != ref.source_document_id:
                raise DomainError(
                    "evidence cites one document and quotes another",
                    details={
                        "chunk_id": str(ref.chunk_id),
                        "cited_document_id": str(ref.source_document_id),
                    },
                )
            if not chunk.visible:
                raise DomainError(
                    "evidence cites a document from another workspace",
                    details={"chunk_id": str(ref.chunk_id)},
                )

        try:
            strictest: str = max((row.classification for row in rows), key=classification_rank)
        except ValueError as exc:
            # A document labelled off the ladder: ranking it would be a guess
            # about who may read the memory built on it.
            raise DomainError("evidence cites a document with an unknown classification") from exc

        now = self.clock.now()
        await session.execute(
            sa.insert(tables.evidence),
            [
                {
                    "evidence_id": ref.evidence_id,
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "source_document_id": ref.source_document_id,
                    "chunk_id": ref.chunk_id,
                    "source_version": ref.source_version,
                    "page": ref.page,
                    "start_offset": ref.start_offset,
                    "end_offset": ref.end_offset,
                    "quote": ref.quote,
                    "relevance_score": ref.relevance_score,
                    "classification": stored[ref.chunk_id].classification,
                    "provenance_hash": ref.provenance_hash,
                    "created_at": now,
                }
                for ref in refs
            ],
        )
        return strictest
