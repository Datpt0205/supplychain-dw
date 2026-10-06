import asyncio
from pathlib import Path

import pytest

from dw_worker.consumers import ConsumerRegistry
from dw_worker.health import beat, is_alive
from dw_worker.main import build_registry, run_worker
from dw_worker.settings import WorkerSettings

pytestmark = pytest.mark.unit


def test_heartbeat_roundtrip(tmp_path: Path) -> None:
    hb = tmp_path / "heartbeat"
    beat(hb)
    assert is_alive(hb, max_age_seconds=10)
    assert not is_alive(tmp_path / "missing", max_age_seconds=10)


def test_registry_rejects_duplicates_and_blank_names() -> None:
    registry = ConsumerRegistry()

    async def consumer() -> None: ...

    registry.register("outbox", consumer)
    with pytest.raises(ValueError, match="already registered"):
        registry.register("outbox", consumer)
    with pytest.raises(ValueError, match="blank"):
        registry.register("  ", consumer)


async def test_worker_runs_consumers_and_shuts_down(tmp_path: Path) -> None:
    settings = WorkerSettings(
        heartbeat_file=tmp_path / "hb",
        heartbeat_interval_seconds=0.05,
        poll_interval_seconds=0.05,
    )
    registry = ConsumerRegistry()
    calls = 0

    async def counting_consumer() -> None:
        nonlocal calls
        calls += 1

    registry.register("counter", counting_consumer)
    shutdown = asyncio.Event()

    worker_task = asyncio.create_task(run_worker(settings, registry, shutdown))
    await asyncio.sleep(0.3)
    shutdown.set()
    await asyncio.wait_for(worker_task, timeout=2)

    assert calls >= 2, "consumer should run repeatedly"
    assert is_alive(settings.heartbeat_file, max_age_seconds=5)


async def test_failing_consumer_does_not_kill_worker(tmp_path: Path) -> None:
    settings = WorkerSettings(
        heartbeat_file=tmp_path / "hb",
        heartbeat_interval_seconds=0.05,
        poll_interval_seconds=0.05,
    )
    registry = ConsumerRegistry()

    async def broken_consumer() -> None:
        raise RuntimeError("boom")

    registry.register("broken", broken_consumer)
    shutdown = asyncio.Event()
    worker_task = asyncio.create_task(run_worker(settings, registry, shutdown))
    await asyncio.sleep(0.3)
    assert not worker_task.done(), "worker must survive consumer failures"
    shutdown.set()
    await asyncio.wait_for(worker_task, timeout=2)


def bare_settings(**overrides: object) -> WorkerSettings:
    """Settings with every lane's infra explicitly absent.

    Stated rather than defaulted because these fields read plain environment
    variables (``S3_ENDPOINT_URL``, ``LANGFUSE_ENABLED``, …): a developer shell
    with a dev stack exported would otherwise decide what this process wires.
    """
    absent: dict[str, object] = {
        "database_url": None,
        "s3_endpoint_url": None,
        "qdrant_url": None,
        "langfuse_enabled": False,
        "otel_endpoint": None,
        "zalo_bot_token": "",
        "zalo_link_secret": "",
    }
    absent.update(overrides)
    return WorkerSettings(**absent)  # type: ignore[arg-type]


def test_a_host_with_no_infrastructure_wires_no_lane() -> None:
    """Every lane is conditional, so the heartbeat-only worker is a real shape."""
    assert set(build_registry(bare_settings()).all()) == set()


def test_only_the_platform_lanes_are_wired() -> None:
    """Ten lanes a database alone is enough for, and no more.

    The outbox, and retention twice. Retention joined the platform set the day
    memory got a lifecycle: `memory.items` is a platform table, so the platform
    is what expires it — a context adds its own rules on top rather than owning
    the only ones. Ingest still needs object storage, and the reaper is
    registered only when something above it gave it a queue.

    Two retention lanes and not one because memory and knowledge expire on
    different terms and a pass that failed would otherwise take the other's work
    down with it. They read ONE policy file, which is the part that matters: a
    build where the two halves of a compliance commitment disagreed is the
    failure this split would otherwise invite. `partitions` reads the same file
    but only ever creates ahead of need, never deletes, so it carries none of
    that risk.

    `spend_guard_retention` is the fifth, and reads no policy file at all —
    unlike audit/memory/knowledge, its window answers no compliance question,
    so it is a technical constant in code, not a term in
    `retention@1.6.0.yaml` (see `SqlSpendGuardRetention`'s docstring).
    `notifications_retention` is the sixth, on the same footing: the in-app
    inbox's 90 days live in `platform.prune_notifications()` itself.
    `checkpoint_retention` is the seventh, and reads the policy file again:
    a checkpoint holds a conversation verbatim, so how long it stays is a
    compliance answer like memory's (`checkpoints` in the same file).
    `channel_link_nonces_retention` is the eighth: one-time link tokens a day
    past their expiry, a technical bound like the spend guard's.

    `supply_chain_follow_ups` is the first context lane: Supply Chain's sweep
    that turns due reminders, escalations and SLA breaches into follow-ups
    and notifications. It owns no job queue, so it brings no ReapTarget.
    `supply_chain_product_review_reconcile` is the second: it raises BGĐ's
    review for a product case waiting without one (a refused or failed start,
    or a case that reached the state before the review existed). It starts
    runs, so this process hosts the review graph; it claims no queue either.

    Naming the whole set is the point: a context's lane arriving in this process
    becomes a visible change rather than a silent one.
    """
    settings = bare_settings(database_url="postgresql+asyncpg://dw:dw@localhost/dw")
    assert set(build_registry(settings).all()) == {
        "outbox",
        "retention",
        "retention_knowledge",
        "partitions",
        "spend_guard_retention",
        "notifications_retention",
        "checkpoint_retention",
        "channel_link_nonces_retention",
        "supply_chain_follow_ups",
        "supply_chain_product_review_reconcile",
    }


def test_the_offboarding_lane_needs_object_storage_too_not_just_a_database() -> None:
    """Unlike every lane above, a database alone is not enough for this one:
    export/purge touch three buckets and the vector index alongside Postgres,
    so it stays unregistered — not half-wired against infra that is not
    there — until `s3_endpoint_url` is set too."""
    db_only = bare_settings(database_url="postgresql+asyncpg://dw:dw@localhost/dw")
    assert "offboarding" not in build_registry(db_only).all()

    db_and_s3 = bare_settings(
        database_url="postgresql+asyncpg://dw:dw@localhost/dw",
        s3_endpoint_url="http://localhost:9000",
    )
    assert "offboarding" in build_registry(db_and_s3).all()


def test_the_case_document_orphan_sweep_needs_object_storage_too() -> None:
    """`supply_chain_document_orphans` lists the case-documents bucket and asks
    the database about each old key, so like offboarding it is wired only when
    both are there, and runs on the retention cadence."""
    db_only = bare_settings(database_url="postgresql+asyncpg://dw:dw@localhost/dw")
    assert "supply_chain_document_orphans" not in build_registry(db_only).all()

    db_and_s3 = build_registry(
        bare_settings(
            database_url="postgresql+asyncpg://dw:dw@localhost/dw",
            s3_endpoint_url="http://localhost:9000",
        )
    )
    assert "supply_chain_document_orphans" in db_and_s3.all()
    assert db_and_s3.interval_for("supply_chain_document_orphans", 1.0) == 3600.0


def test_the_follow_up_sweep_runs_on_its_own_configurable_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Five minutes unless the deployment says otherwise; a tester sets 60
    to see a reminder land within the minute."""
    # `make` exports .env, where a developer may have set the quick cadence.
    monkeypatch.delenv("DW_WORKER_SUPPLY_CHAIN_FOLLOW_UP_INTERVAL_SECONDS", raising=False)
    default = build_registry(bare_settings(database_url="postgresql+asyncpg://dw:dw@localhost/dw"))
    quick = build_registry(
        bare_settings(
            database_url="postgresql+asyncpg://dw:dw@localhost/dw",
            supply_chain_follow_up_interval_seconds=60,
        )
    )
    assert default.interval_for("supply_chain_follow_ups", 1.0) == 300.0
    assert quick.interval_for("supply_chain_follow_ups", 1.0) == 60.0


def test_the_bgd_review_reconcile_runs_on_its_own_configurable_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(
        "DW_WORKER_SUPPLY_CHAIN_PRODUCT_REVIEW_RECONCILE_INTERVAL_SECONDS", raising=False
    )
    default = build_registry(bare_settings(database_url="postgresql+asyncpg://dw:dw@localhost/dw"))
    quick = build_registry(
        bare_settings(
            database_url="postgresql+asyncpg://dw:dw@localhost/dw",
            supply_chain_product_review_reconcile_interval_seconds=30,
        )
    )
    assert default.interval_for("supply_chain_product_review_reconcile", 1.0) == 300.0
    assert quick.interval_for("supply_chain_product_review_reconcile", 1.0) == 30.0


def test_the_pinned_retention_policy_promises_only_classes_code_can_assign() -> None:
    """A memory class nothing can put on a row is a promise in a compliance
    file that the code does not keep (`failure-modes.md` #1). `sensitive`,
    `ephemeral` and `legal_hold` were exactly that until 1.5.0; a class comes
    back together with the path that assigns it, and this goes red until then.

    Equality, so the other direction holds too: the class the service writes
    has a term in the file the sweep reads.
    """
    from dw_memory.service import RETENTION_CLASS
    from dw_platform.retention_policy import load_retention_policy
    from dw_worker.main import RETENTION_POLICY_PATH

    assert set(load_retention_policy(RETENTION_POLICY_PATH).classes) == {RETENTION_CLASS}
