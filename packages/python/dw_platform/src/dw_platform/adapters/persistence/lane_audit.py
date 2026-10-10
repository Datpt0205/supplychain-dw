"""Appending a background lane's audit events from a cross-tenant transaction.

A drain (`app.worker_drain`) reaches every tenant's rows, but
`platform.audit_events` checks `app.tenant_id` on insert like any other tenant
table. So each event's own tenant is bound, per transaction, right before it is
written: the row lands in the tenant it describes and nowhere else.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from dw_platform.adapters.persistence.repositories import SqlAuditRepository
from dw_platform.domain.audit import AuditEvent

__all__ = ["append_across_tenants"]

_BIND_TENANT = text("SELECT set_config('app.tenant_id', :t, true)")


async def append_across_tenants(session: AsyncSession, events: Iterable[AuditEvent]) -> None:
    """Write each event under its own tenant, inside the caller's transaction.

    Call it last in that transaction, after the change it records: it leaves
    the last event's tenant bound until the transaction ends.
    """
    audit = SqlAuditRepository(session)
    for event in events:
        await session.execute(_BIND_TENANT, {"t": str(event.tenant_id.value)})
        await audit.append(event)
