"""Supply Chain's lanes: the follow-up sweep, the stage-1 daily report, the
case-document orphan sweep, and the product approval reconcile (BGĐ's review,
the step-9 sign-off).

The sweep (`dw_supply_chain.application.follow_up_sweep`) opens follow-ups for
due reminders, escalations and SLA breaches, resolves the ones whose signal is
gone, and notifies the people who must act. It is idempotent end to end, so
this lane only has to call it; a failed tick is finished by the next one.

The daily report (`dw_supply_chain.application.daily_report`, stage-1 ticket
08, QE-19 provisional) tells each TP Cung ứng of a workspace, once a day from
17:00 Vietnam time, what the brief's stage-1 groups hold; the notification
inbox's once-per-key delivery makes the lane's cadence irrelevant to that.

The reconcile (`dw_supply_chain.application.product_reviews.
ReconcileProductApprovals`) raises the approval a product case waits on with
none: BGĐ's review in `pending_bod_review`, the step-9 sign-off in
`pending_signoff` (a start the step command could not make, or a case that got
there before the approval existed), and tells the signers of a sign-off's
later steps. It starts runs, so this process hosts both graphs on its own
runner, over the same tables, checkpointer and plan allowance the API's runner
uses; a decision then resumes the run in the API, which hosts the same graphs.

A decision sent from Zalo (zalo-channel ticket 05) resumes a review on the
same runner, so `PRODUCT_STRICT_APPROVAL_PREFIXES` and `product_approval_subjects`
give the worker's approval flow what `wiring.py` gives the API's: the strict
prefix and the case-version port.

The Zalo proposal command (zalo-channel ticket 04, Z4b) is built here too: the
chat's "Đồng ý" creates a product case through the same `ProposeProductCase`
the API's route calls, over the same tables and the same duty policy file.
So is the read-only question command (ticket 06, Z6): the same `AnswerCaseQuery`,
`ListPOCases`, `ListProductCases` and `ListProductCategories` the API's
`POST /case-query` runs.

Built here, at this process's composition root: the concrete adapters are
imported only here, the policies are the shipped files the API also loads
(named once in `dw_supply_chain.policy_files`).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_agent_runtime.adapters.checkpoint import SqlAlchemyCheckpointSaver
from dw_agent_runtime.adapters.langgraph_runner import LangGraphWorkflowRunner
from dw_agent_runtime.adapters.run_store import SqlWorkerRunStore
from dw_agent_runtime.adapters.spend_guard import SqlSpendGuardStore
from dw_agent_runtime.autonomy import AutonomyApprovalPolicy
from dw_agent_runtime.model.budget import RunBudgetLedger
from dw_agent_runtime.model.run_policy import load_worker_run_policy
from dw_agent_runtime.ports import ModelGateway
from dw_agent_runtime.registry import GraphRegistry, WorkerRegistry
from dw_kernel.ports import IdGenerator, UtcClock
from dw_knowledge.adapters.office_parsers import InProcessDocumentParser
from dw_observability.telemetry import TelemetryPort
from dw_platform.adapters.persistence.approval_queries import SqlPendingApprovalQuery
from dw_platform.adapters.persistence.directory import SqlWorkspaceDirectory
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
from dw_supply_chain.adapters.persistence.document_extraction_repository import (
    SqlDocumentExtractionRepository,
    SqlExtractionQueue,
)
from dw_supply_chain.adapters.persistence.follow_up_repository import (
    SqlFollowUpRepository,
    SqlWorkspacesWithCases,
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
from dw_supply_chain.adapters.storage.minio_case_documents import MinioCaseDocumentStorage
from dw_supply_chain.application.approval_subject import ProductCaseApprovalSubject
from dw_supply_chain.application.case_query import AnswerCaseQuery
from dw_supply_chain.application.daily_report import SendStageOneReport
from dw_supply_chain.application.document_extraction import (
    DocumentText,
    ExtractDocuments,
)
from dw_supply_chain.application.document_orphan_sweep import SweepOrphanDocuments
from dw_supply_chain.application.follow_up_retention import PruneClosedFollowUps
from dw_supply_chain.application.follow_up_sweep import SweepFollowUps
from dw_supply_chain.application.handlers import ListPOCases, ListProductCategories
from dw_supply_chain.application.ports import CaseDocumentObjectListingPort
from dw_supply_chain.application.product_cases import ListProductCases, ProposeProductCase
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
from dw_supply_chain.presentation.zalo_case_query import ZaloCaseQueryCommand
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
        workspaces=SqlWorkspacesWithCases(sessions),
        po_case_repo=SqlPOCaseRepository(sessions),
        product_case_repo=SqlProductCaseRepository(sessions),
        supplier_update_repo=SqlSupplierUpdateRepository(sessions),
        policy_override_repo=SqlPolicyOverrideRepository(sessions),
        platform_default_sla_policy=load_supply_chain_sla_policy(policies_dir / SLA_POLICY_FILE),
        platform_default_follow_up_policy=load_supply_chain_follow_up_policy(
            policies_dir / FOLLOW_UP_POLICY_FILE
        ),
        follow_up_repo=SqlFollowUpRepository(sessions),
        holders=SqlScopeHolders(sessions),
        members=SqlScopeHolders(sessions),
        notifier=SqlNotificationRepository(sessions),
        ids=ids,
        clock=clock,
    )


def build_stage_one_report(
    sessions: async_sessionmaker[AsyncSession], *, policies_dir: Path, clock: UtcClock
) -> SendStageOneReport:
    return SendStageOneReport(
        workspaces=SqlWorkspacesWithCases(sessions),
        product_case_repo=SqlProductCaseRepository(sessions),
        policy_override_repo=SqlPolicyOverrideRepository(sessions),
        platform_default_sla_policy=load_supply_chain_sla_policy(policies_dir / SLA_POLICY_FILE),
        holders=SqlScopeHolders(sessions),
        notifier=SqlNotificationRepository(sessions),
        clock=clock,
    )


def build_stage_one_report_consumer(lane: SendStageOneReport) -> Callable[[], Awaitable[None]]:
    async def consume() -> None:
        outcome = await lane.run()
        if outcome.sent or outcome.failed_workspaces:
            logger.info(
                "stage-1 report: sent=%d failed_workspaces=%d",
                outcome.sent,
                outcome.failed_workspaces,
            )

    return consume


@dataclass(frozen=True)
class InProcessDocumentText:
    """`DocumentTextPort` over `dw_knowledge`'s in-process readers: PDF text
    layers, DOCX, XLSX, EML. Nothing here calls a model, so a file's
    identifiers are masked before any model sees its text."""

    parser: InProcessDocumentParser = field(default_factory=InProcessDocumentParser)

    def supports(self, content_type: str) -> bool:
        return self.parser.supports(content_type, "")

    async def text_of(self, data: bytes, content_type: str, filename: str) -> DocumentText:
        parsed = await self.parser.parse(data, content_type, filename)
        return DocumentText(text=parsed.text, warnings=parsed.warnings)


def build_document_extraction(
    sessions: async_sessionmaker[AsyncSession],
    storage: MinioCaseDocumentStorage,
    *,
    gateway: ModelGateway,
    model_profile: str,
    ids: IdGenerator,
    clock: UtcClock,
) -> ExtractDocuments:
    """The extraction lane (ticket ai-automation/02). `gateway` is the
    process's one-call gateway (`ModelStack.one_call`): the plan's daily
    allowance is checked before each call and the call's spend recorded."""
    return ExtractDocuments(
        queue=SqlExtractionQueue(sessions),
        documents=SqlCaseDocumentRepository(sessions),
        storage=storage,
        text=InProcessDocumentText(),
        gateway=gateway,
        extractions=SqlDocumentExtractionRepository(sessions),
        plans=SqlTenantPlans(sessions),
        ids=ids,
        clock=clock,
        model_profile=model_profile,
    )


def build_document_extraction_consumer(lane: ExtractDocuments) -> Callable[[], Awaitable[None]]:
    async def consume() -> None:
        outcome = await lane.run_once()
        if outcome.by_status or outcome.refused_before_reading or outcome.deferred:
            logger.info(
                "document extraction: %s refused_before_reading=%d deferred=%d",
                {status.value: n for status, n in outcome.by_status.items()},
                outcome.refused_before_reading,
                outcome.deferred,
            )

    return consume


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


# What this context adds to the worker's approval flow, as `wiring.py` adds it
# to the API's: its prefix is strict (the requester cannot decide, a decision
# needs a comment). Passed to `build_channel_decision_command`.
PRODUCT_STRICT_APPROVAL_PREFIXES = frozenset({product_review_graph.APPROVAL_TYPE_PREFIX})


def product_approval_subjects(
    sessions: async_sessionmaker[AsyncSession],
) -> ApprovalSubjectVersions:
    """The case's version as the subject a decision on Zalo must still find
    (ADR 0007), registered under this context's prefix."""
    subjects = ApprovalSubjectVersions()
    subjects.register(
        product_review_graph.APPROVAL_TYPE_PREFIX,
        ProductCaseApprovalSubject(SqlProductCaseRepository(sessions)),
    )
    return subjects


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
            # The Category list a chat proposal is resolved against and
            # `propose` checks (ticket 06): the same file the API loads.
            platform_default_sla_policy=load_supply_chain_sla_policy(
                configs_dir / "policies" / SLA_POLICY_FILE
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


def build_zalo_case_query_command(
    sessions: async_sessionmaker[AsyncSession],
    *,
    configs_dir: Path,
    gateway: ModelGateway,
    ids: IdGenerator,
    web_url: str,
) -> ZaloCaseQueryCommand:
    """The chat's read-only questions. `gateway` is the process's one-call
    gateway, as for the proposal command; the handler is the API's own, over
    the same Category list file the API loads."""
    repo = SqlPOCaseRepository(sessions)
    product_cases = SqlProductCaseRepository(sessions)
    authz = ScopeAuthorizationService()
    return ZaloCaseQueryCommand(
        answer=AnswerCaseQuery(
            po_case_repo=repo,
            list_cases=ListPOCases(repo=repo, authz=authz),
            product_cases=product_cases,
            list_product_cases=ListProductCases(repo=product_cases, authz=authz),
            categories=ListProductCategories(
                policy_override_repo=SqlPolicyOverrideRepository(sessions),
                platform_default_sla_policy=load_supply_chain_sla_policy(
                    configs_dir / "policies" / SLA_POLICY_FILE
                ),
                authz=authz,
            ),
            directory=SqlWorkspaceDirectory(sessions),
            gateway=gateway,
            authz=authz,
            ids=ids,
        ),
        web_url=web_url,
    )


def build_follow_up_retention(
    sessions: async_sessionmaker[AsyncSession], *, policies_dir: Path
) -> PruneClosedFollowUps:
    """Satisfies `RetentionPrunePort`; registered as
    `supply_chain_follow_ups_retention` on the retention cadence (ticket P3).
    Reads the same follow-up policy file the sweep does."""
    return PruneClosedFollowUps(
        workspaces=SqlWorkspacesWithCases(sessions),
        policy_override_repo=SqlPolicyOverrideRepository(sessions),
        platform_default_policy=load_supply_chain_follow_up_policy(
            policies_dir / FOLLOW_UP_POLICY_FILE
        ),
        follow_ups=SqlFollowUpRepository(sessions),
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
        if outcome.opened or outcome.resolved or outcome.notified or outcome.failed_workspaces:
            logger.info(
                "follow-up sweep: opened=%d resolved=%d notified=%d failed_workspaces=%d",
                outcome.opened,
                outcome.resolved,
                outcome.notified,
                outcome.failed_workspaces,
            )

    return consume
