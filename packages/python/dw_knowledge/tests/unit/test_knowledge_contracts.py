import hashlib
import uuid

import pytest
from pydantic import ValidationError

from dw_knowledge.contracts import EvidenceRef, SearchQuery

pytestmark = pytest.mark.unit


def make_evidence(**overrides: object) -> EvidenceRef:
    defaults: dict[str, object] = {
        "evidence_id": uuid.uuid4(),
        "source_document_id": uuid.uuid4(),
        "source_version": "1",
        "relevance_score": 0.9,
        "classification": "internal",
        "provenance_hash": hashlib.sha256(b"chunk").hexdigest(),
    }
    defaults.update(overrides)
    return EvidenceRef(**defaults)


def test_evidence_requires_sha256_provenance() -> None:
    assert len(make_evidence().provenance_hash) == 64
    with pytest.raises(ValidationError):
        make_evidence(provenance_hash="abc")


def test_evidence_relevance_bounded() -> None:
    with pytest.raises(ValidationError):
        make_evidence(relevance_score=1.5)


def test_search_query_cannot_carry_tenant_filters() -> None:
    """Security invariant: callers (including model output) cannot smuggle
    tenant/workspace/ACL constraints into a retrieval request."""
    with pytest.raises(ValidationError):
        SearchQuery(text="chính sách mua hàng", tenant_id=str(uuid.uuid4()))  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        SearchQuery(text="x", acl_principals=["user:1"])  # type: ignore[call-arg]


def test_search_query_bounds() -> None:
    query = SearchQuery(text="quy trình phê duyệt", top_k=5)
    assert query.top_k == 5
    with pytest.raises(ValidationError):
        SearchQuery(text="", top_k=5)
    with pytest.raises(ValidationError):
        SearchQuery(text="x", top_k=500)


def test_classifications_run_from_least_to_most_restrictive() -> None:
    """Read off the clearance ladder rather than typed out a second time: a
    clearance reads its own classification and everything below it, so the
    order IS the ladder."""
    from dw_knowledge.contracts import CLASSIFICATIONS, classification_rank

    assert CLASSIFICATIONS == ("internal", "confidential", "restricted")
    assert [classification_rank(c) for c in CLASSIFICATIONS] == [0, 1, 2]


def test_an_unknown_classification_has_no_rank() -> None:
    """Ranking a value this build does not know would be a guess about how
    restrictive it is, and the guess decides who may recall it."""
    from dw_knowledge.contracts import classification_rank

    with pytest.raises(ValueError, match="public"):
        classification_rank("public")
