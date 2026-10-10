"""Unit: `brief_policy` — a tenant may reorder the brief, never hide a group."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dw_supply_chain.brief_policy import (
    SIGNALS_ADDED_AFTER,
    SupplyChainBriefPolicy,
    load_supply_chain_brief_policy,
)
from dw_supply_chain.domain.daily_brief import PRODUCT_SIGNALS, BriefSignal

pytestmark = pytest.mark.unit

_SHIPPED = (
    Path(__file__).resolve().parents[5] / "configs" / "policies" / "supply_chain_brief@1.1.0.yaml"
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


# ---- overrides stored before the stage-1 signals (ticket 08) ------------------------

# The shipped 1.0.0 default, as a tenant might have stored it unchanged.
_ORDER_1_0_0 = [
    "update_escalation_due",
    "sla_breached",
    "case_blocked",
    "approval_pending",
    "manual_review",
    "supplier_reported_delay",
    "update_reminder_due",
    "waiting_external",
    "rework",
    "waiting_on_us",
    "changed_recently",
]


def test_the_signals_added_in_1_1_0_are_the_stage_one_groups() -> None:
    assert SIGNALS_ADDED_AFTER["1.0.0"] == PRODUCT_SIGNALS


def test_a_stored_1_0_0_default_reads_as_the_1_1_0_default() -> None:
    """Every new signal lands where the platform puts it, so a tenant that
    never reordered sees exactly what a new tenant sees."""
    shipped = load_supply_chain_brief_policy(_SHIPPED)
    stored = SupplyChainBriefPolicy.from_stored(_policy(_ORDER_1_0_0), shipped)
    assert stored.signal_order == shipped.signal_order


def test_a_stored_1_0_0_reordering_keeps_every_place_the_tenant_chose() -> None:
    shipped = load_supply_chain_brief_policy(_SHIPPED)
    tenant_order = list(reversed(_ORDER_1_0_0))
    stored = SupplyChainBriefPolicy.from_stored(_policy(tenant_order), shipped)
    old = [s.value for s in stored.signal_order if s not in PRODUCT_SIGNALS]
    assert old == tenant_order
    placed = [s.value for s in stored.signal_order]
    # Each new signal right after its predecessor in the platform order.
    assert placed.index("product_sla_breached") == placed.index("sla_breached") + 1
    assert placed.index("product_awaiting_bod") == placed.index("approval_pending") + 1
    assert placed.index("product_awaiting_signoff") == placed.index("product_awaiting_bod") + 1
    assert placed.index("sample_evaluated_today") == placed.index("waiting_on_us") + 1


def test_a_stored_1_0_0_override_missing_an_old_signal_is_still_refused() -> None:
    shipped = load_supply_chain_brief_policy(_SHIPPED)
    order = [s for s in _ORDER_1_0_0 if s != "update_escalation_due"]
    with pytest.raises(ValidationError, match="update_escalation_due"):
        SupplyChainBriefPolicy.from_stored(_policy(order), shipped)


def test_an_override_claiming_1_1_0_must_name_the_new_signals() -> None:
    shipped = load_supply_chain_brief_policy(_SHIPPED)
    document = _policy(_ORDER_1_0_0) | {"policy_version": "1.1.0"}
    with pytest.raises(ValidationError, match="product_sla_breached"):
        SupplyChainBriefPolicy.from_stored(document, shipped)
