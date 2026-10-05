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

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from dw_agent_runtime.adapters.spend_guard import SqlSpendGuardRetention
from dw_connectors.adapters.zalo_bot import ZaloBotClient
from dw_kernel.ports import SystemClock, Uuid7Generator
from dw_knowledge.adapters.evidence_store import SqlEvidenceStore
from dw_knowledge.retention import SqlKnowledgeRetention
from dw_memory.policy import MemoryWritePolicy
from dw_memory.retention import SqlMemoryRetention
from dw_memory.service import MemoryService
from dw_observability.otel import build_telemetry
from dw_observability.telemetry import TelemetryPort
from dw_platform.adapters.persistence.notifications import SqlNotificationRetention
from dw_platform.adapters.persistence.offboarding import SqlTenantOffboarding
from dw_platform.adapters.persistence.outbox_drain import SqlOutboxDrain
from dw_platform.adapters.persistence.partition_maintenance import SqlPartitionMaintenance
from dw_platform.adapters.persistence.zalo_link_repo import (
    SqlChannelLinkNonceRetention,
    SqlZaloLink,
)
from dw_platform.retention_policy import load_retention_policy
from dw_worker.composition import (
    REPO_ROOT,
    build_embeddings,
    build_export_bucket,
    build_feedback_bucket,
    build_ingest_components,
    build_object_storage,
    build_vector_index,
)
from dw_worker.consumers import ConsumerRegistry
from dw_worker.consumers.ingest import build_ingest_consumer
from dw_worker.consumers.memory import MemoryIndexPort, memory_handlers
from dw_worker.consumers.offboarding import INTERVAL_SECONDS as OFFBOARDING_INTERVAL_SECONDS
from dw_worker.consumers.offboarding import TenantOffboardingLane, build_offboarding_consumer
from dw_worker.consumers.outbox import EventHandler, build_outbox_consumer
from dw_worker.consumers.reaper import INTERVAL_SECONDS as REAP_INTERVAL_SECONDS
from dw_worker.consumers.reaper import ReapTarget, build_reaper_consumer
from dw_worker.consumers.retention import INTERVAL_SECONDS as RETENTION_INTERVAL_SECONDS
from dw_worker.consumers.retention import RetentionPrunePort, build_retention_consumer
from dw_worker.consumers.supply_chain import build_follow_up_consumer, build_follow_up_sweep
from dw_worker.consumers.zalo_poll import build_zalo_poll_consumer
from dw_worker.health import beat
from dw_worker.settings import WorkerSettings

logger = logging.getLogger("dw_worker")


def _build_worker_telemetry(settings: WorkerSettings) -> TelemetryPort:
    return build_telemetry(
        service_name="dw-worker",
        langfuse_enabled=settings.langfuse_enabled,
        langfuse_host=settings.langfuse_host,
        langfuse_public_key=settings.langfuse_public_key,
        langfuse_secret_key=settings.langfuse_secret_key,
        otel_endpoint=settings.otel_endpoint,
    )


def _build_memory_index(settings: WorkerSettings) -> MemoryIndexPort | None:
    """Imported inside the function so a deployment without Qdrant need not have
    the client installed to boot — the same rule the knowledge index follows."""
    if not settings.qdrant_url:
        return None
    from qdrant_client import AsyncQdrantClient

    from dw_memory.adapters.qdrant_ranker import QdrantMemoryRanker

    return QdrantMemoryRanker(
        client=AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key),
        embedder=build_embeddings(settings),
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
    # The spend guard's own housekeeping (Ops hardening Phase 3) — a technical
    # constant, not a legal term, so it is not on retention_policy's cadence
    # or file; see SqlSpendGuardRetention's docstring.
    spend_guard_retention: RetentionPrunePort | None = None
    # The in-app inbox's own bound: 90 days, the database's constant.
    notifications_retention: RetentionPrunePort | None = None
    # One-time channel link nonces: a day past expiry, then gone.
    channel_link_nonces_retention: RetentionPrunePort | None = None
    # The Zalo self-link poll: only with a database, a bot token, a link secret
    # and ZALO_UPDATES_MODE=poll.
    zalo_poll_consumer: Callable[[], Awaitable[None]] | None = None
    # Ops hardening Phase 4. Needs object storage too, not just a database -
    # export/purge touch three buckets and the vector index alongside Postgres.
    offboarding_consumer: Callable[[], Awaitable[None]] | None = None
    # Supply Chain's follow-up sweep: a database is all it needs.
    follow_up_consumer: Callable[[], Awaitable[None]] | None = None

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
        handlers: dict[str, EventHandler] = dict(
            memory_handlers(
                MemoryService(
                    session_factory=sessions,
                    policy=MemoryWritePolicy(),
                    clock=clock,
                    id_generator=ids,
                    evidence_store=SqlEvidenceStore(clock=clock),
                ),
                # Writes the vector that lets a later recall order a long list.
                # Absent without Qdrant, and absent is survivable: the memory is
                # stored and recalled either way, it simply sorts with the ones
                # nothing has an opinion about.
                _build_memory_index(settings),
            )
        )
        # Pinned by filename, like every other versioned artifact here: the
        # answer to a retention question has to name the version that gave it.
        # One file read once — the two sweeps are two halves of one commitment,
        # and a build where they disagreed would be a build that answers the
        # compliance question two ways.
        retention_policy = load_retention_policy(
            REPO_ROOT / "configs" / "policies" / "retention@1.4.0.yaml"
        )
        retention = SqlMemoryRetention(
            session_factory=sessions, policy=retention_policy, clock=clock
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
        spend_guard_retention = SqlSpendGuardRetention(session_factory=sessions, clock=clock)
        notifications_retention = SqlNotificationRetention(session_factory=sessions)
        channel_link_nonces_retention = SqlChannelLinkNonceRetention(session_factory=sessions)
        if settings.zalo_poll_enabled:
            zalo_poll_consumer = build_zalo_poll_consumer(
                ZaloBotClient(bot_token=settings.zalo_bot_token.get_secret_value()),
                SqlZaloLink(sessions),
                link_secret=settings.zalo_link_secret.get_secret_value(),
                clock=clock,
                product_name=settings.product_name,
            )
        follow_up_consumer = build_follow_up_consumer(
            build_follow_up_sweep(
                sessions,
                policies_dir=REPO_ROOT / "configs" / "policies",
                ids=ids,
                clock=clock,
            )
        )
        if settings.s3_endpoint_url:
            offboarding_consumer = build_offboarding_consumer(
                TenantOffboardingLane(
                    store=SqlTenantOffboarding(session_factory=sessions),
                    artifacts=build_object_storage(settings),
                    exports=build_export_bucket(settings),
                    attachments=build_feedback_bucket(settings),
                    vector_index=build_vector_index(settings),
                    clock=clock,
                )
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

    # ---- channels ----------------------------------------------------------
    # The Zalo self-link poll. Its own long-poll paces it; the interval only
    # spaces retries after a failed tick.
    if zalo_poll_consumer is not None:
        registry.register("zalo_link_poll", zalo_poll_consumer)
        logger.info("zalo link poll registered")

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
