import pytest

from dw_supply_chain.domain.evidence import is_verbatim
from dw_supply_chain.domain.supplier_update import (
    CONFIDENCE_THRESHOLD,
    SupplierEventType,
    SupplierUpdateExtraction,
    requires_confirmation,
)

pytestmark = pytest.mark.unit


def _extraction(**overrides: object) -> SupplierUpdateExtraction:
    defaults: dict[str, object] = {
        "event_type": SupplierEventType.PRODUCTION_DELAY,
        "reason": "supplier cites a component shortage",
        "proposed_action": "split shipment",
        "confidence": 0.95,
        "source_ref": "we will be delayed by 7 days",
    }
    defaults.update(overrides)
    return SupplierUpdateExtraction(**defaults)


RAW_TEXT = "Hi team, we will be delayed by 7 days due to a component shortage."


def test_a_high_confidence_verbatim_source_needs_no_confirmation() -> None:
    extraction = _extraction()
    assert not requires_confirmation(extraction, RAW_TEXT)


def test_low_confidence_requires_confirmation_even_with_a_verbatim_source() -> None:
    extraction = _extraction(confidence=CONFIDENCE_THRESHOLD - 0.01)
    assert requires_confirmation(extraction, RAW_TEXT)


def test_a_source_ref_not_found_verbatim_requires_confirmation_regardless_of_confidence() -> None:
    extraction = _extraction(confidence=1.0, source_ref="a quote that was never actually said")
    assert requires_confirmation(extraction, RAW_TEXT)


def test_source_ref_matching_is_whitespace_normalised_not_exact() -> None:
    """The model may reflow whitespace even when quoting faithfully — this
    should still count as verbatim, not fail the evidence check on a detail
    that carries no meaning."""
    extraction = _extraction(source_ref="we will be   delayed\nby 7 days")
    assert is_verbatim(extraction.source_ref, RAW_TEXT)
    assert not requires_confirmation(extraction, RAW_TEXT)


def test_a_paraphrase_is_not_verbatim() -> None:
    assert not is_verbatim("delivery will slip a week", RAW_TEXT)


def test_confidence_exactly_at_the_threshold_does_not_require_confirmation() -> None:
    """The doc says "confidence thấp" (low) requires confirmation — the
    boundary itself is not low, so `>=` rather than `>` is the reading that
    does not silently widen the confirmation gate past what was asked."""
    extraction = _extraction(confidence=CONFIDENCE_THRESHOLD)
    assert not requires_confirmation(extraction, RAW_TEXT)
