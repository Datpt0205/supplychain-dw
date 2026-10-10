"""SQL persistence for follow-ups, and the sweep's one cross-tenant read.

Every read and write runs under `tenant_session`, bound to the context's
tenant and workspace (the table's RLS narrows by both, `d56da3dd2146`), and
every statement also names both explicitly. A follow-up is about a PO case or
a product case; a read joins whichever it names for the case's identifier and
supplier, each join under that table's own RLS. The exception is
`SqlWorkspacesWithCases`, which calls the SECURITY DEFINER
`supply_chain.workspaces_with_cases()`: ids only, so the sweep knows where to
go before it can bind anyone. Retention deletes through the SECURITY DEFINER
`supply_chain.prune_follow_ups(interval)`, bound by the same session settings.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_platform.adapters.persistence.repositories import SqlAuditRepository
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence import tables
from dw_supply_chain.application.ports import FollowUpDraft, FollowUpRecord
from dw_supply_chain.domain.case_document import CaseKind
from dw_supply_chain.domain.follow_up import FollowUpKind, FollowUpStatus

_f = tables.follow_ups
_c = tables.po_cases
_p = tables.product_dev_cases


def _in_scope(context: AccessContext) -> tuple[sa.ColumnElement[bool], ...]:
    return (_f.c.tenant_id == context.tenant_id, _f.c.workspace_id == context.workspace_id)


def _record(row: Row[tuple[object, ...]]) -> FollowUpRecord:
    m = row._mapping
    po_case_id = m[_f.c.po_case_id]
    is_po = po_case_id is not None
    return FollowUpRecord(
        id=m[_f.c.id],
        case_kind=CaseKind.PO if is_po else CaseKind.PRODUCT,
        case_id=po_case_id if is_po else m[_f.c.product_dev_case_id],
        workspace_id=m[_f.c.workspace_id],
        reference=m["po_reference"] if is_po else m["proposal_code"],
        supplier_name=m["po_supplier_name"] if is_po else m["product_supplier_name"],
        kind=FollowUpKind(m[_f.c.kind]),
        episode=m[_f.c.episode],
        milestone=m[_f.c.milestone],
        days=m[_f.c.days],
        limit_days=m[_f.c.limit_days],
        recipient_scopes=frozenset(m[_f.c.recipient_scopes]),
        recipient_user_id=m[_f.c.recipient_user_id],
        status=FollowUpStatus(m[_f.c.status]),
        opened_at=m[_f.c.opened_at],
        notified_at=m[_f.c.notified_at],
        closed_at=m[_f.c.closed_at],
        closed_by=m[_f.c.closed_by],
        close_note=m[_f.c.close_note],
    )


def _with_case() -> sa.Select[tuple[object, ...]]:
    """The follow-up with its case's identifier and supplier. Exactly one of
    the two outer joins finds a row (`ck_follow_ups_one_case`); each is
    narrowed by its own table's RLS as well as by the join's tenant."""
    return sa.select(
        _f,
        _c.c.po_reference.label("po_reference"),
        _c.c.supplier_name.label("po_supplier_name"),
        _p.c.proposal_code.label("proposal_code"),
        _p.c.supplier_name.label("product_supplier_name"),
    ).select_from(
        _f.outerjoin(
            _c, sa.and_(_c.c.tenant_id == _f.c.tenant_id, _c.c.id == _f.c.po_case_id)
        ).outerjoin(
            _p, sa.and_(_p.c.tenant_id == _f.c.tenant_id, _p.c.id == _f.c.product_dev_case_id)
        )
    )


def _row(context: AccessContext, draft: FollowUpDraft) -> dict[str, object]:
    subject = draft.due.subject
    return {
        "id": draft.id,
        "tenant_id": context.tenant_id,
        "workspace_id": subject.workspace_id,
        "po_case_id": subject.case_id if subject.case_kind is CaseKind.PO else None,
        "product_dev_case_id": (subject.case_id if subject.case_kind is CaseKind.PRODUCT else None),
        "kind": draft.due.kind.value,
        "episode": draft.due.episode,
        "milestone": draft.due.milestone,
        "days": draft.due.days,
        "limit_days": draft.due.limit_days,
        "recipient_scopes": sorted(draft.recipient_scopes),
        "recipient_user_id": draft.recipient_user_id,
    }


@dataclass(frozen=True)
class SqlFollowUpRepository:
    session_factory: async_sessionmaker[AsyncSession]

    async def open(
        self,
        context: AccessContext,
        drafts: Sequence[FollowUpDraft],
        *,
        audit: Callable[[FollowUpDraft], AuditEvent],
    ) -> int:
        if not drafts:
            return 0
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            opened = set(
                (
                    await session.execute(
                        pg_insert(_f)
                        .values([_row(context, draft) for draft in drafts])
                        # Either episode key (`uq_follow_ups_po_case_id_kind_episode`,
                        # `uq_follow_ups_product_dev_case_id_kind_episode`): an
                        # episode opens once, whichever kind of case it is on.
                        .on_conflict_do_nothing()
                        .returning(_f.c.id)
                    )
                ).scalars()
            )
            audits = SqlAuditRepository(session)
            for draft in drafts:
                if draft.id in opened:
                    await audits.append(audit(draft))
        return len(opened)

    async def list_open(self, context: AccessContext) -> list[FollowUpRecord]:
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            rows = (
                await session.execute(
                    _with_case()
                    .where(*_in_scope(context), _f.c.status == FollowUpStatus.OPEN.value)
                    .order_by(_f.c.opened_at.desc(), _f.c.id.desc())
                )
            ).all()
        return [_record(row) for row in rows]

    async def resolve(
        self,
        context: AccessContext,
        follow_ups: Sequence[FollowUpRecord],
        *,
        audit: Callable[[FollowUpRecord], AuditEvent],
    ) -> int:
        if not follow_ups:
            return 0
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            closed = set(
                (
                    await session.execute(
                        sa.update(_f)
                        .where(
                            *_in_scope(context),
                            # One array parameter, however many are stale.
                            _f.c.id
                            == sa.any_(
                                sa.literal(
                                    [record.id for record in follow_ups],
                                    postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
                                )
                            ),
                            _f.c.status == FollowUpStatus.OPEN.value,
                        )
                        .values(status=FollowUpStatus.RESOLVED.value, closed_at=sa.func.now())
                        .returning(_f.c.id)
                    )
                ).scalars()
            )
            audits = SqlAuditRepository(session)
            for record in follow_ups:
                if record.id in closed:
                    await audits.append(audit(record))
        return len(closed)

    async def mark_notified(self, context: AccessContext, follow_up_id: uuid.UUID) -> None:
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            await session.execute(
                sa.update(_f)
                .where(*_in_scope(context), _f.c.id == follow_up_id)
                .values(notified_at=sa.func.now())
            )

    async def get(self, context: AccessContext, follow_up_id: uuid.UUID) -> FollowUpRecord | None:
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            row = (
                await session.execute(
                    _with_case().where(*_in_scope(context), _f.c.id == follow_up_id)
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
                        *_in_scope(context),
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

    async def prune_closed(self, context: AccessContext, *, older_than_days: int) -> int:
        """Implements `ClosedFollowUpPrunePort` through
        `supply_chain.prune_follow_ups` (`dw_app` has no DELETE): the function
        deletes only closed rows of the workspace this session binds."""
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            gone = await session.scalar(
                sa.text("SELECT supply_chain.prune_follow_ups(make_interval(days => :days))"),
                {"days": older_than_days},
            )
        return int(gone or 0)


@dataclass(frozen=True)
class SqlWorkspacesWithCases:
    session_factory: async_sessionmaker[AsyncSession]

    async def workspaces(self) -> list[tuple[uuid.UUID, uuid.UUID]]:
        async with self.session_factory() as session, session.begin():
            rows = (
                await session.execute(
                    sa.text(
                        "SELECT tenant_id, workspace_id FROM supply_chain.workspaces_with_cases()"
                    )
                )
            ).all()
        return [(row.tenant_id, row.workspace_id) for row in rows]
