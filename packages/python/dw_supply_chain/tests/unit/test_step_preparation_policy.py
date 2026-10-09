"""Unit: which steps AI prepares (ticket ai-automation/05). Everything an entry
names is checked when the document loads, so nothing it names is read by
nothing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from dw_supply_chain.domain.product_development_case import ProductAction
from dw_supply_chain.domain.step_proposal import PREPARABLE_ACTIONS
from dw_supply_chain.domain.supplier_message import MessagePurpose
from dw_supply_chain.step_preparation_policy import (
    SupplyChainStepPreparation,
    load_supply_chain_step_preparation,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[5]


def _policy(*steps: dict[str, Any]) -> SupplyChainStepPreparation:
    return SupplyChainStepPreparation.model_validate(
        {
            "schema_version": "1.0",
            "policy_id": "supply_chain_step_preparation",
            "policy_version": "1.0.0",
            "steps": list(steps),
        }
    )


CONFIRM = {
    "case_kind": "product",
    "state": "supplier_confirmation",
    "action": "confirm_with_supplier",
    "sources": ["supplier_confirmation_email"],
}


def test_the_platform_prepares_nothing() -> None:
    policy = load_supply_chain_step_preparation(
        REPO_ROOT / "configs" / "policies" / "supply_chain_step_preparation@1.0.0.yaml"
    )
    assert policy.steps == ()


def test_elmichs_override_loads_and_prepares_its_steps() -> None:
    policy = load_supply_chain_step_preparation(
        REPO_ROOT / "scripts" / "elmich_step_preparation_override.yaml"
    )
    assert [s.action for s in policy.steps] == [
        ProductAction.PASS_SAMPLE,
        ProductAction.COMPLETE_PROFILE,
        ProductAction.CONFIRM_WITH_SUPPLIER,
    ]
    assert policy.steps[0].physical and policy.steps[0].result_fields
    assert set(policy.supplier_messages) == set(MessagePurpose)
    assert policy.bod_submission


def test_the_platform_drafts_no_supplier_message_and_a_purpose_is_listed_once() -> None:
    platform = load_supply_chain_step_preparation(
        REPO_ROOT / "configs" / "policies" / "supply_chain_step_preparation@1.0.0.yaml"
    )
    assert platform.supplier_messages == ()
    with pytest.raises(ValidationError, match="listed twice"):
        SupplyChainStepPreparation.model_validate(
            {
                "schema_version": "1.0",
                "policy_id": "supply_chain_step_preparation",
                "policy_version": "1.0.0",
                "supplier_messages": ["sample_request", "sample_request"],
            }
        )


def test_only_steps_whose_only_input_is_a_document_can_be_proposed() -> None:
    # A reason, a supplier name or a code cannot ride on a proposal yet.
    assert ProductAction.REQUEST_REVISION not in PREPARABLE_ACTIONS
    assert ProductAction.REQUEST_SAMPLE not in PREPARABLE_ACTIONS
    assert ProductAction.ISSUE_ITEM_CODE not in PREPARABLE_ACTIONS
    assert ProductAction.BOD_APPROVE not in PREPARABLE_ACTIONS
    assert ProductAction.PLACE_ORDER not in PREPARABLE_ACTIONS
    assert ProductAction.PASS_SAMPLE in PREPARABLE_ACTIONS


@pytest.mark.parametrize(
    ("change", "words"),
    [
        ({"case_kind": "po"}, "PO steps"),
        ({"action": "request_revision", "state": "sample_testing"}, "needs more than"),
        ({"state": "item_coding"}, "is not taken from"),
        ({"sources": []}, "needs a supplier_confirmation_email"),
        ({"result_fields": ["conclusion"]}, "only a physical step"),
        ({"physical": True}, "names the result"),
        ({"drafts": [{"doc_type": "sample_photo", "recipe": "case_facts"}]}, "no template"),
        ({"checks": ["guess"]}, "checks"),
    ],
)
def test_an_entry_that_would_do_nothing_is_refused_by_name(
    change: dict[str, Any], words: str
) -> None:
    with pytest.raises(ValidationError, match=words):
        _policy({**CONFIRM, **change})


def test_a_physical_step_types_its_result_on_the_drafted_paper() -> None:
    with pytest.raises(ValidationError, match="drafted paper"):
        _policy(
            {
                **CONFIRM,
                "physical": True,
                "result_fields": ["confirmed_on"],
            }
        )


def test_a_state_is_prepared_once() -> None:
    with pytest.raises(ValidationError, match="prepared twice"):
        _policy(CONFIRM, CONFIRM)
