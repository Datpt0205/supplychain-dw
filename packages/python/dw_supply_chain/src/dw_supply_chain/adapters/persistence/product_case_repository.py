"""SQL persistence for product-development cases (stage-1 ticket 01).

Every statement runs under `tenant_session`, which binds the context's tenant
AND workspace: the four tables' policies narrow by both, so another
workspace's case reads as absent exactly as another tenant's does. The
statements also name the tenant and workspace themselves, a second layer
where RLS is the first, as `SqlCaseDocumentRepository` does.

One transaction per command: the case row, one history row per pending step,
the round a step opens (INSERT) or closes (a guarded UPDATE that touches only
a still-open round), the revision request a step records, and the audit
event. A refusal from the database comes back as a `ConflictError` by the
constraint's name, never by parsing its message.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.engine import CursorResult, Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_kernel.errors import ConflictError, DWError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.pagination import CursorPosition, Page, PageRequest, build_page
from dw_platform.adapters.persistence.keyset import after_position, newest_first
from dw_platform.adapters.persistence.repositories import SqlAuditRepository
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence import tables
from dw_supply_chain.application.ports import ProductCaseListFilter
from dw_supply_chain.domain.product_development_case import (
    ProductAction,
    ProductCaseStep,
    ProductCaseTransition,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
    SampleResult,
    SampleRound,
    document_refusal,
)
from dw_supply_chain.domain.product_proposal import DraftClaim, ProposalDraftChangedError

_c = tables.product_dev_cases
_t = tables.product_dev_case_state_transitions
_r = tables.product_sample_rounds
_q = tables.sample_revision_requests

PROPOSAL_CODE_CONSTRAINT = "uq_product_dev_cases_tenant_id_proposal_code"
# The database's own refusal of a step's paper, should one reach it past the
# domain's check. The refusal names the step that was being written (a
# `reject_sample` evaluation meets the same constraints as a `pass_sample` one).
_DOCUMENT_CONSTRAINTS = frozenset(
    {
        "fk_product_sample_rounds_tenant_id_case_documents",
        "uq_product_sample_rounds_evaluation_document_id",
        "ck_product_sample_rounds_passed_on_an_evaluation",
        "fk_sample_revision_requests_tenant_id_case_documents",
        "uq_sample_revision_requests_revision_document_id",
    }
)


def _constraint(exc: IntegrityError) -> str | None:
    # asyncpg's own exception carries the name; SQLAlchemy wraps it as the
    # DBAPI error's cause (as `dw_platform`'s separation-of-duties adapter reads it).
    name = getattr(getattr(exc.orig, "__cause__", None), "constraint_name", None)
    return name if isinstance(name, str) else None


async def _consume_draft(session: AsyncSession, context: AccessContext, claim: DraftClaim) -> None:
    """Delete the caller's draft at exactly the summarised version, unexpired;
    refuse (rolling the case back with it) when there is no such row."""
    _d = tables.proposal_drafts
    result = await session.execute(
        sa.delete(_d).where(
            *_in_scope(_d, context),
            _d.c.user_id == context.principal_id,
            _d.c.id == claim.draft_id,
            _d.c.draft_version == claim.draft_version,
            _d.c.summarized_version == claim.draft_version,
            _d.c.expires_at > sa.func.now(),
        )
    )
    assert isinstance(result, CursorResult)
    if result.rowcount != 1:
        raise ProposalDraftChangedError(
            "bản nháp đề xuất đã đổi, hết hạn hoặc đã được dùng",
            details={"draft_id": str(claim.draft_id)},
        )


def _in_scope(table: sa.Table, context: AccessContext) -> tuple[sa.ColumnElement[bool], ...]:
    return (
        table.c.tenant_id == context.tenant_id,
        table.c.workspace_id == context.workspace_id,
    )


def _case(row: Row[tuple[object, ...]]) -> ProductDevelopmentCase:
    m = row._mapping
    interrupted = m[_c.c.interrupted_state]
    return ProductDevelopmentCase(
        id=ProductDevelopmentCaseId(m[_c.c.id]),
        tenant_id=TenantId(m[_c.c.tenant_id]),
        workspace_id=WorkspaceId(m[_c.c.workspace_id]),
        proposal_code=m[_c.c.proposal_code],
        product_name=m[_c.c.product_name],
        category=m[_c.c.category],
        supplier_name=m[_c.c.supplier_name],
        pic_user_id=m[_c.c.pic_user_id],
        created_by=m[_c.c.created_by],
        state=ProductDevState(m[_c.c.state]),
        interrupted_state=ProductDevState(interrupted) if interrupted else None,
        sample_round=m[_c.c.sample_round],
        round_opened_at=m.get("round_opened_at"),
        version=m[_c.c.version],
        created_at=m[_c.c.created_at],
    )


def _with_current_round() -> sa.Select[tuple[object, ...]]:
    """The case with its current round's opening time: what the domain
    compares a document's upload time against."""
    return sa.select(_c, _r.c.opened_at.label("round_opened_at")).select_from(
        _c.outerjoin(
            _r,
            sa.and_(
                _r.c.tenant_id == _c.c.tenant_id,
                _r.c.product_dev_case_id == _c.c.id,
                _r.c.round_no == _c.c.sample_round,
            ),
        )
    )


def _position(case: ProductDevelopmentCase) -> CursorPosition:
    assert case.created_at is not None  # read back from a row, never unsaved
    return CursorPosition(sort_value=case.created_at, tiebreaker=case.id.value)


@dataclass(frozen=True)
class SqlProductCaseRepository:
    """Implements `ProductCaseRepositoryPort`, the review graph's
    `ProductCaseReviewPort`, and `case_documents`' `CaseLookupPort` for the
    product kind."""

    session_factory: async_sessionmaker[AsyncSession]

    async def _write_steps(
        self,
        session: AsyncSession,
        context: AccessContext,
        case: ProductDevelopmentCase,
        steps: list[ProductCaseStep],
    ) -> None:
        for step in steps:
            await self._write_step(session, context, case, step)

    async def _write_step(
        self,
        session: AsyncSession,
        context: AccessContext,
        case: ProductDevelopmentCase,
        step: ProductCaseStep,
    ) -> None:
        owner = {
            "tenant_id": context.tenant_id,
            "workspace_id": context.workspace_id,
            "product_dev_case_id": case.id.value,
        }
        await session.execute(
            sa.insert(_t).values(
                id=uuid.uuid4(),
                **owner,
                action=step.action.value,
                from_state=step.from_state.value if step.from_state else None,
                to_state=step.to_state.value,
                reason=step.reason,
                actor_id=step.actor_id,
            )
        )
        if step.opens_round is not None:
            await session.execute(
                sa.insert(_r).values(
                    id=uuid.uuid4(), **owner, round_no=step.opens_round, opened_by=step.actor_id
                )
            )
        closure = step.closes_round
        if closure is None:
            return
        closed = await session.execute(
            sa.update(_r)
            .where(
                *_in_scope(_r, context),
                _r.c.product_dev_case_id == case.id.value,
                _r.c.round_no == closure.round_no,
                _r.c.result.is_(None),
            )
            .values(
                result=closure.result.value,
                evaluation_document_id=closure.evaluation_document_id,
                closed_at=sa.func.now(),
                closed_by=step.actor_id,
            )
        )
        assert isinstance(closed, CursorResult)
        if closed.rowcount != 1:
            raise ConflictError(
                "this sample round is already closed",
                details={"case_id": str(case.id), "round_no": closure.round_no},
            )
        if closure.revision_document_id is not None:
            assert step.reason is not None  # a revision always carries what to change
            await session.execute(
                sa.insert(_q).values(
                    id=uuid.uuid4(),
                    **owner,
                    round_no=closure.round_no,
                    revision_document_id=closure.revision_document_id,
                    requested_changes=step.reason,
                    sent_by=step.actor_id,
                )
            )

    def _refusal(
        self, exc: IntegrityError, case: ProductDevelopmentCase, steps: list[ProductCaseStep]
    ) -> DWError | None:
        name = _constraint(exc)
        if name == PROPOSAL_CODE_CONSTRAINT:
            return ConflictError(
                "mã đề xuất này đã có trong tenant",
                details={"constraint": name, "proposal_code": case.proposal_code},
            )
        if name in _DOCUMENT_CONSTRAINTS and steps:
            # A command takes one step; the last is the one whose paper it was.
            error = document_refusal(case.id.value, steps[-1].action)
            error.details["constraint"] = name
            return error
        return None

    async def add(
        self,
        context: AccessContext,
        case: ProductDevelopmentCase,
        *,
        audit: AuditEvent,
        consume: DraftClaim | None = None,
    ) -> None:
        steps = case.pop_pending_steps()
        try:
            async with tenant_session(
                self.session_factory, TenantScope.from_access_context(context)
            ) as session:
                if consume is not None:
                    # First, so a second "Đồng ý" racing this one waits on the
                    # row lock and then finds nothing to consume.
                    await _consume_draft(session, context, consume)
                row = (
                    await session.execute(
                        sa.insert(_c)
                        .values(
                            id=case.id.value,
                            tenant_id=context.tenant_id,
                            workspace_id=context.workspace_id,
                            proposal_code=case.proposal_code,
                            product_name=case.product_name,
                            category=case.category,
                            supplier_name=case.supplier_name,
                            pic_user_id=case.pic_user_id,
                            state=case.state.value,
                            sample_round=case.sample_round,
                            version=case.version,
                            created_by=case.created_by,
                        )
                        .returning(_c.c.created_at)
                    )
                ).one()
                await self._write_steps(session, context, case, steps)
                await SqlAuditRepository(session).append(audit)
        except IntegrityError as exc:
            refusal = self._refusal(exc, case, steps)
            if refusal is None:
                raise
            raise refusal from exc
        case.created_at = row.created_at

    async def get(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> ProductDevelopmentCase | None:
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            row = (
                await session.execute(
                    _with_current_round().where(*_in_scope(_c, context), _c.c.id == case_id.value)
                )
            ).first()
        return None if row is None else _case(row)

    async def case_workspace(self, context: AccessContext, case_id: uuid.UUID) -> uuid.UUID | None:
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            found: uuid.UUID | None = await session.scalar(
                sa.select(_c.c.workspace_id).where(*_in_scope(_c, context), _c.c.id == case_id)
            )
        return found

    async def save(
        self, context: AccessContext, case: ProductDevelopmentCase, *, audit: AuditEvent
    ) -> None:
        """`WHERE version = case.version - 1`: the steps a command takes bump
        `version` once each and a command takes one, so the row written is the
        one read. Another writer in between is a refusal, not a merge."""
        steps = case.pop_pending_steps()
        try:
            async with tenant_session(
                self.session_factory, TenantScope.from_access_context(context)
            ) as session:
                result = await session.execute(
                    sa.update(_c)
                    .where(
                        *_in_scope(_c, context),
                        _c.c.id == case.id.value,
                        _c.c.version == case.version - 1,
                    )
                    .values(
                        supplier_name=case.supplier_name,
                        state=case.state.value,
                        interrupted_state=(
                            case.interrupted_state.value if case.interrupted_state else None
                        ),
                        sample_round=case.sample_round,
                        version=case.version,
                    )
                )
                assert isinstance(result, CursorResult)
                if result.rowcount != 1:
                    raise ConflictError(
                        "product case was modified concurrently",
                        details={"case_id": str(case.id)},
                    )
                await self._write_steps(session, context, case, steps)
                await SqlAuditRepository(session).append(audit)
        except IntegrityError as exc:
            refusal = self._refusal(exc, case, steps)
            if refusal is None:
                raise
            raise refusal from exc

    async def append_audit(self, context: AccessContext, audit: AuditEvent) -> None:
        """An audit event about a case that changes nothing on it: the review
        graph recording that BGĐ's decision found the case moved on."""
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            await SqlAuditRepository(session).append(audit)

    async def list_page(
        self, context: AccessContext, request: PageRequest, case_filter: ProductCaseListFilter
    ) -> Page[ProductDevelopmentCase]:
        query = _with_current_round().where(
            *_in_scope(_c, context), after_position(_c.c.created_at, _c.c.id, request.after)
        )
        if case_filter.state is not None:
            query = query.where(_c.c.state == case_filter.state.value)
        if case_filter.pic_user_id is not None:
            query = query.where(_c.c.pic_user_id == case_filter.pic_user_id)
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            rows = (
                await session.execute(
                    query.order_by(*newest_first(_c.c.created_at, _c.c.id)).limit(
                        request.fetch_limit
                    )
                )
            ).all()
        return build_page([_case(row) for row in rows], request=request, position_of=_position)

    async def list_transitions(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> list[ProductCaseTransition]:
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            rows = (
                await session.execute(
                    sa.select(_t)
                    .where(*_in_scope(_t, context), _t.c.product_dev_case_id == case_id.value)
                    .order_by(_t.c.occurred_at.asc(), _t.c.id.asc())
                )
            ).all()
        return [
            ProductCaseTransition(
                action=ProductAction(row.action),
                from_state=ProductDevState(row.from_state) if row.from_state else None,
                to_state=ProductDevState(row.to_state),
                reason=row.reason,
                actor_id=row.actor_id,
                occurred_at=row.occurred_at,
            )
            for row in rows
        ]

    async def list_rounds(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> list[SampleRound]:
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            rows = (
                await session.execute(
                    sa.select(_r, _q.c.revision_document_id, _q.c.requested_changes)
                    .select_from(
                        _r.outerjoin(
                            _q,
                            sa.and_(
                                _q.c.tenant_id == _r.c.tenant_id,
                                _q.c.product_dev_case_id == _r.c.product_dev_case_id,
                                _q.c.round_no == _r.c.round_no,
                            ),
                        )
                    )
                    .where(*_in_scope(_r, context), _r.c.product_dev_case_id == case_id.value)
                    .order_by(_r.c.round_no.asc())
                )
            ).all()
        return [
            SampleRound(
                round_no=row.round_no,
                opened_at=row.opened_at,
                opened_by=row.opened_by,
                result=SampleResult(row.result) if row.result else None,
                evaluation_document_id=row.evaluation_document_id,
                closed_at=row.closed_at,
                closed_by=row.closed_by,
                revision_document_id=row.revision_document_id,
                requested_changes=row.requested_changes,
            )
            for row in rows
        ]


@dataclass(frozen=True)
class SqlWorkspacesAwaitingReview:
    """Implements `WorkspacesAwaitingReviewPort` through
    `supply_chain.workspaces_awaiting_bod_review()`, the one SECURITY DEFINER
    read that crosses tenants for the reconcile lane. Ids only; every read
    after it runs under that tenant's and workspace's RLS."""

    session_factory: async_sessionmaker[AsyncSession]

    async def awaiting_bod_review(self) -> list[tuple[uuid.UUID, uuid.UUID]]:
        async with self.session_factory() as session, session.begin():
            rows = (
                await session.execute(
                    sa.text(
                        "SELECT tenant_id, workspace_id"
                        " FROM supply_chain.workspaces_awaiting_bod_review()"
                    )
                )
            ).all()
        return [(row.tenant_id, row.workspace_id) for row in rows]
