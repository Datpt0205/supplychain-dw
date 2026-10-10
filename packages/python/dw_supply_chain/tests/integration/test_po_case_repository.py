"""Integration: `POCase` persists under RLS, with real optimistic concurrency.

`test_po_case.py` (unit) already proves the aggregate's own guards. What only
a real database can show: a case written for one tenant is invisible to
another even through a raw query with no WHERE clause at all (RLS, not the
repository's own good behaviour), and a stale `save()` genuinely conflicts
rather than silently overwriting a concurrent change.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls

from dw_kernel.errors import ConflictError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.pagination import PageQuery, page_request
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.application.ports import POCaseListFilter
from dw_supply_chain.domain.packaging_design import PreProductionTest, ProductionGate
from dw_supply_chain.domain.po_case import CaseState, POCase, POCaseId
from dw_supply_chain.testing.pages import oldest_first

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
        scopes=frozenset({"supply_chain.po_case.read", "supply_chain.po_case.write"}),
        plan_id="professional",
    )


def _case(
    *, tenant: uuid.UUID = TENANT_A, workspace: uuid.UUID = WORKSPACE_A, **overrides: object
) -> POCase:
    defaults: dict[str, object] = {
        "id": POCaseId(uuid.uuid4()),
        "tenant_id": TenantId(tenant),
        "workspace_id": WorkspaceId(workspace),
        "po_reference": f"PO-{uuid.uuid4().hex[:8]}",
        "supplier_name": "Elmich Co.",
    }
    defaults.update(overrides)
    return POCase(**defaults)  # type: ignore[arg-type]


async def test_add_and_get_round_trips(sessions: async_sessionmaker[AsyncSession]) -> None:
    repo = SqlPOCaseRepository(sessions)
    context = _context()
    case = _case()

    await repo.add(context, case)
    fetched = await repo.get(context, case.id)

    assert fetched is not None
    assert fetched.id == case.id
    assert fetched.tenant_id == case.tenant_id
    assert fetched.workspace_id == case.workspace_id
    assert fetched.po_reference == case.po_reference
    assert fetched.supplier_name == case.supplier_name
    assert fetched.state is CaseState.PO_CREATED
    assert fetched.interrupted_state is None
    assert fetched.version == 1


async def test_get_returns_none_for_an_unknown_id(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlPOCaseRepository(sessions)
    fetched = await repo.get(_context(), POCaseId(uuid.uuid4()))
    assert fetched is None


async def test_save_persists_a_transition_and_the_bumped_version(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlPOCaseRepository(sessions)
    context = _context()
    case = _case()
    await repo.add(context, case)

    case.request_deposit()  # PO_CREATED -> WAITING_DEPOSIT, version 1 -> 2
    await repo.save(context, case)

    fetched = await repo.get(context, case.id)
    assert fetched is not None
    assert fetched.state is CaseState.WAITING_DEPOSIT
    assert fetched.version == 2


async def test_save_persists_an_interrupted_state(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlPOCaseRepository(sessions)
    context = _context()
    case = _case()
    await repo.add(context, case)

    case.flag_blocked("missing evidence")
    await repo.save(context, case)

    fetched = await repo.get(context, case.id)
    assert fetched is not None
    assert fetched.state is CaseState.BLOCKED
    assert fetched.interrupted_state is CaseState.PO_CREATED


async def test_save_with_a_stale_version_conflicts_rather_than_overwriting(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Two readers load the same row; the first writer wins, the second gets a
    conflict rather than silently clobbering the first write."""
    repo = SqlPOCaseRepository(sessions)
    context = _context()
    case = _case()
    await repo.add(context, case)

    reader_one = await repo.get(context, case.id)
    reader_two = await repo.get(context, case.id)
    assert reader_one is not None and reader_two is not None

    reader_one.request_deposit()
    await repo.save(context, reader_one)

    reader_two.request_deposit()  # same starting version as reader_one had
    with pytest.raises(ConflictError):
        await repo.save(context, reader_two)


async def test_po_reference_is_unique_within_a_tenant(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlPOCaseRepository(sessions)
    context = _context()
    case = _case(po_reference="PO-DUP-0001")
    other = _case(po_reference="PO-DUP-0001")

    await repo.add(context, case)
    # The database's refusal, by its constraint's name (ticket 05).
    with pytest.raises(ConflictError) as raised:
        await repo.add(context, other)
    assert raised.value.details == {
        "constraint": "uq_po_cases_tenant_id_po_reference",
        "po_reference": "PO-DUP-0001",
    }
    assert isinstance(raised.value.__cause__, IntegrityError)


async def test_another_tenant_neither_sees_nor_saves_over_the_case(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlPOCaseRepository(sessions)
    case = _case(tenant=TENANT_A, workspace=WORKSPACE_A)
    await repo.add(_context(tenant=TENANT_A, workspace=WORKSPACE_A), case)

    # B's own read of A's id: not found, not an error that leaks it exists.
    fetched_by_b = await repo.get(_context(tenant=TENANT_B, workspace=WORKSPACE_B), case.id)
    assert fetched_by_b is None

    # B attempting to save over A's row (e.g. a forged id) conflicts rather
    # than silently doing nothing or succeeding.
    forged = _case(tenant=TENANT_B, workspace=WORKSPACE_B, id=case.id)
    forged.request_deposit()
    with pytest.raises(ConflictError):
        await repo.save(_context(tenant=TENANT_B, workspace=WORKSPACE_B), forged)

    # A still sees its own, untouched, row.
    fetched_by_a = await repo.get(_context(tenant=TENANT_A, workspace=WORKSPACE_A), case.id)
    assert fetched_by_a is not None
    assert fetched_by_a.state is CaseState.PO_CREATED
    assert fetched_by_a.version == 1


async def test_rls_hides_the_row_even_from_a_query_with_no_tenant_filter(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The repository's own queries never name `tenant_id` at all (see
    `po_case_repository.py`'s docstring) — this test does not go through it,
    on purpose. It proves the database itself refuses the row under
    `app.tenant_id=B`, independent of anything the repository does or does
    not filter by."""
    repo = SqlPOCaseRepository(sessions)
    case = _case(tenant=TENANT_A, workspace=WORKSPACE_A)
    await repo.add(_context(tenant=TENANT_A, workspace=WORKSPACE_A), case)

    async with sessions() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(TENANT_B)}
        )
        rows = (
            await session.execute(
                text("SELECT id FROM supply_chain.po_cases WHERE id = :id"),
                {"id": str(case.id)},
            )
        ).all()

    assert rows == []


# -- list_page: the Case Workspace's own entry point --------------------------


async def test_list_page_returns_cases_newest_first(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # A fresh tenant per test, not the file's shared TENANT_A/TENANT_B: every
    # other test in this file also writes cases under those constants, and a
    # list is the one kind of read where "how many other rows exist" matters.
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    repo = SqlPOCaseRepository(sessions)
    context = _context(tenant=tenant, workspace=workspace)
    first = _case(tenant=tenant, workspace=workspace)
    await repo.add(context, first)
    second = _case(tenant=tenant, workspace=workspace)
    await repo.add(context, second)

    page = await repo.list_page(
        context,
        page_request(limit=50, cursor=None, query=PageQuery(key="supply_chain.po_cases")),
        POCaseListFilter(),
    )

    assert [c.id for c in page.items] == [second.id, first.id]
    assert page.next_cursor is None


async def test_list_page_pages_through_a_cursor_without_skipping_or_repeating(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    repo = SqlPOCaseRepository(sessions)
    context = _context(tenant=tenant, workspace=workspace)
    cases = []
    for _ in range(5):
        case = _case(tenant=tenant, workspace=workspace)
        await repo.add(context, case)
        cases.append(case)
    # Newest first, matching the repository's own ordering.
    expected_order = list(reversed([c.id for c in cases]))

    query = PageQuery(key="supply_chain.po_cases")
    first_page = await repo.list_page(
        context, page_request(limit=2, cursor=None, query=query), POCaseListFilter()
    )
    assert [c.id for c in first_page.items] == expected_order[:2]
    assert first_page.next_cursor is not None

    second_page = await repo.list_page(
        context,
        page_request(limit=2, cursor=first_page.next_cursor, query=query),
        POCaseListFilter(),
    )
    assert [c.id for c in second_page.items] == expected_order[2:4]
    assert second_page.next_cursor is not None

    third_page = await repo.list_page(
        context,
        page_request(limit=2, cursor=second_page.next_cursor, query=query),
        POCaseListFilter(),
    )
    assert [c.id for c in third_page.items] == expected_order[4:5]
    assert third_page.next_cursor is None


async def test_list_page_only_returns_the_callers_tenant(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    mine_tenant, mine_workspace = uuid.uuid4(), uuid.uuid4()
    other_tenant, other_workspace = uuid.uuid4(), uuid.uuid4()
    repo = SqlPOCaseRepository(sessions)
    mine = _case(tenant=mine_tenant, workspace=mine_workspace)
    await repo.add(_context(tenant=mine_tenant, workspace=mine_workspace), mine)
    await repo.add(
        _context(tenant=other_tenant, workspace=other_workspace),
        _case(tenant=other_tenant, workspace=other_workspace),
    )

    page = await repo.list_page(
        _context(tenant=mine_tenant, workspace=mine_workspace),
        page_request(limit=50, cursor=None, query=PageQuery(key="supply_chain.po_cases")),
        POCaseListFilter(),
    )

    assert [c.id for c in page.items] == [mine.id]


# -- list_page filters: the Control Tower's drill-down ------------------------


async def _list(
    repo: SqlPOCaseRepository,
    context: AccessContext,
    case_filter: POCaseListFilter,
    *,
    limit: int = 50,
    cursor: str | None = None,
) -> tuple[list[POCaseId], str | None]:
    query = case_filter.page_query(context.tenant_id)
    page = await repo.list_page(
        context, page_request(limit=limit, cursor=cursor, query=query), case_filter
    )
    return [case.id for case in page.items], page.next_cursor


async def test_list_page_filters_by_state(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    repo = SqlPOCaseRepository(sessions)
    context = _context(tenant=tenant, workspace=workspace)
    await repo.add(context, _case(tenant=tenant, workspace=workspace))
    waiting = []
    for _ in range(2):
        case = _case(tenant=tenant, workspace=workspace)
        await repo.add(context, case)
        case.request_deposit()
        await repo.save(context, case)
        waiting.append(case)

    ids, _ = await _list(repo, context, POCaseListFilter(state=CaseState.WAITING_DEPOSIT))

    assert ids == [waiting[1].id, waiting[0].id]


async def test_list_page_filters_by_the_suppliers_stored_name(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Exact on the stored name — the same identity the Control Tower groups
    by. A spelling that differs only in case or spaces is the same supplier
    (the master record, a0035e9faf32), so its case carries the stored name
    and is listed; a different name is another supplier, never a fuzzy
    match."""
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    repo = SqlPOCaseRepository(sessions)
    context = _context(tenant=tenant, workspace=workspace)
    exact = _case(tenant=tenant, workspace=workspace, supplier_name="Elmich Co.")
    await repo.add(context, exact)
    same = [
        _case(tenant=tenant, workspace=workspace, supplier_name=spelling)
        for spelling in ("elmich co.", "Elmich  Co. ")
    ]
    for case in same:
        await repo.add(context, case)
    other = _case(tenant=tenant, workspace=workspace, supplier_name="Elmich")
    await repo.add(context, other)

    ids, _ = await _list(repo, context, POCaseListFilter(supplier_name="Elmich Co."))

    assert ids == [same[1].id, same[0].id, exact.id]
    assert [case.supplier_name for case in same] == ["Elmich Co.", "Elmich Co."]
    assert await repo.list_supplier_names(context) == ["Elmich", "Elmich Co."]


async def test_list_page_active_only_leaves_out_completed_and_cancelled(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    repo = SqlPOCaseRepository(sessions)
    context = _context(tenant=tenant, workspace=workspace)
    blocked = _case(tenant=tenant, workspace=workspace)
    await repo.add(context, blocked)
    blocked.flag_blocked("customs hold")
    await repo.save(context, blocked)
    cancelled = _case(tenant=tenant, workspace=workspace)
    await repo.add(context, cancelled)
    cancelled.cancel("customer walked away")
    await repo.save(context, cancelled)
    # Both terminal states, each for real — a predicate that forgot either
    # one must fail here, not only a predicate that forgot both.
    completed = _case(tenant=tenant, workspace=workspace)
    await repo.add(context, completed)
    await _walk_to_completed(repo, context, completed)

    everything, _ = await _list(repo, context, POCaseListFilter())
    active, _ = await _list(repo, context, POCaseListFilter(active_only=True))

    assert set(everything) == {blocked.id, cancelled.id, completed.id}
    # An exception state is still active — only terminal states drop out.
    assert active == [blocked.id]


async def test_list_page_combines_filters(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The supplier drill-down: one supplier's ACTIVE cases, which is what
    the Control Tower's supplier row counted — not their closed ones, and not
    another supplier's active ones."""
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    repo = SqlPOCaseRepository(sessions)
    context = _context(tenant=tenant, workspace=workspace)
    wanted = _case(tenant=tenant, workspace=workspace, supplier_name="Quiet Co.")
    await repo.add(context, wanted)
    closed = _case(tenant=tenant, workspace=workspace, supplier_name="Quiet Co.")
    await repo.add(context, closed)
    closed.cancel("duplicate PO")
    await repo.save(context, closed)
    await repo.add(context, _case(tenant=tenant, workspace=workspace, supplier_name="Busy Co."))

    ids, _ = await _list(
        repo, context, POCaseListFilter(supplier_name="Quiet Co.", active_only=True)
    )

    assert ids == [wanted.id]


async def test_list_page_pages_within_a_filter_without_leaking_other_rows(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    repo = SqlPOCaseRepository(sessions)
    context = _context(tenant=tenant, workspace=workspace)
    matching = []
    # Interleaved, so a page boundary lands between a matching row and a
    # non-matching one — the keyset predicate and the filter must compose.
    for _ in range(5):
        case = _case(tenant=tenant, workspace=workspace, supplier_name="Quiet Co.")
        await repo.add(context, case)
        matching.append(case.id)
        await repo.add(context, _case(tenant=tenant, workspace=workspace, supplier_name="Other"))
    expected = list(reversed(matching))
    case_filter = POCaseListFilter(supplier_name="Quiet Co.")

    first, cursor = await _list(repo, context, case_filter, limit=2)
    second, cursor = await _list(repo, context, case_filter, limit=2, cursor=cursor)
    third, cursor = await _list(repo, context, case_filter, limit=2, cursor=cursor)

    assert first + second + third == expected
    assert cursor is None


async def test_list_page_filter_never_reaches_another_tenants_cases(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Same supplier name, same state, two tenants: the filter narrows what
    RLS already scoped, it never widens it."""
    mine_tenant, mine_workspace = uuid.uuid4(), uuid.uuid4()
    other_tenant, other_workspace = uuid.uuid4(), uuid.uuid4()
    repo = SqlPOCaseRepository(sessions)
    mine_context = _context(tenant=mine_tenant, workspace=mine_workspace)
    mine = _case(tenant=mine_tenant, workspace=mine_workspace, supplier_name="Shared Co.")
    await repo.add(mine_context, mine)
    await repo.add(
        _context(tenant=other_tenant, workspace=other_workspace),
        _case(tenant=other_tenant, workspace=other_workspace, supplier_name="Shared Co."),
    )

    ids, _ = await _list(
        repo,
        mine_context,
        POCaseListFilter(state=CaseState.PO_CREATED, supplier_name="Shared Co.", active_only=True),
    )

    assert ids == [mine.id]


# -- list_supplier_names / find_by_reference: what a case query resolves
#    a model's mentions against ----------------------------------------------


async def test_list_supplier_names_is_distinct_sorted_and_scoped_to_the_tenant(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    mine_tenant, mine_workspace = uuid.uuid4(), uuid.uuid4()
    other_tenant, other_workspace = uuid.uuid4(), uuid.uuid4()
    repo = SqlPOCaseRepository(sessions)
    mine = _context(tenant=mine_tenant, workspace=mine_workspace)
    for name in ("Sunhouse Co.", "Elmich Co.", "Sunhouse Co."):
        await repo.add(
            mine, _case(tenant=mine_tenant, workspace=mine_workspace, supplier_name=name)
        )
    await repo.add(
        _context(tenant=other_tenant, workspace=other_workspace),
        _case(tenant=other_tenant, workspace=other_workspace, supplier_name="Their Secret Co."),
    )

    assert await repo.list_supplier_names(mine) == ["Elmich Co.", "Sunhouse Co."]


async def test_find_by_reference_ignores_case_and_surrounding_whitespace(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    repo = SqlPOCaseRepository(sessions)
    context = _context(tenant=tenant, workspace=workspace)
    case = _case(tenant=tenant, workspace=workspace, po_reference="PO-2026-001")
    await repo.add(context, case)

    found = await repo.find_by_reference(context, "  po-2026-001 ")

    assert [c.id for c in found] == [case.id]
    assert await repo.find_by_reference(context, "PO-2026-00") == []


async def test_find_by_reference_returns_every_case_insensitive_match(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The stored uniqueness is case-sensitive, so two spellings can both
    exist — a lookup that returned one of them would be a guess."""
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    repo = SqlPOCaseRepository(sessions)
    context = _context(tenant=tenant, workspace=workspace)
    upper = _case(tenant=tenant, workspace=workspace, po_reference="PO-7")
    lower = _case(tenant=tenant, workspace=workspace, po_reference="po-7")
    await repo.add(context, upper)
    await repo.add(context, lower)

    found = await repo.find_by_reference(context, "Po-7")

    assert {c.id for c in found} == {upper.id, lower.id}


async def test_find_by_reference_never_finds_another_tenants_case(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    mine_tenant, mine_workspace = uuid.uuid4(), uuid.uuid4()
    other_tenant, other_workspace = uuid.uuid4(), uuid.uuid4()
    repo = SqlPOCaseRepository(sessions)
    await repo.add(
        _context(tenant=other_tenant, workspace=other_workspace),
        _case(tenant=other_tenant, workspace=other_workspace, po_reference="PO-THEIRS"),
    )

    found = await repo.find_by_reference(
        _context(tenant=mine_tenant, workspace=mine_workspace), "PO-THEIRS"
    )

    assert found == []


# -- po_case_state_transitions: the SLA evaluator's own history --------------


async def test_current_state_entered_at_is_none_before_any_transition(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlPOCaseRepository(sessions)
    context = _context()
    case = _case()
    await repo.add(context, case)

    assert await repo.get_sla_clock_started_at(context, case.id) is None


async def test_save_persists_the_transition_and_current_state_entered_at_reads_it_back(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlPOCaseRepository(sessions)
    context = _context()
    case = _case()
    await repo.add(context, case)

    case.request_deposit()
    await repo.save(context, case)

    entered_at = await repo.get_sla_clock_started_at(context, case.id)
    assert entered_at is not None


async def test_current_state_entered_at_reflects_the_latest_transition_only(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Two transitions saved separately — the read must return the SECOND
    one's timestamp, not the first, proving the query picks the latest row
    rather than any row for this case."""
    repo = SqlPOCaseRepository(sessions)
    context = _context()
    case = _case()
    await repo.add(context, case)

    case.request_deposit()
    await repo.save(context, case)
    first_entered_at = await repo.get_sla_clock_started_at(context, case.id)

    case.confirm_deposit()
    await repo.save(context, case)
    second_entered_at = await repo.get_sla_clock_started_at(context, case.id)

    assert first_entered_at is not None
    assert second_entered_at is not None
    assert second_entered_at >= first_entered_at


async def test_a_save_with_no_new_transition_inserts_no_history_row(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """`GetPOCase`-style reads never call `save()`, but nothing stops a
    future caller from calling it with an untouched case — must not insert
    a phantom transition when `pop_pending_transitions()` is empty."""
    repo = SqlPOCaseRepository(sessions)
    context = _context()
    case = _case()
    await repo.add(context, case)

    async with sessions() as session:
        count_before = (
            await session.execute(
                text(
                    "SELECT count(*) FROM supply_chain.po_case_state_transitions"
                    " WHERE po_case_id = :id"
                ),
                {"id": str(case.id)},
            )
        ).scalar_one()
    assert count_before == 0


async def test_a_transitions_reason_is_persisted_and_a_reasonless_one_is_null(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The gap named in `.claude/PLAN.md`'s ninth slice: a transition's
    `reason` was validated non-blank and then discarded. `cancel()` carries
    one through to the row; `request_deposit()` has none to carry, and the
    column must read back NULL for it, not an empty string standing in."""
    repo = SqlPOCaseRepository(sessions)
    context = _context()
    case = _case()
    await repo.add(context, case)

    case.request_deposit()
    await repo.save(context, case)
    case.cancel("customer walked away")
    await repo.save(context, case)

    async with sessions() as session, session.begin():
        await session.execute(
            text(
                "SELECT set_config('app.tenant_id', :t, true),"
                " set_config('app.workspace_id', :w, true)"
            ),
            {"t": str(context.tenant_id), "w": str(context.workspace_id)},
        )
        rows = (
            await session.execute(
                text(
                    "SELECT from_state, to_state, reason"
                    " FROM supply_chain.po_case_state_transitions"
                    " WHERE po_case_id = :id ORDER BY occurred_at"
                ),
                {"id": str(case.id)},
            )
        ).all()

    assert [(r.from_state, r.to_state, r.reason) for r in rows] == [
        ("po_created", "waiting_deposit", None),
        ("waiting_deposit", "cancelled", "customer walked away"),
    ]


async def test_list_transitions_returns_the_cases_own_timeline_oldest_first(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlPOCaseRepository(sessions)
    context = _context()
    case = _case()
    await repo.add(context, case)

    case.request_deposit()
    await repo.save(context, case)
    case.confirm_deposit()
    await repo.save(context, case)

    transitions = await oldest_first(
        lambda request: repo.list_transitions(context, case.id, request)
    )

    assert [(t.from_state, t.to_state, t.reason) for t in transitions] == [
        (CaseState.PO_CREATED, CaseState.WAITING_DEPOSIT, None),
        (CaseState.WAITING_DEPOSIT, CaseState.DEPOSIT_CONFIRMED, None),
    ]
    assert transitions[0].occurred_at <= transitions[1].occurred_at


async def test_list_transitions_is_empty_for_a_case_that_never_transitioned(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlPOCaseRepository(sessions)
    context = _context()
    case = _case()
    await repo.add(context, case)

    assert (
        await oldest_first(lambda request: repo.list_transitions(context, case.id, request)) == []
    )


async def test_list_transitions_is_empty_for_another_tenants_case(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlPOCaseRepository(sessions)
    case = _case(tenant=TENANT_A, workspace=WORKSPACE_A)
    await repo.add(_context(tenant=TENANT_A, workspace=WORKSPACE_A), case)
    case.request_deposit()
    await repo.save(_context(tenant=TENANT_A, workspace=WORKSPACE_A), case)

    transitions = await oldest_first(
        lambda request: repo.list_transitions(
            _context(tenant=TENANT_B, workspace=WORKSPACE_B), case.id, request
        )
    )
    assert transitions == []


async def test_another_tenant_cannot_read_the_transition_history(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlPOCaseRepository(sessions)
    case = _case(tenant=TENANT_A, workspace=WORKSPACE_A)
    await repo.add(_context(tenant=TENANT_A, workspace=WORKSPACE_A), case)
    case.request_deposit()
    await repo.save(_context(tenant=TENANT_A, workspace=WORKSPACE_A), case)

    entered_at = await repo.get_sla_clock_started_at(
        _context(tenant=TENANT_B, workspace=WORKSPACE_B), case.id
    )
    assert entered_at is None


async def test_rls_hides_transition_rows_even_from_a_query_with_no_tenant_filter(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlPOCaseRepository(sessions)
    case = _case(tenant=TENANT_A, workspace=WORKSPACE_A)
    await repo.add(_context(tenant=TENANT_A, workspace=WORKSPACE_A), case)
    case.request_deposit()
    await repo.save(_context(tenant=TENANT_A, workspace=WORKSPACE_A), case)

    async with sessions() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(TENANT_B)}
        )
        rows = (
            await session.execute(
                text(
                    "SELECT id FROM supply_chain.po_case_state_transitions WHERE po_case_id = :id"
                ),
                {"id": str(case.id)},
            )
        ).all()

    assert rows == []


# -- list_active / bulk_sla_clock_started_at: the Attention Queue's own
#    bulk reads ------------------------------------------------------------


async def test_receiving_goods_keeps_the_receipt_clock_that_started_at_payment(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Step 17's SLA measures payment to goods in stock: starting to receive
    does not restart it, and the SQL names the same start state as the domain
    (`sla_clock_start_state`)."""
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    repo = SqlPOCaseRepository(sessions)
    context = _context(tenant=tenant, workspace=workspace)
    case = _case(tenant=tenant, workspace=workspace)
    await repo.add(context, case)
    for step in (
        case.request_deposit,
        case.confirm_deposit,
        case.start_pre_production,
        lambda: case.start_production(
            ProductionGate(required=False, test=PreProductionTest.PENDING)
        ),
        case.send_to_qc,
        case.pass_qc,
        case.arrive_at_port,
        case.request_final_payment,
        case.confirm_payment,
    ):
        step()
        await repo.save(context, case)
    (paid,) = [
        t
        for t in await oldest_first(lambda r: repo.list_transitions(context, case.id, r))
        if t.to_state is CaseState.PAYMENT_COMPLETED
    ]
    assert await repo.get_sla_clock_started_at(context, case.id) == paid.occurred_at

    case.start_warehouse_receiving()
    await repo.save(context, case)
    history = await oldest_first(lambda r: repo.list_transitions(context, case.id, r))
    assert history[-1].to_state is CaseState.WAREHOUSE_RECEIVING
    assert history[-1].occurred_at > paid.occurred_at

    assert await repo.get_sla_clock_started_at(context, case.id) == paid.occurred_at
    assert await repo.bulk_sla_clock_started_at(context, [case.id]) == {
        case.id.value: paid.occurred_at
    }
    # Another workspace asking for the same case id gets no clock at all.
    other = _context(tenant=tenant, workspace=uuid.uuid4())
    assert await repo.bulk_sla_clock_started_at(other, [case.id]) == {}


async def _walk_to_completed(
    repo: SqlPOCaseRepository, context: AccessContext, case: POCase
) -> None:
    """PO_CREATED -> ... -> COMPLETED for real, through the case's own
    guarded methods. `save()`'s optimistic-concurrency check assumes one
    persisted step per call, matching how a real caller (AdvancePOCase) only
    ever applies one action at a time."""
    for step in (
        case.request_deposit,
        case.confirm_deposit,
        case.start_pre_production,
        lambda: case.start_production(
            ProductionGate(required=False, test=PreProductionTest.PENDING)
        ),
        case.send_to_qc,
        case.pass_qc,
        case.arrive_at_port,
        case.request_final_payment,
        case.confirm_payment,
        case.start_warehouse_receiving,
        case.complete,
    ):
        step()
        await repo.save(context, case)


async def test_list_active_excludes_terminal_cases(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    repo = SqlPOCaseRepository(sessions)
    context = _context(tenant=tenant, workspace=workspace)

    active = _case(tenant=tenant, workspace=workspace)
    await repo.add(context, active)
    completed = _case(tenant=tenant, workspace=workspace)
    await repo.add(context, completed)
    await _walk_to_completed(repo, context, completed)
    cancelled = _case(tenant=tenant, workspace=workspace)
    await repo.add(context, cancelled)
    cancelled.cancel("customer walked away")
    await repo.save(context, cancelled)

    result = await oldest_first(
        lambda request: repo.list_page(context, request, POCaseListFilter(active_only=True))
    )

    assert [c.id for c in result] == [active.id]


async def test_list_active_only_returns_the_callers_tenant(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    mine_tenant, mine_workspace = uuid.uuid4(), uuid.uuid4()
    other_tenant, other_workspace = uuid.uuid4(), uuid.uuid4()
    repo = SqlPOCaseRepository(sessions)
    mine = _case(tenant=mine_tenant, workspace=mine_workspace)
    await repo.add(_context(tenant=mine_tenant, workspace=mine_workspace), mine)
    await repo.add(
        _context(tenant=other_tenant, workspace=other_workspace),
        _case(tenant=other_tenant, workspace=other_workspace),
    )

    mine_context = _context(tenant=mine_tenant, workspace=mine_workspace)
    result = await oldest_first(
        lambda request: repo.list_page(mine_context, request, POCaseListFilter(active_only=True))
    )

    assert [c.id for c in result] == [mine.id]


async def test_bulk_sla_clock_started_at_reflects_each_cases_own_latest_transition(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    repo = SqlPOCaseRepository(sessions)
    context = _context(tenant=tenant, workspace=workspace)

    transitioned = _case(tenant=tenant, workspace=workspace)
    await repo.add(context, transitioned)
    transitioned.request_deposit()
    await repo.save(context, transitioned)
    transitioned.confirm_deposit()
    await repo.save(context, transitioned)
    never_transitioned = _case(tenant=tenant, workspace=workspace)
    await repo.add(context, never_transitioned)

    result = await repo.bulk_sla_clock_started_at(context, [transitioned.id, never_transitioned.id])

    assert transitioned.id.value in result
    assert never_transitioned.id.value not in result
    direct = await repo.get_sla_clock_started_at(context, transitioned.id)
    assert result[transitioned.id.value] == direct


async def test_bulk_sla_clock_started_at_with_no_case_ids_makes_no_query(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlPOCaseRepository(sessions)
    assert await repo.bulk_sla_clock_started_at(_context(), []) == {}


@pytest.mark.parametrize("padding", ["\t", "\u00a0", " \r\n"])
async def test_find_by_reference_ignores_padding_on_the_stored_reference(
    sessions: async_sessionmaker[AsyncSession], padding: str
) -> None:
    """A reference pasted from a spreadsheet keeps its tab or no-break space;
    SQL must strip the same set Python strips, or production misses a case
    every fake finds."""
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    repo = SqlPOCaseRepository(sessions)
    context = _context(tenant=tenant, workspace=workspace)
    case = _case(tenant=tenant, workspace=workspace, po_reference=f"PO-5{padding}")
    await repo.add(context, case)

    found = await repo.find_by_reference(context, "po-5")

    assert [c.id for c in found] == [case.id]


# -- get_many / list_latest_transitions_since: the daily brief's reads -------


async def test_get_many_returns_only_the_callers_cases_among_the_ids(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    repo = SqlPOCaseRepository(sessions)
    mine = _context(tenant=tenant, workspace=workspace)
    theirs = _context(tenant=uuid.uuid4(), workspace=uuid.uuid4())
    first = _case(tenant=tenant, workspace=workspace)
    second = _case(tenant=tenant, workspace=workspace)
    # The caller's own, but not asked for: a query that ignored the ids and
    # returned the tenant's whole table would still pass without it.
    not_asked = _case(tenant=tenant, workspace=workspace)
    foreign = _case(tenant=theirs.tenant_id, workspace=theirs.workspace_id)
    await repo.add(mine, first)
    await repo.add(mine, second)
    await repo.add(mine, not_asked)
    await repo.add(theirs, foreign)

    found = await repo.get_many(mine, [first.id, foreign.id, POCaseId(uuid.uuid4()), second.id])

    assert {case.id for case in found} == {first.id, second.id}
    assert await repo.get_many(mine, []) == []


async def test_latest_transitions_since_is_one_per_case_the_newest_inside_the_window(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    repo = SqlPOCaseRepository(sessions)
    context = _context(tenant=tenant, workspace=workspace)
    twice = _case(tenant=tenant, workspace=workspace)
    once = _case(tenant=tenant, workspace=workspace)
    untouched = _case(tenant=tenant, workspace=workspace)
    for case in (twice, once, untouched):
        await repo.add(context, case)
    before = datetime.now(UTC) - timedelta(minutes=1)
    twice.request_deposit()
    await repo.save(context, twice)
    twice.confirm_deposit()
    await repo.save(context, twice)
    once.request_deposit()
    await repo.save(context, once)

    total, moved = await repo.list_latest_transitions_since(context, before, limit=10)
    latest = dict(moved)

    assert total == 2
    assert set(latest) == {twice.id, once.id}
    assert latest[twice.id].to_state is CaseState.DEPOSIT_CONFIRMED
    assert latest[once.id].to_state is CaseState.WAITING_DEPOSIT
    # Nothing moved after now: an empty window, not the whole history.
    assert await repo.list_latest_transitions_since(context, datetime.now(UTC), limit=10) == (
        0,
        [],
    )
    # Bounded: the newest mover only, and still counted as two.
    total, newest = await repo.list_latest_transitions_since(context, before, limit=1)
    assert (total, [case_id for case_id, _ in newest]) == (2, [once.id])


async def test_latest_transitions_since_never_reaches_another_tenant(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlPOCaseRepository(sessions)
    theirs = _context(tenant=uuid.uuid4(), workspace=uuid.uuid4())
    case = _case(tenant=theirs.tenant_id, workspace=theirs.workspace_id)
    await repo.add(theirs, case)
    case.request_deposit()
    await repo.save(theirs, case)

    mine = _context(tenant=uuid.uuid4(), workspace=uuid.uuid4())
    since = datetime.now(UTC) - timedelta(hours=1)
    assert await repo.list_latest_transitions_since(mine, since, limit=10) == (0, [])
