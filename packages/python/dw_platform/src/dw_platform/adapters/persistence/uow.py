"""SQL Unit of Work binding the trusted tenant context per transaction.

`SET LOCAL` (via ``set_config(..., is_local => true)``) scopes the RLS context
to the current transaction only — connections returned to the pool carry no
tenant state.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_kernel.errors import TenantContextMissingError
from dw_platform.adapters.persistence.approval_codes import SqlDecisionCodeLedger
from dw_platform.adapters.persistence.repositories import (
    SqlApprovalRepository,
    SqlAuditRepository,
    SqlFeedbackRepository,
    SqlOutboxRepository,
)
from dw_platform.adapters.persistence.tenant_session import TenantScope, bind_tenant
from dw_platform.application.access_context import AccessContext
from dw_platform.application.approval_codes import DecisionCodeLedgerPort
from dw_platform.application.ports import (
    ApprovalRepositoryPort,
    AuditRepositoryPort,
    FeedbackRepositoryPort,
    OutboxRepositoryPort,
)


class SqlPlatformUnitOfWork:
    """Implements ``PlatformUnitOfWork`` over one AsyncSession transaction."""

    approvals: ApprovalRepositoryPort
    audit: AuditRepositoryPort
    feedback: FeedbackRepositoryPort
    outbox: OutboxRepositoryPort
    decision_codes: DecisionCodeLedgerPort

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        context: AccessContext,
    ) -> None:
        if context is None:  # defensive: never run tenant-scoped SQL without context
            raise TenantContextMissingError("UnitOfWork requires an AccessContext")
        self._session_factory = session_factory
        self._context = context
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> SqlPlatformUnitOfWork:
        session = self._session_factory()
        try:
            await session.begin()
            await bind_tenant(session, TenantScope.from_access_context(self._context))
        except BaseException:
            await session.close()
            raise
        self._session = session
        self.approvals = SqlApprovalRepository(session)
        self.audit = SqlAuditRepository(session)
        self.feedback = SqlFeedbackRepository(session)
        self.outbox = SqlOutboxRepository(session)
        self.decision_codes = SqlDecisionCodeLedger(session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._session is not None
        try:
            if exc_type is not None:
                await self._session.rollback()
        finally:
            await self._session.close()
            self._session = None

    async def commit(self) -> None:
        assert self._session is not None, "UoW not entered"
        await self._session.commit()

    async def rollback(self) -> None:
        assert self._session is not None, "UoW not entered"
        await self._session.rollback()


@dataclass(frozen=True)
class SqlPlatformUnitOfWorkFactory:
    """Implements ``PlatformUnitOfWorkFactory``."""

    session_factory: async_sessionmaker[AsyncSession]

    def __call__(self, context: AccessContext) -> SqlPlatformUnitOfWork:
        return SqlPlatformUnitOfWork(self.session_factory, context)
