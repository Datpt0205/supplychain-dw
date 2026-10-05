"""Unit: `product_action_duties` — every step a person takes on a
product-development case belongs to exactly one duty, and the policy is its
own document, apart from the PO case's."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dw_supply_chain.action_duties import CaseDuty, load_supply_chain_action_duties
from dw_supply_chain.domain.po_case import CaseAction
from dw_supply_chain.domain.product_development_case import ProductAction
from dw_supply_chain.product_action_duties import (
    PRODUCT_ACTION_DUTIES_POLICY_ID,
    SupplyChainProductActionDuties,
    load_supply_chain_product_action_duties,
)

pytestmark = pytest.mark.unit

_POLICIES = Path(__file__).resolve().parents[5] / "configs" / "policies"
_SHIPPED = _POLICIES / "supply_chain_product_action_duties@1.0.0.yaml"
_PO_SHIPPED = _POLICIES / "supply_chain_action_duties@1.0.0.yaml"


def _document(mapping: dict[str, str]) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "policy_id": PRODUCT_ACTION_DUTIES_POLICY_ID,
        "policy_version": "1.0.0",
        "action_duties": mapping,
    }


def _shipped_mapping() -> dict[str, str]:
    policy = load_supply_chain_product_action_duties(_SHIPPED)
    return {action.value: duty.value for action, duty in policy.action_duties.items()}


def test_the_shipped_default_gives_every_step_a_duty() -> None:
    policy = load_supply_chain_product_action_duties(_SHIPPED)
    assert set(policy.action_duties) == set(ProductAction)
    assert policy.policy_id == PRODUCT_ACTION_DUTIES_POLICY_ID


@pytest.mark.parametrize(
    ("action", "duty"),
    [
        (ProductAction.PROPOSE, CaseDuty.ORDERING),
        (ProductAction.REQUEST_SAMPLE, CaseDuty.ORDERING),
        (ProductAction.RECEIVE_SAMPLE, CaseDuty.RND),
        (ProductAction.PASS_SAMPLE, CaseDuty.RND),
        (ProductAction.REQUEST_REVISION, CaseDuty.RND),
        (ProductAction.RECEIVE_REVISED_SAMPLE, CaseDuty.RND),
        (ProductAction.REJECT_SAMPLE, CaseDuty.RND),
    ],
)
def test_supply_proposes_and_rnd_tests(action: ProductAction, duty: CaseDuty) -> None:
    assert load_supply_chain_product_action_duties(_SHIPPED).duty_for(action) is duty


@pytest.mark.parametrize(
    "shared",
    [
        ProductAction.WAIT_FOR_EXTERNAL,
        ProductAction.FLAG_BLOCKED,
        ProductAction.FLAG_MANUAL_REVIEW,
        ProductAction.RESUME,
        ProductAction.CANCEL,
    ],
)
def test_the_exception_steps_mirror_the_po_case(shared: ProductAction) -> None:
    po = load_supply_chain_action_duties(_PO_SHIPPED)
    product = load_supply_chain_product_action_duties(_SHIPPED)
    assert product.duty_for(shared) is po.duty_for(CaseAction(shared.value))


def test_a_mapping_that_leaves_a_step_out_is_refused() -> None:
    mapping = _shipped_mapping()
    del mapping["pass_sample"]
    with pytest.raises(ValidationError, match="pass_sample"):
        SupplyChainProductActionDuties.model_validate(_document(mapping))


@pytest.mark.parametrize(
    "mapping_change",
    [
        {"pass_sample": "anyone"},  # a duty that does not exist
        {"request_deposit": "ordering"},  # a PO step, not a product step
    ],
)
def test_an_unknown_duty_or_step_is_refused(mapping_change: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        SupplyChainProductActionDuties.model_validate(
            _document(_shipped_mapping() | mapping_change)
        )


def test_an_unknown_field_is_refused() -> None:
    with pytest.raises(ValidationError):
        SupplyChainProductActionDuties.model_validate(
            _document(_shipped_mapping()) | {"default_duty": "rnd"}
        )


def test_the_po_policy_still_loads_and_needs_no_rnd_step() -> None:
    """Adding a duty does not touch the PO policy: it requires every PO step a
    duty, not every duty a step, so a tenant's PO override written before
    `rnd` existed stays valid."""
    po = load_supply_chain_action_duties(_PO_SHIPPED)
    assert CaseDuty.RND not in set(po.action_duties.values())
