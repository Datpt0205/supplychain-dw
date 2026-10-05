"""Supply Chain's lanes: the follow-up sweep, and the case-document orphan sweep.

The sweep (`dw_supply_chain.application.follow_up_sweep`) opens follow-ups for
due reminders, escalations and SLA breaches, resolves the ones whose signal is
gone, and notifies the people who must act. It is idempotent end to end, so
this lane only has to call it; a failed tick is finished by the next one.

Built here, at this process's composition root: the concrete adapters are
imported only here, the policies are the shipped files the API also loads
(named once in `dw_supply_chain.policy_files`).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.adapters.persistence.notifications import SqlNotificationRepository
from dw_platform.adapters.persistence.policy_overrides import SqlPolicyOverrideRepository
from dw_platform.adapters.persistence.scope_holders import SqlScopeHolders
from dw_supply_chain.adapters.persistence.case_document_repository import (
    SqlCaseDocumentRepository,
)
from dw_supply_chain.adapters.persistence.follow_up_repository import (
    SqlFollowUpRepository,
    SqlTenantsWithCases,
)
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.adapters.persistence.supplier_update_repository import (
    SqlSupplierUpdateRepository,
)
from dw_supply_chain.application.document_orphan_sweep import SweepOrphanDocuments
from dw_supply_chain.application.follow_up_sweep import SweepFollowUps
from dw_supply_chain.application.ports import CaseDocumentObjectListingPort
from dw_supply_chain.follow_up_policy import load_supply_chain_follow_up_policy
from dw_supply_chain.policy_files import FOLLOW_UP_POLICY_FILE, SLA_POLICY_FILE
from dw_supply_chain.sla_policy import load_supply_chain_sla_policy

logger = logging.getLogger(__name__)


def build_follow_up_sweep(
    sessions: async_sessionmaker[AsyncSession],
    *,
    policies_dir: Path,
    ids: IdGenerator,
    clock: UtcClock,
) -> SweepFollowUps:
    return SweepFollowUps(
        tenants=SqlTenantsWithCases(sessions),
        po_case_repo=SqlPOCaseRepository(sessions),
        supplier_update_repo=SqlSupplierUpdateRepository(sessions),
        policy_override_repo=SqlPolicyOverrideRepository(sessions),
        platform_default_sla_policy=load_supply_chain_sla_policy(policies_dir / SLA_POLICY_FILE),
        platform_default_follow_up_policy=load_supply_chain_follow_up_policy(
            policies_dir / FOLLOW_UP_POLICY_FILE
        ),
        follow_up_repo=SqlFollowUpRepository(sessions),
        holders=SqlScopeHolders(sessions),
        notifier=SqlNotificationRepository(sessions),
        ids=ids,
        clock=clock,
    )


def build_document_orphan_sweep(
    sessions: async_sessionmaker[AsyncSession],
    objects: CaseDocumentObjectListingPort,
    *,
    clock: UtcClock,
) -> SweepOrphanDocuments:
    """Satisfies `RetentionPrunePort`; registered as
    `supply_chain_document_orphans` on the retention cadence."""
    return SweepOrphanDocuments(
        objects=objects, keys=SqlCaseDocumentRepository(sessions), clock=clock
    )


def build_follow_up_consumer(sweep: SweepFollowUps) -> Callable[[], Awaitable[None]]:
    async def consume() -> None:
        outcome = await sweep.run()
        if outcome.opened or outcome.resolved or outcome.notified or outcome.failed_tenants:
            logger.info(
                "follow-up sweep: opened=%d resolved=%d notified=%d failed_tenants=%d",
                outcome.opened,
                outcome.resolved,
                outcome.notified,
                outcome.failed_tenants,
            )

    return consume
