"""SQL persistence for step preparations and the two ends of a proposal
(ticket ai-automation/05; migration `1a8527a5b426`).

Every statement runs under `tenant_session` (tenant AND workspace bound; the
table is narrowed by both) and names both as a second layer. Rows are only
inserted (`dw_app` holds SELECT and INSERT).

`SqlProposalOutcomes.apply_approved` is ONE transaction: the typed result's
new draft version, each confirmation, each document made from a confirmed
draft (`origin = ai_prepared`), the case's step (optimistic on its version,
through `SqlProductCaseRepository.save_in`, the writes a click makes), the
preparation row and every audit event. A confirmation of a version already
decided meets the decisions' UNIQUE and the whole is refused, so a proposal is
applied at most once however many times its decision arrives.
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
from dw_supply_chain.adapters.persistence.case_document_repository import (
    insert_case_document,
    version_refusal,
)
from dw_supply_chain.adapters.persistence.document_draft_repository import (
    ALREADY_DECIDED,
    insert_decision,
    insert_draft,
)
from dw_supply_chain.adapters.persistence.product_case_repository import SqlProductCaseRepository
from dw_supply_chain.application.document_drafts import NewDocumentDraft, NewDraftDecision
from dw_supply_chain.application.step_preparation import NewPreparationRecord, PreparationRecord
from dw_supply_chain.application.step_proposals import AiPreparedDocument
from dw_supply_chain.domain.product_development_case import ProductDevelopmentCase
from dw_supply_chain.domain.step_proposal import PreparationOutcome

_p = tables.step_preparations


def _scope(context: AccessContext) -> TenantScope:
    return TenantScope.from_access_context(context)


def _record(row: Row[Any]) -> PreparationRecord:
    return PreparationRecord(
        id=row.id,
        case_id=row.product_dev_case_id,
        transition_id=row.transition_id,
        policy_version=row.policy_version,
        action=row.action,
        outcome=PreparationOutcome(row.outcome),
        reason=row.reason,
        run_id=row.run_id,
        draft_lineages=tuple(uuid.UUID(str(x)) for x in row.draft_lineages),
        created_at=row.created_at,
    )


async def _insert_record(
    session: AsyncSession, context: AccessContext, record: NewPreparationRecord
) -> None:
    await session.execute(
        sa.insert(_p).values(
            id=record.id,
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            product_dev_case_id=record.case_id,
            transition_id=record.transition_id,
            policy_version=record.policy_version,
            action=record.action,
            outcome=record.outcome.value,
            reason=record.reason,
            run_id=record.run_id,
            draft_lineages=[str(x) for x in record.draft_lineages],
            recorded_by=context.principal_id,
        )
    )


@dataclass(frozen=True)
class SqlPreparationRecords:
    """Implements `PreparationRecordsPort`."""

    session_factory: async_sessionmaker[AsyncSession]

    async def latest(
        self, context: AccessContext, transition_id: uuid.UUID, policy_version: str
    ) -> PreparationRecord | None:
        """By `ix_step_preparations_tenant_id_workspace_id_transition_id`."""
        async with tenant_session(self.session_factory, _scope(context)) as session:
            row = (
                await session.execute(
                    sa.select(_p)
                    .where(
                        _p.c.tenant_id == context.tenant_id,
                        _p.c.workspace_id == context.workspace_id,
                        _p.c.transition_id == transition_id,
                        _p.c.policy_version == policy_version,
                    )
                    .order_by(_p.c.created_at.desc(), _p.c.id.desc())
                    .limit(1)
                )
            ).first()
        return None if row is None else _record(row)

    async def add(
        self, context: AccessContext, record: NewPreparationRecord, *, audit: AuditEvent
    ) -> None:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            await _insert_record(session, context, record)
            await SqlAuditRepository(session).append(audit)


@dataclass(frozen=True)
class SqlProposalOutcomes:
    """Implements `ProposalOutcomesPort`."""

    session_factory: async_sessionmaker[AsyncSession]

    async def apply_approved(
        self,
        context: AccessContext,
        *,
        case: ProductDevelopmentCase,
        new_drafts: Sequence[NewDocumentDraft],
        confirmations: Sequence[NewDraftDecision],
        documents: Sequence[AiPreparedDocument],
        record: NewPreparationRecord,
        audits: Sequence[AuditEvent],
    ) -> None:
        cases = SqlProductCaseRepository(self.session_factory)
        steps = case.pop_pending_steps()
        try:
            async with tenant_session(self.session_factory, _scope(context)) as session:
                for draft in new_drafts:
                    await insert_draft(session, context, draft)
                for decision in confirmations:
                    await insert_decision(session, context, decision)
                for prepared in documents:
                    await insert_case_document(
                        session, context, prepared.document, draft_id=prepared.draft_id
                    )
                await cases.save_in(session, context, case, steps)
                await _insert_record(session, context, record)
                audit_log = SqlAuditRepository(session)
                for event in audits:
                    await audit_log.append(event)
        except IntegrityError as exc:
            if ALREADY_DECIDED in str(exc.orig):
                raise ConflictError(
                    "this proposal's drafts were already decided", details={"case_id": str(case.id)}
                ) from exc
            for prepared in documents:
                refusal = version_refusal(exc, prepared.document)
                if refusal is not None:
                    raise refusal from exc
            case_refusal = cases.refusal(exc, case, steps)
            if case_refusal is None:
                raise
            raise case_refusal from exc

    async def reject(
        self,
        context: AccessContext,
        *,
        decisions: Sequence[NewDraftDecision],
        record: NewPreparationRecord,
        audits: Sequence[AuditEvent],
    ) -> None:
        try:
            async with tenant_session(self.session_factory, _scope(context)) as session:
                for decision in decisions:
                    await insert_decision(session, context, decision)
                await _insert_record(session, context, record)
                audit_log = SqlAuditRepository(session)
                for event in audits:
                    await audit_log.append(event)
        except IntegrityError as exc:
            if ALREADY_DECIDED in str(exc.orig):
                raise ConflictError("this proposal's drafts were already decided") from exc
            raise
