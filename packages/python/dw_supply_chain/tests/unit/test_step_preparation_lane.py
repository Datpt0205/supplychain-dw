"""Unit: the lane `supply_chain_step_preparation` (ticket ai-automation/05).

The runner is faked as the real one behaves at its edges: a second start on an
unfinished thread is a `ConflictError` naming the thread, and a refused plan is
`QuotaExceededError`. The superseding is the platform's
(`ApproveAndResumeService.supersede_stale`, its own unit tests); here only what
the lane does with its answer.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pytest

from dw_agent_runtime.contracts import RunContext
from dw_kernel.errors import ConflictError, QuotaExceededError
from dw_kernel.ports import FixedClock, Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.application.step_preparation import (
    PrepareSteps,
    lane_actor,
    preparation_thread_id,
)
from dw_supply_chain.domain.product_development_case import ProductDevState
from dw_supply_chain.product_action_duties import load_supply_chain_product_action_duties
from dw_supply_chain.step_preparation_policy import SupplyChainStepPreparation
from dw_supply_chain.testing.step_preparation import (
    NOW,
    REPO_ROOT,
    SAMPLE_TESTING,
    InMemoryRecords,
    InMemoryStepCases,
)

pytestmark = pytest.mark.unit

DUTIES = load_supply_chain_product_action_duties(
    REPO_ROOT / "configs" / "policies" / "supply_chain_product_action_duties@1.3.0.yaml"
)
TENANT, WORKSPACE = uuid.uuid4(), uuid.uuid4()
POLICY: dict[str, object] = {
    "schema_version": "1.0",
    "policy_id": "supply_chain_step_preparation",
    "policy_version": "1.0.0",
    "steps": [SAMPLE_TESTING.model_dump(mode="json")],
}
PLATFORM = SupplyChainStepPreparation.model_validate({**POLICY, "steps": []})


@dataclass
class Overrides:
    by_tenant: dict[uuid.UUID, dict[str, object]] = field(default_factory=dict)
    asked: int = 0

    async def get(self, context: AccessContext, policy_id: str) -> dict[str, object] | None:
        self.asked += 1
        if policy_id != "supply_chain_step_preparation":
            return None
        return self.by_tenant.get(context.tenant_id)

    async def put(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("not exercised by the lane")


@dataclass
class Workspaces:
    pairs: list[tuple[uuid.UUID, uuid.UUID]]

    async def workspaces(self) -> list[tuple[uuid.UUID, uuid.UUID]]:
        return self.pairs


@dataclass
class Plans:
    async def plan_of(self, tenant_id: uuid.UUID) -> str | None:
        return "professional"


@dataclass
class Runner:
    refuse: Exception | None = None
    started: list[tuple[RunContext, dict[str, Any]]] = field(default_factory=list)
    active: set[uuid.UUID] = field(default_factory=set)
    on_start: Any = None

    async def start(self, *, run_context: RunContext, input_payload: dict[str, Any]) -> uuid.UUID:
        if self.refuse is not None:
            raise self.refuse
        if run_context.thread_id in self.active:
            raise ConflictError("thread busy", details={"thread_id": str(run_context.thread_id)})
        self.started.append((run_context, input_payload))
        if self.on_start is not None:
            self.on_start(input_payload)
        return run_context.run_id


@dataclass
class Pending:
    id: uuid.UUID
    approval_type: str
    payload: Mapping[str, object]
    created_at: datetime | None = NOW
    required_scope: str | None = "supply_chain.duty.rnd"


@dataclass
class Approvals:
    by_case: dict[str, Pending] = field(default_factory=dict)

    async def raised_by_payload(
        self, context: AccessContext, *, approval_type: str, key: str, value: str
    ) -> Pending | None:
        found = self.by_case.get(value)
        return found if found is not None and found.approval_type == approval_type else None


@dataclass
class Supersede:
    stale: bool = False
    asked: list[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=list)

    async def supersede_stale(self, *, approval_id: uuid.UUID, context: AccessContext) -> bool:
        self.asked.append((approval_id, context.principal_id))
        return self.stale


@dataclass
class Holders:
    async def holding(
        self, tenant_id: uuid.UUID, workspace_id: uuid.UUID, scopes: frozenset[str]
    ) -> list[uuid.UUID]:
        return [uuid.UUID(int=1)]


@dataclass
class Notifier:
    sent: list[dict[str, Any]] = field(default_factory=list)

    async def deliver(
        self,
        context: AccessContext,
        *,
        recipients: Sequence[uuid.UUID],
        source_key: str,
        title: str,
        body: str,
        link: str | None,
    ) -> None:
        self.sent.append(
            {"recipients": list(recipients), "title": title, "body": body, "link": link}
        )


@dataclass
class Lane:
    cases: InMemoryStepCases = field(default_factory=InMemoryStepCases)
    records: InMemoryRecords = field(default_factory=InMemoryRecords)
    overrides: Overrides = field(default_factory=Overrides)
    runner: Runner = field(default_factory=Runner)
    approvals: Approvals = field(default_factory=Approvals)
    supersede: Supersede = field(default_factory=Supersede)
    notifier: Notifier = field(default_factory=Notifier)
    clock: FixedClock = field(default_factory=lambda: FixedClock(NOW))

    def build(self, pairs: list[tuple[uuid.UUID, uuid.UUID]] | None = None) -> PrepareSteps:
        return PrepareSteps(
            workspaces=Workspaces(pairs or [(TENANT, WORKSPACE)]),
            cases=self.cases,
            policy_override_repo=self.overrides,
            platform_default_policy=PLATFORM,
            platform_default_duties=DUTIES,
            plans=Plans(),
            runner=self.runner,
            approvals=self.approvals,
            supersede=self.supersede,
            records=self.records,
            holders=Holders(),
            notifier=self.notifier,
            ids=Uuid4Generator(),
            clock=self.clock,
            worker_id="supply_chain_step_preparation",
            worker_version="1.0.0",
        )

    def case(self, state: ProductDevState = ProductDevState.SAMPLE_TESTING) -> Any:
        from dw_supply_chain.testing.step_preparation import StepWorld

        world = StepWorld(tenant_id=TENANT, workspace_id=WORKSPACE, cases=self.cases)
        return world.add_case(state)


async def test_a_tenant_on_the_platform_policy_gets_no_run() -> None:
    lane = Lane()
    lane.case()
    outcome = await lane.build().run()
    assert lane.runner.started == [] and outcome.started == 0
    assert lane.records.rows == []


async def test_a_step_in_the_tenants_policy_starts_one_stamped_run_as_the_lane() -> None:
    lane = Lane()
    lane.overrides.by_tenant[TENANT] = POLICY
    case, entered = lane.case()
    lane.case(ProductDevState.ITEM_CODING)  # a state the policy does not list

    outcome = await lane.build().run()

    assert outcome.started == 1
    ((context, payload),) = lane.runner.started
    assert context.thread_id == preparation_thread_id(case.id.value, entered.id, "1.0.0")
    assert context.actor_id == lane_actor()
    assert context.scopes == frozenset() and context.roles == frozenset()
    # Who decides: the duty of the step, read from the duty policy now.
    assert (
        payload["required_scope"]
        == f"supply_chain.duty.{DUTIES.duty_for(SAMPLE_TESTING.action).value}"
    )
    assert payload["transition_id"] == str(entered.id)


async def test_another_tenants_override_does_not_reach_this_tenant() -> None:
    lane = Lane()
    other = uuid.uuid4()
    lane.overrides.by_tenant[other] = POLICY
    lane.case()
    await lane.build([(TENANT, WORKSPACE)]).run()
    assert lane.runner.started == []


async def test_a_current_pending_proposal_is_told_not_raised_again() -> None:
    lane = Lane()
    lane.overrides.by_tenant[TENANT] = POLICY
    case, _ = lane.case()
    lane.approvals.by_case[str(case.id)] = Pending(
        uuid.uuid4(), "supply_chain.step_proposal.pass_sample", {}
    )
    await lane.build().run()
    assert lane.runner.started == []
    (sent,) = lane.notifier.sent
    # A physical step is decided on the case page; no price, no file content.
    assert sent["link"] == f"/supply-chain/product-cases/{case.id}"
    assert "AI" in sent["title"] and lane_actor() not in sent["recipients"]


async def test_a_stale_proposal_is_superseded_by_the_lane_then_prepared_again() -> None:
    lane = Lane()
    lane.overrides.by_tenant[TENANT] = POLICY
    case, _ = lane.case()
    pending = Pending(uuid.uuid4(), "supply_chain.step_proposal.pass_sample", {})
    lane.approvals.by_case[str(case.id)] = pending
    lane.supersede.stale = True
    outcome = await lane.build().run()
    assert lane.supersede.asked == [(pending.id, lane_actor())]
    assert outcome.superseded == 1 and outcome.started == 1
    assert lane.records.outcomes() == ["superseded"]


@pytest.mark.parametrize("outcome", ["rejected", "applied"])
async def test_a_proposal_a_person_decided_is_not_raised_again(outcome: str) -> None:
    lane = Lane()
    lane.overrides.by_tenant[TENANT] = POLICY
    case, entered = lane.case()
    _record(lane, case, entered, outcome)
    await lane.build().run()
    assert lane.runner.started == []


async def test_not_prepared_waits_before_it_is_tried_again() -> None:
    lane = Lane()
    lane.overrides.by_tenant[TENANT] = POLICY
    case, entered = lane.case()
    _record(lane, case, entered, "not_prepared", reason="source_not_read_yet:sample_evaluation")
    await lane.build().run()
    assert lane.runner.started == []
    lane.clock = FixedClock(NOW + timedelta(minutes=6))
    await lane.build().run()
    assert len(lane.runner.started) == 1


async def test_a_refused_run_is_recorded_once_and_ends_the_tenants_turn() -> None:
    lane = Lane()
    lane.overrides.by_tenant[TENANT] = POLICY
    lane.case()
    lane.case()
    lane.runner.refuse = QuotaExceededError("plan quota")
    outcome = await lane.build().run()
    # One attempt for the tenant this tick, one row saying why.
    assert outcome.not_started == 1
    assert lane.records.outcomes() == ["not_prepared"]
    assert lane.records.rows[0][2].reason == "run_refused"
    # The next tick, the same reason adds no row.
    lane.clock = FixedClock(NOW + timedelta(minutes=6))
    lane.records.clock = lane.clock
    await lane.build().run()
    assert lane.records.outcomes() == ["not_prepared"]
    assert len(lane.runner.started) == 0


async def test_a_thread_another_attempt_holds_starts_nothing() -> None:
    lane = Lane()
    lane.overrides.by_tenant[TENANT] = POLICY
    case, entered = lane.case()
    lane.runner.active.add(preparation_thread_id(case.id.value, entered.id, "1.0.0"))
    outcome = await lane.build().run()
    assert outcome.started == 0 and lane.records.rows == []


def _record(lane: Lane, case: Any, entered: Any, outcome: str, reason: str | None = None) -> None:
    from dw_supply_chain.application.step_preparation import NewPreparationRecord
    from dw_supply_chain.domain.step_proposal import PreparationOutcome

    context = AccessContext(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        principal_id=lane_actor(),
        roles=frozenset(),
        scopes=frozenset(),
        plan_id="system",
    )
    lane.records.insert(
        context,
        NewPreparationRecord(
            id=uuid.uuid4(),
            case_id=case.id.value,
            transition_id=entered.id,
            policy_version="1.0.0",
            action="pass_sample",
            outcome=PreparationOutcome(outcome),
            reason=reason or ("person said no" if outcome == "rejected" else None),
        ),
    )


async def test_outcomes_of_different_duties_raise_no_approval() -> None:
    """Elmich's steps 3-5 (ticket ai-automation/09) choose among three steps
    in one approval, which carries ONE stamped scope. A tenant whose duty
    policy gives them to different duties gets no run, and the case says why:
    never an approval stamped with one of them."""
    from dw_supply_chain.testing.step_preparation import SAMPLE_ROUND

    lane = Lane()
    lane.overrides.by_tenant[TENANT] = {**POLICY, "steps": [SAMPLE_ROUND.model_dump(mode="json")]}
    lane.case()
    raw = DUTIES.model_dump(mode="json")
    mixed = type(DUTIES).model_validate(
        {**raw, "action_duties": {**raw["action_duties"], "request_revision": "ordering"}}
    )
    built = lane.build()
    outcome = await PrepareSteps(
        **{
            **{f: getattr(built, f) for f in built.__dataclass_fields__},
            "platform_default_duties": mixed,
        }
    ).run()
    assert lane.runner.started == [] and outcome.not_started == 1
    assert lane.records.rows[-1][2].reason == "outcome_duties_differ"


async def test_outcomes_of_one_duty_stamp_that_duty() -> None:
    from dw_supply_chain.testing.step_preparation import SAMPLE_ROUND

    lane = Lane()
    lane.overrides.by_tenant[TENANT] = {**POLICY, "steps": [SAMPLE_ROUND.model_dump(mode="json")]}
    lane.case()
    await lane.build().run()
    ((_, payload),) = lane.runner.started
    assert payload["required_scope"] == "supply_chain.duty.rnd"
