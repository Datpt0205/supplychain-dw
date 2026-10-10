"""The memory handler: what it refuses, and whose memory a stored fact becomes.

The idempotency itself is proven against a real database in
`dw_memory/tests/integration` — a second delivery has to find the first row, and
only Postgres can say whether it does. What is provable here is everything
around that: which errors are worth retrying, and where tenancy comes from.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from dw_kernel.ids import TenantId, WorkspaceId
from dw_memory.contracts import MemoryItem, MemoryType, WriteDecision
from dw_memory.policy import MemoryCandidate, PolicyOutcome
from dw_memory.service import ProposalResult
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.outbox import OutboxEvent
from dw_worker.consumers.memory import (
    MEMORY_CANDIDATE_PROPOSED,
    MEMORY_REVIEW_DECIDED,
    build_memory_handler,
    build_review_handler,
    memory_handlers,
)
from dw_worker.consumers.outbox import UndeliverableEventError

pytestmark = pytest.mark.unit

TENANT = uuid.UUID(int=0xA1)
WORKSPACE = uuid.UUID(int=0xA2)
ACTOR = uuid.UUID(int=0xA3)
RUN = uuid.UUID(int=0xA4)


class _Recorder:
    """Captures the call rather than storing anything."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def propose(
        self,
        candidate: MemoryCandidate,
        context: AccessContext,
        *,
        created_by_run_id: uuid.UUID,
        idempotency_key: uuid.UUID | None = None,
    ) -> ProposalResult:
        self.calls.append(
            {
                "candidate": candidate,
                "tenant_id": context.tenant_id,
                "workspace_id": context.workspace_id,
                "principal_id": context.principal_id,
                "run_id": created_by_run_id,
                "idempotency_key": idempotency_key,
            }
        )
        return ProposalResult(
            candidate_id=uuid.uuid4(),
            outcome=PolicyOutcome(decision=WriteDecision.AUTO_WRITE, reason="ok", confidence=0.75),
            item=None,
        )

    async def settle_review(
        self, approval_id: uuid.UUID, context: AccessContext
    ) -> MemoryItem | None:
        self.calls.append(
            {
                "approval_id": approval_id,
                "tenant_id": context.tenant_id,
                "workspace_id": context.workspace_id,
                "principal_id": context.principal_id,
                "scopes": context.scopes,
            }
        )
        return None


def _payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": "1.1",
        "run_id": str(RUN),
        "actor_id": str(ACTOR),
        "candidate": {
            "worker_id": "demo",
            "memory_type": MemoryType.COMMITMENT.value,
            "content": "Anh An cam kết gửi hợp đồng.",
            "provenance_refs": [],
        },
    }
    body.update(overrides)
    return body


def _event(
    payload: dict[str, Any] | None = None, *, event_id: uuid.UUID | None = None
) -> OutboxEvent:
    return OutboxEvent(
        id=event_id or uuid.uuid4(),
        tenant_id=TenantId(TENANT),
        workspace_id=WorkspaceId(WORKSPACE),
        event_type=MEMORY_CANDIDATE_PROPOSED,
        schema_version="1.0",
        aggregate_id=RUN,
        occurred_at=datetime(2026, 9, 18, tzinfo=UTC),
        payload=payload if payload is not None else _payload(),
    )


async def test_the_event_id_is_what_makes_a_redelivery_decide_once() -> None:
    """At-least-once delivery plus a non-idempotent write is a duplicate memory
    per retry. The key has to be the event's own id — anything generated per call
    is a fresh key on the second attempt."""
    recorder = _Recorder()
    event = _event()

    await build_memory_handler(recorder)(event)

    assert recorder.calls[0]["idempotency_key"] == event.id


async def test_tenancy_comes_from_the_envelope_not_the_payload() -> None:
    """What produces this event is a model's output one layer up. A payload that
    could name its own tenant would let it choose whose memory a fact becomes."""
    recorder = _Recorder()

    await build_memory_handler(recorder)(_event())

    assert recorder.calls[0]["tenant_id"] == TENANT
    assert recorder.calls[0]["workspace_id"] == WORKSPACE


async def test_a_payload_that_names_a_tenant_is_refused_outright() -> None:
    """Not ignored — refused. A field this build does not know is more likely a
    newer schema than noise, and dropping it silently stores a memory missing
    whatever the sender meant to send."""
    handler = build_memory_handler(_Recorder())

    with pytest.raises(UndeliverableEventError):
        await handler(_event(_payload(tenant_id=str(uuid.uuid4()))))


async def test_a_malformed_payload_is_undeliverable_rather_than_retried() -> None:
    """It cannot become valid by being tried again. Raising the ordinary kind of
    error here would burn the attempt budget in a loop, and the reason would
    never be written down."""
    handler = build_memory_handler(_Recorder())

    with pytest.raises(UndeliverableEventError):
        await handler(_event({"schema_version": "1.1"}))


async def test_a_payload_carrying_its_own_confidence_is_refused_not_obeyed() -> None:
    """The model-injected shape: a producer copying a confidence out of model
    output. The number that decides AUTO_WRITE is the policy's, so this is not
    an input at all — refused at parse, and nothing reaches the service."""
    recorder = _Recorder()
    body = _payload()
    body["candidate"] = {**body["candidate"], "confidence": 1.0}

    with pytest.raises(UndeliverableEventError, match="confidence"):
        await build_memory_handler(recorder)(_event(body))
    assert recorder.calls == []


async def test_a_schema_1_0_payload_is_refused_rather_than_guessed_at() -> None:
    """1.0 carried a raw confidence. Reading one as 1.1 by dropping the field
    would be a guess about what the sender meant; fail closed."""
    recorder = _Recorder()

    with pytest.raises(UndeliverableEventError, match="schema_version"):
        await build_memory_handler(recorder)(_event(_payload(schema_version="1.0")))
    assert recorder.calls == []


async def test_evidence_the_service_refuses_is_undeliverable_not_retried() -> None:
    """A citation that failed verification does not become true on the next
    attempt. Reported as undeliverable, with the service's reason."""
    from dw_kernel.errors import DomainError

    class _Refuses(_Recorder):
        async def propose(self, *args: Any, **kwargs: Any) -> ProposalResult:
            raise DomainError("evidence cites a chunk from another workspace")

    with pytest.raises(UndeliverableEventError, match="another workspace"):
        await build_memory_handler(_Refuses())(_event())


async def test_a_storage_failure_stays_retryable() -> None:
    """The other half of the same decision: a database that is down IS worth
    trying again, so it must NOT be reported as undeliverable — that would throw
    the memory away."""

    class _Broken(_Recorder):
        async def propose(self, *args: Any, **kwargs: Any) -> ProposalResult:
            raise TimeoutError("database gone")

    handler = build_memory_handler(_Broken())

    with pytest.raises(TimeoutError):
        await handler(_event())


def test_the_worker_wires_this_event_and_only_this_one() -> None:
    """Pins what the composition root turned on. An effect that reacts to an
    ambient record event — "an account was created" — is the shape that bought a
    paid model call per row of a bulk import; this one reacts to an event a run
    emits deliberately when it has something to remember, and to a person's
    decision on a memory that was held for them."""
    assert sorted(memory_handlers(_Recorder())) == [
        MEMORY_CANDIDATE_PROPOSED,
        MEMORY_REVIEW_DECIDED,
    ]


# ------------------------------------------------------------- ranking ----


class _Index:
    def __init__(self, raises: Exception | None = None) -> None:
        self.raises = raises
        self.indexed: list[dict[str, Any]] = []

    async def index(self, **kwargs: Any) -> None:
        self.indexed.append(kwargs)
        if self.raises is not None:
            raise self.raises


class _Stores(_Recorder):
    """Returns a stored item, so the indexing branch is reachable."""

    async def propose(self, candidate: Any, context: Any, **kwargs: Any) -> ProposalResult:
        await super().propose(candidate, context, **kwargs)
        return ProposalResult(
            candidate_id=uuid.uuid4(),
            outcome=PolicyOutcome(decision=WriteDecision.AUTO_WRITE, reason="ok", confidence=0.75),
            item=MemoryItem(
                memory_id=uuid.uuid4(),
                tenant_id=TENANT,
                workspace_id=WORKSPACE,
                worker_id="demo",
                memory_type=MemoryType.COMMITMENT,
                content="Anh An cam kết gửi hợp đồng.",
                confidence=0.9,
                valid_from=datetime(2026, 9, 18, tzinfo=UTC),
                created_by_run_id=RUN,
            ),
        )


async def test_a_stored_memory_is_handed_to_the_index() -> None:
    index = _Index()

    await build_memory_handler(_Stores(), index)(_event())

    assert index.indexed[0]["tenant_id"] == TENANT
    assert index.indexed[0]["worker_id"] == "demo"


async def test_nothing_is_indexed_when_nothing_was_stored() -> None:
    """A REVIEW or a REJECT has no item; indexing one would put a fact in the
    ranker that the policy declined to keep."""
    index = _Index()

    await build_memory_handler(_Recorder(), index)(_event())

    assert index.indexed == []


class _AlreadySuperseded(_Recorder):
    """A redelivery whose memory a later proposal has since closed.

    `propose` answers a seen event id with the stored row, `valid_until`
    included, and supersession has already deleted that row's point.
    """

    async def propose(self, candidate: Any, context: Any, **kwargs: Any) -> ProposalResult:
        await super().propose(candidate, context, **kwargs)
        return ProposalResult(
            candidate_id=uuid.uuid4(),
            outcome=PolicyOutcome(
                decision=WriteDecision.AUTO_WRITE, reason="already decided", confidence=0.75
            ),
            item=MemoryItem(
                memory_id=uuid.uuid4(),
                tenant_id=TENANT,
                workspace_id=WORKSPACE,
                worker_id="demo",
                memory_type=MemoryType.COMMITMENT,
                content="Anh An cam kết gửi hợp đồng.",
                confidence=0.9,
                valid_from=datetime(2026, 9, 18, tzinfo=UTC),
                valid_until=datetime(2026, 9, 19, tzinfo=UTC),
                created_by_run_id=RUN,
            ),
        )


async def test_a_redelivered_memory_that_was_since_superseded_is_not_indexed_again() -> None:
    """Re-indexing it would put back the point supersession deleted, an
    embedding of an answer the system no longer gives, kept until offboarding."""
    index = _Index()

    await build_memory_handler(_AlreadySuperseded(), index)(_event())

    assert index.indexed == []


async def test_the_index_is_optional() -> None:
    """A deployment with no vector store still remembers."""
    result = await build_memory_handler(_Stores())(_event())

    assert "auto_write" in result


# ------------------------------------------------------- reviewed memory --

APPROVAL = uuid.UUID(int=0xB1)
DECIDER = uuid.UUID(int=0xB2)


def _decided(payload: dict[str, Any] | None = None) -> OutboxEvent:
    return OutboxEvent(
        id=uuid.uuid4(),
        tenant_id=TenantId(TENANT),
        workspace_id=WorkspaceId(WORKSPACE),
        event_type=MEMORY_REVIEW_DECIDED,
        schema_version="1.0",
        aggregate_id=APPROVAL,
        occurred_at=datetime(2026, 10, 6, tzinfo=UTC),
        payload=payload
        if payload is not None
        else {
            "approval_id": str(APPROVAL),
            "decision_id": str(uuid.uuid4()),
            "outcome": "approved",
            "decided_by": str(DECIDER),
        },
    )


async def test_a_review_is_settled_in_the_envelopes_tenancy_by_the_decider() -> None:
    """The decider is who the trail names; the tenancy is the envelope's, which
    the approval flow copied from the approval row. The context carries no
    scope: the decision was authorized where it was made."""
    recorder = _Recorder()

    result = await build_review_handler(recorder)(_decided())

    assert result == "memory review settled without a write"
    [call] = recorder.calls
    assert call == {
        "approval_id": APPROVAL,
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "principal_id": DECIDER,
        "scopes": frozenset(),
    }


async def test_a_review_payload_naming_a_tenant_is_refused() -> None:
    body = dict(_decided().payload, tenant_id=str(uuid.uuid4()))
    recorder = _Recorder()

    with pytest.raises(UndeliverableEventError):
        await build_review_handler(recorder)(_decided(body))
    assert recorder.calls == []


async def test_a_review_whose_evidence_no_longer_verifies_is_undeliverable() -> None:
    from dw_kernel.errors import DomainError

    class _Refuses(_Recorder):
        async def settle_review(self, *args: Any, **kwargs: Any) -> MemoryItem | None:
            raise DomainError("evidence chunk no longer exists")

    with pytest.raises(UndeliverableEventError, match="no longer exists"):
        await build_review_handler(_Refuses())(_decided())


async def test_a_review_storage_failure_stays_retryable() -> None:
    class _Broken(_Recorder):
        async def settle_review(self, *args: Any, **kwargs: Any) -> MemoryItem | None:
            raise TimeoutError("database gone")

    with pytest.raises(TimeoutError):
        await build_review_handler(_Broken())(_decided())


async def test_an_approved_memory_is_indexed_and_a_rejected_one_is_not() -> None:
    item = MemoryItem(
        memory_id=uuid.uuid4(),
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        worker_id="demo",
        memory_type=MemoryType.COMMITMENT,
        content="Anh An cam kết gửi hợp đồng.",
        confidence=0.6,
        valid_from=datetime(2026, 10, 6, tzinfo=UTC),
        created_by_run_id=RUN,
    )

    class _Writes(_Recorder):
        async def settle_review(self, *args: Any, **kwargs: Any) -> MemoryItem | None:
            return item

    written, rejected = _Index(), _Index()
    assert await build_review_handler(_Writes(), written)(_decided()) == "memory review written"
    await build_review_handler(_Recorder(), rejected)(_decided())

    assert [entry["memory_id"] for entry in written.indexed] == [item.memory_id]
    assert rejected.indexed == []
