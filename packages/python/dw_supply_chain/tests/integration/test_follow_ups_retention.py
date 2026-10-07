"""Integration: closed follow-ups are pruned after their tenant's term, and
nothing else is (ticket P3).

`supply_chain.prune_follow_ups(interval)` is the only way a follow-up row is
deleted outside its case's cascade: `dw_app` has no DELETE on the table and
calls the function, which runs as its definer and so decides itself what it
may touch — only the bound tenant's bound workspace, only closed rows, only
past the term. These tests age rows from the migrator and prune as `dw_app`.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from supply_chain_harness import REPO_ROOT, DatabaseUrls

from dw_kernel.ids import TenantId, WorkspaceId
from dw_platform.adapters.persistence.policy_overrides import SqlPolicyOverrideRepository
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_supply_chain.adapters.persistence.follow_up_repository import (
    SqlFollowUpRepository,
    SqlWorkspacesWithCases,
)
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.application.follow_up_retention import PruneClosedFollowUps
from dw_supply_chain.application.follow_up_sweep import sweep_context
from dw_supply_chain.application.handlers import FOLLOW_UP_POLICY_ID
from dw_supply_chain.domain.po_case import POCase, POCaseId
from dw_supply_chain.follow_up_policy import load_supply_chain_follow_up_policy
from dw_supply_chain.policy_files import FOLLOW_UP_POLICY_FILE

pytestmark = pytest.mark.integration

PLATFORM_POLICY = load_supply_chain_follow_up_policy(
    REPO_ROOT / "configs" / "policies" / FOLLOW_UP_POLICY_FILE
)


@dataclass(frozen=True)
class _Stack:
    sessions: async_sessionmaker[AsyncSession]
    migrator: AsyncEngine

    def lane(self) -> PruneClosedFollowUps:
        return PruneClosedFollowUps(
            workspaces=SqlWorkspacesWithCases(self.sessions),
            policy_override_repo=SqlPolicyOverrideRepository(self.sessions),
            platform_default_policy=PLATFORM_POLICY,
            follow_ups=SqlFollowUpRepository(self.sessions),
        )


@pytest.fixture
async def stack(db_urls: DatabaseUrls) -> AsyncIterator[_Stack]:
    app = create_async_engine(db_urls.app, poolclass=NullPool)
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    yield _Stack(async_sessionmaker(app, expire_on_commit=False), migrator)
    await app.dispose()
    await migrator.dispose()


async def _case(stack: _Stack, tenant: uuid.UUID, workspace: uuid.UUID) -> POCase:
    case = POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(tenant),
        workspace_id=WorkspaceId(workspace),
        po_reference=f"PO-RT-{uuid.uuid4().hex[:8]}",
        supplier_name="Kangaroo",
    )
    await SqlPOCaseRepository(stack.sessions).add(sweep_context(tenant, workspace), case)
    return case


async def _follow_up(stack: _Stack, case: POCase, *, status: str, age_days: int) -> uuid.UUID:
    """A follow-up opened `age_days` + 1 days ago and, unless open, closed
    `age_days` days ago."""
    follow_up_id = uuid.uuid4()
    async with stack.migrator.begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO supply_chain.follow_ups (id, tenant_id, workspace_id, po_case_id,"
                " kind, episode, days, recipient_scopes, status, opened_at, closed_at)"
                " VALUES (:id, :t, :w, :c, 'update_reminder', :e, 1,"
                " '[\"supply_chain.supplier_update.write\"]'::jsonb, :s,"
                " now() - make_interval(days => :age + 1),"
                " CASE WHEN :s = 'open' THEN NULL ELSE now() - make_interval(days => :age) END)"
            ),
            {
                "id": follow_up_id,
                "t": case.tenant_id.value,
                "w": case.workspace_id.value,
                "c": case.id.value,
                "e": uuid.uuid4().hex,
                "s": status,
                "age": age_days,
            },
        )
    return follow_up_id


async def _surviving(stack: _Stack, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    async with stack.migrator.connect() as conn:
        rows = await conn.execute(
            sa.text("SELECT id FROM supply_chain.follow_ups WHERE id = ANY(:ids)"), {"ids": ids}
        )
    return {row.id for row in rows}


async def _set_term(stack: _Stack, tenant: uuid.UUID, days: int) -> None:
    document = PLATFORM_POLICY.model_dump(mode="json") | {"closed_retention_days": days}
    async with stack.migrator.begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO platform.tenants (id, slug, name) VALUES (:t, :s, 'retention')"
                " ON CONFLICT (id) DO NOTHING"
            ),
            {"t": tenant, "s": f"rt-{tenant.hex[:12]}"},
        )
        await conn.execute(
            sa.text(
                "INSERT INTO platform.policy_overrides (id, tenant_id, policy_id, content)"
                " VALUES (:id, :t, :p, CAST(:c AS jsonb))"
            ),
            {"id": uuid.uuid4(), "t": tenant, "p": FOLLOW_UP_POLICY_ID, "c": json.dumps(document)},
        )


async def test_closed_follow_ups_past_the_term_go_and_nothing_else_does(stack: _Stack) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    case = await _case(stack, tenant, workspace)
    old_done = await _follow_up(stack, case, status="done", age_days=181)
    old_resolved = await _follow_up(stack, case, status="resolved", age_days=400)
    recent_done = await _follow_up(stack, case, status="done", age_days=179)
    ancient_open = await _follow_up(stack, case, status="open", age_days=3000)
    fresh_open = await _follow_up(stack, case, status="open", age_days=0)

    await stack.lane().prune()

    assert await _surviving(
        stack, [old_done, old_resolved, recent_done, ancient_open, fresh_open]
    ) == {recent_done, ancient_open, fresh_open}


async def test_each_tenant_is_pruned_by_its_own_term(stack: _Stack) -> None:
    """Tenant B keeps 400 days: its 200-day-old row survives the pass that
    removes tenant A's row of the same age."""
    a, a_ws, b, b_ws = (uuid.uuid4() for _ in range(4))
    a_old = await _follow_up(stack, await _case(stack, a, a_ws), status="done", age_days=200)
    b_old = await _follow_up(stack, await _case(stack, b, b_ws), status="done", age_days=200)
    await _set_term(stack, b, 400)

    await stack.lane().prune()

    assert await _surviving(stack, [a_old, b_old]) == {b_old}


async def _prune_as_app(stack: _Stack, scope: TenantScope | None, older_than: str) -> int:
    if scope is None:
        async with stack.sessions() as session, session.begin():
            gone = await session.scalar(
                sa.text("SELECT supply_chain.prune_follow_ups(CAST(CAST(:i AS text) AS interval))"),
                {"i": older_than},
            )
    else:
        async with tenant_session(stack.sessions, scope) as session:
            gone = await session.scalar(
                sa.text("SELECT supply_chain.prune_follow_ups(CAST(CAST(:i AS text) AS interval))"),
                {"i": older_than},
            )
    return int(gone or 0)


async def test_a_prune_bound_to_one_tenant_cannot_reach_another(stack: _Stack) -> None:
    """Both tenants under ONE workspace id, so only the tenant clause stands
    between tenant A's prune and tenant B's rows."""
    a, b, workspace = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _case(stack, a, workspace)
    b_old = await _follow_up(
        stack, await _case(stack, b, workspace), status="resolved", age_days=500
    )

    gone = await _prune_as_app(
        stack, TenantScope.from_access_context(sweep_context(a, workspace)), "1 day"
    )

    assert gone == 0
    assert await _surviving(stack, [b_old]) == {b_old}


async def test_a_prune_bound_to_one_workspace_leaves_the_tenants_other_workspaces(
    stack: _Stack,
) -> None:
    tenant, ws1, ws2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _case(stack, tenant, ws1)
    other = await _follow_up(stack, await _case(stack, tenant, ws2), status="done", age_days=500)

    await _prune_as_app(stack, TenantScope.from_access_context(sweep_context(tenant, ws1)), "1 day")

    assert await _surviving(stack, [other]) == {other}


async def test_a_prune_bound_to_no_tenant_deletes_nothing(stack: _Stack) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    old = await _follow_up(
        stack, await _case(stack, tenant, workspace), status="done", age_days=500
    )

    assert await _prune_as_app(stack, None, "1 day") == 0
    assert await _surviving(stack, [old]) == {old}


@pytest.mark.parametrize("older_than", ["0 days", "-5 days", "23 hours"])
async def test_a_term_under_a_day_is_refused_at_the_privilege_boundary(
    stack: _Stack, older_than: str
) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    old = await _follow_up(stack, await _case(stack, tenant, workspace), status="done", age_days=2)

    with pytest.raises(DBAPIError, match="at least one day"):
        await _prune_as_app(
            stack, TenantScope.from_access_context(sweep_context(tenant, workspace)), older_than
        )
    assert await _surviving(stack, [old]) == {old}


async def test_the_function_is_a_definer_only_dw_app_may_run(stack: _Stack) -> None:
    async with stack.migrator.connect() as conn:
        row = (
            await conn.execute(
                sa.text(
                    "SELECT p.prosecdef,"
                    " has_function_privilege('dw_app', p.oid, 'EXECUTE'),"
                    " has_function_privilege('public', p.oid, 'EXECUTE'),"
                    " has_table_privilege('dw_app', 'supply_chain.follow_ups', 'DELETE'),"
                    " has_table_privilege('dw_app', 'supply_chain.follow_ups', 'TRUNCATE')"
                    " FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
                    " WHERE n.nspname = 'supply_chain' AND p.proname = 'prune_follow_ups'"
                )
            )
        ).one()
    assert tuple(row) == (True, True, False, False, False)
