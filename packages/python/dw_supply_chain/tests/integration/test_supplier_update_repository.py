"""Integration: `SupplierUpdate` persists under RLS, cascades with its case.

Mirrors `test_po_case_repository.py`'s rigor: a raw query with no tenant
filter proves RLS itself hides the row, not just the repository's own good
behaviour, and the FK's `ON DELETE CASCADE` is exercised against a real
delete rather than assumed from the migration text.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls

from dw_kernel.ids import TenantId, WorkspaceId
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.adapters.persistence.supplier_update_repository import (
    SqlSupplierUpdateRepository,
)
from dw_supply_chain.domain.po_case import POCase, POCaseId
from dw_supply_chain.domain.supplier_update import (
    SupplierEventType,
    SupplierUpdate,
    SupplierUpdateExtraction,
    SupplierUpdateId,
)

pytestmark = pytest.mark.integration

TENANT_A = uuid.UUID(int=0xA00)
WORKSPACE_A = uuid.UUID(int=0xA01)
TENANT_B = uuid.UUID(int=0xB00)
WORKSPACE_B = uuid.UUID(int=0xB01)


@pytest.fixture
async def sessions(db_urls: DatabaseUrls) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


def _context(*, tenant: uuid.UUID = TENANT_A, workspace: uuid.UUID = WORKSPACE_A) -> AccessContext:
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=frozenset({"supply_chain.po_case.write", "supply_chain.supplier_update.write"}),
        plan_id="professional",
    )


def _case(*, tenant: uuid.UUID = TENANT_A, workspace: uuid.UUID = WORKSPACE_A) -> POCase:
    return POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(tenant),
        workspace_id=WorkspaceId(workspace),
        po_reference=f"PO-{uuid.uuid4().hex[:8]}",
        supplier_name="Elmich Co.",
    )


def _extraction(**overrides: object) -> SupplierUpdateExtraction:
    defaults: dict[str, object] = {
        "event_type": SupplierEventType.PRODUCTION_DELAY,
        "reason": "component shortage",
        "proposed_action": "split shipment",
        "confidence": 0.95,
        "source_ref": "delayed by 7 days",
    }
    defaults.update(overrides)
    return SupplierUpdateExtraction(**defaults)


def _update(
    case: POCase,
    *,
    tenant: uuid.UUID = TENANT_A,
    workspace: uuid.UUID = WORKSPACE_A,
    **overrides: object,
) -> SupplierUpdate:
    defaults: dict[str, object] = {
        "id": SupplierUpdateId(uuid.uuid4()),
        "tenant_id": TenantId(tenant),
        "workspace_id": WorkspaceId(workspace),
        "po_case_id": case.id,
        "raw_text": "we will be delayed by 7 days",
        "extraction": _extraction(),
        "requires_confirmation": False,
    }
    defaults.update(overrides)
    return SupplierUpdate(**defaults)  # type: ignore[arg-type]


async def test_add_and_list_round_trips(sessions: async_sessionmaker[AsyncSession]) -> None:
    po_case_repo = SqlPOCaseRepository(sessions)
    repo = SqlSupplierUpdateRepository(sessions)
    context = _context()
    case = _case()
    await po_case_repo.add(context, case)

    update = _update(case)
    await repo.add(context, update)

    listed = await repo.list_for_case(context, case.id)
    assert len(listed) == 1
    fetched = listed[0]
    assert fetched.id == update.id
    assert fetched.po_case_id == case.id
    assert fetched.raw_text == update.raw_text
    assert fetched.extraction.event_type is SupplierEventType.PRODUCTION_DELAY
    assert fetched.extraction.confidence == pytest.approx(0.95)
    assert fetched.extraction.source_ref == "delayed by 7 days"
    assert fetched.requires_confirmation is False
    assert fetched.created_at is not None


async def test_list_returns_newest_first(sessions: async_sessionmaker[AsyncSession]) -> None:
    po_case_repo = SqlPOCaseRepository(sessions)
    repo = SqlSupplierUpdateRepository(sessions)
    context = _context()
    case = _case()
    await po_case_repo.add(context, case)

    first = _update(case, raw_text="first update")
    await repo.add(context, first)
    second = _update(case, raw_text="second update")
    await repo.add(context, second)

    listed = await repo.list_for_case(context, case.id)
    assert [update.id for update in listed] == [second.id, first.id]


async def test_a_supplier_update_needs_a_real_case(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The FK is not decoration — an update pointing at a case that was
    never created (or never persisted) is refused at the database, not
    silently accepted as an orphan row."""
    repo = SqlSupplierUpdateRepository(sessions)
    orphan_case = _case()  # never added
    with pytest.raises(IntegrityError):
        await repo.add(_context(), _update(orphan_case))


async def test_deleting_the_case_cascades_to_its_updates(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    po_case_repo = SqlPOCaseRepository(sessions)
    repo = SqlSupplierUpdateRepository(sessions)
    context = _context()
    case = _case()
    await po_case_repo.add(context, case)
    await repo.add(context, _update(case))

    async with sessions() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(TENANT_A)}
        )
        await session.execute(
            text("DELETE FROM supply_chain.po_cases WHERE id = :id"), {"id": str(case.id)}
        )

    listed = await repo.list_for_case(context, case.id)
    assert listed == []


async def test_another_tenant_cannot_see_the_update(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    po_case_repo = SqlPOCaseRepository(sessions)
    repo = SqlSupplierUpdateRepository(sessions)
    context_a = _context(tenant=TENANT_A, workspace=WORKSPACE_A)
    case = _case(tenant=TENANT_A, workspace=WORKSPACE_A)
    await po_case_repo.add(context_a, case)
    await repo.add(context_a, _update(case, tenant=TENANT_A, workspace=WORKSPACE_A))

    listed_by_b = await repo.list_for_case(
        _context(tenant=TENANT_B, workspace=WORKSPACE_B), case.id
    )
    assert listed_by_b == []


async def test_rls_hides_the_row_even_from_a_query_with_no_tenant_filter(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    po_case_repo = SqlPOCaseRepository(sessions)
    repo = SqlSupplierUpdateRepository(sessions)
    context = _context(tenant=TENANT_A, workspace=WORKSPACE_A)
    case = _case(tenant=TENANT_A, workspace=WORKSPACE_A)
    await po_case_repo.add(context, case)
    update = _update(case, tenant=TENANT_A, workspace=WORKSPACE_A)
    await repo.add(context, update)

    async with sessions() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(TENANT_B)}
        )
        rows = (
            await session.execute(
                text("SELECT id FROM supply_chain.supplier_updates WHERE id = :id"),
                {"id": str(update.id)},
            )
        ).all()

    assert rows == []


# -- bulk_latest: the Attention Queue's and the daily brief's bulk lookup -----


async def test_bulk_latest_picks_the_newest_update_per_case(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    po_case_repo = SqlPOCaseRepository(sessions)
    repo = SqlSupplierUpdateRepository(sessions)
    context = _context()
    with_updates = _case()
    without_updates = _case()
    await po_case_repo.add(context, with_updates)
    await po_case_repo.add(context, without_updates)

    older = _update(with_updates, extraction=_extraction(delay_days=7))
    await repo.add(context, older)
    # The newer one says something different, so a query that returned the
    # older row (or mixed columns from both) is caught, not just its time.
    newer = _update(
        with_updates,
        raw_text="all back on schedule",
        extraction=_extraction(event_type=SupplierEventType.SHIPMENT_UPDATE, delay_days=None),
        requires_confirmation=True,
    )
    await repo.add(context, newer)

    result = await repo.bulk_latest(context, [with_updates.id, without_updates.id])

    assert without_updates.id.value not in result
    latest = result[with_updates.id.value]
    assert latest.id == newer.id
    assert latest.raw_text == "all back on schedule"
    assert latest.extraction.delay_days is None
    assert latest.requires_confirmation is True
    assert latest.created_at == (await repo.list_for_case(context, with_updates.id))[0].created_at


async def test_bulk_latest_with_no_case_ids_makes_no_query(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlSupplierUpdateRepository(sessions)
    assert await repo.bulk_latest(_context(), []) == {}


async def test_bulk_latest_only_sees_the_callers_tenant(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    po_case_repo = SqlPOCaseRepository(sessions)
    repo = SqlSupplierUpdateRepository(sessions)
    case = _case(tenant=TENANT_A, workspace=WORKSPACE_A)
    await po_case_repo.add(_context(tenant=TENANT_A, workspace=WORKSPACE_A), case)
    await repo.add(
        _context(tenant=TENANT_A, workspace=WORKSPACE_A),
        _update(case, tenant=TENANT_A, workspace=WORKSPACE_A),
    )

    result = await repo.bulk_latest(_context(tenant=TENANT_B, workspace=WORKSPACE_B), [case.id])
    assert result == {}
