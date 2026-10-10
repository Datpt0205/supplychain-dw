"""Unit: the step-9 sign-off graph, pause and resume for real (ticket 04).

LangGraph's own in-memory checkpointer and a fake case store, as
`test_advance_product_case_graph.py` does for BGĐ's review: real
`interrupt()`/`Command(resume=...)` semantics, no database.
`tests/integration/test_product_signoff.py` runs the same through the real
runner, the Postgres checkpointer and `ApproveAndResumeService`.
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
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.ports import FixedClock, Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.domain.product_development_case import (
    ItemCode,
    ProductAction,
    ProductCaseStep,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
    Sku,
)
from dw_supply_chain.workflows.advance_product_case_graph import SUPERSEDED
from dw_supply_chain.workflows.product_signoff_graph import (
    SIGNOFF_APPROVAL_TYPE,
    WORKER_ID,
    build_product_signoff_graph,
)

pytestmark = pytest.mark.unit

TENANT, WORKSPACE = uuid.uuid4(), uuid.uuid4()
REQUESTER, BOD, ACCOUNTANT = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
NOW = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
ITEM = ItemCode(uuid.uuid4(), "MH-0001")
SKU = Sku(uuid.uuid4(), "MH-0001-RED", "Đỏ 24cm")
STEPS = [
    {"step": "bod", "label": "BGĐ", "required_scope": "supply_chain.approve.bod"},
    {"step": "accounting", "label": "Kế toán", "required_scope": "supply_chain.approve.accounting"},
]


@dataclass
class FakeCases:
    """`ProductCaseReviewPort`: narrows by tenant AND workspace as RLS does,
    optimistic on version as `save` is."""

    case: ProductDevelopmentCase
    steps: list[ProductCaseStep] = field(default_factory=list)
    audits: list[AuditEvent] = field(default_factory=list)

    async def get(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> ProductDevelopmentCase | None:
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


def assert_state(cases: FakeCases, expected: ProductDevState) -> None:
    # Through a parameter, for the mypy narrowing reason test_po_case.py gives.
    assert cases.case.state is expected


def _submitted(**overrides: object) -> ProductDevelopmentCase:
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
        "state": ProductDevState.PENDING_SIGNOFF,
        "item_code": ITEM,
        "skus": (SKU,),
        "signoff_round": 1,
        "version": 12,
    }
    fields.update(overrides)
    return ProductDevelopmentCase(**fields)  # type: ignore[arg-type]


def _input(case: ProductDevelopmentCase, steps: list[dict[str, str]] = STEPS) -> dict[str, object]:
    return {
        "product_dev_case_id": str(case.id),
        "proposal_code": case.proposal_code,
        "product_name": case.product_name,
        "item_code": ITEM.code,
        "signoff_round": case.signoff_round,
        "steps": steps,
        "step_index": 0,
    }


class Run:
    def __init__(self, cases: FakeCases) -> None:
        self.graph: Any = build_product_signoff_graph(
            cases, Uuid4Generator(), FixedClock(NOW)
        ).compile(checkpointer=MemorySaver())
        run_id = uuid.uuid4()
        self.context = RunContext(
            run_id=run_id,
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            actor_id=REQUESTER,
            worker_id=WORKER_ID,
            worker_version="1.0.0",
            channel="web",
            plan_id="professional",
            roles=frozenset({"member"}),
            scopes=frozenset({"supply_chain.duty.ordering"}),
            trace_id=str(run_id),
        )
        self.config = {"configurable": {"thread_id": str(run_id)}}

    async def start(self, payload: dict[str, object]) -> dict[str, Any]:
        state: dict[str, Any] = await self.graph.ainvoke(payload, self.config, context=self.context)
        return state

    async def decide(self, approved: bool, by: uuid.UUID, comment: str = "") -> dict[str, Any]:
        state: dict[str, Any] = await self.graph.ainvoke(
            Command(resume={"approved": approved, "comment": comment, "decided_by": str(by)}),
            self.config,
            context=self.context,
        )
        return state


def _pause(state: dict[str, Any]) -> dict[str, Any]:
    (interrupt,) = state["__interrupt__"]
    payload: dict[str, Any] = interrupt.value
    return payload


async def test_the_first_step_pauses_stamped_with_its_scope_and_the_whole_order() -> None:
    case = _submitted()
    cases = FakeCases(case)

    state = await Run(cases).start(_input(case))

    payload = _pause(state)
    assert payload["approval_type"] == SIGNOFF_APPROVAL_TYPE
    assert (payload["step"], payload["step_label"], payload["step_no"]) == ("bod", "BGĐ", 1)
    assert payload["required_scope"] == "supply_chain.approve.bod"
    assert payload["steps"] == [
        {"step": "bod", "label": "BGĐ"},
        {"step": "accounting", "label": "Kế toán"},
    ]
    assert (payload["product_dev_case_id"], payload["item_code"]) == (str(case.id), "MH-0001")
    assert payload["signoff_round"] == 1
    assert cases.steps == [] and cases.audits == []


async def test_bgd_then_accounting_sign_and_the_case_is_ready_to_order() -> None:
    case = _submitted()
    cases = FakeCases(case)
    run = Run(cases)
    await run.start(_input(case))

    after_bod = await run.decide(True, BOD, "Đồng ý")

    second = _pause(after_bod)
    assert (second["step"], second["step_no"]) == ("accounting", 2)
    assert second["required_scope"] == "supply_chain.approve.accounting"
    assert_state(cases, ProductDevState.PENDING_SIGNOFF)
    (signed,) = cases.audits
    assert signed.action == "supply_chain.product_case.signoff_step_approved"
    assert signed.actor_id.value == BOD
    assert signed.details["step"] == "bod"

    done = await run.decide(True, ACCOUNTANT, "Giá vốn khớp")

    assert done["outcome"] == ProductAction.SIGNOFF_APPROVE.value
    assert "__interrupt__" not in done
    assert_state(cases, ProductDevState.READY_TO_ORDER)
    (step,) = cases.steps
    assert (step.action, step.actor_id, step.reason) == (
        ProductAction.SIGNOFF_APPROVE,
        ACCOUNTANT,
        None,
    )


async def test_accounting_not_approving_returns_the_case_to_coding_with_its_codes() -> None:
    case = _submitted()
    cases = FakeCases(case)
    run = Run(cases)
    await run.start(_input(case))
    await run.decide(True, BOD, "Đồng ý")

    done = await run.decide(False, ACCOUNTANT, "Giá vốn chưa khớp với BM04")

    assert done["outcome"] == ProductAction.SIGNOFF_REJECT.value
    assert_state(cases, ProductDevState.ITEM_CODING)
    assert (cases.case.item_code, cases.case.skus) == (ITEM, (SKU,))
    (step,) = cases.steps
    assert (step.action, step.actor_id, step.reason) == (
        ProductAction.SIGNOFF_REJECT,
        ACCOUNTANT,
        "Giá vốn chưa khớp với BM04",
    )
    assert cases.audits[-1].details["step"] == "accounting"


async def test_bgd_not_approving_ends_the_signoff_and_raises_no_later_step() -> None:
    case = _submitted()
    cases = FakeCases(case)
    run = Run(cases)
    await run.start(_input(case))

    done = await run.decide(False, BOD, "Chưa đủ hồ sơ")

    assert "__interrupt__" not in done
    assert done["outcome"] == ProductAction.SIGNOFF_REJECT.value
    assert_state(cases, ProductDevState.ITEM_CODING)
    assert [a.action for a in cases.audits] == ["supply_chain.product_case.signoff_reject"]


async def test_the_order_is_the_one_the_run_was_started_with() -> None:
    """A tenant's override changing the order reaches the cases submitted
    after it: the run follows the steps it carries, nothing else."""
    case = _submitted()
    cases = FakeCases(case)
    run = Run(cases)

    first = _pause(await run.start(_input(case, steps=[STEPS[1], STEPS[0]])))

    assert first["step"] == "accounting"
    assert _pause(await run.decide(True, ACCOUNTANT, "ok"))["step"] == "bod"


async def test_a_single_step_signoff_applies_on_its_one_decision() -> None:
    case = _submitted()
    cases = FakeCases(case)
    run = Run(cases)
    await run.start(_input(case, steps=[STEPS[0]]))

    done = await run.decide(True, BOD, "ok")

    assert_state(cases, ProductDevState.READY_TO_ORDER)
    assert done["outcome"] == ProductAction.SIGNOFF_APPROVE.value


@pytest.mark.parametrize("decided_at_step", [1, 2])
async def test_a_case_cancelled_while_a_step_waits_is_superseded(decided_at_step: int) -> None:
    """Cancelled while BGĐ (or later Kế toán) was deciding: the decision
    applies nothing, no later step is raised, and the run says so."""
    case = _submitted()
    cases = FakeCases(case)
    run = Run(cases)
    await run.start(_input(case))
    if decided_at_step == 2:
        await run.decide(True, BOD, "Đồng ý")
    cancelled = ProductDevState.CANCELLED
    cases.case = replace(cases.case, state=cancelled, version=cases.case.version + 1)

    done = await run.decide(True, BOD if decided_at_step == 1 else ACCOUNTANT, "ok")

    assert done["outcome"] == SUPERSEDED
    assert "__interrupt__" not in done
    assert cases.steps == []
    assert cases.audits[-1].action == "supply_chain.product_case.signoff_superseded"
    assert_state(cases, ProductDevState.CANCELLED)


async def test_a_decision_for_an_earlier_signoff_round_is_superseded() -> None:
    """The case was rejected and submitted again meanwhile: a decision for
    round 1 does not sign round 2."""
    case = _submitted()
    cases = FakeCases(case)
    run = Run(cases)
    await run.start(_input(case, steps=[STEPS[0]]))
    cases.case = replace(cases.case, signoff_round=2)

    done = await run.decide(True, BOD, "ok")

    assert done["outcome"] == SUPERSEDED
    assert_state(cases, ProductDevState.PENDING_SIGNOFF)


async def test_a_decider_named_in_the_start_payload_is_not_the_decider() -> None:
    case = _submitted()
    cases = FakeCases(case)
    run = Run(cases)
    await run.start({**_input(case, steps=[STEPS[0]]), "decided_by": str(REQUESTER)})

    await run.decide(True, BOD, "ok")

    (step,) = cases.steps
    assert step.actor_id == BOD


async def test_a_decision_without_a_yes_or_no_fails_the_run() -> None:
    case = _submitted()
    cases = FakeCases(case)
    run = Run(cases)
    await run.start(_input(case))
    with pytest.raises(ValueError, match="yes or no"):
        await run.graph.ainvoke(
            Command(resume={"comment": "?", "decided_by": str(BOD)}),
            run.config,
            context=run.context,
        )
    assert_state(cases, ProductDevState.PENDING_SIGNOFF)
