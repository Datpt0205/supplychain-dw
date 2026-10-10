"""View receipts and single-use decision codes (channels Z5, ADR 0007).

Migration e399be8c0a2d. Two ways in, one per kind of question:

- **Mine, before a tenant is known.** The bot knows a person (the chat's link
  row) and not yet a tenant, so it reads that person's codes with
  `app.principal_id` bound, through `approval_decision_codes_self_select` —
  the baseline's mechanism for "my memberships". No SECURITY DEFINER function.
- **Everything else** binds the tenant and workspace of the row it touches,
  taken from that row, and runs under `tenant_isolation_*`: recording a view,
  revoking, counting a wrong try, reading the approval a code points at, and
  consuming a code inside the decision's unit of work (`SqlDecisionCodeLedger`).

Nothing here sees or returns the digits: only their HMAC is stored.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_kernel.errors import ConflictError
from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.repositories import SqlAuditRepository, _approval_from_row
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.application.approval_codes import (
    MAX_WRONG_TRIES,
    CodedApproval,
    CodeState,
    LastDecision,
    NewDecisionCode,
    OpenCode,
    StoredCode,
    ViewReceipt,
)
from dw_platform.domain.audit import AuditEvent

_SET_PRINCIPAL = sa.text("SELECT set_config('app.principal_id', :principal_id, true)")
_SET_SCOPE = sa.text(
    "SELECT set_config('app.tenant_id', :tenant_id, true),"
    "       set_config('app.workspace_id', :workspace_id, true)"
)

_codes = tables.approval_decision_codes
_receipts = tables.approval_view_receipts

_OPEN = sa.and_(_codes.c.used_at.is_(None), _codes.c.revoked_at.is_(None))

# The database's clock decides expiry, the same clock `consume` checks against.
_STATE = sa.case(
    (_codes.c.used_at.is_not(None), CodeState.USED.value),
    (_codes.c.revoked_reason == "locked", CodeState.LOCKED.value),
    (_codes.c.revoked_reason == "reissued", CodeState.REISSUED.value),
    (_codes.c.expires_at <= sa.func.now(), CodeState.EXPIRED.value),
    else_=CodeState.OPEN.value,
)

# A code is kept for a day (`platform.prune_approval_decision_codes`); the bot
# reads no further back, so the answer does not depend on when the sweep ran.
_KEPT = sa.text("interval '1 day'")


@dataclass(frozen=True)
class SqlApprovalCodeStore:
    """Implements ``ApprovalCodeStorePort``."""

    session_factory: async_sessionmaker[AsyncSession]

    async def record_view(
        self, context: AccessContext, receipt: ViewReceipt, code: NewDecisionCode | None
    ) -> None:
        try:
            await self._record_view(context, receipt, code)
        except IntegrityError as exc:
            # Two codes issued for one approval and person at once: the
            # one-open-code index keeps the first.
            if "uq_approval_decision_codes_open" not in str(exc.orig):
                raise
            raise ConflictError(
                "another code was issued for this approval at the same moment; ask again",
                details={"approval_id": str(receipt.approval_id)},
            ) from None

    async def _record_view(
        self, context: AccessContext, receipt: ViewReceipt, code: NewDecisionCode | None
    ) -> None:
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            await session.execute(
                sa.insert(_receipts).values(
                    id=receipt.id,
                    tenant_id=receipt.tenant_id,
                    workspace_id=receipt.workspace_id,
                    approval_id=receipt.approval_id,
                    user_id=receipt.user_id,
                    approval_version=receipt.approval_version,
                    subject_version=receipt.subject_version,
                )
            )
            if code is None:
                return
            # Opening the approval again replaces the code it showed: the old
            # one stops working in the same transaction the new one starts.
            await session.execute(
                sa.update(_codes)
                .where(
                    _codes.c.tenant_id == receipt.tenant_id,
                    _codes.c.approval_id == receipt.approval_id,
                    _codes.c.user_id == receipt.user_id,
                    _OPEN,
                )
                .values(revoked_at=sa.func.now(), revoked_reason=CodeState.REISSUED.value)
            )
            await session.execute(
                sa.insert(_codes).values(
                    id=code.id,
                    tenant_id=receipt.tenant_id,
                    workspace_id=receipt.workspace_id,
                    approval_id=receipt.approval_id,
                    user_id=receipt.user_id,
                    receipt_id=receipt.id,
                    code_hash=code.code_hash,
                    comment=code.comment,
                    expires_at=code.expires_at,
                )
            )

    async def open_codes(self, user_id: uuid.UUID) -> Sequence[OpenCode]:
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_PRINCIPAL, {"principal_id": str(user_id)})
            rows = await session.execute(
                sa.select(_codes.c.approval_id, _codes.c.code_hash).where(
                    _codes.c.user_id == user_id,
                    _OPEN,
                    _codes.c.expires_at > sa.func.now(),
                )
            )
            return [OpenCode(approval_id=r.approval_id, code_hash=bytes(r.code_hash)) for r in rows]

    async def codes_of(self, user_id: uuid.UUID) -> Sequence[StoredCode]:
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_PRINCIPAL, {"principal_id": str(user_id)})
            rows = await session.execute(
                sa.select(
                    _codes.c.id,
                    _codes.c.tenant_id,
                    _codes.c.workspace_id,
                    _codes.c.approval_id,
                    _codes.c.user_id,
                    _codes.c.receipt_id,
                    _codes.c.code_hash,
                    _codes.c.comment,
                    _STATE.label("state"),
                )
                .where(
                    _codes.c.user_id == user_id,
                    _codes.c.created_at > sa.func.now() - _KEPT,
                )
                .order_by(_codes.c.created_at.desc(), _codes.c.id)
            )
            return [
                StoredCode(
                    id=r.id,
                    tenant_id=r.tenant_id,
                    workspace_id=r.workspace_id,
                    approval_id=r.approval_id,
                    user_id=r.user_id,
                    receipt_id=r.receipt_id,
                    code_hash=bytes(r.code_hash),
                    comment=r.comment,
                    state=CodeState(r.state),
                )
                for r in rows
            ]

    async def record_wrong_try(self, user_id: uuid.UUID) -> int:
        locked = 0
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_PRINCIPAL, {"principal_id": str(user_id)})
            targets = (
                await session.execute(
                    sa.select(_codes.c.id, _codes.c.tenant_id, _codes.c.workspace_id).where(
                        _codes.c.user_id == user_id, _OPEN, _codes.c.expires_at > sa.func.now()
                    )
                )
            ).all()
            for target in targets:
                # Each code is counted under its own tenant and workspace, the
                # only policy that lets a row be written.
                await session.execute(
                    _SET_SCOPE,
                    {"tenant_id": str(target.tenant_id), "workspace_id": str(target.workspace_id)},
                )
                reaches = _codes.c.failed_attempts + 1 >= MAX_WRONG_TRIES
                reason = await session.scalar(
                    sa.update(_codes)
                    .where(_codes.c.id == target.id, _codes.c.user_id == user_id, _OPEN)
                    .values(
                        failed_attempts=_codes.c.failed_attempts + 1,
                        revoked_at=sa.case((reaches, sa.func.now()), else_=None),
                        revoked_reason=sa.case((reaches, CodeState.LOCKED.value), else_=None),
                    )
                    .returning(_codes.c.revoked_reason)
                )
                locked += int(reason == CodeState.LOCKED.value)
        return locked

    async def coded_approval(self, code: StoredCode) -> CodedApproval | None:
        scope = TenantScope(
            tenant_id=code.tenant_id, workspace_id=code.workspace_id, principal_id=code.user_id
        )
        async with tenant_session(self.session_factory, scope) as session:
            row = (
                await session.execute(
                    sa.select(tables.approval_requests).where(
                        tables.approval_requests.c.id == code.approval_id,
                        tables.approval_requests.c.workspace_id == code.workspace_id,
                    )
                )
            ).first()
            receipt = (
                await session.execute(
                    sa.select(_receipts.c.approval_version, _receipts.c.subject_version).where(
                        _receipts.c.id == code.receipt_id,
                        _receipts.c.user_id == code.user_id,
                        _receipts.c.approval_id == code.approval_id,
                    )
                )
            ).first()
            if row is None or receipt is None:
                return None
            decisions = tables.approval_decisions
            last = (
                await session.execute(
                    sa.select(decisions.c.decided_at, decisions.c.channel)
                    .where(decisions.c.request_id == code.approval_id)
                    .order_by(decisions.c.decided_at.desc())
                    .limit(1)
                )
            ).first()
            return CodedApproval(
                request=_approval_from_row(row),
                receipt_approval_version=receipt.approval_version,
                receipt_subject_version=receipt.subject_version,
                last_decision=(
                    None
                    if last is None
                    else LastDecision(decided_at=last.decided_at, channel=last.channel)
                ),
            )

    async def record_refusal(self, event: AuditEvent) -> None:
        scope = TenantScope(
            tenant_id=event.tenant_id.value,
            workspace_id=event.workspace_id.value,
            principal_id=event.actor_id.value,
        )
        async with tenant_session(self.session_factory, scope) as session:
            await SqlAuditRepository(session).append(event)


@dataclass
class SqlDecisionCodeLedger:
    """Implements ``DecisionCodeLedgerPort`` inside the platform unit of work,
    whose transaction also writes the decision."""

    session: AsyncSession

    async def consume(self, code_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        # One conditional statement, not a read then a write: two deliveries of
        # the same command racing here serialize on the row, and the second one
        # finds `used_at` set and updates nothing.
        result = await self.session.execute(
            sa.update(_codes)
            .where(
                _codes.c.id == code_id,
                _codes.c.user_id == user_id,
                _OPEN,
                _codes.c.expires_at > sa.func.now(),
            )
            .values(used_at=sa.func.now())
        )
        assert isinstance(result, CursorResult)
        return result.rowcount == 1


@dataclass(frozen=True)
class SqlApprovalCodeRetention:
    """Implements ``dw_worker.consumers.retention.RetentionPrunePort``: codes a
    day old go, through ``platform.prune_approval_decision_codes()`` (`dw_app`
    holds no DELETE). Receipts stay with their approval."""

    session_factory: async_sessionmaker[AsyncSession]

    async def prune(self) -> None:
        async with self.session_factory() as session, session.begin():
            await session.execute(sa.text("SELECT platform.prune_approval_decision_codes()"))
