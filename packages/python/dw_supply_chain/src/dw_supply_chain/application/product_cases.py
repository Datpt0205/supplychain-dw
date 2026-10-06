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
- **BGĐ's review (step 6):** a step that leaves the case in
  `pending_bod_review` (`pass_sample`, or `resume` back into it) asks
  `BodReviewPort.ensure` for the review AFTER the step is saved, in its own
  transaction. A refused or failed start does not undo the step: the answer
  says the review is not raised yet, and the worker's reconcile lane raises
  it. BGĐ's two outcomes (`GRAPH_ONLY_ACTIONS`) are refused here, before
  anything is read: only the review graph applies them.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Protocol

from dw_kernel.errors import DomainError, NotFoundError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.pagination import Page, page_request
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import AuthorizationPort, PolicyOverridePort
from dw_supply_chain.application.handlers import (
    PRODUCT_CASE_READ,
    PRODUCT_CASE_WRITE,
    duty_scope,
    resolve_product_action_duties,
)
from dw_supply_chain.application.ports import (
    BodReviewPort,
    PendingApprovalRecord,
    PendingApprovalsPort,
    ProductCaseListFilter,
    ProductCaseRepositoryPort,
    ReviewRaise,
    ReviewRequester,
)
from dw_supply_chain.application.product_case_audit import (
    PRODUCT_CASE_RESOURCE,
    product_case_audit,
)
from dw_supply_chain.domain.case_document import CaseDocument, CaseDocumentId
from dw_supply_chain.domain.product_development_case import (
    GRAPH_ONLY_ACTIONS,
    ProductAction,
    ProductActionInput,
    ProductCaseTransition,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
    SampleRound,
    apply_product_action,
    document_refusal,
)
from dw_supply_chain.product_action_duties import SupplyChainProductActionDuties
from dw_supply_chain.workflows.advance_product_case_graph import (
    BOD_REVIEW_APPROVAL_TYPE,
    BOD_REVIEW_CASE_KEY,
)

logger = logging.getLogger(__name__)

_RESOURCE = PRODUCT_CASE_RESOURCE


class ProductDocumentLookupPort(Protocol):
    """The one question a step asks of the document records: this document,
    if the caller may read it. `CaseDocumentRepositoryPort` satisfies it."""

    async def get(
        self, context: AccessContext, document_id: CaseDocumentId
    ) -> CaseDocument | None: ...


@dataclass(frozen=True, slots=True)
class ProductCaseDetail:
    """A case, its sample rounds, the tenant's step-to-duty mapping the case
    page shows who may take each step from (the same mapping
    `AdvanceProductCase` authorizes against, resolved by the same function),
    and, while it waits for BGĐ, the review approval it waits on."""

    case: ProductDevelopmentCase
    rounds: list[SampleRound]
    duties: SupplyChainProductActionDuties
    pending_review: PendingApprovalRecord | None = None


@dataclass(frozen=True, slots=True)
class ProductStepResult:
    """A step taken, and for a step that left the case waiting for BGĐ, what
    asking for the review did (None for every other step)."""

    case: ProductDevelopmentCase
    review: ReviewRaise | None = None


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
            audit=product_case_audit(
                context,
                self.ids,
                self.clock,
                case,
                ProductAction.PROPOSE.value,
                {"proposal_code": case.proposal_code, "category": case.category},
            ),
        )
        return case


@dataclass(frozen=True)
class GetProductCase:
    """The case page. While the case waits for BGĐ it carries the review
    approval it waits on, read from the approval inbox in the caller's
    workspace: its id, when it was raised, and the scope stamped on it, which
    is who may decide. Nobody's name, and no way to decide from here."""

    repo: ProductCaseRepositoryPort
    authz: AuthorizationPort
    policy_override_repo: PolicyOverridePort
    platform_default_duties: SupplyChainProductActionDuties
    approvals: PendingApprovalsPort

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
            pending_review=(
                await self.approvals.pending_by_payload(
                    context,
                    approval_type=BOD_REVIEW_APPROVAL_TYPE,
                    key=BOD_REVIEW_CASE_KEY,
                    value=str(case.id),
                )
                if case.state is ProductDevState.PENDING_BOD_REVIEW
                else None
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
    """One step a person takes on a case, chosen from the closed
    `ProductAction` set less `GRAPH_ONLY_ACTIONS`.

    A graph-only action is refused first, then the duty is checked, both
    before the case or any document is read, so a caller without the duty
    learns nothing about either. A step that leaves the case waiting for BGĐ
    then asks for the review (module docstring)."""

    repo: ProductCaseRepositoryPort
    documents: ProductDocumentLookupPort
    authz: AuthorizationPort
    policy_override_repo: PolicyOverridePort
    platform_default_duties: SupplyChainProductActionDuties
    reviews: BodReviewPort
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
    ) -> ProductStepResult:
        if action in GRAPH_ONLY_ACTIONS:
            raise DomainError(
                "BGĐ's decision is taken on its approval, not as a step on the case",
                details={"action": action.value},
            )
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
            audit=product_case_audit(
                context,
                self.ids,
                self.clock,
                case,
                action.value,
                {
                    "from_state": before.value,
                    "to_state": case.state.value,
                    "sample_round": case.sample_round,
                    **({"document_id": str(document.id)} if document else {}),
                },
            ),
        )
        if case.state is not ProductDevState.PENDING_BOD_REVIEW:
            return ProductStepResult(case=case)
        return ProductStepResult(case=case, review=await self._ensure_review(context, case))

    async def _ensure_review(
        self, context: AccessContext, case: ProductDevelopmentCase
    ) -> ReviewRaise:
        """The step is saved whatever happens here. Broad on purpose: a quota
        refusal, a spend ceiling or a failed start must not turn a recorded
        step into an error; the reconcile lane raises the review later."""
        try:
            return await self.reviews.ensure(context, case, ReviewRequester.from_context(context))
        except Exception:
            logger.exception(
                "BGĐ review not raised after the step; the reconcile lane retries",
                extra={"case_id": str(case.id)},
            )
            return ReviewRaise.NOT_RAISED
