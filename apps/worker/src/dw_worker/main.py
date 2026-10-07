"""Worker main loop: runs registered consumers and keeps the heartbeat fresh.

The loop is the whole of this module's job. Each consumer is an ``async``
callable that does one tick of work and returns; the loop decides how often to
call it, catches what it raises, and stops every one of them on a single
shutdown event. A consumer that sleeps inside itself would be a consumer the
shutdown event cannot wake, so cadence is declared on the registry instead.

## Plugging in a bounded context

``build_registry`` wires only the platform lanes. A context adds its own at the
marked seam: build its components from settings, then
``registry.register("<name>", consumer, interval_seconds=...)``. If it owns job
queues, append a ``ReapTarget`` for each so abandoned rows are settled here
rather than in a second sweeper; if it has retention rules, satisfy
``RetentionPrunePort``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from dw_agent_runtime.adapters.checkpoint_retention import SqlCheckpointRetention
from dw_agent_runtime.adapters.langgraph_runner import LangGraphWorkflowRunner
from dw_agent_runtime.adapters.model_stack import ModelStack
from dw_agent_runtime.adapters.run_store import SqlWorkerRunStore
from dw_agent_runtime.adapters.spend_guard import SqlSpendGuardRetention, SqlSpendGuardStore
from dw_agent_runtime.allowance import DailyAllowance
from dw_agent_runtime.approval_flow import ApproveAndResumeService
from dw_agent_runtime.channel_decisions import (
    ChannelApprovalDecisionService,
    ChannelDecisionCommand,
)
from dw_agent_runtime.model.run_policy import load_worker_run_policy
from dw_agent_runtime.ports import ModelGateway, RunAllowancePort
from dw_agent_runtime.release import UNRELEASED, release_manifest_ref
from dw_connectors.adapters.zalo_bot import ZaloBotClient
from dw_connectors.adapters.zalo_inbound import ZaloInbound
from dw_connectors.adapters.zalo_link import link_help
from dw_connectors.inbound import ChannelCommandRegistry, InboundRouter
from dw_connectors.ports import ChatSenderPort
from dw_kernel.ports import IdGenerator, SystemClock, UtcClock, Uuid7Generator
from dw_knowledge.adapters.evidence_store import SqlEvidenceStore
from dw_knowledge.retention import SqlKnowledgeRetention
from dw_memory.policy import MemoryWritePolicy
from dw_memory.retention import SqlMemoryRetention
from dw_memory.service import MemoryService
from dw_observability.otel import build_telemetry
from dw_observability.telemetry import TelemetryPort
from dw_platform.adapters.persistence.approval_codes import (
    SqlApprovalCodeRetention,
    SqlApprovalCodeStore,
)
from dw_platform.adapters.persistence.channel_deliveries import (
    SqlChannelDeliveryRetention,
    SqlChannelOutbox,
)
from dw_platform.adapters.persistence.channel_inbound import (
    SqlChannelInboundLedger,
    SqlChannelInboundRetention,
)
from dw_platform.adapters.persistence.channel_preferences import SqlChannelPreferences
from dw_platform.adapters.persistence.membership_lookup import SqlMembershipLookup
from dw_platform.adapters.persistence.notifications import SqlNotificationRetention
from dw_platform.adapters.persistence.offboarding import SqlTenantOffboarding
from dw_platform.adapters.persistence.outbox_drain import SqlOutboxDrain
from dw_platform.adapters.persistence.partition_maintenance import SqlPartitionMaintenance
from dw_platform.adapters.persistence.uow import SqlPlatformUnitOfWorkFactory
from dw_platform.adapters.persistence.zalo_link_repo import (
    SqlChannelLinkNonceRetention,
    SqlZaloLink,
)
from dw_platform.application.access_context import AccessContext
from dw_platform.application.approval_codes import ApprovalSubjectVersions, DecisionCodeKey
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.channel_access import LinkedUserAccess
from dw_platform.application.entitlement import DEFAULT_PLANS, PlanEntitlementService
from dw_platform.retention_policy import load_retention_policy
from dw_worker.composition import (
    REPO_ROOT,
    build_case_document_storage,
    build_case_documents_bucket,
    build_embeddings,
    build_export_bucket,
    build_feedback_bucket,
    build_ingest_components,
    build_model_stack_for,
    build_object_storage,
    build_vector_index,
)
from dw_worker.consumers import ConsumerRegistry
from dw_worker.consumers.channel_delivery import build_channel_delivery_consumer
from dw_worker.consumers.ingest import build_ingest_consumer
from dw_worker.consumers.memory import memory_handlers
from dw_worker.consumers.offboarding import INTERVAL_SECONDS as OFFBOARDING_INTERVAL_SECONDS
from dw_worker.consumers.offboarding import TenantOffboardingLane, build_offboarding_consumer
from dw_worker.consumers.outbox import EventHandler, build_outbox_consumer
from dw_worker.consumers.reaper import INTERVAL_SECONDS as REAP_INTERVAL_SECONDS
from dw_worker.consumers.reaper import ReapTarget, build_reaper_consumer
from dw_worker.consumers.retention import INTERVAL_SECONDS as RETENTION_INTERVAL_SECONDS
from dw_worker.consumers.retention import RetentionPrunePort, build_retention_consumer
from dw_worker.consumers.supply_chain import (
    build_document_orphan_sweep,
    build_follow_up_consumer,
    build_follow_up_retention,
    build_follow_up_sweep,
    build_product_review_reconcile,
    build_product_review_reconcile_consumer,
    build_product_review_runner,
    build_proposal_draft_retention,
    build_stage_one_report,
    build_stage_one_report_consumer,
    build_zalo_case_query_command,
    build_zalo_proposal_command,
    register_product_approvals,
)
from dw_worker.consumers.zalo_poll import build_zalo_poll_consumer
from dw_worker.health import beat
from dw_worker.settings import WorkerSettings

if TYPE_CHECKING:
    from dw_memory.adapters.qdrant_ranker import QdrantMemoryRanker

logger = logging.getLogger("dw_worker")

# A module constant so the test holding this file to the classes code can
# assign reads the file the sweeps below read, not a copy of its name.
RETENTION_POLICY_PATH = REPO_ROOT / "configs" / "policies" / "retention@1.6.0.yaml"
# The run store's staleness, the file the reconcile lane's runner reads too.
WORKER_RUN_POLICY = "worker_runs@1.0.0.yaml"


def _build_worker_telemetry(settings: WorkerSettings) -> TelemetryPort:
    return build_telemetry(
        service_name="dw-worker",
        langfuse_enabled=settings.langfuse_enabled,
        langfuse_host=settings.langfuse_host,
        langfuse_public_key=settings.langfuse_public_key,
        langfuse_secret_key=settings.langfuse_secret_key,
        otel_endpoint=settings.otel_endpoint,
    )


def _build_memory_vectors(settings: WorkerSettings) -> QdrantMemoryRanker | None:
    """The memory ranker's store: written by the outbox handler, purged by
    supersession, retention and offboarding. One instance for all four, so
    the collection that is written is the collection that is purged.

    Imported inside the function so a deployment without Qdrant need not have
    the client installed to boot — the same rule the knowledge index follows."""
    if not settings.qdrant_url:
        return None
    from qdrant_client import AsyncQdrantClient

    from dw_memory.adapters.qdrant_ranker import QdrantMemoryRanker

    return QdrantMemoryRanker(
        client=AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key),
        embedder=build_embeddings(settings),
    )


def release_manifest_ref_for(settings: WorkerSettings, repo_root: Path) -> str:
    """The release this process serves, stamped on the runs it starts, read
    by the reader the API uses (`dw_agent_runtime.release`).

    A deployed worker refuses to start without it: the fallback would stamp
    `unreleased` on every run the BGĐ review reconcile starts, and nothing
    would say so. The image ships `contracts/release` for this."""
    ref = release_manifest_ref(repo_root)
    if settings.is_deployed and ref == UNRELEASED:
        raise RuntimeError(
            f"no release manifest under {repo_root} in the {settings.profile} profile:"
            " a run this worker starts would record no release"
        )
    return ref


def build_one_call_gateway(
    stack: ModelStack,
    sessions: async_sessionmaker[AsyncSession],
    *,
    allowance: RunAllowancePort,
    clock: UtcClock,
) -> ModelGateway:
    """The gateway a chat command reads one message with: the stack the model
    builder made (`build_model_stack_for`, the API's builder too), checked
    against the tenant's plan day — runs and spend — by the same
    `DailyAllowance` the runner checks a run's start with, before any call."""
    run_policy = load_worker_run_policy(REPO_ROOT / "configs" / "policies" / WORKER_RUN_POLICY)
    return stack.one_call(
        DailyAllowance(
            allowance=allowance,
            runs=SqlWorkerRunStore(
                sessions, stale_run_after_seconds=run_policy.stale_run_after_seconds
            ),
            spend=SqlSpendGuardStore(session_factory=sessions),
            clock=clock,
        )
    )


def build_channel_decision_command(
    settings: WorkerSettings,
    sessions: async_sessionmaker[AsyncSession],
    *,
    runner: LangGraphWorkflowRunner,
    ids: IdGenerator,
    clock: UtcClock,
) -> ChannelDecisionCommand:
    """`DUYỆT <mã>` / `KHÔNG <mã> <lý do>` from a linked chat (ADR 0014,
    zalo-channel ticket 05).

    The decision goes through this process's own `ApproveAndResumeService`,
    over `runner`: the one that hosts the review graph, so a decided review
    resumes here from its checkpoint. A context adds its strict prefix and its
    subject-version port the way it does in the API's `wiring.py`; a type no
    context answers for is never decided by chat (no version, no decision), so
    a platform type like `memory.` needs neither here. Without
    `DW_APPROVAL_CODE_SECRET` the command still answers a decision, with
    "not enabled", so the words never reach a model.
    """
    flow = ApproveAndResumeService(
        uow_factory=SqlPlatformUnitOfWorkFactory(sessions),
        runner=runner,
        run_store=runner.run_store,
        clock=clock,
        id_generator=ids,
    )
    subjects = ApprovalSubjectVersions()
    register_product_approvals(flow, subjects, sessions)
    secret = settings.approval_code_secret.get_secret_value()
    return ChannelDecisionCommand(
        ChannelApprovalDecisionService(
            approval_flow=flow,
            authorization=ScopeAuthorizationService(),
            store=SqlApprovalCodeStore(sessions),
            subjects=subjects,
            access=LinkedUserAccess(
                preferences=SqlChannelPreferences(sessions),
                lookup=SqlMembershipLookup(sessions),
            ),
            key=DecisionCodeKey(secret.encode()) if secret else None,
            clock=clock,
            ids=ids,
        )
    )


def build_channel_commands(
    settings: WorkerSettings,
    sessions: async_sessionmaker[AsyncSession],
    *,
    gateway: ModelGateway,
    decisions: ChannelDecisionCommand,
    ids: IdGenerator,
    clock: UtcClock,
) -> ChannelCommandRegistry[AccessContext]:
    """What a linked person can ask for through a chat, in the order it is asked.

    The seam a context plugs its chat commands into. Order is policy, not
    convenience: the decide command first (`build_channel_decision_command`,
    ticket 05: a reply to a pending decision is never re-read as a new
    request), then an open conversation, then intent classification. Z4b's
    proposal is both of the last two: it continues an open draft or reads a
    new message as a proposal, and its reading is the classification — a
    question (ticket 06) it hands on, untouched, to the read-only question
    command registered last, which also answers whoever may not propose.
    Each command declares
    its own scope ceiling; the router builds its context from the person's
    membership cut to that ceiling.
    """
    commands = ChannelCommandRegistry[AccessContext]()
    # First: a decision is never read as a proposal, nor shown to a model.
    commands.register("approval_decision", decisions)
    commands.register(
        "supply_chain.product_proposal",
        build_zalo_proposal_command(
            sessions,
            configs_dir=REPO_ROOT / "configs",
            gateway=gateway,
            ids=ids,
            clock=clock,
            web_url=settings.public_web_url,
        ),
    )
    # Last: read-only, so it may see whatever no command before it took.
    commands.register(
        "supply_chain.case_query",
        build_zalo_case_query_command(
            sessions,
            configs_dir=REPO_ROOT / "configs",
            gateway=gateway,
            ids=ids,
            web_url=settings.public_web_url,
        ),
    )
    return commands


def build_zalo_inbound(
    settings: WorkerSettings,
    sessions: async_sessionmaker[AsyncSession],
    bot: ChatSenderPort,
    clock: UtcClock,
    commands: ChannelCommandRegistry[AccessContext],
) -> ZaloInbound:
    """The Zalo update entry over this deployment's database: the link flow for
    ``/start``/``/stop``, the inbound router for everything else.

    One construction, so the poll lane here and any test of it run the same
    wiring; the webhook (Z3) builds the same object in the API and calls
    ``handle`` with what it was POSTed.
    """
    zalo_link = SqlZaloLink(sessions)
    return ZaloInbound(
        link_secret=settings.zalo_link_secret.get_secret_value(),
        store=zalo_link,
        sender=bot,
        clock=clock,
        product_name=settings.product_name,
        router=InboundRouter(
            identities=zalo_link,
            ledger=SqlChannelInboundLedger(sessions),
            access=LinkedUserAccess(
                preferences=SqlChannelPreferences(sessions),
                lookup=SqlMembershipLookup(sessions),
            ),
            commands=commands,
            sender=bot,
            unlinked_reply=link_help(settings.product_name),
            settings_url=f"{settings.public_web_url.rstrip('/')}/settings",
        ),
    )


def build_registry(settings: WorkerSettings) -> ConsumerRegistry:
    """Wire the lanes this process hosts, skipping any whose infra is absent."""
    registry = ConsumerRegistry()
    clock = SystemClock()
    # Uuid7: a memory id that sorts by when it was learned makes the
    # keyset page over `created_at` stable without a second column.
    ids = Uuid7Generator()
    telemetry = _build_worker_telemetry(settings)

    # Every queue this process wired, with the window its jobs deserve. A queue
    # this host did not wire stays absent rather than being reaped with a
    # guessed window: the rows still exist and another process owns them, and
    # two reapers disagreeing about what counts as stale is worse than one that
    # is silent.
    reap_targets: list[ReapTarget] = []
    # What this deployment considers expired. ``None`` means nothing is pruned,
    # which is the correct default: deleting rows on a schedule nobody asked for
    # is not a safe guess.
    # What this deployment considers expired. No longer `None`: memory carries a
    # `retention_policy` per row and, until now, nothing read it — a lifecycle
    # commitment that a compliance review reads as a promise and that expired
    # nothing. The rules are a versioned artifact, not a constant, because the
    # question it answers ("how long do you keep our data") gets asked about the
    # past as well as the present.
    # Two lanes, not one: memory expires items on a per-class schedule and
    # knowledge expires documents somebody deleted, and a pass that failed would
    # otherwise take the other's work down with it.
    retention: RetentionPrunePort | None = None
    knowledge_retention: RetentionPrunePort | None = None
    partitions: RetentionPrunePort | None = None
    # Run checkpoints of finished threads, on the same policy file's
    # `checkpoints` terms. Its own lane for the same reason memory and knowledge
    # have theirs: a failing pass must not take the others down with it.
    checkpoint_retention: RetentionPrunePort | None = None
    # The spend guard's own housekeeping (Ops hardening Phase 3) — a technical
    # constant, not a legal term, so it is not on retention_policy's cadence
    # or file; see SqlSpendGuardRetention's docstring.
    spend_guard_retention: RetentionPrunePort | None = None
    # The in-app inbox's own bound: 90 days, the database's constant.
    notifications_retention: RetentionPrunePort | None = None
    # One-time channel link nonces: a day past expiry, then gone.
    channel_link_nonces_retention: RetentionPrunePort | None = None
    # Inbound chat message ids: seven days, then gone (INBOUND_MESSAGE_RETENTION).
    channel_inbound_messages_retention: RetentionPrunePort | None = None
    # Single-use decision codes: a day, then gone (the database's constant).
    approval_codes_retention: RetentionPrunePort | None = None
    # The Zalo self-link poll: only with a database, a bot token, a link secret
    # and ZALO_UPDATES_MODE=poll.
    zalo_poll_consumer: Callable[[], Awaitable[None]] | None = None
    # Notifications out through linked Zalo chats (ADR 0013): a database and a
    # bot token, polled or webhooked alike.
    channel_delivery_consumer: Callable[[], Awaitable[None]] | None = None
    # Channel deliveries: 90 days, never a pending one, the database's constant.
    channel_deliveries_retention: RetentionPrunePort | None = None
    # Ops hardening Phase 4. Needs object storage too, not just a database -
    # export/purge touch four buckets and the vector index alongside Postgres.
    offboarding_consumer: Callable[[], Awaitable[None]] | None = None
    # Supply Chain's follow-up sweep: a database is all it needs.
    follow_up_consumer: Callable[[], Awaitable[None]] | None = None
    # Supply Chain's stage-1 daily report to TP Cung ứng: a database too.
    stage_one_report: Callable[[], Awaitable[None]] | None = None
    # Supply Chain's BGĐ review reconcile: a database is all it needs too.
    product_review_reconcile: Callable[[], Awaitable[None]] | None = None
    # Supply Chain's case-document orphan sweep: a database and the bucket.
    document_orphans: RetentionPrunePort | None = None
    # Supply Chain's chat proposal drafts past their 30 minutes: a database.
    proposal_drafts_retention: RetentionPrunePort | None = None
    # Supply Chain's closed follow-ups past their tenant's term: a database.
    follow_ups_retention: RetentionPrunePort | None = None

    if settings.database_url:
        # ---- transactional outbox ----------------------------------------
        # Handlers are keyed by event type. A context registers its own here;
        # an event with no handler is left in the table rather than dropped, so
        # adding the handler later drains the backlog.
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        # Remembering runs here rather than on the request that produced the
        # fact: a run that stops to decide what is worth keeping is a run someone
        # is waiting on. The handler is idempotent because the outbox delivers at
        # least once — see `dw_worker.consumers.memory`.
        # Absent without Qdrant, and absent is survivable: the memory is stored
        # and recalled either way, it simply sorts with the ones nothing has an
        # opinion about — and there are no points to purge.
        memory_vectors = _build_memory_vectors(settings)
        handlers: dict[str, EventHandler] = dict(
            memory_handlers(
                MemoryService(
                    session_factory=sessions,
                    policy=MemoryWritePolicy(),
                    clock=clock,
                    id_generator=ids,
                    evidence_store=SqlEvidenceStore(clock=clock),
                    # A superseded memory's point goes with it.
                    vector_purge=memory_vectors,
                ),
                # Writes the vector that lets a later recall order a long list.
                memory_vectors,
            )
        )
        # Pinned by filename, like every other versioned artifact here: the
        # answer to a retention question has to name the version that gave it.
        # One file read once — the two sweeps are two halves of one commitment,
        # and a build where they disagreed would be a build that answers the
        # compliance question two ways.
        retention_policy = load_retention_policy(RETENTION_POLICY_PATH)
        retention = SqlMemoryRetention(
            session_factory=sessions,
            policy=retention_policy,
            clock=clock,
            vector_index=memory_vectors,
        )
        # Not retention in the sense of deleting: mostly it CREATES next
        # month's partitions, which is what keeps rows out of the DEFAULT one.
        # It shares the lane's port and cadence because it is the same kind of
        # slow housekeeping and answers the same policy file.
        partitions = SqlPartitionMaintenance(
            session_factory=sessions, policy=retention_policy, clock=clock
        )
        knowledge_retention = SqlKnowledgeRetention(
            session_factory=sessions,
            policy=retention_policy,
            clock=clock,
            # Deleting the rows without the points would leave the text of a
            # deleted document in the only store that can still return it.
            vector_index=build_vector_index(settings),
        )
        checkpoint_retention = SqlCheckpointRetention(
            session_factory=sessions, policy=retention_policy, clock=clock
        )
        spend_guard_retention = SqlSpendGuardRetention(session_factory=sessions, clock=clock)
        notifications_retention = SqlNotificationRetention(session_factory=sessions)
        channel_link_nonces_retention = SqlChannelLinkNonceRetention(session_factory=sessions)
        channel_inbound_messages_retention = SqlChannelInboundRetention(session_factory=sessions)
        approval_codes_retention = SqlApprovalCodeRetention(session_factory=sessions)
        # The runner that hosts BGĐ's review graph: the reconcile lane starts
        # reviews on it, and a decision sent from Zalo resumes them on it.
        review_runner = build_product_review_runner(
            sessions,
            configs_dir=REPO_ROOT / "configs",
            ids=ids,
            clock=clock,
            telemetry=telemetry,
            release_manifest_ref=release_manifest_ref_for(settings, REPO_ROOT),
        )
        proposal_drafts_retention = build_proposal_draft_retention(sessions)
        follow_ups_retention = build_follow_up_retention(
            sessions, policies_dir=REPO_ROOT / "configs" / "policies"
        )
        # Rows are queued whether or not this host sends them, so they are
        # pruned whether or not it does.
        channel_deliveries_retention = SqlChannelDeliveryRetention(session_factory=sessions)
        if settings.zalo_send_enabled:
            channel_delivery_consumer = build_channel_delivery_consumer(
                SqlChannelOutbox(sessions),
                channel="zalo",
                address_of=SqlZaloLink(sessions).zalo_id_for,
                sender=ZaloBotClient(bot_token=settings.zalo_bot_token.get_secret_value()),
                web_url=settings.public_web_url,
            )
        if settings.zalo_poll_enabled:
            bot = ZaloBotClient(bot_token=settings.zalo_bot_token.get_secret_value())
            commands = build_channel_commands(
                settings,
                sessions,
                gateway=build_one_call_gateway(
                    build_model_stack_for(settings, sessions, clock=clock, telemetry=telemetry),
                    sessions,
                    # The plan catalogue the API's allowance and the reconcile
                    # lane's runner read.
                    allowance=PlanEntitlementService(DEFAULT_PLANS),
                    clock=clock,
                ),
                decisions=build_channel_decision_command(
                    settings, sessions, runner=review_runner, ids=ids, clock=clock
                ),
                ids=ids,
                clock=clock,
            )
            zalo_poll_consumer = build_zalo_poll_consumer(
                bot, build_zalo_inbound(settings, sessions, bot, clock, commands)
            )
        follow_up_consumer = build_follow_up_consumer(
            build_follow_up_sweep(
                sessions,
                policies_dir=REPO_ROOT / "configs" / "policies",
                ids=ids,
                clock=clock,
            )
        )
        stage_one_report = build_stage_one_report_consumer(
            build_stage_one_report(
                sessions, policies_dir=REPO_ROOT / "configs" / "policies", clock=clock
            )
        )
        product_review_reconcile = build_product_review_reconcile_consumer(
            build_product_review_reconcile(
                sessions, runner=review_runner, configs_dir=REPO_ROOT / "configs", ids=ids
            )
        )
        if settings.s3_endpoint_url:
            offboarding_consumer = build_offboarding_consumer(
                TenantOffboardingLane(
                    store=SqlTenantOffboarding(session_factory=sessions),
                    artifacts=build_object_storage(settings),
                    exports=build_export_bucket(settings),
                    attachments=build_feedback_bucket(settings),
                    case_documents=build_case_documents_bucket(settings),
                    vector_index=build_vector_index(settings),
                    memory_vectors=memory_vectors,
                    clock=clock,
                )
            )
            document_orphans = build_document_orphan_sweep(
                sessions, build_case_document_storage(settings), clock=clock
            )
        registry.register(
            "outbox",
            build_outbox_consumer(
                SqlOutboxDrain(sessions, clock),
                handlers,
                batch_size=settings.outbox_batch_size,
                max_attempts=settings.outbox_max_attempts,
                telemetry=telemetry,
                clock=clock,
            ),
        )

    # ---- knowledge ingestion ---------------------------------------------
    # Needs a database and object storage both; without either, files are
    # staged by nobody and this lane would spin on an empty queue.
    ingest = build_ingest_components(settings)
    if ingest is not None:
        registry.register(
            "ingest",
            build_ingest_consumer(ingest, batch_size=settings.ingest_batch_size),
        )

    # ---- BOUNDED CONTEXT LANES REGISTER HERE -----------------------------
    # Supply Chain: the follow-up sweep. It owns no job queue (the follow-ups
    # table is state, not work waiting to be claimed), so it needs no
    # ReapTarget. A later context adds its lane here the same way, and names
    # it in `apps/worker/tests/unit/test_worker.py`.
    if follow_up_consumer is not None:
        registry.register(
            "supply_chain_follow_ups",
            follow_up_consumer,
            interval_seconds=settings.supply_chain_follow_up_interval_seconds,
        )
    # The stage-1 daily report (ticket 08): on the sweep's cadence, since it
    # reads what the sweep reads; once a day per workspace comes from the
    # inbox's once-per-key delivery, not from the cadence.
    if stage_one_report is not None:
        registry.register(
            "supply_chain_stage_one_report",
            stage_one_report,
            interval_seconds=settings.supply_chain_follow_up_interval_seconds,
        )
    # BGĐ's review for a product case waiting without one (stage-1 ticket
    # 02). Starts runs, claims no queue: no ReapTarget (a stranded run is
    # settled on its thread's next claim, `SqlWorkerRunStore.create`).
    if product_review_reconcile is not None:
        registry.register(
            "supply_chain_product_review_reconcile",
            product_review_reconcile,
            interval_seconds=settings.supply_chain_product_review_reconcile_interval_seconds,
        )
    # Case-document objects no row holds (ADR 0021): housekeeping on the
    # retention cadence, through the retention consumer's error handling.
    if document_orphans is not None:
        registry.register(
            "supply_chain_document_orphans",
            build_retention_consumer(document_orphans),
            interval_seconds=RETENTION_INTERVAL_SECONDS,
        )

    # Chat proposal drafts nobody answered within DRAFT_TTL (zalo-channel
    # ticket 04): its own lane, on the retention cadence, whether or not this
    # process polls — a webhook host's drafts expire the same way.
    if proposal_drafts_retention is not None:
        registry.register(
            "supply_chain_proposal_drafts_retention",
            build_retention_consumer(proposal_drafts_retention),
            interval_seconds=RETENTION_INTERVAL_SECONDS,
        )

    # Closed follow-ups past their tenant's term (ticket P3): its own lane on
    # the retention cadence; open ones are never touched.
    if follow_ups_retention is not None:
        registry.register(
            "supply_chain_follow_ups_retention",
            build_retention_consumer(follow_ups_retention),
            interval_seconds=RETENTION_INTERVAL_SECONDS,
        )

    # ---- channels ----------------------------------------------------------
    # The Zalo self-link poll. Its own long-poll paces it; the interval only
    # spaces retries after a failed tick.
    if zalo_poll_consumer is not None:
        registry.register("zalo_link_poll", zalo_poll_consumer)
        logger.info("zalo link poll registered")
    # In-app notifications out through linked chats. Claims rows with
    # SKIP LOCKED and holds no lease, so it needs no ReapTarget: a row a dead
    # worker held is pending again the moment its transaction ends.
    if channel_delivery_consumer is not None:
        registry.register(
            "channel_delivery",
            channel_delivery_consumer,
            interval_seconds=settings.channel_delivery_interval_seconds,
        )

    # ---- periodic repair --------------------------------------------------
    # Registered last because both sweeps act on what everything above created,
    # and both are skipped when there is nothing for them to act on.
    if reap_targets:
        registry.register(
            "reaper",
            build_reaper_consumer(reap_targets, clock, telemetry),
            interval_seconds=REAP_INTERVAL_SECONDS,
        )
    if retention is not None:
        registry.register(
            "retention",
            build_retention_consumer(retention),
            interval_seconds=RETENTION_INTERVAL_SECONDS,
        )
    if knowledge_retention is not None:
        registry.register(
            "retention_knowledge",
            build_retention_consumer(knowledge_retention),
            interval_seconds=RETENTION_INTERVAL_SECONDS,
        )
    if partitions is not None:
        registry.register(
            "partitions",
            build_retention_consumer(partitions),
            interval_seconds=RETENTION_INTERVAL_SECONDS,
        )
    if checkpoint_retention is not None:
        registry.register(
            "checkpoint_retention",
            build_retention_consumer(checkpoint_retention),
            interval_seconds=RETENTION_INTERVAL_SECONDS,
        )
    if spend_guard_retention is not None:
        registry.register(
            "spend_guard_retention",
            build_retention_consumer(spend_guard_retention),
            interval_seconds=RETENTION_INTERVAL_SECONDS,
        )
    if notifications_retention is not None:
        registry.register(
            "notifications_retention",
            build_retention_consumer(notifications_retention),
            interval_seconds=RETENTION_INTERVAL_SECONDS,
        )
    if channel_link_nonces_retention is not None:
        registry.register(
            "channel_link_nonces_retention",
            build_retention_consumer(channel_link_nonces_retention),
            interval_seconds=RETENTION_INTERVAL_SECONDS,
        )
    if channel_inbound_messages_retention is not None:
        registry.register(
            "channel_inbound_messages_retention",
            build_retention_consumer(channel_inbound_messages_retention),
            interval_seconds=RETENTION_INTERVAL_SECONDS,
        )
    if channel_deliveries_retention is not None:
        registry.register(
            "channel_deliveries_retention",
            build_retention_consumer(channel_deliveries_retention),
            interval_seconds=RETENTION_INTERVAL_SECONDS,
        )
    if approval_codes_retention is not None:
        registry.register(
            "approval_codes_retention",
            build_retention_consumer(approval_codes_retention),
            interval_seconds=RETENTION_INTERVAL_SECONDS,
        )
    if offboarding_consumer is not None:
        registry.register(
            "offboarding", offboarding_consumer, interval_seconds=OFFBOARDING_INTERVAL_SECONDS
        )
    return registry


async def run_worker(
    settings: WorkerSettings,
    registry: ConsumerRegistry,
    shutdown_event: asyncio.Event,
) -> None:
    logger.info("worker starting, consumers=%s", sorted(registry.all()))

    async def heartbeat_loop() -> None:
        while not shutdown_event.is_set():
            beat(settings.heartbeat_file)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    shutdown_event.wait(), timeout=settings.heartbeat_interval_seconds
                )

    async def consumer_loop(name: str) -> None:
        consumer = registry.all()[name]
        interval = registry.interval_for(name, settings.poll_interval_seconds)
        while not shutdown_event.is_set():
            try:
                await consumer()
            except Exception:
                # Broad on purpose: one lane's bad tick must not take the
                # process down and stop every other lane with it.
                logger.exception("consumer %s failed; backing off", name)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shutdown_event.wait(), timeout=interval)

    tasks = [asyncio.create_task(heartbeat_loop(), name="heartbeat")]
    tasks.extend(
        asyncio.create_task(consumer_loop(name), name=f"consumer:{name}") for name in registry.all()
    )
    await shutdown_event.wait()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("worker stopped cleanly")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    settings = WorkerSettings()
    settings.validate_for_profile()
    # A background thread, not the asyncio loop: prometheus_client's server
    # predates asyncio support and reads the global REGISTRY per request, so
    # starting it before the loop exists is fine — there is nothing to race.
    # No setting gates this off: metrics are wired unconditionally now (Ops
    # hardening Phase 5, see `dw_observability.otel`), and a scrape target
    # nobody points Prometheus at costs nothing.
    from prometheus_client import start_http_server

    start_http_server(settings.metrics_port)
    registry = build_registry(settings)
    shutdown_event = asyncio.Event()

    async def runner() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):  # Windows lacks add_signal_handler
                loop.add_signal_handler(sig, shutdown_event.set)
        await run_worker(settings, registry, shutdown_event)

    asyncio.run(runner())


if __name__ == "__main__":
    main()
