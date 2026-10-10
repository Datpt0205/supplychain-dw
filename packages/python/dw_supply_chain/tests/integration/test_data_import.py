"""Integration: the one-time import (0f231b1bf02d; ADR 0027, ticket onboarding/01).

What only the real database can show:

- `catalogue_items` is narrowed by tenant AND workspace (RLS FORCE): a
  neighbour workspace and another tenant read none of its rows, and a row
  written for another tenant or workspace is refused by the policy's WITH
  CHECK;
- its UNIQUEs: the same item and SKU again adds nothing (NULLS NOT DISTINCT,
  so an item with no SKU too), a SKU under a second item code is a 409, and
  a code with spaces around it or blank is refused;
- the supplier name match is the database's (`normalize_supplier_name`), and
  `assign_code` touches only a supplier without a code;
- a dry run of a real workbook writes nothing (counted before and after), and
  applying the same file twice adds rows once.

Owed when written (2026-10-09): not run, no Docker on the machine that wrote it.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import AsyncIterator, Mapping

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls

from dw_kernel.errors import ConflictError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.import_workbook import XlsxWorkbookReader
from dw_supply_chain.adapters.persistence.commercial_repository import SqlSupplierRecords
from dw_supply_chain.adapters.persistence.data_import_repository import (
    SqlCatalogue,
    SqlOpenCaseImport,
    SqlSupplierImport,
)
from dw_supply_chain.application.commercial import SaveSupplierBankAccount, SaveSupplierContact
from dw_supply_chain.application.data_import import (
    SUPPLY_CHAIN_IMPORT,
    ImportSupplyChainData,
    NewCatalogueItem,
    WorkspaceRef,
)
from dw_supply_chain.application.handlers import COMMERCIAL_WRITE
from dw_supply_chain.domain.data_import import SHEETS, RowStatus
from dw_supply_chain.sla_policy import ProductCategory

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine(db_urls: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _context(
    tenant: uuid.UUID,
    workspace: uuid.UUID,
    scopes: frozenset[str] = frozenset({SUPPLY_CHAIN_IMPORT, COMMERCIAL_WRITE}),
) -> AccessContext:
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=scopes,
        plan_id="professional",
    )


def _audit(context: AccessContext) -> AuditEvent:
    return AuditEvent(
        id=uuid.uuid4(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        actor_id=UserId(context.principal_id),
        action="supply_chain.test",
        resource_type="catalogue_item",
        resource_id=str(uuid.uuid4()),
        occurred_at=SystemClock().now(),
    )


def _item(item_code: str, sku_code: str | None) -> NewCatalogueItem:
    return NewCatalogueItem(
        id=uuid.uuid4(), item_code=item_code, sku_code=sku_code, name="Nồi 24cm", category=None
    )


async def test_the_catalogue_stays_in_its_workspace(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    mine = _context(tenant, workspace)
    catalogue = SqlCatalogue(sessions)
    assert await catalogue.add(mine, _item("EL-1", "EL-1-01"), audit=_audit(mine))
    assert [i.sku_code for i in await catalogue.matching(mine, ["EL-1"], [])] == ["EL-1-01"]
    for other in (_context(tenant, uuid.uuid4()), _context(uuid.uuid4(), workspace)):
        assert await catalogue.matching(other, ["EL-1"], ["EL-1-01"]) == []
        # Written into the other scope: allowed there, never seen here.
        assert await catalogue.add(other, _item("EL-1", "EL-1-01"), audit=_audit(other))
    assert len(await catalogue.matching(mine, ["EL-1"], [])) == 1


async def test_a_row_for_another_tenant_or_workspace_is_refused_by_the_policy(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    mine = _context(uuid.uuid4(), uuid.uuid4())
    for tenant, workspace in (
        (uuid.uuid4(), mine.workspace_id),
        (mine.tenant_id, uuid.uuid4()),
    ):
        with pytest.raises(DBAPIError):
            async with tenant_session(sessions, TenantScope.from_access_context(mine)) as session:
                await session.execute(
                    sa.text(
                        "INSERT INTO supply_chain.catalogue_items"
                        " (id, tenant_id, workspace_id, item_code, name, imported_by)"
                        " VALUES (:id, :t, :w, 'EL-9', 'Nồi', :by)"
                    ),
                    {"id": uuid.uuid4(), "t": tenant, "w": workspace, "by": uuid.uuid4()},
                )


async def test_the_catalogue_uniques_and_checks(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    catalogue = SqlCatalogue(sessions)
    assert await catalogue.add(context, _item("EL-2", None), audit=_audit(context))
    assert not await catalogue.add(context, _item("EL-2", None), audit=_audit(context))
    assert await catalogue.add(context, _item("EL-2", "EL-2-01"), audit=_audit(context))
    assert not await catalogue.add(context, _item("EL-2", "EL-2-01"), audit=_audit(context))
    with pytest.raises(ConflictError):
        await catalogue.add(context, _item("EL-3", "EL-2-01"), audit=_audit(context))
    for bad in (_item(" EL-4", None), _item("", None), _item("EL-4", " ")):
        with pytest.raises(IntegrityError):
            await catalogue.add(context, bad, audit=_audit(context))


async def test_the_supplier_match_is_the_database_s_and_a_code_is_set_once(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    suppliers = SqlSupplierImport(sessions)
    supplier_id = uuid.uuid4()
    async with tenant_session(sessions, TenantScope.from_access_context(context)) as session:
        await session.execute(
            sa.text(
                "INSERT INTO supply_chain.suppliers (id, tenant_id, workspace_id, name)"
                " VALUES (:id, :t, :w, 'Công ty  Minh Phát')"
            ),
            {"id": supplier_id, "t": context.tenant_id, "w": context.workspace_id},
        )
    found = await suppliers.by_name(context, "công ty minh phát")
    assert found is not None and found.id == supplier_id and found.code is None
    assert await suppliers.assign_code(
        context, supplier_id=supplier_id, code="MP01", audit=_audit(context)
    )
    assert not await suppliers.assign_code(
        context, supplier_id=supplier_id, code="MP02", audit=_audit(context)
    )
    assert (await suppliers.by_code(context, "MP01")) is not None
    neighbour = _context(context.tenant_id, uuid.uuid4())
    assert await suppliers.by_code(neighbour, "MP01") is None
    assert not await suppliers.create(
        context, supplier_id=uuid.uuid4(), name="Khác", code="MP01", audit=_audit(context)
    )


class _NoCategories:
    async def handle(self, context: AccessContext) -> list[ProductCategory]:
        return []


class _NoMembers:
    async def workspaces(self, context: AccessContext) -> list[WorkspaceRef]:
        return []

    async def assignable_roles(self, context: AccessContext) -> frozenset[str]:
        return frozenset()

    async def member_emails(self, context: AccessContext) -> frozenset[str]:
        return frozenset()

    async def workspace_member_ids(self, context: AccessContext) -> Mapping[str, uuid.UUID]:
        return {}

    async def invite(
        self,
        context: AccessContext,
        *,
        display_name: str,
        email: str,
        memberships: Mapping[uuid.UUID, frozenset[str]],
    ) -> bool:
        raise NotImplementedError("not exercised: the workbook has no users sheet")


def _workbook() -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    suppliers = workbook.active
    suppliers.title = SHEETS[0].title
    suppliers.append([c.header for c in SHEETS[0].columns])
    suppliers.append(["MP01", "Minh Phát", "Chị Lan", "lan@minhphat.vn", None, None, None, None])
    catalogue = workbook.create_sheet(SHEETS[1].title)
    catalogue.append([c.header for c in SHEETS[1].columns])
    catalogue.append(["EL-1", "EL-1-01", "Nồi 24cm", "noi"])
    catalogue.append(["EL-1", "EL-1-02", "Nồi 26cm", "noi"])
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


async def _counts(engine: AsyncEngine, context: AccessContext) -> tuple[int, ...]:
    tables = ("suppliers", "supplier_contacts", "catalogue_items")
    async with engine.connect() as conn:
        await conn.execute(
            sa.text(
                "SELECT set_config('app.tenant_id', :t, false),"
                " set_config('app.workspace_id', :w, false)"
            ),
            {"t": str(context.tenant_id), "w": str(context.workspace_id)},
        )
        counts = []
        for table in tables:
            counted = await conn.scalar(sa.text(f"SELECT count(*) FROM supply_chain.{table}"))
            counts.append(int(counted or 0))
        return tuple(counts)


async def test_a_dry_run_writes_nothing_and_a_second_run_adds_nothing(
    engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    authz, ids, clock = ScopeAuthorizationService(), Uuid4Generator(), SystemClock()
    records = SqlSupplierRecords(sessions)
    handler = ImportSupplyChainData(
        reader=XlsxWorkbookReader(),
        suppliers=SqlSupplierImport(sessions),
        contacts=records,
        accounts=records,
        save_contact=SaveSupplierContact(records, authz, ids, clock),
        save_account=SaveSupplierBankAccount(records, authz, ids, clock),
        catalogue=SqlCatalogue(sessions),
        members=_NoMembers(),
        authz=authz,
        ids=ids,
        clock=clock,
        open_cases=SqlOpenCaseImport(sessions),
        categories=_NoCategories(),
    )
    data = _workbook()
    before = await _counts(engine, context)
    dry = await handler.handle(context, data, apply=False)
    assert {r.status for r in dry.rows} == {RowStatus.CREATED}
    assert await _counts(engine, context) == before
    first = await handler.handle(context, data, apply=True)
    assert {r.status for r in first.rows} == {RowStatus.CREATED}
    after = await _counts(engine, context)
    assert after == (before[0] + 1, before[1] + 1, before[2] + 2)
    second = await handler.handle(context, data, apply=True)
    assert {r.status for r in second.rows} == {RowStatus.EXISTS}
    assert await _counts(engine, context) == after
