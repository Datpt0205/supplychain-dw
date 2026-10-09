"""SQL persistence for product-development cases (stage-1 ticket 01).

Every statement runs under `tenant_session`, which binds the context's tenant
AND workspace: the four tables' policies narrow by both, so another
workspace's case reads as absent exactly as another tenant's does. The
statements also name the tenant and workspace themselves, a second layer
where RLS is the first, as `SqlCaseDocumentRepository` does.

One transaction per command: the case row, one history row per pending step
(with the paper of a step outside the rounds), the round a step opens
(INSERT) or closes (a guarded UPDATE that touches only a still-open round),
the revision request a step records, and the audit event.

Read back with the case: when its current round opened, and when it reached
its current state (`stage_entered_at`, from the history; a resume and a
step that stays where it is do not count), the two bounds the domain checks a
step's paper against; and its item code and SKUs (step 9), so every case
handed out, listed or fetched, carries what its guards read.

A refusal from the database comes back as a `ConflictError` by the
constraint's name, never by parsing its message: a code already taken in the
tenant names the code (ADR 0018), whichever of two racing writers lost.

ĐẶT HÀNG (`place_order`, ADR 0017) is one transaction: the conditional
UPDATE that moves the case out of `ready_to_order` at the version read, its
history row, the PO case and its lines (`insert_po_case`, the PO repository's
one INSERT), and both audit events. The UPDATE is what makes one PO: of two
clicks at once the second waits on the row lock, finds the version moved and
updates nothing, and nothing is inserted.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

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
from dw_supply_chain.adapters.persistence.po_case_repository import insert_po_case
from dw_supply_chain.adapters.persistence.suppliers import ResolvedSupplier, resolve_supplier
from dw_supply_chain.application.ports import ProductCaseListFilter
from dw_supply_chain.domain.daily_brief import ClosedRound
from dw_supply_chain.domain.po_case import POCase
from dw_supply_chain.domain.product_development_case import (
    PRODUCT_TERMINAL_STATES,
    ItemCode,
    ItemCodeIssued,
    ProductAction,
    ProductCaseStep,
    ProductCaseTransition,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
    SampleResult,
    SampleRound,
    Sku,
    SkuAdded,
    SkuRemoved,
    document_refusal,
)
from dw_supply_chain.domain.product_proposal import DraftClaim, ProposalDraftChangedError

_c = tables.product_dev_cases
_t = tables.product_dev_case_state_transitions
_r = tables.product_sample_rounds
_q = tables.sample_revision_requests
_i = tables.item_codes
_s = tables.skus

PROPOSAL_CODE_CONSTRAINT = "uq_product_dev_cases_tenant_id_proposal_code"
# Step 9 (ADR 0018): the database's answer to "is this code free in the tenant".
ITEM_CODE_CONSTRAINT = "uq_item_codes_tenant_id_code"
SKU_CODE_CONSTRAINT = "uq_skus_tenant_id_sku_code"
_ONE_ITEM_CODE_CONSTRAINT = "uq_item_codes_tenant_id_product_dev_case_id"
# One PO case per product case (ADR 0017 amendment, QE-12 open): the
# database's second answer, behind the conditional UPDATE.
ONE_PO_CASE_CONSTRAINT = "uq_po_cases_tenant_id_workspace_id_product_dev_case_id"
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
        "fk_product_dev_case_state_transitions_tenant_id_case_documents",
        "ck_product_dev_case_state_transitions_document_steps",
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
    item_code_id = m.get("item_code_id")
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
        stage_entered_at=m.get("stage_entered_at"),
        item_code=(
            None if item_code_id is None else ItemCode(id=item_code_id, code=m["item_code"])
        ),
        signoff_round=m[_c.c.signoff_round],
        version=m[_c.c.version],
        created_at=m[_c.c.created_at],
    )


def _stage_entered_at() -> sa.ScalarSelect[object]:
    """When the case reached its current state by a step: its latest history
    row into that state that is not a resume (a resume returns to a step
    already reached) nor a step that stayed where it was (step 9's coding).
    Served by the history's (tenant, case, occurred_at) index."""
    return (
        sa.select(sa.func.max(_t.c.occurred_at))
        .where(
            _t.c.tenant_id == _c.c.tenant_id,
            _t.c.product_dev_case_id == _c.c.id,
            _t.c.to_state == _c.c.state,
            _t.c.action != ProductAction.RESUME.value,
            _t.c.from_state.is_distinct_from(_t.c.to_state),
        )
        .scalar_subquery()
    )


def _with_current_round() -> sa.Select[tuple[object, ...]]:
    """The case with its current round's opening time and when it reached its
    current state, what the domain compares a document's upload time
    against, and its item code (one per case, by its UNIQUE)."""
    return sa.select(
        _c,
        _r.c.opened_at.label("round_opened_at"),
        _stage_entered_at().label("stage_entered_at"),
        _i.c.id.label("item_code_id"),
        _i.c.code.label("item_code"),
    ).select_from(
        _c.outerjoin(
            _r,
            sa.and_(
                _r.c.tenant_id == _c.c.tenant_id,
                _r.c.product_dev_case_id == _c.c.id,
                _r.c.round_no == _c.c.sample_round,
            ),
        ).outerjoin(
            _i,
            sa.and_(_i.c.tenant_id == _c.c.tenant_id, _i.c.product_dev_case_id == _c.c.id),
        )
    )


async def _with_skus(
    session: AsyncSession, context: AccessContext, cases: list[ProductDevelopmentCase]
) -> list[ProductDevelopmentCase]:
    """Each case with its SKUs, oldest first, in one read for the lot."""
    if not cases:
        return cases
    rows = (
        await session.execute(
            sa.select(_s)
            .where(
                *_in_scope(_s, context), _s.c.product_dev_case_id.in_([c.id.value for c in cases])
            )
            .order_by(_s.c.added_at.asc(), _s.c.id.asc())
        )
    ).all()
    by_case: dict[uuid.UUID, list[Sku]] = {}
    for row in rows:
        by_case.setdefault(row.product_dev_case_id, []).append(
            Sku(
                id=row.id,
                sku_code=row.sku_code,
                variant_label=row.variant_label,
                planned_quantity=row.planned_quantity,
            )
        )
    for case in cases:
        case.skus = tuple(by_case.get(case.id.value, ()))
    return cases


async def _supplier_of(
    session: AsyncSession, context: AccessContext, case: ProductDevelopmentCase
) -> ResolvedSupplier | None:
    """The case's supplier as the workspace's master record (step 2 names it;
    None before), the case taking its stored name."""
    if case.supplier_name is None:
        return None
    supplier = await resolve_supplier(session, context, context.workspace_id, case.supplier_name)
    case.supplier_name = supplier.name
    return supplier


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
                document_id=step.document_id,
            )
        )
        if step.coding is not None:
            await self._write_coding(session, context, case, step)
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

    async def _write_coding(
        self,
        session: AsyncSession,
        context: AccessContext,
        case: ProductDevelopmentCase,
        step: ProductCaseStep,
    ) -> None:
        """Step 9's change to the item code or the SKUs. Uniqueness is the
        database's: a taken code fails here and `_refusal` names it."""
        change = step.coding
        scope = {"tenant_id": context.tenant_id, "workspace_id": context.workspace_id}
        if isinstance(change, ItemCodeIssued):
            item = change.item_code
            if change.replaces:
                await session.execute(
                    sa.update(_i)
                    .where(*_in_scope(_i, context), _i.c.id == item.id)
                    .values(code=item.code)
                )
            else:
                await session.execute(
                    sa.insert(_i).values(
                        id=item.id,
                        **scope,
                        product_dev_case_id=case.id.value,
                        code=item.code,
                        issued_by=step.actor_id,
                    )
                )
        elif isinstance(change, SkuAdded):
            sku = change.sku
            await session.execute(
                sa.insert(_s).values(
                    id=sku.id,
                    **scope,
                    product_dev_case_id=case.id.value,
                    item_code_id=change.item_code_id,
                    sku_code=sku.sku_code,
                    variant_label=sku.variant_label,
                    planned_quantity=sku.planned_quantity,
                    added_by=step.actor_id,
                )
            )
        elif isinstance(change, SkuRemoved):
            removed = await session.execute(
                sa.delete(_s).where(*_in_scope(_s, context), _s.c.id == change.sku_id)
            )
            assert isinstance(removed, CursorResult)
            if removed.rowcount != 1:
                raise ConflictError(
                    "this SKU is already removed", details={"sku_id": str(change.sku_id)}
                )

    def refusal(
        self, exc: IntegrityError, case: ProductDevelopmentCase, steps: list[ProductCaseStep]
    ) -> DWError | None:
        name = _constraint(exc)
        if name == PROPOSAL_CODE_CONSTRAINT:
            return ConflictError(
                "mã đề xuất này đã có trong tenant",
                details={"constraint": name, "proposal_code": case.proposal_code},
            )
        coding = steps[-1].coding if steps else None
        if name == ITEM_CODE_CONSTRAINT and isinstance(coding, ItemCodeIssued):
            return ConflictError(
                f"mã hàng {coding.item_code.code} đã có trong công ty",
                details={"constraint": name, "item_code": coding.item_code.code},
            )
        if name == SKU_CODE_CONSTRAINT and isinstance(coding, SkuAdded):
            return ConflictError(
                f"mã SKU {coding.sku.sku_code} đã có trong công ty",
                details={"constraint": name, "sku_code": coding.sku.sku_code},
            )
        if name == _ONE_ITEM_CODE_CONSTRAINT:
            return ConflictError(
                "hồ sơ này đã có mã hàng", details={"constraint": name, "case_id": str(case.id)}
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
                supplier = await _supplier_of(session, context, case)
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
                            supplier_id=supplier.id if supplier else None,
                            supplier_name=supplier.name if supplier else None,
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
            refusal = self.refusal(exc, case, steps)
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
            if row is None:
                return None
            (case,) = await _with_skus(session, context, [_case(row)])
        return case

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
                await self.save_in(session, context, case, steps)
                await SqlAuditRepository(session).append(audit)
        except IntegrityError as exc:
            refusal = self.refusal(exc, case, steps)
            if refusal is None:
                raise
            raise refusal from exc

    async def save_in(
        self,
        session: AsyncSession,
        context: AccessContext,
        case: ProductDevelopmentCase,
        steps: list[ProductCaseStep],
    ) -> None:
        """`save`'s writes in the caller's transaction (a step proposal's
        approval writes its documents in the same one): the optimistic UPDATE
        and the history rows of `steps`."""
        supplier = await _supplier_of(session, context, case)
        result = await session.execute(
            sa.update(_c)
            .where(
                *_in_scope(_c, context),
                _c.c.id == case.id.value,
                _c.c.version == case.version - 1,
            )
            .values(
                supplier_id=supplier.id if supplier else None,
                supplier_name=supplier.name if supplier else None,
                state=case.state.value,
                interrupted_state=(
                    case.interrupted_state.value if case.interrupted_state else None
                ),
                sample_round=case.sample_round,
                signoff_round=case.signoff_round,
                pic_user_id=case.pic_user_id,
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

    async def place_order(
        self,
        context: AccessContext,
        case: ProductDevelopmentCase,
        po_case: POCase,
        *,
        audits: Sequence[AuditEvent],
    ) -> None:
        """ĐẶT HÀNG, one transaction (module docstring). The UPDATE names the
        state the step left as well as the version read: a case that moved
        on, or was ordered by a click that got there first, updates nothing,
        and that is a `ConflictError` before anything is inserted."""
        steps = case.pop_pending_steps()
        (step,) = steps
        assert step.from_state is not None  # a step on a case, never `propose`
        try:
            async with tenant_session(
                self.session_factory, TenantScope.from_access_context(context)
            ) as session:
                moved = await session.execute(
                    sa.update(_c)
                    .where(
                        *_in_scope(_c, context),
                        _c.c.id == case.id.value,
                        _c.c.version == case.version - 1,
                        _c.c.state == step.from_state.value,
                    )
                    .values(state=case.state.value, version=case.version)
                )
                assert isinstance(moved, CursorResult)
                if moved.rowcount != 1:
                    raise ConflictError(
                        "hồ sơ đã được đặt hàng hoặc vừa thay đổi; tải lại để xem",
                        details={"case_id": str(case.id)},
                    )
                await self._write_steps(session, context, case, steps)
                po_case.created_at = await insert_po_case(session, context, po_case)
                audit_log = SqlAuditRepository(session)
                for audit in audits:
                    await audit_log.append(audit)
        except IntegrityError as exc:
            if _constraint(exc) == ONE_PO_CASE_CONSTRAINT:
                raise ConflictError(
                    "hồ sơ này đã có Hồ sơ PO",
                    details={"constraint": ONE_PO_CASE_CONSTRAINT, "case_id": str(case.id)},
                ) from exc
            raise

    async def po_case_of(self, context: AccessContext, case_id: uuid.UUID) -> uuid.UUID | None:
        """The PO case ĐẶT HÀNG opened from this case, if the caller may read
        it."""
        _p = tables.po_cases
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            found: uuid.UUID | None = await session.scalar(
                sa.select(_p.c.id).where(
                    *_in_scope(_p, context), _p.c.product_dev_case_id == case_id
                )
            )
        return found

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
        if case_filter.category is not None:
            query = query.where(_c.c.category == case_filter.category)
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
            cases = await _with_skus(session, context, [_case(row) for row in rows])
        return build_page(cases, request=request, position_of=_position)

    async def list_active(
        self, context: AccessContext, request: PageRequest
    ) -> Page[ProductDevelopmentCase]:
        """One page of the workspace's cases not yet ordered or cancelled,
        newest first: what the follow-up sweep and the brief evaluate, a page
        at a time. `ix_product_dev_cases_page` (tenant, workspace, created_at,
        id) carries the ORDER BY."""
        terminal = [state.value for state in PRODUCT_TERMINAL_STATES]
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            rows = (
                await session.execute(
                    _with_current_round()
                    .where(
                        *_in_scope(_c, context),
                        _c.c.state.notin_(terminal),
                        after_position(_c.c.created_at, _c.c.id, request.after),
                    )
                    .order_by(*newest_first(_c.c.created_at, _c.c.id))
                    .limit(request.fetch_limit)
                )
            ).all()
            cases = await _with_skus(session, context, [_case(row) for row in rows])
        return build_page(cases, request=request, position_of=_position)

    async def state_entered_at(
        self, context: AccessContext, case_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, datetime]:
        """Each case's latest history row, served by the history's (tenant,
        case, occurred_at) index; the SLA clock's start, as the PO case's is."""
        if not case_ids:
            return {}
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            rows = (
                await session.execute(
                    sa.select(_t.c.product_dev_case_id, sa.func.max(_t.c.occurred_at).label("at"))
                    .where(*_in_scope(_t, context), _t.c.product_dev_case_id.in_(list(case_ids)))
                    .group_by(_t.c.product_dev_case_id)
                )
            ).all()
        return {row.product_dev_case_id: row.at for row in rows}

    async def list_transitions(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId, request: PageRequest
    ) -> Page[ProductCaseTransition]:
        """Newest first, by `ix_product_dev_case_transitions_case_page`
        (tenant, workspace, case, occurred_at, id)."""
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            rows = (
                await session.execute(
                    sa.select(_t)
                    .where(
                        *_in_scope(_t, context),
                        _t.c.product_dev_case_id == case_id.value,
                        after_position(_t.c.occurred_at, _t.c.id, request.after),
                    )
                    .order_by(*newest_first(_t.c.occurred_at, _t.c.id))
                    .limit(request.fetch_limit)
                )
            ).all()
        return build_page(
            rows,
            request=request,
            position_of=lambda row: CursorPosition(sort_value=row.occurred_at, tiebreaker=row.id),
        ).map_items(
            lambda row: ProductCaseTransition(
                action=ProductAction(row.action),
                from_state=ProductDevState(row.from_state) if row.from_state else None,
                to_state=ProductDevState(row.to_state),
                reason=row.reason,
                actor_id=row.actor_id,
                occurred_at=row.occurred_at,
                document_id=row.document_id,
                id=row.id,
            )
        )

    async def list_rounds(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> list[SampleRound]:
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            rows = (
                await session.execute(
                    _rounds()
                    .where(*_in_scope(_r, context), _r.c.product_dev_case_id == case_id.value)
                    .order_by(_r.c.round_no.asc())
                )
            ).all()
        return [_round(row) for row in rows]

    async def closed_rounds_since(
        self, context: AccessContext, since: datetime
    ) -> list[ClosedRound]:
        """Served by `ix_product_sample_rounds_closed_at` (fd285c0433c4)."""
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            rows = (
                await session.execute(
                    _rounds()
                    .where(*_in_scope(_r, context), _r.c.closed_at >= since)
                    .order_by(_r.c.closed_at.asc(), _r.c.id.asc())
                )
            ).all()
            if not rows:
                return []
            case_rows = (
                await session.execute(
                    _with_current_round().where(
                        *_in_scope(_c, context),
                        _c.c.id.in_({row.product_dev_case_id for row in rows}),
                    )
                )
            ).all()
            cases = {
                case.id.value: case
                for case in await _with_skus(session, context, [_case(r) for r in case_rows])
            }
        return [
            ClosedRound(case=cases[row.product_dev_case_id], sample_round=_round(row))
            for row in rows
        ]

    async def find_by_proposal_code(
        self, context: AccessContext, proposal_code: str
    ) -> list[ProductDevelopmentCase]:
        """Case-insensitive equality on the trimmed code; the tenant-wide
        UNIQUE on the code makes this at most one in practice, but two codes
        differing only in case are both returned rather than one guessed."""
        wanted = proposal_code.strip().lower()
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            rows = (
                await session.execute(
                    _with_current_round()
                    .where(
                        *_in_scope(_c, context),
                        sa.func.lower(sa.func.btrim(_c.c.proposal_code)) == wanted,
                    )
                    .order_by(_c.c.proposal_code.asc())
                )
            ).all()
            return await _with_skus(session, context, [_case(row) for row in rows])


def _rounds() -> sa.Select[tuple[object, ...]]:
    """A round with the revision request that closed it, if one did."""
    return sa.select(_r, _q.c.revision_document_id, _q.c.requested_changes).select_from(
        _r.outerjoin(
            _q,
            sa.and_(
                _q.c.tenant_id == _r.c.tenant_id,
                _q.c.product_dev_case_id == _r.c.product_dev_case_id,
                _q.c.round_no == _r.c.round_no,
            ),
        )
    )


def _round(row: Row[tuple[object, ...]]) -> SampleRound:
    m = row._mapping
    return SampleRound(
        round_no=m[_r.c.round_no],
        opened_at=m[_r.c.opened_at],
        opened_by=m[_r.c.opened_by],
        result=SampleResult(m[_r.c.result]) if m[_r.c.result] else None,
        evaluation_document_id=m[_r.c.evaluation_document_id],
        closed_at=m[_r.c.closed_at],
        closed_by=m[_r.c.closed_by],
        revision_document_id=m[_q.c.revision_document_id],
        requested_changes=m[_q.c.requested_changes],
    )


@dataclass(frozen=True)
class SqlWorkspacesAwaitingApproval:
    """Implements `WorkspacesAwaitingApprovalPort` through
    `supply_chain.workspaces_awaiting_product_approval()`, the one SECURITY
    DEFINER read that crosses tenants for the reconcile lane: workspaces with
    a case waiting for BGĐ's review or for its sign-off. Ids only; every read
    after it runs under that tenant's and workspace's RLS."""

    session_factory: async_sessionmaker[AsyncSession]

    async def awaiting_approval(self) -> list[tuple[uuid.UUID, uuid.UUID]]:
        async with self.session_factory() as session, session.begin():
            rows = (
                await session.execute(
                    sa.text(
                        "SELECT tenant_id, workspace_id"
                        " FROM supply_chain.workspaces_awaiting_product_approval()"
                    )
                )
            ).all()
        return [(row.tenant_id, row.workspace_id) for row in rows]
