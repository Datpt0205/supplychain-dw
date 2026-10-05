"""Unit: `brief_policy` — a tenant may reorder the brief, never hide a group."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dw_supply_chain.brief_policy import SupplyChainBriefPolicy, load_supply_chain_brief_policy
from dw_supply_chain.domain.daily_brief import BriefSignal

pytestmark = pytest.mark.unit

_SHIPPED = (
    Path(__file__).resolve().parents[5] / "configs" / "policies" / "supply_chain_brief@1.0.0.yaml"
)


def _policy(order: list[str]) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "policy_id": "supply_chain_brief",
        "policy_version": "1.0.0",
        "signal_order": order,
    }


def test_the_shipped_default_lists_every_signal_once() -> None:
    policy = load_supply_chain_brief_policy(_SHIPPED)
    assert sorted(policy.signal_order) == sorted(BriefSignal)
    # The one choice the default makes on purpose: news reads last.
    assert policy.signal_order[-1] is BriefSignal.CHANGED_RECENTLY


def test_any_complete_reordering_is_accepted() -> None:
    order = [signal.value for signal in reversed(BriefSignal)]
    policy = SupplyChainBriefPolicy.model_validate(_policy(order))
    assert [signal.value for signal in policy.signal_order] == order


def test_an_order_that_drops_a_signal_is_refused() -> None:
    order = [s.value for s in BriefSignal if s is not BriefSignal.UPDATE_ESCALATION_DUE]
    with pytest.raises(ValidationError, match="update_escalation_due"):
        SupplyChainBriefPolicy.model_validate(_policy(order))


def test_an_order_that_repeats_a_signal_is_refused() -> None:
    order = [s.value for s in BriefSignal] + [BriefSignal.SLA_BREACHED.value]
    with pytest.raises(ValidationError, match="repeated"):
        SupplyChainBriefPolicy.model_validate(_policy(order))


def test_an_unknown_signal_is_refused() -> None:
    order = [s.value for s in BriefSignal] + ["supplier_mood"]
    with pytest.raises(ValidationError):
        SupplyChainBriefPolicy.model_validate(_policy(order))


def test_an_unknown_field_is_refused() -> None:
    document = _policy([s.value for s in BriefSignal]) | {"hidden_signals": ["rework"]}
    with pytest.raises(ValidationError):
        SupplyChainBriefPolicy.model_validate(document)
