"""The follow-up sweep: turn due signals into handed-out work, and tell people.

Run by the worker on a cadence. For every tenant with cases it:

1. assesses the active cases with `assess_active_cases`, the one assessment
   the Attention Queue, the Control Tower and the daily brief share, under
   the tenant's own SLA policy and supplier-update cadence;
2. opens a follow-up for each due signal whose episode has none yet, stamped
   with the recipient scopes the tenant's follow-up policy names now;
3. resolves the open follow-ups whose signal is gone (an update arrived, the
   case moved on, a reminder became an escalation);
4. notifies the members of the case's workspace who hold a stamped scope,
   once per follow-up, and records that it did.

Every step is idempotent, so a sweep that dies half way is finished by the
next one: an episode opens once (the database's unique key), a delivery is
once per recipient (the inbox's), and "notified" is recorded only after the
delivery. A follow-up nobody holds a scope for stays unnotified, and is
delivered the first sweep after someone does.

This is a system process, not a caller: it acts under a context with no
roles and no scopes, bound to one tenant at a time, and reads nothing across
tenants except which tenants to visit.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import PolicyOverridePort
from dw_supply_chain.application.handlers import assess_active_cases, resolve_follow_up_policy
from dw_supply_chain.application.ports import (
    FollowUpDraft,
    FollowUpNotifierPort,
    FollowUpRepositoryPort,
    POCaseRepositoryPort,
    ScopeHoldersPort,
    SupplierUpdateRepositoryPort,
    TenantsWithCasesPort,
)
from dw_supply_chain.domain.follow_up import follow_up_message, follow_ups_due
from dw_supply_chain.follow_up_policy import SupplyChainFollowUpPolicy
from dw_supply_chain.sla_policy import SupplyChainSLAPolicy

logger = logging.getLogger(__name__)

# The sweep's own principal: nobody's inbox, holding no scope, so nothing it
# reads or writes is decided by who it is.
SWEEP_PRINCIPAL = uuid.UUID("5a1ef011-0000-4000-8000-5ca1ab1ef011")


def sweep_context(tenant_id: uuid.UUID, workspace_id: uuid.UUID) -> AccessContext:
    return AccessContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        principal_id=SWEEP_PRINCIPAL,
        roles=frozenset(),
        scopes=frozenset(),
        plan_id="system",
    )


@dataclass(frozen=True, slots=True)
class SweepOutcome:
    opened: int = 0
    resolved: int = 0
    notified: int = 0
    failed_tenants: int = 0

    def __add__(self, other: SweepOutcome) -> SweepOutcome:
        return SweepOutcome(
            opened=self.opened + other.opened,
            resolved=self.resolved + other.resolved,
            notified=self.notified + other.notified,
            failed_tenants=self.failed_tenants + other.failed_tenants,
        )


@dataclass(frozen=True)
class SweepFollowUps:
    tenants: TenantsWithCasesPort
    po_case_repo: POCaseRepositoryPort
    supplier_update_repo: SupplierUpdateRepositoryPort
    policy_override_repo: PolicyOverridePort
    platform_default_sla_policy: SupplyChainSLAPolicy
    platform_default_follow_up_policy: SupplyChainFollowUpPolicy
    follow_up_repo: FollowUpRepositoryPort
    holders: ScopeHoldersPort
    notifier: FollowUpNotifierPort
    ids: IdGenerator
    clock: UtcClock

    async def run(self) -> SweepOutcome:
        total = SweepOutcome()
        for tenant_id, workspace_id in await self.tenants.tenants():
            try:
                total += await self.sweep_tenant(sweep_context(tenant_id, workspace_id))
            except Exception:
                # Broad on purpose: one tenant's bad data must not stop every
                # other tenant's reminders. The next sweep retries it.
                logger.exception("follow-up sweep failed for tenant %s", tenant_id)
                total += SweepOutcome(failed_tenants=1)
        return total

    async def sweep_tenant(self, context: AccessContext) -> SweepOutcome:
        healths = await assess_active_cases(
            context,
            po_case_repo=self.po_case_repo,
            supplier_update_repo=self.supplier_update_repo,
            policy_override_repo=self.policy_override_repo,
            platform_default_policy=self.platform_default_sla_policy,
            clock=self.clock,
        )
        due = [item for health in healths for item in follow_ups_due(health)]
        policy = await resolve_follow_up_policy(
            context, self.policy_override_repo, self.platform_default_follow_up_policy
        )

        already_open = await self.follow_up_repo.list_open(context)
        open_keys = {record.key for record in already_open}
        drafts = [
            FollowUpDraft(
                id=self.ids.new_uuid(),
                due=item,
                recipient_scopes=policy.recipient_scopes(item.kind),
            )
            for item in due
            if item.key not in open_keys
        ]
        opened = await self.follow_up_repo.open(context, drafts)

        due_keys = {item.key for item in due}
        stale = [record.id for record in already_open if record.key not in due_keys]
        await self.follow_up_repo.resolve(context, stale)

        notified = 0
        for record in await self.follow_up_repo.list_open(context):
            if record.notified_at is not None:
                continue
            case_context = sweep_context(context.tenant_id, record.workspace_id)
            recipients = await self.holders.holding(
                case_context, record.workspace_id, record.recipient_scopes
            )
            if not recipients:
                # Nobody holds a scope it was handed to yet; it stays listed
                # and unnotified, and is delivered once somebody does.
                continue
            message = follow_up_message(
                kind=record.kind,
                po_reference=record.po_reference,
                supplier_name=record.supplier_name,
                days=record.days,
                limit_days=record.limit_days,
                milestone=record.milestone,
            )
            await self.notifier.deliver(
                case_context,
                recipients=recipients,
                source_key=f"supply_chain.follow_up:{record.id}",
                title=message.title,
                body=message.body,
                link=f"/supply-chain/po-cases/{record.po_case_id}",
            )
            await self.follow_up_repo.mark_notified(context, record.id)
            notified += 1

        return SweepOutcome(opened=opened, resolved=len(stale), notified=notified)
