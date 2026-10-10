"""SQL persistence for proposal lists (381374b3cb35; ticket ai-automation/08).

All three tables run under `tenant_session` (tenant AND workspace bound) and
name both in every statement as a second layer. One reading per prompt version
and one decision per row are the database's: the UNIQUEs turn a race into a
`ConflictError` by constraint name. Audit events commit with their row. The
lane's queue is a definer function returning ids only.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_kernel.errors import ConflictError
from dw_platform.adapters.persistence.repositories import SqlAuditRepository
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence import tables
from dw_supply_chain.application.proposal_lists import (
    ListReading,
    NewListReading,
    NewProposalList,
    NewRowDecision,
    ProposalList,
    QueuedList,
    RowDecision,
    RowDecisionRecord,
)
from dw_supply_chain.domain.extraction import ExtractionStatus
from dw_supply_chain.domain.proposal_list import TakenCodes

_l = tables.proposal_lists
_r = tables.proposal_list_readings
_d = tables.proposal_list_decisions
_c = tables.product_dev_cases
_i = tables.item_codes
_s = tables.skus
READING_TAKEN = "uq_proposal_list_readings_tenant_id_list_id_prompt"
ROW_DECIDED = "uq_proposal_list_decisions_tenant_id_list_id_row_index"


def _scope(context: AccessContext) -> TenantScope:
    return TenantScope.from_access_context(context)


def _mine(table: sa.Table, context: AccessContext) -> tuple[sa.ColumnElement[bool], ...]:
    return (table.c.tenant_id == context.tenant_id, table.c.workspace_id == context.workspace_id)


_LIST_COLUMNS = (
    _l.c.id,
    _l.c.tenant_id,
    _l.c.workspace_id,
    _l.c.filename,
    _l.c.content_type,
    _l.c.size_bytes,
    _l.c.sha256,
    _l.c.uploaded_by,
    _l.c.created_at,
)


def _list(row: Row[Any]) -> ProposalList:
    return ProposalList(
        id=row.id,
        tenant_id=row.tenant_id,
        workspace_id=row.workspace_id,
        filename=row.filename,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        uploaded_by=row.uploaded_by,
        created_at=row.created_at,
    )


@dataclass(frozen=True)
class SqlProposalLists:
    """Implements `ProposalListRepositoryPort`."""

    session_factory: async_sessionmaker[AsyncSession]

    async def add(self, context: AccessContext, new: NewProposalList, *, audit: AuditEvent) -> None:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            await session.execute(
                sa.insert(_l).values(
                    id=new.id,
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    filename=new.filename,
                    content_type=new.content_type,
                    size_bytes=len(new.content),
                    sha256=new.sha256,
                    content=new.content,
                    uploaded_by=context.principal_id,
                )
            )
            await SqlAuditRepository(session).append(audit)

    async def get(self, context: AccessContext, list_id: uuid.UUID) -> ProposalList | None:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            row = (
                await session.execute(
                    sa.select(*_LIST_COLUMNS).where(*_mine(_l, context), _l.c.id == list_id)
                )
            ).first()
        return None if row is None else _list(row)

    async def content(self, context: AccessContext, list_id: uuid.UUID) -> bytes | None:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            data = await session.scalar(
                sa.select(_l.c.content).where(*_mine(_l, context), _l.c.id == list_id)
            )
        return None if data is None else bytes(data)

    async def recent(self, context: AccessContext, limit: int) -> list[ProposalList]:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            rows = (
                await session.execute(
                    sa.select(*_LIST_COLUMNS)
                    .where(*_mine(_l, context))
                    .order_by(_l.c.created_at.desc(), _l.c.id.desc())
                    .limit(limit)
                )
            ).all()
        return [_list(r) for r in rows]

    async def latest_reading(
        self, context: AccessContext, list_id: uuid.UUID
    ) -> ListReading | None:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            row = (
                await session.execute(
                    sa.select(_r)
                    .where(*_mine(_r, context), _r.c.list_id == list_id)
                    .order_by(_r.c.created_at.desc(), _r.c.id.desc())
                    .limit(1)
                )
            ).first()
        if row is None:
            return None
        m = row._mapping
        return ListReading(
            id=m[_r.c.id],
            list_id=m[_r.c.list_id],
            status=ExtractionStatus(m[_r.c.status]),
            prompt_id=m[_r.c.prompt_id],
            prompt_version=m[_r.c.prompt_version],
            rows=tuple(m[_r.c["items"]]),
            error=m[_r.c.error],
            created_at=m[_r.c.created_at],
        )

    async def add_reading(
        self, context: AccessContext, reading: NewListReading, *, audit: AuditEvent
    ) -> None:
        try:
            async with tenant_session(self.session_factory, _scope(context)) as session:
                await session.execute(
                    sa.insert(_r).values(
                        id=reading.id,
                        tenant_id=context.tenant_id,
                        workspace_id=context.workspace_id,
                        list_id=reading.list_id,
                        status=reading.status.value,
                        prompt_id=reading.prompt_id,
                        prompt_version=reading.prompt_version,
                        model_profile=reading.model_profile,
                        items=[dict(r) for r in reading.rows],
                        redactions=reading.redactions,
                        error=None if reading.error is None else reading.error[:500],
                    )
                )
                await SqlAuditRepository(session).append(audit)
        except IntegrityError as exc:
            if READING_TAKEN in str(exc.orig):
                raise ConflictError("this list is already read under this prompt") from exc
            raise

    async def decisions(
        self, context: AccessContext, list_id: uuid.UUID
    ) -> list[RowDecisionRecord]:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            rows = (
                await session.execute(
                    sa.select(_d)
                    .where(*_mine(_d, context), _d.c.list_id == list_id)
                    .order_by(_d.c.row_index)
                )
            ).all()
        return [
            RowDecisionRecord(
                row_index=r.row_index,
                decision=RowDecision(r.decision),
                product_dev_case_id=r.product_dev_case_id,
                reason=r.reason,
                decided_by=r.decided_by,
                decided_at=r.decided_at,
            )
            for r in rows
        ]

    async def decide(
        self, context: AccessContext, decision: NewRowDecision, *, audit: AuditEvent
    ) -> None:
        try:
            async with tenant_session(self.session_factory, _scope(context)) as session:
                await session.execute(
                    sa.insert(_d).values(
                        id=decision.id,
                        tenant_id=context.tenant_id,
                        workspace_id=context.workspace_id,
                        list_id=decision.list_id,
                        row_index=decision.row_index,
                        decision=decision.decision.value,
                        product_dev_case_id=decision.product_dev_case_id,
                        reason=decision.reason,
                        decided_by=context.principal_id,
                    )
                )
                await SqlAuditRepository(session).append(audit)
        except IntegrityError as exc:
            if ROW_DECIDED in str(exc.orig):
                raise ConflictError(
                    "dòng này đã được xử lý", details={"row": decision.row_index}
                ) from exc
            raise


@dataclass(frozen=True)
class SqlProposalListQueue:
    """Implements `ProposalListQueuePort` through the definer function."""

    session_factory: async_sessionmaker[AsyncSession]

    async def awaiting(self, prompt_ref: str, limit: int) -> list[QueuedList]:
        async with self.session_factory() as session, session.begin():
            rows = (
                await session.execute(
                    sa.text(
                        "SELECT tenant_id, workspace_id, list_id FROM"
                        " supply_chain.proposal_lists_awaiting_reading(:p, :n)"
                    ),
                    {"p": prompt_ref, "n": limit},
                )
            ).all()
        return [
            QueuedList(tenant_id=r.tenant_id, workspace_id=r.workspace_id, list_id=r.list_id)
            for r in rows
        ]


@dataclass(frozen=True)
class SqlTakenCodes:
    """Implements `TakenCodesPort`: only the codes and names asked about, in
    the caller's workspace."""

    session_factory: async_sessionmaker[AsyncSession]

    async def taken(
        self,
        context: AccessContext,
        *,
        proposal_codes: Sequence[str],
        item_codes: Sequence[str],
        product_names: Sequence[str],
    ) -> TakenCodes:
        codes = sorted({c.strip() for c in proposal_codes if c.strip()})[:500]
        items = sorted({c.strip() for c in item_codes if c.strip()})[:500]
        names = sorted({n.strip().casefold() for n in product_names if n.strip()})[:500]
        async with tenant_session(self.session_factory, _scope(context)) as session:
            found_codes = (
                set(
                    (
                        await session.execute(
                            sa.select(_c.c.proposal_code).where(
                                *_mine(_c, context), _c.c.proposal_code.in_(codes)
                            )
                        )
                    ).scalars()
                )
                if codes
                else set()
            )
            found_items: set[str] = set()
            if items:
                found_items |= set(
                    (
                        await session.execute(
                            sa.select(_i.c.code).where(*_mine(_i, context), _i.c.code.in_(items))
                        )
                    ).scalars()
                )
                found_items |= set(
                    (
                        await session.execute(
                            sa.select(_s.c.sku_code).where(
                                *_mine(_s, context), _s.c.sku_code.in_(items)
                            )
                        )
                    ).scalars()
                )
            found_names = (
                set(
                    (
                        await session.execute(
                            sa.select(_c.c.product_name).where(
                                *_mine(_c, context), sa.func.lower(_c.c.product_name).in_(names)
                            )
                        )
                    ).scalars()
                )
                if names
                else set()
            )
        return TakenCodes(
            proposal_codes=frozenset(found_codes),
            item_codes=frozenset(found_items),
            product_names=frozenset(found_names),
        )
