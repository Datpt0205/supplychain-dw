"""Unit: `action_duties` — every case step belongs to exactly one duty, and a
tenant's mapping may move a step between duties but never leave one out."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dw_supply_chain.action_duties import (
    CaseDuty,
    SupplyChainActionDuties,
    load_supply_chain_action_duties,
)
from dw_supply_chain.domain.packaging_design import PackagingAction
from dw_supply_chain.domain.po_case import CaseAction

pytestmark = pytest.mark.unit

_SHIPPED = (
    Path(__file__).resolve().parents[5]
    / "configs"
    / "policies"
    / "supply_chain_action_duties@1.3.0.yaml"
)


def _document(mapping: dict[str, str]) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "policy_id": "supply_chain_action_duties",
        "policy_version": "1.0.0",
        "action_duties": mapping,
    }


def _shipped_mapping() -> dict[str, str]:
    policy = load_supply_chain_action_duties(_SHIPPED)
    return {action.value: duty.value for action, duty in policy.action_duties.items()}


def test_the_shipped_default_gives_every_step_a_duty() -> None:
    policy = load_supply_chain_action_duties(_SHIPPED)
    assert set(policy.action_duties) == {*CaseAction, *PackagingAction}


@pytest.mark.parametrize(
    ("action", "duty"),
    [
        # The default keeps the classic procurement split: who orders, who
        # pays and who receives are different duties.
        (CaseAction.REQUEST_DEPOSIT, CaseDuty.ORDERING),
        (CaseAction.CONFIRM_DEPOSIT, CaseDuty.FINANCE),
        (CaseAction.REQUEST_FINAL_PAYMENT, CaseDuty.ORDERING),
        (CaseAction.CONFIRM_PAYMENT, CaseDuty.FINANCE),
        (CaseAction.PASS_QC, CaseDuty.QC),
        (CaseAction.START_WAREHOUSE_RECEIVING, CaseDuty.WAREHOUSE),
    ],
)
def test_the_default_separates_ordering_paying_and_receiving(
    action: CaseAction, duty: CaseDuty
) -> None:
    assert load_supply_chain_action_duties(_SHIPPED).duty_for(action) is duty


def test_a_mapping_that_leaves_a_step_out_is_refused() -> None:
    mapping = _shipped_mapping()
    del mapping["confirm_payment"]
    with pytest.raises(ValidationError, match="confirm_payment"):
        SupplyChainActionDuties.model_validate(_document(mapping))


@pytest.mark.parametrize(
    "mapping_change",
    [
        {"confirm_payment": "anyone"},  # a duty that does not exist
        {"refund_supplier": "finance"},  # a step that does not exist
    ],
)
def test_an_unknown_duty_or_step_is_refused(mapping_change: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        SupplyChainActionDuties.model_validate(_document(_shipped_mapping() | mapping_change))


def test_an_unknown_field_is_refused() -> None:
    document = _document(_shipped_mapping()) | {"default_duty": "ordering"}
    with pytest.raises(ValidationError):
        SupplyChainActionDuties.model_validate(document)


def test_step_ten_is_cung_ungs_since_1_1_0() -> None:
    """`create_po` (ticket 05) belongs to ordering, as the steps around it."""
    policy = load_supply_chain_action_duties(_SHIPPED)
    assert policy.duty_for(CaseAction.CREATE_PO) is CaseDuty.ORDERING


def test_step_12s_sub_flow_is_cung_ungs_its_test_rnds_and_its_content_mkts() -> None:
    """Slice PK: Cung ứng approves the colour and the design and receives the
    sample; R&D passes or fails the pre-production test (QE-03 provisional).
    1.3.0 (ticket ai-automation/16): Cung ứng sends MKT its pack, MKT submits
    the packaging content."""
    policy = load_supply_chain_action_duties(_SHIPPED)
    assert policy.policy_version == "1.3.0"
    rnd = {PackagingAction.PASS_PRE_PRODUCTION_TEST, PackagingAction.FAIL_PRE_PRODUCTION_TEST}
    for step in PackagingAction:
        expected = (
            CaseDuty.RND
            if step in rnd
            else CaseDuty.MKT
            if step is PackagingAction.SUBMIT_PACKAGING_CONTENT
            else CaseDuty.ORDERING
        )
        assert policy.duty_for(step) is expected, step


@pytest.mark.parametrize("version", ["1.0.0", "1.1.0", "1.2.0"])
def test_an_override_stored_before_mkts_steps_takes_the_platforms_duty_for_them_only(
    version: str,
) -> None:
    """Ticket ai-automation/16: a tenant's override written before 1.3.0 never
    decided MKT's two steps; it still loads, with the platform's duty for
    those two, and everything it did decide stands."""
    platform = load_supply_chain_action_duties(_SHIPPED)
    stored = {
        k: v
        for k, v in _shipped_mapping().items()
        if k not in {"send_mkt_pack", "submit_packaging_content"}
    } | {"confirm_payment": "ordering"}
    policy = SupplyChainActionDuties.from_stored(
        _document(stored) | {"policy_version": version}, platform
    )
    assert policy.duty_for(PackagingAction.SUBMIT_PACKAGING_CONTENT) is CaseDuty.MKT
    assert policy.duty_for(PackagingAction.SEND_MKT_PACK) is CaseDuty.ORDERING
    assert policy.duty_for(CaseAction.CONFIRM_PAYMENT) is CaseDuty.ORDERING
    # Only those two: any other gap is still refused, at any version.
    without = {k: v for k, v in stored.items() if k != "approve_colour"}
    with pytest.raises(ValidationError, match="approve_colour"):
        SupplyChainActionDuties.from_stored(
            _document(without) | {"policy_version": version}, platform
        )
    # And one claiming the current version must name them itself.
    with pytest.raises(ValidationError, match="send_mkt_pack"):
        SupplyChainActionDuties.from_stored(
            _document(stored) | {"policy_version": "1.3.0"}, platform
        )


def test_an_override_without_a_packaging_step_is_refused_so_the_migration_must_add_it() -> None:
    """Why slice PK ships a data migration, as ticket 05 did."""
    mine = {k: v for k, v in _shipped_mapping().items() if k != "approve_colour"}
    with pytest.raises(ValidationError, match="approve_colour"):
        SupplyChainActionDuties.model_validate(_document(mine))


def test_an_override_without_create_po_is_refused_so_the_migration_must_add_it() -> None:
    """Why ticket 05 ships a data migration: a stored override from before
    `create_po` fails this check on read, and every step of that tenant with it."""
    mine = {k: v for k, v in _shipped_mapping().items() if k != "create_po"}
    with pytest.raises(ValidationError, match="create_po"):
        SupplyChainActionDuties.model_validate(_document(mine))
