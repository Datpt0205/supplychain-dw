"""Integration: ĐẶT HÀNG, the hand-off from step 9 to step 10, and step 10
itself (stage-1 ticket 05, ADR 0017).

What only the real database can show:

- the product case moves to `ordered` and the PO case opens in
  `order_requested`, with its lines and both audit events, in one
  transaction; its PIC is the product case row's, never the clicker;
- two clicks at once, as two real transactions, open one PO case: the
  conditional UPDATE makes the second wait on the row lock and update
  nothing; an order on a snapshot of a case cancelled meanwhile opens
  nothing;
- the table refuses a PO case past `order_requested` without a reference,
  and a second PO case for one product case (`dw_app`, raw SQL);
- another tenant or workspace neither orders the case (404) nor reads or
  writes its lines (RLS);
- a tenant's PO step-to-duty override stored before `create_po` existed
  loads after the migration's data step and its steps run;
- one product from step 1, through BGĐ and the sign-off decided by members
  in their roles, ĐẶT HÀNG and step 10, to the PO case's `completed`.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from supply_chain_harness import REPO_ROOT, DatabaseUrls
from test_product_bod_review import (
    ALPHA,
    ALPHA_WS,
    BOD_SCOPE,
    OPERATOR_SCOPES,
    World,
    _another_workspace,
    _member,
    _pending,
    _rnd,
)
from test_product_bod_review import ReviewStack as Stack
from test_product_bod_review import _context as _alpha
from test_product_bod_review import _count as _alpha_count
from test_product_cases import _audit, _coded, _coding, _count, _Db, _reloaded
from test_product_signoff import ACCOUNTING_SCOPE, _decide, _document, _step

from dw_kernel.errors import ConflictError, NotFoundError, PermissionDeniedError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.adapters.persistence.notifications import SqlNotificationRepository
from dw_platform.adapters.persistence.offboarding import SqlTenantOffboarding
from dw_platform.adapters.persistence.policy_overrides import SqlPolicyOverrideRepository
from dw_platform.adapters.persistence.scope_holders import SqlScopeHolders
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.notifications import NotificationService
from dw_platform.testing.seed_env import seed_test_env
from dw_supply_chain.action_duties import CaseDuty, load_supply_chain_action_duties
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.adapters.persistence.product_case_repository import (
    SqlProductCaseRepository,
)
from dw_supply_chain.application.handlers import (
    PRODUCT_CASE_READ,
    PRODUCT_CASE_WRITE,
    AdvancePOCase,
    CreatePO,
    CreatePOCase,
    duty_scope,
)
from dw_supply_chain.application.product_cases import OrderPlaced, PlaceOrder, ProposeProductCase
from dw_supply_chain.approval_matrix import load_supply_chain_approval_matrix
from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.po_case import (
    CaseAction,
    CaseState,
    OrderKind,
    POCase,
    POCaseId,
)
from dw_supply_chain.domain.product_development_case import (
    ProductAction,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
    SkuDraft,
)
from dw_supply_chain.policy_files import PRODUCT_ACTION_DUTIES_POLICY_FILE, SLA_POLICY_FILE
from dw_supply_chain.product_action_duties import load_supply_chain_product_action_duties
from dw_supply_chain.sla_policy import load_supply_chain_sla_policy

pytestmark = pytest.mark.integration

_POLICIES = REPO_ROOT / "configs" / "policies"
_PRODUCT_DUTIES = load_supply_chain_product_action_duties(
    _POLICIES / PRODUCT_ACTION_DUTIES_POLICY_FILE
)
_PO_DUTIES = load_supply_chain_action_duties(_POLICIES / "supply_chain_action_duties@1.1.0.yaml")
_SLA = load_supply_chain_sla_policy(_POLICIES / SLA_POLICY_FILE)
_MATRIX = load_supply_chain_approval_matrix(_POLICIES / "supply_chain_approval_matrix@1.0.0.yaml")
_MIGRATION = (
    REPO_ROOT
    / "db"
    / "migrations"
    / "versions"
    / "84d1c1946b44_supply_chain_place_order_hand_off_to_.py"
)

ORDERING = duty_scope(CaseDuty.ORDERING)
_READ_PO = "supply_chain.po_case.read"
_WRITE_PO = "supply_chain.po_case.write"


def _with(context: AccessContext, *scopes: str) -> AccessContext:
    return AccessContext(
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        principal_id=context.principal_id,
        roles=context.roles,
        scopes=frozenset(scopes),
        plan_id=context.plan_id,
    )


def _fresh(*scopes: str, tenant: uuid.UUID | None = None) -> AccessContext:
    return AccessContext(
        tenant_id=tenant or uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=frozenset(scopes),
        plan_id="professional",
    )


@pytest.fixture
async def db(db_urls: DatabaseUrls) -> AsyncIterator[_Db]:
    app = create_async_engine(db_urls.app, poolclass=NullPool)
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    yield _Db(async_sessionmaker(app, expire_on_commit=False), migrator)
    await app.dispose()
    await migrator.dispose()


@pytest.fixture
async def world(db_urls: DatabaseUrls) -> AsyncIterator[World]:
    await seed_test_env(db_urls.migrator)
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    stack = Stack(db_urls.app)
    yield World(db_urls, migrator, stack)
    await stack.dispose()
    await migrator.dispose()


def _place_order(
    sessions: async_sessionmaker[AsyncSession], repo: SqlProductCaseRepository | None = None
) -> PlaceOrder:
    return PlaceOrder(
        repo=repo or SqlProductCaseRepository(sessions),
        authz=ScopeAuthorizationService(),
        policy_override_repo=SqlPolicyOverrideRepository(sessions),
        platform_default_duties=_PRODUCT_DUTIES,
        platform_default_action_duties=_PO_DUTIES,
        holders=SqlScopeHolders(sessions),
        notifier=SqlNotificationRepository(sessions),
        ids=Uuid4Generator(),
        clock=SystemClock(),
    )


def _create_po(sessions: async_sessionmaker[AsyncSession]) -> CreatePO:
    return CreatePO(
        repo=SqlPOCaseRepository(sessions),
        authz=ScopeAuthorizationService(),
        policy_override_repo=SqlPolicyOverrideRepository(sessions),
        platform_default_action_duties=_PO_DUTIES,
        holders=SqlScopeHolders(sessions),
        notifier=SqlNotificationRepository(sessions),
        ids=Uuid4Generator(),
        clock=SystemClock(),
    )


def _advance_po(stack: Stack) -> AdvancePOCase:
    return AdvancePOCase(
        repo=SqlPOCaseRepository(stack.sessions),
        authz=ScopeAuthorizationService(),
        policy_override_repo=stack.policies,
        platform_default_approval_matrix=_MATRIX,
        platform_default_action_duties=_PO_DUTIES,
        runner=stack.runner,
        ids=Uuid4Generator(),
    )


async def _ready(db: _Db, context: AccessContext) -> ProductDevelopmentCase:
    """A case of a fresh tenant, coded (two SKUs of 50) and signed: ready to
    order. Steps saved as the S1-S4 suites save them."""
    case = await _coded(db, context, await _coding(db, context))
    case.submit_for_signoff(actor_id=context.principal_id)
    await db.cases.save(context, case, audit=_audit(context, case, "submit_for_signoff"))
    case.signoff_approve(actor_id=uuid.uuid4())
    await db.cases.save(context, case, audit=_audit(context, case, "signoff_approve"))
    return await _reloaded(db, context, case)


async def _po_rows(db: _Db, case_id: uuid.UUID) -> int:
    return await _count(
        db, "SELECT count(*) FROM supply_chain.po_cases WHERE product_dev_case_id = :c", c=case_id
    )


# --- the hand-off ------------------------------------------------------------------------


async def test_placing_the_order_opens_the_po_case_with_the_rows_stamps_in_one_transaction(
    db: _Db,
) -> None:
    owner = _fresh()
    case = await _ready(db, owner)
    # Someone else of the workspace clicks: the PIC stays the product case's.
    clicker = AccessContext(
        tenant_id=owner.tenant_id,
        workspace_id=owner.workspace_id,
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=frozenset({ORDERING}),
        plan_id="professional",
    )

    placed = await _place_order(db.sessions).handle(clicker, case_id=case.id)

    assert placed.case.state is ProductDevState.ORDERED
    stored = await _reloaded(db, owner, case)
    assert stored.state is ProductDevState.ORDERED
    async with db.migrator.connect() as conn:
        row = (
            await conn.execute(
                sa.text(
                    "SELECT p.*, d.pic_user_id AS case_pic FROM supply_chain.po_cases p"
                    " JOIN supply_chain.product_dev_cases d ON d.id = p.product_dev_case_id"
                    " WHERE p.id = :p"
                ),
                {"p": placed.po_case.id.value},
            )
        ).one()
        lines = (
            await conn.execute(
                sa.text(
                    "SELECT sku_id, quantity, workspace_id FROM supply_chain.po_case_lines"
                    " WHERE po_case_id = :p"
                ),
                {"p": placed.po_case.id.value},
            )
        ).all()
    assert (row.state, row.po_reference, row.order_kind) == ("order_requested", None, "new")
    assert row.product_dev_case_id == case.id.value
    assert row.pic_user_id == row.case_pic == owner.principal_id != clicker.principal_id
    assert (row.category, row.supplier_name) == (case.category, case.supplier_name)
    assert (row.tenant_id, row.workspace_id) == (owner.tenant_id, owner.workspace_id)
    assert sorted((line.sku_id, line.quantity) for line in lines) == sorted(
        (sku.id, 50) for sku in case.skus
    )
    assert {line.workspace_id for line in lines} == {owner.workspace_id}
    # Both rows audited, in the transaction that wrote them.
    assert (
        await _count(
            db,
            "SELECT count(*) FROM platform.audit_events WHERE actor_id = :a AND ("
            " (action = 'supply_chain.product_case.place_order' AND resource_id = :c)"
            " OR (action = 'supply_chain.po_case.order_requested' AND resource_id = :p))",
            a=clicker.principal_id,
            c=str(case.id),
            p=str(placed.po_case.id),
        )
        == 2
    )
    history = await db.cases.list_transitions(owner, case.id)
    assert (history[-1].action, history[-1].actor_id) == (
        ProductAction.PLACE_ORDER,
        clicker.principal_id,
    )
    # The PO case read back carries the stamps and the lines, the SKUs named.
    po = await SqlPOCaseRepository(db.sessions).get(owner, placed.po_case.id)
    assert po is not None and po.pic_user_id == owner.principal_id
    assert sorted(line.sku_code or "" for line in po.lines) == sorted(
        sku.sku_code for sku in case.skus
    )
    assert await db.cases.po_case_of(owner, case.id.value) == placed.po_case.id.value


@dataclass(frozen=True)
class _ReadTogether(SqlProductCaseRepository):
    """Both clicks read the case before either writes, as two people looking at
    the same page do."""

    barrier: asyncio.Barrier = field(default_factory=lambda: asyncio.Barrier(2))

    async def get(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> ProductDevelopmentCase | None:
        found = await super().get(context, case_id)
        await self.barrier.wait()
        return found


async def test_two_clicks_at_once_open_one_po_case(db: _Db) -> None:
    owner = _fresh(ORDERING)
    case = await _ready(db, owner)
    order = _place_order(db.sessions, _ReadTogether(db.sessions))

    results = await asyncio.gather(
        order.handle(owner, case_id=case.id),
        order.handle(_with(owner, ORDERING), case_id=case.id),
        return_exceptions=True,
    )

    placed = [r for r in results if isinstance(r, OrderPlaced)]
    refused = [r for r in results if isinstance(r, ConflictError)]
    assert (len(placed), len(refused)) == (1, 1), results
    # The conditional UPDATE answered, before anything was inserted.
    assert "constraint" not in refused[0].details
    assert await _po_rows(db, case.id.value) == 1
    history = await db.cases.list_transitions(owner, case.id)
    assert [t.action for t in history].count(ProductAction.PLACE_ORDER) == 1
    assert (
        await _count(
            db,
            "SELECT count(*) FROM platform.audit_events"
            " WHERE action = 'supply_chain.product_case.place_order' AND resource_id = :c",
            c=str(case.id),
        )
        == 1
    )


async def test_an_order_on_a_case_cancelled_meanwhile_opens_nothing(db: _Db) -> None:
    """The page was loaded ready to order; someone cancels; the click lands on
    the old snapshot. The UPDATE names the state and version read, so nothing
    moves and nothing is inserted."""
    owner = _fresh(ORDERING)
    case = await _ready(db, owner)
    seen = await _reloaded(db, owner, case)
    case.cancel(actor_id=owner.principal_id, reason="NCC ngừng sản xuất")
    await db.cases.save(owner, case, audit=_audit(owner, case, "cancel"))

    po_case = seen.place_order(actor_id=owner.principal_id, po_case_id=POCaseId(uuid.uuid4()))
    with pytest.raises(ConflictError):
        await db.cases.place_order(owner, seen, po_case, audits=(_audit(owner, seen, "x"),))

    assert (await _reloaded(db, owner, case)).state is ProductDevState.CANCELLED
    assert await _po_rows(db, case.id.value) == 0


async def test_a_second_click_is_a_409_and_opens_nothing(db: _Db) -> None:
    owner = _fresh(ORDERING)
    case = await _ready(db, owner)
    order = _place_order(db.sessions)
    await order.handle(owner, case_id=case.id)

    with pytest.raises(ConflictError):
        await order.handle(owner, case_id=case.id)
    assert await _po_rows(db, case.id.value) == 1


@pytest.mark.parametrize("scopes", [frozenset(), frozenset({"supply_chain.duty.rnd", _WRITE_PO})])
async def test_ordering_needs_the_ordering_duty(db: _Db, scopes: frozenset[str]) -> None:
    owner = _fresh(ORDERING)
    case = await _ready(db, owner)
    without = _with(owner, *scopes)

    with pytest.raises(PermissionDeniedError):
        await _place_order(db.sessions).handle(without, case_id=case.id)
    assert (await _reloaded(db, owner, case)).state is ProductDevState.READY_TO_ORDER
    assert await _po_rows(db, case.id.value) == 0


@pytest.mark.parametrize("other", ["tenant", "workspace"])
async def test_another_tenant_or_workspace_does_not_find_the_case(db: _Db, other: str) -> None:
    owner = _fresh(ORDERING)
    case = await _ready(db, owner)
    caller = _fresh(ORDERING, tenant=None if other == "tenant" else owner.tenant_id)

    with pytest.raises(NotFoundError):
        await _place_order(db.sessions).handle(caller, case_id=case.id)
    assert await _po_rows(db, case.id.value) == 0


# --- what the table refuses ---------------------------------------------------------------


async def test_a_po_case_past_order_requested_without_a_reference_is_refused(db: _Db) -> None:
    caller = _fresh()
    async with tenant_session(db.sessions, TenantScope.from_access_context(caller)) as session:
        await session.execute(
            sa.text(
                "INSERT INTO supply_chain.po_cases"
                " (id, tenant_id, workspace_id, po_reference, supplier_name, state, order_kind)"
                " VALUES (:i, :t, :w, NULL, 'NCC', 'order_requested', 'new')"
            ),
            {"i": uuid.uuid4(), "t": caller.tenant_id, "w": caller.workspace_id},
        )
    for state in ("po_created", "production", "completed"):
        with pytest.raises(IntegrityError, match="ck_po_cases_po_reference"):
            async with tenant_session(
                db.sessions, TenantScope.from_access_context(caller)
            ) as session:
                await session.execute(
                    sa.text(
                        "INSERT INTO supply_chain.po_cases (id, tenant_id, workspace_id,"
                        " po_reference, supplier_name, state, order_kind)"
                        " VALUES (:i, :t, :w, NULL, 'NCC', :s, 'reorder')"
                    ),
                    {
                        "i": uuid.uuid4(),
                        "t": caller.tenant_id,
                        "w": caller.workspace_id,
                        "s": state,
                    },
                )


async def test_a_second_po_case_for_one_product_case_is_refused(db: _Db) -> None:
    """The database's second answer, behind the conditional UPDATE: one PO
    case per product case while QE-12 is open (ADR 0017 amendment)."""
    owner = _fresh(ORDERING)
    case = await _ready(db, owner)
    await _place_order(db.sessions).handle(owner, case_id=case.id)

    with pytest.raises(
        IntegrityError, match="uq_po_cases_tenant_id_workspace_id_product_dev_case_id"
    ):
        async with tenant_session(db.sessions, TenantScope.from_access_context(owner)) as session:
            await session.execute(
                sa.text(
                    "INSERT INTO supply_chain.po_cases (id, tenant_id, workspace_id,"
                    " po_reference, supplier_name, state, order_kind, product_dev_case_id,"
                    " pic_user_id, category)"
                    " VALUES (:i, :t, :w, NULL, 'NCC', 'order_requested', 'new', :c, :p, 'Nồi')"
                ),
                {
                    "i": uuid.uuid4(),
                    "t": owner.tenant_id,
                    "w": owner.workspace_id,
                    "c": case.id.value,
                    "p": owner.principal_id,
                },
            )


async def test_an_ordered_product_case_cannot_be_deleted_under_its_po_case(db: _Db) -> None:
    owner = _fresh(ORDERING)
    case = await _ready(db, owner)
    await _place_order(db.sessions).handle(owner, case_id=case.id)

    with pytest.raises(IntegrityError, match="fk_po_cases_tenant_id_product_dev_cases"):
        async with tenant_session(db.sessions, TenantScope.from_access_context(owner)) as session:
            await session.execute(
                sa.text("DELETE FROM supply_chain.product_dev_cases WHERE id = :c"),
                {"c": case.id.value},
            )


@pytest.mark.parametrize("other", ["tenant", "workspace"])
async def test_another_tenant_or_workspace_neither_reads_nor_writes_the_lines(
    db: _Db, other: str
) -> None:
    owner = _fresh(ORDERING)
    case = await _ready(db, owner)
    placed = await _place_order(db.sessions).handle(owner, case_id=case.id)
    caller = _fresh(tenant=None if other == "tenant" else owner.tenant_id)

    async with tenant_session(db.sessions, TenantScope.from_access_context(caller)) as session:
        seen = await session.scalar(
            sa.text("SELECT count(*) FROM supply_chain.po_case_lines WHERE po_case_id = :p"),
            {"p": placed.po_case.id.value},
        )
        changed = await session.execute(
            sa.text("UPDATE supply_chain.po_case_lines SET quantity = 1 WHERE po_case_id = :p"),
            {"p": placed.po_case.id.value},
        )
        assert (seen, changed.rowcount) == (0, 0)  # type: ignore[attr-defined]
    # A row naming the owner's tenant, workspace, case and SKU: the FKs accept
    # it, the policy's WITH CHECK does not.
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(db.sessions, TenantScope.from_access_context(caller)) as session:
            await session.execute(
                sa.text(
                    "INSERT INTO supply_chain.po_case_lines"
                    " (id, tenant_id, workspace_id, po_case_id, sku_id, quantity)"
                    " VALUES (:i, :t, :w, :p, :s, 9)"
                ),
                {
                    "i": uuid.uuid4(),
                    "t": owner.tenant_id,
                    "w": owner.workspace_id,
                    "p": placed.po_case.id.value,
                    "s": case.skus[0].id,
                },
            )
    seen_case = await SqlPOCaseRepository(db.sessions).get(caller, placed.po_case.id)
    if other == "tenant":
        assert seen_case is None
    else:
        # `po_cases` is narrowed by tenant only (as since fddd7579ba27); its
        # lines by workspace too, so another workspace sees none of them.
        assert seen_case is not None and seen_case.lines == ()


# --- step 10 -----------------------------------------------------------------------------


async def test_create_po_sets_the_reference_and_a_taken_one_is_a_409_naming_it(db: _Db) -> None:
    owner = _fresh(ORDERING, _READ_PO, _WRITE_PO)
    taken = f"PO-{uuid.uuid4().hex[:8]}"
    await CreatePOCase(
        repo=SqlPOCaseRepository(db.sessions),
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
        policy_override_repo=SqlPolicyOverrideRepository(db.sessions),
        platform_default_sla_policy=_SLA,
    ).handle(owner, po_reference=taken, supplier_name="NCC cũ", order_kind=OrderKind.REORDER)
    case = await _ready(db, owner)
    placed = await _place_order(db.sessions).handle(owner, case_id=case.id)
    create = _create_po(db.sessions)

    with pytest.raises(ConflictError) as raised:
        await create.handle(
            owner, po_case_id=placed.po_case.id, po_reference=taken, order_kind=OrderKind.NEW
        )
    assert raised.value.details == {
        "constraint": "uq_po_cases_tenant_id_po_reference",
        "po_reference": taken,
    }
    repo = SqlPOCaseRepository(db.sessions)
    still = await repo.get(owner, placed.po_case.id)
    assert still is not None and (still.state, still.po_reference) == (
        CaseState.ORDER_REQUESTED,
        None,
    )

    reference = f"PO-{uuid.uuid4().hex[:8]}"
    sku = case.skus[0].id
    done = await create.handle(
        owner,
        po_case_id=placed.po_case.id,
        po_reference=reference,
        order_kind=OrderKind.NEW,
        quantities={sku: 75},
    )
    assert (done.state, done.po_reference) == (CaseState.PO_CREATED, reference)
    stored = await repo.get(owner, placed.po_case.id)
    assert stored is not None and stored.po_reference == reference
    assert {line.sku_id: line.quantity for line in stored.lines}[sku] == 75
    history = await repo.list_transitions(owner, placed.po_case.id)
    assert [(t.from_state, t.to_state) for t in history] == [
        (CaseState.ORDER_REQUESTED, CaseState.PO_CREATED)
    ]
    assert (
        await _count(
            db,
            "SELECT count(*) FROM platform.audit_events"
            " WHERE action = 'supply_chain.po_case.create_po' AND resource_id = :p",
            p=str(placed.po_case.id),
        )
        == 1
    )


async def test_create_po_needs_the_steps_duty(db: _Db) -> None:
    owner = _fresh(ORDERING)
    case = await _ready(db, owner)
    placed = await _place_order(db.sessions).handle(owner, case_id=case.id)

    with pytest.raises(PermissionDeniedError):
        await _create_po(db.sessions).handle(
            _with(owner, duty_scope(CaseDuty.FINANCE), _WRITE_PO),
            po_case_id=placed.po_case.id,
            po_reference="PO-X",
            order_kind=OrderKind.NEW,
        )


async def test_an_override_stored_before_create_po_loads_after_the_migration(db: _Db) -> None:
    """A tenant's PO step-to-duty override written before 1.1.0 lacks
    `create_po`, which the schema requires: every step of that tenant would
    fail. The migration's data step adds `create_po: ordering`, and the
    tenant's own choices stand."""
    owner = _fresh(_READ_PO, _WRITE_PO, ORDERING)
    duties = {
        a.value: d.value for a, d in _PO_DUTIES.action_duties.items() if a.value != "create_po"
    }
    duties["request_deposit"] = "exceptions"
    stored = {
        "schema_version": "1.0",
        "policy_id": "supply_chain_action_duties",
        "policy_version": "1.0.0",
        "action_duties": duties,
    }
    async with db.migrator.begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO platform.tenants (id, slug, name) VALUES (:t, :s, 'Tenant S5')"
                " ON CONFLICT DO NOTHING"
            ),
            {"t": owner.tenant_id, "s": f"s5-{owner.tenant_id.hex[:8]}"},
        )
        await conn.execute(
            sa.text(
                "INSERT INTO platform.policy_overrides (id, tenant_id, policy_id, content)"
                " VALUES (:i, :t, 'supply_chain_action_duties', CAST(:c AS jsonb))"
            ),
            {"i": uuid.uuid4(), "t": owner.tenant_id, "c": json.dumps(stored)},
        )
    po = POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(owner.tenant_id),
        workspace_id=WorkspaceId(owner.workspace_id),
        po_reference=f"PO-{uuid.uuid4().hex[:8]}",
        supplier_name="NCC",
    )
    await SqlPOCaseRepository(db.sessions).add(owner, po)
    advance = AdvancePOCase(
        repo=SqlPOCaseRepository(db.sessions),
        authz=ScopeAuthorizationService(),
        policy_override_repo=SqlPolicyOverrideRepository(db.sessions),
        platform_default_approval_matrix=_MATRIX,
        platform_default_action_duties=_PO_DUTIES,
        runner=None,  # type: ignore[arg-type]  # the matrix gates nothing: no run starts
        ids=Uuid4Generator(),
    )
    exceptions = _with(owner, duty_scope(CaseDuty.EXCEPTIONS))
    with pytest.raises(ValidationError, match="create_po"):
        await advance.handle(exceptions, po_case_id=po.id, action=CaseAction.REQUEST_DEPOSIT)

    spec = importlib.util.spec_from_file_location("migration_84d1c1946b44", _MIGRATION)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    async with db.migrator.begin() as conn:
        await conn.execute(sa.text(migration.ADD_CREATE_PO_TO_OVERRIDES))

    result = await advance.handle(exceptions, po_case_id=po.id, action=CaseAction.REQUEST_DEPOSIT)
    assert result.case.state is CaseState.WAITING_DEPOSIT  # type: ignore[union-attr]
    async with db.migrator.connect() as conn:
        content = await conn.scalar(
            sa.text(
                "SELECT content FROM platform.policy_overrides"
                " WHERE tenant_id = :t AND policy_id = 'supply_chain_action_duties'"
            ),
            {"t": owner.tenant_id},
        )
    assert content["action_duties"]["create_po"] == "ordering"
    assert content["action_duties"]["request_deposit"] == "exceptions"


# --- one product, step 1 to the PO case's `completed` -------------------------------------


async def test_one_product_from_step_one_to_its_po_case_completed(world: World) -> None:
    """Every step through its handler, each by a person holding its duty; BGĐ
    and the sign-off decided by members in their roles; ĐẶT HÀNG; step 10;
    steps 11-17."""
    stack = world.stack
    operator = await _member(world, "sc_operator")
    operator = _with(operator, *operator.scopes, PRODUCT_CASE_READ, PRODUCT_CASE_WRITE)
    rnd = _rnd()
    supply_lead = _alpha(
        frozenset(
            {
                PRODUCT_CASE_READ,
                duty_scope(CaseDuty.SUPPLY_LEAD),
                "supply_chain.document.read",
                "supply_chain.document.write",
            }
        )
    )
    bod = await _member(world, "sc_bod", "approver")
    accountant = await _member(world, "sc_finance", "approver")

    propose = ProposeProductCase(
        repo=stack.cases,
        authz=ScopeAuthorizationService(),
        policy_override_repo=stack.policies,
        platform_default_duties=_PRODUCT_DUTIES,
        platform_default_sla_policy=_SLA,
        ids=Uuid4Generator(),
        clock=SystemClock(),
    )
    case = await propose.handle(
        _with(operator, *OPERATOR_SCOPES),
        proposal_code=f"DX-{uuid.uuid4().hex[:8]}",
        product_name="Nồi inox 5 đáy 20cm",
        category="noi",
    )
    target = case.id
    step = stack.advance.handle
    await step(
        operator, case_id=target, action=ProductAction.REQUEST_SAMPLE, supplier_name="NCC An"
    )
    await step(rnd, case_id=target, action=ProductAction.RECEIVE_SAMPLE)
    paper = await _document(world, rnd, target.value, DocumentType.SAMPLE_EVALUATION)
    await step(rnd, case_id=target, action=ProductAction.PASS_SAMPLE, document_id=paper.id.value)

    review = await _pending(world, target.value)
    assert review is not None and review.required_scope == BOD_SCOPE
    await _decide(world, review, bod, approve=True, comment="BGĐ duyệt mẫu")

    paper = await _document(world, rnd, target.value, DocumentType.PRODUCT_PROFILE_BM04)
    await step(
        rnd, case_id=target, action=ProductAction.COMPLETE_PROFILE, document_id=paper.id.value
    )
    paper = await _document(
        world, supply_lead, target.value, DocumentType.SUPPLIER_CONFIRMATION_EMAIL
    )
    await step(
        supply_lead,
        case_id=target,
        action=ProductAction.CONFIRM_WITH_SUPPLIER,
        document_id=paper.id.value,
    )
    code = f"MH-{uuid.uuid4().hex[:8]}"
    await step(operator, case_id=target, action=ProductAction.ISSUE_ITEM_CODE, item_code=code)
    await step(
        operator,
        case_id=target,
        action=ProductAction.ADD_SKU,
        sku=SkuDraft(f"{code}-S", "Size S", 200),
    )
    await step(operator, case_id=target, action=ProductAction.SUBMIT_FOR_SIGNOFF)
    first = await _step(world, target.value)
    assert first is not None and first.required_scope == BOD_SCOPE
    await _decide(world, first, bod, approve=True, comment="BGĐ ký")
    second = await _step(world, target.value)
    assert second is not None and second.required_scope == ACCOUNTING_SCOPE
    await _decide(world, second, accountant, approve=True, comment="Kế toán ký")
    assert (await stack.cases.get(operator, target)).state is ProductDevState.READY_TO_ORDER  # type: ignore[union-attr]

    placed = await _place_order(stack.sessions).handle(operator, case_id=target)
    assert placed.po_case.pic_user_id == operator.principal_id

    reference = f"PO-{uuid.uuid4().hex[:8]}"
    po_ops = _with(operator, *operator.scopes, _READ_PO)
    await _create_po(stack.sessions).handle(
        po_ops, po_case_id=placed.po_case.id, po_reference=reference, order_kind=OrderKind.NEW
    )
    # Kế toán is told the PO exists (step 10's "Bàn giao cho").
    inbox = NotificationService(SqlNotificationRepository(stack.sessions))
    assert [n.link for n in (await inbox.latest(accountant)).items if reference in n.title] == [
        f"/supply-chain/po-cases/{placed.po_case.id}"
    ]

    advance = _advance_po(stack)
    by_duty = {
        CaseDuty.ORDERING: po_ops,
        CaseDuty.FINANCE: _alpha(frozenset({duty_scope(CaseDuty.FINANCE)})),
        CaseDuty.QC: _alpha(frozenset({duty_scope(CaseDuty.QC)})),
        CaseDuty.LOGISTICS: _alpha(frozenset({duty_scope(CaseDuty.LOGISTICS)})),
        CaseDuty.WAREHOUSE: _alpha(frozenset({duty_scope(CaseDuty.WAREHOUSE)})),
    }
    for action in (
        CaseAction.REQUEST_DEPOSIT,
        CaseAction.CONFIRM_DEPOSIT,
        CaseAction.START_PRE_PRODUCTION,
        CaseAction.START_PRODUCTION,
        CaseAction.SEND_TO_QC,
        CaseAction.PASS_QC,
        CaseAction.ARRIVE_AT_PORT,
        CaseAction.REQUEST_FINAL_PAYMENT,
        CaseAction.CONFIRM_PAYMENT,
        CaseAction.START_WAREHOUSE_RECEIVING,
        CaseAction.COMPLETE,
    ):
        await advance.handle(
            by_duty[_PO_DUTIES.duty_for(action)], po_case_id=placed.po_case.id, action=action
        )

    done = await SqlPOCaseRepository(stack.sessions).get(po_ops, placed.po_case.id)
    assert done is not None
    assert (done.state, done.po_reference, done.order_kind) == (
        CaseState.COMPLETED,
        reference,
        OrderKind.NEW,
    )
    assert (done.product_dev_case_id, done.pic_user_id, done.category) == (
        target.value,
        operator.principal_id,
        "noi",
    )
    assert (await stack.cases.get(operator, target)).state is ProductDevState.ORDERED  # type: ignore[union-attr]
    assert (
        await _alpha_count(
            world,
            "SELECT count(*) FROM supply_chain.po_case_state_transitions WHERE po_case_id = :p",
            p=placed.po_case.id.value,
        )
        == 12
    )


async def test_the_ordering_duty_is_told_there_is_a_po_to_create(world: World) -> None:
    """ĐẶT HÀNG tells the holders of `create_po`'s duty in the workspace, less
    the clicker; another workspace's holders are not told."""
    operator = await _member(world, "sc_operator")
    clicker = await _member(world, "sc_operator")
    elsewhere = await _member(world, "sc_operator", workspace=await _another_workspace(world))
    finance = await _member(world, "sc_finance")
    db = _Db(world.stack.sessions, world.migrator)
    case = await _ready_alpha(db, operator)

    placed = await _place_order(world.stack.sessions).handle(
        _with(clicker, *clicker.scopes), case_id=case.id
    )

    inbox = NotificationService(SqlNotificationRepository(world.stack.sessions))

    async def told(member: AccessContext) -> list[str | None]:
        return [n.link for n in (await inbox.latest(member)).items if case.product_name in n.title]

    link = f"/supply-chain/po-cases/{placed.po_case.id}"
    assert await told(operator) == [link]
    assert await told(clicker) == []
    assert await told(finance) == []
    assert await told(elsewhere) == []


async def _ready_alpha(db: _Db, operator: AccessContext) -> ProductDevelopmentCase:
    """`_ready`, in Alpha's main workspace, with a product name of its own."""
    actor = _alpha(frozenset(), principal=operator.principal_id)
    case = await _ready(db, actor)
    assert case.workspace_id.value == ALPHA_WS and case.tenant_id.value == ALPHA
    return case


async def test_offboarding_purges_an_ordered_case_with_its_po_case_and_lines(db: _Db) -> None:
    """The purge deletes in name order: `po_cases` (and, by cascade, its lines)
    before `product_dev_cases`, so neither RESTRICT foreign key stands in its
    way; another tenant's ordered case stays whole."""
    tenant = uuid.uuid4()
    leaving = _fresh(ORDERING, _READ_PO, tenant=tenant)
    staying = _fresh(ORDERING, _READ_PO)
    for owner in (leaving, staying):
        case = await _ready(db, owner)
        placed = await _place_order(db.sessions).handle(owner, case_id=case.id)
        await _create_po(db.sessions).handle(
            owner,
            po_case_id=placed.po_case.id,
            po_reference=f"PO-{uuid.uuid4().hex[:8]}",
            order_kind=OrderKind.NEW,
        )

    await SqlTenantOffboarding(db.sessions).purge_rows(tenant)

    for table in (
        "po_cases",
        "po_case_lines",
        "po_case_state_transitions",
        "product_dev_cases",
        "skus",
    ):
        assert (
            await _count(
                db, f"SELECT count(*) FROM supply_chain.{table} WHERE tenant_id = :t", t=tenant
            )
            == 0
        ), table
    assert (
        await _count(
            db,
            "SELECT count(*) FROM supply_chain.po_case_lines WHERE tenant_id = :t",
            t=staying.tenant_id,
        )
        == 2
    )


async def test_the_application_adds_lines_and_sets_their_quantity_only(db: _Db) -> None:
    """Asked of the catalog: a line is written at ĐẶT HÀNG and its quantity
    set at step 10; it leaves only with its PO case."""
    async with db.migrator.connect() as conn:
        granted = {
            verb
            for verb in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
            if await conn.scalar(
                sa.text("SELECT has_table_privilege('dw_app', 'supply_chain.po_case_lines', :v)"),
                {"v": verb},
            )
        }
        updatable = {
            column
            for column in ("quantity", "sku_id", "po_case_id", "tenant_id", "workspace_id")
            if await conn.scalar(
                sa.text(
                    "SELECT has_column_privilege('dw_app', 'supply_chain.po_case_lines', :c,"
                    " 'UPDATE')"
                ),
                {"c": column},
            )
        }
    assert granted == {"SELECT", "INSERT"}
    assert updatable == {"quantity"}
