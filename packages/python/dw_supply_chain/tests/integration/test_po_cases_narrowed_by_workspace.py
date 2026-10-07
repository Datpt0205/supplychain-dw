"""Integration: a PO case and its children are read only in its own workspace.

Port ticket 04 (migration `62cdcf3bf2d2`): W2 of the same tenant as W1 does not
see, list, change or extend W1's PO case, its transitions, supplier updates or
delay analyses. Three layers, each tested on its own:

- the handlers the web routes call read through RLS and answer "not found",
  with no model call and no row;
- RLS on the four tables, asked straight with SQL under W2's settings;
- the database refuses a child row in another workspace than its case's, by
  the composite FK, whatever path wrote it.

And the one widening that must still work: offboarding's
`app.workspace_scope = 'tenant'` reads every workspace of its tenant.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.ports import ModelRequest
from dw_kernel.errors import ConflictError, NotFoundError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_supply_chain.adapters.persistence.delay_impact_repository import (
    SqlDelayImpactAnalysisRepository,
)
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.adapters.persistence.supplier_update_repository import (
    SqlSupplierUpdateRepository,
)
from dw_supply_chain.application.handlers import (
    AnalyzeDelayImpact,
    GetPOCase,
    ListCaseTransitions,
    ListDelayImpactAnalyses,
    ListPOCases,
    ListSupplierUpdates,
    SubmitSupplierUpdate,
)
from dw_supply_chain.application.ports import POCaseListFilter
from dw_supply_chain.domain.delay_impact import (
    DelayImpactAnalysis,
    DelayImpactAnalysisId,
    DelayImpactExtraction,
    ImpactedMilestoneEstimate,
    MitigationOption,
)
from dw_supply_chain.domain.po_case import CaseState, POCase, POCaseId
from dw_supply_chain.domain.supplier_update import (
    SupplierEventType,
    SupplierUpdate,
    SupplierUpdateExtraction,
    SupplierUpdateId,
)

pytestmark = pytest.mark.integration

TENANT = uuid.UUID(int=0xD4A00)
W1, W2 = uuid.UUID(int=0xD4A01), uuid.UUID(int=0xD4A02)
_TABLES = ("po_cases", "po_case_state_transitions", "supplier_updates", "delay_impact_analyses")


class _NeverCalledGateway:
    """A model call on a case the caller may not see is already a leak."""

    def __init__(self) -> None:
        self.calls: list[ModelRequest] = []

    async def generate_structured(
        self, request: ModelRequest, output_type: type[Any], *, run_context: RunContext
    ) -> Any:
        self.calls.append(request)
        raise AssertionError("the model was called for another workspace's case")


@pytest.fixture
async def sessions(db_urls: DatabaseUrls) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


def _context(workspace: uuid.UUID) -> AccessContext:
    return AccessContext(
        tenant_id=TENANT,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=frozenset(
            {
                "supply_chain.po_case.read",
                "supply_chain.po_case.write",
                "supply_chain.supplier_update.read",
                "supply_chain.supplier_update.write",
                "supply_chain.delay_impact.read",
                "supply_chain.delay_impact.write",
            }
        ),
        plan_id="professional",
    )


def _update(case: POCase, workspace: uuid.UUID) -> SupplierUpdate:
    return SupplierUpdate(
        id=SupplierUpdateId(uuid.uuid4()),
        tenant_id=TenantId(TENANT),
        workspace_id=WorkspaceId(workspace),
        po_case_id=case.id,
        raw_text="we will be delayed by 7 days",
        extraction=SupplierUpdateExtraction(
            event_type=SupplierEventType.PRODUCTION_DELAY,
            delay_days=7,
            reason="component shortage",
            proposed_action="split shipment",
            confidence=0.95,
            source_ref="delayed by 7 days",
        ),
        requires_confirmation=False,
    )


def _analysis(case: POCase, update: SupplierUpdate, workspace: uuid.UUID) -> DelayImpactAnalysis:
    return DelayImpactAnalysis(
        id=DelayImpactAnalysisId(uuid.uuid4()),
        tenant_id=TenantId(TENANT),
        workspace_id=WorkspaceId(workspace),
        po_case_id=case.id,
        supplier_update_id=update.id,
        delay_days=7,
        impacted_milestones=(
            ImpactedMilestoneEstimate(milestone=CaseState.QC, estimated_delay_days=7),
        ),
        extraction=DelayImpactExtraction(
            assumptions=["x"],
            mitigation_options=[MitigationOption(description="split", tradeoff="cost")],
        ),
    )


async def _w1_case(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[POCase, SupplierUpdate]:
    """A W1 case with a row in every child table: a transition (from the
    advance below), a supplier update and a delay analysis."""
    context = _context(W1)
    repo = SqlPOCaseRepository(sessions)
    case = POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(TENANT),
        workspace_id=WorkspaceId(W1),
        po_reference=f"PO-{uuid.uuid4().hex[:8]}",
        supplier_name=f"W1 Secret {uuid.uuid4().hex[:6]}",
        state=CaseState.PRODUCTION,
    )
    await repo.add(context, case)
    case.send_to_qc()
    await repo.save(context, case)
    update = _update(case, W1)
    await SqlSupplierUpdateRepository(sessions).add(context, update)
    await SqlDelayImpactAnalysisRepository(sessions).add(context, _analysis(case, update, W1))
    return case, update


async def _rows(
    sessions: async_sessionmaker[AsyncSession],
    case: POCase,
    *,
    workspace: uuid.UUID | None,
    scope: str = "",
) -> dict[str, int]:
    """What `dw_app` reads of the case in each table, under the given settings."""
    found: dict[str, int] = {}
    async with sessions() as session, session.begin():
        for key, value in (
            ("app.tenant_id", str(TENANT)),
            ("app.workspace_id", str(workspace) if workspace else ""),
            ("app.workspace_scope", scope),
        ):
            await session.execute(
                sa.text("SELECT set_config(:k, :v, true)"), {"k": key, "v": value}
            )
        for table in _TABLES:
            column = "id" if table == "po_cases" else "po_case_id"
            found[table] = int(
                await session.scalar(
                    sa.text(f"SELECT count(*) FROM supply_chain.{table} WHERE {column} = :id"),
                    {"id": str(case.id)},
                )
                or 0
            )
    return found


async def test_the_case_and_every_child_are_visible_in_their_own_workspace(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # The positive control: without it, every "0" below could be a broken fixture.
    case, _ = await _w1_case(sessions)

    assert await _rows(sessions, case, workspace=W1) == dict.fromkeys(_TABLES, 1)


async def test_rls_hides_the_case_and_every_child_from_another_workspace(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    case, _ = await _w1_case(sessions)

    assert await _rows(sessions, case, workspace=W2) == dict.fromkeys(_TABLES, 0)


async def test_offboarding_scope_reads_every_workspace_of_the_tenant(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    case, _ = await _w1_case(sessions)

    assert await _rows(sessions, case, workspace=None, scope="tenant") == dict.fromkeys(_TABLES, 1)
    # The scope widens across workspaces, never across tenants.
    async with sessions() as session, session.begin():
        await session.execute(
            sa.text(
                "SELECT set_config('app.tenant_id', :t, true),"
                " set_config('app.workspace_scope', 'tenant', true)"
            ),
            {"t": str(uuid.UUID(int=0xD4B00))},
        )
        seen = await session.scalar(
            sa.text("SELECT count(*) FROM supply_chain.po_cases WHERE id = :id"),
            {"id": str(case.id)},
        )
    assert seen == 0


async def test_another_workspace_reads_nothing_through_the_handlers(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    case, _ = await _w1_case(sessions)
    repo = SqlPOCaseRepository(sessions)
    authz = ScopeAuthorizationService()
    w2 = _context(W2)
    assert case.po_reference is not None

    with pytest.raises(NotFoundError):
        await GetPOCase(repo=repo, authz=authz).handle(w2, case.id)
    with pytest.raises(NotFoundError):
        await ListCaseTransitions(repo=repo, authz=authz).handle(w2, case.id)
    with pytest.raises(NotFoundError):
        await ListSupplierUpdates(
            po_case_repo=repo,
            supplier_update_repo=SqlSupplierUpdateRepository(sessions),
            authz=authz,
        ).handle(w2, case.id)
    with pytest.raises(NotFoundError):
        await ListDelayImpactAnalyses(
            po_case_repo=repo,
            delay_impact_repo=SqlDelayImpactAnalysisRepository(sessions),
            authz=authz,
        ).handle(w2, case.id)
    page = await ListPOCases(repo=repo, authz=authz).handle(
        w2, POCaseListFilter(supplier_name=case.supplier_name), limit=50, cursor=None
    )
    assert page.items == ()
    assert await repo.find_by_reference(w2, case.po_reference) == []
    assert case.supplier_name not in await repo.list_supplier_names(w2)
    assert case.id not in {c.id for c in await repo.list_active(w2)}
    assert await repo.get_many(w2, [case.id]) == []
    # And the same reads from W1 do find it, so the above is the workspace.
    assert await repo.find_by_reference(_context(W1), case.po_reference) != []
    first = await ListPOCases(repo=repo, authz=authz).handle(
        _context(W1), POCaseListFilter(supplier_name=case.supplier_name), limit=50, cursor=None
    )
    assert [c.id for c in first.items] == [case.id]


async def test_another_workspace_cannot_act_on_the_case(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    case, update = await _w1_case(sessions)
    repo = SqlPOCaseRepository(sessions)
    gateway = _NeverCalledGateway()
    w2 = _context(W2)

    with pytest.raises(NotFoundError):
        await SubmitSupplierUpdate(
            po_case_repo=repo,
            supplier_update_repo=SqlSupplierUpdateRepository(sessions),
            gateway=gateway,
            authz=ScopeAuthorizationService(),
            ids=Uuid4Generator(),
            clock=SystemClock(),
        ).handle(w2, po_case_id=case.id, raw_text="delayed by 9 days")
    with pytest.raises(NotFoundError):
        await AnalyzeDelayImpact(
            po_case_repo=repo,
            supplier_update_repo=SqlSupplierUpdateRepository(sessions),
            delay_impact_repo=SqlDelayImpactAnalysisRepository(sessions),
            gateway=gateway,
            authz=ScopeAuthorizationService(),
            ids=Uuid4Generator(),
            clock=SystemClock(),
        ).handle(w2, po_case_id=case.id, supplier_update_id=update.id)
    # A save that skipped the read (a stale aggregate W1 handed over): RLS
    # makes the row absent to the UPDATE, so it is the concurrency refusal.
    stale = await repo.get(_context(W1), case.id)
    assert stale is not None
    stale.pass_qc()
    with pytest.raises(ConflictError):
        await repo.save(w2, stale)

    assert gateway.calls == []
    assert await _rows(sessions, case, workspace=W1) == dict.fromkeys(_TABLES, 1)
    after = await repo.get(_context(W1), case.id)
    assert after is not None and after.state is CaseState.QC


async def test_rls_refuses_a_row_written_into_another_workspace(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # WITH CHECK: under W2's settings, a row claiming W1 is refused.
    case, _ = await _w1_case(sessions)

    with pytest.raises(DBAPIError, match="row-level security"):
        await SqlSupplierUpdateRepository(sessions).add(_context(W2), _update(case, W1))


async def test_the_database_refuses_a_supplier_update_in_another_workspace_than_its_case(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Through RLS as W2, onto W1's case: only the composite FK can refuse it,
    # because Postgres checks an FK without row security.
    case, _ = await _w1_case(sessions)

    with pytest.raises(IntegrityError, match="fk_supplier_updates_tenant_id_po_cases"):
        await SqlSupplierUpdateRepository(sessions).add(_context(W2), _update(case, W2))


async def test_the_database_refuses_an_analysis_in_another_workspace_than_its_case(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    case, update = await _w1_case(sessions)

    with pytest.raises(IntegrityError, match="fk_delay_impact_analyses_tenant_id_"):
        await SqlDelayImpactAnalysisRepository(sessions).add(
            _context(W2), _analysis(case, update, W2)
        )


async def test_the_database_refuses_a_transition_in_another_workspace_than_its_case(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    case, _ = await _w1_case(sessions)

    with pytest.raises(IntegrityError, match="fk_po_case_state_transitions_tenant_id_po_cases"):
        async with sessions() as session, session.begin():
            await session.execute(
                sa.text(
                    "SELECT set_config('app.tenant_id', :t, true),"
                    " set_config('app.workspace_id', :w, true)"
                ),
                {"t": str(TENANT), "w": str(W2)},
            )
            await session.execute(
                sa.text(
                    "INSERT INTO supply_chain.po_case_state_transitions"
                    " (id, tenant_id, workspace_id, po_case_id, from_state, to_state)"
                    " VALUES (:id, :t, :w, :case, 'qc', 'in_transit')"
                ),
                {"id": str(uuid.uuid4()), "t": str(TENANT), "w": str(W2), "case": str(case.id)},
            )
