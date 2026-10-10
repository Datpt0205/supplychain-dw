"""A draft filed as a case document, in one transaction (ticket
ai-automation/17, item 2): the draft's confirmation, the document made from it
(`origin = ai_prepared`, its `draft_id`) and the audit events. A version
already decided meets the decisions' UNIQUE and refuses the whole; two
documents racing for one version are told to try again.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_kernel.errors import ConflictError
from dw_platform.adapters.persistence.repositories import SqlAuditRepository
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence.case_document_repository import (
    insert_case_document,
    version_refusal,
)
from dw_supply_chain.adapters.persistence.document_draft_repository import (
    ALREADY_DECIDED,
    insert_decision,
)
from dw_supply_chain.application.document_drafts import NewDraftDecision
from dw_supply_chain.application.ports import NewCaseDocument


@dataclass(frozen=True)
class SqlDraftFilings:
    """Implements `DraftFilingsPort`."""

    session_factory: async_sessionmaker[AsyncSession]

    async def record(
        self,
        context: AccessContext,
        *,
        document: NewCaseDocument,
        confirmation: NewDraftDecision,
        draft_id: uuid.UUID,
        audits: Sequence[AuditEvent],
    ) -> None:
        try:
            async with tenant_session(
                self.session_factory, TenantScope.from_access_context(context)
            ) as session:
                await insert_decision(session, context, confirmation)
                await insert_case_document(session, context, document, draft_id=draft_id)
                audit_log = SqlAuditRepository(session)
                for event in audits:
                    await audit_log.append(event)
        except IntegrityError as exc:
            if ALREADY_DECIDED in str(exc.orig):
                raise ConflictError(
                    "bản nháp đã được quyết", details={"draft_id": str(draft_id)}
                ) from exc
            refusal = version_refusal(exc, document)
            if refusal is None:
                raise
            raise refusal from exc


__all__ = ["SqlDraftFilings"]
