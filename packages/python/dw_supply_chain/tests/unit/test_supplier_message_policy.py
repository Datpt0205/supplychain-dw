"""Unit: the templates of a message to a supplier (ticket ai-automation/07).
Every purpose a tenant may enable has a template, and a template names only
the placeholders code fills, checked when the document loads."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from dw_supply_chain.domain.supplier_message import MessagePurpose
from dw_supply_chain.supplier_message_policy import (
    SupplyChainSupplierMessages,
    load_supply_chain_supplier_messages,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[5]
SHIPPED = REPO_ROOT / "configs" / "policies" / "supply_chain_supplier_messages@1.1.0.yaml"


def _raw() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))
    return loaded


def test_the_shipped_templates_word_every_purpose() -> None:
    policy = load_supply_chain_supplier_messages(SHIPPED)
    assert set(policy.templates) == set(MessagePurpose)


def test_a_purpose_without_a_template_is_refused_by_name() -> None:
    raw = _raw()
    del raw["templates"]["supplier_reminder"]
    with pytest.raises(ValidationError, match="supplier_reminder"):
        SupplyChainSupplierMessages.model_validate(raw)


def test_a_placeholder_code_does_not_fill_is_refused() -> None:
    raw = _raw()
    raw["templates"]["sample_request"]["subject"] = "[{reference}] giá {unit_price}"
    with pytest.raises(ValidationError, match="unit_price"):
        SupplyChainSupplierMessages.model_validate(raw)
