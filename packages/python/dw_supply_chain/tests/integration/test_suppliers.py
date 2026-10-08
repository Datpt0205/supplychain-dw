"""Integration: the supplier master record (`a0035e9faf32`, ticket hardening/04).

What only the real database can show:

- the database decides two spellings are one supplier
  (`supply_chain.normalize_supplier_name`): case, runs of spaces, a no-break
  space, a decomposed mark — and a case takes the supplier's stored name;
- resolution never reaches another tenant's or another workspace's supplier
  of the same name, and a case cannot point at one by SQL either (the
  composite FK), nor carry a name other than its supplier's;
- a supplier is created once, audited in the caller's name, and a case that
  reuses it audits no second creation;
- the backfill turns existing spellings into one supplier per workspace,
  named as first written.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls, database_urls, recreate_database, run_alembic

from dw_kernel.ids import TenantId, WorkspaceId
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.adapters.persistence.suppliers import SUPPLIER_CREATED
from dw_supply_chain.domain.po_case import POCase, POCaseId

pytestmark = pytest.mark.integration


@pytest.fixture
async def sessions(db_urls: DatabaseUrls) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def migrator(db_urls: DatabaseUrls) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(db_urls.migrator, poolclass=NullPool)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


def _context(tenant: uuid.UUID, workspace: uuid.UUID) -> AccessContext:
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=frozenset({"supply_chain.po_case.write"}),
        plan_id="professional",
    )


def _case(context: AccessContext, supplier_name: str) -> POCase:
    return POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        po_reference=f"PO-{uuid.uuid4().hex[:8]}",
        supplier_name=supplier_name,
    )


async def _supplier_of(
    migrator: async_sessionmaker[AsyncSession], case: POCase
) -> tuple[uuid.UUID, str]:
    async with migrator() as session:
        row = (
            await session.execute(
                sa.text(
                    "SELECT supplier_id, supplier_name FROM supply_chain.po_cases WHERE id = :i"
                ),
                {"i": case.id.value},
            )
        ).one()
    return row.supplier_id, row.supplier_name


async def test_spellings_the_database_calls_equal_are_one_supplier_with_one_name(
    sessions: async_sessionmaker[AsyncSession],
    migrator: async_sessionmaker[AsyncSession],
) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    repo = SqlPOCaseRepository(sessions)
    first = _case(context, "Công ty Đông Á")
    await repo.add(context, first)
    variants = [
        "CÔNG TY ĐÔNG Á",
        "  công   ty đông á ",
        "Công\u00a0ty Đông Á",
        # "ô" and "á" as a letter plus a combining mark (NFD).
        "Co\u0302ng ty Đông A\u0301",
    ]
    cases = [_case(context, spelling) for spelling in variants]
    for case in cases:
        await repo.add(context, case)
    different = _case(context, "Cong ty Dong A")  # no marks: another supplier
    await repo.add(context, different)

    supplier, name = await _supplier_of(migrator, first)
    assert name == "Công ty Đông Á"
    for case in cases:
        assert await _supplier_of(migrator, case) == (supplier, "Công ty Đông Á")
        assert case.supplier_name == "Công ty Đông Á"
    assert (await _supplier_of(migrator, different))[0] != supplier
    assert await repo.list_supplier_names(context) == ["Cong ty Dong A", "Công ty Đông Á"]

    async with migrator() as session:
        created = (
            await session.execute(
                sa.text(
                    "SELECT resource_id, actor_id FROM platform.audit_events"
                    " WHERE tenant_id = :t AND action = :a ORDER BY occurred_at"
                ),
                {"t": context.tenant_id, "a": SUPPLIER_CREATED},
            )
        ).all()
    assert [r.resource_id for r in created] == [
        str(supplier),
        str((await _supplier_of(migrator, different))[0]),
    ]
    assert {r.actor_id for r in created} == {context.principal_id}


async def test_resolution_never_reaches_another_tenants_or_workspaces_supplier(
    sessions: async_sessionmaker[AsyncSession],
    migrator: async_sessionmaker[AsyncSession],
) -> None:
    tenant = uuid.uuid4()
    mine = _context(tenant, uuid.uuid4())
    neighbour = _context(tenant, uuid.uuid4())
    # Same workspace id under another tenant: only the tenant keeps it apart.
    stranger = _context(uuid.uuid4(), mine.workspace_id)
    repo = SqlPOCaseRepository(sessions)
    cases = {context: _case(context, "Kangaroo") for context in (mine, neighbour, stranger)}
    for context, case in cases.items():
        await repo.add(context, case)

    suppliers = {
        context: (await _supplier_of(migrator, case))[0] for context, case in cases.items()
    }
    assert len(set(suppliers.values())) == 3
    for context in (mine, neighbour, stranger):
        assert await repo.list_supplier_names(context) == ["Kangaroo"]
    async with tenant_session(sessions, TenantScope.from_access_context(mine)) as session:
        visible = (await session.execute(sa.text("SELECT id FROM supply_chain.suppliers"))).all()
    assert [row.id for row in visible] == [suppliers[mine]]


@pytest.mark.parametrize("whose", ["neighbour", "stranger"])
async def test_a_case_cannot_point_at_another_workspaces_supplier_by_sql(
    sessions: async_sessionmaker[AsyncSession],
    migrator: async_sessionmaker[AsyncSession],
    whose: str,
) -> None:
    tenant = uuid.uuid4()
    mine = _context(tenant, uuid.uuid4())
    other = (
        _context(tenant, uuid.uuid4())
        if whose == "neighbour"
        else _context(uuid.uuid4(), mine.workspace_id)
    )
    repo = SqlPOCaseRepository(sessions)
    theirs = _case(other, "Kangaroo")
    await repo.add(other, theirs)
    their_supplier, _ = await _supplier_of(migrator, theirs)

    with pytest.raises(IntegrityError, match="fk_po_cases_tenant_id_suppliers"):
        async with tenant_session(sessions, TenantScope.from_access_context(mine)) as session:
            await session.execute(
                sa.text(
                    "INSERT INTO supply_chain.po_cases (id, tenant_id, workspace_id,"
                    " po_reference, supplier_id, supplier_name, state, order_kind)"
                    " VALUES (:i, :t, :w, 'PO-X', :s, 'Kangaroo', 'po_created', 'reorder')"
                ),
                {
                    "i": uuid.uuid4(),
                    "t": mine.tenant_id,
                    "w": mine.workspace_id,
                    "s": their_supplier,
                },
            )


async def test_a_case_cannot_carry_a_name_other_than_its_suppliers(
    sessions: async_sessionmaker[AsyncSession],
    migrator: async_sessionmaker[AsyncSession],
) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    repo = SqlPOCaseRepository(sessions)
    case = _case(context, "Kangaroo")
    await repo.add(context, case)

    with pytest.raises(IntegrityError, match="fk_po_cases_tenant_id_suppliers"):
        async with tenant_session(sessions, TenantScope.from_access_context(context)) as session:
            await session.execute(
                sa.text("UPDATE supply_chain.po_cases SET supplier_name = 'Kangaru' WHERE id = :i"),
                {"i": case.id.value},
            )
    assert (await _supplier_of(migrator, case))[1] == "Kangaroo"


_BACKFILL_DB = "dw_test_suppliers_backfill"
_BEFORE = "d9e136c14d83"
_SUPPLIERS = "a0035e9faf32"


async def test_the_backfill_makes_one_supplier_per_spelling_group_named_as_first_written() -> None:
    urls = database_urls(_BACKFILL_DB)
    await recreate_database(urls.admin, _BACKFILL_DB)
    result = run_alembic(["upgrade", _BEFORE], urls.migrator)
    assert result.returncode == 0, result.stderr
    tenant, w1, w2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    engine = create_async_engine(urls.migrator, poolclass=NullPool)
    rows = [
        # (workspace, name, created) — the oldest spelling names the supplier.
        (w1, "dong a inox", datetime(2026, 1, 2, tzinfo=UTC)),
        (w1, "Dong A Inox", datetime(2026, 1, 1, tzinfo=UTC)),
        (w1, " DONG  A INOX", datetime(2026, 1, 3, tzinfo=UTC)),
        (w1, "Kangaroo", datetime(2026, 1, 4, tzinfo=UTC)),
        (w2, "DONG A INOX", datetime(2026, 1, 5, tzinfo=UTC)),
    ]
    try:
        async with engine.begin() as conn:
            for workspace, name, created in rows:
                await conn.execute(
                    sa.text(
                        "INSERT INTO supply_chain.po_cases (id, tenant_id, workspace_id,"
                        " po_reference, supplier_name, state, order_kind, created_at)"
                        " VALUES (gen_random_uuid(), :t, :w, gen_random_uuid()::text, :n,"
                        " 'po_created', 'reorder', :c)"
                    ),
                    {"t": tenant, "w": workspace, "n": name, "c": created},
                )
        result = run_alembic(["upgrade", _SUPPLIERS], urls.migrator)
        assert result.returncode == 0, result.stderr
        async with engine.connect() as conn:
            suppliers = (
                await conn.execute(
                    sa.text(
                        "SELECT workspace_id, name FROM supply_chain.suppliers"
                        " WHERE tenant_id = :t ORDER BY workspace_id = :w1 DESC, name"
                    ),
                    {"t": tenant, "w1": w1},
                )
            ).all()
            cases = (
                await conn.execute(
                    sa.text(
                        "SELECT c.workspace_id, c.supplier_name, s.name AS stored"
                        " FROM supply_chain.po_cases c JOIN supply_chain.suppliers s"
                        " ON s.id = c.supplier_id WHERE c.tenant_id = :t"
                    ),
                    {"t": tenant},
                )
            ).all()
    finally:
        await engine.dispose()

    assert [(r.workspace_id, r.name) for r in suppliers] == [
        (w1, "Dong A Inox"),
        (w1, "Kangaroo"),
        (w2, "DONG A INOX"),
    ]
    assert len(cases) == len(rows)
    assert all(case.supplier_name == case.stored for case in cases)
    assert sorted(case.supplier_name for case in cases if case.workspace_id == w1) == [
        "Dong A Inox",
        "Dong A Inox",
        "Dong A Inox",
        "Kangaroo",
    ]
