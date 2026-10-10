"""An in-memory world for the reports (ticket ai-automation/20): their unit
tests and the `supply_chain.weekly_report` eval grader run the REAL handlers
over `InMemoryReportReads`, which keeps its port's promise: a reader counts
only its own tenant AND workspace (RLS).
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from dw_kernel.ports import FixedClock, Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_supply_chain.ai_time_saved_policy import (
    SupplyChainAiTimeSaved,
    load_supply_chain_ai_time_saved,
)
from dw_supply_chain.application.reports import (
    GetAiAcceptance,
    GetSupplierScorecard,
    GetWeeklyReport,
    SummarizeWeeklyReport,
)
from dw_supply_chain.domain.reports import CaseMove, DraftVersion, SupplierRecord
from dw_supply_chain.policy_files import AI_TIME_SAVED_POLICY_FILE
from dw_supply_chain.testing.purchase_orders import NOW
from dw_supply_chain.testing.step_preparation import REPO_ROOT, _NoOverrides

Scope = tuple[uuid.UUID, uuid.UUID]
SHIPPED_TIME_SAVED: SupplyChainAiTimeSaved = load_supply_chain_ai_time_saved(
    REPO_ROOT / "configs" / "policies" / AI_TIME_SAVED_POLICY_FILE
)


@dataclass
class InMemoryReportReads:
    product: list[tuple[Scope, datetime, CaseMove]] = field(default_factory=list)
    po: list[tuple[Scope, datetime, CaseMove]] = field(default_factory=list)
    created: list[tuple[Scope, datetime, str]] = field(default_factory=list)
    product_states: list[tuple[Scope, str]] = field(default_factory=list)
    po_states: list[tuple[Scope, str]] = field(default_factory=list)
    suppliers: list[tuple[Scope, SupplierRecord]] = field(default_factory=list)
    drafts: list[tuple[Scope, datetime, DraftVersion]] = field(default_factory=list)

    @staticmethod
    def _mine(context: AccessContext, scope: Scope) -> bool:
        return scope == (context.tenant_id, context.workspace_id)

    async def product_moves(
        self, context: AccessContext, start: datetime, end: datetime
    ) -> list[CaseMove]:
        return [m for s, at, m in self.product if self._mine(context, s) and start <= at < end]

    async def po_moves(
        self, context: AccessContext, start: datetime, end: datetime
    ) -> list[CaseMove]:
        return [m for s, at, m in self.po if self._mine(context, s) and start <= at < end]

    async def po_created(self, context: AccessContext, start: datetime, end: datetime) -> list[str]:
        return [r for s, at, r in self.created if self._mine(context, s) and start <= at < end]

    async def open_states(
        self, context: AccessContext
    ) -> tuple[Mapping[str, int], Mapping[str, int]]:
        return (
            Counter(st for s, st in self.product_states if self._mine(context, s)),
            Counter(st for s, st in self.po_states if self._mine(context, s)),
        )

    async def supplier_records(self, context: AccessContext) -> list[SupplierRecord]:
        return [r for s, r in self.suppliers if self._mine(context, s)]

    async def draft_versions(self, context: AccessContext, since: datetime) -> list[DraftVersion]:
        began = {
            v.lineage_id
            for s, at, v in self.drafts
            if self._mine(context, s) and v.version == 1 and at >= since
        }
        return [v for s, _, v in self.drafts if self._mine(context, s) and v.lineage_id in began]


@dataclass
class ReportWorld:
    tenant_id: uuid.UUID = field(default_factory=uuid.uuid4)
    workspace_id: uuid.UUID = field(default_factory=uuid.uuid4)
    reads: InMemoryReportReads = field(default_factory=InMemoryReportReads)
    clock: FixedClock = field(default_factory=lambda: FixedClock(NOW))
    gateway: Any = None
    model_profile: str | None = None

    @property
    def scope(self) -> Scope:
        return (self.tenant_id, self.workspace_id)

    def context(
        self, scopes: frozenset[str], *, workspace: uuid.UUID | None = None
    ) -> AccessContext:
        return AccessContext(
            tenant_id=self.tenant_id,
            workspace_id=workspace or self.workspace_id,
            principal_id=uuid.uuid4(),
            roles=frozenset(),
            scopes=scopes,
            plan_id="professional",
        )

    def weekly(self) -> GetWeeklyReport:
        return GetWeeklyReport(
            reads=self.reads, authz=ScopeAuthorizationService(), clock=self.clock
        )

    def summary(self) -> SummarizeWeeklyReport:
        return SummarizeWeeklyReport(
            get=self.weekly(),
            gateway=self.gateway,
            ids=Uuid4Generator(),
            model_profile=self.model_profile,
        )

    def suppliers(self) -> GetSupplierScorecard:
        return GetSupplierScorecard(reads=self.reads, authz=ScopeAuthorizationService())

    def acceptance(self) -> GetAiAcceptance:
        return GetAiAcceptance(
            reads=self.reads,
            authz=ScopeAuthorizationService(),
            policy_override_repo=_NoOverrides(),
            platform_default_time_saved=SHIPPED_TIME_SAVED,
            clock=self.clock,
        )


__all__ = ["SHIPPED_TIME_SAVED", "InMemoryReportReads", "ReportWorld"]
