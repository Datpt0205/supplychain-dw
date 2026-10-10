"""Messages to a supplier: AI drafts, a person sends (ADR 0029, E18; ticket
ai-automation/07).

Three doors, each deciding where its effect happens:

- **`DraftSupplierMessage`**, the lane's door (no route reaches it): one
  message for one trigger (`source_key`), drafted at most once. In order, and
  what each step refuses:
  1. a trigger already drafted costs one read and no call;
  2. the case is read under the caller's tenant AND workspace (RLS) and must
     be in that workspace, or nothing is called and nothing written (a case of
     another tenant or workspace is no case here);
  3. the tenant's plan, read by the platform; none gets no call (fail closed);
  4. the evidence: the case's own facts, the trigger (a follow-up), the
     reply-by date code computed, and the approved documents it attaches;
     never a price (the lane holds no `supply_chain.commercial.read`, and a
     drafter who did not hold it would not get one either), never a bank
     account;
  5. one structured call through the process's one-call gateway (the plan's
     daily allowance and the spend ledger), then code keeps the paragraphs that
     check out (`domain.grounded_writing`) between the template's own greeting,
     reply-by line and closing;
  6. one append-only row with its audit, and the case's PIC told there is a
     draft: the case's code and the purpose, never the text or a price.
- **`ListSupplierMessages`** (`supply_chain.document.read`): a case's
  messages, each with who said it was sent.
- **`MarkSupplierMessageSent`** (`supply_chain.document.write`): "Đã gửi",
  once per message, naming the text the person copied (`content_sha256`); a
  message with no body cannot be sent.

Nothing here sends anything anywhere: there is no mailbox, no SMTP and no
`external` tool (`test_no_outbound_mail.py` holds the package to it).

**`DraftSupplierMessages`** is the worker lane `supply_chain_supplier_messages`:
for each workspace whose tenant's step preparation policy lists purposes, it
drafts a reminder for each open supplier-side follow-up, and a sample request
or a confirmation for each case that entered the step (once per history row).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.model.budget import BudgetExceededError
from dw_agent_runtime.ports import ModelGateway, ModelOutputInvalidError
from dw_kernel.errors import (
    ConflictError,
    DomainError,
    InfrastructureError,
    NotFoundError,
    QuotaExceededError,
)
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.pagination import MAX_PAGE_SIZE, Page, PageRequest, page_request
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import AuthorizationPort, PolicyOverridePort
from dw_platform.domain.audit import AuditEvent, lane_audit_event, system_actor
from dw_supply_chain.application.bm04_prefill import Bm04ProfileReadPort
from dw_supply_chain.application.case_documents import CaseLookups
from dw_supply_chain.application.handlers import (
    DOCUMENT_READ,
    DOCUMENT_WRITE,
    po_case_link,
    product_case_link,
)
from dw_supply_chain.application.ports import (
    FollowUpRecord,
    LineReceiptsPort,
    POCaseListFilter,
    ProductCaseListFilter,
    ReviewNotifierPort,
    TenantPlanPort,
)
from dw_supply_chain.application.step_preparation import (
    CaseDocumentListPort,
    CaseDraftsPort,
    CaseListingPort,
    ExtractionReadingsPort,
    entered_current_state,
    resolve_step_preparation,
)
from dw_supply_chain.domain.case_document import CaseKind, DocumentType
from dw_supply_chain.domain.commercial import PRICE_FIELDS, ProductProfile, SupplierContact
from dw_supply_chain.domain.document_draft import DraftStatus, field_value
from dw_supply_chain.domain.extraction import ExtractionStatus
from dw_supply_chain.domain.follow_up import FollowUpKind
from dw_supply_chain.domain.grounded_writing import EvidenceItem, ground_sentences
from dw_supply_chain.domain.po_case import CaseState, POCase, POCaseId, reference_label
from dw_supply_chain.domain.product_development_case import (
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
)
from dw_supply_chain.domain.receipt_check import discrepancies
from dw_supply_chain.domain.supplier_message import (
    SUPPLIER_SIDE_KINDS,
    SUPPLIER_SIDE_MILESTONES,
    MessagePurpose,
    MessageStatus,
    SupplierMessageWriting,
    compose,
    reply_by,
    vn_date,
)
from dw_supply_chain.step_preparation_policy import SupplyChainStepPreparation
from dw_supply_chain.supplier_message_policy import (
    SupplyChainSupplierMessages,
    resolve_supplier_messages,
)
from dw_supply_chain.workflows.grounded_writing import WritingPrompt, write_with_evidence

logger = logging.getLogger(__name__)

MESSAGES_LANE = "supply_chain_supplier_messages"
MESSAGES_WORKER_ID = "supply_chain.supplier_messages"
MESSAGES_WORKER_VERSION = "1.0.0"
MESSAGE_PROMPT = WritingPrompt("supply_chain.draft_supplier_message", "1.3.0")
MESSAGE_DRAFTED = "supply_chain.supplier_message.drafted"
MESSAGE_SENT = "supply_chain.supplier_message.sent"
_RESOURCE = "supplier_message"
_BATCH = 10

PURPOSE_LABELS: Mapping[MessagePurpose, str] = {
    MessagePurpose.SAMPLE_REQUEST: "Đề nghị gửi mẫu",
    MessagePurpose.SUPPLIER_REMINDER: "Nhắc NCC cập nhật",
    MessagePurpose.SUPPLIER_CONFIRMATION: "Xác nhận sản phẩm với NCC",
    MessagePurpose.SAMPLE_REVISION_REQUEST: "Gửi phiếu yêu cầu chỉnh sửa mẫu",
    MessagePurpose.PRODUCTION_PROGRESS: "Hỏi tiến độ sản xuất hằng tuần",
    MessagePurpose.DISCREPANCY_CLAIM: "Khiếu nại chênh lệch hàng nhập kho",
}
# The step a case enters that a message of this purpose goes with.
_STEP_PURPOSES: Mapping[MessagePurpose, ProductDevState] = {
    MessagePurpose.SAMPLE_REQUEST: ProductDevState.SAMPLE_REQUESTED,
    MessagePurpose.SUPPLIER_CONFIRMATION: ProductDevState.SUPPLIER_CONFIRMATION,
}


def lane_actor() -> uuid.UUID:
    return system_actor(MESSAGES_LANE).value


def lane_context(tenant_id: uuid.UUID, workspace_id: uuid.UUID) -> AccessContext:
    """The lane in one workspace: its own actor, no role, no scope."""
    return AccessContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        principal_id=lane_actor(),
        roles=frozenset(),
        scopes=frozenset(),
        plan_id="system",
    )


# ------------------------------------------------------------------- model --


@dataclass(frozen=True, slots=True)
class SupplierMessage:
    id: uuid.UUID
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    case_kind: CaseKind
    case_id: uuid.UUID
    purpose: MessagePurpose
    status: MessageStatus
    source_key: str
    supplier_name: str | None
    recipient_name: str | None
    recipient_email: str | None
    subject: str
    body: str
    attachments: tuple[uuid.UUID, ...]
    citations: tuple[Mapping[str, Any], ...]
    dropped: int
    template_version: str
    prompt_id: str
    prompt_version: str
    model_profile: str | None
    content_sha256: str
    created_by: uuid.UUID
    created_at: datetime
    sent_by: uuid.UUID | None = None
    sent_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class NewSupplierMessage:
    id: uuid.UUID
    case_kind: CaseKind
    case_id: uuid.UUID
    purpose: MessagePurpose
    status: MessageStatus
    source_key: str
    supplier_name: str | None
    recipient_name: str | None
    recipient_email: str | None
    subject: str
    body: str
    attachments: tuple[uuid.UUID, ...]
    citations: tuple[Mapping[str, Any], ...]
    dropped: int
    template_version: str
    prompt_id: str
    prompt_version: str
    model_profile: str | None
    content_sha256: str


@dataclass(frozen=True, slots=True)
class NewMessageSend:
    id: uuid.UUID
    message_id: uuid.UUID
    content_sha256: str


# ------------------------------------------------------------------- ports --


class SupplierMessageRepositoryPort(Protocol):
    async def has_source(self, context: AccessContext, source_key: str) -> bool: ...

    async def add(
        self, context: AccessContext, message: NewSupplierMessage, *, audit: AuditEvent
    ) -> None:
        """Raises `ConflictError` when the workspace already has a message for
        this `source_key` (another tick drafted it first)."""
        ...

    async def get(
        self, context: AccessContext, message_id: uuid.UUID
    ) -> SupplierMessage | None: ...

    async def list_for_case(
        self, context: AccessContext, case_kind: CaseKind, case_id: uuid.UUID
    ) -> list[SupplierMessage]: ...

    async def mark_sent(
        self, context: AccessContext, send: NewMessageSend, *, audit: AuditEvent
    ) -> None:
        """Raises `ConflictError` when the message was already marked sent."""
        ...


class SupplierContactLookupPort(Protocol):
    """The supplier's current contact, found by the case's supplier name in
    the caller's workspace (the directory's normalised name)."""

    async def contact_for(
        self, context: AccessContext, supplier_name: str
    ) -> SupplierContact | None: ...


class ProductCaseGetPort(Protocol):
    async def get(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> ProductDevelopmentCase | None: ...


class POCaseGetPort(Protocol):
    async def get(self, context: AccessContext, case_id: POCaseId) -> POCase | None: ...


class POCaseListingPort(Protocol):
    """The workspace's PO cases in one state (ticket ai-automation/17)."""

    async def list_page(
        self, context: AccessContext, request: PageRequest, case_filter: POCaseListFilter
    ) -> Page[POCase]: ...


class OpenFollowUpsPort(Protocol):
    async def list_open(self, context: AccessContext) -> list[FollowUpRecord]: ...


class WorkspacesPort(Protocol):
    async def workspaces(self) -> list[tuple[uuid.UUID, uuid.UUID]]: ...


# ----------------------------------------------------------------- drafting --


@dataclass(frozen=True, slots=True)
class MessageRequest:
    """One message to draft: the case, why, and what drafted it."""

    case_kind: CaseKind
    case_id: uuid.UUID
    purpose: MessagePurpose
    source_key: str
    # What the trigger adds to the evidence (a follow-up, a revision request).
    evidence: tuple[EvidenceItem, ...] = ()
    # Approved documents the message goes with: named, never their bytes.
    attachments: tuple[uuid.UUID, ...] = ()


class DraftOutcome(StrEnum):
    DRAFTED = "drafted"
    REFUSED = "refused"
    FAILED = "failed"
    ALREADY = "already"
    NO_CASE = "no_case"
    DEFERRED = "deferred"


@dataclass(frozen=True, slots=True)
class _Subject:
    reference: str
    product: str
    supplier_name: str | None
    pic: uuid.UUID | None
    link: str
    facts: str


def _product_subject(case: ProductDevelopmentCase) -> _Subject:
    facts = [
        f"Mã đề xuất: {case.proposal_code}",
        f"Sản phẩm: {case.product_name}",
        f"Nhóm sản phẩm: {case.category}",
    ]
    if case.supplier_name:
        facts.append(f"Nhà cung cấp: {case.supplier_name}")
    if case.sample_round:
        facts.append(f"Vòng mẫu: {case.sample_round}")
    return _Subject(
        reference=case.proposal_code,
        product=case.product_name,
        supplier_name=case.supplier_name,
        pic=case.pic_user_id,
        link=product_case_link(case.id.value),
        facts="; ".join(facts),
    )


def _po_subject(case: POCase) -> _Subject:
    reference = reference_label(case.po_reference)
    return _Subject(
        reference=reference,
        product=case.supplier_name,
        supplier_name=case.supplier_name,
        pic=case.pic_user_id,
        link=po_case_link(case.id.value),
        facts=f"Đơn hàng: {reference}; Nhà cung cấp: {case.supplier_name}",
    )


@dataclass(frozen=True)
class DraftSupplierMessage:
    product_cases: ProductCaseGetPort
    po_cases: POCaseGetPort
    contacts: SupplierContactLookupPort
    messages: SupplierMessageRepositoryPort
    plans: TenantPlanPort
    gateway: ModelGateway
    policy_override_repo: PolicyOverridePort
    platform_default_templates: SupplyChainSupplierMessages
    notifier: ReviewNotifierPort
    ids: IdGenerator
    clock: UtcClock
    lane: str = MESSAGES_LANE
    worker_id: str = MESSAGES_WORKER_ID
    worker_version: str = MESSAGES_WORKER_VERSION
    # None is the deployment's own profile (`luna` for Elmich).
    model_profile: str | None = None

    async def draft(self, context: AccessContext, request: MessageRequest) -> DraftOutcome:
        if await self.messages.has_source(context, request.source_key):
            return DraftOutcome.ALREADY
        subject = await self._subject(context, request)
        if subject is None:
            logger.warning("supplier message refused: case not in the caller's workspace")
            return DraftOutcome.NO_CASE
        plan = await self.plans.plan_of(context.tenant_id)
        if plan is None:
            return DraftOutcome.DEFERRED
        templates = await resolve_supplier_messages(
            context, self.policy_override_repo, self.platform_default_templates
        )
        template = templates.templates[request.purpose]
        contact = (
            None
            if subject.supplier_name is None
            else await self.contacts.contact_for(context, subject.supplier_name)
        )
        recipient = contact.name if contact is not None else (subject.supplier_name or "Quý NCC")
        due = vn_date(reply_by(self.clock.now().date(), template.reply_within_days))
        words = {
            "reference": subject.reference,
            "product": subject.product,
            "recipient": recipient,
            "reply_by": due,
        }
        evidence = (
            EvidenceItem("case", "Hồ sơ", subject.facts),
            EvidenceItem("reply_by", "Hạn phản hồi", f"Hạn phản hồi: {due}"),
            *request.evidence,
        )
        run_id = self.ids.new_uuid()
        status = MessageStatus.DRAFTED
        kept: tuple[Any, ...] = ()
        dropped = 0
        try:
            writing = await write_with_evidence(
                self.gateway,
                RunContext(
                    run_id=run_id,
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    actor_id=context.principal_id,
                    worker_id=self.worker_id,
                    worker_version=self.worker_version,
                    channel="worker",
                    plan_id=plan,
                    roles=frozenset(),
                    scopes=frozenset(),
                    trace_id=str(run_id),
                    subject_ref=f"{request.case_kind.value}_case:{request.case_id}",
                ),
                MESSAGE_PROMPT,
                SupplierMessageWriting,
                task={
                    "purpose": request.purpose.value,
                    "purpose_label": PURPOSE_LABELS[request.purpose],
                    "recipient": recipient,
                },
                evidence=evidence,
                model_profile=self.model_profile,
            )
            grounded = ground_sentences(writing.paragraphs, evidence)
            kept, dropped = grounded.kept, len(grounded.dropped)
            if not kept:
                status = MessageStatus.REFUSED
        except QuotaExceededError:
            return DraftOutcome.DEFERRED
        except ModelOutputInvalidError:
            status = MessageStatus.REFUSED
        except (InfrastructureError, BudgetExceededError):
            logger.warning("supplier message: the model call failed", exc_info=True)
            status = MessageStatus.FAILED
        composed = compose(
            subject=template.subject.format(**words),
            greeting=templates.greeting.format(**words),
            paragraphs=kept,
            reply_line=templates.reply_line.format(**words),
            closing=templates.closing.format(**words),
            attachments=[str(a) for a in request.attachments],
        )
        message = NewSupplierMessage(
            id=self.ids.new_uuid(),
            case_kind=request.case_kind,
            case_id=request.case_id,
            purpose=request.purpose,
            status=status,
            source_key=request.source_key,
            supplier_name=subject.supplier_name,
            recipient_name=recipient,
            recipient_email=None if contact is None else contact.email,
            subject=composed.subject,
            body=composed.body if status is MessageStatus.DRAFTED else "",
            attachments=request.attachments,
            citations=tuple(k.as_json() for k in kept),
            dropped=dropped,
            template_version=templates.policy_version,
            prompt_id=MESSAGE_PROMPT.prompt_id,
            prompt_version=MESSAGE_PROMPT.prompt_version,
            model_profile=self.model_profile,
            content_sha256=composed.content_sha256,
        )
        try:
            await self.messages.add(context, message, audit=self._audit(context, message))
        except ConflictError:
            return DraftOutcome.ALREADY
        await self._notify(context, subject, message)
        return DraftOutcome(status.value)

    async def _subject(self, context: AccessContext, request: MessageRequest) -> _Subject | None:
        if request.case_kind is CaseKind.PRODUCT:
            product = await self.product_cases.get(
                context, ProductDevelopmentCaseId(request.case_id)
            )
            if product is None or (
                product.tenant_id.value,
                product.workspace_id.value,
            ) != (context.tenant_id, context.workspace_id):
                return None
            return _product_subject(product)
        po = await self.po_cases.get(context, POCaseId(request.case_id))
        if po is None or (po.tenant_id.value, po.workspace_id.value) != (
            context.tenant_id,
            context.workspace_id,
        ):
            return None
        return _po_subject(po)

    def _audit(self, context: AccessContext, message: NewSupplierMessage) -> AuditEvent:
        return lane_audit_event(
            lane=self.lane,
            event_id=self.ids.new_uuid(),
            tenant_id=TenantId(context.tenant_id),
            workspace_id=WorkspaceId(context.workspace_id),
            action=MESSAGE_DRAFTED,
            resource_type=_RESOURCE,
            resource_id=str(message.id),
            occurred_at=self.clock.now(),
            details={
                "case_kind": message.case_kind.value,
                "case_id": str(message.case_id),
                "purpose": message.purpose.value,
                "status": message.status.value,
                "source_key": message.source_key,
                "kept": len(message.citations),
                "dropped": message.dropped,
                "prompt": f"{message.prompt_id}@{message.prompt_version}",
            },
        )

    async def _notify(
        self, context: AccessContext, subject: _Subject, message: NewSupplierMessage
    ) -> None:
        """The PIC is told a draft is waiting: the case's code and the purpose,
        never the text and never a price (E15)."""
        if subject.pic is None:
            return
        try:
            await self.notifier.deliver(
                context,
                recipients=[subject.pic],
                source_key=f"supply_chain.supplier_message:{message.id}",
                title=f"AI đã soạn thư gửi NCC: {subject.reference}",
                body=(
                    f"{PURPOSE_LABELS[message.purpose]}. Mở hồ sơ để kiểm, sao chép và gửi"
                    " từ hộp thư của bạn, rồi bấm Đã gửi."
                    if message.status is MessageStatus.DRAFTED
                    else f"{PURPOSE_LABELS[message.purpose]}: AI không soạn được; cần tự viết."
                ),
                link=subject.link,
            )
        except Exception:
            logger.exception("supplier message drafted but its notification failed")


# ---------------------------------------------------------------- reading --


async def _require_case(
    cases: CaseLookups, context: AccessContext, case_kind: CaseKind, case_id: uuid.UUID
) -> None:
    workspace = await cases[case_kind].case_workspace(context, case_id)
    if workspace is None or workspace != context.workspace_id:
        raise NotFoundError(
            "case not found", details={"case_kind": case_kind.value, "case_id": str(case_id)}
        )


@dataclass(frozen=True)
class ListSupplierMessages:
    cases: CaseLookups
    messages: SupplierMessageRepositoryPort
    authz: AuthorizationPort

    async def handle(
        self, context: AccessContext, case_kind: CaseKind, case_id: uuid.UUID
    ) -> list[SupplierMessage]:
        await self.authz.require(
            context=context, action=DOCUMENT_READ, resource_type=_RESOURCE, resource_id=str(case_id)
        )
        await _require_case(self.cases, context, case_kind, case_id)
        return await self.messages.list_for_case(context, case_kind, case_id)


@dataclass(frozen=True)
class MarkSupplierMessageSent:
    """A person says they sent this text from their own mailbox."""

    messages: SupplierMessageRepositoryPort
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(
        self, context: AccessContext, message_id: uuid.UUID, *, content_sha256: str
    ) -> SupplierMessage:
        await self.authz.require(
            context=context,
            action=DOCUMENT_WRITE,
            resource_type=_RESOURCE,
            resource_id=str(message_id),
        )
        message = await self.messages.get(context, message_id)
        if message is None or message.workspace_id != context.workspace_id:
            raise NotFoundError("message not found", details={"message_id": str(message_id)})
        if message.status is not MessageStatus.DRAFTED:
            raise DomainError(
                "thư này không có nội dung AI soạn; hãy tự viết thư",
                details={"status": message.status.value},
            )
        if content_sha256 != message.content_sha256:
            raise ConflictError(
                "nội dung thư đã khác bản bạn sao chép",
                details={"message_id": str(message_id)},
            )
        await self.messages.mark_sent(
            context,
            NewMessageSend(
                id=self.ids.new_uuid(), message_id=message.id, content_sha256=content_sha256
            ),
            audit=AuditEvent(
                id=self.ids.new_uuid(),
                tenant_id=TenantId(context.tenant_id),
                workspace_id=WorkspaceId(context.workspace_id),
                actor_id=UserId(context.principal_id),
                action=MESSAGE_SENT,
                resource_type=_RESOURCE,
                resource_id=str(message.id),
                occurred_at=self.clock.now(),
                details={
                    "case_kind": message.case_kind.value,
                    "case_id": str(message.case_id),
                    "content_sha256": content_sha256,
                },
            ),
        )
        sent = await self.messages.get(context, message_id)
        assert sent is not None
        return sent


# ------------------------------------------------------------------- lane --


def bm04_message_text(profile: ProductProfile) -> str:
    """A BM04 version's terms as a message may cite them: its attributes and
    planning terms, never a price or a currency (E15: no price in a letter)."""
    parts = [
        f"{key}: {value}"
        for key, value in sorted(profile.attributes.items())
        if key not in PRICE_FIELDS and value not in (None, "")
    ]
    c = profile.commercial
    if c.moq is not None:
        parts.append(f"MOQ: {c.moq}")
    if c.lead_time_days is not None:
        parts.append(f"Thời gian sản xuất (ngày): {c.lead_time_days}")
    if c.incoterm is not None:
        parts.append(f"Incoterm: {c.incoterm.value}")
    return "; ".join(parts)


def follow_up_evidence(record: FollowUpRecord) -> EvidenceItem:
    """What a supplier-side follow-up says, as evidence a reminder may cite."""
    if record.kind is FollowUpKind.SLA_BREACH:
        limit = "" if record.limit_days is None else f" (hạn {record.limit_days} ngày)"
        text = f"Quá hạn mốc {record.milestone}: đã {record.days} ngày{limit}"
    else:
        limit = "" if record.limit_days is None else f" (ngưỡng {record.limit_days} ngày)"
        text = f"Nhà cung cấp chưa gửi cập nhật {record.days} ngày{limit}"
    return EvidenceItem(f"follow_up:{record.id}", "Việc cần theo dõi", text)


def is_supplier_side(record: FollowUpRecord) -> bool:
    if record.kind in SUPPLIER_SIDE_KINDS:
        return True
    return record.kind is FollowUpKind.SLA_BREACH and record.milestone in SUPPLIER_SIDE_MILESTONES


@dataclass(slots=True)
class MessageLaneCount:
    by_outcome: dict[DraftOutcome, int] = field(default_factory=dict)
    failed_workspaces: int = 0

    def count(self, outcome: DraftOutcome) -> None:
        self.by_outcome[outcome] = self.by_outcome.get(outcome, 0) + 1

    @property
    def calls(self) -> int:
        return sum(
            n
            for outcome, n in self.by_outcome.items()
            if outcome in (DraftOutcome.DRAFTED, DraftOutcome.REFUSED, DraftOutcome.FAILED)
        )


# How long after a count its claim is still drafted (the discrepancy
# report's own window: the lane reads the counts of this window only).
CLAIM_WINDOW = timedelta(days=30)


@dataclass(frozen=True)
class DraftSupplierMessages:
    """The worker lane `supply_chain_supplier_messages` (module docstring)."""

    workspaces: WorkspacesPort
    cases: CaseListingPort
    follow_ups: OpenFollowUpsPort
    drafter: DraftSupplierMessage
    policy_override_repo: PolicyOverridePort
    platform_default_policy: SupplyChainStepPreparation
    # Where an approved revision request is found (ticket ai-automation/09);
    # a host without them drafts no message of that purpose.
    documents: CaseDocumentListPort | None = None
    drafts: CaseDraftsPort | None = None
    # The case's BM04 versions (ticket ai-automation/12): the confirmation at
    # step 8 is drafted from the latest one, never with its price.
    profiles: Bm04ProfileReadPort | None = None
    # PO cases in production and their schedules (ticket ai-automation/17):
    # the weekly progress chase; a host without them drafts none.
    po_listing: POCaseListingPort | None = None
    readings: ExtractionReadingsPort | None = None
    # The warehouse's counts (ticket ai-automation/18): the claim letter when
    # a count differs from what was shipped; a host without them drafts none.
    receipts: LineReceiptsPort | None = None
    batch: int = _BATCH

    async def run(self) -> MessageLaneCount:
        count = MessageLaneCount()
        for tenant_id, workspace_id in await self.workspaces.workspaces():
            if count.calls >= self.batch:
                break
            try:
                await self._workspace(lane_context(tenant_id, workspace_id), count)
            except Exception:
                logger.exception(
                    "supplier messages failed for a workspace",
                    extra={"tenant_id": str(tenant_id), "workspace_id": str(workspace_id)},
                )
                count.failed_workspaces += 1
        return count

    async def _workspace(self, context: AccessContext, count: MessageLaneCount) -> None:
        policy = await resolve_step_preparation(
            context, self.policy_override_repo, self.platform_default_policy
        )
        purposes = frozenset(policy.supplier_messages)
        if not purposes:
            return
        for request in await self._requests(context, purposes):
            if count.calls >= self.batch:
                return
            outcome = await self.drafter.draft(context, request)
            count.count(outcome)
            if outcome == DraftOutcome.DEFERRED:
                return  # the tenant's day is spent, or it has no plan

    async def _requests(
        self, context: AccessContext, purposes: frozenset[MessagePurpose]
    ) -> list[MessageRequest]:
        requests: list[MessageRequest] = []
        if MessagePurpose.SUPPLIER_REMINDER in purposes:
            requests.extend(
                MessageRequest(
                    case_kind=record.case_kind,
                    case_id=record.case_id,
                    purpose=MessagePurpose.SUPPLIER_REMINDER,
                    source_key=f"follow_up:{record.id}",
                    evidence=(follow_up_evidence(record),),
                )
                for record in await self.follow_ups.list_open(context)
                if record.workspace_id == context.workspace_id and is_supplier_side(record)
            )
        for purpose, state in _STEP_PURPOSES.items():
            if purpose in purposes:
                requests.extend(await self._step_requests(context, purpose, state))
        if MessagePurpose.SAMPLE_REVISION_REQUEST in purposes:
            requests.extend(await self._revision_requests(context))
        if MessagePurpose.PRODUCTION_PROGRESS in purposes:
            requests.extend(await self._progress_requests(context))
        if MessagePurpose.DISCREPANCY_CLAIM in purposes:
            requests.extend(await self._claim_requests(context))
        return requests

    async def _claim_requests(self, context: AccessContext) -> list[MessageRequest]:
        """One claim per PO case whose count, recorded in the window, differs
        from what was shipped: the lines as the warehouse recorded them (no
        price), the same words the discrepancy report prints."""
        if self.receipts is None:
            return []
        since = self.drafter.clock.now() - CLAIM_WINDOW
        found: list[MessageRequest] = []
        for case_id in await self.receipts.discrepant_cases(context, since=since):
            lines = discrepancies(await self.receipts.for_case(context, case_id))
            if not lines:
                continue
            found.append(
                MessageRequest(
                    case_kind=CaseKind.PO,
                    case_id=case_id,
                    purpose=MessagePurpose.DISCREPANCY_CLAIM,
                    source_key=f"discrepancy_claim:{case_id}",
                    evidence=(
                        EvidenceItem(
                            f"receipts:{case_id}",
                            "Số Kho đếm khi nhập kho",
                            "; ".join(line.words() for line in lines),
                        ),
                    ),
                )
            )
        return found

    async def _progress_requests(self, context: AccessContext) -> list[MessageRequest]:
        """One chase a week (ISO week) for each PO case in production: the
        case's facts and the supplier's own schedule as read (milestones and
        ETD; no price) the message may cite."""
        if self.po_listing is None:
            return []
        year, week, _ = self.drafter.clock.now().date().isocalendar()
        found: list[MessageRequest] = []
        case_filter = POCaseListFilter(state=CaseState.PRODUCTION)
        cursor: str | None = None
        while True:
            page = await self.po_listing.list_page(
                context,
                page_request(
                    limit=MAX_PAGE_SIZE,
                    cursor=cursor,
                    query=case_filter.page_query(context.tenant_id),
                ),
                case_filter,
            )
            for case in page.items:
                found.append(
                    MessageRequest(
                        case_kind=CaseKind.PO,
                        case_id=case.id.value,
                        purpose=MessagePurpose.PRODUCTION_PROGRESS,
                        source_key=f"production_progress:{case.id}:{year}-W{week:02d}",
                        evidence=await self._schedule(context, case.id.value),
                    )
                )
            if page.next_cursor is None:
                return found
            cursor = page.next_cursor

    async def _schedule(
        self, context: AccessContext, case_id: uuid.UUID
    ) -> tuple[EvidenceItem, ...]:
        """The newest production schedule's reading, as words a message may
        cite: its ETD and milestones as the supplier wrote them."""
        if self.documents is None or self.readings is None:
            return ()
        documents = [
            d
            for d in await self.documents.list_for_case(context, CaseKind.PO, case_id)
            if d.doc_type is DocumentType.PRODUCTION_SCHEDULE and d.case_id == case_id
        ]
        if not documents:
            return ()
        paper = max(documents, key=lambda d: (d.version, d.uploaded_at))
        reading = next(
            (
                r
                for r in await self.readings.readings(context, [paper.id.value])
                if r.sha256 == paper.sha256 and r.status is ExtractionStatus.EXTRACTED
            ),
            None,
        )
        if reading is None:
            return ()
        lines = []
        etd = field_value(reading.fields, "etd")
        if etd:
            lines.append(f"ETD: {etd}")
        rows = reading.fields.get("milestones")
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, Mapping):
                continue
            name = field_value(row, "name")
            planned = field_value(row, "planned_date")
            status = field_value(row, "status")
            if name:
                lines.append(
                    f"{name}: {planned or 'chưa ghi ngày'}" + (f" ({status})" if status else "")
                )
        if not lines:
            return ()
        return (EvidenceItem(f"doc:{paper.id}", "Lịch sản xuất của NCC", "; ".join(lines)),)

    async def _revision_requests(self, context: AccessContext) -> list[MessageRequest]:
        """One message per revision request document of a case waiting for its
        revised sample: the document attached, its items (when AI prepared it
        and a person approved it) as the evidence the message may cite."""
        if self.documents is None or self.drafts is None:
            return []
        found: list[MessageRequest] = []
        for case in await self._cases_in(context, ProductDevState.REVISION_REQUESTED):
            documents = [
                d
                for d in await self.documents.list_for_case(
                    context, CaseKind.PRODUCT, case.id.value
                )
                if d.doc_type is DocumentType.SAMPLE_REVISION_REQUEST
            ]
            if not documents:
                continue
            paper = max(documents, key=lambda d: (d.version, d.uploaded_at))
            confirmed = [
                d
                for d in await self.drafts.latest_for_case(context, CaseKind.PRODUCT, case.id.value)
                if d.doc_type is DocumentType.SAMPLE_REVISION_REQUEST
                and d.status is DraftStatus.CONFIRMED
            ]
            items = (
                field_value(max(confirmed, key=lambda d: d.created_at).fields, "items")
                if confirmed
                else None
            )
            lines = [
                f"{i.get('criterion')}: {i.get('finding')}; yêu cầu: {i.get('requirement') or ''}"
                for i in (items if isinstance(items, list) else [])
                if isinstance(i, Mapping)
            ]
            found.append(
                MessageRequest(
                    case_kind=CaseKind.PRODUCT,
                    case_id=case.id.value,
                    purpose=MessagePurpose.SAMPLE_REVISION_REQUEST,
                    source_key=f"sample_revision_request:{paper.id}",
                    evidence=(
                        EvidenceItem(
                            f"doc:{paper.id}",
                            "Phiếu yêu cầu chỉnh sửa mẫu",
                            f"Phiếu vòng {case.sample_round}: "
                            + (" | ".join(lines) if lines else f"file {paper.filename}"),
                        ),
                    ),
                    attachments=(paper.id.value,),
                )
            )
        return found

    async def _cases_in(
        self, context: AccessContext, state: ProductDevState
    ) -> list[ProductDevelopmentCase]:
        found: list[ProductDevelopmentCase] = []
        case_filter = ProductCaseListFilter(state=state)
        cursor: str | None = None
        while True:
            page = await self.cases.list_page(
                context,
                page_request(
                    limit=MAX_PAGE_SIZE,
                    cursor=cursor,
                    query=case_filter.page_query(context.tenant_id, context.workspace_id),
                ),
                case_filter,
            )
            found.extend(page.items)
            if page.next_cursor is None:
                return found
            cursor = page.next_cursor

    async def _step_requests(
        self, context: AccessContext, purpose: MessagePurpose, state: ProductDevState
    ) -> list[MessageRequest]:
        found: list[MessageRequest] = []
        for case in await self._cases_in(context, state):
            entered = await entered_current_state(self.cases, context, case)
            if entered is None or entered.id is None:
                continue
            evidence, attachments = (
                await self._bm04(context, case)
                if purpose is MessagePurpose.SUPPLIER_CONFIRMATION
                else ((), ())
            )
            found.append(
                MessageRequest(
                    case_kind=CaseKind.PRODUCT,
                    case_id=case.id.value,
                    purpose=purpose,
                    source_key=f"{purpose.value}:{entered.id}",
                    evidence=evidence,
                    attachments=attachments,
                )
            )
        return found

    async def _bm04(
        self, context: AccessContext, case: ProductDevelopmentCase
    ) -> tuple[tuple[EvidenceItem, ...], tuple[uuid.UUID, ...]]:
        """The confirmed BM04 a step-8 confirmation is drafted from: its
        latest version's terms without a price, and its document attached."""
        profile = (
            None if self.profiles is None else await self.profiles.latest(context, case.id.value)
        )
        documents = (
            []
            if self.documents is None
            else [
                d
                for d in await self.documents.list_for_case(
                    context, CaseKind.PRODUCT, case.id.value
                )
                if d.doc_type is DocumentType.PRODUCT_PROFILE_BM04
            ]
        )
        paper = max(documents, key=lambda d: (d.version, d.uploaded_at)) if documents else None
        attachments = () if paper is None else (paper.id.value,)
        if profile is None:
            return (), attachments
        key = f"doc:{paper.id}" if paper is not None else f"bm04:{profile.id}"
        return (EvidenceItem(key, "BM04 đã duyệt (không giá)", bm04_message_text(profile)),), (
            attachments
        )


__all__ = [
    "MESSAGES_LANE",
    "MESSAGE_PROMPT",
    "PURPOSE_LABELS",
    "DraftOutcome",
    "DraftSupplierMessage",
    "DraftSupplierMessages",
    "ListSupplierMessages",
    "MarkSupplierMessageSent",
    "MessageRequest",
    "NewMessageSend",
    "NewSupplierMessage",
    "SupplierMessage",
    "SupplierMessageRepositoryPort",
    "follow_up_evidence",
    "is_supplier_side",
    "lane_context",
]
