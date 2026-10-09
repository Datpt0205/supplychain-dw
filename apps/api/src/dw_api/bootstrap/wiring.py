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
from collections.abc import Mapping
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from dw_agent_runtime.adapters.run_events import RunStateListener
from dw_agent_runtime.adapters.run_store import SqlWorkerRunStore
from dw_agent_runtime.adapters.spend_guard import SqlSpendGuardStore
from dw_agent_runtime.allowance import DailyAllowance
from dw_agent_runtime.approval_codes import ApprovalViewService
from dw_agent_runtime.approval_flow import ApproveAndResumeService
from dw_agent_runtime.model.run_policy import load_worker_run_policy
from dw_api.bootstrap.container import ApiContainer
from dw_api.bootstrap.identity import build_token_verifier
from dw_api.bootstrap.paths import (
    DOC_TEMPLATES_DIR,
    SUPPLY_CHAIN_ACTION_DUTIES,
    SUPPLY_CHAIN_ADVANCE_CASE_WORKER,
    SUPPLY_CHAIN_ADVANCE_PRODUCT_CASE_WORKER,
    SUPPLY_CHAIN_APPROVAL_MATRIX_POLICY,
    SUPPLY_CHAIN_BM04_SCHEMA,
    SUPPLY_CHAIN_BRIEF_POLICY,
    SUPPLY_CHAIN_FOLLOW_UP_POLICY,
    SUPPLY_CHAIN_ITEM_CODE_RULE,
    SUPPLY_CHAIN_PACKAGING_POLICY,
    SUPPLY_CHAIN_PRODUCT_ACTION_DUTIES,
    SUPPLY_CHAIN_PRODUCT_APPROVALS,
    SUPPLY_CHAIN_PRODUCT_SIGNOFF_WORKER,
    SUPPLY_CHAIN_SAMPLE_CRITERIA,
    SUPPLY_CHAIN_SLA_POLICY,
    SUPPLY_CHAIN_STEP_PREPARATION_POLICY,
    SUPPLY_CHAIN_STEP_PREPARATION_WORKER,
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
from dw_platform.adapters.persistence.approval_codes import SqlApprovalCodeStore
from dw_platform.adapters.persistence.approval_queries import SqlPendingApprovalQuery
from dw_platform.adapters.persistence.caching_lookup import CachingMembershipLookup
from dw_platform.adapters.persistence.channel_inbound import SqlChannelUpdateQueue
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
from dw_platform.adapters.persistence.support_grants import (
    SqlStaffGrants,
    SqlSupportGrantRepository,
)
from dw_platform.adapters.persistence.tenant_members import SqlTenantMembersRepository
from dw_platform.adapters.persistence.uow import SqlPlatformUnitOfWorkFactory
from dw_platform.adapters.persistence.zalo_link_repo import SqlZaloLink
from dw_platform.application.access_context import AccessContext
from dw_platform.application.admin_console import AdminConsoleService
from dw_platform.application.approval_codes import DecisionCodeKey
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
from dw_platform.application.support_access import (
    SupportAccessContextFactory,
    SupportGrantService,
)
from dw_platform.application.tenant_members import TenantMembersService

_LOG = logging.getLogger("dw_api.bootstrap")

_RUN_POLICY = load_worker_run_policy(WORKER_RUN_POLICY)

# Routes open to a support context (ADR 0024), as (method, path template).
# Empty on the platform: a context adds a route here in the same change as
# that route's negative test under a support grant. `test_support_routes.py`
# fails when RequireAccessContextOrSupport appears on any route not listed.
SUPPORT_ALLOWED_ROUTES: frozenset[tuple[str, str]] = frozenset()


def _asyncpg_dsn(url: str) -> str:
    """SQLAlchemy's URL minus the driver marker asyncpg does not understand."""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def build_container(settings: ApiSettings | None = None) -> ApiContainer:
    container = _build_container(settings)
    # Every context has registered its support scope sets by now (the seam
    # below); nothing may add one while the process serves requests.
    container.support_catalog.freeze()
    return container


def _build_container(settings: ApiSettings | None) -> ApiContainer:
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
    container.tenant_members = TenantMembersService(
        SqlTenantMembersRepository(session_factory), authorization, clock, ids
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
    container.support_grants = SupportGrantService(
        repo=SqlSupportGrantRepository(session_factory),
        member_scopes=SqlScopeHolders(session_factory),
        catalog=container.support_catalog,
        clock=clock,
        ids=ids,
    )
    staff_grants = SqlStaffGrants(session_factory)
    container.support_access = SupportAccessContextFactory(
        grants=staff_grants,
        member_scopes=SqlScopeHolders(session_factory),
        clock=clock,
        ids=ids,
    )
    container.support_access_audit = staff_grants
    container.staff_grants = staff_grants
    container.support_allowed_routes = SUPPORT_ALLOWED_ROUTES
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
    # The hosted way updates arrive (ADR 0008): queued here, handled by the
    # worker's drain through the same entry the poll lane uses.
    if settings.zalo_webhook_enabled:
        container.zalo_webhook_inbox = SqlChannelUpdateQueue(session_factory)

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
    # The portal half of a decision on Zalo (ADR 0007, channels Z5): the view
    # receipt and the single-use code. A context registers who answers for its
    # approval types' subject version on `approval_subjects` at the seam below
    # (`approval_subjects.register(prefix, port)`); a type nobody answers for
    # is decided on the web only.
    # The decision's own registry: a decision on a stamped subject version
    # reads the same ports a view receipt does (ticket ai-automation/05).
    approval_subjects = wiring.approval_flow.subjects
    code_secret = settings.approval_code_secret.get_secret_value()
    container.approval_views = ApprovalViewService(
        uow_factory=uow_factory,
        approval_flow=wiring.approval_flow,
        store=SqlApprovalCodeStore(session_factory),
        subjects=approval_subjects,
        chats=SqlZaloLink(session_factory),
        key=DecisionCodeKey(code_secret.encode()) if code_secret else None,
        clock=clock,
        ids=ids,
    )
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
    from dw_supply_chain.action_duties import load_supply_chain_action_duties
    from dw_supply_chain.adapters.persistence.delay_impact_repository import (
        SqlDelayImpactAnalysisRepository,
    )
    from dw_supply_chain.adapters.persistence.follow_up_repository import SqlFollowUpRepository
    from dw_supply_chain.adapters.persistence.packaging_design_repository import (
        SqlPackagingDesignRepository,
    )
    from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
    from dw_supply_chain.adapters.persistence.product_case_repository import (
        SqlProductCaseRepository,
    )
    from dw_supply_chain.adapters.persistence.supplier_update_repository import (
        SqlSupplierUpdateRepository,
    )
    from dw_supply_chain.application.case_query import AnswerCaseQuery
    from dw_supply_chain.application.handlers import (
        AdvancePOCase,
        AnalyzeDelayImpact,
        CloseFollowUp,
        CreatePO,
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
        ListPOCaseApprovals,
        ListPOCases,
        ListProductCategories,
        ListSupplierUpdates,
        ReassignPOCasePic,
        SetActionDutiesOverride,
        SetApprovalMatrixOverride,
        SetBriefPolicyOverride,
        SetFollowUpPolicyOverride,
        SetSLAPolicyOverride,
        SubmitSupplierUpdate,
        SummarizeDailyBrief,
    )
    from dw_supply_chain.application.product_cases import ListProductCases
    from dw_supply_chain.application.production_gate import ProductionGateResolver
    from dw_supply_chain.approval_matrix import load_supply_chain_approval_matrix
    from dw_supply_chain.brief_policy import load_supply_chain_brief_policy
    from dw_supply_chain.follow_up_policy import load_supply_chain_follow_up_policy
    from dw_supply_chain.packaging_policy import load_supply_chain_packaging_policy
    from dw_supply_chain.sla_policy import load_supply_chain_sla_policy
    from dw_supply_chain.workflows.advance_case_graph import (
        APPROVAL_TYPE_PREFIX,
        GRAPH_VERSION,
        WORKER_ID,
        build_advance_case_graph,
    )

    po_case_repo = SqlPOCaseRepository(wiring.seam.session_factory)
    # Product-development cases are read here by the brief and the command bar
    # (ticket 08); their steps and documents are wired below, with storage.
    product_case_repo = SqlProductCaseRepository(wiring.seam.session_factory)
    policy_override_repo = SqlPolicyOverrideRepository(wiring.seam.session_factory)
    platform_default_sla_policy = load_supply_chain_sla_policy(SUPPLY_CHAIN_SLA_POLICY)
    platform_default_approval_matrix = load_supply_chain_approval_matrix(
        SUPPLY_CHAIN_APPROVAL_MATRIX_POLICY
    )
    platform_default_brief_policy = load_supply_chain_brief_policy(SUPPLY_CHAIN_BRIEF_POLICY)
    platform_default_action_duties = load_supply_chain_action_duties(SUPPLY_CHAIN_ACTION_DUTIES)
    platform_default_packaging_policy = load_supply_chain_packaging_policy(
        SUPPLY_CHAIN_PACKAGING_POLICY
    )
    packaging_design_repo = SqlPackagingDesignRepository(wiring.seam.session_factory)
    # Step 13's gate (slice PK), one object for both doors to it: the direct
    # step and the approval graph's apply node.
    production_gate = ProductionGateResolver(
        designs=packaging_design_repo,
        policy_override_repo=policy_override_repo,
        platform_default=platform_default_packaging_policy,
    )
    supplier_update_repo = SqlSupplierUpdateRepository(wiring.seam.session_factory)
    delay_impact_repo = SqlDelayImpactAnalysisRepository(wiring.seam.session_factory)
    # Every Supply Chain model call is a one-call run with no runner around it,
    # so nothing else would ever free its spend-ledger entry (failure-modes #6),
    # and nothing else would check the tenant's plan day before it spends: the
    # same `DailyAllowance` the runner checks a run's start with.
    one_call_gateway = wiring.model_stack.one_call(
        DailyAllowance(
            allowance=entitlement,
            runs=run_store,
            spend=SqlSpendGuardStore(session_factory=session_factory),
            clock=clock,
        )
    )
    container.supply_chain_create_po_case = CreatePOCase(
        repo=po_case_repo,
        authz=authorization,
        ids=wiring.seam.ids,
        clock=wiring.seam.clock,
        policy_override_repo=policy_override_repo,
        platform_default_sla_policy=platform_default_sla_policy,
    )
    # Who is a member of a workspace now: a new PIC must be (ticket 06).
    scope_holders = SqlScopeHolders(wiring.seam.session_factory)
    container.supply_chain_reassign_po_case_pic = ReassignPOCasePic(
        repo=po_case_repo,
        members=scope_holders,
        authz=authorization,
        ids=wiring.seam.ids,
        clock=wiring.seam.clock,
    )
    container.supply_chain_create_po = CreatePO(
        repo=po_case_repo,
        authz=authorization,
        policy_override_repo=policy_override_repo,
        platform_default_action_duties=platform_default_action_duties,
        holders=SqlScopeHolders(wiring.seam.session_factory),
        notifier=SqlNotificationRepository(wiring.seam.session_factory),
        ids=wiring.seam.ids,
        clock=wiring.seam.clock,
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
        clock=wiring.seam.clock,
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
        clock=wiring.seam.clock,
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
        WORKER_ID,
        GRAPH_VERSION,
        lambda: build_advance_case_graph(
            po_case_repo, wiring.seam.ids, wiring.seam.clock, production_gate
        ),
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
        production_gate=production_gate,
        ids=wiring.seam.ids,
        clock=wiring.seam.clock,
    )
    container.supply_chain_list_case_transitions = ListCaseTransitions(
        repo=po_case_repo, authz=authorization
    )
    container.supply_chain_list_case_approvals = ListPOCaseApprovals(
        repo=po_case_repo,
        pending_approvals=SqlPendingApprovalQuery(wiring.seam.session_factory, authorization),
        authz=authorization,
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
    # The SAME list handlers the routes use: one place decides what a filter
    # means and who may read the result.
    list_product_cases = ListProductCases(repo=product_case_repo, authz=authorization)
    list_product_categories = ListProductCategories(
        policy_override_repo=policy_override_repo,
        platform_default_sla_policy=platform_default_sla_policy,
        authz=authorization,
    )
    container.supply_chain_answer_case_query = AnswerCaseQuery(
        po_case_repo=po_case_repo,
        list_cases=list_po_cases,
        product_cases=product_case_repo,
        list_product_cases=list_product_cases,
        categories=list_product_categories,
        # The platform's directory: who a PIC named in a question may be.
        directory=SqlWorkspaceDirectory(wiring.seam.session_factory),
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
        product_case_repo=product_case_repo,
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
    platform_default_follow_up_policy = load_supply_chain_follow_up_policy(
        SUPPLY_CHAIN_FOLLOW_UP_POLICY
    )
    container.supply_chain_get_follow_up_policy = GetFollowUpPolicy(
        policy_override_repo=policy_override_repo,
        platform_default_policy=platform_default_follow_up_policy,
        authz=authorization,
    )
    container.supply_chain_set_follow_up_policy_override = SetFollowUpPolicyOverride(
        policy_override_repo=policy_override_repo,
        platform_default_policy=platform_default_follow_up_policy,
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
    from dw_supply_chain.adapters.storage.minio_case_documents import (
        MinioCaseDocumentStorage,
    )
    from dw_supply_chain.application.approval_subject import ProductCaseApprovalSubject
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
    from dw_supply_chain.application.packaging_designs import (
        GetPackagingDesign,
        GetPackagingPolicy,
        SetPackagingPolicyOverride,
        TakePackagingStep,
    )
    from dw_supply_chain.application.product_cases import (
        AdvanceProductCase,
        GetProductCase,
        ListProductCaseTransitions,
        PlaceOrder,
        ProposeProductCase,
        ReassignProductCasePic,
    )
    from dw_supply_chain.application.product_reviews import EnsureProductApproval
    from dw_supply_chain.domain.case_document import CaseKind
    from dw_supply_chain.product_action_duties import load_supply_chain_product_action_duties
    from dw_supply_chain.product_approvals import load_supply_chain_product_approvals
    from dw_supply_chain.workflows import advance_product_case_graph as product_review_graph
    from dw_supply_chain.workflows import product_signoff_graph

    document_repo = SqlCaseDocumentRepository(wiring.seam.session_factory)
    document_storage = MinioCaseDocumentStorage(client=minio, bucket=settings.case_documents_bucket)
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
    # Step 12's sub-flow (slice PK): its test steps take a report from these
    # documents, read under the caller's RLS.
    container.supply_chain_get_packaging_design = GetPackagingDesign(
        po_cases=po_case_repo,
        designs=packaging_design_repo,
        authz=authorization,
        policy_override_repo=policy_override_repo,
        platform_default_duties=platform_default_action_duties,
        platform_default_policy=platform_default_packaging_policy,
    )
    container.supply_chain_take_packaging_step = TakePackagingStep(
        po_cases=po_case_repo,
        designs=packaging_design_repo,
        documents=document_repo,
        authz=authorization,
        policy_override_repo=policy_override_repo,
        platform_default_duties=platform_default_action_duties,
        notifier=SqlNotificationRepository(wiring.seam.session_factory),
        ids=wiring.seam.ids,
        clock=wiring.seam.clock,
    )
    container.supply_chain_get_packaging_policy = GetPackagingPolicy(
        policy_override_repo=policy_override_repo,
        platform_default_policy=platform_default_packaging_policy,
        authz=authorization,
    )
    container.supply_chain_set_packaging_policy_override = SetPackagingPolicyOverride(
        policy_override_repo=policy_override_repo,
        authz=authorization,
        ids=wiring.seam.ids,
        clock=wiring.seam.clock,
    )
    # Commercial data and BM04 as fields (ADR 0026, ticket ai-automation/01):
    # prices behind `supply_chain.commercial.*`, read and written only here.
    from dw_supply_chain.adapters.persistence.commercial_repository import (
        SqlPOCommercialRepository,
        SqlProductProfileRepository,
        SqlSupplierRecords,
    )
    from dw_supply_chain.application import commercial as sc_commercial
    from dw_supply_chain.bm04_schema import load_supply_chain_bm04_schema
    from dw_supply_chain.presentation.commercial_routes import CommercialHandlers

    bm04_schemas = sc_commercial.Bm04SchemaSource(
        policy_override_repo=policy_override_repo,
        platform_default=load_supply_chain_bm04_schema(SUPPLY_CHAIN_BM04_SCHEMA),
    )
    profiles = SqlProductProfileRepository(wiring.seam.session_factory)
    po_commercial = SqlPOCommercialRepository(wiring.seam.session_factory)
    supplier_records = SqlSupplierRecords(wiring.seam.session_factory)
    # Document drafts and templates (ticket ai-automation/03): the platform's
    # templates load here, once, and fail the start if one is malformed; a
    # tenant's own versions load from storage the first time it asks.
    from dw_agent_runtime.adapters.docx_templates import DocxRenderer, DocxTemplateInspector
    from dw_agent_runtime.doc_templates import DocTemplateRegistry
    from dw_supply_chain.adapters.persistence.document_draft_repository import (
        SqlDocTemplateOverrides,
        SqlDocumentDraftRepository,
    )
    from dw_supply_chain.application import document_drafts as sc_drafts
    from dw_supply_chain.presentation.draft_routes import DraftHandlers

    template_inspector = DocxTemplateInspector()
    template_registry = DocTemplateRegistry(inspector=template_inspector)
    template_registry.load_directory(DOC_TEMPLATES_DIR)
    template_overrides = SqlDocTemplateOverrides(wiring.seam.session_factory)
    tenant_templates = sc_drafts.TenantDocTemplates(
        registry=template_registry, overrides=template_overrides
    )
    draft_repo = SqlDocumentDraftRepository(wiring.seam.session_factory)
    container.supply_chain_drafts = DraftHandlers(
        list_drafts=sc_drafts.ListCaseDrafts(
            cases=case_lookups, drafts=draft_repo, templates=tenant_templates, authz=authorization
        ),
        get_draft=sc_drafts.GetDocumentDraft(
            drafts=draft_repo, templates=tenant_templates, authz=authorization
        ),
        revise=sc_drafts.ReviseDocumentDraft(
            drafts=draft_repo,
            templates=tenant_templates,
            authz=authorization,
            ids=wiring.seam.ids,
            clock=wiring.seam.clock,
        ),
        reject=sc_drafts.RejectDocumentDraft(
            drafts=draft_repo,
            templates=tenant_templates,
            authz=authorization,
            ids=wiring.seam.ids,
            clock=wiring.seam.clock,
        ),
        render=sc_drafts.RenderDocumentDraft(
            drafts=draft_repo,
            templates=tenant_templates,
            renderer=DocxRenderer(),
            authz=authorization,
        ),
        list_templates=sc_drafts.ListDocTemplates(
            registry=template_registry, overrides=template_overrides, authz=authorization
        ),
        set_template=sc_drafts.SetDocTemplateOverride(
            registry=template_registry,
            inspector=template_inspector,
            overrides=template_overrides,
            authz=authorization,
            ids=wiring.seam.ids,
            clock=wiring.seam.clock,
        ),
    )
    container.supply_chain_commercial = CommercialHandlers(
        get_profile=sc_commercial.GetProductProfile(
            cases=product_case_repo, profiles=profiles, schemas=bm04_schemas, authz=authorization
        ),
        save_profile=sc_commercial.SaveProductProfile(
            cases=product_case_repo,
            profiles=profiles,
            schemas=bm04_schemas,
            authz=authorization,
            ids=wiring.seam.ids,
            clock=wiring.seam.clock,
        ),
        get_schema=sc_commercial.GetBm04Schema(schemas=bm04_schemas, authz=authorization),
        set_schema=sc_commercial.SetBm04SchemaOverride(
            policy_override_repo=policy_override_repo,
            authz=authorization,
            ids=wiring.seam.ids,
            clock=wiring.seam.clock,
        ),
        get_commercial=sc_commercial.GetPOCommercial(
            cases=po_case_repo, commercial=po_commercial, authz=authorization
        ),
        set_commercial=sc_commercial.SetPOCommercialTerms(
            cases=po_case_repo,
            commercial=po_commercial,
            authz=authorization,
            ids=wiring.seam.ids,
            clock=wiring.seam.clock,
        ),
        record_payment=sc_commercial.RecordPOPayment(
            cases=po_case_repo,
            commercial=po_commercial,
            documents=document_repo,
            authz=authorization,
            ids=wiring.seam.ids,
            clock=wiring.seam.clock,
        ),
        list_suppliers=sc_commercial.ListSuppliers(suppliers=supplier_records, authz=authorization),
        get_contact=sc_commercial.GetSupplierContact(
            contacts=supplier_records, authz=authorization
        ),
        save_contact=sc_commercial.SaveSupplierContact(
            contacts=supplier_records,
            authz=authorization,
            ids=wiring.seam.ids,
            clock=wiring.seam.clock,
        ),
        get_bank_account=sc_commercial.GetSupplierBankAccount(
            accounts=supplier_records, authz=authorization
        ),
        save_bank_account=sc_commercial.SaveSupplierBankAccount(
            accounts=supplier_records,
            authz=authorization,
            ids=wiring.seam.ids,
            clock=wiring.seam.clock,
        ),
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
    # The step-9 sign-off (ticket 04): its own graph and worker, under the same
    # strict prefix and the same case-version port as the review.
    wiring.seam.graphs.register(
        product_signoff_graph.WORKER_ID,
        product_signoff_graph.GRAPH_VERSION,
        lambda: product_signoff_graph.build_product_signoff_graph(
            product_case_repo, wiring.seam.ids, wiring.seam.clock
        ),
    )
    wiring.seam.workers.load_file(SUPPLY_CHAIN_PRODUCT_SIGNOFF_WORKER)
    wiring.approval_flow.strict_approval_prefixes = (
        wiring.approval_flow.strict_approval_prefixes
        | frozenset({product_review_graph.APPROVAL_TYPE_PREFIX})
    )
    # Steps 6 and 9 may be decided on Zalo after a view (ADR 0014): the case's
    # version is what a view saw and a decision must still find.
    approval_subjects.register(
        product_review_graph.APPROVAL_TYPE_PREFIX, ProductCaseApprovalSubject(product_case_repo)
    )
    # The approval inbox, read through the narrow Protocol the context declares.
    pending_approvals = SqlPendingApprovalQuery(wiring.seam.session_factory, authorization)
    product_reviews = EnsureProductApproval(
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
        platform_default_sla_policy=platform_default_sla_policy,
        ids=wiring.seam.ids,
        clock=wiring.seam.clock,
    )
    container.supply_chain_get_product_case = GetProductCase(
        repo=product_case_repo,
        authz=authorization,
        policy_override_repo=policy_override_repo,
        platform_default_duties=platform_default_product_duties,
        platform_default_sla_policy=platform_default_sla_policy,
        approvals=pending_approvals,
        clock=wiring.seam.clock,
    )
    container.supply_chain_reassign_product_case_pic = ReassignProductCasePic(
        repo=product_case_repo,
        members=scope_holders,
        authz=authorization,
        ids=wiring.seam.ids,
        clock=wiring.seam.clock,
    )
    container.supply_chain_list_product_categories = list_product_categories
    container.supply_chain_list_product_cases = list_product_cases
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
    container.supply_chain_place_order = PlaceOrder(
        repo=product_case_repo,
        authz=authorization,
        policy_override_repo=policy_override_repo,
        platform_default_duties=platform_default_product_duties,
        platform_default_action_duties=platform_default_action_duties,
        holders=SqlScopeHolders(wiring.seam.session_factory),
        notifier=SqlNotificationRepository(wiring.seam.session_factory),
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

    # Step proposals (ADR 0025, ticket ai-automation/05): the worker's lane
    # starts the preparation runs; this process hosts the same graph so a
    # decision on the web resumes the run here. Strict like the product
    # prefix (a decision needs a comment, the requester, the lane, cannot
    # decide), and its subject version is what a decision must still find.
    from dw_supply_chain.adapters.persistence.document_extraction_repository import (
        SqlExtractionReadings,
    )
    from dw_supply_chain.adapters.persistence.step_preparation_repository import (
        SqlPreparationRecords,
        SqlProposalOutcomes,
    )
    from dw_supply_chain.application import step_preparation as sc_preparation
    from dw_supply_chain.application import step_proposals as sc_proposals
    from dw_supply_chain.application.bm04_prefill import Bm04Preparation, Bm04ProfileWriter
    from dw_supply_chain.domain.step_proposal import STEP_PROPOSAL_PREFIX
    from dw_supply_chain.presentation.step_proposal_routes import StepProposalHandlers
    from dw_supply_chain.step_preparation_policy import load_supply_chain_step_preparation
    from dw_supply_chain.workflows import step_preparation_graph

    platform_default_step_preparation = load_supply_chain_step_preparation(
        SUPPLY_CHAIN_STEP_PREPARATION_POLICY
    )
    preparation_records = SqlPreparationRecords(wiring.seam.session_factory)
    # A sample round (ticket ai-automation/09): its criteria and R&D's
    # measurements, which a proposal's subject binds too. No model here: the
    # worker's lane prepares; this process only resumes a decided run.
    from dw_supply_chain.adapters.persistence.sample_measurement_repository import (
        SqlSampleMeasurements,
    )
    from dw_supply_chain.sample_criteria_policy import load_supply_chain_sample_criteria

    measurements = SqlSampleMeasurements(wiring.seam.session_factory)
    sample_preparation = sc_preparation.SamplePreparation(
        measurements=measurements,
        policy_override_repo=policy_override_repo,
        platform_default_criteria=load_supply_chain_sample_criteria(SUPPLY_CHAIN_SAMPLE_CRITERIA),
    )
    bm04_preparation = Bm04Preparation(schemas=bm04_schemas, profiles=profiles)
    # Step 9 (ticket ai-automation/13): the tenant's code rule, the codes the
    # application and the imported catalogue hold, and the BM04's variants.
    from dw_supply_chain.adapters.persistence.code_registry import SqlCodeRegistry
    from dw_supply_chain.application import item_coding as sc_coding
    from dw_supply_chain.item_code_rule_policy import load_supply_chain_item_code_rule

    coding_preparation = sc_coding.ItemCodingPreparation(
        codes=SqlCodeRegistry(wiring.seam.session_factory),
        profiles=profiles,
        policy_override_repo=policy_override_repo,
        platform_default_rule=load_supply_chain_item_code_rule(SUPPLY_CHAIN_ITEM_CODE_RULE),
    )
    proposal_subject = sc_proposals.StepProposalSubject(
        cases=product_case_repo,
        drafts=draft_repo,
        documents=document_repo,
        sample=sample_preparation,
        bm04=bm04_preparation,
        coding=coding_preparation,
    )
    preparer = sc_preparation.PrepareStep(
        cases=product_case_repo,
        documents=document_repo,
        readings=SqlExtractionReadings(wiring.seam.session_factory),
        drafts=draft_repo,
        prepare_draft=sc_drafts.PrepareDocumentDraft(
            cases=case_lookups,
            drafts=draft_repo,
            templates=tenant_templates,
            ids=wiring.seam.ids,
            clock=wiring.seam.clock,
        ),
        templates=tenant_templates,
        records=preparation_records,
        ids=wiring.seam.ids,
        clock=wiring.seam.clock,
        sample=sample_preparation,
        bm04=bm04_preparation,
        coding=coding_preparation,
    )
    applier = sc_proposals.ApplyStepProposal(
        cases=product_case_repo,
        drafts=draft_repo,
        documents=document_repo,
        templates=tenant_templates,
        renderer=DocxRenderer(),
        storage=document_storage,
        subject=proposal_subject,
        outcomes=SqlProposalOutcomes(wiring.seam.session_factory),
        ids=wiring.seam.ids,
        clock=wiring.seam.clock,
        # A confirmed BM04 (step 7, ticket ai-automation/11) becomes a profile
        # version with the step, priced only for a decider who may set prices.
        profiles=Bm04ProfileWriter(
            schemas=bm04_schemas, profiles=profiles, authz=authorization, ids=wiring.seam.ids
        ),
        coding=coding_preparation,
    )
    wiring.seam.graphs.register(
        step_preparation_graph.WORKER_ID,
        step_preparation_graph.GRAPH_VERSION,
        lambda: step_preparation_graph.build_step_preparation_graph(preparer, applier),
    )
    wiring.seam.workers.load_file(SUPPLY_CHAIN_STEP_PREPARATION_WORKER)
    wiring.approval_flow.strict_approval_prefixes = (
        wiring.approval_flow.strict_approval_prefixes | frozenset({STEP_PROPOSAL_PREFIX})
    )
    approval_subjects.register(STEP_PROPOSAL_PREFIX, proposal_subject)
    container.supply_chain_step_proposals = StepProposalHandlers(
        get_proposal=sc_proposals.GetStepProposal(
            cases=product_case_repo,
            records=preparation_records,
            approvals=pending_approvals,
            subject=proposal_subject,
            templates=tenant_templates,
            policy_override_repo=policy_override_repo,
            platform_default_policy=platform_default_step_preparation,
            authz=authorization,
        ),
        decide=sc_proposals.DecideStepProposal(
            cases=product_case_repo,
            approvals=pending_approvals,
            templates=tenant_templates,
            decisions=PlatformStepDecisions(wiring.approval_flow, authorization),
            policy_override_repo=policy_override_repo,
            platform_default_policy=platform_default_step_preparation,
        ),
        get_policy=sc_proposals.GetStepPreparationPolicy(
            policy_override_repo=policy_override_repo,
            platform_default_policy=platform_default_step_preparation,
            authz=authorization,
        ),
        set_policy=sc_proposals.SetStepPreparationPolicyOverride(
            policy_override_repo=policy_override_repo,
            authz=authorization,
            ids=wiring.seam.ids,
            clock=wiring.seam.clock,
        ),
        get_item_code_rule=sc_coding.GetItemCodeRulePolicy(
            policy_override_repo=policy_override_repo,
            platform_default_rule=coding_preparation.platform_default_rule,
            authz=authorization,
        ),
        set_item_code_rule=sc_coding.SetItemCodeRulePolicyOverride(
            policy_override_repo=policy_override_repo,
            authz=authorization,
            ids=wiring.seam.ids,
            clock=wiring.seam.clock,
        ),
    )

    # Step 10 (ticket ai-automation/14): the worker's lane drafts the PO;
    # this process shows what code finds in it and takes Cung ứng's approval
    # (create_po with the draft, its terms and prices, one transaction).
    from dw_supply_chain.adapters.persistence.purchase_order_outcomes import (
        SqlPurchaseOrderOutcomes,
    )
    from dw_supply_chain.application import purchase_orders as sc_purchase_orders
    from dw_supply_chain.presentation.purchase_order_routes import PurchaseOrderHandlers

    container.supply_chain_purchase_orders = PurchaseOrderHandlers(
        get=sc_purchase_orders.GetPurchaseOrderProposal(
            cases=po_case_repo,
            commercial=po_commercial,
            sources=sc_purchase_orders.PurchaseOrderSources(
                profiles=profiles,
                documents=document_repo,
                readings=SqlExtractionReadings(wiring.seam.session_factory),
            ),
            drafts=draft_repo,
            policy_override_repo=policy_override_repo,
            platform_default_duties=platform_default_action_duties,
            authz=authorization,
            clock=wiring.seam.clock,
        ),
        approve=sc_purchase_orders.ApprovePurchaseOrder(
            cases=po_case_repo,
            drafts=draft_repo,
            templates=tenant_templates,
            renderer=DocxRenderer(),
            storage=document_storage,
            outcomes=SqlPurchaseOrderOutcomes(wiring.seam.session_factory),
            policy_override_repo=policy_override_repo,
            platform_default_duties=platform_default_action_duties,
            holders=SqlScopeHolders(wiring.seam.session_factory),
            notifier=SqlNotificationRepository(wiring.seam.session_factory),
            authz=authorization,
            ids=wiring.seam.ids,
            clock=wiring.seam.clock,
        ),
    )

    # Messages to a supplier (ADR 0029, ticket ai-automation/07): the worker's
    # lane drafts them; this process lists them and records "Đã gửi". Nothing
    # here sends anything.
    from dw_supply_chain.adapters.persistence.supplier_message_repository import (
        SqlSupplierMessageRepository,
    )
    from dw_supply_chain.application import supplier_messages as sc_messages
    from dw_supply_chain.presentation.supplier_message_routes import SupplierMessageHandlers

    message_repo = SqlSupplierMessageRepository(wiring.seam.session_factory)
    container.supply_chain_supplier_messages = SupplierMessageHandlers(
        list_messages=sc_messages.ListSupplierMessages(
            cases=case_lookups, messages=message_repo, authz=authorization
        ),
        mark_sent=sc_messages.MarkSupplierMessageSent(
            messages=message_repo,
            authz=authorization,
            ids=wiring.seam.ids,
            clock=wiring.seam.clock,
        ),
    )

    # Step 1 from a list (ticket ai-automation/08): the worker's lane reads an
    # uploaded list; a row becomes a case only through `propose`, as the PIC.
    from dw_supply_chain.adapters.persistence.proposal_list_repository import SqlProposalLists
    from dw_supply_chain.application import proposal_lists as sc_lists
    from dw_supply_chain.presentation.proposal_list_routes import ProposalListHandlers

    list_repo = SqlProposalLists(wiring.seam.session_factory)
    propose_handler = container.supply_chain_propose_product_case
    assert propose_handler is not None
    container.supply_chain_proposal_lists = ProposalListHandlers(
        upload=sc_lists.UploadProposalList(
            lists=list_repo,
            propose=propose_handler,
            authz=authorization,
            ids=wiring.seam.ids,
            clock=wiring.seam.clock,
        ),
        list_recent=sc_lists.ListProposalLists(lists=list_repo, authz=authorization),
        get=sc_lists.GetProposalList(lists=list_repo, authz=authorization),
        propose=sc_lists.ProposeFromList(
            lists=list_repo,
            propose=propose_handler,
            authz=authorization,
            ids=wiring.seam.ids,
            clock=wiring.seam.clock,
        ),
        drop=sc_lists.DropFromList(
            lists=list_repo,
            propose=propose_handler,
            authz=authorization,
            ids=wiring.seam.ids,
            clock=wiring.seam.clock,
        ),
    )

    # A sample round's checklist (ticket ai-automation/09): R&D enters what it
    # measured; the comparison is the one the preparation reads.
    from dw_supply_chain.application import sample_checklist as sc_checklist
    from dw_supply_chain.presentation.sample_checklist_routes import SampleChecklistHandlers

    record_measurement = sc_checklist.RecordMeasurement(
        cases=product_case_repo,
        store=measurements,
        sample=sample_preparation,
        authz=authorization,
        platform_default_duties=load_supply_chain_product_action_duties(
            SUPPLY_CHAIN_PRODUCT_ACTION_DUTIES
        ),
        ids=wiring.seam.ids,
        clock=wiring.seam.clock,
    )
    container.supply_chain_sample_checklist = SampleChecklistHandlers(
        get_checklist=sc_checklist.GetSampleChecklist(
            cases=product_case_repo, record=record_measurement, authz=authorization
        ),
        record=record_measurement,
        get_policy=sc_checklist.GetSampleCriteriaPolicy(
            policy_override_repo=policy_override_repo,
            platform_default_criteria=sample_preparation.platform_default_criteria,
            authz=authorization,
        ),
        set_policy=sc_checklist.SetSampleCriteriaPolicyOverride(
            policy_override_repo=policy_override_repo,
            authz=authorization,
            ids=wiring.seam.ids,
            clock=wiring.seam.clock,
        ),
    )

    # The one-time import (ADR 0027, ticket onboarding/01): suppliers with
    # their contact and bank account through the commercial handlers, the
    # catalogue, and users through the platform's own member services.
    from dw_supply_chain.adapters.import_workbook import XlsxWorkbookReader, build_template
    from dw_supply_chain.adapters.persistence.data_import_repository import (
        SqlCatalogue,
        SqlSupplierImport,
    )
    from dw_supply_chain.adapters.platform_members import PlatformMemberDirectory
    from dw_supply_chain.application import data_import as sc_import
    from dw_supply_chain.presentation.import_routes import ImportHandlers

    assert container.admin_console is not None and container.tenant_members is not None
    container.supply_chain_import = ImportHandlers(
        run=sc_import.ImportSupplyChainData(
            reader=XlsxWorkbookReader(),
            suppliers=SqlSupplierImport(wiring.seam.session_factory),
            contacts=supplier_records,
            accounts=supplier_records,
            save_contact=sc_commercial.SaveSupplierContact(
                contacts=supplier_records,
                authz=authorization,
                ids=wiring.seam.ids,
                clock=wiring.seam.clock,
            ),
            save_account=sc_commercial.SaveSupplierBankAccount(
                accounts=supplier_records,
                authz=authorization,
                ids=wiring.seam.ids,
                clock=wiring.seam.clock,
            ),
            catalogue=SqlCatalogue(wiring.seam.session_factory),
            members=PlatformMemberDirectory(
                console=container.admin_console, members=container.tenant_members
            ),
            authz=authorization,
            ids=wiring.seam.ids,
            clock=wiring.seam.clock,
        ),
        template=sc_import.GetImportTemplate(authz=authorization, build=build_template),
    )

    # Build your context from `container.runtime` (the RuntimeSeam) and attach
    # its handlers, then mount its router in `main.create_app`. Nothing above
    # this line may import a business package. A context offering support
    # access registers here too: `container.support_catalog.register(...)`
    # and `.register_resource(...)` (ADR 0024).

    return container


@dataclass(frozen=True)
class PlatformStepDecisions:
    """`StepDecisionPort` over the platform's decision, with this
    deployment's authorization: the one place a step proposal is decided."""

    flow: ApproveAndResumeService
    authorization: ScopeAuthorizationService

    async def decide(
        self,
        context: AccessContext,
        *,
        approval_id: uuid.UUID,
        approve: bool,
        comment: str,
        typed_input: Mapping[str, str] | None,
    ) -> None:
        await self.flow.decide(
            approval_id=approval_id,
            approve=approve,
            comment=comment,
            context=context,
            authorization=self.authorization,
            typed_input=typed_input,
        )


def build_engine(url: str, *, pool_pre_ping: bool = True) -> AsyncEngine:
    """Shared engine construction, for processes that need one outside the API."""
    return create_async_engine(url, pool_pre_ping=pool_pre_ping)
