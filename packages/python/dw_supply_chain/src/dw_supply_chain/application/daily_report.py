"""The stage-1 daily report to TP Cung ứng (process.md section 3.2, step 3:
"báo cáo hàng ngày cho Trưởng phòng Cung ứng").

QE-19 is unanswered; the provisional reading (ticket 08, Comments), the
narrowest that does the job:

- **It is the brief, not a second report.** The groups are the daily brief's
  own stage-1 groups (`handlers.read_stage_one`, `daily_brief.stage_one_groups`),
  so the report cannot count a case the brief does not show. The content lives
  on the portal; the notice carries counts and the link to the brief.
- **One in-app notice per day per TP Cung ứng in the workspace**: every member
  holding both `supply_chain.duty.supply_lead` and `PRODUCT_CASE_READ` (a
  notice about cases goes only to someone who may open them). The notification
  inbox's once-per-`source_key` delivery makes it once a day whatever the
  lane's cadence, and a tick that dies half way is finished by the next.
- **Zalo gets it through Z2, unchanged**: the channel lane sends the title and
  the link, never the body (ADR 0013), and the title names only the date
  (QE-20: identifiers only; there is no product name or supplier in it).
- **From `REPORT_FROM_HOUR` Vietnam time**, so "today's samples" means a day's
  work; a sample evaluated after the notice went out is on the brief, live,
  and in the next day's count only if it is still waiting.
- **Nothing to report, nothing sent**: a workspace with no stage-1 group today
  gets no notice.

This is a system process, not a caller: like the follow-up sweep it acts per
workspace under a context with no roles and no scopes (`sweep_context`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from dw_kernel.ports import UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import PolicyOverridePort
from dw_supply_chain.action_duties import CaseDuty
from dw_supply_chain.application.follow_up_sweep import sweep_context
from dw_supply_chain.application.handlers import PRODUCT_CASE_READ, duty_scope, read_stage_one
from dw_supply_chain.application.ports import (
    FollowUpNotifierPort,
    ScopeHoldersPort,
    StageOneReadsPort,
    WorkspacesWithCasesPort,
)
from dw_supply_chain.domain.daily_brief import (
    VIETNAM,
    BriefGroup,
    BriefSignal,
    stage_one_groups,
)
from dw_supply_chain.domain.product_development_case import SampleResult
from dw_supply_chain.sla_policy import SupplyChainSLAPolicy

logger = logging.getLogger(__name__)

# QE-19 provisional: the report goes out from 17:00 Vietnam time.
REPORT_FROM_HOUR = 17

BRIEF_LINK = "/supply-chain/daily-brief"


def report_title(day: datetime) -> str:
    """Identifiers only (QE-20): the date, nothing a person typed."""
    return f"Báo cáo giai đoạn 1 ngày {day:%d/%m/%Y}"


def report_body(groups: list[BriefGroup]) -> str:
    """Counts per group, in the portal's words; the cases are on the brief."""
    totals: dict[tuple[BriefSignal, str | None], int] = {
        (group.signal, group.qualifier): group.total for group in groups
    }

    def total(signal: BriefSignal) -> int:
        return sum(n for (s, _), n in totals.items() if s is signal)

    def sampled(result: SampleResult) -> int:
        return totals.get((BriefSignal.SAMPLE_EVALUATED_TODAY, result.value), 0)

    return (
        f"Mẫu đã đánh giá hôm nay: {sampled(SampleResult.PASSED)} đạt, "
        f"{sampled(SampleResult.NEEDS_REVISION)} cần chỉnh sửa, "
        f"{sampled(SampleResult.REJECTED)} hủy. "
        f"Chờ BGĐ duyệt: {total(BriefSignal.PRODUCT_AWAITING_BOD)}. "
        f"Chờ trình ký: {total(BriefSignal.PRODUCT_AWAITING_SIGNOFF)}. "
        f"Quá hạn giai đoạn 1: {total(BriefSignal.PRODUCT_SLA_BREACHED)}. "
        "Chi tiết theo PIC trên bản tin hằng ngày."
    )


@dataclass(frozen=True, slots=True)
class ReportOutcome:
    sent: int = 0
    failed_workspaces: int = 0

    def __add__(self, other: ReportOutcome) -> ReportOutcome:
        return ReportOutcome(
            sent=self.sent + other.sent,
            failed_workspaces=self.failed_workspaces + other.failed_workspaces,
        )


@dataclass(frozen=True)
class SendStageOneReport:
    workspaces: WorkspacesWithCasesPort
    product_case_repo: StageOneReadsPort
    policy_override_repo: PolicyOverridePort
    platform_default_sla_policy: SupplyChainSLAPolicy
    holders: ScopeHoldersPort
    notifier: FollowUpNotifierPort
    clock: UtcClock

    async def run(self) -> ReportOutcome:
        local = self.clock.now().astimezone(VIETNAM)
        if local.hour < REPORT_FROM_HOUR:
            return ReportOutcome()
        total = ReportOutcome()
        for tenant_id, workspace_id in await self.workspaces.workspaces():
            try:
                total += await self.report_workspace(sweep_context(tenant_id, workspace_id))
            except Exception:
                # Broad on purpose, as the follow-up sweep: one workspace's bad
                # data must not stop every other one's report.
                logger.exception(
                    "stage-1 report failed for tenant %s workspace %s", tenant_id, workspace_id
                )
                total += ReportOutcome(failed_workspaces=1)
        return total

    async def report_workspace(self, context: AccessContext) -> ReportOutcome:
        snapshot = await read_stage_one(
            context,
            product_case_repo=self.product_case_repo,
            policy_override_repo=self.policy_override_repo,
            platform_default_policy=self.platform_default_sla_policy,
            clock=self.clock,
        )
        groups = stage_one_groups(snapshot)
        if not groups:
            return ReportOutcome()
        leads = set(
            await self.holders.holding(
                context, context.workspace_id, frozenset({duty_scope(CaseDuty.SUPPLY_LEAD)})
            )
        )
        readers = set(
            await self.holders.holding(
                context, context.workspace_id, frozenset({PRODUCT_CASE_READ})
            )
        )
        recipients = sorted(leads & readers)
        if not recipients:
            return ReportOutcome()
        day = self.clock.now().astimezone(VIETNAM)
        await self.notifier.deliver(
            context,
            recipients=recipients,
            source_key=f"supply_chain.stage_one_report:{context.workspace_id}:{day:%Y-%m-%d}",
            title=report_title(day),
            body=report_body(groups),
            link=BRIEF_LINK,
        )
        return ReportOutcome(sent=1)
