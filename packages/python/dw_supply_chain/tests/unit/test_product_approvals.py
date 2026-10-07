"""Unit: `product_approvals` — who decides each product-case approval, and in
what order the step-9 sign-off runs (stage-1 tickets 02 and 04, QE-10)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dw_supply_chain.policy_files import PRODUCT_APPROVALS_POLICY_FILE
from dw_supply_chain.product_approvals import (
    PRODUCT_APPROVALS_POLICY_ID,
    SupplyChainProductApprovals,
    load_supply_chain_product_approvals,
)

pytestmark = pytest.mark.unit

_SHIPPED = (
    Path(__file__).resolve().parents[5] / "configs" / "policies" / PRODUCT_APPROVALS_POLICY_FILE
)


def _shipped() -> SupplyChainProductApprovals:
    return load_supply_chain_product_approvals(_SHIPPED)


def _document(**fields: object) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "policy_id": PRODUCT_APPROVALS_POLICY_ID,
        "policy_version": "1.1.0",
        "bod_review": {"required_scope": "supply_chain.approve.bod"},
        "signoff": [
            {"step": "bod", "label": "BGĐ", "required_scope": "supply_chain.approve.bod"},
        ],
        **fields,
    }


def test_the_shipped_signoff_is_bgd_then_accounting() -> None:
    """QE-10 (provisional): sequential, both required, BGĐ first."""
    policy = _shipped()
    assert policy.policy_version == "1.1.0"
    assert [(s.step, s.label, s.required_scope) for s in policy.signoff] == [
        ("bod", "BGĐ", "supply_chain.approve.bod"),
        ("accounting", "Kế toán", "supply_chain.approve.accounting"),
    ]
    assert policy.bod_review.required_scope == "supply_chain.approve.bod"


def test_an_override_stored_before_the_signoff_existed_takes_the_platforms() -> None:
    """A 1.0.0 override decided who reviews samples, never who signs: it
    keeps its own review scope and takes the platform's sign-off."""
    stored = _document(policy_version="1.0.0", bod_review={"required_scope": "elmich.bod"})
    del stored["signoff"]
    policy = SupplyChainProductApprovals.from_stored(stored, _shipped())
    assert policy.bod_review.required_scope == "elmich.bod"
    assert policy.signoff == _shipped().signoff


def test_an_override_at_1_1_0_must_name_its_signoff() -> None:
    stored = _document()
    del stored["signoff"]
    with pytest.raises(ValidationError, match="signoff"):
        SupplyChainProductApprovals.from_stored(stored, _shipped())


@pytest.mark.parametrize(
    "signoff",
    [
        [],
        [
            {"step": "bod", "label": "BGĐ", "required_scope": "a.b"},
            {"step": "bod", "label": "BGĐ lần hai", "required_scope": "a.c"},
        ],
        [{"step": "Bod", "label": "BGĐ", "required_scope": "a.b"}],
        [{"step": "bod", "label": " ", "required_scope": "a.b"}],
        [{"step": "bod", "label": "BGĐ", "required_scope": ""}],
    ],
)
def test_an_empty_repeated_or_malformed_signoff_is_refused(signoff: list[object]) -> None:
    """Fail closed: no step means nobody signs, and a step named twice would
    pause twice on one key the page cannot tell apart."""
    with pytest.raises(ValidationError):
        SupplyChainProductApprovals.model_validate(_document(signoff=signoff))
