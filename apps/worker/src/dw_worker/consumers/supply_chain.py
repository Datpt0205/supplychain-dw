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

Step preparation (ticket ai-automation/05) runs on that runner too, when the
case-document bucket exists (a confirmed draft becomes a stored file): the lane
`supply_chain_step_preparation` starts the runs, a decision on Zalo resumes
them here, one on the web in the API, which hosts the same graph.

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
from dw_agent_runtime.adapters.docx_templates import DocxRenderer, DocxTemplateInspector
from dw_agent_runtime.adapters.langgraph_runner import LangGraphWorkflowRunner
from dw_agent_runtime.adapters.run_store import SqlWorkerRunStore
from dw_agent_runtime.adapters.spend_guard import SqlSpendGuardStore
from dw_agent_runtime.approval_flow import ApproveAndResumeService
from dw_agent_runtime.autonomy import AutonomyApprovalPolicy
from dw_agent_runtime.doc_templates import DocTemplateRegistry
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
from dw_supply_chain.action_duties import load_supply_chain_action_duties
from dw_supply_chain.adapters.persistence.case_document_repository import (
    SqlCaseDocumentRepository,
)
from dw_supply_chain.adapters.persistence.code_registry import SqlCodeRegistry
from dw_supply_chain.adapters.persistence.commercial_repository import (
    SqlPOCommercialRepository,
    SqlProductProfileRepository,
    SqlSupplierAccountLookup,
)
from dw_supply_chain.adapters.persistence.document_draft_repository import (
    SqlDocTemplateOverrides,
    SqlDocumentDraftRepository,
)
from dw_supply_chain.adapters.persistence.document_extraction_repository import (
    SqlDocumentExtractionRepository,
    SqlExtractionQueue,
    SqlExtractionReadings,
)
from dw_supply_chain.adapters.persistence.follow_up_repository import (
    SqlFollowUpRepository,
    SqlWorkspacesWithCases,
)
from dw_supply_chain.adapters.persistence.packaging_design_repository import (
    SqlPackagingDesignRepository,
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
from dw_supply_chain.adapters.persistence.proposal_list_repository import (
    SqlProposalListQueue,
    SqlProposalLists,
    SqlTakenCodes,
)
from dw_supply_chain.adapters.persistence.sample_measurement_repository import (
    SqlSampleMeasurements,
)
from dw_supply_chain.adapters.persistence.step_preparation_repository import (
    SqlPreparationRecords,
    SqlProposalOutcomes,
)
from dw_supply_chain.adapters.persistence.supplier_message_repository import (
    SqlSupplierContactLookup,
    SqlSupplierMessageRepository,
)
from dw_supply_chain.adapters.persistence.supplier_update_repository import (
    SqlSupplierUpdateRepository,
)
from dw_supply_chain.adapters.storage.minio_case_documents import MinioCaseDocumentStorage
from dw_supply_chain.application.approval_subject import ProductCaseApprovalSubject
from dw_supply_chain.application.bm04_prefill import (
    Bm04Preparation,
    Bm04ProfileWriter,
    Bm04Writer,
)
from dw_supply_chain.application.bod_submissions import PrepareBodSubmission
from dw_supply_chain.application.case_query import AnswerCaseQuery
from dw_supply_chain.application.commercial import Bm04SchemaSource
from dw_supply_chain.application.daily_report import SendStageOneReport
from dw_supply_chain.application.document_drafts import (
    PrepareDocumentDraft,
    TenantDocTemplates,
)
from dw_supply_chain.application.document_extraction import (
    DocumentText,
    ExtractDocuments,
)
from dw_supply_chain.application.document_orphan_sweep import SweepOrphanDocuments
from dw_supply_chain.application.follow_up_retention import PruneClosedFollowUps
from dw_supply_chain.application.follow_up_sweep import SweepFollowUps
from dw_supply_chain.application.handlers import ListPOCases, ListProductCategories
from dw_supply_chain.application.item_coding import ItemCodingPreparation
from dw_supply_chain.application.packaging_papers import (
    PackagingSources,
    PreparePackagingPapers,
)
from dw_supply_chain.application.po_steps import POStepSources, PreparePOSteps
from dw_supply_chain.application.ports import CaseDocumentObjectListingPort
from dw_supply_chain.application.product_cases import ListProductCases, ProposeProductCase
from dw_supply_chain.application.product_reviews import (
    EnsureProductApproval,
    ReconcileProductApprovals,
)
from dw_supply_chain.application.proposal_lists import ReadProposalLists, TenantCategories
from dw_supply_chain.application.purchase_orders import (
    PreparePurchaseOrders,
    PurchaseOrderSources,
)
from dw_supply_chain.application.step_preparation import (
    EvaluationWriter,
    PrepareStep,
    PrepareSteps,
    SamplePreparation,
)
from dw_supply_chain.application.step_proposals import ApplyStepProposal, StepProposalSubject
from dw_supply_chain.application.supplier_messages import (
    DraftSupplierMessage,
    DraftSupplierMessages,
)
from dw_supply_chain.bm04_schema import load_supply_chain_bm04_schema
from dw_supply_chain.domain.case_document import CaseKind
from dw_supply_chain.domain.step_proposal import STEP_PROPOSAL_PREFIX
from dw_supply_chain.follow_up_policy import load_supply_chain_follow_up_policy
from dw_supply_chain.item_code_rule_policy import load_supply_chain_item_code_rule
from dw_supply_chain.model_routes import (
    BM04_TASK,
    BOD_SUBMISSION_TASK,
    PROPOSAL_LIST_TASK,
    SAMPLE_EVALUATION_TASK,
    SUPPLIER_MESSAGE_TASK,
    load_supply_chain_model_routes,
)
from dw_supply_chain.packaging_policy import load_supply_chain_packaging_policy
from dw_supply_chain.policy_files import (
    ACTION_DUTIES_POLICY_FILE,
    ADVANCE_PRODUCT_CASE_WORKER_FILE,
    BM04_SCHEMA_POLICY_FILE,
    FOLLOW_UP_POLICY_FILE,
    ITEM_CODE_RULE_POLICY_FILE,
    MODEL_ROUTES_POLICY_FILE,
    PACKAGING_POLICY_FILE,
    PRODUCT_ACTION_DUTIES_POLICY_FILE,
    PRODUCT_APPROVALS_POLICY_FILE,
    PRODUCT_SIGNOFF_WORKER_FILE,
    SAMPLE_CRITERIA_POLICY_FILE,
    SLA_POLICY_FILE,
    STEP_PREPARATION_POLICY_FILE,
    STEP_PREPARATION_WORKER_FILE,
    SUPPLIER_MESSAGES_POLICY_FILE,
)
from dw_supply_chain.presentation.zalo_case_query import ZaloCaseQueryCommand
from dw_supply_chain.presentation.zalo_proposal import ZaloProposalCommand
from dw_supply_chain.product_action_duties import load_supply_chain_product_action_duties
from dw_supply_chain.product_approvals import load_supply_chain_product_approvals
from dw_supply_chain.sample_criteria_policy import load_supply_chain_sample_criteria
from dw_supply_chain.sla_policy import load_supply_chain_sla_policy
from dw_supply_chain.step_preparation_policy import load_supply_chain_step_preparation
from dw_supply_chain.supplier_message_policy import load_supply_chain_supplier_messages
from dw_supply_chain.workflows import advance_product_case_graph as product_review_graph
from dw_supply_chain.workflows import product_signoff_graph, step_preparation_graph

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
    configs_dir: Path,
    gates_dir: Path,
    ids: IdGenerator,
    clock: UtcClock,
) -> ExtractDocuments:
    """The extraction lane (ticket ai-automation/02). `gateway` is the
    process's one-call gateway (`ModelStack.one_call`): the plan's daily
    allowance is checked before each call and the call's spend recorded.
    A document type routed to another profile (`supply_chain_model_routes`)
    runs there; a route to a profile that has not passed the model gate stops
    the worker at start, naming it."""
    routes = load_supply_chain_model_routes(
        configs_dir / "policies" / MODEL_ROUTES_POLICY_FILE, gates_dir
    )
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
        routes=routes.extraction_routes(),
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


def build_proposal_lists(
    sessions: async_sessionmaker[AsyncSession],
    *,
    gateway: ModelGateway,
    model_profile: str,
    configs_dir: Path,
    gates_dir: Path,
    ids: IdGenerator,
    clock: UtcClock,
) -> ReadProposalLists:
    """The lane `supply_chain_proposal_lists` (ticket ai-automation/08): the
    one-call gateway, the Category list `propose` itself accepts, and the
    task's route from `supply_chain_model_routes`."""
    policies = configs_dir / "policies"
    routes = load_supply_chain_model_routes(policies / MODEL_ROUTES_POLICY_FILE, gates_dir)
    propose = ProposeProductCase(
        repo=SqlProductCaseRepository(sessions),
        authz=ScopeAuthorizationService(),
        policy_override_repo=SqlPolicyOverrideRepository(sessions),
        platform_default_duties=load_supply_chain_product_action_duties(
            policies / PRODUCT_ACTION_DUTIES_POLICY_FILE
        ),
        platform_default_sla_policy=load_supply_chain_sla_policy(policies / SLA_POLICY_FILE),
        ids=ids,
        clock=clock,
    )
    return ReadProposalLists(
        queue=SqlProposalListQueue(sessions),
        lists=SqlProposalLists(sessions),
        text=InProcessDocumentText(),
        gateway=gateway,
        categories=TenantCategories(propose),
        taken=SqlTakenCodes(sessions),
        plans=SqlTenantPlans(sessions),
        notifier=SqlNotificationRepository(sessions),
        ids=ids,
        clock=clock,
        model_profile=routes.profile_for(PROPOSAL_LIST_TASK) or model_profile,
    )


def build_proposal_lists_consumer(lane: ReadProposalLists) -> Callable[[], Awaitable[None]]:
    async def consume() -> None:
        outcome = await lane.run_once()
        if outcome.by_outcome:
            logger.info("proposal lists: %s", outcome.by_outcome)

    return consume


def build_supplier_messages(
    sessions: async_sessionmaker[AsyncSession],
    *,
    gateway: ModelGateway,
    model_profile: str,
    configs_dir: Path,
    gates_dir: Path,
    ids: IdGenerator,
    clock: UtcClock,
) -> DraftSupplierMessages:
    """The lane `supply_chain_supplier_messages` (ticket ai-automation/07).
    `gateway` is the process's one-call gateway, as the extraction lane's; the
    task's route comes from `supply_chain_model_routes`, refused at start when
    it names a profile that has not passed the model gate."""
    policies = configs_dir / "policies"
    routes = load_supply_chain_model_routes(policies / MODEL_ROUTES_POLICY_FILE, gates_dir)
    product_cases = SqlProductCaseRepository(sessions)
    return DraftSupplierMessages(
        workspaces=SqlWorkspacesWithCases(sessions),
        cases=product_cases,
        follow_ups=SqlFollowUpRepository(sessions),
        drafter=DraftSupplierMessage(
            product_cases=product_cases,
            po_cases=SqlPOCaseRepository(sessions),
            contacts=SqlSupplierContactLookup(sessions),
            messages=SqlSupplierMessageRepository(sessions),
            plans=SqlTenantPlans(sessions),
            gateway=gateway,
            policy_override_repo=SqlPolicyOverrideRepository(sessions),
            platform_default_templates=load_supply_chain_supplier_messages(
                policies / SUPPLIER_MESSAGES_POLICY_FILE
            ),
            notifier=SqlNotificationRepository(sessions),
            ids=ids,
            clock=clock,
            model_profile=routes.profile_for(SUPPLIER_MESSAGE_TASK) or model_profile,
        ),
        policy_override_repo=SqlPolicyOverrideRepository(sessions),
        platform_default_policy=load_supply_chain_step_preparation(
            policies / STEP_PREPARATION_POLICY_FILE
        ),
        documents=SqlCaseDocumentRepository(sessions),
        drafts=SqlDocumentDraftRepository(sessions),
        profiles=SqlProductProfileRepository(sessions),
        # The weekly production chase (ticket ai-automation/17).
        po_listing=SqlPOCaseRepository(sessions),
        readings=SqlExtractionReadings(sessions),
    )


def build_supplier_messages_consumer(
    lane: DraftSupplierMessages,
) -> Callable[[], Awaitable[None]]:
    async def consume() -> None:
        outcome = await lane.run()
        if outcome.by_outcome or outcome.failed_workspaces:
            logger.info(
                "supplier messages: %s failed_workspaces=%d",
                {o.value: n for o, n in outcome.by_outcome.items()},
                outcome.failed_workspaces,
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


@dataclass(frozen=True)
class StepPreparationStack:
    """What the preparation graph is built from, and the subject a
    decision on its approval must still find (ticket ai-automation/05)."""

    preparer: PrepareStep
    applier: ApplyStepProposal
    subject: StepProposalSubject


def build_step_preparation_stack(
    sessions: async_sessionmaker[AsyncSession],
    storage: MinioCaseDocumentStorage,
    *,
    configs_dir: Path,
    ids: IdGenerator,
    clock: UtcClock,
    gateway: ModelGateway | None = None,
    model_profile: str | None = None,
    gates_dir: Path | None = None,
) -> StepPreparationStack:
    """The preparer and the applier over the same tables, templates and bucket
    the API's are built on; a sample round's criteria and measurements, and
    (given `gateway`, the process's one-call gateway) the model that words its
    record and request, on the route `supply_chain_model_routes` gives it."""
    product_cases = SqlProductCaseRepository(sessions)
    documents = SqlCaseDocumentRepository(sessions)
    drafts = SqlDocumentDraftRepository(sessions)
    registry = DocTemplateRegistry(inspector=DocxTemplateInspector())
    registry.load_directory(configs_dir / "doc_templates")
    templates = TenantDocTemplates(registry=registry, overrides=SqlDocTemplateOverrides(sessions))
    policies = configs_dir / "policies"
    writer = None
    bm04_writer = None
    if gateway is not None:
        routes = load_supply_chain_model_routes(
            policies / MODEL_ROUTES_POLICY_FILE, gates_dir or configs_dir.parent / "evals" / "gates"
        )
        writer = EvaluationWriter(
            gateway=gateway,
            plans=SqlTenantPlans(sessions),
            ids=ids,
            worker_id=step_preparation_graph.WORKER_ID,
            worker_version=step_preparation_graph.WORKER_VERSION,
            model_profile=routes.profile_for(SAMPLE_EVALUATION_TASK) or model_profile,
        )
        bm04_writer = Bm04Writer(
            gateway=gateway,
            plans=SqlTenantPlans(sessions),
            ids=ids,
            worker_id=step_preparation_graph.WORKER_ID,
            worker_version=step_preparation_graph.WORKER_VERSION,
            model_profile=routes.profile_for(BM04_TASK) or model_profile,
        )
    bm04_schemas = Bm04SchemaSource(
        policy_override_repo=SqlPolicyOverrideRepository(sessions),
        platform_default=load_supply_chain_bm04_schema(policies / BM04_SCHEMA_POLICY_FILE),
    )
    sample = SamplePreparation(
        measurements=SqlSampleMeasurements(sessions),
        policy_override_repo=SqlPolicyOverrideRepository(sessions),
        platform_default_criteria=load_supply_chain_sample_criteria(
            policies / SAMPLE_CRITERIA_POLICY_FILE
        ),
        writer=writer,
    )
    bm04 = Bm04Preparation(
        schemas=bm04_schemas, writer=bm04_writer, profiles=SqlProductProfileRepository(sessions)
    )
    # Step 9 (ticket ai-automation/13): no model, only code and the tenant's rule.
    coding = ItemCodingPreparation(
        codes=SqlCodeRegistry(sessions),
        profiles=SqlProductProfileRepository(sessions),
        policy_override_repo=SqlPolicyOverrideRepository(sessions),
        platform_default_rule=load_supply_chain_item_code_rule(
            policies / ITEM_CODE_RULE_POLICY_FILE
        ),
    )
    subject = StepProposalSubject(
        cases=product_cases,
        drafts=drafts,
        documents=documents,
        sample=sample,
        bm04=bm04,
        coding=coding,
    )
    preparer = PrepareStep(
        cases=product_cases,
        documents=documents,
        readings=SqlExtractionReadings(sessions),
        drafts=drafts,
        prepare_draft=PrepareDocumentDraft(
            cases={CaseKind.PO: SqlPOCaseRepository(sessions), CaseKind.PRODUCT: product_cases},
            drafts=drafts,
            templates=templates,
            ids=ids,
            clock=clock,
        ),
        templates=templates,
        records=SqlPreparationRecords(sessions),
        ids=ids,
        clock=clock,
        sample=sample,
        bm04=bm04,
        coding=coding,
    )
    applier = ApplyStepProposal(
        cases=product_cases,
        drafts=drafts,
        documents=documents,
        templates=templates,
        renderer=DocxRenderer(),
        storage=storage,
        subject=subject,
        outcomes=SqlProposalOutcomes(sessions),
        ids=ids,
        clock=clock,
        profiles=Bm04ProfileWriter(
            schemas=bm04_schemas,
            profiles=SqlProductProfileRepository(sessions),
            authz=ScopeAuthorizationService(),
            ids=ids,
        ),
        coding=coding,
    )
    return StepPreparationStack(preparer=preparer, applier=applier, subject=subject)


def build_product_review_runner(
    sessions: async_sessionmaker[AsyncSession],
    *,
    configs_dir: Path,
    ids: IdGenerator,
    clock: UtcClock,
    telemetry: TelemetryPort,
    release_manifest_ref: str | None,
    step_preparation: StepPreparationStack | None = None,
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
    if step_preparation is not None:
        stack = step_preparation
        graphs.register(
            step_preparation_graph.WORKER_ID,
            step_preparation_graph.GRAPH_VERSION,
            lambda: step_preparation_graph.build_step_preparation_graph(
                stack.preparer, stack.applier
            ),
        )
    workers = WorkerRegistry(graph_registry=graphs)
    workers.load_file(configs_dir / "workers" / ADVANCE_PRODUCT_CASE_WORKER_FILE)
    workers.load_file(configs_dir / "workers" / PRODUCT_SIGNOFF_WORKER_FILE)
    if step_preparation is not None:
        workers.load_file(configs_dir / "workers" / STEP_PREPARATION_WORKER_FILE)
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
    gateway: ModelGateway | None = None,
    model_profile: str | None = None,
    gates_dir: Path | None = None,
    clock: UtcClock | None = None,
) -> ReconcileProductApprovals:
    """The reconcile lane, starting reviews on `runner`
    (`build_product_review_runner`). Given `gateway` (the one-call gateway),
    it drafts the tờ trình BGĐ reads before raising BGĐ's review (ticket
    ai-automation/10), on the route `supply_chain_model_routes` gives it."""
    product_cases = SqlProductCaseRepository(sessions)
    submissions = None
    if gateway is not None and clock is not None:
        routes = load_supply_chain_model_routes(
            configs_dir / "policies" / MODEL_ROUTES_POLICY_FILE,
            gates_dir or configs_dir.parent / "evals" / "gates",
        )
        drafts = SqlDocumentDraftRepository(sessions)
        registry = DocTemplateRegistry(inspector=DocxTemplateInspector())
        registry.load_directory(configs_dir / "doc_templates")
        templates = TenantDocTemplates(
            registry=registry, overrides=SqlDocTemplateOverrides(sessions)
        )
        submissions = PrepareBodSubmission(
            cases=product_cases,
            documents=SqlCaseDocumentRepository(sessions),
            readings=SqlExtractionReadings(sessions),
            drafts=drafts,
            prepare_draft=PrepareDocumentDraft(
                cases={
                    CaseKind.PO: SqlPOCaseRepository(sessions),
                    CaseKind.PRODUCT: product_cases,
                },
                drafts=drafts,
                templates=templates,
                ids=ids,
                clock=clock,
            ),
            plans=SqlTenantPlans(sessions),
            gateway=gateway,
            ids=ids,
            clock=clock,
            worker_id=product_review_graph.WORKER_ID,
            worker_version=product_review_graph.WORKER_VERSION,
            policy_override_repo=SqlPolicyOverrideRepository(sessions),
            platform_default_policy=load_supply_chain_step_preparation(
                configs_dir / "policies" / STEP_PREPARATION_POLICY_FILE
            ),
            model_profile=routes.profile_for(BOD_SUBMISSION_TASK) or model_profile,
        )
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
            submissions=submissions,
        ),
    )


# What this context adds to the worker's approval flow, as `wiring.py` adds it
# to the API's: its prefix is strict (the requester cannot decide, a decision
# needs a comment). Passed to `build_channel_decision_command`.
PRODUCT_STRICT_APPROVAL_PREFIXES = frozenset(
    {product_review_graph.APPROVAL_TYPE_PREFIX, STEP_PROPOSAL_PREFIX}
)


def product_approval_subjects(
    sessions: async_sessionmaker[AsyncSession],
    step_preparation: StepPreparationStack | None = None,
) -> ApprovalSubjectVersions:
    """The case's version as the subject a decision on Zalo must still find
    (ADR 0007), registered under this context's prefix; and a step proposal's
    subject (its case, drafts and sources) under its own, when this process
    hosts the preparation graph."""
    subjects = ApprovalSubjectVersions()
    subjects.register(
        product_review_graph.APPROVAL_TYPE_PREFIX,
        ProductCaseApprovalSubject(SqlProductCaseRepository(sessions)),
    )
    if step_preparation is not None:
        subjects.register(STEP_PROPOSAL_PREFIX, step_preparation.subject)
    return subjects


def build_step_preparation(
    sessions: async_sessionmaker[AsyncSession],
    *,
    runner: LangGraphWorkflowRunner,
    subjects: ApprovalSubjectVersions,
    configs_dir: Path,
    ids: IdGenerator,
    clock: UtcClock,
) -> PrepareSteps:
    """The lane `supply_chain_step_preparation`, starting runs on `runner`
    (which must host the preparation graph) and superseding its own stale
    proposals through the platform's approval flow over the same subjects."""
    flow = ApproveAndResumeService(
        uow_factory=SqlPlatformUnitOfWorkFactory(sessions),
        runner=runner,
        run_store=runner.run_store,
        clock=clock,
        id_generator=ids,
        subjects=subjects,
    )
    policies = configs_dir / "policies"
    return PrepareSteps(
        workspaces=SqlWorkspacesWithCases(sessions),
        cases=SqlProductCaseRepository(sessions),
        policy_override_repo=SqlPolicyOverrideRepository(sessions),
        platform_default_policy=load_supply_chain_step_preparation(
            policies / STEP_PREPARATION_POLICY_FILE
        ),
        platform_default_duties=load_supply_chain_product_action_duties(
            policies / PRODUCT_ACTION_DUTIES_POLICY_FILE
        ),
        plans=SqlTenantPlans(sessions),
        runner=runner,
        # Bookkeeping, never shown to a person: not narrowed by who asks.
        approvals=SqlPendingApprovalQuery(sessions, ScopeAuthorizationService()),
        supersede=flow,
        records=SqlPreparationRecords(sessions),
        holders=SqlScopeHolders(sessions),
        notifier=SqlNotificationRepository(sessions),
        ids=ids,
        clock=clock,
        worker_id=step_preparation_graph.WORKER_ID,
        worker_version=step_preparation_graph.WORKER_VERSION,
    )


def build_step_preparation_consumer(lane: PrepareSteps) -> Callable[[], Awaitable[None]]:
    async def consume() -> None:
        outcome = await lane.run()
        if outcome.started or outcome.superseded or outcome.not_started:
            logger.info(
                "step preparation: started=%d superseded=%d not_started=%d failed_workspaces=%d",
                outcome.started,
                outcome.superseded,
                outcome.not_started,
                outcome.failed_workspaces,
            )

    return consume


def build_purchase_orders(
    sessions: async_sessionmaker[AsyncSession],
    *,
    configs_dir: Path,
    ids: IdGenerator,
    clock: UtcClock,
) -> PreparePurchaseOrders:
    """The lane `supply_chain_purchase_orders` (ticket ai-automation/14): one
    PO draft, by code, for each PO case awaiting its PO in a tenant whose
    step preparation policy says `purchase_order`. No model, no bucket."""
    registry = DocTemplateRegistry(inspector=DocxTemplateInspector())
    registry.load_directory(configs_dir / "doc_templates")
    templates = TenantDocTemplates(registry=registry, overrides=SqlDocTemplateOverrides(sessions))
    drafts = SqlDocumentDraftRepository(sessions)
    po_cases = SqlPOCaseRepository(sessions)
    return PreparePurchaseOrders(
        workspaces=SqlWorkspacesWithCases(sessions),
        cases=po_cases,
        commercial=SqlPOCommercialRepository(sessions),
        sources=PurchaseOrderSources(
            profiles=SqlProductProfileRepository(sessions),
            documents=SqlCaseDocumentRepository(sessions),
            readings=SqlExtractionReadings(sessions),
        ),
        drafts=drafts,
        prepare_draft=PrepareDocumentDraft(
            cases={CaseKind.PO: po_cases, CaseKind.PRODUCT: SqlProductCaseRepository(sessions)},
            drafts=drafts,
            templates=templates,
            ids=ids,
            clock=clock,
        ),
        policy_override_repo=SqlPolicyOverrideRepository(sessions),
        platform_default_policy=load_supply_chain_step_preparation(
            configs_dir / "policies" / STEP_PREPARATION_POLICY_FILE
        ),
        holders=SqlScopeHolders(sessions),
        notifier=SqlNotificationRepository(sessions),
        clock=clock,
    )


def build_purchase_orders_consumer(lane: PreparePurchaseOrders) -> Callable[[], Awaitable[None]]:
    async def consume() -> None:
        outcome = await lane.run()
        if outcome.drafted or outcome.failed_workspaces:
            logger.info(
                "purchase orders: drafted=%d failed_workspaces=%d",
                outcome.drafted,
                outcome.failed_workspaces,
            )

    return consume


def build_po_steps(
    sessions: async_sessionmaker[AsyncSession],
    *,
    configs_dir: Path,
    ids: IdGenerator,
    clock: UtcClock,
) -> PreparePOSteps:
    """The lane `supply_chain_po_steps` (tickets ai-automation/15-18): the
    paper of each enabled PO step, by code, once per case in the step's
    state. No model, no bucket."""
    registry = DocTemplateRegistry(inspector=DocxTemplateInspector())
    registry.load_directory(configs_dir / "doc_templates")
    templates = TenantDocTemplates(registry=registry, overrides=SqlDocTemplateOverrides(sessions))
    drafts = SqlDocumentDraftRepository(sessions)
    po_cases = SqlPOCaseRepository(sessions)
    policies = configs_dir / "policies"
    return PreparePOSteps(
        workspaces=SqlWorkspacesWithCases(sessions),
        cases=po_cases,
        sources=POStepSources(
            commercial=SqlPOCommercialRepository(sessions),
            accounts=SqlSupplierAccountLookup(sessions),
            documents=SqlCaseDocumentRepository(sessions),
            readings=SqlExtractionReadings(sessions),
        ),
        drafts=drafts,
        prepare_draft=PrepareDocumentDraft(
            cases={CaseKind.PO: po_cases, CaseKind.PRODUCT: SqlProductCaseRepository(sessions)},
            drafts=drafts,
            templates=templates,
            ids=ids,
            clock=clock,
        ),
        policy_override_repo=SqlPolicyOverrideRepository(sessions),
        platform_default_policy=load_supply_chain_step_preparation(
            policies / STEP_PREPARATION_POLICY_FILE
        ),
        platform_default_duties=load_supply_chain_action_duties(
            policies / ACTION_DUTIES_POLICY_FILE
        ),
        holders=SqlScopeHolders(sessions),
        notifier=SqlNotificationRepository(sessions),
        clock=clock,
    )


def build_po_steps_consumer(lane: PreparePOSteps) -> Callable[[], Awaitable[None]]:
    async def consume() -> None:
        outcome = await lane.run()
        if outcome.drafted or outcome.failed_workspaces:
            logger.info(
                "PO steps: drafted=%d failed_workspaces=%d",
                outcome.drafted,
                outcome.failed_workspaces,
            )

    return consume


def build_packaging_papers(
    sessions: async_sessionmaker[AsyncSession],
    *,
    configs_dir: Path,
    ids: IdGenerator,
    clock: UtcClock,
) -> PreparePackagingPapers:
    """The lane `supply_chain_packaging_papers` (ticket ai-automation/16):
    step 12's skeletons and revision requests, by code, from the BM04 and the
    proof the extraction lane read. No model, no bucket."""
    registry = DocTemplateRegistry(inspector=DocxTemplateInspector())
    registry.load_directory(configs_dir / "doc_templates")
    templates = TenantDocTemplates(registry=registry, overrides=SqlDocTemplateOverrides(sessions))
    drafts = SqlDocumentDraftRepository(sessions)
    po_cases = SqlPOCaseRepository(sessions)
    policies = configs_dir / "policies"
    return PreparePackagingPapers(
        workspaces=SqlWorkspacesWithCases(sessions),
        cases=po_cases,
        designs=SqlPackagingDesignRepository(sessions),
        sources=PackagingSources(
            profiles=SqlProductProfileRepository(sessions),
            documents=SqlCaseDocumentRepository(sessions),
            readings=SqlExtractionReadings(sessions),
        ),
        drafts=drafts,
        prepare_draft=PrepareDocumentDraft(
            cases={CaseKind.PO: po_cases, CaseKind.PRODUCT: SqlProductCaseRepository(sessions)},
            drafts=drafts,
            templates=templates,
            ids=ids,
            clock=clock,
        ),
        policy_override_repo=SqlPolicyOverrideRepository(sessions),
        platform_default_policy=load_supply_chain_step_preparation(
            policies / STEP_PREPARATION_POLICY_FILE
        ),
        platform_default_packaging=load_supply_chain_packaging_policy(
            policies / PACKAGING_POLICY_FILE
        ),
        platform_default_duties=load_supply_chain_action_duties(
            policies / ACTION_DUTIES_POLICY_FILE
        ),
        holders=SqlScopeHolders(sessions),
        notifier=SqlNotificationRepository(sessions),
        clock=clock,
    )


def build_packaging_papers_consumer(
    lane: PreparePackagingPapers,
) -> Callable[[], Awaitable[None]]:
    async def consume() -> None:
        outcome = await lane.run()
        if outcome.drafted or outcome.failed_workspaces:
            logger.info(
                "packaging papers: drafted=%d failed_workspaces=%d",
                outcome.drafted,
                outcome.failed_workspaces,
            )

    return consume


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
