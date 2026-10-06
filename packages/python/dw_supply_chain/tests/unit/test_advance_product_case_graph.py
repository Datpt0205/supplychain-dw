"""Unit: BGĐ's review graph (step 6), pause and resume for real.

LangGraph's own in-memory checkpointer and a fake case store: real
`interrupt()`/`Command(resume=...)` semantics, no database.
`tests/integration/test_product_bod_review.py` proves the same cycle through
the real runner, the Postgres checkpointer, `ApproveAndResumeService` and a
process restart.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from dw_agent_runtime.contracts import RunContext
from dw_kernel.errors import NotFoundError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.ports import FixedClock, Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.domain.product_development_case import (
    ProductAction,
    ProductCaseStep,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
)
from dw_supply_chain.workflows.advance_product_case_graph import (
    BOD_REVIEW_APPROVAL_TYPE,
    SUPERSEDED,
    build_advance_product_case_graph,
)

pytestmark = pytest.mark.unit

TENANT, WORKSPACE = uuid.uuid4(), uuid.uuid4()
REQUESTER, DECIDER = uuid.uuid4(), uuid.uuid4()
NOW = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)


@dataclass
class FakeReviewCases:
    """`ProductCaseReviewPort`: narrows by tenant AND workspace as RLS does,
    optimistic on version as `save` is."""

    case: ProductDevelopmentCase
    steps: list[ProductCaseStep] = field(default_factory=list)
    audits: list[AuditEvent] = field(default_factory=list)
    contexts: list[AccessContext] = field(default_factory=list)

    async def get(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> ProductDevelopmentCase | None:
        self.contexts.append(context)
        visible = (self.case.tenant_id.value, self.case.workspace_id.value) == (
            context.tenant_id,
            context.workspace_id,
        )
        if case_id != self.case.id or not visible:
            return None
        return replace(self.case, _pending_steps=[])

    async def save(
        self, context: AccessContext, case: ProductDevelopmentCase, *, audit: AuditEvent
    ) -> None:
        assert case.version == self.case.version + 1
        self.steps.extend(case.pop_pending_steps())
        self.audits.append(audit)
        self.case = replace(case, _pending_steps=[])

    async def append_audit(self, context: AccessContext, audit: AuditEvent) -> None:
        self.audits.append(audit)


def _waiting(**overrides: object) -> ProductDevelopmentCase:
    fields: dict[str, object] = {
        "id": ProductDevelopmentCaseId(uuid.uuid4()),
        "tenant_id": TenantId(TENANT),
        "workspace_id": WorkspaceId(WORKSPACE),
        "proposal_code": "DX-2026-001",
        "product_name": "Nồi inox 3 đáy 24cm",
        "category": "Nồi",
        "pic_user_id": REQUESTER,
        "created_by": REQUESTER,
        "supplier_name": "NCC Minh Long",
        "state": ProductDevState.PENDING_BOD_REVIEW,
        "sample_round": 2,
        "version": 7,
    }
    fields.update(overrides)
    return ProductDevelopmentCase(**fields)  # type: ignore[arg-type]


def _run_context() -> RunContext:
    run_id = uuid.uuid4()
    return RunContext(
        run_id=run_id,
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=REQUESTER,
        worker_id="supply_chain_advance_product_case",
        worker_version="1.0.0",
        channel="web",
        plan_id="professional",
        roles=frozenset({"member"}),
        scopes=frozenset({"supply_chain.duty.rnd"}),
        trace_id=str(run_id),
    )


def _input(case: ProductDevelopmentCase, **extra: object) -> dict[str, object]:
    return {
        "product_dev_case_id": str(case.id),
        "proposal_code": case.proposal_code,
        "product_name": case.product_name,
        "sample_round": case.sample_round,
        "required_scope": "supply_chain.approve.bod",
        **extra,
    }


class Run:
    def __init__(self, cases: FakeReviewCases) -> None:
        # `Any`: the compiled graph's generics, as `test_advance_case_graph`.
        self.graph: Any = build_advance_product_case_graph(
            cases, Uuid4Generator(), FixedClock(NOW)
        ).compile(checkpointer=MemorySaver())
        self.context = _run_context()
        self.config = {"configurable": {"thread_id": str(self.context.run_id)}}

    async def start(self, payload: dict[str, object]) -> dict[str, Any]:
        state: dict[str, Any] = await self.graph.ainvoke(payload, self.config, context=self.context)
        return state

    async def decide(self, resume: dict[str, object]) -> dict[str, Any]:
        state: dict[str, Any] = await self.graph.ainvoke(
            Command(resume=resume), self.config, context=self.context
        )
        return state


async def test_starting_pauses_with_the_minimal_payload_and_applies_nothing() -> None:
    case = _waiting()
    cases = FakeReviewCases(case)
    run = Run(cases)

    state = await run.start(_input(case))

    payload = state["__interrupt__"][0].value
    assert payload["approval_type"] == BOD_REVIEW_APPROVAL_TYPE
    assert payload["required_scope"] == "supply_chain.approve.bod"
    # Lead decision 9: what BGĐ needs to find the case, and nothing more.
    assert set(payload) == {
        "approval_type",
        "reason",
        "product_dev_case_id",
        "proposal_code",
        "product_name",
        "sample_round",
        "required_scope",
    }
    assert (payload["product_dev_case_id"], payload["sample_round"]) == (str(case.id), 2)
    assert cases.steps == [] and cases.audits == []


async def test_approving_moves_the_case_to_the_profile_as_the_decider() -> None:
    case = _waiting()
    cases = FakeReviewCases(case)
    run = Run(cases)
    await run.start(_input(case))

    state = await run.decide(
        {"approved": True, "comment": "Đồng ý giá vốn", "decided_by": str(DECIDER)}
    )

    assert state["outcome"] == "bod_approve"
    assert cases.case.state is ProductDevState.PROFILE_IN_PROGRESS
    (step,) = cases.steps
    assert (step.action, step.actor_id, step.reason) == (ProductAction.BOD_APPROVE, DECIDER, None)
    (audit,) = cases.audits
    assert audit.action == "supply_chain.product_case.bod_approve"
    assert audit.actor_id.value == DECIDER
    # Read and written in the run's own tenant and workspace.
    assert {(c.tenant_id, c.workspace_id) for c in cases.contexts} == {(TENANT, WORKSPACE)}


async def test_rejecting_cancels_with_bgds_comment_as_the_reason() -> None:
    """Unlike the PO graph (`test_rejecting_applies_nothing`), BGĐ not
    approving is a step: the case is cancelled, the comment its reason."""
    case = _waiting()
    cases = FakeReviewCases(case)
    run = Run(cases)
    await run.start(_input(case))

    state = await run.decide(
        {"approved": False, "comment": "Giá vốn quá cao", "decided_by": str(DECIDER)}
    )

    assert state["outcome"] == "bod_reject"
    assert cases.case.state is ProductDevState.CANCELLED
    (step,) = cases.steps
    assert (step.action, step.actor_id, step.reason) == (
        ProductAction.BOD_REJECT,
        DECIDER,
        "Giá vốn quá cao",
    )
    assert cases.audits[0].actor_id.value == DECIDER


async def test_a_decision_that_is_neither_yes_nor_no_moves_nothing() -> None:
    """A rejection cancels the case; a malformed decision must not be read as
    one. The run fails and the case keeps waiting for a fresh review."""
    case = _waiting()
    cases = FakeReviewCases(case)
    run = Run(cases)
    await run.start(_input(case))

    with pytest.raises(ValueError, match="yes or no"):
        await run.decide({"approved": "yes", "comment": "?", "decided_by": str(DECIDER)})

    assert cases.case.state is ProductDevState.PENDING_BOD_REVIEW
    assert cases.steps == [] and cases.audits == []


async def test_a_decider_named_in_the_start_payload_is_not_the_decider() -> None:
    """Only the resume value names who decided; a `decided_by` in the
    interrupt's input is state the decision overwrites."""
    case = _waiting()
    cases = FakeReviewCases(case)
    run = Run(cases)
    await run.start(_input(case, decided_by=str(REQUESTER)))

    await run.decide({"approved": True, "comment": "ok", "decided_by": str(DECIDER)})

    assert cases.steps[0].actor_id == DECIDER


async def test_a_resume_naming_no_decider_fails_rather_than_record_nobody() -> None:
    case = _waiting()
    cases = FakeReviewCases(case)
    run = Run(cases)
    await run.start(_input(case))

    with pytest.raises(ValueError):
        await run.decide({"approved": True, "comment": "ok"})
    assert cases.steps == []


@pytest.mark.parametrize("approved", [True, False])
async def test_a_case_cancelled_while_bgd_decided_is_superseded(approved: bool) -> None:
    case = _waiting()
    cases = FakeReviewCases(case)
    run = Run(cases)
    await run.start(_input(case))
    cases.case = replace(cases.case, state=ProductDevState.CANCELLED, version=8)

    state = await run.decide({"approved": approved, "comment": "muộn", "decided_by": str(DECIDER)})

    assert state["outcome"] == SUPERSEDED
    assert cases.case.state is ProductDevState.CANCELLED
    assert cases.steps == []
    (audit,) = cases.audits
    assert audit.action == "supply_chain.product_case.bod_review_superseded"
    assert audit.actor_id.value == DECIDER
    assert audit.details["approved"] is approved


async def test_a_review_of_an_earlier_round_is_superseded() -> None:
    case = _waiting()
    cases = FakeReviewCases(case)
    run = Run(cases)
    await run.start(_input(case, sample_round=1))

    state = await run.decide({"approved": True, "comment": "ok", "decided_by": str(DECIDER)})

    assert state["outcome"] == SUPERSEDED
    assert cases.case.state is ProductDevState.PENDING_BOD_REVIEW


async def test_a_case_gone_from_the_runs_workspace_fails_the_run() -> None:
    case = _waiting(workspace_id=WorkspaceId(uuid.uuid4()))
    cases = FakeReviewCases(case)
    run = Run(cases)
    await run.start(_input(case))

    with pytest.raises(NotFoundError):
        await run.decide({"approved": True, "comment": "ok", "decided_by": str(DECIDER)})
