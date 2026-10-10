"""Memory write policy: the model never writes directly.

Every candidate is classified; only well-evidenced, corroborated and
appropriately classified facts are auto-written. Everything else waits for
review or is rejected. Fail closed.

**Confidence is computed here, never received.** The number that decides
whether a fact is stored without a person comes from signals code has checked,
not from the producer — and a producer is a model's output one layer up. It
used to be a field on the candidate, compared with 0.80, so whatever wrote the
candidate decided what was remembered. `MemoryCandidate` has no such field now,
and `extra="forbid"` turns a payload that brings one into a refusal.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dw_knowledge.contracts import EvidenceRef, classification_rank
from dw_memory.contracts import MemoryType, WriteDecision


class MemoryCandidate(BaseModel):
    """A proposed long-term memory fact produced by a workflow.

    Carries no confidence: see the module docstring. `classification` is the
    producer's claim, held to the cited documents' own labels when the evidence
    is verified — a claim below them is refused.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    worker_id: str
    memory_type: MemoryType
    content: str = Field(min_length=1)
    structured_facts: dict[str, object] = {}
    subject_refs: tuple[str, ...] = ()
    provenance_refs: tuple[EvidenceRef, ...] = ()
    classification: str = "internal"
    # Proposed by the workflow, never by raw model output: see MemoryService.
    fact_key: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("classification")
    @classmethod
    def _on_the_clearance_ladder(cls, value: str) -> str:
        # Raises for anything off the ladder recall filters by. The ladder is
        # the owner of the set; this reads it rather than listing it again.
        classification_rank(value)
        return value


@dataclass(frozen=True)
class PolicyOutcome:
    decision: WriteDecision
    reason: str
    # Computed by the policy; what is stored and what recall orders by.
    confidence: float


@dataclass(frozen=True)
class MemoryWritePolicy:
    """Versioned policy object; thresholds are configuration, not model output.

    The signal is corroboration: how many distinct documents a candidate cites.
    Distinct documents, not references — repeating one citation under fresh
    evidence ids finds no second source. Every reference of an auto-write is
    verified against the chunk it names before the item is stored
    (`EvidenceStorePort.record`), and one bad reference refuses the whole write,
    so whenever an item commits, the count it was decided on is a count of
    verified documents. A REVIEW is not verified here: a person decides it, and
    the approval that stores it goes through the same check.
    """

    policy_version: str = "2.0.0"
    auto_write_sources: int = 2

    def evaluate(self, candidate: MemoryCandidate) -> PolicyOutcome:
        sources = len({ref.source_document_id for ref in candidate.provenance_refs})
        # Each independent document halves the remaining doubt: 1 → 0.5,
        # 2 → 0.75, 3 → 0.875. Only an ordering for recall; the decision below
        # reads the count itself.
        confidence = 1.0 - 0.5**sources
        if sources == 0:
            return PolicyOutcome(
                WriteDecision.REJECT, "memory without provenance is never stored", confidence
            )
        if candidate.classification == "restricted":
            return PolicyOutcome(
                WriteDecision.REVIEW, "restricted content always requires human review", confidence
            )
        if sources >= self.auto_write_sources:
            return PolicyOutcome(
                WriteDecision.AUTO_WRITE, "corroborated, evidenced, unrestricted", confidence
            )
        return PolicyOutcome(
            WriteDecision.REVIEW, "too few independent sources to write unreviewed", confidence
        )
