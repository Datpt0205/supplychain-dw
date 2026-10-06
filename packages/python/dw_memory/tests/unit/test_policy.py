import hashlib
import uuid

import pytest
from pydantic import ValidationError

from dw_knowledge.contracts import EvidenceRef
from dw_memory.contracts import MemoryType, WriteDecision
from dw_memory.policy import MemoryCandidate, MemoryWritePolicy

pytestmark = pytest.mark.unit


def make_evidence(document_id: uuid.UUID | None = None) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=uuid.uuid4(),
        source_document_id=document_id or uuid.uuid4(),
        source_version="1",
        relevance_score=0.9,
        classification="internal",
        provenance_hash=hashlib.sha256(b"evidence").hexdigest(),
    )


def make_candidate(**overrides: object) -> MemoryCandidate:
    defaults: dict[str, object] = {
        "worker_id": "demo",
        "memory_type": MemoryType.COMMITMENT,
        "content": "Phòng Kỹ thuật cam kết bàn giao API trước 15/08.",
        # Two documents: the corroboration auto-write asks for.
        "provenance_refs": (make_evidence(), make_evidence()),
        "classification": "internal",
    }
    defaults.update(overrides)
    return MemoryCandidate.model_validate(defaults)


def test_no_provenance_is_rejected() -> None:
    policy = MemoryWritePolicy()
    outcome = policy.evaluate(make_candidate(provenance_refs=()))
    assert outcome.decision is WriteDecision.REJECT
    assert "provenance" in outcome.reason


def test_restricted_always_reviewed() -> None:
    outcome = MemoryWritePolicy().evaluate(make_candidate(classification="restricted"))
    assert outcome.decision is WriteDecision.REVIEW


def test_two_documents_agreeing_auto_write() -> None:
    outcome = MemoryWritePolicy().evaluate(make_candidate())
    assert outcome.decision is WriteDecision.AUTO_WRITE


def test_one_document_goes_to_review() -> None:
    outcome = MemoryWritePolicy().evaluate(make_candidate(provenance_refs=(make_evidence(),)))
    assert outcome.decision is WriteDecision.REVIEW


def test_citing_one_document_twice_is_still_one_source() -> None:
    """The count is of documents, not of references: a producer that repeats
    one citation under fresh evidence ids has not found a second source."""
    document = uuid.uuid4()
    outcome = MemoryWritePolicy().evaluate(
        make_candidate(provenance_refs=tuple(make_evidence(document) for _ in range(5)))
    )
    assert outcome.decision is WriteDecision.REVIEW


def test_confidence_is_computed_from_sources_and_grows_with_them() -> None:
    """What gets stored, and what recall orders by, is the policy's number."""
    policy = MemoryWritePolicy()
    one, two, three = (
        policy.evaluate(
            make_candidate(provenance_refs=tuple(make_evidence() for _ in range(n)))
        ).confidence
        for n in (1, 2, 3)
    )
    assert 0.0 < one < two < three <= 1.0


def test_a_candidate_cannot_carry_its_own_confidence() -> None:
    """The number that decides AUTO_WRITE is the policy's. A field a producer
    could set — and a producer is a model's output one layer up — would let the
    model decide what is remembered without a person."""
    with pytest.raises(ValidationError, match="confidence"):
        make_candidate(confidence=1.0)


def test_a_classification_off_the_ladder_is_refused_at_parse() -> None:
    """`public` is not below `internal`; it is a value recall's clearance filter
    has never heard of, and storing it would make the fact unrecallable or,
    worse, ranked by a guess."""
    with pytest.raises(ValidationError, match="classification"):
        make_candidate(classification="public")
