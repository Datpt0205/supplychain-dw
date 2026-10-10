"""Integration: open cases imported at their current state (e623edd08e76;
ticket onboarding/02).

What only the real database can show:

- an imported product case and PO case carry ONE history row (`import`, no
  `from_state`) dated as declared, the case's `created_at` as declared, and a
  case being tested its round opened as declared;
- a second start row, a start row after another row, and a row with no
  `from_state` that is not the import are refused by the database
  (`uq_..._one_start`, `refuse_late_start`, the CHECKs);
- the lookups by key read only the caller's workspace (RLS).

Owed when written (2026-10-10): not run, no Docker on the machine that wrote it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls

from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.adapters.persistence import tables
from dw_supply_chain.adapters.persistence.data_import_repository import SqlOpenCaseImport
from dw_supply_chain.application.po_case_audit import po_case_audit
from dw_supply_chain.application.product_case_audit import product_case_audit
from dw_supply_chain.domain.case_import import imported_po_case
from dw_supply_chain.domain.po_case import CaseState, OrderKind, POCaseId
from dw_supply_chain.domain.product_development_case import (
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
)

pytestmark = pytest.mark.integration

ENTERED = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
CREATED = datetime(2026, 8, 15, 0, 0, tzinfo=UTC)


@pytest.fixture
async def sessions(db_urls: DatabaseUrls) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


def _context(tenant: uuid.UUID, workspace: uuid.UUID) -> AccessContext:
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=frozenset(),
        plan_id="professional",
    )


def _product(context: AccessContext) -> ProductDevelopmentCase:
    return ProductDevelopmentCase.imported(
        id=ProductDevelopmentCaseId(uuid.uuid4()),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        proposal_code=f"DX-{uuid.uuid4().hex[:8]}",
        product_name="Nồi inox 24cm",
        category="noi",
        supplier_name="Minh Phát",
        state=ProductDevState.SAMPLE_TESTING,
        sample_round=2,
        pic_user_id=context.principal_id,
        actor_id=context.principal_id,
        entered_at=ENTERED,
        created_at=CREATED,
    )


async def test_an_imported_product_case_has_one_backdated_import_row_and_its_round(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = _product(context)
    port = SqlOpenCaseImport(sessions)
    await port.add_product_case(
        context,
        case,
        audit=product_case_audit(
            context, Uuid4Generator(), SystemClock(), case, "imported", {"via": "import"}
        ),
    )
    t, r, c = (
        tables.product_dev_case_state_transitions,
        tables.product_sample_rounds,
        tables.product_dev_cases,
    )
    async with tenant_session(sessions, TenantScope.from_access_context(context)) as session:
        rows = (
            await session.execute(sa.select(t).where(t.c.product_dev_case_id == case.id.value))
        ).all()
        rounds = (
            await session.execute(sa.select(r).where(r.c.product_dev_case_id == case.id.value))
        ).all()
        created = await session.scalar(sa.select(c.c.created_at).where(c.c.id == case.id.value))
    assert [(x.action, x.from_state, x.to_state, x.occurred_at) for x in rows] == [
        ("import", None, "sample_testing", ENTERED)
    ]
    assert [(x.round_no, x.opened_at) for x in rounds] == [(2, ENTERED)]
    assert created == CREATED
    found = await port.product_cases(context, [case.proposal_code])
    assert list(found) == [case.proposal_code]
    elsewhere = _context(context.tenant_id, uuid.uuid4())
    assert await port.product_cases(elsewhere, [case.proposal_code]) == {}


async def test_an_imported_po_case_starts_with_its_import_row_and_only_once(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = imported_po_case(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        po_reference=f"PO-{uuid.uuid4().hex[:8]}",
        supplier_name="Minh Phát",
        state=CaseState.PRODUCTION,
        order_kind=OrderKind.REORDER,
        product_dev_case_id=None,
        pic_user_id=None,
        category=None,
        created_at=CREATED,
    )
    port = SqlOpenCaseImport(sessions)
    await port.add_po_case(
        context,
        case,
        entered_at=ENTERED,
        audit=po_case_audit(context, Uuid4Generator(), SystemClock(), case.id, "imported", {}),
    )
    t = tables.po_case_state_transitions
    owner = {
        "tenant_id": context.tenant_id,
        "workspace_id": context.workspace_id,
        "po_case_id": case.id.value,
    }
    async with tenant_session(sessions, TenantScope.from_access_context(context)) as session:
        rows = (await session.execute(sa.select(t).where(t.c.po_case_id == case.id.value))).all()
    assert [(x.action, x.from_state, x.to_state, x.occurred_at) for x in rows] == [
        ("import", None, "production", ENTERED)
    ]
    assert await port.po_references(context, [case.po_reference or ""]) == frozenset(
        {case.po_reference}
    )
    for bad in (
        # A second start row.
        {"from_state": None, "to_state": "qc", "action": "import"},
        # A row with no state before it that is not the import.
        {"from_state": None, "to_state": "qc", "action": None},
        # An action other than the import.
        {"from_state": "production", "to_state": "qc", "action": "propose"},
    ):
        with pytest.raises((IntegrityError, DBAPIError)):
            async with tenant_session(
                sessions, TenantScope.from_access_context(context)
            ) as session:
                await session.execute(sa.insert(t).values(id=uuid.uuid4(), **owner, **bad))


async def test_a_start_row_after_any_other_row_is_refused(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """`refuse_late_start`: a case moved once cannot be given an import row
    afterwards (the one-start index alone would allow it on a case with no
    start row, as every case `CreatePOCase` opened is)."""
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = imported_po_case(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        po_reference=f"PO-{uuid.uuid4().hex[:8]}",
        supplier_name="Minh Phát",
        state=CaseState.PO_CREATED,
        order_kind=OrderKind.REORDER,
        product_dev_case_id=None,
        pic_user_id=None,
        category=None,
        created_at=CREATED,
    )
    from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository

    await SqlPOCaseRepository(sessions).add(context, case)
    t = tables.po_case_state_transitions
    owner = {
        "tenant_id": context.tenant_id,
        "workspace_id": context.workspace_id,
        "po_case_id": case.id.value,
    }
    async with tenant_session(sessions, TenantScope.from_access_context(context)) as session:
        await session.execute(
            sa.insert(t).values(
                id=uuid.uuid4(), **owner, from_state="po_created", to_state="waiting_deposit"
            )
        )
    with pytest.raises((IntegrityError, DBAPIError)):
        async with tenant_session(sessions, TenantScope.from_access_context(context)) as session:
            await session.execute(
                sa.insert(t).values(
                    id=uuid.uuid4(),
                    **owner,
                    from_state=None,
                    to_state="waiting_deposit",
                    action="import",
                )
            )
