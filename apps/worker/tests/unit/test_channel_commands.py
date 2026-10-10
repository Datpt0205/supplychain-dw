"""The worker's decide-command seam (channels Z5, ADR 0007).

A context that hosts its graph here builds the decide command with
`build_channel_decision_command` over its runner, passing its strict prefixes
and subject versions. This product's `build_channel_commands` registers that
command first and its own commands after it; that order is held against real
Postgres in `tests/integration/test_zalo_decision_db.py`, so the platform's
empty-registry checks of `build_channel_commands` are not carried here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_agent_runtime.adapters.langgraph_runner import LangGraphWorkflowRunner
from dw_agent_runtime.channel_decisions import ChannelDecisionCommand
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.application.approval_codes import ApprovalSubjectVersions
from dw_worker.main import build_channel_decision_command
from dw_worker.settings import WorkerSettings

pytestmark = pytest.mark.unit


@dataclass
class _Runner:
    """Stands in for the runner a context hosts its graph on; only its store is read."""

    run_store: Any = field(default_factory=object)


def _decision_command(secret: str) -> ChannelDecisionCommand:
    return build_channel_decision_command(
        WorkerSettings(approval_code_secret=secret),  # type: ignore[call-arg]
        cast(async_sessionmaker[AsyncSession], object()),
        runner=cast(LangGraphWorkflowRunner, _Runner()),
        subjects=ApprovalSubjectVersions(),
        strict_approval_prefixes=frozenset({"purchasing."}),
        ids=Uuid4Generator(),
        clock=SystemClock(),
    )


def test_the_decide_command_resumes_on_the_contexts_runner_with_its_strict_prefixes() -> None:
    decisions = _decision_command("a-code-secret-of-32-bytes-or-so")
    flow = decisions.service.approval_flow
    assert flow.strict_approval_prefixes >= {"purchasing."}
    assert decisions.service.key is not None


def test_without_a_code_secret_the_command_still_answers_but_holds_no_key() -> None:
    """It answers "not enabled" rather than let the words reach a model."""
    assert _decision_command("").service.key is None
