"""A PO case's steps 11-17 prepared by code, approved on the PO case page
(tickets ai-automation/15-18; ADR 0025 amended AI-14 and AI-15;
`domain.po_step`).

- **The lane** (`PreparePOSteps`, `supply_chain_po_steps`): for each workspace
  whose tenant's step preparation policy lists PO steps, each PO case in the
  state of an enabled step that drafts a paper gets ONE draft of it, filled by
  code (`RECIPES`), and the holders of the step's duty are told, without an
  amount. A case whose draft was rejected is not drafted again. No model is
  asked here: the model's work is the extraction lane's reading of the
  supplier's and the bank's papers, which code checks.
- **The page** (`GetPOStepProposal`): the enabled step of the case's state,
  its draft, what code finds now (each source document missing or unread,
  every amount, currency, account and line that does not match), AI's
  suggestion beside each result a person types (an amount hidden without
  `supply_chain.commercial.read`), whether AI proposes the step (nothing
  found) and whether the caller may approve (and why not).
- **The approval** (`ApprovePOStep`): the step's duty scope under the tenant's
  policy and, where the step writes a price, `supply_chain.commercial.write`.
  The case must still be in the step's state, the draft the open latest
  version the person saw (its content hash) with every amount code's, the
  typed results valid, and the paper the tenant requires for the step on the
  case. Then ONE transaction: the draft confirmed and rendered into the case's
  document (`ai_prepared`), the step taken through the same dispatch a click
  uses, the payment recorded, every audit event. A finding (even a beneficiary
  account that is not the master's) never blocks: a person decides, seeing it.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Protocol

from dw_agent_runtime.doc_templates import DocumentRendererPort
from dw_kernel.errors import ConflictError, DomainError, NotFoundError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.pagination import MAX_PAGE_SIZE, page_request
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import AuthorizationPort, PolicyOverridePort
from dw_platform.domain.audit import AuditEvent, system_actor
from dw_supply_chain.action_duties import CaseDuty, SupplyChainActionDuties
from dw_supply_chain.application.commercial import PAYMENT_RECORDED, allows
from dw_supply_chain.application.document_drafts import (
    DraftTemplatesPort,
    FieldInput,
    NewDraftDecision,
    PrepareDocumentDraft,
    template_values,
)
from dw_supply_chain.application.handlers import (
    COMMERCIAL_READ,
    COMMERCIAL_WRITE,
    PO_CASE_READ,
    duty_scope,
    notify_duty_holders,
    po_case_link,
    resolve_action_duties,
)
from dw_supply_chain.application.po_case_audit import po_case_audit
from dw_supply_chain.application.ports import (
    CaseDocumentStoragePort,
    NewCaseDocument,
    POCaseListFilter,
    ReviewNotifierPort,
    ScopeHoldersPort,
)
from dw_supply_chain.application.purchase_orders import POCaseStorePort, POCommercialReadPort
from dw_supply_chain.application.step_preparation import (
    CaseDocumentListPort,
    CaseDraftsPort,
    ExtractionReadingsPort,
    WorkspacesWithCasesPort,
    resolve_step_preparation,
)
from dw_supply_chain.domain.case_document import (
    CaseDocument,
    CaseDocumentId,
    CaseKind,
    DocumentType,
    ObjectKey,
)
from dw_supply_chain.domain.commercial import (
    NewPOPayment,
    PaymentKind,
    POCommercial,
    SupplierBankAccount,
    account_digest,
    latest_payments,
)
from dw_supply_chain.domain.document_draft import DocumentDraft, DraftDecision, DraftStatus
from dw_supply_chain.domain.extraction import EXTRACTION_SPECS, ExtractionStatus
from dw_supply_chain.domain.payment_check import (
    OrderedLine,
    PaymentFacts,
    SourceRead,
    account_findings,
    amount_findings,
    currency_findings,
    drafted_differs,
    line_findings,
    source_findings,
)
from dw_supply_chain.domain.po_case import CaseAction, POCase, POCaseId, apply_action
from dw_supply_chain.domain.po_step import (
    PO_STEPS,
    POStepKind,
    POStepSpec,
    ResultField,
    ResultKind,
    step_for_state,
)
from dw_supply_chain.domain.purchase_order_draft import decimal_text
from dw_supply_chain.domain.step_proposal import Finding
from dw_supply_chain.step_preparation_policy import SupplyChainStepPreparation

logger = logging.getLogger(__name__)

PO_STEPS_LANE = "supply_chain_po_steps"
PO_STEP_DRAFT_CONFIRMED = "supply_chain.document_draft.confirmed"
PO_STEP_DOCUMENT_ADDED = "supply_chain.case_document.ai_prepared"
_VIA = "po_step_approved"


def po_steps_lane_context(tenant_id: uuid.UUID, workspace_id: uuid.UUID) -> AccessContext:
    """The lane in one workspace: its own actor, no role, no scope."""
    return AccessContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        principal_id=system_actor(PO_STEPS_LANE).value,
        roles=frozenset(),
        scopes=frozenset(),
        plan_id="system",
    )


# ------------------------------------------------------------------ ports --


class SupplierAccountPort(Protocol):
    """The current master account of the workspace's supplier whose
    normalised name is the case's, under the caller's tenant and workspace.
    Read by code only; never a prompt variable."""

    async def account_for(
        self, context: AccessContext, supplier_name: str
    ) -> SupplierBankAccount | None: ...


class POStepPaperGatePort(Protocol):
    """The paper the tenant's policy names for a step (`PaperGateResolver`)."""

    async def paper_for(
        self, context: AccessContext, action: CaseAction
    ) -> DocumentType | None: ...


class POStepOutcomesPort(Protocol):
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
    ) -> None:
        """The draft's confirmation and the document made from it (naming the
        draft), the step (optimistic on the case's version), the payment, every
        audit event: all or nothing. A draft already decided, or a case saved
        meanwhile, is a `ConflictError`."""
        ...


# ------------------------------------------------------------------ facts --


@dataclass(frozen=True)
class POStepFacts:
    """What one step reads, under the caller's tenant and workspace."""

    case: POCase
    commercial: POCommercial
    payment: PaymentFacts
    master: SupplierBankAccount | None
    sources: Mapping[DocumentType, SourceRead]
    documents: Sequence[CaseDocument]

    def source(self, doc_type: DocumentType) -> SourceRead:
        return self.sources.get(doc_type, SourceRead(doc_type))

    def ordered(self) -> list[OrderedLine]:
        prices = {line.sku_id: line.unit_price for line in self.commercial.lines}
        return [
            OrderedLine(
                sku_code=line.sku_code or "",
                quantity=line.quantity,
                unit_price=prices.get(line.sku_id),
            )
            for line in self.case.lines
            if line.sku_code
        ]

    def newest(self, doc_type: DocumentType) -> CaseDocument | None:
        mine = [d for d in self.documents if d.doc_type is doc_type]
        return max(mine, key=lambda d: d.version) if mine else None


@dataclass(frozen=True)
class POStepSources:
    commercial: POCommercialReadPort
    accounts: SupplierAccountPort
    documents: CaseDocumentListPort
    readings: ExtractionReadingsPort

    async def gather(self, context: AccessContext, case: POCase, spec: POStepSpec) -> POStepFacts:
        commercial = await self.commercial.read(context, case.id.value)
        master = await self.accounts.account_for(context, case.supplier_name)
        documents = own_documents(
            context,
            await self.documents.list_for_case(context, CaseKind.PO, case.id.value),
            case.id.value,
        )
        sources: dict[DocumentType, SourceRead] = {}
        for doc_type in spec.sources:
            sources[doc_type] = await newest_reading(self.readings, context, documents, doc_type)
        deposit = latest_payments(commercial.payments).get(PaymentKind.DEPOSIT)
        return POStepFacts(
            case=case,
            commercial=commercial,
            payment=PaymentFacts(
                currency=commercial.terms.currency,
                order_total=commercial.order_total,
                deposit_percent=commercial.terms.deposit_percent,
                deposit_paid=None if deposit is None else deposit.amount,
                master_digest=None if master is None else account_digest(master.account_number),
            ),
            master=master,
            sources=sources,
            documents=documents,
        )


async def newest_reading(
    readings: ExtractionReadingsPort,
    context: AccessContext,
    documents: Sequence[CaseDocument],
    doc_type: DocumentType,
) -> SourceRead:
    """The newest document of `doc_type` among `documents` (already the
    caller's own) and its reading under the current prompt: one function for
    every step that reads a paper (tickets ai-automation/15-18)."""
    mine = [d for d in documents if d.doc_type is doc_type]
    if not mine:
        return SourceRead(doc_type)
    newest = max(mine, key=lambda d: d.version)
    spec = EXTRACTION_SPECS.get(doc_type)
    if spec is None:
        return SourceRead(doc_type, document_id=newest.id.value, sha256=newest.sha256)
    reading = next(
        (
            r
            for r in await readings.readings(context, [newest.id.value])
            if r.document_id == newest.id.value
            and r.sha256 == newest.sha256
            and (r.prompt_id, r.prompt_version) == (spec.prompt_id, spec.prompt_version)
        ),
        None,
    )
    if reading is None:
        return SourceRead(doc_type, document_id=newest.id.value, sha256=newest.sha256)
    return SourceRead(
        doc_type,
        document_id=newest.id.value,
        sha256=newest.sha256,
        extraction_id=reading.id,
        status=reading.status,
        fields=reading.fields if reading.status is ExtractionStatus.EXTRACTED else {},
    )


def own_documents(
    context: AccessContext, documents: Sequence[CaseDocument], case_id: uuid.UUID
) -> list[CaseDocument]:
    """The second layer behind RLS: only this case's documents of the
    caller's own tenant and workspace."""
    return [
        d
        for d in documents
        if d.case_id == case_id
        and d.tenant_id == context.tenant_id
        and d.workspace_id == context.workspace_id
    ]


# ---------------------------------------------------------------- recipes --


@dataclass(frozen=True, slots=True)
class Suggestion:
    """AI's reading (or code's figure) beside a result a person types."""

    value: str | None
    quote: str | None = None
    document_id: uuid.UUID | None = None


class POStepRecipe(Protocol):
    """How one PO step is prepared: its draft's values (none when it drafts no
    paper), the amounts a draft must state as code computes them, what code
    finds, and the suggestions beside its results."""

    def draft_values(self, facts: POStepFacts, today: date) -> dict[str, FieldInput]: ...

    def expected(self, facts: POStepFacts) -> dict[str, Decimal | None]: ...

    def findings(self, facts: POStepFacts) -> list[Finding]: ...

    def suggestions(self, facts: POStepFacts) -> dict[str, Suggestion]: ...


def _text(value: Decimal | None) -> str | None:
    return None if value is None else decimal_text(value)


def _cited(source: SourceRead, name: str) -> FieldInput | None:
    value = source.value(name)
    if not isinstance(value, str) or not value.strip():
        return None
    return FieldInput(value=value, document_id=source.document_id, quote=source.quote(name))


def _put(values: dict[str, FieldInput], name: str, value: str | None) -> None:
    if value is not None:
        values[name] = FieldInput(value=value)


def _beneficiary(values: dict[str, FieldInput], master: SupplierBankAccount | None) -> None:
    if master is None:
        return
    values["bank_name"] = FieldInput(value=master.bank_name)
    values["account_holder"] = FieldInput(value=master.account_holder)
    values["account_number"] = FieldInput(value=master.account_number)


def _terms_findings(facts: POStepFacts, *, deposit: bool) -> list[Finding]:
    found: list[Finding] = []
    if facts.payment.currency is None:
        found.append(
            Finding("term_missing", "currency", "PO chưa có tiền tệ; người điền trên hồ sơ")
        )
    if facts.payment.order_total is None:
        found.append(
            Finding(
                "term_missing",
                "order_total",
                "PO chưa tính được tổng: dòng nào đó thiếu số lượng hay đơn giá",
            )
        )
    if deposit and facts.payment.deposit_percent is None:
        found.append(
            Finding(
                "term_missing", "deposit_percent", "PO chưa có % đặt cọc; người điền trên hồ sơ"
            )
        )
    return found


def _no_master(facts: POStepFacts) -> list[Finding]:
    if facts.master is not None:
        return []
    return [
        Finding(
            "beneficiary_missing",
            "account_number",
            "NCC chưa có tài khoản trong danh mục: đề nghị chưa có tài khoản thụ hưởng; nhập tài"
            " khoản đã xác minh vào danh mục NCC",
        )
    ]


@dataclass(frozen=True)
class DepositRequestRecipe:
    """Step 11's request: the deposit is the order total times the PO's %."""

    def draft_values(self, facts: POStepFacts, today: date) -> dict[str, FieldInput]:
        pi = facts.source(DocumentType.PROFORMA_INVOICE)
        values: dict[str, FieldInput] = {
            "request_date": FieldInput(value=today.isoformat()),
            "supplier_name": FieldInput(value=facts.case.supplier_name),
        }
        _put(values, "po_reference", facts.case.po_reference)
        reference = _cited(pi, "invoice_number")
        if reference is not None:
            values["proforma_reference"] = reference
        _put(values, "currency", facts.payment.currency)
        for name, value in self.expected(facts).items():
            _put(values, name, _text(value))
        _beneficiary(values, facts.master)
        return values

    def expected(self, facts: POStepFacts) -> dict[str, Decimal | None]:
        return {
            "order_total": facts.payment.order_total,
            "deposit_percent": facts.payment.deposit_percent,
            "amount": facts.payment.deposit_due,
        }

    def findings(self, facts: POStepFacts) -> list[Finding]:
        pi = facts.source(DocumentType.PROFORMA_INVOICE)
        found = _terms_findings(facts, deposit=True) + _no_master(facts) + source_findings(pi)
        found += amount_findings(pi, "total", "tổng tiền", facts.payment.order_total)
        if pi.number("deposit_amount") is not None:
            found += amount_findings(pi, "deposit_amount", "tiền cọc", facts.payment.deposit_due)
        found += currency_findings(pi, facts.payment.currency)
        found += account_findings(pi, facts.payment.master_digest)
        found += line_findings(facts.ordered(), pi)
        return found

    def suggestions(self, facts: POStepFacts) -> dict[str, Suggestion]:
        return {}


@dataclass(frozen=True)
class FinalPaymentRequestRecipe:
    """Step 16's request: the balance is the order total less the deposit
    recorded as paid; the invoice is matched with the PO line by line."""

    def draft_values(self, facts: POStepFacts, today: date) -> dict[str, FieldInput]:
        invoice = facts.source(DocumentType.COMMERCIAL_INVOICE)
        values: dict[str, FieldInput] = {
            "request_date": FieldInput(value=today.isoformat()),
            "supplier_name": FieldInput(value=facts.case.supplier_name),
        }
        _put(values, "po_reference", facts.case.po_reference)
        reference = _cited(invoice, "invoice_number")
        if reference is not None:
            values["invoice_reference"] = reference
        _put(values, "currency", facts.payment.currency)
        for name, value in self.expected(facts).items():
            _put(values, name, _text(value))
        _beneficiary(values, facts.master)
        return values

    def expected(self, facts: POStepFacts) -> dict[str, Decimal | None]:
        return {
            "order_total": facts.payment.order_total,
            "deposit_paid": facts.payment.deposit_paid,
            "amount": facts.payment.balance_due,
        }

    def findings(self, facts: POStepFacts) -> list[Finding]:
        invoice = facts.source(DocumentType.COMMERCIAL_INVOICE)
        found = _terms_findings(facts, deposit=False) + _no_master(facts)
        if facts.payment.deposit_paid is None:
            found.append(
                Finding(
                    "deposit_unrecorded",
                    "deposit_paid",
                    "Chưa ghi tiền cọc đã trả: số đề nghị thanh toán chưa tính được",
                )
            )
        found += source_findings(invoice)
        found += amount_findings(invoice, "total", "tổng tiền", facts.payment.order_total)
        found += currency_findings(invoice, facts.payment.currency)
        found += account_findings(invoice, facts.payment.master_digest)
        found += line_findings(facts.ordered(), invoice)
        return found

    def suggestions(self, facts: POStepFacts) -> dict[str, Suggestion]:
        return {}


@dataclass(frozen=True)
class PaymentConfirmationRecipe:
    """Kế toán's confirmation of a payment it made at the bank: the transfer
    receipt read against what was due; the amount paid and the date are
    typed, with the receipt's reading beside them."""

    due_words: str
    deposit: bool

    def draft_values(self, facts: POStepFacts, today: date) -> dict[str, FieldInput]:
        return {}

    def due(self, facts: POStepFacts) -> Decimal | None:
        return facts.payment.deposit_due if self.deposit else facts.payment.balance_due

    def expected(self, facts: POStepFacts) -> dict[str, Decimal | None]:
        return {}

    def findings(self, facts: POStepFacts) -> list[Finding]:
        receipt = facts.source(DocumentType.BANK_TRANSFER_RECEIPT)
        found = _terms_findings(facts, deposit=self.deposit)
        if not self.deposit and facts.payment.deposit_paid is None:
            found.append(
                Finding(
                    "deposit_unrecorded",
                    "deposit_paid",
                    "Chưa ghi tiền cọc đã trả: số còn phải trả chưa tính được",
                )
            )
        found += source_findings(receipt)
        found += amount_findings(receipt, "amount", self.due_words, self.due(facts))
        found += currency_findings(receipt, facts.payment.currency)
        found += account_findings(receipt, facts.payment.master_digest)
        return found

    def suggestions(self, facts: POStepFacts) -> dict[str, Suggestion]:
        receipt = facts.source(DocumentType.BANK_TRANSFER_RECEIPT)
        out: dict[str, Suggestion] = {}
        if receipt.extracted:
            for result, name in (("paid_amount", "amount"), ("paid_on", "transfer_date")):
                value = receipt.value(name)
                if isinstance(value, str) and value:
                    out[result] = Suggestion(value, receipt.quote(name), receipt.document_id)
        return out


# One recipe per step: the lane, the page and the approval read the same one.
RECIPES: Mapping[POStepKind, POStepRecipe] = {
    POStepKind.DEPOSIT_REQUEST: DepositRequestRecipe(),
    POStepKind.DEPOSIT_PAYMENT: PaymentConfirmationRecipe("số tiền đã chuyển", deposit=True),
    POStepKind.FINAL_PAYMENT_REQUEST: FinalPaymentRequestRecipe(),
    POStepKind.FINAL_PAYMENT: PaymentConfirmationRecipe("số tiền đã chuyển", deposit=False),
}
assert set(RECIPES) == set(POStepKind), "every PO step has a recipe"


def draft_findings(spec: POStepSpec, draft: DocumentDraft, facts: POStepFacts) -> list[Finding]:
    """What code finds in the step's draft as it stands (a person may have
    edited it): each amount that is not code's."""
    return [
        Finding(
            "total_differs",
            name,
            "Số trong bản nháp khác số hệ thống tính từ PO; sửa trước khi duyệt",
        )
        for name in drafted_differs(draft.fields, RECIPES[spec.kind].expected(facts))
    ]


def _latest_draft(drafts: Sequence[DocumentDraft], doc_type: DocumentType) -> DocumentDraft | None:
    mine = [d for d in drafts if d.doc_type is doc_type]
    return max(mine, key=lambda d: d.created_at) if mine else None


async def _enabled(
    context: AccessContext,
    policy_override_repo: PolicyOverridePort,
    platform_default: SupplyChainStepPreparation,
) -> tuple[POStepKind, ...]:
    policy = await resolve_step_preparation(context, policy_override_repo, platform_default)
    return policy.po_steps


async def _po_in_workspace(
    cases: POCaseStorePort, context: AccessContext, case_id: POCaseId
) -> POCase:
    case = await cases.get(context, case_id)
    if case is None or case.workspace_id.value != context.workspace_id:
        raise NotFoundError("PO case not found", details={"case_id": str(case_id)})
    return case


# ------------------------------------------------------------------- lane --


_DRAFTED_TITLE: Mapping[DocumentType, str] = {
    DocumentType.DEPOSIT_DOCS: "AI đã soạn đề nghị đặt cọc",
    DocumentType.PAYMENT_DOCS: "AI đã soạn đề nghị thanh toán",
}


@dataclass(slots=True)
class POStepCount:
    drafted: int = 0
    failed_workspaces: int = 0


@dataclass(frozen=True)
class PreparePOSteps:
    """The worker lane `supply_chain_po_steps` (module docstring)."""

    workspaces: WorkspacesWithCasesPort
    cases: POCaseStorePort
    sources: POStepSources
    drafts: CaseDraftsPort
    prepare_draft: PrepareDocumentDraft
    policy_override_repo: PolicyOverridePort
    platform_default_policy: SupplyChainStepPreparation
    platform_default_duties: SupplyChainActionDuties
    holders: ScopeHoldersPort
    notifier: ReviewNotifierPort
    clock: UtcClock

    async def run(self) -> POStepCount:
        count = POStepCount()
        for tenant_id, workspace_id in await self.workspaces.workspaces():
            context = po_steps_lane_context(tenant_id, workspace_id)
            try:
                enabled = await _enabled(
                    context, self.policy_override_repo, self.platform_default_policy
                )
                for kind in enabled:
                    spec = PO_STEPS[kind]
                    if spec.draft is not None:
                        count.drafted += await self._workspace(context, spec)
            except Exception:
                logger.exception(
                    "PO step drafting failed for a workspace",
                    extra={"tenant_id": str(tenant_id), "workspace_id": str(workspace_id)},
                )
                count.failed_workspaces += 1
        return count

    async def _workspace(self, context: AccessContext, spec: POStepSpec) -> int:
        case_filter = POCaseListFilter(state=spec.state)
        cursor: str | None = None
        drafted = 0
        while True:
            page = await self.cases.list_page(
                context,
                page_request(
                    limit=MAX_PAGE_SIZE,
                    cursor=cursor,
                    query=case_filter.page_query(context.tenant_id),
                ),
                case_filter,
            )
            for listed in page.items:
                drafted += await self.draft(context, spec, listed.id)
            if page.next_cursor is None:
                return drafted
            cursor = page.next_cursor

    async def draft(self, context: AccessContext, spec: POStepSpec, case_id: POCaseId) -> int:
        """One draft of the step's paper for a case in its state that has
        none (whatever became of an earlier one: a rejection is a person's
        no)."""
        assert spec.draft is not None
        case = await self.cases.get(context, case_id)
        if case is None or case.state is not spec.state:
            return 0
        existing = await self.drafts.latest_for_case(context, CaseKind.PO, case.id.value)
        if any(d.doc_type is spec.draft for d in existing):
            return 0
        facts = await self.sources.gather(context, case, spec)
        values = RECIPES[spec.kind].draft_values(facts, self.clock.now().date())
        draft = await self.prepare_draft.handle(
            context,
            case_kind=CaseKind.PO,
            case_id=case.id.value,
            doc_type=spec.draft,
            values=values,
        )
        duties = await resolve_action_duties(
            context, self.policy_override_repo, self.platform_default_duties
        )
        await notify_duty_holders(
            context,
            holders=self.holders,
            notifier=self.notifier,
            duty=duties.duty_for(spec.action),
            source_key=f"supply_chain.po_step_draft:{draft.lineage_id}",
            title=f"{_DRAFTED_TITLE.get(spec.draft, 'AI đã soạn chứng từ')}: {case.supplier_name}",
            body="Bản nháp chờ người kiểm và duyệt trên hồ sơ PO.",
            link=po_case_link(case.id.value),
        )
        return 1


# ------------------------------------------------------------------- page --


@dataclass(frozen=True, slots=True)
class ResultView:
    field: ResultField
    suggestion: Suggestion | None
    # The suggestion is an amount the caller may not read.
    redacted: bool = False


@dataclass(frozen=True, slots=True)
class POStepProposal:
    spec: POStepSpec | None
    draft: DocumentDraft | None
    findings: tuple[Finding, ...]
    results: tuple[ResultView, ...]
    # AI proposes the step: nothing found.
    proposed: bool
    can_approve: bool
    # Why the caller may not approve, in words; None when they may.
    blocked: str | None
    # The paper the tenant requires for the step, when it is not on the case.
    missing_paper: DocumentType | None = None


@dataclass(frozen=True)
class GetPOStepProposal:
    cases: POCaseStorePort
    sources: POStepSources
    drafts: CaseDraftsPort
    papers: POStepPaperGatePort
    policy_override_repo: PolicyOverridePort
    platform_default_policy: SupplyChainStepPreparation
    platform_default_duties: SupplyChainActionDuties
    authz: AuthorizationPort

    async def handle(self, context: AccessContext, case_id: POCaseId) -> POStepProposal:
        await self.authz.require(
            context=context, action=PO_CASE_READ, resource_type="po_case", resource_id=str(case_id)
        )
        case = await _po_in_workspace(self.cases, context, case_id)
        enabled = await _enabled(context, self.policy_override_repo, self.platform_default_policy)
        spec = step_for_state(case.state, enabled)
        if spec is None:
            return POStepProposal(
                None, None, (), (), False, False, "Bước hiện tại không có đề xuất của AI"
            )
        recipe = RECIPES[spec.kind]
        facts = await self.sources.gather(context, case, spec)
        draft = (
            None
            if spec.draft is None
            else _latest_draft(
                await self.drafts.latest_for_case(context, CaseKind.PO, case.id.value), spec.draft
            )
        )
        findings = recipe.findings(facts)
        if draft is not None:
            findings += draft_findings(spec, draft, facts)
        prices_visible = await allows(self.authz, context, COMMERCIAL_READ, "po_case")
        suggestions = recipe.suggestions(facts)
        results = tuple(
            _result_view(f, suggestions.get(f.name), prices_visible) for f in spec.results
        )
        missing_paper = await _missing_paper(self.papers, context, spec, facts)
        blocked = await self._blocked(context, spec, draft, missing_paper)
        return POStepProposal(
            spec=spec,
            draft=draft,
            findings=tuple(findings),
            results=results,
            proposed=not findings and (spec.draft is None or draft is not None),
            can_approve=blocked is None,
            blocked=blocked,
            missing_paper=missing_paper,
        )

    async def _blocked(
        self,
        context: AccessContext,
        spec: POStepSpec,
        draft: DocumentDraft | None,
        missing_paper: DocumentType | None,
    ) -> str | None:
        duties = await resolve_action_duties(
            context, self.policy_override_repo, self.platform_default_duties
        )
        if spec.draft is not None and draft is None:
            return "Chưa có bản nháp: hệ thống soạn trong ít phút"
        if draft is not None and draft.status is not DraftStatus.OPEN:
            return "Bản nháp này đã được quyết"
        if missing_paper is not None:
            return "Bước này cần chứng từ của công ty trên hồ sơ trước khi duyệt"
        if not await allows(
            self.authz, context, duty_scope(duties.duty_for(spec.action)), "po_case"
        ):
            return "Duyệt bước này cần duty của bước"
        if spec.writes_prices and not await allows(
            self.authz, context, COMMERCIAL_WRITE, "po_case"
        ):
            return "Duyệt bước này ghi số tiền: cần quyền ghi dữ liệu thương mại"
        return None


def _result_view(field_: ResultField, suggestion: Suggestion | None, visible: bool) -> ResultView:
    if suggestion is not None and field_.kind is ResultKind.AMOUNT and not visible:
        return ResultView(field_, None, redacted=True)
    return ResultView(field_, suggestion)


async def _missing_paper(
    papers: POStepPaperGatePort, context: AccessContext, spec: POStepSpec, facts: POStepFacts
) -> DocumentType | None:
    paper = await papers.paper_for(context, spec.action)
    if paper is None or paper is spec.draft:
        return None
    if facts.newest(paper) is not None:
        return None
    return paper


# --------------------------------------------------------------- approval --


def _typed_results(spec: POStepSpec, typed: Mapping[str, str]) -> dict[str, Any]:
    unknown = sorted(set(typed) - {f.name for f in spec.results})
    if unknown:
        raise DomainError("ô kết quả không thuộc bước này", details={"fields": unknown})
    out: dict[str, Any] = {}
    for result in spec.results:
        raw = (typed.get(result.name) or "").strip()
        if not raw:
            if result.required:
                raise DomainError(
                    f"cần nhập {result.label.lower()}", details={"field": result.name}
                )
            continue
        match result.kind:
            case ResultKind.AMOUNT:
                try:
                    out[result.name] = Decimal(raw)
                except ArithmeticError as exc:
                    raise DomainError(
                        "số tiền không hợp lệ", details={"field": result.name}
                    ) from exc
            case ResultKind.DATE:
                try:
                    out[result.name] = date.fromisoformat(raw)
                except ValueError as exc:
                    raise DomainError("ngày không hợp lệ", details={"field": result.name}) from exc
    return out


_PAYMENT_KIND: Mapping[POStepKind, PaymentKind] = {
    POStepKind.DEPOSIT_PAYMENT: PaymentKind.DEPOSIT,
    POStepKind.FINAL_PAYMENT: PaymentKind.FINAL,
}
# The paper a payment cites: the request approved at the step before.
_PAYMENT_PAPER: Mapping[PaymentKind, DocumentType] = {
    PaymentKind.DEPOSIT: DocumentType.DEPOSIT_DOCS,
    PaymentKind.FINAL: DocumentType.PAYMENT_DOCS,
}
_NEXT_DUTY_NOTICE: Mapping[POStepKind, tuple[CaseDuty, str, str]] = {
    POStepKind.DEPOSIT_REQUEST: (
        CaseDuty.FINANCE,
        "Đề nghị đặt cọc chờ Kế toán chi",
        "Cung ứng đã duyệt đề nghị đặt cọc; chi ở ngân hàng, tải UNC lên hồ sơ rồi xác nhận.",
    ),
    POStepKind.FINAL_PAYMENT_REQUEST: (
        CaseDuty.FINANCE,
        "Đề nghị thanh toán chờ Kế toán chi",
        "Cung ứng đã duyệt đề nghị thanh toán; chi ở ngân hàng, tải UNC lên hồ sơ rồi xác nhận.",
    ),
    POStepKind.DEPOSIT_PAYMENT: (
        CaseDuty.ORDERING,
        "Kế toán đã xác nhận đặt cọc",
        "Đã đặt cọc cho nhà cung cấp; hồ sơ sang bước tiếp theo.",
    ),
    POStepKind.FINAL_PAYMENT: (
        CaseDuty.ORDERING,
        "Kế toán đã xác nhận thanh toán",
        "Đã thanh toán cho nhà cung cấp; hồ sơ sang nhận hàng vào kho.",
    ),
}


@dataclass(frozen=True)
class ApprovePOStep:
    cases: POCaseStorePort
    sources: POStepSources
    drafts: CaseDraftsPort
    templates: DraftTemplatesPort
    renderer: DocumentRendererPort
    storage: CaseDocumentStoragePort
    outcomes: POStepOutcomesPort
    papers: POStepPaperGatePort
    policy_override_repo: PolicyOverridePort
    platform_default_policy: SupplyChainStepPreparation
    platform_default_duties: SupplyChainActionDuties
    holders: ScopeHoldersPort
    notifier: ReviewNotifierPort
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(
        self,
        context: AccessContext,
        case_id: POCaseId,
        *,
        kind: POStepKind,
        draft_id: uuid.UUID | None,
        content_sha256: str | None,
        results: Mapping[str, str],
    ) -> POCase:
        spec = PO_STEPS[kind]
        duties = await resolve_action_duties(
            context, self.policy_override_repo, self.platform_default_duties
        )
        await self.authz.require(
            context=context,
            action=duty_scope(duties.duty_for(spec.action)),
            resource_type="po_case",
            resource_id=str(case_id),
        )
        if spec.writes_prices:
            await self.authz.require(
                context=context,
                action=COMMERCIAL_WRITE,
                resource_type="po_case",
                resource_id=str(case_id),
            )
        case = await _po_in_workspace(self.cases, context, case_id)
        enabled = await _enabled(context, self.policy_override_repo, self.platform_default_policy)
        if kind not in enabled:
            raise DomainError(
                "công ty chưa bật bước này cho AI chuẩn bị", details={"step": kind.value}
            )
        if case.state is not spec.state:
            raise ConflictError(
                "hồ sơ đã sang bước khác; mở lại để xem bước hiện tại",
                details={"case_id": str(case_id), "state": case.state.value},
            )
        typed = _typed_results(spec, results)
        facts = await self.sources.gather(context, case, spec)
        draft = await self._draft(context, spec, case, facts, draft_id, content_sha256)
        missing = await _missing_paper(self.papers, context, spec, facts)
        if missing is not None:
            raise ConflictError(
                f"{spec.action.value} cần {missing.value} trên hồ sơ này trước",
                details={"case_id": str(case_id), "missing_document_type": missing.value},
            )
        payment = self._payment(spec, case, facts, typed)
        before = case.state
        apply_action(case, action=spec.action, reason=None)
        document, confirmation = await self._document(context, case, draft)
        audits = [
            po_case_audit(
                context,
                self.ids,
                self.clock,
                case.id,
                spec.action.value,
                {
                    "from_state": before.value,
                    "to_state": case.state.value,
                    "via": _VIA,
                    "step": kind.value,
                    "draft_id": None if draft is None else str(draft.id),
                    "document_id": None if document is None else str(document.id),
                },
            )
        ]
        if draft is not None and document is not None:
            audits += [
                self._event(
                    context,
                    PO_STEP_DRAFT_CONFIRMED,
                    "document_draft",
                    draft.id,
                    {"lineage_id": str(draft.lineage_id), "document_id": str(document.id)},
                ),
                self._event(
                    context,
                    PO_STEP_DOCUMENT_ADDED,
                    "case_document",
                    document.id.value,
                    {
                        "case_kind": CaseKind.PO.value,
                        "case_id": str(case.id),
                        "doc_type": document.doc_type.value,
                        "draft_id": str(draft.id),
                        "sha256": document.sha256,
                    },
                ),
            ]
        if payment is not None:
            # Names the kind, never the amount: the audit log is read by people
            # without the commercial scope.
            audits.append(
                self._event(
                    context,
                    PAYMENT_RECORDED,
                    "po_payment",
                    payment.id,
                    {"po_case_id": str(case.id), "kind": payment.kind.value, "via": _VIA},
                )
            )
        await self.outcomes.apply(
            context,
            case=case,
            confirmation=confirmation,
            document=document,
            draft_id=None if draft is None else draft.id,
            payment=payment,
            audits=audits,
        )
        duty, title, body = _NEXT_DUTY_NOTICE[kind]
        await notify_duty_holders(
            context,
            holders=self.holders,
            notifier=self.notifier,
            duty=duty,
            source_key=f"supply_chain.po_step:{case.id}:{kind.value}",
            title=f"{title}: {case.supplier_name}",
            body=body,
            link=po_case_link(case.id.value),
        )
        return case

    async def _draft(
        self,
        context: AccessContext,
        spec: POStepSpec,
        case: POCase,
        facts: POStepFacts,
        draft_id: uuid.UUID | None,
        content_sha256: str | None,
    ) -> DocumentDraft | None:
        if spec.draft is None:
            if draft_id is not None:
                raise DomainError("bước này không có bản nháp", details={"draft_id": str(draft_id)})
            return None
        if draft_id is None or content_sha256 is None:
            raise DomainError("duyệt bước này cần bản nháp đã xem", details={"field": "draft_id"})
        draft = await self.drafts.get(context, draft_id)
        if (
            draft is None
            or draft.case_kind is not CaseKind.PO
            or draft.case_id != case.id.value
            or draft.doc_type is not spec.draft
        ):
            raise NotFoundError("no draft by that id for this step of this case")
        if draft.status is not DraftStatus.OPEN or draft.content_sha256 != content_sha256:
            raise ConflictError(
                "bản nháp đã đổi hoặc đã được quyết; mở lại để xem bản mới nhất",
                details={"draft_id": str(draft_id)},
            )
        differ = drafted_differs(draft.fields, RECIPES[spec.kind].expected(facts))
        missing = [
            name
            for name in ("currency", "amount")
            if (draft.fields.get(name) or {}).get("value") in (None, "")
        ]
        if differ or missing:
            raise DomainError(
                "bản nháp chưa duyệt được: số khác số hệ thống tính hoặc còn thiếu ô bắt buộc",
                details={"differ": differ, "missing": missing},
            )
        return draft

    def _payment(
        self, spec: POStepSpec, case: POCase, facts: POStepFacts, typed: Mapping[str, Any]
    ) -> NewPOPayment | None:
        kind = _PAYMENT_KIND.get(spec.kind)
        if kind is None:
            return None
        currency = facts.payment.currency
        if currency is None:
            raise DomainError("PO chưa có tiền tệ: không ghi được số tiền đã chi")
        paper = facts.newest(_PAYMENT_PAPER[kind])
        return NewPOPayment.of(
            id=self.ids.new_uuid(),
            po_case_id=case.id.value,
            kind=kind,
            amount_value=typed["paid_amount"],
            currency=currency,
            due_date=None,
            paid_on=typed.get("paid_on"),
            document_id=None if paper is None else paper.id.value,
        )

    async def _document(
        self, context: AccessContext, case: POCase, draft: DocumentDraft | None
    ) -> tuple[NewCaseDocument | None, NewDraftDecision | None]:
        if draft is None:
            return None, None
        template = await self.templates.resolve(context, draft.template_id, draft.template_version)
        rendered = self.renderer.render(template, template_values(draft.fields, hide_prices=False))
        document_id = self.ids.new_uuid()
        key = ObjectKey.build(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            case_kind=CaseKind.PO,
            case_id=case.id.value,
            document_id=document_id,
        ).value
        # Outside the transaction, as an upload is: a row that then fails
        # leaves an object the orphan sweep removes.
        await self.storage.put(key, rendered.data, rendered.content_type)
        document = NewCaseDocument(
            id=CaseDocumentId(document_id),
            case_kind=CaseKind.PO,
            case_id=case.id.value,
            doc_type=draft.doc_type,
            object_key=key,
            filename=f"{template.spec.title}.{rendered.extension}",
            content_type=rendered.content_type,
            size_bytes=len(rendered.data),
            sha256=hashlib.sha256(rendered.data).hexdigest(),
        )
        confirmation = NewDraftDecision(
            id=self.ids.new_uuid(),
            draft_id=draft.id,
            decision=DraftDecision.CONFIRMED,
            reason=None,
        )
        return document, confirmation

    def _event(
        self,
        context: AccessContext,
        action: str,
        resource_type: str,
        resource_id: uuid.UUID,
        details: dict[str, Any],
    ) -> AuditEvent:
        return AuditEvent(
            id=self.ids.new_uuid(),
            tenant_id=TenantId(context.tenant_id),
            workspace_id=WorkspaceId(context.workspace_id),
            actor_id=UserId(context.principal_id),
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id),
            occurred_at=self.clock.now(),
            details=details,
        )


__all__ = [
    "PO_STEPS_LANE",
    "RECIPES",
    "ApprovePOStep",
    "GetPOStepProposal",
    "POStepCount",
    "POStepFacts",
    "POStepOutcomesPort",
    "POStepPaperGatePort",
    "POStepProposal",
    "POStepRecipe",
    "POStepSources",
    "PreparePOSteps",
    "ResultView",
    "Suggestion",
    "SupplierAccountPort",
    "draft_findings",
    "newest_reading",
    "own_documents",
    "po_steps_lane_context",
]
