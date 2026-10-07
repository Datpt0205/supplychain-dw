"""Unit: `product_action_duties` — every step a person takes on a
product-development case belongs to exactly one duty, and the policy is its
own document, apart from the PO case's."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dw_supply_chain.action_duties import CaseDuty, load_supply_chain_action_duties
from dw_supply_chain.domain.po_case import CaseAction
from dw_supply_chain.domain.product_development_case import GRAPH_ONLY_ACTIONS, ProductAction
from dw_supply_chain.policy_files import PRODUCT_ACTION_DUTIES_POLICY_FILE
from dw_supply_chain.product_action_duties import (
    PRODUCT_ACTION_DUTIES_POLICY_ID,
    STEPS_ADDED_AFTER,
    SupplyChainProductActionDuties,
    load_supply_chain_product_action_duties,
)

pytestmark = pytest.mark.unit

_POLICIES = Path(__file__).resolve().parents[5] / "configs" / "policies"
_SHIPPED = _POLICIES / PRODUCT_ACTION_DUTIES_POLICY_FILE
_PO_SHIPPED = _POLICIES / "supply_chain_action_duties@1.0.0.yaml"


def _document(mapping: dict[str, str], *, version: str = "1.2.0") -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "policy_id": PRODUCT_ACTION_DUTIES_POLICY_ID,
        "policy_version": version,
        "action_duties": mapping,
    }


def _shipped_mapping() -> dict[str, str]:
    policy = load_supply_chain_product_action_duties(_SHIPPED)
    return {action.value: duty.value for action, duty in policy.action_duties.items()}


# The document a tenant could have stored before step 6 existed (S1): every
# step a person took then, and nothing else.
_S1_OVERRIDE = {
    "propose": "ordering",
    "request_sample": "ordering",
    "receive_sample": "rnd",
    "pass_sample": "rnd",
    "request_revision": "rnd",
    "receive_revised_sample": "rnd",
    "reject_sample": "rnd",
    "wait_for_external": "exceptions",
    "flag_blocked": "exceptions",
    "flag_manual_review": "exceptions",
    "resume": "exceptions",
    "cancel": "ordering",
}


def test_the_shipped_default_gives_every_step_a_person_takes_a_duty() -> None:
    policy = load_supply_chain_product_action_duties(_SHIPPED)
    assert set(policy.action_duties) == set(ProductAction) - GRAPH_ONLY_ACTIONS
    assert policy.policy_id == PRODUCT_ACTION_DUTIES_POLICY_ID


def test_an_override_stored_before_step_6_existed_stays_valid() -> None:
    """Step 6 added two actions and no duty for them: a tenant's override
    written in S1 names neither, and must still load."""
    policy = SupplyChainProductActionDuties.from_stored(
        _document(_S1_OVERRIDE, version="1.0.0"), _shipped()
    )
    assert policy.duty_for(ProductAction.PASS_SAMPLE) is CaseDuty.RND


def _shipped() -> SupplyChainProductActionDuties:
    return load_supply_chain_product_action_duties(_SHIPPED)


_STEP_9 = {
    ProductAction.ISSUE_ITEM_CODE,
    ProductAction.ADD_SKU,
    ProductAction.REMOVE_SKU,
    ProductAction.SUBMIT_FOR_SIGNOFF,
}
# What a tenant could have stored at 1.1.0 (S3): S1's steps and steps 7, 8.
_S3_OVERRIDE = _S1_OVERRIDE | {"complete_profile": "rnd", "confirm_with_supplier": "supply_lead"}


def test_the_shipped_default_is_1_2_0_with_steps_seven_eight_and_nine() -> None:
    """Steps 7 and 8 (S3): R&D completes the BM04, TP Cung ứng confirms with
    the supplier. Step 9 (S4): Cung ứng codes the product and submits it."""
    policy = _shipped()
    assert policy.policy_version == "1.2.0"
    assert policy.duty_for(ProductAction.COMPLETE_PROFILE) is CaseDuty.RND
    assert policy.duty_for(ProductAction.CONFIRM_WITH_SUPPLIER) is CaseDuty.SUPPLY_LEAD
    for action in _STEP_9:
        assert policy.duty_for(action) is CaseDuty.ORDERING, action
    assert {
        "1.0.0": {
            ProductAction.COMPLETE_PROFILE,
            ProductAction.CONFIRM_WITH_SUPPLIER,
            *_STEP_9,
        },
        "1.1.0": _STEP_9,
    } == STEPS_ADDED_AFTER


def test_a_1_0_0_override_takes_the_platforms_duty_for_steps_it_predates() -> None:
    """A tenant who wrote its override before steps 7, 8 and 9 existed never
    decided who takes them: the platform's answer applies to those, and
    every step the tenant did decide keeps the tenant's duty."""
    mine = _S1_OVERRIDE | {"cancel": "exceptions", "receive_sample": "ordering"}
    policy = SupplyChainProductActionDuties.from_stored(
        _document(mine, version="1.0.0"), _shipped()
    )

    assert policy.duty_for(ProductAction.COMPLETE_PROFILE) is CaseDuty.RND
    assert policy.duty_for(ProductAction.CONFIRM_WITH_SUPPLIER) is CaseDuty.SUPPLY_LEAD
    assert policy.duty_for(ProductAction.SUBMIT_FOR_SIGNOFF) is CaseDuty.ORDERING
    assert policy.duty_for(ProductAction.CANCEL) is CaseDuty.EXCEPTIONS
    assert policy.duty_for(ProductAction.RECEIVE_SAMPLE) is CaseDuty.ORDERING


def test_a_1_1_0_override_takes_the_platforms_duty_for_step_nine_only() -> None:
    mine = _S3_OVERRIDE | {"confirm_with_supplier": "ordering"}
    policy = SupplyChainProductActionDuties.from_stored(
        _document(mine, version="1.1.0"), _shipped()
    )
    assert policy.duty_for(ProductAction.CONFIRM_WITH_SUPPLIER) is CaseDuty.ORDERING
    for action in _STEP_9:
        assert policy.duty_for(action) is CaseDuty.ORDERING, action


def test_a_1_1_0_override_missing_step_eight_is_broken_not_old() -> None:
    mine = dict(_S3_OVERRIDE)
    del mine["confirm_with_supplier"]
    with pytest.raises(ValidationError, match="confirm_with_supplier"):
        SupplyChainProductActionDuties.from_stored(_document(mine, version="1.1.0"), _shipped())


def test_a_1_0_0_override_that_already_names_a_later_step_keeps_its_choice() -> None:
    mine = _S1_OVERRIDE | {"complete_profile": "ordering", "add_sku": "supply_lead"}
    policy = SupplyChainProductActionDuties.from_stored(
        _document(mine, version="1.0.0"), _shipped()
    )
    assert policy.duty_for(ProductAction.COMPLETE_PROFILE) is CaseDuty.ORDERING
    assert policy.duty_for(ProductAction.ADD_SKU) is CaseDuty.SUPPLY_LEAD
    assert policy.duty_for(ProductAction.CONFIRM_WITH_SUPPLIER) is CaseDuty.SUPPLY_LEAD


def test_only_the_steps_a_1_0_0_override_predates_are_filled() -> None:
    """Fail closed: a 1.0.0 override missing a step that DID exist then is
    broken, not old, and refuses (as `test_a_broken_tenant_override_refuses_
    rather_than_falling_back` holds for the handlers)."""
    mine = dict(_S1_OVERRIDE)
    del mine["pass_sample"]
    with pytest.raises(ValidationError, match="pass_sample"):
        SupplyChainProductActionDuties.from_stored(_document(mine, version="1.0.0"), _shipped())


def test_an_override_written_at_1_2_0_must_name_every_step() -> None:
    """Only a document older than the steps is completed from the platform;
    one that claims the current version and leaves them out is refused, and
    so is a new override sent without them (the PUT body validates whole)."""
    with pytest.raises(ValidationError, match="submit_for_signoff"):
        SupplyChainProductActionDuties.from_stored(_document(_S3_OVERRIDE), _shipped())
    with pytest.raises(ValidationError, match="add_sku"):
        SupplyChainProductActionDuties.model_validate(_document(_S3_OVERRIDE, version="1.1.0"))


@pytest.mark.parametrize("graph_only", sorted(GRAPH_ONLY_ACTIONS))
def test_a_duty_for_a_step_only_the_graph_takes_is_refused(graph_only: ProductAction) -> None:
    """Nobody reads such a key (failure-modes #1): BGĐ is not a duty, it
    decides an approval limited by `required_scope`. Accepting the key would
    read like a control over who approves, and control nothing."""
    with pytest.raises(ValidationError, match=graph_only.value):
        SupplyChainProductActionDuties.model_validate(
            _document(_shipped_mapping() | {graph_only.value: "ordering"})
        )


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
        (ProductAction.COMPLETE_PROFILE, CaseDuty.RND),
        (ProductAction.CONFIRM_WITH_SUPPLIER, CaseDuty.SUPPLY_LEAD),
        (ProductAction.ISSUE_ITEM_CODE, CaseDuty.ORDERING),
        (ProductAction.ADD_SKU, CaseDuty.ORDERING),
        (ProductAction.REMOVE_SKU, CaseDuty.ORDERING),
        (ProductAction.SUBMIT_FOR_SIGNOFF, CaseDuty.ORDERING),
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
    `rnd` or `supply_lead` existed stays valid."""
    po = load_supply_chain_action_duties(_PO_SHIPPED)
    assert {CaseDuty.RND, CaseDuty.SUPPLY_LEAD}.isdisjoint(set(po.action_duties.values()))
