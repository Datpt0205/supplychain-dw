"""Product-development cases: propose, read, list, and take a step (stage-1
ticket 01, ADR 0016).

What each command decides, and where:

- **Who:** every step, `propose` included, needs the scope of its duty under
  the tenant's `SupplyChainProductActionDuties` (`resolve_product_action_duties`),
  checked here before the case is read. Opening a case also needs
  `supply_chain.product_case.write`, as opening a PO case needs
  `supply_chain.po_case.write` (lead decision 9). Reading needs
  `supply_chain.product_case.read`; every holder sees every case of the
  workspace, and the PIC filter only narrows (QE-18).
- **Which case:** read under the caller's tenant AND workspace (the table's
  RLS narrows by both, and the repository names both); another tenant's or
  workspace's case is not found, never forbidden.
- **The PIC:** `ProposeProductCase` takes no PIC at all. The domain stamps
  the actor, so no caller (the route, the Zalo channel of Z4, a tool) can
  name one.
- **The paper:** a step that takes a document gets the one the caller named,
  read under the caller's RLS; whether it is this round's is the domain's
  rule (`ProductDevelopmentCase._round_document`). A named document the
  caller cannot read is the same refusal as one never named.
- **The record:** the state, the history row, the round and the audit event
  are one transaction in the repository.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from dw_kernel.errors import NotFoundError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.pagination import Page, page_request
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import AuthorizationPort, PolicyOverridePort
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.application.handlers import (
    PRODUCT_CASE_READ,
    PRODUCT_CASE_WRITE,
    duty_scope,
    resolve_product_action_duties,
)
from dw_supply_chain.application.ports import ProductCaseListFilter, ProductCaseRepositoryPort
from dw_supply_chain.domain.case_document import CaseDocument, CaseDocumentId
from dw_supply_chain.domain.product_development_case import (
    ProductAction,
    ProductActionInput,
    ProductCaseTransition,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    SampleRound,
    apply_product_action,
    document_refusal,
)
from dw_supply_chain.product_action_duties import SupplyChainProductActionDuties

_RESOURCE = "product_dev_case"
_AUDIT_PREFIX = "supply_chain.product_case."


class ProductDocumentLookupPort(Protocol):
    """The one question a step asks of the document records: this document,
    if the caller may read it. `CaseDocumentRepositoryPort` satisfies it."""

    async def get(
        self, context: AccessContext, document_id: CaseDocumentId
    ) -> CaseDocument | None: ...


@dataclass(frozen=True, slots=True)
class ProductCaseDetail:
    """A case, its sample rounds, and the tenant's step-to-duty mapping the
    case page shows who may take each step from: the same mapping
    `AdvanceProductCase` authorizes against, resolved by the same function."""

    case: ProductDevelopmentCase
    rounds: list[SampleRound]
    duties: SupplyChainProductActionDuties


def _audit(
    context: AccessContext,
    ids: IdGenerator,
    clock: UtcClock,
    case: ProductDevelopmentCase,
    action: ProductAction,
    details: dict[str, object],
) -> AuditEvent:
    return AuditEvent(
        id=ids.new_uuid(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        actor_id=UserId(context.principal_id),
        action=f"{_AUDIT_PREFIX}{action.value}",
        resource_type=_RESOURCE,
        resource_id=str(case.id),
        occurred_at=clock.now(),
        details=details,
    )


async def _case_in_workspace(
    repo: ProductCaseRepositoryPort, context: AccessContext, case_id: ProductDevelopmentCaseId
) -> ProductDevelopmentCase:
    case = await repo.get(context, case_id)
    if case is None or case.workspace_id.value != context.workspace_id:
        raise NotFoundError("product case not found", details={"case_id": str(case_id)})
    return case


@dataclass(frozen=True)
class ProposeProductCase:
    """Step 1: Cung ứng proposes a product and becomes its PIC.

    Tenant and workspace come from the verified context, and so does the PIC:
    `handle` has no parameter that could name another person. Gated twice,
    both before anything is read: the context's write, as `CreatePOCase` is
    gated (lead decision 9), and the duty the tenant's policy gives
    `propose` (decision 7)."""

    repo: ProductCaseRepositoryPort
    authz: AuthorizationPort
    policy_override_repo: PolicyOverridePort
    platform_default_duties: SupplyChainProductActionDuties
    ids: IdGenerator
    clock: UtcClock

    async def handle(
        self, context: AccessContext, *, proposal_code: str, product_name: str, category: str
    ) -> ProductDevelopmentCase:
        await self.authz.require(
            context=context, action=PRODUCT_CASE_WRITE, resource_type=_RESOURCE
        )
        duties = await resolve_product_action_duties(
            context, self.policy_override_repo, self.platform_default_duties
        )
        await self.authz.require(
            context=context,
            action=duty_scope(duties.duty_for(ProductAction.PROPOSE)),
            resource_type=_RESOURCE,
        )
        case = ProductDevelopmentCase.propose(
            id=ProductDevelopmentCaseId(self.ids.new_uuid()),
            tenant_id=TenantId(context.tenant_id),
            workspace_id=WorkspaceId(context.workspace_id),
            proposal_code=proposal_code,
            product_name=product_name,
            category=category,
            actor_id=context.principal_id,
        )
        await self.repo.add(
            context,
            case,
            audit=_audit(
                context,
                self.ids,
                self.clock,
                case,
                ProductAction.PROPOSE,
                {"proposal_code": case.proposal_code, "category": case.category},
            ),
        )
        return case


@dataclass(frozen=True)
class GetProductCase:
    repo: ProductCaseRepositoryPort
    authz: AuthorizationPort
    policy_override_repo: PolicyOverridePort
    platform_default_duties: SupplyChainProductActionDuties

    async def handle(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> ProductCaseDetail:
        await self.authz.require(
            context=context,
            action=PRODUCT_CASE_READ,
            resource_type=_RESOURCE,
            resource_id=str(case_id),
        )
        case = await _case_in_workspace(self.repo, context, case_id)
        return ProductCaseDetail(
            case=case,
            rounds=await self.repo.list_rounds(context, case.id),
            duties=await resolve_product_action_duties(
                context, self.policy_override_repo, self.platform_default_duties
            ),
        )


@dataclass(frozen=True)
class ListProductCases:
    """The workspace's cases, newest first, narrowed by the filter. The
    cursor is decoded against the filter's own `page_query` here, so no
    caller can pair one filter's cursor with another's query."""

    repo: ProductCaseRepositoryPort
    authz: AuthorizationPort

    async def handle(
        self,
        context: AccessContext,
        case_filter: ProductCaseListFilter,
        *,
        limit: int,
        cursor: str | None,
    ) -> Page[ProductDevelopmentCase]:
        await self.authz.require(context=context, action=PRODUCT_CASE_READ, resource_type=_RESOURCE)
        request = page_request(
            limit=limit,
            cursor=cursor,
            query=case_filter.page_query(context.tenant_id, context.workspace_id),
        )
        return await self.repo.list_page(context, request, case_filter)


@dataclass(frozen=True)
class ListProductCaseTransitions:
    repo: ProductCaseRepositoryPort
    authz: AuthorizationPort

    async def handle(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> list[ProductCaseTransition]:
        await self.authz.require(
            context=context,
            action=PRODUCT_CASE_READ,
            resource_type=_RESOURCE,
            resource_id=str(case_id),
        )
        case = await _case_in_workspace(self.repo, context, case_id)
        return await self.repo.list_transitions(context, case.id)


@dataclass(frozen=True)
class AdvanceProductCase:
    """One step on a case, chosen from the closed `ProductAction` set.

    The duty is checked first, before the case or any document is read, so a
    caller without it learns nothing about either. No step here needs an
    approval in this slice; `pass_sample` starting the BGĐ review is S2's."""

    repo: ProductCaseRepositoryPort
    documents: ProductDocumentLookupPort
    authz: AuthorizationPort
    policy_override_repo: PolicyOverridePort
    platform_default_duties: SupplyChainProductActionDuties
    ids: IdGenerator
    clock: UtcClock

    async def handle(
        self,
        context: AccessContext,
        *,
        case_id: ProductDevelopmentCaseId,
        action: ProductAction,
        reason: str | None = None,
        supplier_name: str | None = None,
        document_id: uuid.UUID | None = None,
    ) -> ProductDevelopmentCase:
        duties = await resolve_product_action_duties(
            context, self.policy_override_repo, self.platform_default_duties
        )
        await self.authz.require(
            context=context,
            action=duty_scope(duties.duty_for(action)),
            resource_type=_RESOURCE,
            resource_id=str(case_id),
        )
        case = await _case_in_workspace(self.repo, context, case_id)
        document = None
        if document_id is not None:
            document = await self.documents.get(context, CaseDocumentId(document_id))
            if document is None:
                raise document_refusal(case.id.value, action)
        before = case.state
        apply_product_action(
            case,
            action=action,
            given=ProductActionInput(
                actor_id=context.principal_id,
                reason=reason,
                supplier_name=supplier_name,
                document=document,
            ),
        )
        await self.repo.save(
            context,
            case,
            audit=_audit(
                context,
                self.ids,
                self.clock,
                case,
                action,
                {
                    "from_state": before.value,
                    "to_state": case.state.value,
                    "sample_round": case.sample_round,
                    **({"document_id": str(document.id)} if document else {}),
                },
            ),
        )
        return case
