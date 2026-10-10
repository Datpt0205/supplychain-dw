"""Memory service: candidate → policy decision → (maybe) stored item.

A stored memory is a claim this system will repeat as fact, so what it rests on is
written with it, in one transaction, and checked first:

- the evidence it cites is verified against the chunks it names and recorded in
  `knowledge.evidence`, so `evidence_id` resolves to something;
- it is stored at a classification no lower than the documents it cites, so
  recall's clearance filter reads the sources' label and not the producer's;
- `memory.item_evidence` ties the item to that evidence with foreign keys, so the
  citation cannot name a row that was never written;
- the write is audited, because a fact appearing in a customer's system with
  nobody able to say when it was learned is the thing an audit trail is for.

All four writes share the service's transaction. A memory that committed while its
evidence rolled back would be precisely the dangling citation this prevents.

A candidate the policy holds for REVIEW opens a `memory.review` approval in the
same transaction (`dw_memory.review`). When a person decides it,
`settle_review` writes the item through the very same checks (the decision
stands in for the confidence threshold, never for the evidence) or records the
refusal.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_kernel.errors import ConflictError, DomainError, NotFoundError, PermissionDeniedError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.pagination import CursorPosition, Page, PageRequest, build_page
from dw_kernel.ports import IdGenerator, UtcClock
from dw_knowledge.contracts import EvidenceRef, classification_rank, classifications_for_clearance
from dw_memory import tables
from dw_memory.contracts import MEMORY_SCHEMA_VERSION, MemoryItem, WriteDecision
from dw_memory.policy import MemoryCandidate, MemoryWritePolicy, PolicyOutcome
from dw_memory.ports import EvidenceStorePort
from dw_memory.ranking import MemoryRankerPort, MemoryVectorPurgePort, rank_by
from dw_memory.review import MEMORY_REVIEW
from dw_platform.adapters.persistence.keyset import after_position, newest_first
from dw_platform.adapters.persistence.repositories import (
    SqlApprovalRepository,
    SqlAuditRepository,
)
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ApprovalAudience
from dw_platform.domain.approval import ApprovalRequest, ApprovalStatus
from dw_platform.domain.audit import AuditEvent

logger = logging.getLogger("dw_memory.service")

_SET_TENANT = text("SELECT set_config('app.tenant_id', :tenant_id, true)")

# How many recalled facts may reach one model call. A ceiling, not a tuning
# knob: recall runs on every step, and an unbounded list would grow the prompt
# with the agent's own output until compaction fought it.
DEFAULT_RECALL_LIMIT = 12

# How many live memories a ranker may reorder. Wider than the cap so the
# ranker has something to choose from, bounded so one account with years of
# history does not load its whole past to pick twelve rows.
RANKING_POOL = 100

# The one retention class a memory is written with, and so the only one the
# pinned retention policy may name for memory: a class nothing assigns is a
# term in a compliance file the code does not keep. A second class arrives with
# the decision that assigns it — a legal hold with the route that sets one.
RETENTION_CLASS = "default"

# One action per outcome, so "what did this worker learn, and what did it decline
# to learn" are both answerable from the trail rather than only the first.
_ACTION = {
    WriteDecision.AUTO_WRITE: "memory.item_written",
    WriteDecision.REVIEW: "memory.write_held_for_review",
    WriteDecision.REJECT: "memory.write_rejected",
}


# What became of a held candidate once a person decided. Its own actions, not
# `_ACTION`'s: "written because a person approved it" and "written because the
# policy needed nobody" are different answers to who is accountable for a fact.
_WRITTEN_AFTER_REVIEW = "memory.written_after_review"
_REVIEW_REJECTED = "memory.review_rejected"
_REVIEW_FAILED = "memory.review_failed"


@dataclass(frozen=True)
class ProposalResult:
    candidate_id: uuid.UUID
    outcome: PolicyOutcome
    item: MemoryItem | None


@dataclass(frozen=True)
class ReviewCandidate:
    """What a reviewer reads before deciding: the claim and what it cites.

    Served by `get_candidate` under the reader's clearance. The approval carries
    identifiers and the label only, so the inbox, which every holder of
    `approvals.read` sees, never shows the content.
    """

    candidate_id: uuid.UUID
    worker_id: str
    memory_type: str
    content: str
    structured_facts: dict[str, object]
    subject_refs: tuple[str, ...]
    fact_key: str | None
    provenance_refs: tuple[dict[str, object], ...]
    classification: str
    confidence: float
    decision: str
    memory_id: uuid.UUID | None
    created_by_run_id: uuid.UUID
    created_at: datetime


@dataclass
class MemoryService:
    session_factory: async_sessionmaker[AsyncSession]
    policy: MemoryWritePolicy
    clock: UtcClock
    id_generator: IdGenerator
    # Verifies a citation against the source material before it is written. The
    # policy above decides whether a fact is worth keeping; this decides whether
    # its stated reason is real, which no amount of confidence can substitute for.
    evidence_store: EvidenceStorePort
    # Orders what recall found when there is more of it than fits. Optional, and
    # it can only ever change the ORDER — see `dw_memory.ranking`.
    ranker: MemoryRankerPort | None = None
    # Deletes the vector of a memory this proposal superseded. Optional because
    # a deployment without a vector store has no points to delete.
    vector_purge: MemoryVectorPurgePort | None = None

    async def propose(
        self,
        candidate: MemoryCandidate,
        context: AccessContext,
        *,
        created_by_run_id: uuid.UUID,
        idempotency_key: uuid.UUID | None = None,
    ) -> ProposalResult:
        """Decide on a candidate and, if it passes, store it with its evidence.

        `idempotency_key` is for callers that may be asked twice — the outbox
        delivers at least once, so its handler will be. Passing the event's id
        makes the candidate row's primary key deterministic, and the second
        delivery finds it already there and returns what was decided the first
        time instead of writing a second memory. Without a key the behaviour is
        unchanged: a fresh id every call, which is right for a caller that means
        each proposal to be its own.
        """
        outcome = self.policy.evaluate(candidate)
        candidate_id = idempotency_key or self.id_generator.new_uuid()
        now = self.clock.now()

        item: MemoryItem | None = None
        if outcome.decision is WriteDecision.AUTO_WRITE:
            item = MemoryItem(
                memory_id=self.id_generator.new_uuid(),
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                worker_id=candidate.worker_id,
                memory_type=candidate.memory_type,
                subject_refs=candidate.subject_refs,
                content=candidate.content,
                structured_facts=dict(candidate.structured_facts),
                provenance_refs=candidate.provenance_refs,
                confidence=outcome.confidence,
                classification=candidate.classification,
                valid_from=now,
                retention_policy=RETENTION_CLASS,
                memory_schema_version=MEMORY_SCHEMA_VERSION,
                created_by_run_id=created_by_run_id,
                fact_key=candidate.fact_key,
            )

        # Bound before the branch: a REVIEW or REJECT stores no item and closes
        # nothing, but the audit event is written either way and reads this.
        superseded: tuple[uuid.UUID, ...] = ()
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_TENANT, {"tenant_id": str(context.tenant_id)})
            if idempotency_key is not None:
                seen = await self._already_decided(session, idempotency_key)
                if seen is not None:
                    return seen
            await session.execute(
                sa.insert(tables.write_candidates).values(
                    id=candidate_id,
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    worker_id=candidate.worker_id,
                    memory_type=candidate.memory_type.value,
                    content=candidate.content,
                    structured_facts=dict(candidate.structured_facts),
                    provenance_refs=[
                        ref.model_dump(mode="json") for ref in candidate.provenance_refs
                    ],
                    confidence=outcome.confidence,
                    classification=candidate.classification,
                    decision=outcome.decision.value,
                    memory_id=item.memory_id if item else None,
                    created_by_run_id=created_by_run_id,
                    created_at=now,
                    subject_refs=list(candidate.subject_refs),
                    fact_key=candidate.fact_key,
                )
            )
            approval_id: uuid.UUID | None = None
            if item is not None:
                # Raising in here rolls back the candidate row with the item.
                superseded = await self._store_item(session, item, now=now)
            elif outcome.decision is WriteDecision.REVIEW:
                # The label is about to be stamped on an approval, where it
                # decides who reads the content and who may decide. A producer's
                # claim is not enough for that: checked against the cited
                # documents now, by the same verification the write uses, and
                # undone, because the evidence is recorded when the item is.
                verifying = await session.begin_nested()
                try:
                    await self._verify_evidence(
                        session,
                        candidate.provenance_refs,
                        claimed=candidate.classification,
                        tenant_id=context.tenant_id,
                        workspace_id=context.workspace_id,
                    )
                finally:
                    await verifying.rollback()
                # Same transaction as the candidate: a held fact with no approval
                # is a fact nobody is ever asked about, which is all "review"
                # meant before this existed.
                approval_id = await self._open_review(
                    session, context, candidate, candidate_id, outcome
                )
            await SqlAuditRepository(session).append(
                self._audit_event(
                    context,
                    candidate,
                    outcome,
                    item,
                    candidate_id,
                    created_by_run_id,
                    now,
                    superseded=superseded,
                    approval_id=approval_id,
                )
            )
        await self._purge_vectors(superseded)
        return ProposalResult(candidate_id=candidate_id, outcome=outcome, item=item)

    async def _store_item(
        self, session: AsyncSession, item: MemoryItem, *, now: datetime
    ) -> tuple[uuid.UUID, ...]:
        """Verify the evidence, store the item, close what it supersedes and tie
        it to its evidence. Returns the memories it closed.

        The one write path for an item, whether the policy wrote it alone or a
        person approved it. An approval stands in for the confidence threshold,
        never for the citation check, and a second copy of these steps is where
        the two would drift apart.
        """
        # Before the item: a reference that fails verification must not leave a
        # memory behind.
        await self._verify_evidence(
            session,
            item.provenance_refs,
            claimed=item.classification,
            tenant_id=item.tenant_id,
            workspace_id=item.workspace_id,
        )
        await session.execute(
            sa.insert(tables.items).values(
                memory_id=item.memory_id,
                tenant_id=item.tenant_id,
                workspace_id=item.workspace_id,
                worker_id=item.worker_id,
                memory_type=item.memory_type.value,
                subject_refs=list(item.subject_refs),
                content=item.content,
                structured_facts=dict(item.structured_facts),
                provenance_refs=[ref.model_dump(mode="json") for ref in item.provenance_refs],
                confidence=item.confidence,
                classification=item.classification,
                valid_from=item.valid_from,
                valid_until=item.valid_until,
                retention_policy=item.retention_policy,
                memory_schema_version=item.memory_schema_version,
                created_by_run_id=item.created_by_run_id,
                fact_key=item.fact_key,
                created_at=now,
            )
        )
        superseded = await self._close_superseded(session, item, now=now)
        await session.execute(
            sa.insert(tables.item_evidence),
            [
                {
                    "memory_id": item.memory_id,
                    "evidence_id": ref.evidence_id,
                    "tenant_id": item.tenant_id,
                }
                for ref in item.provenance_refs
            ],
        )
        return superseded

    async def _verify_evidence(
        self,
        session: AsyncSession,
        refs: Sequence[EvidenceRef],
        *,
        claimed: str,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> None:
        """Verify and record the citations, and hold the claimed label to them.

        Refused, not raised to the cited label: the policy has already decided
        on the claimed one, and raising it now would write (or put before a
        reviewer) a fact the "restricted always needs review" rule never saw at
        its real level. A claim ABOVE the sources stands — the more restrictive
        label is the producer's to choose.
        """
        cited = await self.evidence_store.record(
            session, refs, tenant_id=tenant_id, workspace_id=workspace_id
        )
        if classification_rank(claimed) < classification_rank(cited):
            raise DomainError(
                "memory claims a lower classification than the evidence it cites",
                details={"claimed": claimed, "evidence": cited},
            )

    async def _open_review(
        self,
        session: AsyncSession,
        context: AccessContext,
        candidate: MemoryCandidate,
        candidate_id: uuid.UUID,
        outcome: PolicyOutcome,
    ) -> uuid.UUID:
        """One `memory.review` approval for a held candidate; returns its id.

        No run: the run that produced the fact has finished, and an approval
        bound to it would make `decide` look for a run waiting on it. The payload
        is identifiers and the label: the inbox shows it to every holder of
        `approvals.read`, and the content is served apart, to a clearance that
        covers it (`get_candidate`). The label is also what
        `require_clearance_for_review` checks the decider against.
        """
        approval_id = self.id_generator.new_uuid()
        await SqlApprovalRepository(session).add(
            ApprovalRequest(
                id=approval_id,
                tenant_id=TenantId(context.tenant_id),
                workspace_id=WorkspaceId(context.workspace_id),
                approval_type=MEMORY_REVIEW,
                requested_by=UserId(context.principal_id),
                reason=outcome.reason,
                payload={
                    "candidate_id": str(candidate_id),
                    "worker_id": candidate.worker_id,
                    "memory_type": candidate.memory_type.value,
                    "classification": candidate.classification,
                },
            )
        )
        return approval_id

    async def settle_review(
        self, approval_id: uuid.UUID, context: AccessContext
    ) -> MemoryItem | None:
        """Act on a decided `memory.review`: write the item, or record the refusal.

        `context` carries the tenant and workspace of the decided approval and,
        as principal, the person who decided it. What was decided is read from
        the approval row, which the decision wrote, not from the event that
        announced it.

        Returns the item when the candidate is (or already was) written, None
        when it is not. Idempotent: a redelivery finds the candidate settled and
        writes nothing a second time. Raises `DomainError` when the evidence no
        longer verifies; the refusal is committed first (candidate settled as
        `reject`, `memory.review_failed` on the trail), because the evidence
        will not become valid on a retry.
        """
        now = self.clock.now()
        superseded: tuple[uuid.UUID, ...] = ()
        failure: DomainError | None = None
        item: MemoryItem | None = None
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_TENANT, {"tenant_id": str(context.tenant_id)})
            # Read as the decider with no scopes (the worker's context), so the
            # visibility rule of ADR 0004 applies to this read as to any other:
            # a `memory.review` is never stamped (`_open_review`), so it is seen.
            request = await SqlApprovalRepository(session).get(
                approval_id,
                workspace_id=context.workspace_id,
                audience=ApprovalAudience(context=context, holds_decide=False),
            )
            if (
                request is None
                or request.approval_type != MEMORY_REVIEW
                or request.workspace_id.value != context.workspace_id
            ):
                raise NotFoundError(
                    "memory review approval not found", details={"approval_id": str(approval_id)}
                )
            row = await self._candidate_row(
                session, uuid.UUID(str(request.payload["candidate_id"])), context, lock=True
            )
            if row is None:
                raise NotFoundError(
                    "memory candidate not found", details={"approval_id": str(approval_id)}
                )
            if row.memory_id is not None:
                return await self._stored_item(session, row.memory_id)
            if row.decision != WriteDecision.REVIEW.value:
                return None
            if request.status is ApprovalStatus.PENDING:
                raise ConflictError(
                    "memory review is not decided yet", details={"approval_id": str(approval_id)}
                )
            if request.status is not ApprovalStatus.APPROVED:
                await self._settle_refused(session, row.id)
                await self._review_audit(session, context, row, _REVIEW_REJECTED, request, now=now)
                return None
            item = self._item_from_candidate(row, now=now)
            try:
                # A savepoint, so a refusal undoes the item and keeps the
                # transaction for recording why.
                async with session.begin_nested():
                    superseded = await self._store_item(session, item, now=now)
            except DomainError as exc:
                failure, item = exc, None
                await self._settle_refused(session, row.id)
                await self._review_audit(
                    session,
                    context,
                    row,
                    _REVIEW_FAILED,
                    request,
                    now=now,
                    extra={"error": exc.message},
                )
            else:
                await session.execute(
                    sa.update(tables.write_candidates)
                    .where(tables.write_candidates.c.id == row.id)
                    .values(memory_id=item.memory_id)
                )
                await self._review_audit(
                    session,
                    context,
                    row,
                    _WRITTEN_AFTER_REVIEW,
                    request,
                    now=now,
                    resource_id=item.memory_id,
                    extra={
                        "fact_key": item.fact_key,
                        "superseded": [str(memory_id) for memory_id in superseded],
                    },
                )
        if failure is not None:
            raise failure
        await self._purge_vectors(superseded)
        return item

    async def get_candidate(
        self, candidate_id: uuid.UUID, context: AccessContext
    ) -> ReviewCandidate:
        """A held candidate's content, for the person deciding on it.

        Same three boundaries as recall: tenant by RLS, the caller's workspace,
        and a clearance that covers the label. Another workspace's candidate is
        not found; one above the caller's clearance is refused, since its
        existence and label are already on the approval they can see.
        """
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_TENANT, {"tenant_id": str(context.tenant_id)})
            row = await self._candidate_row(session, candidate_id, context, lock=False)
        if row is None:
            raise NotFoundError(
                "memory candidate not found", details={"candidate_id": str(candidate_id)}
            )
        if row.classification not in classifications_for_clearance(context.clearance):
            raise PermissionDeniedError(
                "your clearance does not cover this memory candidate",
                details={"candidate_id": str(candidate_id)},
            )
        return ReviewCandidate(
            candidate_id=row.id,
            worker_id=row.worker_id,
            memory_type=row.memory_type,
            content=row.content,
            structured_facts=dict(row.structured_facts),
            subject_refs=tuple(row.subject_refs),
            fact_key=row.fact_key,
            provenance_refs=tuple(row.provenance_refs),
            classification=row.classification,
            confidence=row.confidence,
            decision=row.decision,
            memory_id=row.memory_id,
            created_by_run_id=row.created_by_run_id,
            created_at=row.created_at,
        )

    @staticmethod
    async def _candidate_row(
        session: AsyncSession, candidate_id: uuid.UUID, context: AccessContext, *, lock: bool
    ) -> sa.Row[Any] | None:
        query = sa.select(tables.write_candidates).where(
            tables.write_candidates.c.id == candidate_id,
            tables.write_candidates.c.workspace_id == context.workspace_id,
        )
        # Locked when settling: two deliveries of one decision racing past the
        # "already settled?" check would otherwise both write.
        if lock:
            query = query.with_for_update()
        return (await session.execute(query)).first()

    def _item_from_candidate(self, row: sa.Row[Any], *, now: datetime) -> MemoryItem:
        """The item a held candidate describes, valid from the approval on."""
        return MemoryItem.model_validate(
            {
                "memory_id": self.id_generator.new_uuid(),
                "tenant_id": row.tenant_id,
                "workspace_id": row.workspace_id,
                "worker_id": row.worker_id,
                "memory_type": row.memory_type,
                "subject_refs": tuple(row.subject_refs),
                "content": row.content,
                "structured_facts": dict(row.structured_facts),
                "provenance_refs": tuple(row.provenance_refs),
                "confidence": row.confidence,
                "classification": row.classification,
                "valid_from": now,
                "retention_policy": RETENTION_CLASS,
                "memory_schema_version": MEMORY_SCHEMA_VERSION,
                "created_by_run_id": row.created_by_run_id,
                "fact_key": row.fact_key,
            }
        )

    @staticmethod
    async def _settle_refused(session: AsyncSession, candidate_id: uuid.UUID) -> None:
        """Mark a held candidate as never to be written. `memory_id` stays NULL;
        the trail says whether a person refused it or its evidence did."""
        await session.execute(
            sa.update(tables.write_candidates)
            .where(tables.write_candidates.c.id == candidate_id)
            .values(decision=WriteDecision.REJECT.value)
        )

    @staticmethod
    async def _stored_item(session: AsyncSession, memory_id: uuid.UUID) -> MemoryItem | None:
        stored = (
            await session.execute(
                sa.select(tables.items).where(tables.items.c.memory_id == memory_id)
            )
        ).first()
        if stored is None:
            return None
        return MemoryItem.model_validate(
            {
                **dict(stored._mapping),
                "subject_refs": tuple(stored.subject_refs),
                "provenance_refs": tuple(stored.provenance_refs),
            }
        )

    async def _review_audit(
        self,
        session: AsyncSession,
        context: AccessContext,
        row: sa.Row[Any],
        action: str,
        request: ApprovalRequest,
        *,
        now: datetime,
        resource_id: uuid.UUID | None = None,
        extra: dict[str, object] | None = None,
    ) -> None:
        """Who decided, on which approval, about which candidate. The actor is
        the decider: the run's actor proposed the fact, this person settled it."""
        await SqlAuditRepository(session).append(
            AuditEvent(
                id=self.id_generator.new_uuid(),
                tenant_id=TenantId(context.tenant_id),
                workspace_id=WorkspaceId(context.workspace_id),
                actor_id=UserId(context.principal_id),
                action=action,
                resource_type="memory_item",
                resource_id=str(resource_id or row.id),
                run_id=row.created_by_run_id,
                trace_id=None,
                details={
                    "candidate_id": str(row.id),
                    "approval_id": str(request.id),
                    "worker_id": row.worker_id,
                    "memory_type": row.memory_type,
                    "classification": row.classification,
                    "evidence_count": len(row.provenance_refs),
                    "policy_version": self.policy.policy_version,
                    **(extra or {}),
                },
                occurred_at=now.astimezone(UTC),
            )
        )

    async def _purge_vectors(self, superseded: tuple[uuid.UUID, ...]) -> None:
        """Delete the points of memories this proposal closed, after the commit.

        Recall never reads a closed memory, so its point has no reader left. A
        store that is down is logged, not raised: the new memory is committed,
        failing here would send it back round the outbox's retry loop, and the
        stray point can no longer reach an answer because `nearest` only orders
        ids recall chose. Offboarding still removes it with the tenant.
        """
        if not superseded or self.vector_purge is None:
            return
        try:
            await self.vector_purge.delete(superseded)
        except Exception:
            logger.warning(
                "could not delete superseded memory vectors; they stay until offboarding",
                extra={"superseded": [str(memory_id) for memory_id in superseded]},
                exc_info=True,
            )

    async def _already_decided(
        self, session: AsyncSession, candidate_id: uuid.UUID
    ) -> ProposalResult | None:
        """What was decided for this candidate id, if it has been seen before.

        A read inside the caller's transaction, deliberately: checking on one
        connection and writing on another leaves the window where two deliveries
        both find nothing. The candidate table's primary key is the backstop if
        two workers race past this check at the same instant — one of them gets a
        unique violation and the delivery is retried, which is the correct
        outcome for at-least-once.
        """
        row = (
            await session.execute(
                sa.select(
                    tables.write_candidates.c.decision,
                    tables.write_candidates.c.memory_id,
                    tables.write_candidates.c.confidence,
                ).where(tables.write_candidates.c.id == candidate_id)
            )
        ).first()
        if row is None:
            return None
        item = await self._stored_item(session, row.memory_id) if row.memory_id else None
        decision = WriteDecision(row.decision)
        return ProposalResult(
            candidate_id=candidate_id,
            # The reason is not stored on the candidate row, and inventing one
            # here would put words in the first decision's mouth. The decision
            # itself is what a caller acts on.
            outcome=PolicyOutcome(
                decision=decision, reason="already decided", confidence=row.confidence
            ),
            item=item,
        )

    async def _close_superseded(
        self, session: AsyncSession, item: MemoryItem, *, now: datetime
    ) -> tuple[uuid.UUID, ...]:
        """Close the live memories this one answers over, and say which.

        Two memories sharing a `fact_key` AND a subject are two answers to one
        question; the later one is the answer now. Closing is `valid_until = now`
        — the row, its provenance and its audit entry all stay, so "what did we
        believe last Tuesday" is still answerable. Deleting would make the system
        unable to explain a decision it had already made.

        In the caller's transaction, deliberately: a memory superseded by one
        whose evidence then failed verification would leave the customer with no
        live answer at all.

        A memory with no key supersedes nothing. That is the compatibility story
        and also the correct default — an episode does not replace an episode.
        """
        if item.fact_key is None or not item.subject_refs:
            return ()
        closed = await session.execute(
            sa.update(tables.items)
            .where(
                tables.items.c.workspace_id == item.workspace_id,
                tables.items.c.worker_id == item.worker_id,
                tables.items.c.fact_key == item.fact_key,
                tables.items.c.memory_id != item.memory_id,
                tables.items.c.valid_until.is_(None),
                tables.items.c.subject_refs.op("?|")(
                    sa.literal(list(item.subject_refs), sa.ARRAY(sa.Text))
                ),
            )
            .values(valid_until=now)
            .returning(tables.items.c.memory_id)
        )
        return tuple(row.memory_id for row in closed.all())

    def _audit_event(
        self,
        context: AccessContext,
        candidate: MemoryCandidate,
        outcome: PolicyOutcome,
        item: MemoryItem | None,
        candidate_id: uuid.UUID,
        created_by_run_id: uuid.UUID,
        now: datetime,
        *,
        superseded: tuple[uuid.UUID, ...] = (),
        approval_id: uuid.UUID | None = None,
    ) -> AuditEvent:
        """What was learned, on whose evidence, and under which policy.

        The policy version is recorded because thresholds move: a fact auto-written
        at 0.80 confidence should still read as having been auto-written under the
        rules of the day, not judged against whatever the threshold becomes.
        """
        return AuditEvent(
            id=self.id_generator.new_uuid(),
            tenant_id=TenantId(context.tenant_id),
            workspace_id=WorkspaceId(context.workspace_id),
            actor_id=UserId(context.principal_id),
            action=_ACTION[outcome.decision],
            resource_type="memory_item",
            # The item when there is one, else the candidate that was not stored: a
            # refusal has to be as addressable as a write.
            resource_id=str(item.memory_id) if item is not None else str(candidate_id),
            run_id=created_by_run_id,
            trace_id=None,
            details={
                "worker_id": candidate.worker_id,
                "memory_type": candidate.memory_type.value,
                "confidence": outcome.confidence,
                "classification": candidate.classification,
                "reason": outcome.reason,
                "policy_version": self.policy.policy_version,
                "evidence_count": len(candidate.provenance_refs),
                # Which memories this one closed, and under what name. A fact
                # that silently replaces another is the same trail problem as a
                # setting that changes without saying what changed: the row is
                # still there, but nothing connects it to what replaced it.
                "fact_key": candidate.fact_key,
                "superseded": [str(memory_id) for memory_id in superseded],
                # The approval a held fact now waits on, so the trail can follow
                # it from "held" to whoever settled it.
                **({"approval_id": str(approval_id)} if approval_id is not None else {}),
            },
            occurred_at=now.astimezone(UTC),
        )

    async def list_items(self, context: AccessContext, request: PageRequest) -> Page[MemoryItem]:
        """Tenant-scoped long-term memory inventory (newest first, resumable).

        Ordered by ``created_at`` rather than ``valid_from``: the two differ for a
        backdated fact, and only ``created_at`` is monotonic with insertion, which
        is what makes a cursor position stable while the agent keeps writing.
        """
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_TENANT, {"tenant_id": str(context.tenant_id)})
            rows = await session.execute(
                sa.select(tables.items)
                .where(
                    tables.items.c.workspace_id == context.workspace_id,
                    after_position(
                        tables.items.c.created_at, tables.items.c.memory_id, request.after
                    ),
                )
                .order_by(*newest_first(tables.items.c.created_at, tables.items.c.memory_id))
                .limit(request.fetch_limit)
            )
            # Paged on the rows, then mapped: ``created_at`` carries the sort
            # position and is not a field of MemoryItem, so the cursor has to be
            # minted while the row is still in hand.
            page = build_page(
                rows.all(),
                request=request,
                position_of=lambda row: CursorPosition(
                    sort_value=row.created_at, tiebreaker=row.memory_id
                ),
            )
            return page.map_items(
                lambda row: MemoryItem.model_validate(
                    {
                        **dict(row._mapping),
                        "subject_refs": tuple(row.subject_refs),
                        "provenance_refs": tuple(row.provenance_refs),
                    }
                )
            )

    async def recall(
        self,
        context: AccessContext,
        *,
        worker_id: str,
        subject_refs: Sequence[str],
        now: datetime,
        limit: int = DEFAULT_RECALL_LIMIT,
        query: str | None = None,
    ) -> tuple[MemoryItem, ...]:
        """What this worker already knows about these subjects, for this caller.

        The read side of memory. `propose` has been able to write since the
        provenance work; nothing read it back, which made every stored fact
        write-only — the shape this repository keeps producing, and the reason
        `list_items` (an inventory screen) is not the same thing as recall.

        Four conditions, and each one is a boundary rather than a preference:

        - **tenant**: the GUC is set from the verified context and RLS enforces
          it, the same as every other read here;
        - **workspace**: a tenant's two teams do not share what they learned;
        - **worker**: a fact another worker wrote was learned under a different
          prompt and toolset, and carrying it over is how one worker's mistake
          becomes another's premise;
        - **clearance**: a memory carries the classification of the material it
          was learned from, so a run may only recall what it could have read
          directly — resolved through `classifications_for_clearance`, the same
          ladder retrieval uses, never a second table.

        Plus validity: a fact whose window has closed is history, not memory.

        Ordered by confidence then recency, because what reaches the model is
        capped and the cap should drop the least-supported claim rather than an
        arbitrary one. An empty `subject_refs` recalls nothing: a run that is
        about no particular record has no basis to pull one record's facts in,
        and "no subject" must not read as "every subject".
        """
        # A shortcut, not the enforcement: `?|` against an empty array matches
        # no row either, so the behaviour holds without this line. It is here to
        # skip a round trip that can only return nothing.
        if not subject_refs:
            return ()
        allowed = classifications_for_clearance(context.clearance)
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_TENANT, {"tenant_id": str(context.tenant_id)})
            rows = await session.execute(
                sa.select(tables.items)
                .where(
                    tables.items.c.workspace_id == context.workspace_id,
                    tables.items.c.worker_id == worker_id,
                    tables.items.c.classification.in_(allowed),
                    # JSONB `?|`: the stored subject list overlaps the asked-for
                    # one. Done in SQL rather than by filtering in Python, so the
                    # limit below applies to matching rows and not to whatever
                    # the first page happened to hold.
                    #
                    # The right operand is typed `text[]` explicitly. Left to
                    # infer, SQLAlchemy binds a Python list as JSONB and Postgres
                    # answers `operator does not exist: jsonb ?| jsonb` — which
                    # only a real database says, and is the reason this is tested
                    # against one.
                    tables.items.c.subject_refs.op("?|")(
                        sa.literal(list(subject_refs), sa.ARRAY(sa.Text))
                    ),
                    tables.items.c.valid_from <= now,
                    sa.or_(
                        tables.items.c.valid_until.is_(None),
                        tables.items.c.valid_until > now,
                    ),
                )
                .order_by(
                    tables.items.c.confidence.desc(),
                    tables.items.c.created_at.desc(),
                    tables.items.c.memory_id.desc(),
                )
                # Read wider than the cap only when something will reorder them;
                # otherwise the first `limit` by confidence IS the answer and
                # fetching more is work nobody reads.
                .limit(RANKING_POOL if self._ranks(query) else limit)
            )
            found = [
                MemoryItem.model_validate(
                    {
                        **dict(row._mapping),
                        "subject_refs": tuple(row.subject_refs),
                        "provenance_refs": tuple(row.provenance_refs),
                    }
                )
                for row in rows.all()
            ]
        return tuple(await self._ordered(found, query, context, worker_id))[:limit]

    def _ranks(self, query: str | None) -> bool:
        return self.ranker is not None and bool(query)

    async def _ordered(
        self,
        found: list[MemoryItem],
        query: str | None,
        context: AccessContext,
        worker_id: str,
    ) -> list[MemoryItem]:
        """Similarity order when a ranker can give one, the query's order otherwise.

        Outside the transaction: the rows are already in hand, and holding a
        database connection open across a call to another service is how a slow
        dependency becomes a connection-pool outage.

        Fails open to the existing order, deliberately. A ranker that is down
        leaves an agent reading its memories confidence-first, which is what
        every run did before this existed — not an agent with no memory, and
        certainly not a failed run.
        """
        if self.ranker is None or not query or not found:
            return found
        try:
            order = await self.ranker.nearest(
                query,
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                worker_id=worker_id,
                candidate_ids=[item.memory_id for item in found],
            )
        except Exception:
            logger.warning(
                "memory ranker failed; falling back to confidence order",
                extra={"worker_id": worker_id},
                exc_info=True,
            )
            return found
        return rank_by(order, [(item.memory_id, item) for item in found])
