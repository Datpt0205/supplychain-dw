"""Remembering happens after the answer, not during it.

A run that stops to decide what is worth keeping is a run the person is waiting
on. So a workflow announces a candidate on the outbox when its turn is over and
this handler stores it on the worker — the foreground path pays nothing, and a
memory that fails to store fails where a retry is cheap instead of in front of a
customer.

**At-least-once is the whole design problem.** The outbox counts an attempt at
claim time and marks the row afterwards, so a process that dies between those
two points delivers the same event again. Without a key, that is a second
identical memory; with two retries, a third. The event's own id is passed as the
idempotency key, which makes the candidate row's primary key deterministic — the
second delivery finds the first decision and returns it.

**A malformed payload is not retried.** It cannot become valid by being tried
again, so it raises `UndeliverableEventError` and the reason is recorded rather
than burning the attempt budget in a loop. That distinction is the one thing a
handler must get right here: a database that is down IS worth retrying, and
raising the wrong kind of error for it would throw the memory away. The same
holds for evidence the service refuses (`DomainError`): a citation that failed
verification, or a fact claiming a lower classification than its sources, does
not become true on the next attempt.

**A reviewed candidate is settled here too.** A person decides a
`memory.review` approval in the platform inbox; the approval flow announces the
decision as `memory.review.decided`, and that handler asks the service to write
the item or record the refusal. Tenancy again comes from the envelope, and what
was decided from the approval row, which the service reads itself.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from dw_kernel.errors import DomainError, NotFoundError
from dw_memory.contracts import MemoryItem
from dw_memory.policy import MemoryCandidate
from dw_memory.review import MEMORY_REVIEW_DECIDED
from dw_memory.service import ProposalResult
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.outbox import OutboxEvent
from dw_worker.consumers.outbox import EventHandler, UndeliverableEventError

__all__ = [
    "MEMORY_CANDIDATE_PROPOSED",
    "MEMORY_REVIEW_DECIDED",
    "MemoryCandidatePayload",
    "MemoryIndexPort",
    "ReviewDecidedPayload",
    "build_memory_handler",
    "build_review_handler",
    "memory_handlers",
]

MEMORY_CANDIDATE_PROPOSED = "memory.candidate_proposed"
"""What a workflow announces when a turn produced something worth keeping."""

# `AccessContext` requires a plan and this path reads none, so rather than
# borrow a real one — which a later entitlement check would honour — the
# context carries a plan the catalog does not contain. `has_feature` answers
# False for it and `runs_per_day` answers 0, so anything that starts reading
# the plan here refuses rather than grants.
_NO_PLAN = "background"


class MemoryIndexPort(Protocol):
    """What makes a stored memory rankable later.

    Optional on purpose: a deployment with no vector store still remembers, it
    just cannot order a long list by what a question is about. See
    `dw_memory.adapters.qdrant_ranker` for why this never raises.
    """

    async def index(
        self,
        *,
        memory_id: uuid.UUID,
        content: str,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID,
        worker_id: str,
    ) -> None: ...


class MemoryProposePort(Protocol):
    """`MemoryService.propose`, stated by its consumer.

    The candidate carries no confidence. Whether a fact is written without a
    person is decided from what the implementation computes out of verified
    signals (`MemoryWritePolicy`), never from a number model output supplied.
    Raises `DomainError` for evidence it refuses.
    """

    async def propose(
        self,
        candidate: MemoryCandidate,
        context: AccessContext,
        *,
        created_by_run_id: uuid.UUID,
        idempotency_key: uuid.UUID | None = None,
    ) -> ProposalResult: ...


class MemoryReviewPort(Protocol):
    """`MemoryService.settle_review`, stated by its consumer.

    Returns the item when the candidate is written (now or by an earlier
    delivery), None when it is not. Raises `DomainError` when its evidence no
    longer verifies, after recording the refusal.
    """

    async def settle_review(
        self, approval_id: uuid.UUID, context: AccessContext
    ) -> MemoryItem | None: ...


class MemoryWritePort(MemoryProposePort, MemoryReviewPort, Protocol):
    """Both of memory's write paths, as one service satisfies them."""


class ReviewDecidedPayload(BaseModel):
    """The body `ApproveAndResumeService` writes for a run-less decision.

    Only `approval_id` is acted on, and `decided_by` names who is accountable.
    The outcome is read again from the approval row by the service: the row is
    what the decision wrote, the event only says that it happened.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_id: uuid.UUID
    decision_id: uuid.UUID
    outcome: str
    decided_by: uuid.UUID


class MemoryCandidatePayload(BaseModel):
    """The event body, validated before anything reaches the database.

    `extra="forbid"`: an event carrying a field this build does not know is more
    likely a newer schema than a harmless extra, and silently dropping it would
    store a memory that is missing whatever the sender thought it was sending.

    Deliberately NOT carrying tenant or workspace: those come off the event's own
    envelope, which the emitting transaction wrote. A payload that could name its
    own tenant would let whatever produced the event choose whose memory it
    becomes — and what produces it is a model's output, one layer up.

    `1.1` dropped the candidate's `confidence`, for the same reason: the number
    that decides AUTO_WRITE is computed by the policy from verified evidence,
    never carried in. A `1.0` event is refused, not read as `1.1` by dropping
    the field — that would be a guess about what the sender meant.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^1\.1$")
    run_id: uuid.UUID
    actor_id: uuid.UUID
    candidate: MemoryCandidate


def _access_for(event: OutboxEvent, payload: MemoryCandidatePayload) -> AccessContext:
    """Tenancy from the envelope, identity from the payload's actor.

    The run's own scopes are not replayed: this is a background write of a fact
    the run already produced, not a chance to act as that user again. Memory has
    no scope gate of its own — the policy and the evidence check are what decide
    whether it is written.
    """
    return AccessContext(
        # `.value`: the envelope carries the typed ids, `AccessContext` the plain
        # ones. Passing the wrapper raised at validation rather than silently
        # becoming something else, which is the behaviour to want here.
        tenant_id=event.tenant_id.value,
        workspace_id=event.workspace_id.value,
        principal_id=payload.actor_id,
        roles=frozenset(),
        scopes=frozenset(),
        plan_id=_NO_PLAN,
    )


def build_memory_handler(
    service: MemoryProposePort, index: MemoryIndexPort | None = None
) -> EventHandler:
    """The handler to wire under `MEMORY_CANDIDATE_PROPOSED`.

    Indexing happens AFTER the memory is committed and cannot fail the delivery:
    a vector store that is down must not send a fact that is already stored back
    round the retry loop, which would then store it again under a new event id.
    """

    async def handle(event: OutboxEvent) -> str:
        try:
            payload = MemoryCandidatePayload.model_validate(event.payload)
        except ValidationError as exc:
            raise UndeliverableEventError(f"payload does not parse: {exc}") from exc
        try:
            result = await service.propose(
                payload.candidate,
                _access_for(event, payload),
                created_by_run_id=payload.run_id,
                # The event id, so a redelivery decides once. See the module docstring.
                idempotency_key=event.id,
            )
        except DomainError as exc:
            raise UndeliverableEventError(f"refused: {exc.message} {exc.details}") from exc
        # A redelivery answers with the stored row, and a later proposal may
        # have closed it and deleted its point since. Indexing it again would
        # put that point back for nobody: recall never reads a closed memory.
        if index is not None and result.item is not None and result.item.valid_until is None:
            await index.index(
                memory_id=result.item.memory_id,
                content=result.item.content,
                tenant_id=result.item.tenant_id,
                workspace_id=result.item.workspace_id,
                worker_id=result.item.worker_id,
            )
        return f"memory candidate {result.outcome.decision.value}"

    return handle


def _decider_access(event: OutboxEvent, payload: ReviewDecidedPayload) -> AccessContext:
    """Tenancy from the envelope, and the decider as the one acting. No scopes:
    the decision was authorized where it was made, and this only carries it out."""
    return AccessContext(
        tenant_id=event.tenant_id.value,
        workspace_id=event.workspace_id.value,
        principal_id=payload.decided_by,
        roles=frozenset(),
        scopes=frozenset(),
        plan_id=_NO_PLAN,
    )


def build_review_handler(
    service: MemoryReviewPort, index: MemoryIndexPort | None = None
) -> EventHandler:
    """The handler to wire under `MEMORY_REVIEW_DECIDED`.

    Indexed after the commit, for the same reason as `build_memory_handler`.
    """

    async def handle(event: OutboxEvent) -> str:
        try:
            payload = ReviewDecidedPayload.model_validate(event.payload)
        except ValidationError as exc:
            raise UndeliverableEventError(f"payload does not parse: {exc}") from exc
        try:
            item = await service.settle_review(payload.approval_id, _decider_access(event, payload))
        except (DomainError, NotFoundError) as exc:
            # Evidence that no longer verifies, or an approval this tenant and
            # workspace cannot see: neither changes on a retry.
            raise UndeliverableEventError(f"refused: {exc.message} {exc.details}") from exc
        if item is None:
            return "memory review settled without a write"
        if index is not None and item.valid_until is None:
            await index.index(
                memory_id=item.memory_id,
                content=item.content,
                tenant_id=item.tenant_id,
                workspace_id=item.workspace_id,
                worker_id=item.worker_id,
            )
        return "memory review written"

    return handle


def memory_handlers(
    service: MemoryWritePort, index: MemoryIndexPort | None = None
) -> dict[str, EventHandler]:
    """Ready to merge into the outbox consumer's handler map."""
    return {
        MEMORY_CANDIDATE_PROPOSED: build_memory_handler(service, index),
        MEMORY_REVIEW_DECIDED: build_review_handler(service, index),
    }
