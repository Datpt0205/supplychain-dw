"""The follow-up sweep over in-memory ports: what opens, when, for whom, once.

The fakes honour the ports' contracts where the sweep uses them (an episode
opens once; a delivery is once per recipient and key) and refuse the methods
it never calls. What the database itself guarantees is covered against a real
one by the integration suite.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

import pytest

from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.pagination import Page, PageRequest
from dw_kernel.ports import Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent, system_actor
from dw_supply_chain.application.follow_up_sweep import (
    FOLLOW_UP_OPENED,
    FOLLOW_UP_RESOLVED,
    FOLLOW_UP_SWEEP_LANE,
    SWEEP_PRINCIPAL,
    SweepFollowUps,
)
from dw_supply_chain.application.ports import FollowUpDraft, FollowUpRecord, POCaseListFilter
from dw_supply_chain.domain.case_document import CaseKind
from dw_supply_chain.domain.follow_up import FollowUpKind, FollowUpStatus
from dw_supply_chain.domain.po_case import (
    TERMINAL_STATES,
    CaseState,
    CaseTransition,
    POCase,
    POCaseId,
)
from dw_supply_chain.domain.product_development_case import (
    PRODUCT_TERMINAL_STATES,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
)
from dw_supply_chain.domain.supplier_update import (
    SupplierEventType,
    SupplierUpdate,
    SupplierUpdateExtraction,
    SupplierUpdateId,
)
from dw_supply_chain.follow_up_policy import SupplyChainFollowUpPolicy
from dw_supply_chain.sla_policy import (
    ProductCategory,
    SLAConfirmationStatus,
    SLAMilestone,
    SupplierUpdateCadence,
    SupplyChainSLAPolicy,
)
from dw_supply_chain.testing.pages import case_position, newest_first_page

pytestmark = pytest.mark.unit

TENANT = uuid.uuid4()
WORKSPACE = uuid.uuid4()
OTHER_WORKSPACE = uuid.uuid4()
OTHER_TENANT = uuid.uuid4()
START = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)
OPERATOR, OWNER = uuid.uuid4(), uuid.uuid4()
# PICs: members of WORKSPACE holding no recipient scope, so whatever they are
# told they are told as the PIC.
PIC, NEW_PIC, GONE_PIC = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
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

    async def list_page(
        self, context: AccessContext, request: PageRequest, case_filter: POCaseListFilter
    ) -> Page[POCase]:
        # The sweep reads the active cases only, a page at a time, under the
        # caller's tenant as the repository reads them.
        if case_filter != POCaseListFilter(active_only=True):
            raise NotImplementedError("the sweep lists active cases only")
        if context.tenant_id in self.broken_tenants:
            raise RuntimeError("this tenant's data is unreadable")
        active = [
            case
            for case in self.cases.values()
            if case.tenant_id.value == context.tenant_id and case.state not in TERMINAL_STATES
        ]
        return newest_first_page(active, request, case_position)

    async def bulk_current_state_entered_at(
        self, context: AccessContext, case_ids: list[POCaseId]
    ) -> dict[uuid.UUID, datetime]:
        return {i.value: self.entered_at[i.value] for i in case_ids if i.value in self.entered_at}

    async def add(
        self, context: AccessContext, case: POCase, *, audit: AuditEvent | None = None
    ) -> None:
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

    async def list_transitions(
        self, context: AccessContext, case_id: POCaseId, request: PageRequest
    ) -> Page[CaseTransition]:
        raise NotImplementedError("not exercised by the sweep")

    async def list_supplier_names(self, context: AccessContext) -> list[str]:
        raise NotImplementedError("not exercised by the sweep")

    async def find_by_reference(self, context: AccessContext, po_reference: str) -> list[POCase]:
        raise NotImplementedError("not exercised by the sweep")

    async def get_many(self, context: AccessContext, case_ids: list[POCaseId]) -> list[POCase]:
        raise NotImplementedError("not exercised by the sweep")

    async def list_latest_transitions_since(
        self, context: AccessContext, since: datetime, *, limit: int
    ) -> tuple[int, list[tuple[POCaseId, CaseTransition]]]:
        raise NotImplementedError("not exercised by the sweep")


class FakeProducts:
    """`ActiveProductCasesPort`: the context's workspace only, as the table's
    RLS reads."""

    def __init__(self) -> None:
        self.cases: dict[uuid.UUID, ProductDevelopmentCase] = {}
        self.entered_at: dict[uuid.UUID, datetime] = {}

    async def list_active(
        self, context: AccessContext, request: PageRequest
    ) -> Page[ProductDevelopmentCase]:
        active = [
            case
            for case in self.cases.values()
            if case.tenant_id.value == context.tenant_id
            and case.workspace_id.value == context.workspace_id
            and case.state not in PRODUCT_TERMINAL_STATES
        ]
        return newest_first_page(active, request, case_position)

    async def state_entered_at(
        self, context: AccessContext, case_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, datetime]:
        return {i: self.entered_at[i] for i in case_ids if i in self.entered_at}


class FakeUpdates:
    def __init__(self) -> None:
        self.latest: dict[uuid.UUID, SupplierUpdate] = {}

    async def bulk_latest(
        self, context: AccessContext, case_ids: list[POCaseId]
    ) -> dict[uuid.UUID, SupplierUpdate]:
        return {i.value: self.latest[i.value] for i in case_ids if i.value in self.latest}

    async def add(
        self, context: AccessContext, update: SupplierUpdate, *, audit: AuditEvent | None = None
    ) -> None:
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
    """Honours the port: an episode opens once, and every read and write is
    the context's workspace only (the table's RLS, `d56da3dd2146`)."""

    cases: FakeCases
    products: FakeProducts
    rows: dict[uuid.UUID, FollowUpRecord] = field(default_factory=dict)
    # What each write committed with it, as the repository's transaction does.
    audits: list[AuditEvent] = field(default_factory=list)

    def _label(self, kind: CaseKind, case_id: uuid.UUID) -> tuple[str | None, str | None]:
        if kind is CaseKind.PO:
            po = self.cases.cases[case_id]
            return po.po_reference, po.supplier_name
        product = self.products.cases[case_id]
        return product.proposal_code, product.supplier_name

    async def open(
        self,
        context: AccessContext,
        drafts: Sequence[FollowUpDraft],
        *,
        audit: Callable[[FollowUpDraft], AuditEvent],
    ) -> int:
        taken = {r.key for r in self.rows.values()}
        opened = 0
        for draft in drafts:
            subject = draft.due.subject
            assert subject.workspace_id == context.workspace_id  # RLS' WITH CHECK
            if draft.due.key in taken:
                continue  # an episode opens once, open or closed
            reference, supplier = self._label(subject.case_kind, subject.case_id)
            self.rows[draft.id] = FollowUpRecord(
                id=draft.id,
                case_kind=subject.case_kind,
                case_id=subject.case_id,
                workspace_id=subject.workspace_id,
                reference=reference,
                supplier_name=supplier,
                kind=draft.due.kind,
                episode=draft.due.episode,
                milestone=draft.due.milestone,
                days=draft.due.days,
                limit_days=draft.due.limit_days,
                recipient_scopes=draft.recipient_scopes,
                recipient_user_id=draft.recipient_user_id,
                status=FollowUpStatus.OPEN,
                opened_at=START,
                notified_at=None,
                closed_at=None,
                closed_by=None,
                close_note=None,
            )
            taken.add(draft.due.key)
            self.audits.append(audit(draft))
            opened += 1
        return opened

    async def list_open(self, context: AccessContext) -> list[FollowUpRecord]:
        return [
            r
            for r in self.rows.values()
            if r.status is FollowUpStatus.OPEN and r.workspace_id == context.workspace_id
        ]

    async def resolve(
        self,
        context: AccessContext,
        follow_ups: Sequence[FollowUpRecord],
        *,
        audit: Callable[[FollowUpRecord], AuditEvent],
    ) -> int:
        closed = 0
        for record in follow_ups:
            row = self.rows[record.id]
            if row.status is FollowUpStatus.OPEN and row.workspace_id == context.workspace_id:
                self.rows[record.id] = replace(row, status=FollowUpStatus.RESOLVED, closed_at=START)
                self.audits.append(audit(record))
                closed += 1
        return closed

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


class FakeWorkspaces:
    def __init__(self, *workspaces: tuple[uuid.UUID, uuid.UUID]) -> None:
        self.pairs = list(workspaces) or [(TENANT, WORKSPACE)]

    async def workspaces(self) -> list[tuple[uuid.UUID, uuid.UUID]]:
        return self.pairs


class FakeMembers:
    """`WorkspaceMembersPort`: who is a member of which workspace, now."""

    def __init__(self) -> None:
        self.of: dict[uuid.UUID, set[uuid.UUID]] = {
            WORKSPACE: {OPERATOR, OWNER, PIC, NEW_PIC},
            OTHER_WORKSPACE: {GONE_PIC},
        }

    async def members(
        self, tenant_id: uuid.UUID, workspace_id: uuid.UUID, user_ids: frozenset[uuid.UUID]
    ) -> frozenset[uuid.UUID]:
        return frozenset(user_ids & self.of.get(workspace_id, set()))


class FakeHolders:
    def __init__(self) -> None:
        self.by_scope: dict[str, list[uuid.UUID]] = {RECORDS: [OPERATOR], OWNS: [OWNER]}

    async def holding(
        self, tenant_id: uuid.UUID, workspace_id: uuid.UUID, scopes: frozenset[str]
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
    schema_version="2.0",
    policy_id="supply_chain_sla",
    policy_version="2.0.0",
    categories=(ProductCategory(key="noi", label="Nồi"),),
    default={
        "deposit": SLAMilestone(duration="1d", status=SLAConfirmationStatus.CONFIRMED),
        "bm04": SLAMilestone(duration="4d", status=SLAConfirmationStatus.CONFIRMED),
        "supplier_confirmation": SLAMilestone(
            duration="5d", status=SLAConfirmationStatus.PENDING_BUSINESS_CONFIRMATION
        ),
    },
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
# 1.1: every kind also reaches the case's PIC.
_PIC_ROUTING = SupplyChainFollowUpPolicy(
    schema_version="1.1",
    policy_id="supply_chain_follow_ups",
    policy_version="1.1.0",
    recipients={
        FollowUpKind.UPDATE_REMINDER: ("pic", RECORDS),
        FollowUpKind.UPDATE_ESCALATION: ("pic", RECORDS, OWNS),
        FollowUpKind.SLA_BREACH: ("pic", RECORDS, OWNS),
    },
)


@dataclass
class World:
    cases: FakeCases = field(default_factory=FakeCases)
    products: FakeProducts = field(default_factory=FakeProducts)
    updates: FakeUpdates = field(default_factory=FakeUpdates)
    overrides: FakeOverrides = field(default_factory=FakeOverrides)
    holders: FakeHolders = field(default_factory=FakeHolders)
    members: FakeMembers = field(default_factory=FakeMembers)
    notifier: FakeNotifier = field(default_factory=FakeNotifier)
    clock: Clock = field(default_factory=Clock)
    workspaces: FakeWorkspaces = field(default_factory=FakeWorkspaces)
    routing: SupplyChainFollowUpPolicy = _ROUTING

    def __post_init__(self) -> None:
        self.follow_ups = FakeFollowUps(cases=self.cases, products=self.products)

    def sweep(self) -> SweepFollowUps:
        return SweepFollowUps(
            workspaces=self.workspaces,
            po_case_repo=self.cases,
            product_case_repo=self.products,
            supplier_update_repo=self.updates,
            policy_override_repo=self.overrides,
            platform_default_sla_policy=_SLA,
            platform_default_follow_up_policy=self.routing,
            follow_up_repo=self.follow_ups,
            holders=self.holders,
            members=self.members,
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
        workspace: uuid.UUID = WORKSPACE,
        pic: uuid.UUID | None = None,
    ) -> POCase:
        created = self.clock.at - timedelta(days=created_days_ago)
        case = POCase(
            id=POCaseId(uuid.uuid4()),
            tenant_id=TenantId(tenant),
            workspace_id=WorkspaceId(workspace),
            po_reference=f"PO-{len(self.cases.cases) + 1:04d}",
            supplier_name="Kangaroo",
            state=state,
            created_at=created,
            pic_user_id=pic,
        )
        self.cases.cases[case.id.value] = case
        self.cases.entered_at[case.id.value] = created
        return case

    def product(
        self,
        *,
        state: ProductDevState = ProductDevState.PROFILE_IN_PROGRESS,
        in_state_days: float = 5,
        pic: uuid.UUID = PIC,
        workspace: uuid.UUID = WORKSPACE,
    ) -> ProductDevelopmentCase:
        case = ProductDevelopmentCase(
            id=ProductDevelopmentCaseId(uuid.uuid4()),
            tenant_id=TenantId(TENANT),
            workspace_id=WorkspaceId(workspace),
            proposal_code=f"DX-{len(self.products.cases) + 1}",
            product_name="Nồi 24cm",
            category="noi",
            pic_user_id=pic,
            created_by=pic,
            state=state,
            created_at=self.clock.at - timedelta(days=30),
        )
        self.products.cases[case.id.value] = case
        self.products.entered_at[case.id.value] = self.clock.at - timedelta(days=in_state_days)
        return case

    def move(self, case: ProductDevelopmentCase, state: ProductDevState) -> None:
        self.products.cases[case.id.value].state = state
        self.products.entered_at[case.id.value] = self.clock.at

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
        f"Leo thang: {escalation.reference} không có cập nhật 2 ngày"
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


async def test_one_workspace_failing_does_not_stop_another() -> None:
    world = World(workspaces=FakeWorkspaces((OTHER_TENANT, WORKSPACE), (TENANT, WORKSPACE)))
    world.case(created_days_ago=1)
    world.cases.broken_tenants.add(OTHER_TENANT)

    outcome = await world.sweep().run()

    assert (outcome.failed_workspaces, outcome.opened, outcome.notified) == (1, 1, 1)


async def test_the_sweep_acts_as_nobody_bound_to_one_tenant() -> None:
    world = World()
    world.case(created_days_ago=1)

    await world.sweep().run()

    (context,) = world.notifier.contexts
    assert context.principal_id == SWEEP_PRINCIPAL
    assert (context.roles, context.scopes) == (frozenset(), frozenset())
    assert (context.tenant_id, context.workspace_id) == (TENANT, WORKSPACE)


# ---- product cases (stage-1 ticket 06) ----------------------------------------


async def test_a_product_case_past_its_bm04_days_opens_a_breach_for_its_pic_and_the_scopes() -> (
    None
):
    world = World(routing=_PIC_ROUTING)
    case = world.product(in_state_days=5)

    outcome = await world.sweep().run()

    (breach,) = world.follow_ups.of(FollowUpKind.SLA_BREACH)
    assert (breach.case_kind, breach.case_id) == (CaseKind.PRODUCT, case.id.value)
    assert (breach.milestone, breach.days, breach.limit_days) == ("bm04", 5, 4)
    assert breach.recipient_user_id == PIC
    assert (outcome.opened, outcome.notified) == (1, 1)
    for person in (PIC, OPERATOR, OWNER):
        assert world.notifier.to(person) == [
            (
                "Trễ SLA BM04: hồ sơ phát triển DX-1",
                "5 ngày ở bước này, hạn 4 ngày.",
                f"/supply-chain/product-cases/{case.id.value}",
            )
        ]


async def test_a_product_case_within_its_days_or_on_a_pending_number_opens_nothing() -> None:
    world = World(routing=_PIC_ROUTING)
    world.product(in_state_days=3)  # bm04 is 4 days
    world.product(state=ProductDevState.SUPPLIER_CONFIRMATION, in_state_days=30)  # pending

    assert (await world.sweep().run()).opened == 0


async def test_a_product_case_that_moves_on_resolves_its_breach() -> None:
    world = World(routing=_PIC_ROUTING)
    case = world.product(in_state_days=5)
    await world.sweep().run()

    world.move(case, ProductDevState.SUPPLIER_CONFIRMATION)
    outcome = await world.sweep().run()

    assert outcome.resolved == 1
    assert [r.status for r in world.follow_ups.rows.values()] == [FollowUpStatus.RESOLVED]


async def test_after_reassign_a_new_follow_up_goes_to_the_new_pic_the_old_keeps_its_own() -> None:
    world = World(routing=_PIC_ROUTING)
    first_case = world.product(in_state_days=5)
    await world.sweep().run()
    (old,) = world.follow_ups.of(FollowUpKind.SLA_BREACH)

    first_case.pic_user_id = NEW_PIC  # reassign_pic
    second_case = world.product(in_state_days=6, pic=NEW_PIC)
    await world.sweep().run()

    assert world.follow_ups.rows[old.id].recipient_user_id == PIC
    (new,) = [r for r in world.follow_ups.of(FollowUpKind.SLA_BREACH) if r.id != old.id]
    assert (new.case_id, new.recipient_user_id) == (second_case.id.value, NEW_PIC)
    # The old one was told once, to whom it was stamped for; never re-sent.
    assert [m[0] for m in world.notifier.to(PIC)] == ["Trễ SLA BM04: hồ sơ phát triển DX-1"]
    assert [m[0] for m in world.notifier.to(NEW_PIC)] == ["Trễ SLA BM04: hồ sơ phát triển DX-2"]


async def test_a_po_case_with_no_pic_reaches_the_scopes_only() -> None:
    world = World(routing=_PIC_ROUTING)
    world.case(created_days_ago=1, pic=None)

    await world.sweep().run()

    (reminder,) = world.follow_ups.of(FollowUpKind.UPDATE_REMINDER)
    assert reminder.recipient_user_id is None
    assert len(world.notifier.to(OPERATOR)) == 1


async def test_a_po_case_carries_its_pic_too() -> None:
    world = World(routing=_PIC_ROUTING)
    world.case(created_days_ago=1, pic=PIC)

    await world.sweep().run()

    (reminder,) = world.follow_ups.of(FollowUpKind.UPDATE_REMINDER)
    assert reminder.recipient_user_id == PIC
    assert len(world.notifier.to(PIC)) == 1


async def test_a_pic_no_longer_in_the_workspace_is_not_stamped_and_the_scopes_still_are() -> None:
    world = World(routing=_PIC_ROUTING)
    world.product(in_state_days=5, pic=GONE_PIC)

    await world.sweep().run()

    (breach,) = world.follow_ups.of(FollowUpKind.SLA_BREACH)
    assert breach.recipient_user_id is None
    assert world.notifier.to(GONE_PIC) == []
    assert len(world.notifier.to(OPERATOR)) == 1


async def test_membership_is_asked_again_when_the_notice_is_sent() -> None:
    world = World(routing=_PIC_ROUTING)
    world.holders.by_scope = {}
    world.product(in_state_days=5)
    # Opened and stamped while the PIC was a member, but not yet sent: drop
    # the PIC between the open and the delivery.
    original_open = world.follow_ups.open

    async def open_then_leave(
        context: AccessContext,
        drafts: Sequence[FollowUpDraft],
        *,
        audit: Callable[[FollowUpDraft], AuditEvent],
    ) -> int:
        opened = await original_open(context, drafts, audit=audit)
        world.members.of[WORKSPACE].discard(PIC)
        return opened

    world.follow_ups.open = open_then_leave  # type: ignore[method-assign]
    outcome = await world.sweep().run()

    (breach,) = world.follow_ups.of(FollowUpKind.SLA_BREACH)
    assert breach.recipient_user_id == PIC
    assert outcome.notified == 0
    assert world.notifier.to(PIC) == []


async def test_a_1_0_routing_never_stamps_the_pic() -> None:
    world = World()  # _ROUTING, 1.0
    world.product(in_state_days=5)

    await world.sweep().run()

    (breach,) = world.follow_ups.of(FollowUpKind.SLA_BREACH)
    assert breach.recipient_user_id is None
    assert world.notifier.to(PIC) == []


async def test_each_workspace_is_swept_under_its_own_context_and_takes_its_own_cases() -> None:
    world = World(
        routing=_PIC_ROUTING,
        workspaces=FakeWorkspaces((TENANT, WORKSPACE), (TENANT, OTHER_WORKSPACE)),
    )
    world.members.of[OTHER_WORKSPACE] |= {OPERATOR}
    mine = world.case(created_days_ago=1)
    theirs = world.case(created_days_ago=1, workspace=OTHER_WORKSPACE)
    other_product = world.product(in_state_days=5, workspace=OTHER_WORKSPACE, pic=GONE_PIC)

    await world.sweep().run()

    by_case = {r.case_id: r for r in world.follow_ups.rows.values()}
    assert by_case[mine.id.value].workspace_id == WORKSPACE
    assert by_case[theirs.id.value].workspace_id == OTHER_WORKSPACE
    assert by_case[other_product.id.value].workspace_id == OTHER_WORKSPACE
    # GONE_PIC is a member of the other workspace: told about that case there.
    assert by_case[other_product.id.value].recipient_user_id == GONE_PIC
    assert {c.workspace_id for c in world.notifier.contexts} == {WORKSPACE, OTHER_WORKSPACE}
    assert len(world.follow_ups.rows) == 3


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


# -- the sweep audits its own open and resolve, as itself --------------------


async def test_the_sweep_audits_each_follow_up_it_opens_and_resolves_as_its_lane() -> None:
    world = World()
    case = world.case(created_days_ago=1)
    await world.sweep().run()
    await world.sweep().run()  # nothing new: nothing audited twice
    world.supplier_writes(case)
    await world.sweep().run()

    (reminder,) = world.follow_ups.rows.values()
    assert [(e.action, e.resource_id) for e in world.follow_ups.audits] == [
        (FOLLOW_UP_OPENED, str(reminder.id)),
        (FOLLOW_UP_RESOLVED, str(reminder.id)),
    ]
    for event in world.follow_ups.audits:
        # The lane, never a person, and never the recipient it told.
        assert event.actor_id == system_actor(FOLLOW_UP_SWEEP_LANE)
        assert event.details["actor"] == f"system:{FOLLOW_UP_SWEEP_LANE}"
        assert (event.tenant_id.value, event.workspace_id.value) == (TENANT, WORKSPACE)
        assert event.details["case_id"] == str(case.id.value)
    assert world.follow_ups.audits[0].details["kind"] == FollowUpKind.UPDATE_REMINDER.value


async def test_an_episode_that_already_opened_is_neither_reopened_nor_audited_again() -> None:
    world = World()
    world.case(created_days_ago=1)
    await world.sweep().run()
    (row,) = world.follow_ups.rows.values()
    world.follow_ups.rows[row.id] = replace(row, status=FollowUpStatus.DONE)

    await world.sweep().run()

    assert [e.action for e in world.follow_ups.audits] == [FOLLOW_UP_OPENED]
