"""Integration: idempotent seed, membership lookup, UoW with tenant context."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from pg_harness import DatabaseUrls
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.uow import SqlPlatformUnitOfWorkFactory
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ApprovalAudience, ScopeAuthorizationService
from dw_platform.domain.approval import ApprovalRequest, DecisionOutcome
from dw_platform.domain.audit import AuditEvent
from dw_platform.domain.outbox import OutboxEvent
from dw_platform.testing.seed_env import seed_test_env

pytestmark = pytest.mark.integration


def _sees(context: AccessContext) -> ApprovalAudience:
    """What `context` may see of the approvals (ADR 0004), as the API asks it."""
    return ApprovalAudience.of(context, ScopeAuthorizationService())


NOW = datetime(2026, 7, 23, 11, 0, tzinfo=UTC)

# Roles and plans are owned by migration 0143, not the seed, so they are not
# listed here — a migrated database already has them and the seed leaves them
# untouched.
SEED_TABLES = ("tenants", "workspaces", "users", "memberships", "entitlements")


@pytest.fixture
async def migrator_engine(db_urls: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    """The migrator connection: owns every object and holds BYPASSRLS.

    Used only to arrange state a test needs to already exist. What the test
    then asserts must go through ``app_engine``, or it proves nothing about
    what the application is allowed to see.
    """
    engine = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def app_engine(db_urls: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    """The runtime role, subject to RLS exactly as a request is."""
    await seed_test_env(db_urls.migrator)
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_uow_persists_approval_audit_outbox_under_rls(
    db_urls: DatabaseUrls, migrator_engine: AsyncEngine, app_engine: AsyncEngine
) -> None:
    await seed_test_env(db_urls.migrator)
    async with migrator_engine.connect() as conn:
        alpha_id = (
            await conn.execute(
                sa.select(tables.tenants.c.id).where(tables.tenants.c.slug == "tenant-alpha")
            )
        ).scalar_one()
        alpha_ws = (
            await conn.execute(
                sa.select(tables.workspaces.c.id).where(
                    tables.workspaces.c.tenant_id == alpha_id,
                    # The seed's own workspace. The admin-console tests create
                    # more inside this same tenant, so "the one workspace" stopped
                    # being a thing that exists and this asked for it by name.
                    tables.workspaces.c.slug == "main",
                )
            )
        ).scalar_one()

    context = AccessContext(
        tenant_id=alpha_id,
        workspace_id=alpha_ws,
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=frozenset({"demo.write"}),
        plan_id="professional",
    )
    factory = SqlPlatformUnitOfWorkFactory(
        async_sessionmaker(app_engine, class_=AsyncSession, expire_on_commit=False)
    )

    request = ApprovalRequest(
        id=uuid.uuid4(),
        tenant_id=TenantId(alpha_id),
        workspace_id=WorkspaceId(alpha_ws),
        approval_type="demo.dispatch",
        requested_by=UserId(context.principal_id),
        reason="integration test",
    )
    async with factory(context) as uow:
        await uow.approvals.add(request)
        await uow.audit.append(
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=TenantId(alpha_id),
                workspace_id=WorkspaceId(alpha_ws),
                actor_id=UserId(context.principal_id),
                action="approval.requested",
                resource_type="approval_request",
                resource_id=str(request.id),
                occurred_at=NOW,
            )
        )
        await uow.outbox.add(
            OutboxEvent(
                id=uuid.uuid4(),
                tenant_id=TenantId(alpha_id),
                workspace_id=WorkspaceId(alpha_ws),
                event_type="platform.approval.requested",
                schema_version="1.0",
                aggregate_id=request.id,
                occurred_at=NOW,
            )
        )
        await uow.commit()

    # Read back + decide in a second transaction.
    async with factory(context) as uow:
        loaded = await uow.approvals.get(
            request.id, workspace_id=context.workspace_id, audience=_sees(context)
        )
        assert loaded is not None and loaded.status.value == "pending"
        decision = loaded.decide(
            decision_id=uuid.uuid4(),
            decided_by=UserId(context.principal_id),
            outcome=DecisionOutcome.APPROVED,
            decided_at=NOW,
        )
        await uow.approvals.save(loaded)
        await uow.approvals.add_decision(decision)
        await uow.commit()

    async with factory(context) as uow:
        final = await uow.approvals.get(
            request.id, workspace_id=context.workspace_id, audience=_sees(context)
        )
        assert final is not None and final.status.value == "approved"
        pending_outbox = await uow.outbox.list_unprocessed()
        assert any(e.aggregate_id == request.id for e in pending_outbox)
