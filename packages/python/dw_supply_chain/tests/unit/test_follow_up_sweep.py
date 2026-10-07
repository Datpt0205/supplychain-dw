"""The follow-up sweep over in-memory ports: what opens, when, for whom, once.

The fakes honour the ports' contracts where the sweep uses them (an episode
opens once; a delivery is once per recipient and key) and refuse the methods
it never calls. What the database itself guarantees is covered against a real
one by the integration suite.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

import pytest

from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.pagination import Page, PageRequest
from dw_kernel.ports import Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.application.follow_up_sweep import SWEEP_PRINCIPAL, SweepFollowUps
from dw_supply_chain.application.ports import FollowUpDraft, FollowUpRecord, POCaseListFilter
from dw_supply_chain.domain.follow_up import FollowUpKind, FollowUpStatus
from dw_supply_chain.domain.po_case import (
    TERMINAL_STATES,
    CaseState,
    CaseTransition,
    POCase,
    POCaseId,
)
from dw_supply_chain.domain.supplier_update import (
    SupplierEventType,
    SupplierUpdate,
    SupplierUpdateExtraction,
    SupplierUpdateId,
)
from dw_supply_chain.follow_up_policy import SupplyChainFollowUpPolicy
from dw_supply_chain.sla_policy import (
    SLAConfirmationStatus,
    SLAMilestone,
    SupplierUpdateCadence,
    SupplyChainSLAPolicy,
)

pytestmark = pytest.mark.unit

TENANT = uuid.uuid4()
WORKSPACE = uuid.uuid4()
OTHER_TENANT = uuid.uuid4()
START = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)
OPERATOR, OWNER = uuid.uuid4(), uuid.uuid4()
RECORDS = "supply_chain.supplier_update.write"
OWNS = "supply_chain.sla_policy.write"


class Clock:
    def __init__(self) -> None:
        self.at = START

    def now(self) -> datetime:
        return self.at


class FakeCases:
    def __init__(self) -> None:
        self.cases: dict[uuid.UUID, POCase] = {}
        self.entered_at: dict[uuid.UUID, datetime] = {}
        self.broken_tenants: set[uuid.UUID] = set()

    async def list_active(self, context: AccessContext) -> list[POCase]:
        if context.tenant_id in self.broken_tenants:
            raise RuntimeError("this tenant's data is unreadable")
        return [
            case
            for case in self.cases.values()
            if case.tenant_id.value == context.tenant_id and case.state not in TERMINAL_STATES
        ]

    async def bulk_current_state_entered_at(
        self, context: AccessContext, case_ids: list[POCaseId]
    ) -> dict[uuid.UUID, datetime]:
        return {i.value: self.entered_at[i.value] for i in case_ids if i.value in self.entered_at}

    async def add(self, context: AccessContext, case: POCase) -> None:
        raise NotImplementedError("not exercised by the sweep")

    async def get(self, context: AccessContext, case_id: POCaseId) -> POCase | None:
        raise NotImplementedError("not exercised by the sweep")

    async def save(
        self, context: AccessContext, case: POCase, *, audit: AuditEvent | None = None
    ) -> None:
        raise NotImplementedError("not exercised by the sweep")

    async def get_current_state_entered_at(
        self, context: AccessContext, case_id: POCaseId
    ) -> datetime | None:
        raise NotImplementedError("not exercised by the sweep")

    async def list_page(
        self, context: AccessContext, request: PageRequest, case_filter: POCaseListFilter
    ) -> Page[POCase]:
        raise NotImplementedError("not exercised by the sweep")

    async def list_transitions(
        self, context: AccessContext, case_id: POCaseId
    ) -> list[CaseTransition]:
        raise NotImplementedError("not exercised by the sweep")

    async def list_supplier_names(self, context: AccessContext) -> list[str]:
        raise NotImplementedError("not exercised by the sweep")

    async def find_by_reference(self, context: AccessContext, po_reference: str) -> list[POCase]:
        raise NotImplementedError("not exercised by the sweep")

    async def get_many(self, context: AccessContext, case_ids: list[POCaseId]) -> list[POCase]:
        raise NotImplementedError("not exercised by the sweep")

    async def list_latest_transitions_since(
        self, context: AccessContext, since: datetime
    ) -> list[tuple[POCaseId, CaseTransition]]:
        raise NotImplementedError("not exercised by the sweep")


class FakeUpdates:
    def __init__(self) -> None:
        self.latest: dict[uuid.UUID, SupplierUpdate] = {}

    async def bulk_latest(
        self, context: AccessContext, case_ids: list[POCaseId]
    ) -> dict[uuid.UUID, SupplierUpdate]:
        return {i.value: self.latest[i.value] for i in case_ids if i.value in self.latest}

    async def add(self, context: AccessContext, update: SupplierUpdate) -> None:
        raise NotImplementedError("not exercised by the sweep")

    async def get(
        self, context: AccessContext, update_id: SupplierUpdateId
    ) -> SupplierUpdate | None:
        raise NotImplementedError("not exercised by the sweep")

    async def list_for_case(
        self, context: AccessContext, po_case_id: POCaseId
    ) -> list[SupplierUpdate]:
        raise NotImplementedError("not exercised by the sweep")


class FakeOverrides:
    def __init__(self) -> None:
        self.docs: dict[tuple[uuid.UUID, str], dict[str, object]] = {}

    async def get(self, context: AccessContext, policy_id: str) -> dict[str, object] | None:
        return self.docs.get((context.tenant_id, policy_id))

    async def put(
        self, context: AccessContext, policy_id: str, content: object, *, audit: AuditEvent
    ) -> None:
        raise NotImplementedError("not exercised by the sweep")


@dataclass
class FakeFollowUps:
    rows: dict[uuid.UUID, FollowUpRecord] = field(default_factory=dict)

    async def open(self, context: AccessContext, drafts: Sequence[FollowUpDraft]) -> int:
        taken = {r.key for r in self.rows.values() if r.po_case_id in _case_ids(drafts)}
        opened = 0
        for draft in drafts:
            if draft.due.key in taken:
                continue  # an episode opens once, open or closed
            case = draft.due.case
            self.rows[draft.id] = FollowUpRecord(
                id=draft.id,
                po_case_id=case.id.value,
                workspace_id=case.workspace_id.value,
                po_reference=case.po_reference,
                supplier_name=case.supplier_name,
                kind=draft.due.kind,
                episode=draft.due.episode,
                milestone=draft.due.milestone,
                days=draft.due.days,
                limit_days=draft.due.limit_days,
                recipient_scopes=draft.recipient_scopes,
                status=FollowUpStatus.OPEN,
                opened_at=START,
                notified_at=None,
                closed_at=None,
                closed_by=None,
                close_note=None,
            )
            taken.add(draft.due.key)
            opened += 1
        return opened

    async def list_open(self, context: AccessContext) -> list[FollowUpRecord]:
        return [r for r in self.rows.values() if r.status is FollowUpStatus.OPEN]

    async def resolve(self, context: AccessContext, follow_up_ids: Sequence[uuid.UUID]) -> None:
        for i in follow_up_ids:
            if self.rows[i].status is FollowUpStatus.OPEN:
                self.rows[i] = replace(
                    self.rows[i], status=FollowUpStatus.RESOLVED, closed_at=START
                )

    async def mark_notified(self, context: AccessContext, follow_up_id: uuid.UUID) -> None:
        self.rows[follow_up_id] = replace(self.rows[follow_up_id], notified_at=START)

    async def get(self, context: AccessContext, follow_up_id: uuid.UUID) -> FollowUpRecord | None:
        return self.rows.get(follow_up_id)

    async def close_done(
        self,
        context: AccessContext,
        follow_up_id: uuid.UUID,
        *,
        note: str | None,
        audit: AuditEvent,
    ) -> bool:
        row = self.rows[follow_up_id]
        if row.status is not FollowUpStatus.OPEN:
            return False
        self.rows[follow_up_id] = replace(row, status=FollowUpStatus.DONE, closed_at=START)
        return True

    def of(self, kind: FollowUpKind) -> list[FollowUpRecord]:
        return [r for r in self.rows.values() if r.kind is kind]


def _case_ids(drafts: Sequence[FollowUpDraft]) -> set[uuid.UUID]:
    return {d.due.case.id.value for d in drafts}


class FakeTenants:
    def __init__(self, *tenants: uuid.UUID) -> None:
        self.ids = tenants or (TENANT,)

    async def tenants(self) -> list[tuple[uuid.UUID, uuid.UUID]]:
        return [(t, WORKSPACE) for t in self.ids]


class FakeHolders:
    def __init__(self) -> None:
        self.by_scope: dict[str, list[uuid.UUID]] = {RECORDS: [OPERATOR], OWNS: [OWNER]}

    async def holding(
        self, context: AccessContext, workspace_id: uuid.UUID, scopes: frozenset[str]
    ) -> list[uuid.UUID]:
        return sorted({u for s in scopes for u in self.by_scope.get(s, [])}, key=str)


@dataclass
class FakeNotifier:
    sent: dict[tuple[uuid.UUID, str], tuple[str, str, str | None]] = field(default_factory=dict)
    contexts: list[AccessContext] = field(default_factory=list)

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
        self.contexts.append(context)
        for recipient in recipients:
            self.sent.setdefault((recipient, source_key), (title, body, link))

    def to(self, user: uuid.UUID) -> list[tuple[str, str, str | None]]:
        return [message for (who, _), message in self.sent.items() if who == user]


_SLA = SupplyChainSLAPolicy(
    schema_version="1.0",
    policy_id="supply_chain_sla",
    policy_version="1.2.0",
    sla={"deposit": SLAMilestone(duration="1d", status=SLAConfirmationStatus.CONFIRMED)},
    supplier_update=SupplierUpdateCadence(reminder_after="1d", escalation_after="2d"),
)
_ROUTING = SupplyChainFollowUpPolicy(
    schema_version="1.0",
    policy_id="supply_chain_follow_ups",
    policy_version="1.0.0",
    recipients={
        FollowUpKind.UPDATE_REMINDER: (RECORDS,),
        FollowUpKind.UPDATE_ESCALATION: (RECORDS, OWNS),
        FollowUpKind.SLA_BREACH: (RECORDS, OWNS),
    },
)


@dataclass
class World:
    cases: FakeCases = field(default_factory=FakeCases)
    updates: FakeUpdates = field(default_factory=FakeUpdates)
    overrides: FakeOverrides = field(default_factory=FakeOverrides)
    follow_ups: FakeFollowUps = field(default_factory=FakeFollowUps)
    holders: FakeHolders = field(default_factory=FakeHolders)
    notifier: FakeNotifier = field(default_factory=FakeNotifier)
    clock: Clock = field(default_factory=Clock)
    tenants: FakeTenants = field(default_factory=FakeTenants)

    def sweep(self) -> SweepFollowUps:
        return SweepFollowUps(
            tenants=self.tenants,
            po_case_repo=self.cases,
            supplier_update_repo=self.updates,
            policy_override_repo=self.overrides,
            platform_default_sla_policy=_SLA,
            platform_default_follow_up_policy=_ROUTING,
            follow_up_repo=self.follow_ups,
            holders=self.holders,
            notifier=self.notifier,
            ids=Uuid4Generator(),
            clock=self.clock,
        )

    def case(
        self,
        *,
        state: CaseState = CaseState.PRODUCTION,
        created_days_ago: float = 0,
        tenant: uuid.UUID = TENANT,
    ) -> POCase:
        created = self.clock.at - timedelta(days=created_days_ago)
        case = POCase(
            id=POCaseId(uuid.uuid4()),
            tenant_id=TenantId(tenant),
            workspace_id=WorkspaceId(WORKSPACE),
            po_reference=f"PO-{len(self.cases.cases) + 1:04d}",
            supplier_name="Kangaroo",
            state=state,
            created_at=created,
        )
        self.cases.cases[case.id.value] = case
        self.cases.entered_at[case.id.value] = created
        return case

    def supplier_writes(self, case: POCase) -> None:
        self.updates.latest[case.id.value] = SupplierUpdate(
            id=SupplierUpdateId(uuid.uuid4()),
            tenant_id=case.tenant_id,
            workspace_id=case.workspace_id,
            po_case_id=case.id,
            raw_text="đang sản xuất",
            extraction=SupplierUpdateExtraction(
                event_type=SupplierEventType.SHIPMENT_UPDATE,
                confidence=0.9,
                source_ref="đang sản xuất",
                reason="cập nhật",
                proposed_action="không",
            ),
            requires_confirmation=False,
            created_at=self.clock.at,
        )


# ---- a supplier's silence ---------------------------------------------------


async def test_a_quiet_supplier_opens_one_reminder_and_notifies_its_holders_once() -> None:
    world = World()
    case = world.case(created_days_ago=1)

    first = await world.sweep().run()
    second = await world.sweep().run()

    (reminder,) = world.follow_ups.of(FollowUpKind.UPDATE_REMINDER)
    assert (first.opened, first.notified) == (1, 1)
    assert (second.opened, second.notified) == (0, 0)
    assert reminder.recipient_scopes == {RECORDS}
    (message,) = world.notifier.to(OPERATOR)
    assert message == (
        f"Nhắc NCC cập nhật: {case.po_reference}",
        "Kangaroo chưa gửi cập nhật 1 ngày.",
        f"/supply-chain/po-cases/{case.id.value}",
    )
    # The owner is not handed a reminder.
    assert world.notifier.to(OWNER) == []


async def test_a_supplier_quiet_past_the_escalation_point_escalates_instead() -> None:
    world = World()
    world.case(created_days_ago=1)
    await world.sweep().run()
    world.clock.at += timedelta(days=1)

    outcome = await world.sweep().run()

    (reminder,) = world.follow_ups.of(FollowUpKind.UPDATE_REMINDER)
    (escalation,) = world.follow_ups.of(FollowUpKind.UPDATE_ESCALATION)
    assert reminder.status is FollowUpStatus.RESOLVED
    assert escalation.status is FollowUpStatus.OPEN
    assert (outcome.opened, outcome.resolved, outcome.notified) == (1, 1, 1)
    assert [m[0] for m in world.notifier.to(OWNER)] == [
        f"Leo thang: {escalation.po_reference} không có cập nhật 2 ngày"
    ]


async def test_an_update_from_the_supplier_resolves_the_follow_up() -> None:
    world = World()
    case = world.case(created_days_ago=1)
    await world.sweep().run()
    world.supplier_writes(case)

    await world.sweep().run()

    assert [r.status for r in world.follow_ups.rows.values()] == [FollowUpStatus.RESOLVED]


async def test_silence_after_an_update_is_a_new_episode_and_a_new_reminder() -> None:
    world = World()
    case = world.case(created_days_ago=1)
    await world.sweep().run()
    world.supplier_writes(case)
    await world.sweep().run()
    world.clock.at += timedelta(days=1)

    outcome = await world.sweep().run()

    reminders = world.follow_ups.of(FollowUpKind.UPDATE_REMINDER)
    assert outcome.opened == 1
    assert sorted(r.status.value for r in reminders) == ["open", "resolved"]
    assert len(world.notifier.to(OPERATOR)) == 2


async def test_a_follow_up_a_person_closed_does_not_reopen_for_the_same_episode() -> None:
    world = World()
    world.case(created_days_ago=1)
    await world.sweep().run()
    (reminder,) = world.follow_ups.of(FollowUpKind.UPDATE_REMINDER)
    await world.follow_ups.close_done(_any_context(), reminder.id, note=None, audit=_audit())

    outcome = await world.sweep().run()

    assert outcome.opened == 0
    assert [r.status for r in world.follow_ups.rows.values()] == [FollowUpStatus.DONE]


async def test_a_case_that_finished_resolves_its_follow_ups() -> None:
    world = World()
    case = world.case(created_days_ago=1)
    await world.sweep().run()
    world.cases.cases[case.id.value] = replace(case, state=CaseState.COMPLETED)

    await world.sweep().run()

    assert [r.status for r in world.follow_ups.rows.values()] == [FollowUpStatus.RESOLVED]


# ---- an SLA breach -------------------------------------------------------------


async def test_a_breached_milestone_opens_an_sla_follow_up_for_the_coordinator_and_owner() -> None:
    world = World()
    case = world.case(state=CaseState.WAITING_DEPOSIT)
    world.supplier_writes(case)  # the supplier is not quiet; only the SLA speaks
    world.cases.entered_at[case.id.value] = world.clock.at - timedelta(days=2)

    await world.sweep().run()

    (breach,) = world.follow_ups.of(FollowUpKind.SLA_BREACH)
    assert (breach.milestone, breach.days, breach.limit_days) == ("deposit", 2, 1)
    for person in (OPERATOR, OWNER):
        assert [m[0] for m in world.notifier.to(person)] == [
            f"Trễ SLA đặt cọc: {case.po_reference}"
        ]


# ---- routing -------------------------------------------------------------------


async def test_recipients_are_the_tenants_routing_stamped_when_it_opens() -> None:
    world = World()
    world.case(created_days_ago=1)
    routed_to_owner = _ROUTING.model_copy(
        update={"recipients": {**_ROUTING.recipients, FollowUpKind.UPDATE_REMINDER: (OWNS,)}}
    )
    world.overrides.docs[(TENANT, "supply_chain_follow_ups")] = routed_to_owner.model_dump(
        mode="json"
    )

    await world.sweep().run()
    # A later edit does not re-route work already handed out.
    world.overrides.docs.clear()
    await world.sweep().run()

    (reminder,) = world.follow_ups.of(FollowUpKind.UPDATE_REMINDER)
    assert reminder.recipient_scopes == {OWNS}
    assert len(world.notifier.to(OWNER)) == 1
    assert world.notifier.to(OPERATOR) == []


async def test_a_delivery_after_the_policy_changed_still_goes_to_whom_it_was_stamped_for() -> None:
    """Opened while routed to the owner, whom nobody held yet; the tenant
    then routes reminders back to coordinators. The follow-up was handed to
    the owner, so it waits for one rather than going to a coordinator."""
    world = World()
    world.case(created_days_ago=1)
    world.holders.by_scope = {RECORDS: [OPERATOR]}
    world.overrides.docs[(TENANT, "supply_chain_follow_ups")] = _ROUTING.model_copy(
        update={"recipients": {**_ROUTING.recipients, FollowUpKind.UPDATE_REMINDER: (OWNS,)}}
    ).model_dump(mode="json")
    await world.sweep().run()
    world.overrides.docs.clear()

    await world.sweep().run()
    assert world.notifier.to(OPERATOR) == []

    world.holders.by_scope = {RECORDS: [OPERATOR], OWNS: [OWNER]}
    await world.sweep().run()
    assert len(world.notifier.to(OWNER)) == 1
    assert world.notifier.to(OPERATOR) == []


async def test_nobody_holding_the_scope_leaves_it_unnotified_until_someone_does() -> None:
    world = World()
    world.case(created_days_ago=1)
    world.holders.by_scope = {}

    first = await world.sweep().run()
    world.holders.by_scope = {RECORDS: [OPERATOR]}
    second = await world.sweep().run()

    assert (first.opened, first.notified) == (1, 0)
    assert second.notified == 1
    assert len(world.notifier.to(OPERATOR)) == 1


async def test_the_tenants_own_cadence_decides_when_a_reminder_is_due() -> None:
    world = World()
    world.case(created_days_ago=0)
    assert (await world.sweep().run()).opened == 0

    world.overrides.docs[(TENANT, "supply_chain_sla")] = _SLA.model_copy(
        update={
            "supplier_update": SupplierUpdateCadence(reminder_after="0d", escalation_after="1d")
        }
    ).model_dump(mode="json")
    assert (await world.sweep().run()).opened == 1


# ---- the sweep as a process ---------------------------------------------------


async def test_one_tenant_failing_does_not_stop_another() -> None:
    world = World(tenants=FakeTenants(OTHER_TENANT, TENANT))
    world.case(created_days_ago=1)
    world.cases.broken_tenants.add(OTHER_TENANT)

    outcome = await world.sweep().run()

    assert (outcome.failed_tenants, outcome.opened, outcome.notified) == (1, 1, 1)


async def test_the_sweep_acts_as_nobody_bound_to_one_tenant() -> None:
    world = World()
    world.case(created_days_ago=1)

    await world.sweep().run()

    (context,) = world.notifier.contexts
    assert context.principal_id == SWEEP_PRINCIPAL
    assert (context.roles, context.scopes) == (frozenset(), frozenset())
    assert (context.tenant_id, context.workspace_id) == (TENANT, WORKSPACE)


def _any_context() -> AccessContext:
    return AccessContext(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        principal_id=OPERATOR,
        roles=frozenset(),
        scopes=frozenset({RECORDS}),
        plan_id="professional",
    )


def _audit() -> AuditEvent:
    return AuditEvent(
        id=uuid.uuid4(),
        tenant_id=TenantId(TENANT),
        workspace_id=WorkspaceId(WORKSPACE),
        actor_id=UserId(OPERATOR),
        action="supply_chain.follow_up.done",
        resource_type="follow_up",
        resource_id="x",
        occurred_at=START,
    )
