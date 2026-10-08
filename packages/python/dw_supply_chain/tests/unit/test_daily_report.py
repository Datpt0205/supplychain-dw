"""Unit: the stage-1 daily report to TP Cung ứng (ticket 08, QE-19 provisional)
— when it goes, to whom, once, and what its title may carry."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.ports import FixedClock
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.application.daily_report import BRIEF_LINK, SendStageOneReport
from dw_supply_chain.application.handlers import PRODUCT_CASE_READ
from dw_supply_chain.domain.product_development_case import (
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
    SampleResult,
    SampleRound,
)
from dw_supply_chain.policy_files import SLA_POLICY_FILE
from dw_supply_chain.sla_policy import load_supply_chain_sla_policy
from dw_supply_chain.testing.product_cases import InMemoryProductCases

pytestmark = pytest.mark.unit

_POLICIES = Path(__file__).resolve().parents[5] / "configs" / "policies"
TENANT, W1, W2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
LEAD, LEAD_NO_READ, READER = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
SUPPLY_LEAD = "supply_chain.duty.supply_lead"
# 17:30 and 16:30 in Vietnam on 7 October.
AFTER, BEFORE = datetime(2026, 10, 7, 10, 30, tzinfo=UTC), datetime(2026, 10, 7, 9, 30, tzinfo=UTC)


def _product(
    code: str, state: ProductDevState, *, name: str = "Nồi gang", workspace: uuid.UUID = W1
) -> ProductDevelopmentCase:
    return ProductDevelopmentCase(
        id=ProductDevelopmentCaseId(uuid.uuid4()),
        tenant_id=TenantId(TENANT),
        workspace_id=WorkspaceId(workspace),
        proposal_code=code,
        product_name=name,
        category="noi",
        pic_user_id=LEAD,
        created_by=LEAD,
        state=state,
        created_at=AFTER - timedelta(hours=1),
    )


def _closed(result: SampleResult, at: datetime) -> SampleRound:
    return SampleRound(
        round_no=1,
        opened_at=at - timedelta(days=1),
        opened_by=LEAD,
        result=result,
        evaluation_document_id=None,
        closed_at=at,
        closed_by=LEAD,
        revision_document_id=None,
        requested_changes=None,
    )


@dataclass
class _Workspaces:
    pairs: list[tuple[uuid.UUID, uuid.UUID]]

    async def workspaces(self) -> list[tuple[uuid.UUID, uuid.UUID]]:
        return self.pairs


@dataclass
class _Holders:
    """Who holds which scope in which workspace; any-of, as the port says."""

    held: dict[tuple[uuid.UUID, uuid.UUID], frozenset[str]]

    async def holding(
        self, tenant_id: uuid.UUID, workspace_id: uuid.UUID, scopes: frozenset[str]
    ) -> list[uuid.UUID]:
        return [
            user for (ws, user), mine in self.held.items() if ws == workspace_id and mine & scopes
        ]


@dataclass
class _Inbox:
    """Once per (recipient, source_key), as `platform.deliver_notification`."""

    sent: dict[tuple[uuid.UUID, str], dict[str, Any]] = field(default_factory=dict)

    async def deliver(
        self,
        context: AccessContext,
        *,
        recipients: Sequence[uuid.UUID],
        source_key: str,
        title: str,
        body: str,
        link: str | None,
    ) -> None:
        for recipient in recipients:
            self.sent.setdefault(
                (recipient, source_key),
                {"workspace": context.workspace_id, "title": title, "body": body, "link": link},
            )


class _NoOverrides:
    async def get(self, context: AccessContext, policy_id: str) -> None:
        return None

    async def put(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError("the report reads policies")


def _lane(products: InMemoryProductCases, inbox: _Inbox, now: datetime) -> SendStageOneReport:
    return SendStageOneReport(
        workspaces=_Workspaces([(TENANT, W1), (TENANT, W2)]),
        product_case_repo=products,
        policy_override_repo=_NoOverrides(),
        platform_default_sla_policy=load_supply_chain_sla_policy(_POLICIES / SLA_POLICY_FILE),
        holders=_Holders(
            {
                (W1, LEAD): frozenset({SUPPLY_LEAD, PRODUCT_CASE_READ}),
                # TP Cung ứng's duty without the read: never told about cases.
                (W1, LEAD_NO_READ): frozenset({SUPPLY_LEAD}),
                # A reader who is not TP Cung ứng.
                (W1, READER): frozenset({PRODUCT_CASE_READ}),
            }
        ),
        notifier=inbox,
        clock=FixedClock(now),
    )


def _seeded() -> InMemoryProductCases:
    products = InMemoryProductCases()
    passed = _product("SP-001", ProductDevState.PENDING_BOD_REVIEW, name="Chảo bí mật 28")
    products.seed([passed])
    products.seed_round(passed, _closed(SampleResult.PASSED, AFTER - timedelta(hours=2)))
    return products


async def test_one_notice_per_day_to_each_supply_lead_who_may_read_cases() -> None:
    inbox, products = _Inbox(), _seeded()
    outcome = await _lane(products, inbox, AFTER).run()
    again = await _lane(products, inbox, AFTER + timedelta(hours=3)).run()

    assert outcome.sent == 1 and again.sent == 1  # delivered again: a no-op
    assert set(inbox.sent) == {(LEAD, f"supply_chain.stage_one_report:{W1}:2026-10-07")}
    [notice] = inbox.sent.values()
    assert notice["workspace"] == W1
    assert notice["link"] == BRIEF_LINK
    assert notice["body"].startswith("Mẫu đã đánh giá hôm nay: 1 đạt, 0 cần chỉnh sửa, 0 hủy.")
    assert "Chờ BGĐ duyệt: 1." in notice["body"]


async def test_the_title_names_only_the_date() -> None:
    """Z2 sends the title to Zalo: no product name, no code, no supplier."""
    inbox = _Inbox()
    await _lane(_seeded(), inbox, AFTER).run()
    [notice] = inbox.sent.values()
    assert notice["title"] == "Báo cáo giai đoạn 1 ngày 07/10/2026"
    assert "SP-001" not in notice["title"] and "Chảo" not in notice["title"]


async def test_nothing_goes_before_the_hour() -> None:
    inbox = _Inbox()
    assert (await _lane(_seeded(), inbox, BEFORE).run()).sent == 0
    assert inbox.sent == {}


async def test_a_workspace_with_nothing_to_report_is_not_told() -> None:
    inbox, products = _Inbox(), InMemoryProductCases()
    products.seed([_product("SP-9", ProductDevState.PROPOSED)])
    assert (await _lane(products, inbox, AFTER).run()).sent == 0
    assert inbox.sent == {}


async def test_another_workspaces_cases_are_not_counted() -> None:
    inbox, products = _Inbox(), InMemoryProductCases()
    products.seed([_product("SP-W2", ProductDevState.PENDING_BOD_REVIEW, workspace=W2)])
    await _lane(products, inbox, AFTER).run()
    # W2 has the case but no TP Cung ứng; W1 has the lead but no case.
    assert inbox.sent == {}


async def test_one_failing_workspace_does_not_stop_the_others() -> None:
    class _Broken(InMemoryProductCases):
        async def list_active(self, context: AccessContext) -> list[ProductDevelopmentCase]:
            if context.workspace_id == W2:
                raise RuntimeError("bad row")
            return await super().list_active(context)

    inbox, products = _Inbox(), _Broken()
    passed = _product("SP-001", ProductDevState.PENDING_BOD_REVIEW)
    products.seed([passed])
    lane = replace(
        _lane(products, inbox, AFTER), workspaces=_Workspaces([(TENANT, W2), (TENANT, W1)])
    )
    outcome = await lane.run()
    assert (outcome.sent, outcome.failed_workspaces) == (1, 1)
