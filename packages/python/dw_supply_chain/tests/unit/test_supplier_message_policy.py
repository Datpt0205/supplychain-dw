"""Unit: the templates of a message to a supplier (ticket ai-automation/07).
Every purpose a tenant may enable has a template, and a template names only
the placeholders code fills, checked when the document loads."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from dw_platform.application.access_context import AccessContext
from dw_supply_chain.domain.supplier_message import MessagePurpose
from dw_supply_chain.policy_files import SUPPLIER_MESSAGES_POLICY_FILE
from dw_supply_chain.supplier_message_policy import (
    SupplyChainSupplierMessages,
    load_supply_chain_supplier_messages,
    resolve_supplier_messages,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[5]
SHIPPED = REPO_ROOT / "configs" / "policies" / SUPPLIER_MESSAGES_POLICY_FILE


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


class _Stored:
    def __init__(self, stored: dict[str, Any]) -> None:
        self.stored = stored

    async def get(self, context: AccessContext, policy_id: str) -> dict[str, Any] | None:
        return self.stored

    async def put(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError("not exercised by resolution")


def _resolve(stored: dict[str, Any]) -> SupplyChainSupplierMessages:
    context = AccessContext(
        tenant_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
        roles=frozenset(),
        scopes=frozenset(),
        plan_id="professional",
    )
    platform = load_supply_chain_supplier_messages(SHIPPED)
    return asyncio.run(resolve_supplier_messages(context, _Stored(stored), platform))


def test_a_tenant_wording_from_before_a_purpose_existed_takes_the_platforms_for_it() -> None:
    raw = _raw()
    raw["policy_version"] = "1.1.0"
    del raw["templates"]["production_progress"]
    raw["templates"]["sample_request"]["subject"] = "[{reference}] Mẫu của chúng tôi"
    resolved = _resolve(raw)
    platform = load_supply_chain_supplier_messages(SHIPPED)
    assert (
        resolved.templates[MessagePurpose.PRODUCTION_PROGRESS]
        == platform.templates[MessagePurpose.PRODUCTION_PROGRESS]
    )
    # Only the missing purpose: the tenant's own wording stays its own.
    assert resolved.templates[MessagePurpose.SAMPLE_REQUEST].subject == (
        "[{reference}] Mẫu của chúng tôi"
    )


def test_a_current_tenant_wording_missing_a_purpose_is_still_refused() -> None:
    raw = _raw()
    del raw["templates"]["production_progress"]
    with pytest.raises(ValidationError, match="production_progress"):
        _resolve(raw)
