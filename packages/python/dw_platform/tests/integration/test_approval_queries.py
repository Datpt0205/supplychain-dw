"""Integration: `SqlPendingApprovalQuery` under RLS.

What a bounded context's narrow read of the approval inbox must get right,
and only a real database can show: the prefix is literal (LIKE's `_` would
otherwise match any character, and `leave_request.` holds one), only pending
rows count, the count is every match while the rows are the newest few, and
another tenant's approvals are not there at all.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from pg_harness import DatabaseUrls
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_platform.adapters.persistence.approval_queries import SqlPendingApprovalQuery
from dw_platform.adapters.persistence.repositories import SqlApprovalRepository
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.domain.approval import ApprovalRequest, ApprovalStatus

pytestmark = pytest.mark.integration

_PREFIX = "leave_request.decide."


@pytest.fixture
async def sessions(db_urls: DatabaseUrls) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


def _context(workspace_id: uuid.UUID | None = None) -> AccessContext:
    # A fresh tenant per test: approvals written by other tests in this
    # database never leak into these counts.
    return AccessContext(
        tenant_id=uuid.uuid4(),
        workspace_id=workspace_id or uuid.uuid4(),
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=frozenset(),
        plan_id="professional",
    )


async def _add(
    sessions: async_sessionmaker[AsyncSession],
    context: AccessContext,
    approval_type: str,
    *,
    status: ApprovalStatus = ApprovalStatus.PENDING,
) -> ApprovalRequest:
    request = ApprovalRequest(
        id=uuid.uuid4(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        approval_type=approval_type,
        requested_by=UserId(context.principal_id),
        reason="test",
        payload={"case_id": str(uuid.uuid4())},
        status=status,
    )
    async with tenant_session(sessions, TenantScope.from_access_context(context)) as session:
        await SqlApprovalRepository(session).add(request)
    return request


async def test_the_prefix_is_matched_literally_and_only_pending_rows_count(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    context = _context()
    wanted = await _add(sessions, context, f"{_PREFIX}cancel")
    # `_` is LIKE's any-one-character wildcard: unescaped, this would match.
    await _add(sessions, context, "supplyXchain.caseXaction.cancel")
    await _add(sessions, context, "workflow.review")
    await _add(sessions, context, f"{_PREFIX}flag_blocked", status=ApprovalStatus.APPROVED)

    total, rows = await SqlPendingApprovalQuery(
        sessions, ScopeAuthorizationService()
    ).list_pending_by_type_prefix(context, prefix=_PREFIX, limit=10)

    assert total == 1
    assert [row.id for row in rows] == [wanted.id]


async def test_the_count_is_every_match_and_the_rows_are_the_newest(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    context = _context()
    added = [await _add(sessions, context, f"{_PREFIX}cancel") for _ in range(3)]

    total, rows = await SqlPendingApprovalQuery(
        sessions, ScopeAuthorizationService()
    ).list_pending_by_type_prefix(context, prefix=_PREFIX, limit=2)

    assert total == 3
    assert [row.id for row in rows] == [added[2].id, added[1].id]


async def test_another_tenants_approvals_are_not_there(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    mine = _context()
    # The other tenant's row carries MY workspace id (UUIDs are not
    # tenant-bound): only the tenant boundary can keep it out, not the
    # workspace filter of platform-runtime/approval-audit-and-workspace/02.
    theirs = _context(workspace_id=mine.workspace_id)
    await _add(sessions, theirs, f"{_PREFIX}cancel")

    total, rows = await SqlPendingApprovalQuery(
        sessions, ScopeAuthorizationService()
    ).list_pending_by_type_prefix(mine, prefix=_PREFIX, limit=10)

    assert (total, rows) == (0, [])
