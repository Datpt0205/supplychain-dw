"""Step 12's colour, packaging and pre-production sub-flow: read it, take a step
of it, and read or set the tenant's packaging policy (slice PK).

What each command decides, and where:

- **Who:** each step needs the scope of its duty under the tenant's PO
  step-to-duty policy (`resolve_action_duties`, 1.2.0 gives the sub-flow its
  duties), checked before the case or any document is read. Reading needs
  `supply_chain.po_case.read`. The page is told, per step, whether the caller
  may take it, by the same check the step runs (`allowed`), never by a second
  rule in the browser.
- **Which case:** read under the caller's tenant AND workspace; another's case
  is not found, never forbidden. Whether it is in `pre_production` is the
  domain's rule, and the repository re-checks it in the saving transaction.
- **The paper:** a test step takes the report the caller named, read under the
  caller's RLS; whether it is this case's report since the sample is the
  domain's rule (`PackagingDesign._step_document`). A named document the caller
  cannot read is the same refusal as one never named.
- **The record:** the design row, its history row and the audit event are one
  transaction. Approving the colour writes "đã báo TP MKT" into the history and
  tells the case's PIC, after the step is saved.
- **The packaging policy:** read and set with the step-to-duty scopes (the rule
  decides when a step may be taken, as the duty mapping decides by whom); a
  tenant's override replaces the document whole and is audited.
- **MKT** (ADR 0028; ticket ai-automation/16): where the tenant's packaging
  policy requires the packaging content, approving the colour writes no "đã
  báo TP MKT" line; sending MKT its pack tells the holders of MKT's duty (the
  duty of `submit_packaging_content` under the tenant's policy), and MKT's
  submission tells the holders of the design's duty. The page lists the pack:
  the product's BM04, the user manual and the maquette, each the newest of its
  type (or missing). Elsewhere MKT's two steps are not offered at all.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from dw_kernel.errors import NotFoundError, PermissionDeniedError
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import AuthorizationPort, PolicyOverridePort
from dw_supply_chain.action_duties import CaseDuty, SupplyChainActionDuties
from dw_supply_chain.application.handlers import (
    ACTION_DUTIES_READ,
    ACTION_DUTIES_WRITE,
    PO_CASE_READ,
    _put_policy_override,
    duty_scope,
    notify_duty_holders,
    po_case_link,
    resolve_action_duties,
)
from dw_supply_chain.application.po_case_audit import PO_CASE_RESOURCE, po_case_audit
from dw_supply_chain.application.ports import (
    PackagingDesignRepositoryPort,
    POCaseRepositoryPort,
    ReviewNotifierPort,
    ScopeHoldersPort,
)
from dw_supply_chain.application.product_cases import ProductDocumentLookupPort
from dw_supply_chain.application.production_gate import resolve_packaging_policy
from dw_supply_chain.application.step_preparation import CaseDocumentListPort
from dw_supply_chain.domain.case_document import CaseDocumentId, CaseKind, DocumentType
from dw_supply_chain.domain.packaging_design import (
    ACTION_DOCUMENT_TYPE,
    MKT_ACTIONS,
    REASON_REQUIRED_ACTIONS,
    PackagingAction,
    PackagingDesign,
    PackagingHistoryEntry,
    document_refusal,
)
from dw_supply_chain.domain.po_case import CaseState, POCase, POCaseId
from dw_supply_chain.packaging_policy import PACKAGING_POLICY_ID, SupplyChainPackagingPolicy

logger = logging.getLogger(__name__)

_PACKAGING_POLICY_RESOURCE = "packaging_policy"

# Who is told when MKT's hand-off moves (ADR 0028): the holders of the duty of
# the next step, under the tenant's own policy; no price, no content.
MKT_HAND_OFF: dict[PackagingAction, tuple[PackagingAction, str, str]] = {
    PackagingAction.SEND_MKT_PACK: (
        PackagingAction.SUBMIT_PACKAGING_CONTENT,
        "Gói làm bao bì đã gửi MKT",
        "Cung ứng đã gửi BM04, HDSD, maquette; MKT nộp nội dung bao bì trên hồ sơ PO.",
    ),
    PackagingAction.SUBMIT_PACKAGING_CONTENT: (
        PackagingAction.APPROVE_DESIGN,
        "MKT đã nộp nội dung bao bì",
        "Nội dung bao bì đã có trên hồ sơ PO; Cung ứng kiểm bản in thiết kế rồi duyệt.",
    ),
}


@dataclass(frozen=True, slots=True)
class PackagingStepView:
    """One step as the caller sees it: whose duty it is, whether the caller
    holds that duty, and what it needs."""

    action: PackagingAction
    duty: CaseDuty
    allowed: bool
    requires_reason: bool
    requires_document: bool


@dataclass(frozen=True, slots=True)
class PackItem:
    """One paper of MKT's pack: its type and the newest document of it, if any."""

    doc_type: DocumentType
    document_id: uuid.UUID | None


# What MKT receives (ADR 0028): the BM04 lives on the product case, the user
# manual and the maquette on the PO case.
MKT_PACK: tuple[tuple[CaseKind, DocumentType], ...] = (
    (CaseKind.PRODUCT, DocumentType.PRODUCT_PROFILE_BM04),
    (CaseKind.PO, DocumentType.USER_MANUAL),
    (CaseKind.PO, DocumentType.MAQUETTE),
)


@dataclass(frozen=True, slots=True)
class PackagingDesignDetail:
    design: PackagingDesign
    case_state: CaseState
    require_pre_production_test: bool
    steps: tuple[PackagingStepView, ...]
    history: tuple[PackagingHistoryEntry, ...]
    require_packaging_content: bool = False
    pack: tuple[PackItem, ...] = ()


async def _case_in_workspace(
    repo: POCaseRepositoryPort, context: AccessContext, case_id: POCaseId
) -> POCase:
    case = await repo.get(context, case_id)
    if (
        case is None
        or case.tenant_id.value != context.tenant_id
        or case.workspace_id.value != context.workspace_id
    ):
        raise NotFoundError("PO case not found", details={"case_id": str(case_id)})
    return case


def _fresh(case: POCase) -> PackagingDesign:
    return PackagingDesign(
        po_case_id=case.id.value, tenant_id=case.tenant_id, workspace_id=case.workspace_id
    )


@dataclass(frozen=True)
class GetPackagingDesign:
    po_cases: POCaseRepositoryPort
    designs: PackagingDesignRepositoryPort
    authz: AuthorizationPort
    policy_override_repo: PolicyOverridePort
    platform_default_duties: SupplyChainActionDuties
    platform_default_policy: SupplyChainPackagingPolicy
    documents: CaseDocumentListPort

    async def handle(self, context: AccessContext, po_case_id: POCaseId) -> PackagingDesignDetail:
        await self.authz.require(
            context=context,
            action=PO_CASE_READ,
            resource_type=PO_CASE_RESOURCE,
            resource_id=str(po_case_id),
        )
        case = await _case_in_workspace(self.po_cases, context, po_case_id)
        design = await self.designs.get(context, case.id.value) or _fresh(case)
        history = await self.designs.history(context, case.id.value)
        duties = await resolve_action_duties(
            context, self.policy_override_repo, self.platform_default_duties
        )
        policy = await resolve_packaging_policy(
            context, self.policy_override_repo, self.platform_default_policy
        )
        steps = []
        for action in PackagingAction:
            if action in MKT_ACTIONS and not policy.require_packaging_content:
                continue
            duty = duties.duty_for(action)
            steps.append(
                PackagingStepView(
                    action=action,
                    duty=duty,
                    allowed=await self._may(context, duty, case),
                    requires_reason=action in REASON_REQUIRED_ACTIONS,
                    requires_document=action in ACTION_DOCUMENT_TYPE,
                )
            )
        return PackagingDesignDetail(
            design=design,
            case_state=case.state,
            require_pre_production_test=policy.require_pre_production_test,
            steps=tuple(steps),
            history=tuple(history),
            require_packaging_content=policy.require_packaging_content,
            pack=await self._pack(context, case) if policy.require_packaging_content else (),
        )

    async def _pack(self, context: AccessContext, case: POCase) -> tuple[PackItem, ...]:
        cases = {CaseKind.PO: case.id.value, CaseKind.PRODUCT: case.product_dev_case_id}
        items = []
        for kind, doc_type in MKT_PACK:
            case_id = cases[kind]
            mine = (
                []
                if case_id is None
                else [
                    d
                    for d in await self.documents.list_for_case(context, kind, case_id)
                    if d.doc_type is doc_type and d.case_id == case_id
                ]
            )
            newest = max(mine, key=lambda d: d.version) if mine else None
            items.append(PackItem(doc_type, None if newest is None else newest.id.value))
        return tuple(items)

    async def _may(self, context: AccessContext, duty: CaseDuty, case: POCase) -> bool:
        # The step's own check, asked rather than re-derived.
        try:
            await self.authz.require(
                context=context,
                action=duty_scope(duty),
                resource_type=PO_CASE_RESOURCE,
                resource_id=str(case.id),
            )
        except PermissionDeniedError:
            return False
        return True


@dataclass(frozen=True)
class TakePackagingStep:
    po_cases: POCaseRepositoryPort
    designs: PackagingDesignRepositoryPort
    documents: ProductDocumentLookupPort
    authz: AuthorizationPort
    policy_override_repo: PolicyOverridePort
    platform_default_duties: SupplyChainActionDuties
    platform_default_policy: SupplyChainPackagingPolicy
    holders: ScopeHoldersPort
    notifier: ReviewNotifierPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(
        self,
        context: AccessContext,
        *,
        po_case_id: POCaseId,
        action: PackagingAction,
        reason: str | None = None,
        document_id: uuid.UUID | None = None,
    ) -> PackagingDesign:
        duties = await resolve_action_duties(
            context, self.policy_override_repo, self.platform_default_duties
        )
        await self.authz.require(
            context=context,
            action=duty_scope(duties.duty_for(action)),
            resource_type=PO_CASE_RESOURCE,
            resource_id=str(po_case_id),
        )
        case = await _case_in_workspace(self.po_cases, context, po_case_id)
        document = None
        if document_id is not None:
            document = await self.documents.get(context, CaseDocumentId(document_id))
            if document is None:
                raise document_refusal(case.id.value, action)
        design = await self.designs.get(context, case.id.value) or _fresh(case)
        policy = await resolve_packaging_policy(
            context, self.policy_override_repo, self.platform_default_policy
        )
        mkt = policy.require_packaging_content
        now: datetime = self.clock.now()
        design.take(
            action,
            case_in_pre_production=case.state is CaseState.PRE_PRODUCTION,
            reason=reason,
            document=document,
            now=now,
            mkt_required=mkt,
        )
        await self.designs.save(
            context,
            design,
            actor_id=context.principal_id,
            audit=po_case_audit(
                context,
                self.ids,
                self.clock,
                case.id,
                action.value,
                {
                    "colour_status": design.colour_status.value,
                    "design_status": design.design_status.value,
                    "pre_production_test": design.pre_production_test.value,
                    **({"document_id": str(document.id)} if document else {}),
                    **({"reason": reason.strip()} if reason and reason.strip() else {}),
                },
            ),
        )
        if action in MKT_HAND_OFF:
            duty_action, title, body = MKT_HAND_OFF[action]
            await notify_duty_holders(
                context,
                holders=self.holders,
                notifier=self.notifier,
                duty=duties.duty_for(duty_action),
                source_key=f"supply_chain.{action.value}:{case.id}:{design.version}",
                title=f"{title}: {case.supplier_name}",
                body=body,
                link=po_case_link(case.id.value),
            )
        elif action is PackagingAction.APPROVE_COLOUR and mkt and case.pic_user_id is not None:
            try:
                await self.notifier.deliver(
                    context,
                    recipients=[case.pic_user_id],
                    source_key=f"supply_chain.colour_approved:{case.id}",
                    title="Màu đã được duyệt",
                    body="Cung ứng đã duyệt mẫu màu; bước tiếp theo là gửi gói cho MKT.",
                    link=po_case_link(case.id.value),
                )
            except Exception:
                logger.exception("packaging: the PIC was not told of the colour approval")
        elif action is PackagingAction.APPROVE_COLOUR and case.pic_user_id is not None:
            # Told after the step is saved; a failed notice never undoes it.
            try:
                await self.notifier.deliver(
                    context,
                    recipients=[case.pic_user_id],
                    source_key=f"supply_chain.colour_approved:{case.id}",
                    title="Màu đã được duyệt, đã báo TP MKT",
                    body=(
                        "Cung ứng đã duyệt mẫu màu và báo Trưởng phòng MKT; "
                        "bước tiếp theo là thiết kế bao bì."
                    ),
                    link=po_case_link(case.id.value),
                )
            except Exception:
                logger.exception("packaging: the PIC was not told of the colour approval")
        return design


@dataclass(frozen=True)
class GetPackagingPolicy:
    policy_override_repo: PolicyOverridePort
    platform_default_policy: SupplyChainPackagingPolicy
    authz: AuthorizationPort

    async def handle(self, context: AccessContext) -> SupplyChainPackagingPolicy:
        await self.authz.require(
            context=context, action=ACTION_DUTIES_READ, resource_type=_PACKAGING_POLICY_RESOURCE
        )
        return await resolve_packaging_policy(
            context, self.policy_override_repo, self.platform_default_policy
        )


@dataclass(frozen=True)
class SetPackagingPolicyOverride:
    policy_override_repo: PolicyOverridePort
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(self, context: AccessContext, policy: SupplyChainPackagingPolicy) -> None:
        await self.authz.require(
            context=context, action=ACTION_DUTIES_WRITE, resource_type=_PACKAGING_POLICY_RESOURCE
        )
        await _put_policy_override(
            context,
            self.policy_override_repo,
            policy_id=PACKAGING_POLICY_ID,
            policy=policy,
            resource_type=_PACKAGING_POLICY_RESOURCE,
            ids=self.ids,
            clock=self.clock,
        )
