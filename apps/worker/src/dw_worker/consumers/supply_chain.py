"""Supply Chain's lanes: the follow-up sweep, the case-document orphan sweep,
and the product approval reconcile (BGĐ's review, the step-9 sign-off).

The sweep (`dw_supply_chain.application.follow_up_sweep`) opens follow-ups for
due reminders, escalations and SLA breaches, resolves the ones whose signal is
gone, and notifies the people who must act. It is idempotent end to end, so
this lane only has to call it; a failed tick is finished by the next one.

The reconcile (`dw_supply_chain.application.product_reviews.
ReconcileProductApprovals`) raises the approval a product case waits on with
none: BGĐ's review in `pending_bod_review`, the step-9 sign-off in
`pending_signoff` (a start the step command could not make, or a case that got
there before the approval existed), and tells the signers of a sign-off's
later steps. It starts runs, so this process hosts both graphs on its own
runner, over the same tables, checkpointer and plan allowance the API's runner
uses; a decision then resumes the run in the API, which hosts the same graphs.

A decision sent from Zalo (zalo-channel ticket 05) resumes a review on the
same runner, so `register_product_approvals` gives the worker's approval flow
what `wiring.py` gives the API's: the strict prefix and the case-version port.

The Zalo proposal command (zalo-channel ticket 04, Z4b) is built here too: the
chat's "Đồng ý" creates a product case through the same `ProposeProductCase`
the API's route calls, over the same tables and the same duty policy file.

Built here, at this process's composition root: the concrete adapters are
imported only here, the policies are the shipped files the API also loads
(named once in `dw_supply_chain.policy_files`).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_agent_runtime.adapters.checkpoint import SqlAlchemyCheckpointSaver
from dw_agent_runtime.adapters.langgraph_runner import LangGraphWorkflowRunner
from dw_agent_runtime.adapters.run_store import SqlWorkerRunStore
from dw_agent_runtime.adapters.spend_guard import SqlSpendGuardStore
from dw_agent_runtime.approval_flow import ApproveAndResumeService
from dw_agent_runtime.autonomy import AutonomyApprovalPolicy
from dw_agent_runtime.model.budget import RunBudgetLedger
from dw_agent_runtime.model.run_policy import load_worker_run_policy
from dw_agent_runtime.ports import ModelGateway
from dw_agent_runtime.registry import GraphRegistry, WorkerRegistry
from dw_kernel.ports import IdGenerator, UtcClock
from dw_observability.telemetry import TelemetryPort
from dw_platform.adapters.persistence.approval_queries import SqlPendingApprovalQuery
from dw_platform.adapters.persistence.notifications import SqlNotificationRepository
from dw_platform.adapters.persistence.policy_overrides import SqlPolicyOverrideRepository
from dw_platform.adapters.persistence.scope_holders import SqlScopeHolders
from dw_platform.adapters.persistence.tenant_plans import SqlTenantPlans
from dw_platform.adapters.persistence.uow import SqlPlatformUnitOfWorkFactory
from dw_platform.adapters.persistence.workspace_names import SqlWorkspaceNames
from dw_platform.application.approval_codes import ApprovalSubjectVersions
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.entitlement import DEFAULT_PLANS, PlanEntitlementService
from dw_supply_chain.adapters.persistence.case_document_repository import (
    SqlCaseDocumentRepository,
)
from dw_supply_chain.adapters.persistence.follow_up_repository import (
    SqlFollowUpRepository,
    SqlTenantsWithCases,
)
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.adapters.persistence.product_case_repository import (
    SqlProductCaseRepository,
    SqlWorkspacesAwaitingApproval,
)
from dw_supply_chain.adapters.persistence.proposal_draft_repository import (
    SqlProposalDraftRepository,
    SqlProposalDraftRetention,
)
from dw_supply_chain.adapters.persistence.supplier_update_repository import (
    SqlSupplierUpdateRepository,
)
from dw_supply_chain.application.approval_subject import ProductCaseApprovalSubject
from dw_supply_chain.application.document_orphan_sweep import SweepOrphanDocuments
from dw_supply_chain.application.follow_up_sweep import SweepFollowUps
from dw_supply_chain.application.ports import CaseDocumentObjectListingPort
from dw_supply_chain.application.product_cases import ProposeProductCase
from dw_supply_chain.application.product_reviews import (
    EnsureProductApproval,
    ReconcileProductApprovals,
)
from dw_supply_chain.follow_up_policy import load_supply_chain_follow_up_policy
from dw_supply_chain.policy_files import (
    ADVANCE_PRODUCT_CASE_WORKER_FILE,
    FOLLOW_UP_POLICY_FILE,
    PRODUCT_ACTION_DUTIES_POLICY_FILE,
    PRODUCT_APPROVALS_POLICY_FILE,
    PRODUCT_SIGNOFF_WORKER_FILE,
    SLA_POLICY_FILE,
)
from dw_supply_chain.presentation.zalo_proposal import ZaloProposalCommand
from dw_supply_chain.product_action_duties import load_supply_chain_product_action_duties
from dw_supply_chain.product_approvals import load_supply_chain_product_approvals
from dw_supply_chain.sla_policy import load_supply_chain_sla_policy
from dw_supply_chain.workflows import advance_product_case_graph as product_review_graph
from dw_supply_chain.workflows import product_signoff_graph

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


def build_product_review_runner(
    sessions: async_sessionmaker[AsyncSession],
    *,
    configs_dir: Path,
    ids: IdGenerator,
    clock: UtcClock,
    telemetry: TelemetryPort,
    release_manifest_ref: str | None,
) -> LangGraphWorkflowRunner:
    """This process's runner: the review and sign-off graphs registered once,
    their workers loaded from the shipped files, the run store's staleness from the shipped
    run policy, the plan catalogue the API's allowance reads. The reconcile
    lane starts reviews on it and a decision sent from Zalo (ticket 05)
    resumes them on it."""
    product_cases = SqlProductCaseRepository(sessions)
    graphs = GraphRegistry()
    graphs.register(
        product_review_graph.WORKER_ID,
        product_review_graph.GRAPH_VERSION,
        lambda: product_review_graph.build_advance_product_case_graph(product_cases, ids, clock),
    )
    graphs.register(
        product_signoff_graph.WORKER_ID,
        product_signoff_graph.GRAPH_VERSION,
        lambda: product_signoff_graph.build_product_signoff_graph(product_cases, ids, clock),
    )
    workers = WorkerRegistry(graph_registry=graphs)
    workers.load_file(configs_dir / "workers" / ADVANCE_PRODUCT_CASE_WORKER_FILE)
    workers.load_file(configs_dir / "workers" / PRODUCT_SIGNOFF_WORKER_FILE)
    run_policy = load_worker_run_policy(configs_dir / "policies" / "worker_runs@1.0.0.yaml")
    return LangGraphWorkflowRunner(
        worker_registry=workers,
        graph_registry=graphs,
        checkpoint_saver=SqlAlchemyCheckpointSaver(sessions),
        run_store=SqlWorkerRunStore(
            sessions, stale_run_after_seconds=run_policy.stale_run_after_seconds
        ),
        uow_factory=SqlPlatformUnitOfWorkFactory(sessions),
        clock=clock,
        id_generator=ids,
        allowance=PlanEntitlementService(DEFAULT_PLANS),
        budget=RunBudgetLedger(),
        approval_policy=AutonomyApprovalPolicy(),
        release_manifest_ref=release_manifest_ref,
        telemetry=telemetry,
        spend_store=SqlSpendGuardStore(session_factory=sessions),
    )


def build_product_review_reconcile(
    sessions: async_sessionmaker[AsyncSession],
    *,
    runner: LangGraphWorkflowRunner,
    configs_dir: Path,
    ids: IdGenerator,
) -> ReconcileProductApprovals:
    """The reconcile lane, starting reviews on `runner`
    (`build_product_review_runner`)."""
    product_cases = SqlProductCaseRepository(sessions)
    # Only `raised_by_payload` is asked here, which no audience narrows; the
    # authorization is the query's constructor argument all the same.
    approvals = SqlPendingApprovalQuery(sessions, ScopeAuthorizationService())
    return ReconcileProductApprovals(
        workspaces=SqlWorkspacesAwaitingApproval(sessions),
        cases=product_cases,
        plans=SqlTenantPlans(sessions),
        reviews=EnsureProductApproval(
            runner=runner,
            approvals=approvals,
            holders=SqlScopeHolders(sessions),
            notifier=SqlNotificationRepository(sessions),
            policy_override_repo=SqlPolicyOverrideRepository(sessions),
            platform_default_approvals=load_supply_chain_product_approvals(
                configs_dir / "policies" / PRODUCT_APPROVALS_POLICY_FILE
            ),
            ids=ids,
        ),
    )


def register_product_approvals(
    flow: ApproveAndResumeService,
    subjects: ApprovalSubjectVersions,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """What this context adds to the worker's approval flow, as `wiring.py`
    adds it to the API's: its prefix is strict (the requester cannot decide,
    a decision needs a comment), and the case's version is the subject a
    decision on Zalo must still find (ADR 0014)."""
    flow.strict_approval_prefixes = flow.strict_approval_prefixes | frozenset(
        {product_review_graph.APPROVAL_TYPE_PREFIX}
    )
    subjects.register(
        product_review_graph.APPROVAL_TYPE_PREFIX,
        ProductCaseApprovalSubject(SqlProductCaseRepository(sessions)),
    )


def build_zalo_proposal_command(
    sessions: async_sessionmaker[AsyncSession],
    *,
    configs_dir: Path,
    gateway: ModelGateway,
    ids: IdGenerator,
    clock: UtcClock,
    web_url: str,
) -> ZaloProposalCommand:
    """The chat proposal command. `gateway` is the process's one-call gateway
    (`ModelStack.one_call`), so the plan's daily allowance is checked before
    each model call and the per-run ledger entry freed after it."""
    return ZaloProposalCommand(
        propose=ProposeProductCase(
            repo=SqlProductCaseRepository(sessions),
            authz=ScopeAuthorizationService(),
            policy_override_repo=SqlPolicyOverrideRepository(sessions),
            platform_default_duties=load_supply_chain_product_action_duties(
                configs_dir / "policies" / PRODUCT_ACTION_DUTIES_POLICY_FILE
            ),
            ids=ids,
            clock=clock,
        ),
        drafts=SqlProposalDraftRepository(sessions),
        gateway=gateway,
        workspaces=SqlWorkspaceNames(sessions),
        ids=ids,
        clock=clock,
        web_url=web_url,
    )


def build_proposal_draft_retention(
    sessions: async_sessionmaker[AsyncSession],
) -> SqlProposalDraftRetention:
    """Satisfies `RetentionPrunePort`; registered as
    `supply_chain_proposal_drafts_retention` on the retention cadence."""
    return SqlProposalDraftRetention(session_factory=sessions)


def build_product_review_reconcile_consumer(
    lane: ReconcileProductApprovals,
) -> Callable[[], Awaitable[None]]:
    async def consume() -> None:
        outcome = await lane.run()
        if outcome.attempted or outcome.failed:
            logger.info(
                "product approval reconcile: attempted=%d raised=%d failed=%d",
                outcome.attempted,
                outcome.raised,
                outcome.failed,
            )

    return consume


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
