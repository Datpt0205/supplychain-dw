"""An approved PO step, in one transaction (tickets ai-automation/15-18).

The draft's confirmation (or, for the outcome not chosen, its closing
decision), the document made from it (`origin = ai_prepared`, its
`draft_id`), the step (`SqlPOCaseRepository.save_in`: optimistic on the
version, the history row), the shipping dates and container it learnt, the
payment (`insert_payment`, what the commercial card writes) and every audit
event. A confirmation of a version already
decided meets the decisions' UNIQUE, a case saved meanwhile its version guard:
either refuses the whole.
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
from dw_supply_chain.adapters.persistence.commercial_repository import (
    insert_payment,
    payment_conflict,
)
from dw_supply_chain.adapters.persistence.document_draft_repository import (
    ALREADY_DECIDED,
    insert_decision,
)
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.application.document_drafts import NewDraftDecision
from dw_supply_chain.application.ports import NewCaseDocument
from dw_supply_chain.domain.commercial import NewPOPayment
from dw_supply_chain.domain.po_case import POCase, Shipping


@dataclass(frozen=True)
class SqlPOStepOutcomes:
    """Implements `POStepOutcomesPort`."""

    session_factory: async_sessionmaker[AsyncSession]

    async def apply(
        self,
        context: AccessContext,
        *,
        case: POCase,
        confirmation: NewDraftDecision | None,
        document: NewCaseDocument | None,
        draft_id: uuid.UUID | None,
        payment: NewPOPayment | None,
        audits: Sequence[AuditEvent],
        closed: NewDraftDecision | None,
        shipping: Shipping | None,
    ) -> None:
        cases = SqlPOCaseRepository(self.session_factory)
        try:
            async with tenant_session(
                self.session_factory, TenantScope.from_access_context(context)
            ) as session:
                if confirmation is not None:
                    await insert_decision(session, context, confirmation)
                if document is not None:
                    await insert_case_document(session, context, document, draft_id=draft_id)
                if closed is not None:
                    await insert_decision(session, context, closed)
                await cases.save_in(session, case)
                if shipping is not None:
                    await cases.write_shipping_in(session, case.id, shipping)
                if payment is not None:
                    await insert_payment(session, context, payment)
                audit_log = SqlAuditRepository(session)
                for event in audits:
                    await audit_log.append(event)
        except IntegrityError as exc:
            if ALREADY_DECIDED in str(exc.orig):
                raise ConflictError(
                    "bản nháp đã được quyết", details={"case_id": str(case.id)}
                ) from exc
            refusal = (None if document is None else version_refusal(exc, document)) or (
                payment_conflict(exc)
            )
            if refusal is None:
                raise
            raise refusal from exc


__all__ = ["SqlPOStepOutcomes"]
