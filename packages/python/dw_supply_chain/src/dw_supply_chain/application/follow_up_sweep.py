"""The follow-up sweep: turn due signals into handed-out work, and tell people.

Run by the worker on a cadence. For every workspace with cases it:

1. assesses the workspace's active PO cases with `assess_active_cases`, the
   one assessment the Attention Queue, the Control Tower and the daily brief
   share, and its active product-development cases with `evaluate_product_sla`
   (stage-1 ticket 06), under the tenant's own SLA policy, each case under its
   stamped Category;
2. opens a follow-up for each due signal whose episode has none yet, stamped
   with the recipient scopes the tenant's follow-up policy names now and,
   where the policy routes the kind to `pic`, the case's PIC if they are a
   member of the case's workspace now;
3. resolves the open follow-ups whose signal is gone (an update arrived, the
   case moved on, a reminder became an escalation);
4. notifies the members of the case's workspace who hold a stamped scope, and
   the stamped PIC if still a member, once per follow-up, and records that it
   did.

Every step is idempotent, so a sweep that dies half way is finished by the
next one: an episode opens once (the database's unique key), a delivery is
once per recipient (the inbox's), and "notified" is recorded only after the
delivery. A follow-up nobody holds a scope for stays unnotified, and is
delivered the first sweep after someone does.

Per workspace, not per tenant: PO cases, product cases and follow-ups are
narrowed by workspace in the database, so one workspace's sweep reads nothing of another,
and a failure in one leaves the others swept.

This is a system process, not a caller: it acts under a context with no
roles and no scopes, bound to one tenant and workspace at a time, and reads
nothing across tenants except which workspaces to visit.

It audits its own changes as itself (platform ADR 0011): each follow-up it
opens or resolves writes `supply_chain.follow_up.opened` / `.resolved` to
`platform.audit_events` in the same transaction, with the actor
`system_actor(FOLLOW_UP_SWEEP_LANE)` and `details.actor` `system:<lane>`, never
a person's id. Notifying is not audited here: the inbox and the channel
outbox record each delivery.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import PolicyOverridePort
from dw_platform.domain.audit import AuditEvent, lane_audit_event
from dw_supply_chain.application.handlers import (
    assess_active_cases,
    assess_active_product_cases,
    po_case_link,
    product_case_link,
    resolve_follow_up_policy,
)
from dw_supply_chain.application.ports import (
    ActiveProductCasesPort,
    FollowUpDraft,
    FollowUpNotifierPort,
    FollowUpRecord,
    FollowUpRepositoryPort,
    POCaseRepositoryPort,
    ScopeHoldersPort,
    SupplierUpdateRepositoryPort,
    WorkspaceMembersPort,
    WorkspacesWithCasesPort,
)
from dw_supply_chain.domain.case_document import CaseKind
from dw_supply_chain.domain.follow_up import (
    FollowUpDue,
    follow_up_message,
    follow_ups_due,
    product_follow_ups_due,
)
from dw_supply_chain.follow_up_policy import SupplyChainFollowUpPolicy
from dw_supply_chain.sla_policy import SupplyChainSLAPolicy

logger = logging.getLogger(__name__)

# The sweep's own principal: nobody's inbox, holding no scope, so nothing it
# reads or writes is decided by who it is.
SWEEP_PRINCIPAL = uuid.UUID("5a1ef011-0000-4000-8000-5ca1ab1ef011")

# The worker registry name the sweep runs under, and so its audit actor
# (`system_actor`): renaming the lane changes who the log says acted.
FOLLOW_UP_SWEEP_LANE = "supply_chain_follow_ups"
FOLLOW_UP_OPENED = "supply_chain.follow_up.opened"
FOLLOW_UP_RESOLVED = "supply_chain.follow_up.resolved"
_FOLLOW_UP_RESOURCE = "follow_up"


def sweep_context(tenant_id: uuid.UUID, workspace_id: uuid.UUID) -> AccessContext:
    return AccessContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        principal_id=SWEEP_PRINCIPAL,
        roles=frozenset(),
        scopes=frozenset(),
        plan_id="system",
    )


def _case_link(record: FollowUpRecord) -> str:
    if record.case_kind is CaseKind.PRODUCT:
        return product_case_link(record.case_id)
    return po_case_link(record.case_id)


@dataclass(frozen=True, slots=True)
class SweepOutcome:
    opened: int = 0
    resolved: int = 0
    notified: int = 0
    failed_workspaces: int = 0

    def __add__(self, other: SweepOutcome) -> SweepOutcome:
        return SweepOutcome(
            opened=self.opened + other.opened,
            resolved=self.resolved + other.resolved,
            notified=self.notified + other.notified,
            failed_workspaces=self.failed_workspaces + other.failed_workspaces,
        )


@dataclass(frozen=True)
class SweepFollowUps:
    workspaces: WorkspacesWithCasesPort
    po_case_repo: POCaseRepositoryPort
    product_case_repo: ActiveProductCasesPort
    supplier_update_repo: SupplierUpdateRepositoryPort
    policy_override_repo: PolicyOverridePort
    platform_default_sla_policy: SupplyChainSLAPolicy
    platform_default_follow_up_policy: SupplyChainFollowUpPolicy
    follow_up_repo: FollowUpRepositoryPort
    holders: ScopeHoldersPort
    members: WorkspaceMembersPort
    notifier: FollowUpNotifierPort
    ids: IdGenerator
    clock: UtcClock

    async def run(self) -> SweepOutcome:
        total = SweepOutcome()
        for tenant_id, workspace_id in await self.workspaces.workspaces():
            try:
                total += await self.sweep_workspace(sweep_context(tenant_id, workspace_id))
            except Exception:
                # Broad on purpose: one workspace's bad data must not stop
                # every other one's reminders. The next sweep retries it.
                logger.exception(
                    "follow-up sweep failed for tenant %s workspace %s", tenant_id, workspace_id
                )
                total += SweepOutcome(failed_workspaces=1)
        return total

    async def _due(self, context: AccessContext) -> list[FollowUpDue]:
        """Every signal due in the context's workspace, PO and product cases."""
        healths = await assess_active_cases(
            context,
            po_case_repo=self.po_case_repo,
            supplier_update_repo=self.supplier_update_repo,
            policy_override_repo=self.policy_override_repo,
            platform_default_policy=self.platform_default_sla_policy,
            clock=self.clock,
        )
        due = [
            item
            for health in healths
            # RLS already reads only this workspace's PO cases (`62cdcf3bf2d2`);
            # kept as a second check, so a widened policy cannot double-notify.
            if health.case.workspace_id.value == context.workspace_id
            for item in follow_ups_due(health)
        ]
        for product in await assess_active_product_cases(
            context,
            product_case_repo=self.product_case_repo,
            policy_override_repo=self.policy_override_repo,
            platform_default_policy=self.platform_default_sla_policy,
            clock=self.clock,
        ):
            due.extend(product_follow_ups_due(product.case, product.sla))
        return due

    async def sweep_workspace(self, context: AccessContext) -> SweepOutcome:
        due = await self._due(context)
        policy = await resolve_follow_up_policy(
            context, self.policy_override_repo, self.platform_default_follow_up_policy
        )

        already_open = await self.follow_up_repo.list_open(context)
        open_keys = {record.key for record in already_open}
        new = [item for item in due if item.key not in open_keys]
        # Whom a `pic` recipient may be: PICs still in the workspace, now.
        present = await self.members.members(
            context.tenant_id,
            context.workspace_id,
            frozenset(
                item.subject.pic_user_id
                for item in new
                if item.subject.pic_user_id is not None and policy.routes_to_pic(item.kind)
            ),
        )
        drafts = [
            FollowUpDraft(
                id=self.ids.new_uuid(),
                due=item,
                recipient_scopes=policy.recipient_scopes(item.kind),
                recipient_user_id=(
                    item.subject.pic_user_id
                    if policy.routes_to_pic(item.kind) and item.subject.pic_user_id in present
                    else None
                ),
            )
            for item in new
        ]
        opened = await self.follow_up_repo.open(
            context,
            drafts,
            audit=lambda draft: self._audit(
                context,
                FOLLOW_UP_OPENED,
                follow_up_id=draft.id,
                workspace_id=draft.due.subject.workspace_id,
                details={
                    "case_kind": draft.due.subject.case_kind.value,
                    "case_id": str(draft.due.subject.case_id),
                    "kind": draft.due.kind.value,
                    "episode": draft.due.episode,
                    "recipient_scopes": sorted(draft.recipient_scopes),
                    "pic_routed": draft.recipient_user_id is not None,
                },
            ),
        )

        due_keys = {item.key for item in due}
        stale = [record for record in already_open if record.key not in due_keys]
        resolved = await self.follow_up_repo.resolve(
            context,
            stale,
            audit=lambda record: self._audit(
                context,
                FOLLOW_UP_RESOLVED,
                follow_up_id=record.id,
                workspace_id=record.workspace_id,
                details={
                    "case_kind": record.case_kind.value,
                    "case_id": str(record.case_id),
                    "kind": record.kind.value,
                    "episode": record.episode,
                    "reason": "signal_gone",
                },
            ),
        )

        notified = 0
        for record in await self.follow_up_repo.list_open(context):
            if record.notified_at is not None:
                continue
            recipients = set(
                await self.holders.holding(
                    context.tenant_id, record.workspace_id, record.recipient_scopes
                )
            )
            if record.recipient_user_id is not None:
                # Stamped when it opened; told only while still a member
                # (the channel lane checks again before anything leaves).
                recipients |= await self.members.members(
                    context.tenant_id, record.workspace_id, frozenset({record.recipient_user_id})
                )
            if not recipients:
                # Nobody holds a scope it was handed to yet; it stays listed
                # and unnotified, and is delivered once somebody does.
                continue
            message = follow_up_message(
                kind=record.kind,
                case_kind=record.case_kind,
                reference=record.reference,
                supplier_name=record.supplier_name,
                days=record.days,
                limit_days=record.limit_days,
                milestone=record.milestone,
            )
            await self.notifier.deliver(
                context,
                recipients=sorted(recipients),
                source_key=f"supply_chain.follow_up:{record.id}",
                title=message.title,
                body=message.body,
                link=_case_link(record),
            )
            await self.follow_up_repo.mark_notified(context, record.id)
            notified += 1

        return SweepOutcome(opened=opened, resolved=resolved, notified=notified)

    def _audit(
        self,
        context: AccessContext,
        action: str,
        *,
        follow_up_id: uuid.UUID,
        workspace_id: uuid.UUID,
        details: dict[str, object],
    ) -> AuditEvent:
        return lane_audit_event(
            lane=FOLLOW_UP_SWEEP_LANE,
            event_id=self.ids.new_uuid(),
            tenant_id=TenantId(context.tenant_id),
            workspace_id=WorkspaceId(workspace_id),
            action=action,
            resource_type=_FOLLOW_UP_RESOURCE,
            resource_id=str(follow_up_id),
            occurred_at=self.clock.now(),
            details=details,
        )
