"""Closed follow-ups past their tenant's term are pruned (ticket P3).

A follow-up is history once closed, and the sweep opens one per case, kind and
episode for as long as the case lives: unbounded (failure-modes #6). This lane
bounds it. Per workspace with cases, under the sweep's own context (no roles,
no scopes, bound to one tenant and workspace), it resolves that tenant's
follow-up policy and asks the database to delete what closed longer ago than
`follow_up_retention_days`. Which rows may go is not decided here:
`supply_chain.prune_follow_ups` deletes only closed rows of the bound workspace
past the term, and nothing at all when no tenant is bound.

One workspace failing (an override that no longer validates, a dropped
connection) is logged and leaves the others pruned; the next pass retries.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import PolicyOverridePort
from dw_supply_chain.application.follow_up_sweep import sweep_context
from dw_supply_chain.application.handlers import resolve_follow_up_policy
from dw_supply_chain.application.ports import WorkspacesWithCasesPort
from dw_supply_chain.follow_up_policy import SupplyChainFollowUpPolicy, follow_up_retention_days

logger = logging.getLogger(__name__)


class ClosedFollowUpPrunePort(Protocol):
    async def prune_closed(self, context: AccessContext, *, older_than_days: int) -> int:
        """Deletes the closed follow-ups of `context`'s workspace that closed
        more than `older_than_days` ago; returns how many."""
        ...


@dataclass(frozen=True)
class PruneClosedFollowUps:
    """Satisfies the worker's `RetentionPrunePort`."""

    workspaces: WorkspacesWithCasesPort
    policy_override_repo: PolicyOverridePort
    platform_default_policy: SupplyChainFollowUpPolicy
    follow_ups: ClosedFollowUpPrunePort

    async def prune(self) -> None:
        for tenant_id, workspace_id in await self.workspaces.workspaces():
            context = sweep_context(tenant_id, workspace_id)
            try:
                policy = await resolve_follow_up_policy(
                    context, self.policy_override_repo, self.platform_default_policy
                )
                days = follow_up_retention_days(policy, self.platform_default_policy)
                gone = await self.follow_ups.prune_closed(context, older_than_days=days)
            except Exception:
                logger.exception(
                    "follow-up retention failed for workspace %s; the next pass retries",
                    workspace_id,
                )
                continue
            if gone:
                logger.info(
                    "follow-up retention: workspace=%s pruned=%d term_days=%d",
                    workspace_id,
                    gone,
                    days,
                )
