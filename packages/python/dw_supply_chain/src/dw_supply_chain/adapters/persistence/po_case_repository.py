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

A case ĐẶT HÀNG opened carries planned lines (`po_case_lines`, tenant AND
workspace scoped); `get` reads them back with each SKU's code and label, the
lists do not. `insert_po_case` is the one INSERT of a case and its lines,
shared with the product repository, which writes the case in ĐẶT HÀNG's own
transaction. The PIC and Category are the stamped columns, never joined from
the product case (ADR 0017; `test_no_po_case_read_joins_the_product_case`).
A PO reference already taken in the tenant is a `ConflictError` by the
constraint's name.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.engine import CursorResult, Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_kernel.errors import ConflictError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.pagination import CursorPosition, Page, PageRequest, build_page
from dw_platform.adapters.persistence.keyset import after_position, newest_first
from dw_platform.adapters.persistence.repositories import SqlAuditRepository
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence import tables
from dw_supply_chain.application.ports import PO_REFERENCE_PADDING, POCaseListFilter
from dw_supply_chain.domain.po_case import (
    TERMINAL_STATES,
    CaseState,
    CaseTransition,
    OrderKind,
    POCase,
    POCaseId,
    POCaseLine,
)
from dw_supply_chain.domain.sla_evaluation import SLA_CLOCK_STARTS_IN

_lines = tables.po_case_lines
PO_REFERENCE_CONSTRAINT = "uq_po_cases_tenant_id_po_reference"


def _case_from_row(
    row: Row[tuple],  # type: ignore[type-arg]
    lines: tuple[POCaseLine, ...] = (),
) -> POCase:
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
        order_kind=OrderKind(row.order_kind),
        product_dev_case_id=row.product_dev_case_id,
        pic_user_id=row.pic_user_id,
        category=row.category,
        lines=lines,
    )


def _constraint(exc: IntegrityError) -> str | None:
    name = getattr(getattr(exc.orig, "__cause__", None), "constraint_name", None)
    return name if isinstance(name, str) else None


def reference_refusal(exc: IntegrityError, case: POCase) -> ConflictError | None:
    """A PO reference already taken in the tenant, named; None for any other
    refusal, which the caller re-raises."""
    if _constraint(exc) != PO_REFERENCE_CONSTRAINT:
        return None
    return ConflictError(
        f"số PO {case.po_reference} đã có trong công ty",
        details={"constraint": PO_REFERENCE_CONSTRAINT, "po_reference": case.po_reference or ""},
    )


async def insert_po_case(session: AsyncSession, case: POCase) -> datetime:
    """The case's row and its lines, in the caller's transaction; returns when
    the row was written."""
    created_at: datetime = (
        await session.execute(
            sa.insert(tables.po_cases)
            .values(
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
                order_kind=case.order_kind.value,
                product_dev_case_id=case.product_dev_case_id,
                pic_user_id=case.pic_user_id,
                category=case.category,
            )
            .returning(tables.po_cases.c.created_at)
        )
    ).scalar_one()
    if case.lines:
        await session.execute(
            sa.insert(_lines),
            [
                {
                    "id": uuid.uuid4(),
                    "tenant_id": case.tenant_id.value,
                    "workspace_id": case.workspace_id.value,
                    "po_case_id": case.id.value,
                    "sku_id": line.sku_id,
                    "quantity": line.quantity,
                }
                for line in case.lines
            ],
        )
    return created_at


async def _lines_of(session: AsyncSession, case_id: POCaseId) -> tuple[POCaseLine, ...]:
    """The case's lines, in the order they were written, each with its SKU's
    code and label. RLS narrows both tables to the caller's workspace."""
    skus = tables.skus
    rows = (
        await session.execute(
            sa.select(_lines.c.sku_id, _lines.c.quantity, skus.c.sku_code, skus.c.variant_label)
            .select_from(
                _lines.outerjoin(
                    skus,
                    sa.and_(skus.c.tenant_id == _lines.c.tenant_id, skus.c.id == _lines.c.sku_id),
                )
            )
            .where(_lines.c.po_case_id == case_id.value)
            .order_by(_lines.c.created_at.asc(), skus.c.sku_code.asc())
        )
    ).all()
    return tuple(
        POCaseLine(
            sku_id=row.sku_id,
            quantity=row.quantity,
            sku_code=row.sku_code,
            variant_label=row.variant_label,
        )
        for row in rows
    )


def _transition_from_row(row: Row[tuple]) -> CaseTransition:  # type: ignore[type-arg]
    return CaseTransition(
        from_state=CaseState(row.from_state),
        to_state=CaseState(row.to_state),
        reason=row.reason,
        occurred_at=row.occurred_at,
    )


def _is_active(state: sa.ColumnElement[str]) -> sa.ColumnElement[bool]:
    """Not in a terminal state — the one SQL spelling of "active": `list_page`'s
    `active_only`, which both the Control Tower's count (read a page at a
    time by `handlers.assess_active_cases`) and its drill-down use, so the
    count and the list it links to cannot come apart."""
    return state.notin_([terminal.value for terminal in TERMINAL_STATES])


def _clock_start_state(state: sa.ColumnElement[str]) -> sa.ColumnElement[str]:
    """`sla_clock_start_state`, in SQL: built from the domain's own table, so
    the two cannot name different states."""
    if not SLA_CLOCK_STARTS_IN:
        return state
    return sa.case(
        {current.value: start.value for current, start in SLA_CLOCK_STARTS_IN.items()},
        value=state,
        else_=state,
    )


def _row_position(row: Row[tuple]) -> CursorPosition:  # type: ignore[type-arg]
    return CursorPosition(sort_value=row.occurred_at, tiebreaker=row.id)


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

    async def add(
        self, context: AccessContext, case: POCase, *, audit: AuditEvent | None = None
    ) -> None:
        """The case, its first transitions and `audit` in one transaction.
        `audit` is optional here only for fixtures that seed a case; every
        command goes through `POCaseRepositoryPort`, which requires it."""
        scope = TenantScope.from_access_context(context)
        try:
            async with tenant_session(self.session_factory, scope) as session:
                created_at = await insert_po_case(session, case)
                await self._insert_pending_transitions(session, case)
                if audit is not None:
                    await SqlAuditRepository(session).append(audit)
        except IntegrityError as exc:
            refusal = reference_refusal(exc, case)
            if refusal is None:
                raise
            raise refusal from exc
        case.created_at = created_at

    async def get(self, context: AccessContext, case_id: POCaseId) -> POCase | None:
        scope = TenantScope.from_access_context(context)
        async with tenant_session(self.session_factory, scope) as session:
            result = await session.execute(
                sa.select(tables.po_cases).where(tables.po_cases.c.id == case_id.value)
            )
            row = result.first()
            if row is None:
                return None
            return _case_from_row(row, await _lines_of(session, case_id))

    async def case_workspace(self, context: AccessContext, case_id: uuid.UUID) -> uuid.UUID | None:
        """The workspace of the caller's tenant's case, or None: what
        `case_documents`' `CaseLookupPort` asks of the PO kind."""
        scope = TenantScope.from_access_context(context)
        async with tenant_session(self.session_factory, scope) as session:
            found: uuid.UUID | None = await session.scalar(
                sa.select(tables.po_cases.c.workspace_id).where(tables.po_cases.c.id == case_id)
            )
        return found

    async def save(
        self, context: AccessContext, case: POCase, *, audit: AuditEvent | None = None
    ) -> None:
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
        quantities = case.pop_pending_line_quantities()
        try:
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
                        order_kind=case.order_kind.value,
                        pic_user_id=case.pic_user_id,
                    )
                )
                assert isinstance(result, CursorResult)
                if result.rowcount != 1:
                    raise ConflictError(
                        "PO case was modified concurrently",
                        details={"case_id": str(case.id)},
                    )
                for sku_id, quantity in quantities.items():
                    await session.execute(
                        sa.update(_lines)
                        .where(_lines.c.po_case_id == case.id.value, _lines.c.sku_id == sku_id)
                        .values(quantity=quantity)
                    )
                await self._insert_pending_transitions(session, case)
                if audit is not None:
                    await SqlAuditRepository(session).append(audit)
        except IntegrityError as exc:
            refusal = reference_refusal(exc, case)
            if refusal is None:
                raise
            raise refusal from exc

    async def get_sla_clock_started_at(
        self, context: AccessContext, case_id: POCaseId
    ) -> datetime | None:
        found = await self.bulk_sla_clock_started_at(context, [case_id])
        return found.get(case_id.value)

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
        self, context: AccessContext, case_id: POCaseId, request: PageRequest
    ) -> Page[CaseTransition]:
        """Newest first, by `ix_po_case_state_transitions_case_page` (tenant,
        workspace, case, occurred_at, id): RLS supplies the first two."""
        transitions = tables.po_case_state_transitions
        scope = TenantScope.from_access_context(context)
        async with tenant_session(self.session_factory, scope) as session:
            rows = (
                await session.execute(
                    sa.select(
                        transitions.c.id,
                        transitions.c.from_state,
                        transitions.c.to_state,
                        transitions.c.reason,
                        transitions.c.occurred_at,
                    )
                    .where(
                        transitions.c.po_case_id == case_id.value,
                        after_position(transitions.c.occurred_at, transitions.c.id, request.after),
                    )
                    .order_by(*newest_first(transitions.c.occurred_at, transitions.c.id))
                    .limit(request.fetch_limit)
                )
            ).all()
        return build_page(rows, request=request, position_of=_row_position).map_items(
            _transition_from_row
        )

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

    async def bulk_sla_clock_started_at(
        self, context: AccessContext, case_ids: list[POCaseId]
    ) -> dict[uuid.UUID, datetime]:
        """The latest entry of each case into the state its SLA clock starts
        in (`sla_clock_start_state` of its current state, as SQL)."""
        if not case_ids:
            return {}
        scope = TenantScope.from_access_context(context)
        ids = [case_id.value for case_id in case_ids]
        transitions, cases = tables.po_case_state_transitions, tables.po_cases
        async with tenant_session(self.session_factory, scope) as session:
            result = await session.execute(
                sa.select(
                    transitions.c.po_case_id,
                    sa.func.max(transitions.c.occurred_at).label("occurred_at"),
                )
                .select_from(
                    transitions.join(
                        cases,
                        sa.and_(
                            cases.c.tenant_id == transitions.c.tenant_id,
                            cases.c.workspace_id == transitions.c.workspace_id,
                            cases.c.id == transitions.c.po_case_id,
                        ),
                    )
                )
                .where(
                    transitions.c.po_case_id.in_(ids),
                    transitions.c.to_state == _clock_start_state(cases.c.state),
                )
                .group_by(transitions.c.po_case_id)
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
        self, context: AccessContext, since: datetime, *, limit: int
    ) -> tuple[int, list[tuple[POCaseId, CaseTransition]]]:
        scope = TenantScope.from_access_context(context)
        transitions = tables.po_case_state_transitions
        # `ix_po_case_state_transitions_tenant_occurred_at` carries the
        # window: RLS supplies tenant_id and workspace_id, the range bounds
        # occurred_at. Each moved case once, at its latest transition.
        latest = (
            sa.select(
                transitions.c.po_case_id,
                transitions.c.from_state,
                transitions.c.to_state,
                transitions.c.reason,
                transitions.c.occurred_at,
                transitions.c.id,
            )
            .distinct(transitions.c.po_case_id)
            .where(transitions.c.occurred_at >= since)
            .order_by(
                transitions.c.po_case_id,
                transitions.c.occurred_at.desc(),
                transitions.c.id.desc(),
            )
            .subquery()
        )
        async with tenant_session(self.session_factory, scope) as session:
            total = (
                await session.execute(sa.select(sa.func.count()).select_from(latest))
            ).scalar_one()
            rows = (
                await session.execute(
                    sa.select(latest)
                    .order_by(latest.c.occurred_at.desc(), latest.c.id.desc())
                    .limit(limit)
                )
            ).all()
        return total, [(POCaseId(row.po_case_id), _transition_from_row(row)) for row in rows]
