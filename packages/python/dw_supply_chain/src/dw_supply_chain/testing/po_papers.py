"""The PO step paper gate for tests that do not exercise it (ticket
ai-automation/15).

The platform's rule (no step requires a paper): what every PO case had before
the ticket. A test of the gate itself builds `PaperGateResolver` over real or
recorded stores and a policy that names a paper.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.application.po_papers import PaperGateResolver
from dw_supply_chain.domain.case_document import CaseDocument, CaseKind
from dw_supply_chain.po_documents_policy import SupplyChainPODocuments

PLATFORM_DEFAULT_PO_DOCUMENTS = SupplyChainPODocuments(
    schema_version="1.0",
    policy_id="supply_chain_po_documents",
    policy_version="1.0.0",
)


class _NoDocuments:
    async def list_for_case(
        self, context: AccessContext, case_kind: CaseKind, case_id: uuid.UUID
    ) -> list[CaseDocument]:
        return []


class _NoOverrides:
    async def get(self, context: AccessContext, policy_id: str) -> dict[str, object] | None:
        return None

    async def put(
        self,
        context: AccessContext,
        policy_id: str,
        content: Mapping[str, object],
        *,
        audit: AuditEvent,
    ) -> None:
        raise NotImplementedError("not exercised by the open paper gate")


def open_paper_gate() -> PaperGateResolver:
    return PaperGateResolver(
        documents=_NoDocuments(),
        policy_override_repo=_NoOverrides(),
        platform_default=PLATFORM_DEFAULT_PO_DOCUMENTS,
    )


__all__ = ["PLATFORM_DEFAULT_PO_DOCUMENTS", "open_paper_gate"]
