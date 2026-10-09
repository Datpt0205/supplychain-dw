"""An approved PO draft, in one transaction (ticket ai-automation/14).

The typed version of the draft and its confirmation, the `purchase_order`
document made from it (`origin = ai_prepared`, its `draft_id`), the PO step
(`SqlPOCaseRepository.save_in`: optimistic on the version, the line
quantities, the history row), the terms and line prices (`write_terms`, what
the commercial card writes) and every audit event. A confirmation of a
version already decided meets the decisions' UNIQUE, a PO number already in
the tenant `uq_po_cases_tenant_id_po_reference`: either refuses the whole.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

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
from dw_supply_chain.adapters.persistence.commercial_repository import write_terms
from dw_supply_chain.adapters.persistence.document_draft_repository import (
    ALREADY_DECIDED,
    insert_decision,
    insert_draft,
)
from dw_supply_chain.adapters.persistence.po_case_repository import (
    SqlPOCaseRepository,
    reference_refusal,
)
from dw_supply_chain.application.document_drafts import NewDocumentDraft, NewDraftDecision
from dw_supply_chain.application.ports import NewCaseDocument
from dw_supply_chain.domain.commercial import CommercialTerms
from dw_supply_chain.domain.po_case import POCase


@dataclass(frozen=True)
class SqlPurchaseOrderOutcomes:
    """Implements `PurchaseOrderOutcomesPort`."""

    session_factory: async_sessionmaker[AsyncSession]

    async def apply(
        self,
        context: AccessContext,
        *,
        case: POCase,
        new_draft: NewDocumentDraft,
        confirmation: NewDraftDecision,
        document: NewCaseDocument,
        terms: CommercialTerms,
        line_prices: Mapping[uuid.UUID, Decimal],
        audits: Sequence[AuditEvent],
    ) -> None:
        cases = SqlPOCaseRepository(self.session_factory)
        try:
            async with tenant_session(
                self.session_factory, TenantScope.from_access_context(context)
            ) as session:
                await insert_draft(session, context, new_draft)
                await insert_decision(session, context, confirmation)
                await insert_case_document(session, context, document, draft_id=new_draft.id)
                await cases.save_in(session, case)
                await write_terms(session, context, case.id.value, terms, line_prices)
                audit_log = SqlAuditRepository(session)
                for event in audits:
                    await audit_log.append(event)
        except IntegrityError as exc:
            if ALREADY_DECIDED in str(exc.orig):
                raise ConflictError(
                    "bản nháp PO đã được quyết", details={"case_id": str(case.id)}
                ) from exc
            refusal = reference_refusal(exc, case) or version_refusal(exc, document)
            if refusal is None:
                raise
            raise refusal from exc


__all__ = ["SqlPurchaseOrderOutcomes"]
