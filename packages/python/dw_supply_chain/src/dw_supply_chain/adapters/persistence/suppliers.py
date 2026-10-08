"""The supplier master record: a free-text name resolved to one supplier.

`resolve_supplier` is the one way a case comes to name a supplier: inside the
case write's own transaction, it finds the supplier of the caller's workspace
whose normalized name equals the given one, or creates it with the name as
written. "Equal" is the database's `supply_chain.normalize_supplier_name`, read
through the generated `normalized_name` and its UNIQUE key, so Python never
restates the rule. A supplier it creates is audited in the same transaction,
in the caller's name. RLS narrows every statement to the caller's tenant and
workspace, so another workspace's supplier of the same name is never found
and never reused.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_platform.adapters.persistence.repositories import SqlAuditRepository
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence import tables

_s = tables.suppliers
SUPPLIER_CREATED = "supply_chain.supplier.created"


@dataclass(frozen=True, slots=True)
class ResolvedSupplier:
    id: uuid.UUID
    # As stored: the spelling the supplier was first created with.
    name: str


def _same_supplier(name: str) -> sa.ColumnElement[bool]:
    return _s.c.normalized_name == sa.func.supply_chain.normalize_supplier_name(name)


async def _find(session: AsyncSession, name: str) -> ResolvedSupplier | None:
    row = (await session.execute(sa.select(_s.c.id, _s.c.name).where(_same_supplier(name)))).first()
    return None if row is None else ResolvedSupplier(id=row.id, name=row.name)


async def resolve_supplier(
    session: AsyncSession, context: AccessContext, workspace_id: uuid.UUID, name: str
) -> ResolvedSupplier:
    """The workspace's supplier named `name` (normalized), created if none is.
    Runs in the caller's transaction; a concurrent creation of the same name
    is not an error: the insert yields to it and the read finds it."""
    found = await _find(session, name)
    if found is not None:
        return found
    created = (
        await session.execute(
            pg_insert(_s)
            .values(
                id=uuid.uuid4(),
                tenant_id=context.tenant_id,
                workspace_id=workspace_id,
                name=name.strip(),
            )
            .on_conflict_do_nothing(
                constraint="uq_suppliers_tenant_id_workspace_id_normalized_name"
            )
            .returning(_s.c.id, _s.c.name)
        )
    ).first()
    if created is None:
        raced = await _find(session, name)
        assert raced is not None  # the conflicting row is this workspace's
        return raced
    await SqlAuditRepository(session).append(
        AuditEvent(
            id=uuid.uuid4(),
            tenant_id=TenantId(context.tenant_id),
            workspace_id=WorkspaceId(workspace_id),
            actor_id=UserId(context.principal_id),
            action=SUPPLIER_CREATED,
            resource_type="supplier",
            resource_id=str(created.id),
            occurred_at=datetime.now(UTC),
            details={"name": created.name},
        )
    )
    return ResolvedSupplier(id=created.id, name=created.name)
