"""SQL repository mapping `supply_chain.po_cases` <-> `POCase`.

Shape mirrors `dw_platform.adapters.persistence.membership_admin.
SqlMembershipAdminRepository`, not `SqlApprovalRepository`: this context has
no multi-repository unit of work to share a pre-bound session through, so
each method takes the session factory once (at construction) and the
caller's verified `context` per call, deriving its own tenant-scoped session
from `dw_platform.adapters.persistence.tenant_session` — the same mechanism
every other tenant-scoped repository in this platform uses. Queries here
name no `tenant_id` filter of their own: RLS is the one enforcement
mechanism for a Postgres-backed store in this repo; `dw_platform.
tenant_filter()` is for the stores that have no RLS to lean on (Qdrant), not
a second check to bolt onto a query already governed by it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.engine import CursorResult, Row
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_kernel.errors import ConflictError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.pagination import CursorPosition, Page, PageRequest, build_page
from dw_platform.adapters.persistence.keyset import after_position, newest_first
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.adapters.persistence import tables
from dw_supply_chain.application.ports import PO_REFERENCE_PADDING, POCaseListFilter
from dw_supply_chain.domain.po_case import (
    TERMINAL_STATES,
    CaseState,
    CaseTransition,
    POCase,
    POCaseId,
)


def _case_from_row(row: Row[tuple]) -> POCase:  # type: ignore[type-arg]
    return POCase(
        id=POCaseId(row.id),
        tenant_id=TenantId(row.tenant_id),
        workspace_id=WorkspaceId(row.workspace_id),
        po_reference=row.po_reference,
        supplier_name=row.supplier_name,
        state=CaseState(row.state),
        interrupted_state=CaseState(row.interrupted_state) if row.interrupted_state else None,
        created_at=row.created_at,
        version=row.version,
    )


def _transition_from_row(row: Row[tuple]) -> CaseTransition:  # type: ignore[type-arg]
    return CaseTransition(
        from_state=CaseState(row.from_state),
        to_state=CaseState(row.to_state),
        reason=row.reason,
        occurred_at=row.occurred_at,
    )


def _is_active(state: sa.ColumnElement[str]) -> sa.ColumnElement[bool]:
    """Not in a terminal state — the one SQL spelling of "active", shared by
    `list_active` (what the Control Tower counts) and `list_page`'s
    `active_only` (its drill-down), so the count and the list it links to
    cannot come apart."""
    return state.notin_([terminal.value for terminal in TERMINAL_STATES])


def _case_position(case: POCase) -> CursorPosition:
    # `created_at` is None only on an aggregate never inserted yet — the
    # column default fills it, and `list_page` only ever sees rows read back.
    assert case.created_at is not None
    return CursorPosition(sort_value=case.created_at, tiebreaker=case.id.value)


@dataclass(frozen=True)
class SqlPOCaseRepository:
    """Implements `POCaseRepositoryPort`."""

    session_factory: async_sessionmaker[AsyncSession]

    async def _insert_pending_transitions(self, session: AsyncSession, case: POCase) -> None:
        """Drains `case`'s own pending-transition queue into `po_case_
        state_transitions`, in the same session/transaction as the state
        write that triggered them — a caller cannot see the state change
        without also being able to see why, and a rollback of one rolls
        back the other. Row ids are generated here, not via an injected
        `IdGenerator`: this table's own identity is never exposed to a
        caller, same precedent `SqlMembershipAdminRepository.record_grant`
        already sets for a housekeeping row nobody looks up by id."""
        transitions = case.pop_pending_transitions()
        if not transitions:
            return
        await session.execute(
            sa.insert(tables.po_case_state_transitions),
            [
                {
                    "id": uuid.uuid4(),
                    "tenant_id": case.tenant_id.value,
                    "workspace_id": case.workspace_id.value,
                    "po_case_id": case.id.value,
                    "from_state": from_state.value,
                    "to_state": to_state.value,
                    "reason": reason,
                }
                for from_state, to_state, reason in transitions
            ],
        )

    async def add(self, context: AccessContext, case: POCase) -> None:
        scope = TenantScope.from_access_context(context)
        async with tenant_session(self.session_factory, scope) as session:
            await session.execute(
                sa.insert(tables.po_cases).values(
                    id=case.id.value,
                    tenant_id=case.tenant_id.value,
                    workspace_id=case.workspace_id.value,
                    po_reference=case.po_reference,
                    supplier_name=case.supplier_name,
                    state=case.state.value,
                    interrupted_state=(
                        case.interrupted_state.value if case.interrupted_state else None
                    ),
                    version=case.version,
                )
            )
            await self._insert_pending_transitions(session, case)

    async def get(self, context: AccessContext, case_id: POCaseId) -> POCase | None:
        scope = TenantScope.from_access_context(context)
        async with tenant_session(self.session_factory, scope) as session:
            result = await session.execute(
                sa.select(tables.po_cases).where(tables.po_cases.c.id == case_id.value)
            )
            row = result.first()
            return _case_from_row(row) if row else None

    async def save(self, context: AccessContext, case: POCase) -> None:
        """Persists the aggregate's current state.

        `WHERE version = case.version - 1` is the optimistic-concurrency
        check: the in-memory aggregate's guarded methods already bumped
        `version` by exactly one, so the row this write may touch is the one
        it was read from, not one someone else already moved on.
        `rowcount != 1` collapses "no such row" and "someone else already
        saved a newer version" into the same refusal — the caller cannot
        tell which, which is what stops a retry loop from being written to
        paper over a genuine conflict.
        """
        scope = TenantScope.from_access_context(context)
        async with tenant_session(self.session_factory, scope) as session:
            result = await session.execute(
                sa.update(tables.po_cases)
                .where(
                    tables.po_cases.c.id == case.id.value,
                    tables.po_cases.c.version == case.version - 1,
                )
                .values(
                    po_reference=case.po_reference,
                    supplier_name=case.supplier_name,
                    state=case.state.value,
                    interrupted_state=(
                        case.interrupted_state.value if case.interrupted_state else None
                    ),
                    version=case.version,
                )
            )
            assert isinstance(result, CursorResult)
            if result.rowcount != 1:
                raise ConflictError(
                    "PO case was modified concurrently",
                    details={"case_id": str(case.id)},
                )
            await self._insert_pending_transitions(session, case)

    async def get_current_state_entered_at(
        self, context: AccessContext, case_id: POCaseId
    ) -> datetime | None:
        scope = TenantScope.from_access_context(context)
        async with tenant_session(self.session_factory, scope) as session:
            result = await session.execute(
                sa.select(tables.po_case_state_transitions.c.occurred_at)
                .where(tables.po_case_state_transitions.c.po_case_id == case_id.value)
                .order_by(tables.po_case_state_transitions.c.occurred_at.desc())
                .limit(1)
            )
            row = result.first()
            return row.occurred_at if row else None

    async def list_page(
        self, context: AccessContext, request: PageRequest, case_filter: POCaseListFilter
    ) -> Page[POCase]:
        scope = TenantScope.from_access_context(context)
        columns = tables.po_cases.c
        query = sa.select(tables.po_cases).where(
            after_position(columns.created_at, columns.id, request.after)
        )
        if case_filter.state is not None:
            query = query.where(columns.state == case_filter.state.value)
        if case_filter.supplier_name is not None:
            query = query.where(columns.supplier_name == case_filter.supplier_name)
        if case_filter.active_only:
            query = query.where(_is_active(columns.state))
        async with tenant_session(self.session_factory, scope) as session:
            result = await session.execute(
                query.order_by(*newest_first(columns.created_at, columns.id)).limit(
                    request.fetch_limit
                )
            )
            rows = result.all()
        return build_page(
            [_case_from_row(row) for row in rows], request=request, position_of=_case_position
        )

    async def list_transitions(
        self, context: AccessContext, case_id: POCaseId
    ) -> list[CaseTransition]:
        scope = TenantScope.from_access_context(context)
        async with tenant_session(self.session_factory, scope) as session:
            result = await session.execute(
                sa.select(
                    tables.po_case_state_transitions.c.from_state,
                    tables.po_case_state_transitions.c.to_state,
                    tables.po_case_state_transitions.c.reason,
                    tables.po_case_state_transitions.c.occurred_at,
                )
                .where(tables.po_case_state_transitions.c.po_case_id == case_id.value)
                .order_by(tables.po_case_state_transitions.c.occurred_at.asc())
            )
            return [_transition_from_row(row) for row in result]

    async def list_active(self, context: AccessContext) -> list[POCase]:
        scope = TenantScope.from_access_context(context)
        async with tenant_session(self.session_factory, scope) as session:
            result = await session.execute(
                sa.select(tables.po_cases)
                .where(_is_active(tables.po_cases.c.state))
                .order_by(tables.po_cases.c.created_at.asc())
            )
            return [_case_from_row(row) for row in result]

    async def list_supplier_names(self, context: AccessContext) -> list[str]:
        scope = TenantScope.from_access_context(context)
        async with tenant_session(self.session_factory, scope) as session:
            result = await session.execute(
                sa.select(tables.po_cases.c.supplier_name)
                .distinct()
                .order_by(tables.po_cases.c.supplier_name)
            )
            return list(result.scalars())

    async def find_by_reference(self, context: AccessContext, po_reference: str) -> list[POCase]:
        scope = TenantScope.from_access_context(context)
        async with tenant_session(self.session_factory, scope) as session:
            result = await session.execute(
                sa.select(tables.po_cases)
                .where(
                    sa.func.lower(
                        sa.func.btrim(tables.po_cases.c.po_reference, PO_REFERENCE_PADDING)
                    )
                    == po_reference.strip(PO_REFERENCE_PADDING).lower()
                )
                .order_by(tables.po_cases.c.po_reference)
            )
            return [_case_from_row(row) for row in result]

    async def bulk_current_state_entered_at(
        self, context: AccessContext, case_ids: list[POCaseId]
    ) -> dict[uuid.UUID, datetime]:
        if not case_ids:
            return {}
        scope = TenantScope.from_access_context(context)
        ids = [case_id.value for case_id in case_ids]
        async with tenant_session(self.session_factory, scope) as session:
            result = await session.execute(
                sa.select(
                    tables.po_case_state_transitions.c.po_case_id,
                    sa.func.max(tables.po_case_state_transitions.c.occurred_at).label(
                        "occurred_at"
                    ),
                )
                .where(tables.po_case_state_transitions.c.po_case_id.in_(ids))
                .group_by(tables.po_case_state_transitions.c.po_case_id)
            )
            return {row.po_case_id: row.occurred_at for row in result}

    async def get_many(self, context: AccessContext, case_ids: list[POCaseId]) -> list[POCase]:
        if not case_ids:
            return []
        scope = TenantScope.from_access_context(context)
        ids = [case_id.value for case_id in case_ids]
        async with tenant_session(self.session_factory, scope) as session:
            result = await session.execute(
                sa.select(tables.po_cases).where(tables.po_cases.c.id.in_(ids))
            )
            return [_case_from_row(row) for row in result]

    async def list_latest_transitions_since(
        self, context: AccessContext, since: datetime
    ) -> list[tuple[POCaseId, CaseTransition]]:
        scope = TenantScope.from_access_context(context)
        transitions = tables.po_case_state_transitions
        async with tenant_session(self.session_factory, scope) as session:
            # `ix_po_case_state_transitions_tenant_occurred_at` carries the
            # window: RLS supplies tenant_id, the range bounds occurred_at.
            result = await session.execute(
                sa.select(
                    transitions.c.po_case_id,
                    transitions.c.from_state,
                    transitions.c.to_state,
                    transitions.c.reason,
                    transitions.c.occurred_at,
                )
                .distinct(transitions.c.po_case_id)
                .where(transitions.c.occurred_at >= since)
                .order_by(
                    transitions.c.po_case_id,
                    transitions.c.occurred_at.desc(),
                    transitions.c.id.desc(),
                )
            )
            return [(POCaseId(row.po_case_id), _transition_from_row(row)) for row in result]
