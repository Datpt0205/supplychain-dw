"""The paper a PO case's step needs, enforced where the step is taken (ticket
ai-automation/15; QE-02; `po_documents_policy`).

`PaperGateResolver.require` is asked by every path that takes a PO step: a
person's click (`AdvancePOCase`), the apply node of an approval the tenant's
matrix gates (`workflows.advance_case_graph`), and a step proposal AI prepared
(`application.po_steps.ApprovePOStep`, where the paper is approved in the same
transaction, so it is satisfied by the paper being written). A step the
tenant's policy names is refused (409, naming the type) until a document of
that type is on THIS case, read under the caller's tenant and workspace.

The tenant's policy is a process rule: read and replaced with
`supply_chain.action_duties.read|write`, like its other step rules.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from dw_kernel.errors import ConflictError
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import AuthorizationPort, PolicyOverridePort
from dw_supply_chain.application.handlers import (
    ACTION_DUTIES_READ,
    ACTION_DUTIES_WRITE,
    _put_policy_override,
    _resolve_policy,
)
from dw_supply_chain.application.step_preparation import CaseDocumentListPort
from dw_supply_chain.domain.case_document import CaseKind, DocumentType
from dw_supply_chain.domain.po_case import CaseAction
from dw_supply_chain.po_documents_policy import PO_DOCUMENTS_POLICY_ID, SupplyChainPODocuments

_RESOURCE = "po_documents_policy"


async def resolve_po_documents(
    context: AccessContext,
    policy_override_repo: PolicyOverridePort,
    platform_default: SupplyChainPODocuments,
) -> SupplyChainPODocuments:
    return await _resolve_policy(
        context,
        policy_override_repo,
        policy_id=PO_DOCUMENTS_POLICY_ID,
        schema=SupplyChainPODocuments,
        platform_default=platform_default,
    )


def paper_refusal(case_id: uuid.UUID, action: CaseAction, paper: DocumentType) -> ConflictError:
    return ConflictError(
        f"{action.value} cần {paper.value} trên hồ sơ này trước",
        details={
            "case_id": str(case_id),
            "action": action.value,
            "missing_document_type": paper.value,
        },
    )


@dataclass(frozen=True)
class PaperGateResolver:
    documents: CaseDocumentListPort
    policy_override_repo: PolicyOverridePort
    platform_default: SupplyChainPODocuments

    async def paper_for(self, context: AccessContext, action: CaseAction) -> DocumentType | None:
        policy = await resolve_po_documents(
            context, self.policy_override_repo, self.platform_default
        )
        return policy.paper_for(action)

    async def require(self, context: AccessContext, case_id: uuid.UUID, action: CaseAction) -> None:
        """Refuses `action` on the case unless the paper the tenant's policy
        names for it is on the case."""
        paper = await self.paper_for(context, action)
        if paper is None:
            return
        documents = await self.documents.list_for_case(context, CaseKind.PO, case_id)
        if not any(d.doc_type is paper and d.case_id == case_id for d in documents):
            raise paper_refusal(case_id, action, paper)


@dataclass(frozen=True)
class GetPODocumentsPolicy:
    policy_override_repo: PolicyOverridePort
    platform_default: SupplyChainPODocuments
    authz: AuthorizationPort

    async def handle(self, context: AccessContext) -> SupplyChainPODocuments:
        await self.authz.require(
            context=context, action=ACTION_DUTIES_READ, resource_type=_RESOURCE
        )
        return await resolve_po_documents(context, self.policy_override_repo, self.platform_default)


@dataclass(frozen=True)
class SetPODocumentsPolicyOverride:
    policy_override_repo: PolicyOverridePort
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(self, context: AccessContext, policy: SupplyChainPODocuments) -> None:
        await self.authz.require(
            context=context, action=ACTION_DUTIES_WRITE, resource_type=_RESOURCE
        )
        await _put_policy_override(
            context,
            self.policy_override_repo,
            policy_id=PO_DOCUMENTS_POLICY_ID,
            policy=policy,
            resource_type=_RESOURCE,
            ids=self.ids,
            clock=self.clock,
        )


__all__ = [
    "GetPODocumentsPolicy",
    "PaperGateResolver",
    "SetPODocumentsPolicyOverride",
    "paper_refusal",
    "resolve_po_documents",
]
