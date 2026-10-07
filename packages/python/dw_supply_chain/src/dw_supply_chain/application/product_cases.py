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
- **The approvals (step 6, step 9):** a step that leaves the case waiting
  on one (`pass_sample` into `pending_bod_review`, `submit_for_signoff` into
  `pending_signoff`) asks `ProductApprovalPort.ensure` for it AFTER the step
  is saved, in its own transaction. A refused or failed start does not undo
  the step: the answer says the approval is not raised yet, and the worker's
  reconcile lane raises it. The approvals' outcomes (`GRAPH_ONLY_ACTIONS`)
  are refused here, before anything is read: only their graphs apply them.
- **Step 9's codes:** the item code and SKU a person sends are handed to the
  domain as they came; the ids of the rows a step creates are minted here.
  Whether a code is taken in the tenant is the database's answer, a 409
  naming the code (ADR 0018).
- **ĐẶT HÀNG (ticket 05, ADR 0017):** its own command, `PlaceOrder`, never
  the step command, which refuses it: the case moves to `ordered` and the PO
  case opens awaiting its PO in one transaction, with both audit events. The
  PO case takes the PIC, Category and supplier of the row read here, a stamp
  never looked up again; the request names none of them. Then the holders of
  `create_po`'s duty in the workspace are told there is a PO to create.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Protocol

from dw_kernel.errors import DomainError, NotFoundError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.pagination import Page, page_request
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import AuthorizationPort, PolicyOverridePort
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.action_duties import SupplyChainActionDuties
from dw_supply_chain.application.handlers import (
    PRODUCT_CASE_READ,
    PRODUCT_CASE_WRITE,
    duty_scope,
    notify_duty_holders,
    po_case_link,
    resolve_action_duties,
    resolve_product_action_duties,
)
from dw_supply_chain.application.ports import (
    PendingApprovalRecord,
    PendingApprovalsPort,
    ProductApprovalPort,
    ProductCaseListFilter,
    ProductCaseRepositoryPort,
    ReviewNotifierPort,
    ReviewRaise,
    ReviewRequester,
    ScopeHoldersPort,
)
from dw_supply_chain.application.product_case_audit import (
    PRODUCT_CASE_RESOURCE,
    product_case_audit,
)
from dw_supply_chain.application.product_reviews import AWAITED_APPROVAL_TYPE
from dw_supply_chain.domain.case_document import CaseDocument, CaseDocumentId
from dw_supply_chain.domain.po_case import CaseAction, POCase, POCaseId
from dw_supply_chain.domain.product_development_case import (
    AWAITING_APPROVAL_STATES,
    COMMAND_ONLY_ACTIONS,
    GRAPH_ONLY_ACTIONS,
    ProductAction,
    ProductActionInput,
    ProductCaseTransition,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
    SampleRound,
    SkuDraft,
    apply_product_action,
    document_refusal,
)
from dw_supply_chain.domain.product_proposal import DraftClaim, ProposalOrigin
from dw_supply_chain.product_action_duties import SupplyChainProductActionDuties
from dw_supply_chain.workflows.advance_product_case_graph import BOD_REVIEW_CASE_KEY

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
    """A case (with its item code and SKUs), its sample rounds, the tenant's
    step-to-duty mapping the case page shows who may take each step from (the
    same mapping `AdvanceProductCase` authorizes against, resolved by the same
    function), and, while it waits for BGĐ or for its sign-off, the approval
    it waits on; once ordered, the PO case ĐẶT HÀNG opened."""

    case: ProductDevelopmentCase
    rounds: list[SampleRound]
    duties: SupplyChainProductActionDuties
    pending_review: PendingApprovalRecord | None = None
    po_case_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ProductStepResult:
    """A step taken, and for a step that left the case waiting on an approval
    (BGĐ's review, the sign-off), what asking for it did (None for every
    other step)."""

    case: ProductDevelopmentCase
    review: ReviewRaise | None = None


async def _case_in_workspace(
    repo: ProductCaseRepositoryPort, context: AccessContext, case_id: ProductDevelopmentCaseId
) -> ProductDevelopmentCase:
    case = await repo.get(context, case_id)
    if (
        case is None
        or case.tenant_id.value != context.tenant_id
        or case.workspace_id.value != context.workspace_id
    ):
        raise NotFoundError("product case not found", details={"case_id": str(case_id)})
    return case


@dataclass(frozen=True)
class ProposeProductCase:
    """Step 1: Cung ứng proposes a product and becomes its PIC.

    Tenant and workspace come from the verified context, and so does the PIC:
    `handle` has no parameter that could name another person. Gated twice,
    both before anything is read: the context's write, as `CreatePOCase` is
    gated (lead decision 9), and the duty the tenant's policy gives
    `propose` (decision 7). `propose_scopes` is that set, the one answer both
    this handler and a chat command's ceiling read (ADR 0012 condition 2).

    The web route and the Zalo proposal command both call `handle`. The chat
    passes two things the route does not, neither of which decides anything
    about the case: `origin` (channel and chat reference, for the audit record
    only) and `consume` (the draft the case is made from, deleted in the case's
    own transaction, guarded on its summarised version)."""

    repo: ProductCaseRepositoryPort
    authz: AuthorizationPort
    policy_override_repo: PolicyOverridePort
    platform_default_duties: SupplyChainProductActionDuties
    ids: IdGenerator
    clock: UtcClock

    async def propose_scopes(self, context: AccessContext) -> frozenset[str]:
        """Every scope proposing needs in the caller's tenant: the write, and
        the scope of the duty the tenant's own policy gives `propose`."""
        duties = await resolve_product_action_duties(
            context, self.policy_override_repo, self.platform_default_duties
        )
        return frozenset({PRODUCT_CASE_WRITE, duty_scope(duties.duty_for(ProductAction.PROPOSE))})

    async def handle(
        self,
        context: AccessContext,
        *,
        proposal_code: str,
        product_name: str,
        category: str,
        origin: ProposalOrigin | None = None,
        consume: DraftClaim | None = None,
    ) -> ProductDevelopmentCase:
        # The write first, before the tenant's policy is even read.
        await self.authz.require(
            context=context, action=PRODUCT_CASE_WRITE, resource_type=_RESOURCE
        )
        for scope in sorted(await self.propose_scopes(context)):
            await self.authz.require(context=context, action=scope, resource_type=_RESOURCE)
        case = ProductDevelopmentCase.propose(
            id=ProductDevelopmentCaseId(self.ids.new_uuid()),
            tenant_id=TenantId(context.tenant_id),
            workspace_id=WorkspaceId(context.workspace_id),
            proposal_code=proposal_code,
            product_name=product_name,
            category=category,
            actor_id=context.principal_id,
        )
        details: dict[str, object] = {
            "proposal_code": case.proposal_code,
            "category": case.category,
        }
        if origin is not None:
            details["origin"] = {"channel": origin.channel, "chat_ref": origin.chat_ref}
        await self.repo.add(
            context,
            case,
            audit=product_case_audit(
                context, self.ids, self.clock, case, ProductAction.PROPOSE.value, details
            ),
            consume=consume,
        )
        return case


@dataclass(frozen=True)
class GetProductCase:
    """The case page. While the case waits for BGĐ or for its sign-off it
    carries the approval it waits on, read from the approval inbox in the
    caller's workspace and narrowed to who may see it: its id, when it was
    raised, the scope stamped on it (who may decide) and, for a sign-off, its
    step and the steps of the round. Nobody's name, and no way to decide from
    here."""

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
                    approval_type=AWAITED_APPROVAL_TYPE[case.state],
                    key=BOD_REVIEW_CASE_KEY,
                    value=str(case.id),
                )
                if case.state in AWAITING_APPROVAL_STATES
                else None
            ),
            po_case_id=(
                await self.repo.po_case_of(context, case.id.value)
                if case.state is ProductDevState.ORDERED
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
    learns nothing about either. A step that leaves the case waiting on an
    approval then asks for it (module docstring)."""

    repo: ProductCaseRepositoryPort
    documents: ProductDocumentLookupPort
    authz: AuthorizationPort
    policy_override_repo: PolicyOverridePort
    platform_default_duties: SupplyChainProductActionDuties
    reviews: ProductApprovalPort
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
        item_code: str | None = None,
        sku: SkuDraft | None = None,
        sku_id: uuid.UUID | None = None,
    ) -> ProductStepResult:
        if action in GRAPH_ONLY_ACTIONS:
            raise DomainError(
                "an approval's outcome is decided on the approval, not as a step on the case",
                details={"action": action.value},
            )
        if action in COMMAND_ONLY_ACTIONS:
            raise DomainError(
                f"{action.value} is taken through its own command, not as a plain step",
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
                item_code=item_code,
                sku=sku,
                sku_id=sku_id,
                new_id=self.ids.new_uuid(),
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
                    **_coding_details(case, action, sku_id),
                },
            ),
        )
        if case.state not in AWAITING_APPROVAL_STATES:
            return ProductStepResult(case=case)
        return ProductStepResult(case=case, review=await self._ensure_review(context, case))

    async def _ensure_review(
        self, context: AccessContext, case: ProductDevelopmentCase
    ) -> ReviewRaise:
        """The step is saved whatever happens here. Broad on purpose: a quota
        refusal, a spend ceiling or a failed start must not turn a recorded
        step into an error; the reconcile lane raises the approval later."""
        try:
            return await self.reviews.ensure(context, case, ReviewRequester.from_context(context))
        except Exception:
            logger.exception(
                "approval not raised after the step; the reconcile lane retries",
                extra={"case_id": str(case.id)},
            )
            return ReviewRaise.NOT_RAISED


@dataclass(frozen=True, slots=True)
class OrderPlaced:
    """ĐẶT HÀNG done: the case, now `ordered`, and the PO case it opened."""

    case: ProductDevelopmentCase
    po_case: POCase


@dataclass(frozen=True)
class PlaceOrder:
    """ĐẶT HÀNG (module docstring). Who may is `place_order`'s duty under the
    tenant's product policy, checked before the case is read; another
    tenant's or workspace's case is not found. A second click, or a click on
    a case that moved on, is a 409 and opens nothing: the repository's
    conditional UPDATE answers it, whatever the case looked like when read."""

    repo: ProductCaseRepositoryPort
    authz: AuthorizationPort
    policy_override_repo: PolicyOverridePort
    platform_default_duties: SupplyChainProductActionDuties
    platform_default_action_duties: SupplyChainActionDuties
    holders: ScopeHoldersPort
    notifier: ReviewNotifierPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(
        self, context: AccessContext, *, case_id: ProductDevelopmentCaseId
    ) -> OrderPlaced:
        duties = await resolve_product_action_duties(
            context, self.policy_override_repo, self.platform_default_duties
        )
        await self.authz.require(
            context=context,
            action=duty_scope(duties.duty_for(ProductAction.PLACE_ORDER)),
            resource_type=_RESOURCE,
            resource_id=str(case_id),
        )
        case = await _case_in_workspace(self.repo, context, case_id)
        before = case.state
        po_case = case.place_order(
            actor_id=context.principal_id, po_case_id=POCaseId(self.ids.new_uuid())
        )
        await self.repo.place_order(
            context,
            case,
            po_case,
            audits=(
                product_case_audit(
                    context,
                    self.ids,
                    self.clock,
                    case,
                    ProductAction.PLACE_ORDER.value,
                    {
                        "from_state": before.value,
                        "to_state": case.state.value,
                        "po_case_id": str(po_case.id),
                    },
                ),
                AuditEvent(
                    id=self.ids.new_uuid(),
                    tenant_id=TenantId(context.tenant_id),
                    workspace_id=WorkspaceId(context.workspace_id),
                    actor_id=UserId(context.principal_id),
                    action="supply_chain.po_case.order_requested",
                    resource_type="po_case",
                    resource_id=str(po_case.id),
                    occurred_at=self.clock.now(),
                    details={
                        "product_dev_case_id": str(case.id),
                        "pic_user_id": str(po_case.pic_user_id),
                        "category": po_case.category or "",
                        "lines": len(po_case.lines),
                    },
                ),
            ),
        )
        action_duties = await resolve_action_duties(
            context, self.policy_override_repo, self.platform_default_action_duties
        )
        await notify_duty_holders(
            context,
            holders=self.holders,
            notifier=self.notifier,
            duty=action_duties.duty_for(CaseAction.CREATE_PO),
            source_key=f"supply_chain.order_requested:{po_case.id}",
            title=f"Chờ tạo PO: {case.product_name}",
            body=(
                f"Sản phẩm {case.product_name} ({case.proposal_code}) đã được ĐẶT HÀNG;"
                " Hồ sơ PO chờ tạo PO (bước 10)."
            ),
            link=po_case_link(po_case.id.value),
        )
        return OrderPlaced(case=case, po_case=po_case)


def _coding_details(
    case: ProductDevelopmentCase, action: ProductAction, sku_id: uuid.UUID | None
) -> dict[str, object]:
    """What a step-9 coding step's audit event names: the code it issued, the
    SKU it added or removed."""
    if action is ProductAction.ISSUE_ITEM_CODE and case.item_code is not None:
        return {"item_code": case.item_code.code}
    if action is ProductAction.ADD_SKU and case.skus:
        return {"sku_code": case.skus[-1].sku_code}
    if action is ProductAction.REMOVE_SKU and sku_id is not None:
        return {"sku_id": str(sku_id)}
    return {}
