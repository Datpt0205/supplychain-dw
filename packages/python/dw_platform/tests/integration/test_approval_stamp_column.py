"""Integration: `approval_requests.required_scope` (ADR 0004, migration 36dabf47619c).

What only the database can show: the stamp survives a round trip, its shape is
refused by the CHECK (the one place the pattern is written), and a decision
never moves it, by code or by grant.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from pg_harness import DatabaseUrls
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_platform.adapters.persistence.repositories import SqlApprovalRepository
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ApprovalAudience, ScopeAuthorizationService
from dw_platform.domain.approval import ApprovalRequest, ApprovalStatus, DecisionOutcome

pytestmark = pytest.mark.integration


def _sees(context: AccessContext) -> ApprovalAudience:
    """What `context` may see of the approvals (ADR 0004), as the API asks it."""
    return ApprovalAudience.of(context, ScopeAuthorizationService())


BOARD_SCOPE = "demo.approve.board"


@pytest.fixture
async def sessions(db_urls: DatabaseUrls) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


def _context() -> AccessContext:
    return AccessContext(
        tenant_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=frozenset(),
        plan_id="professional",
    )


def _request(context: AccessContext, required_scope: Any) -> ApprovalRequest:
    return ApprovalRequest(
        id=uuid.uuid4(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        approval_type="demo.action.approve",
        requested_by=UserId(context.principal_id),
        reason="test",
        required_scope=required_scope,
    )


async def _add(
    sessions: async_sessionmaker[AsyncSession], context: AccessContext, request: ApprovalRequest
) -> None:
    async with tenant_session(sessions, TenantScope.from_access_context(context)) as session:
        await SqlApprovalRepository(session).add(request)


async def _get(
    sessions: async_sessionmaker[AsyncSession], context: AccessContext, request_id: uuid.UUID
) -> ApprovalRequest | None:
    async with tenant_session(sessions, TenantScope.from_access_context(context)) as session:
        return await SqlApprovalRepository(session).get(
            request_id, workspace_id=context.workspace_id, audience=_sees(context)
        )


@pytest.mark.parametrize("required_scope", [BOARD_SCOPE, None])
async def test_the_stamp_round_trips(
    sessions: async_sessionmaker[AsyncSession], required_scope: str | None
) -> None:
    context = _context()
    request = _request(context, required_scope)
    await _add(sessions, context, request)

    stored = await _get(sessions, context, request.id)

    assert stored is not None
    assert stored.required_scope == required_scope


@pytest.mark.parametrize(
    "malformed",
    [
        "Demo.approve",  # upper case
        "demo",  # one segment is a name, not a scope
        "",  # empty is not "no scope"; it would be a stamp nobody holds
        "demo.",
        ".approve",
        " demo.approve",
        "demo.approve board",
        "demo-x.approve",
    ],
)
async def test_the_database_refuses_a_malformed_stamp(
    sessions: async_sessionmaker[AsyncSession], malformed: str
) -> None:
    context = _context()
    with pytest.raises(IntegrityError, match="ck_approval_requests_required_scope"):
        await _add(sessions, context, _request(context, malformed))


async def test_a_non_string_stamp_is_refused_not_coerced(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """A number reaching the row as '5' would be a stamp nobody can satisfy and
    no reader can explain; the driver refuses it before the CHECK sees it."""
    context = _context()
    with pytest.raises(DBAPIError):
        await _add(sessions, context, _request(context, 5))


async def test_a_decision_does_not_move_the_stamp(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """`save` writes the decision columns only. Even an aggregate whose stamp was
    changed in memory leaves the stored stamp as it was raised."""
    context = _context()
    request = _request(context, BOARD_SCOPE)
    await _add(sessions, context, request)

    async with tenant_session(sessions, TenantScope.from_access_context(context)) as session:
        repo = SqlApprovalRepository(session)
        loaded = await repo.get(
            request.id, workspace_id=context.workspace_id, audience=_sees(context)
        )
        assert loaded is not None
        loaded.required_scope = "demo.approve.anyone"
        loaded.decide(
            decision_id=uuid.uuid4(),
            decided_by=UserId(uuid.uuid4()),
            outcome=DecisionOutcome.APPROVED,
            decided_at=datetime.now(UTC),
        )
        await repo.save(loaded)

    stored = await _get(sessions, context, request.id)
    assert stored is not None
    assert stored.status is ApprovalStatus.APPROVED
    assert stored.required_scope == BOARD_SCOPE


async def test_the_application_role_cannot_rewrite_the_stamp(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Credentials reach further than reviewed code: the grant refuses it too."""
    context = _context()
    request = _request(context, BOARD_SCOPE)
    await _add(sessions, context, request)

    with pytest.raises(DBAPIError, match="permission denied"):
        async with tenant_session(sessions, TenantScope.from_access_context(context)) as session:
            await session.execute(
                sa.text(
                    "UPDATE platform.approval_requests SET required_scope = NULL WHERE id = :id"
                ),
                {"id": request.id},
            )

    stored = await _get(sessions, context, request.id)
    assert stored is not None
    assert stored.required_scope == BOARD_SCOPE
