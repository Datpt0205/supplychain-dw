"""Hard-deleting documents somebody marked deleted, once the grace is over.

The schedule is `dw_platform.retention_policy`; what is here is the SQL and the
order the two stores are touched in.

`documents.status = 'deleted'` is a soft delete: the row and its chunks stay so
the action can be undone and so a retrieval that already quoted the document can
still explain itself. That is the right default and it was also the whole
lifecycle — nothing ever removed the bytes, which is the half a data-processing
agreement is actually about.

**Two stores, and the order is not arbitrary.** Postgres is the system of record
but Qdrant is where the text actually lives in a form a search can return. Points
go first: a crash between the two then leaves a document that is invisible to
search and still in the database, which the next pass finishes. The other order
leaves points with no row behind them — text of a deleted document, in a store
that has nothing left to join against to discover it should be gone.

**A cited document is held back, not crashed on.** `evidence -> documents` is
RESTRICT and Postgres enforces it in a trigger that ignores row security, so a
naive `DELETE` on a batch containing one cited document fails the whole batch and
the sweep would make no progress, for ever, with nothing in the log but a
rollback. The citation test is therefore part of the SELECT: `NOT EXISTS` against
`knowledge.evidence`. Held-back documents are counted and logged, because a
number that stays high is how somebody finds out that evidence is not draining.

The held-back ones do drain: `SqlMemoryRetention` removes evidence once no memory
cites it, so a document blocked this hour becomes deletable a few hours after the
last memory quoting it expires. Deliberately two passes and not one transaction —
the two are different retention questions with different terms.

**Not covered here, and deliberately:** a `superseded` document, which is the
previous version of one that is still live. How many versions back to keep is a
different question from how long a deletion takes to become final, and expiring
version history under a key named `deleted_grace_days` would be answering a
question nobody asked.

`knowledge.chunks` is not deleted explicitly: `chunks.document_id` is ON DELETE
CASCADE, and referential-integrity triggers run with row security off.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.ports import UtcClock
from dw_knowledge import tables
from dw_knowledge.ports import VectorIndexPort
from dw_platform.adapters.persistence.lane_audit import append_across_tenants
from dw_platform.domain.audit import lane_audit_event
from dw_platform.retention_policy import RetentionPolicy

logger = logging.getLogger("dw_knowledge.retention")

__all__ = ["SqlKnowledgeRetention"]

# Set per transaction by this sweep and by nothing reachable from a request —
# same escape hatch the outbox, ingest and memory drains use, opened for these
# tables by migration `15276c3c92fa`.
_SET_DRAIN = text("SELECT set_config('app.worker_drain', 'on', true)")

_DELETED = "deleted"

# The worker registry name this sweep runs under: its actor on the audit log.
LANE = "retention_knowledge"
PURGED_ACTION = "knowledge.document.purged"


@dataclass(frozen=True)
class SqlKnowledgeRetention:
    """Implements `dw_worker.consumers.retention.RetentionPrunePort` for knowledge."""

    session_factory: async_sessionmaker[AsyncSession]
    policy: RetentionPolicy
    clock: UtcClock
    # Not optional. A deployment that cannot reach its vector store cannot finish
    # a deletion, and half a deletion is the state this whole module exists to
    # prevent. Without Qdrant the composition root hands over the in-memory
    # adapter, which is the same answer the ingest lane gets.
    vector_index: VectorIndexPort

    async def prune(self) -> None:
        cutoff = self.clock.now() - timedelta(days=self.policy.knowledge.deleted_grace_days)
        doomed, held = await self._candidates(cutoff)
        for document_id in doomed:
            await self.vector_index.delete_document(document_id)
        deleted = await self._delete(doomed) if doomed else 0
        if deleted or held:
            logger.info(
                "knowledge retention pass",
                extra={
                    "deleted": deleted,
                    # Not an error and not nothing: these are documents past
                    # their term that the database will not let go of yet.
                    "held_by_citations": held,
                    "policy_version": self.policy.policy_version,
                },
            )

    def _expired(self, cutoff: object) -> sa.ColumnElement[bool]:
        return sa.and_(
            tables.documents.c.status == _DELETED,
            tables.documents.c.deleted_at.is_not(None),
            tables.documents.c.deleted_at < cutoff,
        )

    def _cites(self) -> sa.Exists:
        return (
            sa.select(tables.evidence.c.evidence_id)
            .where(tables.evidence.c.source_document_id == tables.documents.c.id)
            .exists()
        )

    async def _candidates(self, cutoff: object) -> tuple[list[uuid.UUID], int]:
        """What this pass may delete, and what it must not, in one transaction.

        Read and delete are separate transactions because the vector deletes sit
        between them, and a transaction held open across a network call is a lock
        held open across a network call.
        """
        expired, cites = self._expired(cutoff), self._cites()
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_DRAIN)
            held = await session.scalar(
                sa.select(sa.func.count()).select_from(tables.documents).where(expired, cites)
            )
            rows = await session.execute(
                sa.select(tables.documents.c.id)
                .where(expired, ~cites)
                .limit(self.policy.batch_limit)
            )
            return [row.id for row in rows], held or 0

    async def _delete(self, doomed: list[uuid.UUID]) -> int:
        """The citation test again, because the read was a transaction ago.

        A memory proposed in the gap would have written evidence against one of
        these, and RESTRICT would then fail the whole batch rather than that one
        row. Re-asking inside the deleting transaction is what keeps one late
        citation from stalling every other document behind it.
        """
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_DRAIN)
            removed = (
                await session.execute(
                    sa.delete(tables.documents)
                    .where(tables.documents.c.id.in_(doomed), ~self._cites())
                    .returning(
                        tables.documents.c.id,
                        tables.documents.c.tenant_id,
                        tables.documents.c.workspace_id,
                    )
                )
            ).all()
            # A hard delete nobody can undo: one audit row per document, in its
            # own tenant, in the deleting transaction.
            now = self.clock.now()
            await append_across_tenants(
                session,
                (
                    lane_audit_event(
                        lane=LANE,
                        event_id=uuid.uuid4(),
                        tenant_id=TenantId(row.tenant_id),
                        workspace_id=WorkspaceId(row.workspace_id),
                        action=PURGED_ACTION,
                        resource_type="knowledge_document",
                        resource_id=str(row.id),
                        occurred_at=now,
                        details={"policy_version": self.policy.policy_version},
                    )
                    for row in removed
                ),
            )
            return len(removed)
