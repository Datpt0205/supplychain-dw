"""Integration: approvals and audit read within the caller's workspace.

RLS on `approval_requests` and `audit_events` narrows by tenant only, so the
workspace is narrowed by the repository (platform-runtime/approval-audit-and-
workspace/02; changing the policy is a separate decision). One tenant, two
workspaces: a member of W2 must not read W1's approval payloads or audit
details through any reader, and W1's own reads still see them.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from pg_harness import DatabaseUrls
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.pagination import PageQuery, PageRequest
from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.approval_queries import SqlPendingApprovalQuery
from dw_platform.adapters.persistence.uow import SqlPlatformUnitOfWorkFactory
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ApprovalAudience, ScopeAuthorizationService
from dw_platform.domain.approval import ApprovalRequest
from dw_platform.domain.audit import AuditEvent

pytestmark = pytest.mark.integration


def _sees(context: AccessContext) -> ApprovalAudience:
    """What `context` may see of the approvals (ADR 0004), as the API asks it."""
    return ApprovalAudience.of(context, ScopeAuthorizationService())


_PREFIX = "leave_request.decide."
_PAGE = PageRequest(limit=50, after=None, query=PageQuery(key="test.workspace_reads"))


@pytest.fixture
async def sessions(db_urls: DatabaseUrls) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


def _member(tenant: uuid.UUID, workspace: uuid.UUID) -> AccessContext:
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=frozenset({"approvals.read"}),
        plan_id="professional",
    )


def _two_workspaces() -> tuple[AccessContext, AccessContext]:
    """A in W1 and B in W2, one fresh tenant: other tests' rows never count."""
    tenant = uuid.uuid4()
    return _member(tenant, uuid.uuid4()), _member(tenant, uuid.uuid4())


async def _raise_approval(
    sessions: async_sessionmaker[AsyncSession], context: AccessContext
) -> ApprovalRequest:
    request = ApprovalRequest(
        id=uuid.uuid4(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        approval_type=f"{_PREFIX}cancel",
        requested_by=UserId(context.principal_id),
        reason="test",
        payload={"secret": f"of {context.workspace_id}"},
    )
    async with SqlPlatformUnitOfWorkFactory(sessions)(context) as uow:
        await uow.approvals.add(request)
        await uow.commit()
    return request


async def _record(
    sessions: async_sessionmaker[AsyncSession], context: AccessContext, run_id: uuid.UUID
) -> AuditEvent:
    event = AuditEvent(
        id=uuid.uuid4(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        actor_id=UserId(context.principal_id),
        action="run.completed",
        resource_type="worker_run",
        resource_id=str(run_id),
        run_id=run_id,
        details={"secret": f"of {context.workspace_id}"},
        occurred_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    async with SqlPlatformUnitOfWorkFactory(sessions)(context) as uow:
        await uow.audit.append(event)
        await uow.commit()
    return event


async def test_the_inbox_lists_only_the_callers_workspace(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    a, b = _two_workspaces()
    theirs = await _raise_approval(sessions, a)
    mine = await _raise_approval(sessions, b)

    async with SqlPlatformUnitOfWorkFactory(sessions)(b) as uow:
        page = await uow.approvals.list_pending(
            _PAGE, workspace_id=b.workspace_id, audience=_sees(b)
        )
    assert [request.id for request in page.items] == [mine.id]

    async with SqlPlatformUnitOfWorkFactory(sessions)(a) as uow:
        page = await uow.approvals.list_pending(
            _PAGE, workspace_id=a.workspace_id, audience=_sees(a)
        )
    assert [request.id for request in page.items] == [theirs.id]


async def test_another_workspaces_approval_is_not_found_by_id(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    a, b = _two_workspaces()
    theirs = await _raise_approval(sessions, a)

    async with SqlPlatformUnitOfWorkFactory(sessions)(b) as uow:
        assert (
            await uow.approvals.get(theirs.id, workspace_id=b.workspace_id, audience=_sees(b))
            is None
        )
    async with SqlPlatformUnitOfWorkFactory(sessions)(a) as uow:
        found = await uow.approvals.get(theirs.id, workspace_id=a.workspace_id, audience=_sees(a))
    assert found is not None
    assert found.id == theirs.id


async def test_a_contexts_pending_count_is_the_callers_workspace_only(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The second reader of the inbox (`SqlPendingApprovalQuery`, a context
    counting its own pending approvals): it must not count or show what the
    inbox itself would refuse."""
    a, b = _two_workspaces()
    await _raise_approval(sessions, a)
    mine = await _raise_approval(sessions, b)

    total, rows = await SqlPendingApprovalQuery(
        sessions, ScopeAuthorizationService()
    ).list_pending_by_type_prefix(b, prefix=_PREFIX, limit=10)

    assert total == 1
    assert [row.id for row in rows] == [mine.id]


async def test_the_audit_trail_lists_only_the_callers_workspace(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    a, b = _two_workspaces()
    theirs = await _record(sessions, a, uuid.uuid4())
    mine = await _record(sessions, b, uuid.uuid4())

    async with SqlPlatformUnitOfWorkFactory(sessions)(b) as uow:
        page = await uow.audit.list_page(_PAGE, workspace_id=b.workspace_id)
    assert [event.id for event in page.items] == [mine.id]

    async with SqlPlatformUnitOfWorkFactory(sessions)(a) as uow:
        page = await uow.audit.list_page(_PAGE, workspace_id=a.workspace_id)
    assert [event.id for event in page.items] == [theirs.id]


async def test_another_workspaces_run_timeline_is_empty(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    a, b = _two_workspaces()
    run_id = uuid.uuid4()
    theirs = await _record(sessions, a, run_id)

    async with SqlPlatformUnitOfWorkFactory(sessions)(b) as uow:
        assert await uow.audit.list_for_run(run_id, workspace_id=b.workspace_id) == []
    async with SqlPlatformUnitOfWorkFactory(sessions)(a) as uow:
        events = await uow.audit.list_for_run(run_id, workspace_id=a.workspace_id)
    assert [event.id for event in events] == [theirs.id]


async def test_the_audit_scope_separates_a_member_from_a_director(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """`GET /audit/events` asks for `audit.events` instead of `approvals.read`
    so that a `member` is refused and a `director` is not. That holds only
    while the catalogue says so; read it from the rows, not from a copy."""
    async with sessions() as session:
        rows = await session.execute(
            sa.select(tables.roles.c.key, tables.roles.c.scopes).where(
                tables.roles.c.key.in_(["member", "director"])
            )
        )
        scopes = {row.key: set(row.scopes) for row in rows}

    assert "approvals.read" in scopes["member"]
    assert "audit.events" not in scopes["member"]
    assert "audit.events" in scopes["director"]
