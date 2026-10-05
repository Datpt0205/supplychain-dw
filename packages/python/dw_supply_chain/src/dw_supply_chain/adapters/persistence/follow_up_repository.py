"""SQL persistence for follow-ups, and the sweep's one cross-tenant read.

Every read and write runs under `tenant_session`, bound to the context's
tenant (the table's RLS narrows by it), and every statement also names the
tenant explicitly. The exception is `SqlTenantsWithCases`, which calls the
SECURITY DEFINER `supply_chain.tenants_with_cases()`: ids only, so the sweep
knows whom to visit before it can bind anyone.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_platform.adapters.persistence.repositories import SqlAuditRepository
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence import tables
from dw_supply_chain.application.ports import FollowUpDraft, FollowUpRecord
from dw_supply_chain.domain.follow_up import FollowUpKind, FollowUpStatus

_f = tables.follow_ups
_c = tables.po_cases


def _record(row: Row[tuple[object, ...]]) -> FollowUpRecord:
    m = row._mapping
    return FollowUpRecord(
        id=m[_f.c.id],
        po_case_id=m[_f.c.po_case_id],
        workspace_id=m[_f.c.workspace_id],
        po_reference=m[_c.c.po_reference],
        supplier_name=m[_c.c.supplier_name],
        kind=FollowUpKind(m[_f.c.kind]),
        episode=m[_f.c.episode],
        milestone=m[_f.c.milestone],
        days=m[_f.c.days],
        limit_days=m[_f.c.limit_days],
        recipient_scopes=frozenset(m[_f.c.recipient_scopes]),
        status=FollowUpStatus(m[_f.c.status]),
        opened_at=m[_f.c.opened_at],
        notified_at=m[_f.c.notified_at],
        closed_at=m[_f.c.closed_at],
        closed_by=m[_f.c.closed_by],
        close_note=m[_f.c.close_note],
    )


def _with_case() -> sa.Select[tuple[object, ...]]:
    return sa.select(_f, _c.c.po_reference, _c.c.supplier_name).select_from(
        _f.join(_c, _c.c.id == _f.c.po_case_id)
    )


@dataclass(frozen=True)
class SqlFollowUpRepository:
    session_factory: async_sessionmaker[AsyncSession]

    async def open(self, context: AccessContext, drafts: Sequence[FollowUpDraft]) -> int:
        if not drafts:
            return 0
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            result = await session.execute(
                pg_insert(_f)
                .values(
                    [
                        {
                            "id": draft.id,
                            "tenant_id": context.tenant_id,
                            "workspace_id": draft.due.case.workspace_id.value,
                            "po_case_id": draft.due.case.id.value,
                            "kind": draft.due.kind.value,
                            "episode": draft.due.episode,
                            "milestone": draft.due.milestone,
                            "days": draft.due.days,
                            "limit_days": draft.due.limit_days,
                            "recipient_scopes": sorted(draft.recipient_scopes),
                        }
                        for draft in drafts
                    ]
                )
                .on_conflict_do_nothing(constraint="uq_follow_ups_po_case_id_kind_episode")
            )
        return int(getattr(result, "rowcount", 0) or 0)

    async def list_open(self, context: AccessContext) -> list[FollowUpRecord]:
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            rows = (
                await session.execute(
                    _with_case()
                    .where(
                        _f.c.tenant_id == context.tenant_id,
                        _f.c.status == FollowUpStatus.OPEN.value,
                    )
                    .order_by(_f.c.opened_at.desc(), _f.c.id.desc())
                )
            ).all()
        return [_record(row) for row in rows]

    async def resolve(self, context: AccessContext, follow_up_ids: Sequence[uuid.UUID]) -> None:
        if not follow_up_ids:
            return
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            await session.execute(
                sa.update(_f)
                .where(
                    _f.c.tenant_id == context.tenant_id,
                    _f.c.id.in_(list(follow_up_ids)),
                    _f.c.status == FollowUpStatus.OPEN.value,
                )
                .values(status=FollowUpStatus.RESOLVED.value, closed_at=sa.func.now())
            )

    async def mark_notified(self, context: AccessContext, follow_up_id: uuid.UUID) -> None:
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            await session.execute(
                sa.update(_f)
                .where(_f.c.tenant_id == context.tenant_id, _f.c.id == follow_up_id)
                .values(notified_at=sa.func.now())
            )

    async def get(self, context: AccessContext, follow_up_id: uuid.UUID) -> FollowUpRecord | None:
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            row = (
                await session.execute(
                    _with_case().where(_f.c.tenant_id == context.tenant_id, _f.c.id == follow_up_id)
                )
            ).first()
        return None if row is None else _record(row)

    async def close_done(
        self,
        context: AccessContext,
        follow_up_id: uuid.UUID,
        *,
        note: str | None,
        audit: AuditEvent,
    ) -> bool:
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            row = (
                await session.execute(
                    sa.update(_f)
                    .where(
                        _f.c.tenant_id == context.tenant_id,
                        _f.c.id == follow_up_id,
                        _f.c.status == FollowUpStatus.OPEN.value,
                    )
                    .values(
                        status=FollowUpStatus.DONE.value,
                        closed_at=sa.func.now(),
                        closed_by=context.principal_id,
                        close_note=note,
                    )
                    .returning(_f.c.id)
                )
            ).first()
            if row is None:
                return False
            await SqlAuditRepository(session).append(audit)
        return True


@dataclass(frozen=True)
class SqlTenantsWithCases:
    session_factory: async_sessionmaker[AsyncSession]

    async def tenants(self) -> list[tuple[uuid.UUID, uuid.UUID]]:
        async with self.session_factory() as session, session.begin():
            rows = (
                await session.execute(
                    sa.text("SELECT tenant_id, workspace_id FROM supply_chain.tenants_with_cases()")
                )
            ).all()
        return [(row.tenant_id, row.workspace_id) for row in rows]
