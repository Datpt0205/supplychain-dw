"""The SLA policy's own answers, plus a real load of the shipped file.

`GetSLAEvaluation` (`application/handlers.py`) is the real caller now,
via `domain/sla_evaluation.py`. This file stays scoped to the policy's own
parsing/lookup rules, which `test_sla_evaluation.py` builds test policies
directly against rather than re-testing here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from dw_supply_chain.sla_policy import (
    ProductCategory,
    SLAConfirmationStatus,
    SLAMilestone,
    SupplierUpdateCadence,
    SupplyChainSLAPolicy,
    load_supply_chain_sla_policy,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[5]
POLICY_PATH = REPO_ROOT / "configs" / "policies" / "supply_chain_sla@2.0.0.yaml"


def _policy(**overrides: object) -> SupplyChainSLAPolicy:
    defaults: dict[str, object] = {
        "schema_version": "2.0",
        "policy_id": "supply_chain_sla",
        "policy_version": "2.0.0",
        "categories": [ProductCategory(key="noi", label="Nồi")],
        "default": {
            "deposit": SLAMilestone(
                duration="10d", status=SLAConfirmationStatus.PENDING_BUSINESS_CONFIRMATION
            ),
        },
        "supplier_update": {"reminder_after": "5d", "escalation_after": "10d"},
    }
    defaults.update(overrides)
    return SupplyChainSLAPolicy(**defaults)


def test_the_shipped_policy_file_parses_with_short_test_durations() -> None:
    """The real committed file, not a fixture — a typo here would otherwise
    only be caught the day something finally reads it."""
    policy = load_supply_chain_sla_policy(POLICY_PATH)
    assert policy.policy_id == "supply_chain_sla"
    assert set(policy.default) == {
        "sample_collection",
        "sample_testing",
        "bod_review",
        "bm04",
        "supplier_confirmation",
        "item_coding",
        "signoff",
        "deposit",
        "port_arrival",
        "payment",
        "warehouse_receipt",
    }
    # Short values, asked for so a breach can be seen within a day or two,
    # not Elmich's reference numbers (those are Elmich's own override).
    assert policy.policy_version == "2.0.0"
    assert policy.default["bm04"].duration_days == 2
    assert policy.default["supplier_confirmation"].duration_days == 2
    assert policy.default["deposit"].duration_days == 1
    assert policy.default["port_arrival"].duration_days == 2
    assert policy.default["payment"].duration_days == 1
    assert policy.default["warehouse_receipt"].duration_days == 1
    assert [c.key for c in policy.categories] == ["noi", "chao"]
    assert policy.duration_days_for("sample_testing", "chao") == 1
    assert policy.duration_days_for("sample_testing", "noi") == 2


def test_every_shipped_milestone_is_confirmed_so_it_is_evaluated() -> None:
    """A pending milestone evaluates as not_evaluable, so a shipped default
    left pending would make every SLA signal silent while reading as set."""
    policy = load_supply_chain_sla_policy(POLICY_PATH)
    for name, milestone in policy.default.items():
        assert milestone.status is SLAConfirmationStatus.CONFIRMED, name
        assert policy.duration_days_for(name) == milestone.duration_days


def test_a_confirmed_milestone_returns_its_duration() -> None:
    policy = _policy(
        default={
            "deposit": SLAMilestone(duration="10d", status=SLAConfirmationStatus.CONFIRMED),
        }
    )
    assert policy.duration_days_for("deposit") == 10


def test_a_pending_milestone_returns_none_even_though_it_exists() -> None:
    policy = _policy()
    assert policy.duration_days_for("deposit") is None


def test_an_unknown_milestone_returns_none_the_same_as_a_pending_one() -> None:
    """Deliberately indistinguishable from the pending case: a caller must
    not be able to special-case "not found" into treating a pending number
    as real."""
    policy = _policy()
    assert policy.duration_days_for("from_a_later_release") is None


def test_duration_must_be_whole_days() -> None:
    with pytest.raises(ValidationError):
        SLAMilestone(duration="4h", status=SLAConfirmationStatus.PENDING_BUSINESS_CONFIRMATION)
    with pytest.raises(ValidationError):
        SLAMilestone(duration="4", status=SLAConfirmationStatus.PENDING_BUSINESS_CONFIRMATION)


def test_an_unrecognised_status_is_refused_by_the_schema() -> None:
    """A fixed set enforced by the type, not a free string a typo can silently
    join — same discipline as CaseState in po_case.py."""
    with pytest.raises(ValidationError):
        SLAMilestone(duration="10d", status="confirmed_by_email")


def test_an_unversioned_or_malformed_file_is_refused_not_guessed() -> None:
    raw = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    del raw["policy_version"]
    with pytest.raises(ValidationError):
        SupplyChainSLAPolicy.model_validate(raw)


def test_the_shipped_policy_reminds_after_one_day_and_escalates_after_two() -> None:
    cadence = load_supply_chain_sla_policy(POLICY_PATH).supplier_update.cadence
    assert (cadence.reminder_after_days, cadence.escalation_after_days) == (1, 2)


def test_an_escalation_must_come_after_the_reminder() -> None:
    for reminder, escalation in (("2d", "2d"), ("3d", "1d")):
        with pytest.raises(ValidationError, match="longer than reminder_after"):
            SupplierUpdateCadence(reminder_after=reminder, escalation_after=escalation)


def test_a_zero_day_reminder_is_allowed_for_testing_the_chain() -> None:
    cadence = SupplierUpdateCadence(reminder_after="0d", escalation_after="1d").cadence
    assert (cadence.reminder_after_days, cadence.escalation_after_days) == (0, 1)


def test_a_policy_without_a_supplier_update_cadence_is_refused() -> None:
    """No default in code: an override written before 1.2.0 was given the
    old 5d/10d by migration, and anything else missing it is refused."""
    document = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    del document["supplier_update"]
    with pytest.raises(ValidationError, match="supplier_update"):
        SupplyChainSLAPolicy.model_validate(document)


# ---- 2.0: the tenant's Category list (ADR 0019) ---------------------------------


def test_a_1_x_document_is_refused_its_overrides_were_migrated() -> None:
    """`sla` without `categories` is the 1.x shape; d56da3dd2146 rewrote every
    stored one, so a 1.x document reaching the schema is refused, never read
    as an empty policy."""
    with pytest.raises(ValidationError):
        SupplyChainSLAPolicy.model_validate(
            {
                "schema_version": "1.0",
                "policy_id": "supply_chain_sla",
                "policy_version": "1.2.0",
                "sla": {"deposit": {"duration": "1d", "status": "confirmed"}},
                "supplier_update": {"reminder_after": "1d", "escalation_after": "2d"},
            }
        )


def test_an_empty_category_list_is_refused_nobody_could_open_a_case() -> None:
    with pytest.raises(ValidationError):
        _policy(categories=[])


def test_a_repeated_category_key_is_refused() -> None:
    with pytest.raises(ValidationError, match="repeat"):
        _policy(
            categories=[
                ProductCategory(key="noi", label="Nồi"),
                ProductCategory(key="noi", label="Nồi inox"),
            ]
        )


def test_a_number_for_a_category_not_in_the_list_is_refused() -> None:
    """A typo in `by_category` would be a number nothing reads."""
    with pytest.raises(ValidationError, match="does not have"):
        _policy(
            by_category={
                "cha0": {"deposit": SLAMilestone(duration="1d", status="confirmed")},
            }
        )


@pytest.mark.parametrize("key", ["Nồi", "noi chao", "", "-noi", "x" * 65])
def test_a_category_key_is_a_slug(key: str) -> None:
    with pytest.raises(ValidationError):
        ProductCategory(key=key, label="Nồi")


def test_a_category_label_must_say_something() -> None:
    with pytest.raises(ValidationError):
        ProductCategory(key="noi", label="   ")


def test_the_category_lookup_is_by_exact_key() -> None:
    policy = _policy()
    assert policy.category("noi") == ProductCategory(key="noi", label="Nồi")
    assert policy.category("Nồi") is None
    assert policy.category("NOI") is None


def test_elmichs_own_numbers_are_a_valid_override_and_all_still_pending() -> None:
    """`scripts/elmich_sla_override.yaml`, what `seed_supply_chain_demo.py
    elmich-sla` writes for Elmich's tenant: valid as a tenant document, with
    the numbers of process.md, none enforced until Elmich confirms (QE-04,
    QE-05)."""
    policy = load_supply_chain_sla_policy(REPO_ROOT / "scripts" / "elmich_sla_override.yaml")
    assert {name: m.duration_days for name, m in policy.default.items()} == {
        "bm04": 4,
        "supplier_confirmation": 5,
        "deposit": 10,
        "port_arrival": 21,
        "payment": 10,
        "warehouse_receipt": 5,
    }
    assert all(
        m.status is SLAConfirmationStatus.PENDING_BUSINESS_CONFIRMATION
        for m in policy.default.values()
    )
    assert all(policy.duration_days_for(name) is None for name in policy.default)
