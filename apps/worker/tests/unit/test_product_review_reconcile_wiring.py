"""The worker's BGĐ review reconcile is the first lane that starts runs, so
this process builds a runner of its own (stage-1 ticket 02). The API builds
the other one (`dw_api.bootstrap.runtime`). These tests pin the parts the
two must agree on, so a change to one that the other did not get is a red
test rather than a run recorded differently depending on who started it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from dw_agent_runtime.adapters.langgraph_runner import LangGraphWorkflowRunner
from dw_agent_runtime.adapters.spend_guard import SqlSpendGuardStore
from dw_agent_runtime.autonomy import AutonomyApprovalPolicy
from dw_agent_runtime.model.run_policy import load_worker_run_policy
from dw_agent_runtime.release import UNRELEASED
from dw_kernel.ports import SystemClock, Uuid7Generator
from dw_observability.telemetry import NullTelemetry
from dw_platform.application.entitlement import DEFAULT_PLANS, PlanEntitlementService
from dw_supply_chain.workflows import advance_product_case_graph as review_graph
from dw_worker.composition import REPO_ROOT
from dw_worker.consumers.supply_chain import (
    build_product_review_reconcile,
    build_product_review_runner,
)
from dw_worker.main import release_manifest_ref_for
from dw_worker.settings import WorkerSettings

pytestmark = pytest.mark.unit


def _settings(profile: str) -> WorkerSettings:
    return WorkerSettings(profile=profile, database_url=None)  # type: ignore[arg-type]


@pytest.mark.parametrize("profile", ["uat", "production"])
def test_a_deployed_worker_refuses_to_start_without_a_release_manifest(
    tmp_path: Path, profile: str
) -> None:
    """The image that lacked `contracts/release` stamped `unreleased` on every
    review the lane raised, silently. A deployed worker now refuses."""
    with pytest.raises(RuntimeError, match="no release manifest"):
        release_manifest_ref_for(_settings(profile), tmp_path)


def test_a_deployed_worker_stamps_the_shipped_release(tmp_path: Path) -> None:
    ref = tmp_path / "contracts" / "release" / "manifest.ref"
    ref.parent.mkdir(parents=True)
    ref.write_text("sha256:abc123\n", encoding="utf-8")
    assert release_manifest_ref_for(_settings("production"), tmp_path) == "sha256:abc123"


def test_a_development_tree_without_a_manifest_records_unreleased(tmp_path: Path) -> None:
    assert release_manifest_ref_for(_settings("local"), tmp_path) == UNRELEASED


def test_the_review_runner_is_configured_as_the_apis_runner_is() -> None:
    """What `dw_api.bootstrap.runtime.build_runtime` and `wiring.py` give the
    API's runner: the plan allowance over `DEFAULT_PLANS`, the spend guard,
    one autonomy policy, the shipped run policy's staleness, the release ref.
    Not a usage meter: the review graph calls no model, so there is no
    LangChain usage to meter."""
    engine = create_async_engine("postgresql+asyncpg://dw:dw@127.0.0.1:1/never")
    sessions = async_sessionmaker(engine, class_=AsyncSession)
    ids = Uuid7Generator()
    lane = build_product_review_reconcile(
        sessions,
        runner=build_product_review_runner(
            sessions,
            configs_dir=REPO_ROOT / "configs",
            ids=ids,
            clock=SystemClock(),
            telemetry=NullTelemetry(),
            release_manifest_ref="sha256:pinned",
        ),
        configs_dir=REPO_ROOT / "configs",
        ids=ids,
    )

    runner = lane.reviews.runner
    assert isinstance(runner, LangGraphWorkflowRunner)
    assert isinstance(runner.allowance, PlanEntitlementService)
    assert runner.allowance == PlanEntitlementService(DEFAULT_PLANS)
    assert isinstance(runner.spend_store, SqlSpendGuardStore)
    assert isinstance(runner.approval_policy, AutonomyApprovalPolicy)
    assert runner.release_manifest_ref == "sha256:pinned"
    shipped = load_worker_run_policy(REPO_ROOT / "configs" / "policies" / "worker_runs@1.0.0.yaml")
    assert runner.run_store.stale_run_after_seconds == shipped.stale_run_after_seconds
    # The one graph this process hosts, at the version the API registers.
    runner.graph_registry.resolve(review_graph.WORKER_ID, review_graph.GRAPH_VERSION)
    worker = runner.worker_registry.resolve(review_graph.WORKER_ID, review_graph.WORKER_VERSION)
    assert worker.definition.graph_version == review_graph.GRAPH_VERSION
