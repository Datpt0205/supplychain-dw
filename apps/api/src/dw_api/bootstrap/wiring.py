"""The composition root itself: the only place concrete adapters are wired.

Tests build their own container with fake ports; deployed wiring lives here.

Reading order is the dependency order — settings, then the database and the
platform services that need one, then object storage, then the agent runtime
that needs both. Each stage is skipped rather than faked when its infrastructure
is absent, which is why almost every field on ``ApiContainer`` is optional: a
host with no database mounts fewer routers instead of serving routers that fail
on every request.

## Plugging in a bounded context

Nothing in this package names a business context, and it must stay that way.
A context joins in three places and no others:

1. here, at the marked seam below — build its handlers from
   ``container.runtime`` and hang them off your own container extension;
2. ``main.create_app`` — mount its presentation router;
3. ``configs/`` — its worker, graph, prompt, tool, toolset and policy files.

``RuntimeSeam`` is the published object it is wired from, so adding a context is
a call rather than an edit in the middle of this file.
"""

from __future__ import annotations

import logging
import uuid

from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from dw_agent_runtime.adapters.run_events import RunStateListener
from dw_agent_runtime.adapters.run_store import SqlWorkerRunStore
from dw_agent_runtime.model.run_policy import load_worker_run_policy
from dw_api.bootstrap.container import ApiContainer
from dw_api.bootstrap.identity import build_token_verifier
from dw_api.bootstrap.paths import (
    SUPPLY_CHAIN_ACTION_DUTIES,
    SUPPLY_CHAIN_ADVANCE_CASE_WORKER,
    SUPPLY_CHAIN_ADVANCE_PRODUCT_CASE_WORKER,
    SUPPLY_CHAIN_APPROVAL_MATRIX_POLICY,
    SUPPLY_CHAIN_BRIEF_POLICY,
    SUPPLY_CHAIN_FOLLOW_UP_POLICY,
    SUPPLY_CHAIN_PRODUCT_ACTION_DUTIES,
    SUPPLY_CHAIN_PRODUCT_APPROVALS,
    SUPPLY_CHAIN_SLA_POLICY,
    WORKER_RUN_POLICY,
)
from dw_api.bootstrap.runtime import build_runtime
from dw_api.bootstrap.storage import (
    build_attachment_storage,
    build_minio_client,
    build_object_storage,
)
from dw_api.bootstrap.telemetry import build_telemetry
from dw_api.health import HealthService, database_probe, qdrant_probe, redis_probe
from dw_api.settings import ApiSettings
from dw_connectors.adapters.zalo_link import ZaloLinking
from dw_kernel.ports import SystemClock, Uuid7Generator
from dw_platform.adapters.cache import NullCache, ValkeyCache
from dw_platform.adapters.persistence.admin_console_repo import SqlAdminConsoleRepository
from dw_platform.adapters.persistence.approval_queries import SqlPendingApprovalQuery
from dw_platform.adapters.persistence.caching_lookup import CachingMembershipLookup
from dw_platform.adapters.persistence.channel_preferences import SqlChannelPreferences
from dw_platform.adapters.persistence.directory import SqlWorkspaceDirectory
from dw_platform.adapters.persistence.hierarchy_repo import SqlHierarchyRepository
from dw_platform.adapters.persistence.idempotency_store import SqlIdempotencyStore
from dw_platform.adapters.persistence.identity_provisioning import SqlIdentityBootstrap
from dw_platform.adapters.persistence.membership_admin import SqlMembershipAdminRepository
from dw_platform.adapters.persistence.membership_lookup import SqlMembershipLookup
from dw_platform.adapters.persistence.notifications import SqlNotificationRepository
from dw_platform.adapters.persistence.policy_overrides import SqlPolicyOverrideRepository
from dw_platform.adapters.persistence.provisioning_repo import SqlProvisioningRepository
from dw_platform.adapters.persistence.scope_holders import SqlScopeHolders
from dw_platform.adapters.persistence.separation_of_duties_repo import (
    SqlSeparationOfDutiesRepository,
)
from dw_platform.adapters.persistence.uow import SqlPlatformUnitOfWorkFactory
from dw_platform.adapters.persistence.zalo_link_repo import SqlZaloLink
from dw_platform.application.admin_console import AdminConsoleService
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.entitlement import DEFAULT_PLANS, PlanEntitlementService
from dw_platform.application.hierarchy import HierarchyService
from dw_platform.application.idempotency import HttpIdempotency
from dw_platform.application.identity import DbAccessContextFactory
from dw_platform.application.membership_admin import (
    GrantMembershipHandler,
    RevokeMembershipHandler,
)
from dw_platform.application.notifications import NotificationService
from dw_platform.application.provisioning import ProvisioningService
from dw_platform.application.separation_of_duties import SeparationOfDutiesService

_LOG = logging.getLogger("dw_api.bootstrap")

_RUN_POLICY = load_worker_run_policy(WORKER_RUN_POLICY)


def _asyncpg_dsn(url: str) -> str:
    """SQLAlchemy's URL minus the driver marker asyncpg does not understand."""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def build_container(settings: ApiSettings | None = None) -> ApiContainer:
    settings = settings or ApiSettings()
    settings.validate_for_profile()

    clock = SystemClock()
    # Time-ordered ids (RFC 9562), not random v4. Every primary key in this
    # schema is a UUID, and a random one scatters each insert across the whole
    # B-tree; a v7 key appends to one edge of it. The difference is invisible at
    # demo size and is the difference between a healthy index and a bloated one
    # at real size — and it cannot be fixed later, because the rows already
    # written keep the keys they were given.
    ids = Uuid7Generator()
    authorization = ScopeAuthorizationService()
    entitlement = PlanEntitlementService(DEFAULT_PLANS)
    telemetry = build_telemetry(settings)

    # Built ahead of the database gate below: neither depends on Postgres, and
    # readiness must report both regardless of whether a database is
    # configured at all.
    cache = ValkeyCache.from_url(settings.redis_url) if settings.redis_url else NullCache()
    qdrant_client = (
        AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
        if settings.qdrant_url
        else None
    )

    def _health_service(engine: AsyncEngine | None) -> HealthService:
        return HealthService(
            probes={
                "database": database_probe(engine),
                "redis": redis_probe(cache.client if isinstance(cache, ValkeyCache) else None),
                "qdrant": qdrant_probe(qdrant_client),
            }
        )

    container = ApiContainer(
        settings=settings,
        engine=None,
        health_service=_health_service(None),
        token_verifier=build_token_verifier(settings),
        access_context_factory=None,
        identity_bootstrap=None,
        uow_factory=None,
        authorization=authorization,
        entitlement=entitlement,
        cache=cache,
        qdrant_client=qdrant_client,
    )
    if not settings.database_url:
        _LOG.warning("no database configured: only stateless routes are mounted")
        return container

    # ---- database + platform services ------------------------------------
    # The library default (pool_size=5, max_overflow=10) serialises past 15
    # concurrent DB-touching requests in an async app. 10/20 gives real
    # headroom and stays well under Postgres' default 100 across all pools.
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    container.engine = engine
    container.health_service = _health_service(engine)

    # Cache the membership lookup (the per-request AccessContext) so a burst
    # from one user hits the database once. No cache URL → straight to the
    # database, unchanged.
    container.access_context_factory = DbAccessContextFactory(
        CachingMembershipLookup(SqlMembershipLookup(session_factory), cache)
    )
    container.identity_bootstrap = SqlIdentityBootstrap(
        session_factory=session_factory,
        default_tenant_id=uuid.UUID(settings.default_tenant_id),
        default_workspace_id=uuid.UUID(settings.default_workspace_id),
        default_role=settings.default_role,
        auto_provision_default_membership=settings.auto_provision_default_membership,
    )
    uow_factory = SqlPlatformUnitOfWorkFactory(session_factory)
    container.uow_factory = uow_factory
    container.idempotency = HttpIdempotency(SqlIdempotencyStore(session_factory), clock)
    container.workspace_directory = SqlWorkspaceDirectory(session_factory)

    membership_repo = SqlMembershipAdminRepository(session_factory)
    container.grant_membership = GrantMembershipHandler(membership_repo, authorization, clock, ids)
    container.revoke_membership = RevokeMembershipHandler(
        membership_repo, authorization, clock, ids
    )
    container.admin_console = AdminConsoleService(
        SqlAdminConsoleRepository(session_factory), authorization, clock, ids
    )
    container.hierarchy = HierarchyService(
        SqlHierarchyRepository(session_factory), authorization, clock, ids
    )
    container.separation_of_duties = SeparationOfDutiesService(
        SqlSeparationOfDutiesRepository(session_factory), authorization, clock, ids
    )
    container.notifications = NotificationService(SqlNotificationRepository(session_factory))
    # The user's own Zalo link, on the request pool (`dw_app`), which holds the
    # identity-plane grants it needs; never the provisioner engine.
    if settings.zalo_link_enabled:
        container.zalo_linking = ZaloLinking(
            store=SqlZaloLink(session_factory),
            link_secret=settings.zalo_link_secret.get_secret_value(),
            clock=clock,
            bot_link=settings.zalo_bot_link,
        )
        container.channel_preferences = SqlChannelPreferences(session_factory)

    # ---- provisioning ----------------------------------------------------
    # A second engine as the provisioner role: writes across tenants but holds
    # no grant on any business schema. No URL → /platform is not mounted.
    if settings.provisioner_database_url:
        provisioner_engine = create_async_engine(
            settings.provisioner_database_url, pool_pre_ping=True
        )
        container.provisioner_engine = provisioner_engine
        container.provisioning = ProvisioningService(
            repo=SqlProvisioningRepository(
                async_sessionmaker(provisioner_engine, class_=AsyncSession, expire_on_commit=False)
            ),
            clock=clock,
            ids=ids,
        )

    # ---- runs ------------------------------------------------------------
    run_store = SqlWorkerRunStore(
        session_factory, stale_run_after_seconds=_RUN_POLICY.stale_run_after_seconds
    )
    container.run_store = run_store
    # asyncpg directly: a listening connection never returns to a pool, and
    # SQLAlchemy's URL prefix is not a DSN asyncpg accepts.
    container.run_events = RunStateListener(_asyncpg_dsn(settings.database_url))

    # ---- object storage --------------------------------------------------
    minio = build_minio_client(settings)
    object_storage = build_object_storage(minio, settings)
    container.object_storage = object_storage
    container.feedback_storage = build_attachment_storage(minio, settings)

    # `object_storage` is None exactly when `minio` is; both are named so the
    # case-document storage below is built from a client known to exist.
    if minio is None or object_storage is None:
        _LOG.warning("no object storage configured: the agent runtime is not wired")
        return container

    # ---- agent runtime ---------------------------------------------------
    wiring = build_runtime(
        settings,
        session_factory=session_factory,
        uow_factory=uow_factory,
        run_store=run_store,
        allowance=entitlement,
        object_storage=object_storage,
        telemetry=telemetry,
        clock=clock,
        ids=ids,
    )
    container.runtime = wiring.seam
    container.runner = wiring.runner
    container.approval_flow = wiring.approval_flow
    container.knowledge_gateway = wiring.knowledge_gateway
    container.ingest_job_store = wiring.ingest_jobs
    container.memory_service = wiring.memory_service
    container.tool_registry = wiring.tool_registry

    # ---- BOUNDED CONTEXTS PLUG IN HERE -----------------------------------
    # Supply Chain: built from the seam, never from a global. `container.runtime`
    # carries the session factory, clock, ids, registries and gateways; anything
    # this context needs beyond them is its own adapter. `authorization` has no
    # seam field of its own (only platform-native handlers use it today, all
    # built the same way — from this function's own local, same as here) so it
    # comes from the closure directly rather than from `wiring.seam`.
    from dw_agent_runtime.model.single_call import SingleCallModelGateway
    from dw_supply_chain.action_duties import load_supply_chain_action_duties
    from dw_supply_chain.adapters.persistence.delay_impact_repository import (
        SqlDelayImpactAnalysisRepository,
    )
    from dw_supply_chain.adapters.persistence.follow_up_repository import SqlFollowUpRepository
    from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
    from dw_supply_chain.adapters.persistence.supplier_update_repository import (
        SqlSupplierUpdateRepository,
    )
    from dw_supply_chain.application.handlers import (
        AdvancePOCase,
        AnalyzeDelayImpact,
        AnswerCaseQuery,
        CloseFollowUp,
        CreatePOCase,
        GetActionDuties,
        GetApprovalMatrix,
        GetAttentionQueue,
        GetBriefPolicy,
        GetDailyBrief,
        GetFollowUpPolicy,
        GetMissingUpdateStatus,
        GetPOCase,
        GetPortfolioSummary,
        GetSLAEvaluation,
        GetSLAPolicy,
        ListCaseTransitions,
        ListDelayImpactAnalyses,
        ListFollowUps,
        ListPOCases,
        ListSupplierUpdates,
        SetActionDutiesOverride,
        SetApprovalMatrixOverride,
        SetBriefPolicyOverride,
        SetFollowUpPolicyOverride,
        SetSLAPolicyOverride,
        SubmitSupplierUpdate,
        SummarizeDailyBrief,
    )
    from dw_supply_chain.approval_matrix import load_supply_chain_approval_matrix
    from dw_supply_chain.brief_policy import load_supply_chain_brief_policy
    from dw_supply_chain.follow_up_policy import load_supply_chain_follow_up_policy
    from dw_supply_chain.sla_policy import load_supply_chain_sla_policy
    from dw_supply_chain.workflows.advance_case_graph import (
        APPROVAL_TYPE_PREFIX,
        GRAPH_VERSION,
        WORKER_ID,
        build_advance_case_graph,
    )

    po_case_repo = SqlPOCaseRepository(wiring.seam.session_factory)
    policy_override_repo = SqlPolicyOverrideRepository(wiring.seam.session_factory)
    platform_default_sla_policy = load_supply_chain_sla_policy(SUPPLY_CHAIN_SLA_POLICY)
    platform_default_approval_matrix = load_supply_chain_approval_matrix(
        SUPPLY_CHAIN_APPROVAL_MATRIX_POLICY
    )
    platform_default_brief_policy = load_supply_chain_brief_policy(SUPPLY_CHAIN_BRIEF_POLICY)
    platform_default_action_duties = load_supply_chain_action_duties(SUPPLY_CHAIN_ACTION_DUTIES)
    supplier_update_repo = SqlSupplierUpdateRepository(wiring.seam.session_factory)
    delay_impact_repo = SqlDelayImpactAnalysisRepository(wiring.seam.session_factory)
    # Every Supply Chain model call is a one-call run with no runner around it,
    # so nothing else would ever free its spend-ledger entry (failure-modes #6).
    one_call_gateway = SingleCallModelGateway(inner=wiring.seam.gateway, ledger=wiring.seam.budget)
    container.supply_chain_create_po_case = CreatePOCase(
        repo=po_case_repo, authz=authorization, ids=wiring.seam.ids
    )
    container.supply_chain_get_po_case = GetPOCase(repo=po_case_repo, authz=authorization)
    list_po_cases = ListPOCases(repo=po_case_repo, authz=authorization)
    container.supply_chain_list_po_cases = list_po_cases
    container.supply_chain_submit_supplier_update = SubmitSupplierUpdate(
        po_case_repo=po_case_repo,
        supplier_update_repo=supplier_update_repo,
        gateway=one_call_gateway,
        authz=authorization,
        ids=wiring.seam.ids,
    )
    container.supply_chain_list_supplier_updates = ListSupplierUpdates(
        po_case_repo=po_case_repo, supplier_update_repo=supplier_update_repo, authz=authorization
    )
    container.supply_chain_analyze_delay_impact = AnalyzeDelayImpact(
        po_case_repo=po_case_repo,
        supplier_update_repo=supplier_update_repo,
        delay_impact_repo=delay_impact_repo,
        gateway=one_call_gateway,
        authz=authorization,
        ids=wiring.seam.ids,
    )
    container.supply_chain_list_delay_impact_analyses = ListDelayImpactAnalyses(
        po_case_repo=po_case_repo, delay_impact_repo=delay_impact_repo, authz=authorization
    )
    container.supply_chain_get_missing_update_status = GetMissingUpdateStatus(
        po_case_repo=po_case_repo,
        supplier_update_repo=supplier_update_repo,
        policy_override_repo=policy_override_repo,
        platform_default_policy=platform_default_sla_policy,
        authz=authorization,
        clock=wiring.seam.clock,
    )
    # Supply Chain is the first bounded context to actually register a graph
    # through this seam — `graphs.register` before `workers.load_file`
    # (the loader resolves the graph immediately, per `WorkerRegistry.
    # load_file`'s own fail-fast contract). `repo` is captured by closure,
    # never a concrete adapter imported inside the graph module itself.
    wiring.seam.graphs.register(
        WORKER_ID, GRAPH_VERSION, lambda: build_advance_case_graph(po_case_repo)
    )
    wiring.seam.workers.load_file(SUPPLY_CHAIN_ADVANCE_CASE_WORKER)
    # Separation of duties + a mandatory comment for every approval this
    # graph raises, platform-wide — not something a tenant's own approval-
    # matrix override can weaken (see `approval_matrix.py`'s docstring).
    # `|=` rather than `=`: a second bounded context adding its own prefix
    # later must not silently drop this one.
    wiring.approval_flow.strict_approval_prefixes = (
        wiring.approval_flow.strict_approval_prefixes | frozenset({APPROVAL_TYPE_PREFIX})
    )
    container.supply_chain_advance_po_case = AdvancePOCase(
        repo=po_case_repo,
        authz=authorization,
        policy_override_repo=policy_override_repo,
        platform_default_approval_matrix=platform_default_approval_matrix,
        platform_default_action_duties=platform_default_action_duties,
        runner=wiring.runner,
        ids=wiring.seam.ids,
    )
    container.supply_chain_list_case_transitions = ListCaseTransitions(
        repo=po_case_repo, authz=authorization
    )
    container.supply_chain_get_sla_evaluation = GetSLAEvaluation(
        po_case_repo=po_case_repo,
        policy_override_repo=policy_override_repo,
        platform_default_policy=platform_default_sla_policy,
        authz=authorization,
        clock=wiring.seam.clock,
    )
    container.supply_chain_get_sla_policy = GetSLAPolicy(
        policy_override_repo=policy_override_repo,
        platform_default_policy=platform_default_sla_policy,
        authz=authorization,
    )
    container.supply_chain_set_sla_policy_override = SetSLAPolicyOverride(
        policy_override_repo=policy_override_repo,
        authz=authorization,
        ids=wiring.seam.ids,
        clock=wiring.seam.clock,
    )
    container.supply_chain_get_approval_matrix = GetApprovalMatrix(
        policy_override_repo=policy_override_repo,
        platform_default_matrix=platform_default_approval_matrix,
        authz=authorization,
    )
    container.supply_chain_set_approval_matrix_override = SetApprovalMatrixOverride(
        policy_override_repo=policy_override_repo,
        authz=authorization,
        ids=wiring.seam.ids,
        clock=wiring.seam.clock,
    )
    container.supply_chain_get_attention_queue = GetAttentionQueue(
        po_case_repo=po_case_repo,
        supplier_update_repo=supplier_update_repo,
        policy_override_repo=policy_override_repo,
        platform_default_policy=platform_default_sla_policy,
        authz=authorization,
        clock=wiring.seam.clock,
    )
    container.supply_chain_answer_case_query = AnswerCaseQuery(
        po_case_repo=po_case_repo,
        # The SAME list handler the route uses: one place decides what a
        # filter means and who may read the result.
        list_cases=list_po_cases,
        gateway=one_call_gateway,
        authz=authorization,
        ids=wiring.seam.ids,
    )
    container.supply_chain_get_portfolio_summary = GetPortfolioSummary(
        po_case_repo=po_case_repo,
        supplier_update_repo=supplier_update_repo,
        policy_override_repo=policy_override_repo,
        platform_default_policy=platform_default_sla_policy,
        authz=authorization,
        clock=wiring.seam.clock,
    )
    get_daily_brief = GetDailyBrief(
        po_case_repo=po_case_repo,
        supplier_update_repo=supplier_update_repo,
        policy_override_repo=policy_override_repo,
        platform_default_policy=platform_default_sla_policy,
        platform_default_brief_policy=platform_default_brief_policy,
        # The platform's own approval inbox, read through the narrow
        # Protocol Supply Chain declares: the context never reads
        # platform.approval_requests itself.
        pending_approvals=SqlPendingApprovalQuery(wiring.seam.session_factory, authorization),
        authz=authorization,
        clock=wiring.seam.clock,
    )
    container.supply_chain_get_daily_brief = get_daily_brief
    container.supply_chain_summarize_daily_brief = SummarizeDailyBrief(
        # The SAME brief handler the page reads: one place decides who may
        # see the brief and what it holds, summarized or not.
        get_daily_brief=get_daily_brief,
        gateway=one_call_gateway,
        ids=wiring.seam.ids,
    )
    container.supply_chain_get_action_duties = GetActionDuties(
        policy_override_repo=policy_override_repo,
        platform_default_duties=platform_default_action_duties,
        authz=authorization,
    )
    container.supply_chain_set_action_duties_override = SetActionDutiesOverride(
        policy_override_repo=policy_override_repo,
        authz=authorization,
        ids=wiring.seam.ids,
        clock=wiring.seam.clock,
    )
    container.supply_chain_get_brief_policy = GetBriefPolicy(
        policy_override_repo=policy_override_repo,
        platform_default_policy=platform_default_brief_policy,
        authz=authorization,
    )
    container.supply_chain_set_brief_policy_override = SetBriefPolicyOverride(
        policy_override_repo=policy_override_repo,
        authz=authorization,
        ids=wiring.seam.ids,
        clock=wiring.seam.clock,
    )
    follow_up_repo = SqlFollowUpRepository(wiring.seam.session_factory)
    container.supply_chain_list_follow_ups = ListFollowUps(
        follow_up_repo=follow_up_repo, authz=authorization
    )
    container.supply_chain_close_follow_up = CloseFollowUp(
        follow_up_repo=follow_up_repo,
        authz=authorization,
        ids=wiring.seam.ids,
        clock=wiring.seam.clock,
    )
    container.supply_chain_get_follow_up_policy = GetFollowUpPolicy(
        policy_override_repo=policy_override_repo,
        platform_default_policy=load_supply_chain_follow_up_policy(SUPPLY_CHAIN_FOLLOW_UP_POLICY),
        authz=authorization,
    )
    container.supply_chain_set_follow_up_policy_override = SetFollowUpPolicyOverride(
        policy_override_repo=policy_override_repo,
        authz=authorization,
        ids=wiring.seam.ids,
        clock=wiring.seam.clock,
    )
    # Case documents (ADR 0021), in their own bucket on the same client. Like
    # everything above they exist only where object storage does: the rest of
    # the Supply Chain API is built from the runtime, which needs it too.
    from dw_supply_chain.adapters.persistence.case_document_repository import (
        SqlCaseDocumentRepository,
    )
    from dw_supply_chain.adapters.persistence.product_case_repository import (
        SqlProductCaseRepository,
    )
    from dw_supply_chain.adapters.storage.minio_case_documents import (
        MinioCaseDocumentStorage,
    )
    from dw_supply_chain.application.case_documents import (
        CaseLookupPort,
        DownloadCaseDocument,
        ListCaseDocuments,
        UploadCaseDocument,
    )
    from dw_supply_chain.application.handlers import (
        GetProductActionDuties,
        SetProductActionDutiesOverride,
    )
    from dw_supply_chain.application.product_cases import (
        AdvanceProductCase,
        GetProductCase,
        ListProductCases,
        ListProductCaseTransitions,
        ProposeProductCase,
    )
    from dw_supply_chain.application.product_reviews import EnsureBodReview
    from dw_supply_chain.domain.case_document import CaseKind
    from dw_supply_chain.product_action_duties import load_supply_chain_product_action_duties
    from dw_supply_chain.product_approvals import load_supply_chain_product_approvals
    from dw_supply_chain.workflows import advance_product_case_graph as product_review_graph

    document_repo = SqlCaseDocumentRepository(wiring.seam.session_factory)
    document_storage = MinioCaseDocumentStorage(client=minio, bucket=settings.case_documents_bucket)
    product_case_repo = SqlProductCaseRepository(wiring.seam.session_factory)
    # Each kind of case answers "which workspace is this case in" for its own
    # documents; the document handlers pick by kind.
    case_lookups: dict[CaseKind, CaseLookupPort] = {
        CaseKind.PO: po_case_repo,
        CaseKind.PRODUCT: product_case_repo,
    }
    container.supply_chain_upload_case_document = UploadCaseDocument(
        cases=case_lookups,
        documents=document_repo,
        storage=document_storage,
        authz=authorization,
        ids=wiring.seam.ids,
        clock=wiring.seam.clock,
        max_bytes=settings.case_document_max_bytes,
    )
    container.supply_chain_list_case_documents = ListCaseDocuments(
        cases=case_lookups, documents=document_repo, authz=authorization
    )
    container.supply_chain_download_case_document = DownloadCaseDocument(
        documents=document_repo, storage=document_storage, authz=authorization
    )

    # Product-development cases (stage 1, ADR 0016). Built here, beside the
    # documents, because a step's paper is read through the document records.
    platform_default_product_duties = load_supply_chain_product_action_duties(
        SUPPLY_CHAIN_PRODUCT_ACTION_DUTIES
    )
    # BGĐ's review (step 6): its graph registered once the case records it
    # applies BGĐ's decision through exist, then its worker. Strict, like the
    # PO prefix and with `|=` for the same reason: the requester cannot decide
    # their own review and a decision needs a comment. Who else may decide is
    # the `required_scope` each review is stamped with.
    wiring.seam.graphs.register(
        product_review_graph.WORKER_ID,
        product_review_graph.GRAPH_VERSION,
        lambda: product_review_graph.build_advance_product_case_graph(
            product_case_repo, wiring.seam.ids, wiring.seam.clock
        ),
    )
    wiring.seam.workers.load_file(SUPPLY_CHAIN_ADVANCE_PRODUCT_CASE_WORKER)
    wiring.approval_flow.strict_approval_prefixes = (
        wiring.approval_flow.strict_approval_prefixes
        | frozenset({product_review_graph.APPROVAL_TYPE_PREFIX})
    )
    # The approval inbox, read through the narrow Protocol the context declares.
    pending_approvals = SqlPendingApprovalQuery(wiring.seam.session_factory, authorization)
    product_reviews = EnsureBodReview(
        runner=wiring.runner,
        approvals=pending_approvals,
        holders=SqlScopeHolders(wiring.seam.session_factory),
        notifier=SqlNotificationRepository(wiring.seam.session_factory),
        policy_override_repo=policy_override_repo,
        platform_default_approvals=load_supply_chain_product_approvals(
            SUPPLY_CHAIN_PRODUCT_APPROVALS
        ),
        ids=wiring.seam.ids,
    )
    container.supply_chain_propose_product_case = ProposeProductCase(
        repo=product_case_repo,
        authz=authorization,
        policy_override_repo=policy_override_repo,
        platform_default_duties=platform_default_product_duties,
        ids=wiring.seam.ids,
        clock=wiring.seam.clock,
    )
    container.supply_chain_get_product_case = GetProductCase(
        repo=product_case_repo,
        authz=authorization,
        policy_override_repo=policy_override_repo,
        platform_default_duties=platform_default_product_duties,
        approvals=pending_approvals,
    )
    container.supply_chain_list_product_cases = ListProductCases(
        repo=product_case_repo, authz=authorization
    )
    container.supply_chain_advance_product_case = AdvanceProductCase(
        repo=product_case_repo,
        documents=document_repo,
        authz=authorization,
        policy_override_repo=policy_override_repo,
        platform_default_duties=platform_default_product_duties,
        reviews=product_reviews,
        ids=wiring.seam.ids,
        clock=wiring.seam.clock,
    )
    container.supply_chain_list_product_case_transitions = ListProductCaseTransitions(
        repo=product_case_repo, authz=authorization
    )
    container.supply_chain_get_product_action_duties = GetProductActionDuties(
        policy_override_repo=policy_override_repo,
        platform_default_duties=platform_default_product_duties,
        authz=authorization,
    )
    container.supply_chain_set_product_action_duties_override = SetProductActionDutiesOverride(
        policy_override_repo=policy_override_repo,
        authz=authorization,
        ids=wiring.seam.ids,
        clock=wiring.seam.clock,
    )

    # Build your context from `container.runtime` (the RuntimeSeam) and attach
    # its handlers, then mount its router in `main.create_app`. Nothing above
    # this line may import a business package.

    return container


def build_engine(url: str, *, pool_pre_ping: bool = True) -> AsyncEngine:
    """Shared engine construction, for processes that need one outside the API."""
    return create_async_engine(url, pool_pre_ping=pool_pre_ping)
