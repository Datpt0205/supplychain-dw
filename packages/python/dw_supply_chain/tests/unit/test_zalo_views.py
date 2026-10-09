"""Unit: what a chat reply may name (ADR 0026, E15; ticket ai-automation/01).

A reply is built from `zalo_views`, never from a case; the views' fields are
the chat's list of allowed fields and must stay disjoint from every price
field. Adding `unit_price` (or any `PRICE_FIELDS` member) to a view turns the
first test red.
"""

from __future__ import annotations

import uuid
from dataclasses import fields

import pytest

from dw_kernel.ids import TenantId, WorkspaceId
from dw_supply_chain.domain.commercial import BANK_ACCOUNT_FIELDS, PRICE_FIELDS
from dw_supply_chain.domain.po_case import CaseState, POCase, POCaseId
from dw_supply_chain.presentation import zalo_case_query
from dw_supply_chain.presentation.zalo_views import (
    ZALO_FIELDS,
    ZaloPOCaseView,
    ZaloProductCaseView,
    po_case_view,
)

pytestmark = pytest.mark.unit


def test_the_chats_field_list_holds_no_price_amount_term_or_bank_detail() -> None:
    assert {
        f.name for view in (ZaloPOCaseView, ZaloProductCaseView) for f in fields(view)
    } == ZALO_FIELDS
    assert ZALO_FIELDS.isdisjoint(PRICE_FIELDS | BANK_ACCOUNT_FIELDS | {"currency", "incoterm"})


def test_a_po_line_in_a_reply_names_only_the_views_fields() -> None:
    case = POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(uuid.uuid4()),
        workspace_id=WorkspaceId(uuid.uuid4()),
        po_reference="PO-001",
        supplier_name="NCC A",
        state=CaseState.PRODUCTION,
    )
    view = po_case_view(case)
    line = zalo_case_query._case_line(case, "https://portal.example/")
    assert line == (
        f"- PO-001 — NCC A: Đang sản xuất https://portal.example/supply-chain/po-cases/{view.id}"
    )
