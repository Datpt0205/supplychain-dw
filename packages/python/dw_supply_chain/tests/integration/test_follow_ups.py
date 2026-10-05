"""Integration: the follow-up sweep end to end, on the real database.

A case goes quiet; the sweep opens a follow-up for the tenant's coordinators,
puts a notification in their inbox and nobody else's, does nothing more the
second time, and resolves it when the supplier writes. Tenancy holds all the
way: another tenant's sweep and list see none of it, the cross-tenant read is
ids only, and the history cannot be deleted by the application.

`test_follow_up_sweep.py` (unit) proves the sweep's decisions against fakes;
this proves the adapters, RLS, the unique episode and the inbox agree with
them.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls

from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.adapters.persistence.identity_provisioning import SqlIdentityBootstrap
from dw_platform.adapters.persistence.membership_admin import SqlMembershipAdminRepository
from dw_platform.adapters.persistence.notifications import SqlNotificationRepository
from dw_platform.adapters.persistence.policy_overrides import SqlPolicyOverrideRepository
from dw_platform.adapters.persistence.scope_holders import SqlScopeHolders
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.notifications import NotificationService
from dw_platform.domain.audit import AuditEvent
from dw_platform.testing.seed_env import seed_test_env
from dw_supply_chain.adapters.persistence.follow_up_repository import (
    SqlFollowUpRepository,
    SqlTenantsWithCases,
)
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.adapters.persistence.supplier_update_repository import (
    SqlSupplierUpdateRepository,
)
from dw_supply_chain.application.follow_up_sweep import SweepFollowUps, sweep_context
from dw_supply_chain.application.handlers import CloseFollowUp, ListFollowUps
from dw_supply_chain.domain.follow_up import FollowUpKind, FollowUpStatus
from dw_supply_chain.domain.po_case import POCase, POCaseId
from dw_supply_chain.domain.supplier_update import (
    SupplierEventType,
    SupplierUpdate,
    SupplierUpdateExtraction,
    SupplierUpdateId,
)
from dw_supply_chain.follow_up_policy import load_supply_chain_follow_up_policy
from dw_supply_chain.policy_files import FOLLOW_UP_POLICY_FILE, SLA_POLICY_FILE
from dw_supply_chain.sla_policy import load_supply_chain_sla_policy

pytestmark = pytest.mark.integration

POLICIES = Path(__file__).resolve().parents[5] / "configs" / "policies"
ALPHA = uuid.UUID("d6b43d0e-c3c6-5dbc-bc08-150621bd9a5d")
ALPHA_WS = uuid.UUID("64764894-718d-5558-ba17-9a2949214063")
BETA = uuid.UUID("6634f09a-d1d7-54a6-aa23-f3f018f41f28")
BETA_WS = uuid.UUID("eda6af16-a0c4-55d3-be0b-163414390572")
RECORDS = "supply_chain.supplier_update.write"


@dataclass(frozen=True)
class _Identity:
    subject: str
    email: str | None
    issuer: str = "https://issuer.test/realms/dw"
    name: str | None = None


@dataclass(frozen=True)
class _Stack:
    sessions: async_sessionmaker[AsyncSession]
    migrator: AsyncEngine

    def sweep(self) -> SweepFollowUps:
        return SweepFollowUps(
            tenants=SqlTenantsWithCases(self.sessions),
            po_case_repo=SqlPOCaseRepository(self.sessions),
            supplier_update_repo=SqlSupplierUpdateRepository(self.sessions),
            policy_override_repo=SqlPolicyOverrideRepository(self.sessions),
            platform_default_sla_policy=load_supply_chain_sla_policy(POLICIES / SLA_POLICY_FILE),
            platform_default_follow_up_policy=load_supply_chain_follow_up_policy(
                POLICIES / FOLLOW_UP_POLICY_FILE
            ),
            follow_up_repo=SqlFollowUpRepository(self.sessions),
            holders=SqlScopeHolders(self.sessions),
            notifier=SqlNotificationRepository(self.sessions),
            ids=Uuid4Generator(),
            clock=SystemClock(),
        )


@pytest.fixture
async def stack(db_urls: DatabaseUrls) -> AsyncIterator[_Stack]:
    await seed_test_env(db_urls.migrator)
    app = create_async_engine(db_urls.app, poolclass=NullPool)
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    yield _Stack(async_sessionmaker(app, expire_on_commit=False), migrator)
    await app.dispose()
    await migrator.dispose()


async def _member(
    stack: _Stack, role: str, tenant: uuid.UUID = ALPHA, workspace: uuid.UUID = ALPHA_WS
) -> AccessContext:
    view = await SqlIdentityBootstrap(
        session_factory=stack.sessions,
        default_tenant_id=tenant,
        default_workspace_id=workspace,
        auto_provision_default_membership=True,
    ).bootstrap(_Identity(subject=f"sub-{uuid.uuid4()}", email=f"{uuid.uuid4().hex[:8]}@fu.test"))
    admin = AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset({"org_admin"}),
        scopes=frozenset({"platform.members.write"}),
        plan_id="professional",
    )
    await SqlMembershipAdminRepository(stack.sessions).grant(
        admin,
        user_id=view.principal_id,
        workspace_id=workspace,
        role_keys=frozenset({role}),
        department="general",
        audit=AuditEvent(
            id=uuid.uuid4(),
            tenant_id=TenantId(tenant),
            workspace_id=WorkspaceId(workspace),
            actor_id=UserId(admin.principal_id),
            action="platform.membership.grant",
            resource_type="membership",
            resource_id=str(view.principal_id),
            occurred_at=datetime.now(UTC),
        ),
    )
    scopes = await _scopes_of(stack, role)
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=view.principal_id,
        roles=frozenset({role}),
        scopes=scopes,
        plan_id="professional",
    )


async def _scopes_of(stack: _Stack, role: str) -> frozenset[str]:
    async with stack.migrator.connect() as conn:
        scopes = await conn.scalar(
            sa.text("SELECT scopes FROM platform.roles WHERE key = :k"), {"k": role}
        )
    return frozenset(scopes)


async def _quiet_case(stack: _Stack, context: AccessContext) -> POCase:
    """A case created a day and an hour ago, never heard from: the shipped
    cadence (1d reminder, 2d escalation) makes a reminder due."""
    case = POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        po_reference=f"PO-FU-{uuid.uuid4().hex[:6]}",
        supplier_name="Kangaroo",
    )
    await SqlPOCaseRepository(stack.sessions).add(context, case)
    async with stack.migrator.begin() as conn:
        await conn.execute(
            sa.text(
                "UPDATE supply_chain.po_cases SET created_at = now() - interval '25 hours'"
                " WHERE id = :id"
            ),
            {"id": case.id.value},
        )
    return case


async def _follow_ups_of(stack: _Stack, case: POCase) -> list[tuple[str, str, list[str]]]:
    async with stack.migrator.connect() as conn:
        rows = (
            await conn.execute(
                sa.text(
                    "SELECT kind, status, recipient_scopes FROM supply_chain.follow_ups"
                    " WHERE po_case_id = :id ORDER BY opened_at"
                ),
                {"id": case.id.value},
            )
        ).all()
    return [(r.kind, r.status, list(r.recipient_scopes)) for r in rows]


async def test_a_quiet_case_reaches_its_coordinator_once_and_resolves_when_the_supplier_writes(
    stack: _Stack,
) -> None:
    operator = await _member(stack, "sc_operator")
    finance = await _member(stack, "sc_finance")
    case = await _quiet_case(stack, operator)
    sweep = stack.sweep()

    await sweep.sweep_tenant(sweep_context(ALPHA, ALPHA_WS))
    await sweep.sweep_tenant(sweep_context(ALPHA, ALPHA_WS))

    assert await _follow_ups_of(stack, case) == [("update_reminder", "open", [RECORDS])]
    inbox = NotificationService(SqlNotificationRepository(stack.sessions))
    mine = [n for n in (await inbox.latest(operator)).items if case.po_reference in n.title]
    assert [(n.title, n.link) for n in mine] == [
        (f"Nhắc NCC cập nhật: {case.po_reference}", f"/supply-chain/po-cases/{case.id.value}")
    ]
    # Finance was not handed a reminder to chase a supplier.
    assert not [n for n in (await inbox.latest(finance)).items if case.po_reference in n.title]

    await SqlSupplierUpdateRepository(stack.sessions).add(
        operator,
        SupplierUpdate(
            id=SupplierUpdateId(uuid.uuid4()),
            tenant_id=case.tenant_id,
            workspace_id=case.workspace_id,
            po_case_id=case.id,
            raw_text="đang sản xuất đúng tiến độ",
            extraction=SupplierUpdateExtraction(
                event_type=SupplierEventType.SHIPMENT_UPDATE,
                confidence=0.95,
                source_ref="đang sản xuất đúng tiến độ",
                reason="cập nhật định kỳ",
                proposed_action="không cần",
            ),
            requires_confirmation=False,
        ),
    )
    await sweep.sweep_tenant(sweep_context(ALPHA, ALPHA_WS))

    assert await _follow_ups_of(stack, case) == [("update_reminder", "resolved", [RECORDS])]


async def test_another_tenant_sees_and_sweeps_none_of_it(stack: _Stack) -> None:
    operator = await _member(stack, "sc_operator")
    case = await _quiet_case(stack, operator)
    await stack.sweep().sweep_tenant(sweep_context(ALPHA, ALPHA_WS))
    beta = await _member(stack, "sc_operator", BETA, BETA_WS)

    listed = await ListFollowUps(
        SqlFollowUpRepository(stack.sessions), ScopeAuthorizationService()
    ).handle(beta)
    assert [v for v in listed if v.record.po_case_id == case.id.value] == []

    # Beta's own sweep resolves nothing of Alpha's.
    await stack.sweep().sweep_tenant(sweep_context(BETA, BETA_WS))
    assert await _follow_ups_of(stack, case) == [("update_reminder", "open", [RECORDS])]


async def test_the_cross_tenant_read_is_ids_only_and_unscoped_reads_nothing(
    stack: _Stack,
) -> None:
    operator = await _member(stack, "sc_operator")
    await _quiet_case(stack, operator)

    tenants = await SqlTenantsWithCases(stack.sessions).tenants()
    assert ALPHA in {tenant for tenant, _ in tenants}

    async with stack.sessions() as session, session.begin():
        visible = await session.scalar(sa.text("SELECT count(*) FROM supply_chain.follow_ups"))
    assert visible == 0


async def test_a_coordinator_closes_it_with_an_audit_row_and_it_cannot_be_deleted(
    stack: _Stack,
) -> None:
    operator = await _member(stack, "sc_operator")
    case = await _quiet_case(stack, operator)
    await stack.sweep().sweep_tenant(sweep_context(ALPHA, ALPHA_WS))
    repo = SqlFollowUpRepository(stack.sessions)
    (open_one,) = [r for r in await repo.list_open(operator) if r.po_case_id == case.id.value]

    await CloseFollowUp(repo, ScopeAuthorizationService(), Uuid4Generator(), SystemClock()).handle(
        operator, open_one.id, "đã gọi NCC"
    )

    closed = await repo.get(operator, open_one.id)
    assert closed is not None
    assert (closed.status, closed.closed_by, closed.close_note) == (
        FollowUpStatus.DONE,
        operator.principal_id,
        "đã gọi NCC",
    )
    async with stack.migrator.connect() as conn:
        audited = await conn.scalar(
            sa.text(
                "SELECT count(*) FROM platform.audit_events"
                " WHERE action = 'supply_chain.follow_up.done' AND resource_id = :id"
            ),
            {"id": str(open_one.id)},
        )
    assert audited == 1
    # A closed episode does not reopen.
    await stack.sweep().sweep_tenant(sweep_context(ALPHA, ALPHA_WS))
    assert [k for k, s, _ in await _follow_ups_of(stack, case)] == [FollowUpKind.UPDATE_REMINDER]

    with pytest.raises(ProgrammingError, match="permission denied"):
        async with stack.sessions() as session, session.begin():
            await session.execute(
                sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(ALPHA)}
            )
            await session.execute(
                sa.text("DELETE FROM supply_chain.follow_ups WHERE id = :id"), {"id": open_one.id}
            )


async def test_whoever_holds_the_scope_through_a_role_is_a_recipient(stack: _Stack) -> None:
    operator = await _member(stack, "sc_operator")
    owner = await _member(stack, "sc_process_admin")
    holders = await SqlScopeHolders(stack.sessions).holding(
        sweep_context(ALPHA, ALPHA_WS), ALPHA_WS, frozenset({RECORDS})
    )
    assert operator.principal_id in holders
    assert owner.principal_id not in holders
