"""Integration: step 9's codes (ticket ai-automation/13; ADR 0018, ADR 0027).

What only the real database can show:

- `SqlCodeRegistry` sees the application's item codes and SKUs and the
  imported catalogue of the caller's workspace only (RLS), and a case's own
  codes are not "taken" by it;
- a case saved with several steps at once (its code, its SKUs, its
  submission: an approved step-9 proposal) passes the optimistic version
  check (`version - len(steps)`), and a PIC reassignment (no step) still does;
- a code another case of the tenant holds is refused by the UNIQUE when the
  approval writes it, naming the code (409), even from another workspace.

Owed when written (2026-10-09): not run, no Docker on the machine that wrote it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls

from dw_kernel.errors import ConflictError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import SystemClock
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence.code_registry import SqlCodeRegistry
from dw_supply_chain.adapters.persistence.data_import_repository import SqlCatalogue
from dw_supply_chain.adapters.persistence.product_case_repository import SqlProductCaseRepository
from dw_supply_chain.application.data_import import NewCatalogueItem
from dw_supply_chain.domain.item_coding import Holder
from dw_supply_chain.domain.product_development_case import (
    ProductAction,
    ProductActionInput,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
    SkuDraft,
    apply_product_action,
)

pytestmark = pytest.mark.integration


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


def _audit(context: AccessContext) -> AuditEvent:
    return AuditEvent(
        id=uuid.uuid4(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        actor_id=UserId(context.principal_id),
        action="supply_chain.test",
        resource_type="product_dev_case",
        resource_id=str(uuid.uuid4()),
        occurred_at=SystemClock().now(),
    )


async def _case_in_item_coding(
    sessions: async_sessionmaker[AsyncSession], context: AccessContext
) -> ProductDevelopmentCase:
    """A case taken to step 9 by writing its state directly (the steps
    before are not what is under test)."""
    repo = SqlProductCaseRepository(sessions)
    case = ProductDevelopmentCase.propose(
        id=ProductDevelopmentCaseId(uuid.uuid4()),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        proposal_code=f"DX-{uuid.uuid4().hex[:8]}",
        product_name="Nồi inox 3 đáy 24cm",
        category="noi",
        actor_id=context.principal_id,
    )
    await repo.add(context, case, audit=_audit(context))
    loaded = await repo.get(context, case.id)
    assert loaded is not None
    loaded.state = ProductDevState.ITEM_CODING
    loaded.version += 1
    await repo.save(context, loaded, audit=_audit(context))
    again = await repo.get(context, case.id)
    assert again is not None
    return again


def _code(case: ProductDevelopmentCase, context: AccessContext, item: str, skus: list[str]) -> None:
    apply_product_action(
        case,
        action=ProductAction.ISSUE_ITEM_CODE,
        given=ProductActionInput(
            actor_id=context.principal_id, item_code=item, new_id=uuid.uuid4()
        ),
    )
    for sku in skus:
        apply_product_action(
            case,
            action=ProductAction.ADD_SKU,
            given=ProductActionInput(
                actor_id=context.principal_id,
                sku=SkuDraft(sku_code=sku, variant_label=sku),
                new_id=uuid.uuid4(),
            ),
        )


async def test_several_steps_save_together_and_the_registry_sees_its_workspace_only(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    mine = _context(tenant, workspace)
    repo = SqlProductCaseRepository(sessions)
    case = await _case_in_item_coding(sessions, mine)
    _code(case, mine, f"EL-{tenant.hex[:5]}", [f"EL-{tenant.hex[:5]}-01"])
    apply_product_action(
        case,
        action=ProductAction.SUBMIT_FOR_SIGNOFF,
        given=ProductActionInput(actor_id=mine.principal_id),
    )
    # Three steps, one save: the optimistic check counts them.
    await repo.save(mine, case, audit=_audit(mine))
    stored = await repo.get(mine, case.id)
    assert stored is not None and stored.state is ProductDevState.PENDING_SIGNOFF
    item = f"EL-{tenant.hex[:5]}"
    await SqlCatalogue(sessions).add(
        mine,
        NewCatalogueItem(
            id=uuid.uuid4(), item_code="EL-99999", sku_code=None, name="Cũ", category=None
        ),
        audit=_audit(mine),
    )
    registry = SqlCodeRegistry(sessions)
    assert set(await registry.item_codes_with_prefix(mine, "EL-")) >= {item, "EL-99999"}
    taken = await registry.taken(mine, [item, "EL-99999"], [f"{item}-01"], except_case=uuid.uuid4())
    assert taken.item_codes == {item: (Holder.APP,), "EL-99999": (Holder.CATALOGUE,)}
    assert taken.sku_codes == {f"{item}-01": (Holder.APP,)}
    own = await registry.taken(mine, [item], [f"{item}-01"], except_case=case.id.value)
    assert own.item_codes == {} and own.sku_codes == {}
    for other in (_context(tenant, uuid.uuid4()), _context(uuid.uuid4(), workspace)):
        assert await registry.item_codes_with_prefix(other, "EL-") == []
        nothing = await registry.taken(other, [item, "EL-99999"], [], except_case=uuid.uuid4())
        assert nothing.item_codes == {}


async def test_a_code_another_workspace_holds_is_refused_naming_it(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    tenant = uuid.uuid4()
    first, second = _context(tenant, uuid.uuid4()), _context(tenant, uuid.uuid4())
    repo = SqlProductCaseRepository(sessions)
    code = f"EL-{tenant.hex[:5]}"
    held = await _case_in_item_coding(sessions, first)
    _code(held, first, code, [])
    await repo.save(first, held, audit=_audit(first))
    case = await _case_in_item_coding(sessions, second)
    _code(case, second, code, [f"{code}-01"])
    with pytest.raises(ConflictError, match=code):
        await repo.save(second, case, audit=_audit(second))
