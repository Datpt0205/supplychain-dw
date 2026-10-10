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
from datetime import date, timedelta
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
    LineReceiptsPort,
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
    line_quantities,
    sku_key,
    source_findings,
)
from dw_supply_chain.domain.po_case import (
    CaseAction,
    POCase,
    POCaseId,
    Shipping,
    apply_action,
    container_number,
)
from dw_supply_chain.domain.po_step import (
    PO_STEPS,
    POStepKind,
    POStepSpec,
    ResultField,
    ResultKind,
    step_for_state,
)
from dw_supply_chain.domain.purchase_order_draft import decimal_text
from dw_supply_chain.domain.receipt_check import (
    LineCount,
    LineDiscrepancy,
    NewLineReceipt,
    counted_lines,
    discrepancies,
)
from dw_supply_chain.domain.shipping_check import (
    container_findings,
    customs_findings,
    etd_findings,
    qc_suggestion,
)
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
        closed: NewDraftDecision | None,
        shipping: Shipping | None,
        receipts: Sequence[NewLineReceipt],
    ) -> None:
        """The draft's confirmation and the document made from it (naming the
        draft), or the closing of a draft whose outcome was not taken; the
        step (optimistic on the case's version), the shipping dates and
        container it learnt, the payment, the warehouse's counts, every audit
        event: all or nothing. A draft already decided, or a case saved
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
    """How one PO step is prepared: whether it wants its draft now, its
    draft's values (none when it drafts no paper), the amounts a draft must
    state as code computes them, what code finds, and the suggestions beside
    its results."""

    def wants_draft(self, facts: POStepFacts) -> bool: ...

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


def _suggest(source: SourceRead, names: Mapping[str, str]) -> dict[str, Suggestion]:
    """The reading beside each result: result name -> the source's field."""
    out: dict[str, Suggestion] = {}
    if source.extracted:
        for result, name in names.items():
            value = source.value(name)
            if isinstance(value, str) and value:
                out[result] = Suggestion(value, source.quote(name), source.document_id)
    return out


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

    def wants_draft(self, facts: POStepFacts) -> bool:
        return True

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

    def wants_draft(self, facts: POStepFacts) -> bool:
        return True

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
        # The three-way match (ticket ai-automation/17): the packing list's
        # quantities against the PO, and QC's report by its numbers.
        packing = facts.source(DocumentType.PACKING_LIST)
        found += source_findings(packing)
        found += line_findings(facts.ordered(), packing, prices=False)
        if qc_suggestion(facts.source(DocumentType.QC_REPORT)).verdict == "fail":
            found.append(
                Finding(
                    "qc_report_fails",
                    DocumentType.QC_REPORT.value,
                    "Báo cáo QC mới nhất có số lỗi vượt AQL; kiểm trước khi thanh toán",
                )
            )
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

    def wants_draft(self, facts: POStepFacts) -> bool:
        return True

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
        return _suggest(
            facts.source(DocumentType.BANK_TRANSFER_RECEIPT),
            {"paid_amount": "amount", "paid_on": "transfer_date"},
        )


@dataclass(frozen=True)
class ProductionRecipe:
    """Step 13 (ticket ai-automation/17): the supplier's schedule read, its
    ETD held to the PO's expected delivery; Cung ứng sends the goods to QC."""

    def wants_draft(self, facts: POStepFacts) -> bool:
        return False

    def draft_values(self, facts: POStepFacts, today: date) -> dict[str, FieldInput]:
        return {}

    def expected(self, facts: POStepFacts) -> dict[str, Decimal | None]:
        return {}

    def findings(self, facts: POStepFacts) -> list[Finding]:
        schedule = facts.source(DocumentType.PRODUCTION_SCHEDULE)
        return source_findings(schedule) + etd_findings(
            schedule, facts.commercial.terms.expected_delivery_date
        )

    def suggestions(self, facts: POStepFacts) -> dict[str, Suggestion]:
        return _suggest(facts.source(DocumentType.PRODUCTION_SCHEDULE), {"etd": "etd"})


@dataclass(frozen=True)
class QcRecipe:
    """Step 14 (ticket ai-automation/17): QC's verdict is QC's; beside the
    empty field, code's suggestion by the report's numbers, and a rework
    request drafted when the numbers fail."""

    def wants_draft(self, facts: POStepFacts) -> bool:
        return qc_suggestion(facts.source(DocumentType.QC_REPORT)).verdict == "fail"

    def draft_values(self, facts: POStepFacts, today: date) -> dict[str, FieldInput]:
        report = facts.source(DocumentType.QC_REPORT)
        values: dict[str, FieldInput] = {
            "requested_on": FieldInput(value=today.isoformat()),
            "supplier_name": FieldInput(value=facts.case.supplier_name),
        }
        _put(values, "po_reference", facts.case.po_reference)
        summary = [
            f"{words}: {report.value(found)} (Ac {report.value(accept)})"
            for found, accept, words in (
                ("critical_found", "critical_accept", "Lỗi nghiêm trọng"),
                ("major_found", "major_accept", "Lỗi nặng"),
                ("minor_found", "minor_accept", "Lỗi nhẹ"),
            )
            if report.value(found) is not None and report.value(accept) is not None
        ]
        if summary:
            values["qc_summary"] = FieldInput(value="; ".join(summary))
        defects = report.fields.get("defects")
        items = [
            {"defect": d.get("value"), "requirement": None}
            for d in (defects if isinstance(defects, list) else [])
            if isinstance(d, Mapping) and d.get("value")
        ]
        values["items"] = FieldInput(value=items)
        return values

    def expected(self, facts: POStepFacts) -> dict[str, Decimal | None]:
        return {}

    def findings(self, facts: POStepFacts) -> list[Finding]:
        report = facts.source(DocumentType.QC_REPORT)
        return source_findings(report) + list(qc_suggestion(report).findings)

    def suggestions(self, facts: POStepFacts) -> dict[str, Suggestion]:
        report = facts.source(DocumentType.QC_REPORT)
        out = _suggest(
            facts.source(DocumentType.PACKING_LIST), {"container_number": "container_number"}
        )
        verdict = qc_suggestion(report).verdict
        if verdict is not None:
            out["qc_result"] = Suggestion(verdict, None, report.document_id)
        return out


@dataclass(frozen=True)
class ArrivalRecipe:
    """Step 15 (ticket ai-automation/17): proposed once the carrier's arrival
    notice is read; the packing list against the PO by SKU, the containers
    the papers name, and the customs file's completeness."""

    def wants_draft(self, facts: POStepFacts) -> bool:
        return False

    def draft_values(self, facts: POStepFacts, today: date) -> dict[str, FieldInput]:
        return {}

    def expected(self, facts: POStepFacts) -> dict[str, Decimal | None]:
        return {}

    def findings(self, facts: POStepFacts) -> list[Finding]:
        notice = facts.source(DocumentType.ARRIVAL_NOTICE)
        packing = facts.source(DocumentType.PACKING_LIST)
        found = source_findings(notice)
        found += line_findings(facts.ordered(), packing, prices=False)
        found += container_findings(packing, facts.source(DocumentType.BILL_OF_LADING), notice)
        found += customs_findings(d.doc_type for d in facts.documents)
        return found

    def suggestions(self, facts: POStepFacts) -> dict[str, Suggestion]:
        found = _suggest(facts.source(DocumentType.ARRIVAL_NOTICE), {"eta": "eta"})
        return found or _suggest(facts.source(DocumentType.BILL_OF_LADING), {"eta": "eta"})


@dataclass(frozen=True)
class WarehouseRecipe:
    """Step 17 (ticket ai-automation/18): the goods-received note drafted from
    the PO's lines and what the packing list says was shipped; the counts are
    the warehouse's, typed at approval and reconciled by code
    (`domain.receipt_check`)."""

    def wants_draft(self, facts: POStepFacts) -> bool:
        return True

    def draft_values(self, facts: POStepFacts, today: date) -> dict[str, FieldInput]:
        shipped = line_quantities(facts.source(DocumentType.PACKING_LIST))
        values: dict[str, FieldInput] = {
            "received_on": FieldInput(value=today.isoformat()),
            "supplier_name": FieldInput(value=facts.case.supplier_name),
        }
        _put(values, "po_reference", facts.case.po_reference)
        rows = []
        for line in facts.case.lines:
            held = shipped.get(sku_key(line.sku_code) or "")
            rows.append(
                {
                    "sku_code": line.sku_code,
                    "variant": line.variant_label,
                    "ordered": None if line.quantity is None else str(line.quantity),
                    "shipped": None if held is None else str(held[0]),
                    "counted": None,
                }
            )
        values["items"] = FieldInput(value=rows)
        return values

    def expected(self, facts: POStepFacts) -> dict[str, Decimal | None]:
        return {}

    def findings(self, facts: POStepFacts) -> list[Finding]:
        packing = facts.source(DocumentType.PACKING_LIST)
        return source_findings(packing) + line_findings(facts.ordered(), packing, prices=False)

    def suggestions(self, facts: POStepFacts) -> dict[str, Suggestion]:
        return {}


def shipped_by_line(facts: POStepFacts) -> dict[uuid.UUID, tuple[int, str | None]]:
    """What the packing list says was shipped, per PO line (by its SKU)."""
    shipped = line_quantities(facts.source(DocumentType.PACKING_LIST))
    return {
        line.sku_id: shipped[key]
        for line in facts.case.lines
        if (key := sku_key(line.sku_code)) is not None and key in shipped
    }


def discrepancy_values(
    case: POCase, lines: Sequence[LineDiscrepancy], today: date
) -> dict[str, FieldInput]:
    """The discrepancy report, every number the warehouse's or the PO's as
    recorded with the count (`LineDiscrepancy`), never re-read."""
    values: dict[str, FieldInput] = {
        "prepared_on": FieldInput(value=today.isoformat()),
        "supplier_name": FieldInput(value=case.supplier_name),
        "items": FieldInput(
            value=[
                {
                    "sku_code": line.sku_code,
                    "ordered": None if line.ordered is None else str(line.ordered),
                    "shipped": None if line.shipped is None else str(line.shipped),
                    "counted": str(line.counted),
                    "difference": str(line.difference),
                }
                for line in lines
            ]
        ),
    }
    _put(values, "po_reference", case.po_reference)
    return values


# One recipe per step: the lane, the page and the approval read the same one.
RECIPES: Mapping[POStepKind, POStepRecipe] = {
    POStepKind.DEPOSIT_REQUEST: DepositRequestRecipe(),
    POStepKind.DEPOSIT_PAYMENT: PaymentConfirmationRecipe("số tiền đã chuyển", deposit=True),
    POStepKind.FINAL_PAYMENT_REQUEST: FinalPaymentRequestRecipe(),
    POStepKind.FINAL_PAYMENT: PaymentConfirmationRecipe("số tiền đã chuyển", deposit=False),
    POStepKind.PRODUCTION: ProductionRecipe(),
    POStepKind.QC: QcRecipe(),
    POStepKind.ARRIVAL: ArrivalRecipe(),
    POStepKind.WAREHOUSE: WarehouseRecipe(),
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
    DocumentType.REWORK_REQUEST: "AI đã soạn yêu cầu làm lại (QC không đạt theo số)",
    DocumentType.WAREHOUSE_RECEIPT: "AI đã soạn phiếu nhập kho",
}
# How long after a count its discrepancy is still drafted into a report: the
# lane reads the counts of this window, not every case ever completed.
DISCREPANCY_WINDOW = timedelta(days=30)


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
    # The warehouse's counts (ticket ai-automation/18): a discrepancy report
    # is drafted from them; a host without them drafts none.
    receipts: LineReceiptsPort | None = None

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
                if POStepKind.WAREHOUSE in enabled:
                    count.drafted += await self.discrepancy_reports(context)
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
        if not RECIPES[spec.kind].wants_draft(facts):
            return 0
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

    async def discrepancy_reports(self, context: AccessContext) -> int:
        """One discrepancy report for each PO case whose count, recorded in
        the window, differs from what was shipped; Cung ứng is told. A case
        with a report already (whatever became of it) is not drafted again."""
        if self.receipts is None:
            return 0
        since = self.clock.now() - DISCREPANCY_WINDOW
        drafted = 0
        for case_id in await self.receipts.discrepant_cases(context, since=since):
            existing = await self.drafts.latest_for_case(context, CaseKind.PO, case_id)
            if any(d.doc_type is DocumentType.DISCREPANCY_REPORT for d in existing):
                continue
            case = await self.cases.get(context, POCaseId(case_id))
            if case is None or case.workspace_id.value != context.workspace_id:
                continue
            lines = discrepancies(await self.receipts.for_case(context, case_id))
            if not lines:
                continue
            draft = await self.prepare_draft.handle(
                context,
                case_kind=CaseKind.PO,
                case_id=case_id,
                doc_type=DocumentType.DISCREPANCY_REPORT,
                values=discrepancy_values(case, lines, self.clock.now().date()),
            )
            await notify_duty_holders(
                context,
                holders=self.holders,
                notifier=self.notifier,
                duty=CaseDuty.ORDERING,
                source_key=f"supply_chain.po_step_draft:{draft.lineage_id}",
                title=f"AI đã soạn biên bản chênh lệch nhập kho: {case.supplier_name}",
                body="Số Kho đếm khác số NCC giao; biên bản và thư khiếu nại chờ người kiểm.",
                link=po_case_link(case_id),
            )
            drafted += 1
        return drafted


# ------------------------------------------------------------------- page --


@dataclass(frozen=True, slots=True)
class ResultView:
    field: ResultField
    suggestion: Suggestion | None
    # The suggestion is an amount the caller may not read.
    redacted: bool = False


@dataclass(frozen=True, slots=True)
class CountLineView:
    """A PO line the warehouse counts: what was ordered, and beside the empty
    count what the packing list says was shipped (None: not read)."""

    sku_id: uuid.UUID
    sku_code: str | None
    variant_label: str | None
    ordered: int | None
    shipped: int | None
    quote: str | None
    document_id: uuid.UUID | None


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
    # The lines to count, for a step that takes counts.
    lines: tuple[CountLineView, ...] = ()


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
            proposed=not findings
            and (spec.draft is None or spec.draft_optional or draft is not None),
            can_approve=blocked is None,
            blocked=blocked,
            missing_paper=missing_paper,
            lines=_count_lines(facts) if spec.counts else (),
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
        if spec.draft is not None and not spec.draft_optional and draft is None:
            return "Chưa có bản nháp: hệ thống soạn trong ít phút"
        if draft is not None and not spec.draft_optional and draft.status is not DraftStatus.OPEN:
            return "Bản nháp này đã được quyết"
        if missing_paper is not None:
            return "Bước này cần chứng từ của công ty trên hồ sơ trước khi duyệt"
        for action in spec.actions:
            if not await allows(
                self.authz, context, duty_scope(duties.duty_for(action)), "po_case"
            ):
                return "Duyệt bước này cần duty của bước"
        if spec.writes_prices and not await allows(
            self.authz, context, COMMERCIAL_WRITE, "po_case"
        ):
            return "Duyệt bước này ghi số tiền: cần quyền ghi dữ liệu thương mại"
        return None


def _count_lines(facts: POStepFacts) -> tuple[CountLineView, ...]:
    shipped = shipped_by_line(facts)
    packing = facts.source(DocumentType.PACKING_LIST)
    return tuple(
        CountLineView(
            sku_id=line.sku_id,
            sku_code=line.sku_code,
            variant_label=line.variant_label,
            ordered=line.quantity,
            shipped=shipped[line.sku_id][0] if line.sku_id in shipped else None,
            quote=shipped[line.sku_id][1] if line.sku_id in shipped else None,
            document_id=packing.document_id if line.sku_id in shipped else None,
        )
        for line in facts.case.lines
    )


def _counted(
    spec: POStepSpec,
    case: POCase,
    facts: POStepFacts,
    counts: Mapping[uuid.UUID, int] | None,
) -> list[LineCount]:
    """The warehouse's counts for a step that takes them, each held beside
    what was ordered and shipped; refused for a step that does not."""
    if not spec.counts:
        if counts:
            raise DomainError("bước này không nhập số đếm", details={"field": "counts"})
        return []
    shipped = shipped_by_line(facts)
    by_code = {
        line.sku_code or str(line.sku_id): shipped[line.sku_id][0]
        for line in case.lines
        if line.sku_id in shipped
    }
    return counted_lines(
        [(line.sku_id, line.sku_code or str(line.sku_id), line.quantity) for line in case.lines],
        by_code,
        counts or {},
    )


def _with_counts(fields: Mapping[str, Any], counted: Sequence[LineCount]) -> dict[str, Any]:
    """The goods-received note's fields with the count typed at approval in
    each line's `counted` cell (by SKU): the filed note carries the counts."""
    out = dict(fields)
    entry = out.get("items")
    rows = entry.get("value") if isinstance(entry, Mapping) else None
    if not counted or not isinstance(rows, list):
        return out
    by_code = {line.sku_code: line.counted for line in counted}
    out["items"] = {
        **entry,  # type: ignore[dict-item]
        "value": [
            {**row, "counted": str(by_code[row["sku_code"]])}
            if isinstance(row, Mapping) and row.get("sku_code") in by_code
            else row
            for row in rows
        ],
    }
    return out


def _result_view(field_: ResultField, suggestion: Suggestion | None, visible: bool) -> ResultView:
    if suggestion is not None and field_.kind is ResultKind.AMOUNT and not visible:
        return ResultView(field_, None, redacted=True)
    return ResultView(field_, suggestion)


async def _missing_paper(
    papers: POStepPaperGatePort,
    context: AccessContext,
    spec: POStepSpec,
    facts: POStepFacts,
    action: CaseAction | None = None,
) -> DocumentType | None:
    paper = await papers.paper_for(context, action or spec.action)
    if paper is None or paper is spec.draft:
        return None
    if facts.newest(paper) is not None:
        return None
    return paper


# --------------------------------------------------------------- approval --


def _draft_serves(spec: POStepSpec, action: CaseAction) -> bool:
    """Whether the step's paper belongs to the outcome taken: a rework request
    to a fail, every other step's paper to its one action."""
    if spec.draft is DocumentType.REWORK_REQUEST:
        return action is CaseAction.FAIL_QC
    return True


def _shipping(typed: Mapping[str, Any]) -> Shipping | None:
    shipping = Shipping(
        etd=typed.get("etd"),
        eta=typed.get("eta"),
        container_number=typed.get("container_number")
        if typed.get("qc_result") in (None, "pass")
        else None,
    )
    return None if shipping == Shipping() else shipping


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
            case ResultKind.CHOICE:
                if raw not in result.options:
                    raise DomainError(
                        f"{result.label.lower()} phải là một trong các lựa chọn của bước",
                        details={"field": result.name, "choices": list(result.options)},
                    )
                out[result.name] = raw
            case ResultKind.TEXT:
                if len(raw) > 2000:
                    raise DomainError("tối đa 2000 ký tự", details={"field": result.name})
                out[result.name] = raw
    # A result required only for one outcome (a reason for a fail).
    if spec.outcome_field is not None:
        chosen = out.get(spec.outcome_field)
        for result in spec.results:
            if (
                result.required_for is not None
                and result.required_for == chosen
                and result.name not in out
            ):
                raise DomainError(
                    f"cần nhập {result.label.lower()}", details={"field": result.name}
                )
    if "container_number" in out:
        out["container_number"] = container_number(out["container_number"])
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
# Who is told once a step is taken, by the step: the next duty, without an
# amount.
_NEXT_DUTY_NOTICE: Mapping[CaseAction, tuple[CaseDuty, str, str]] = {
    CaseAction.REQUEST_DEPOSIT: (
        CaseDuty.FINANCE,
        "Đề nghị đặt cọc chờ Kế toán chi",
        "Cung ứng đã duyệt đề nghị đặt cọc; chi ở ngân hàng, tải UNC lên hồ sơ rồi xác nhận.",
    ),
    CaseAction.REQUEST_FINAL_PAYMENT: (
        CaseDuty.FINANCE,
        "Đề nghị thanh toán chờ Kế toán chi",
        "Cung ứng đã duyệt đề nghị thanh toán; chi ở ngân hàng, tải UNC lên hồ sơ rồi xác nhận.",
    ),
    CaseAction.CONFIRM_DEPOSIT: (
        CaseDuty.ORDERING,
        "Kế toán đã xác nhận đặt cọc",
        "Đã đặt cọc cho nhà cung cấp; hồ sơ sang bước tiếp theo.",
    ),
    CaseAction.CONFIRM_PAYMENT: (
        CaseDuty.ORDERING,
        "Kế toán đã xác nhận thanh toán",
        "Đã thanh toán cho nhà cung cấp; hồ sơ sang nhận hàng vào kho.",
    ),
    CaseAction.SEND_TO_QC: (
        CaseDuty.QC,
        "Hàng chờ QC kiểm",
        "Cung ứng đã chuyển hồ sơ sang kiểm hàng; tải báo cáo QC lên hồ sơ rồi kết luận.",
    ),
    CaseAction.PASS_QC: (
        CaseDuty.LOGISTICS,
        "QC đạt, hàng chờ vận chuyển",
        "QC đã kết luận đạt; theo dõi vận đơn và giấy báo hàng đến trên hồ sơ PO.",
    ),
    CaseAction.FAIL_QC: (
        CaseDuty.ORDERING,
        "QC không đạt, cần làm lại",
        "QC đã kết luận không đạt; yêu cầu làm lại có trên hồ sơ PO để gửi nhà cung cấp.",
    ),
    CaseAction.ARRIVE_AT_PORT: (
        CaseDuty.ORDERING,
        "Hàng đã về cảng",
        "Logistics đã xác nhận hàng về cảng; bước tiếp theo là đề nghị thanh toán.",
    ),
    CaseAction.COMPLETE: (
        CaseDuty.ORDERING,
        "Hàng đã nhập kho",
        "Kho đã đếm và nhập kho; hồ sơ PO hoàn tất.",
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
        counts: Mapping[uuid.UUID, int] | None = None,
    ) -> POCase:
        spec = PO_STEPS[kind]
        typed = _typed_results(spec, results)
        action = spec.action_for(typed)
        duties = await resolve_action_duties(
            context, self.policy_override_repo, self.platform_default_duties
        )
        await self.authz.require(
            context=context,
            action=duty_scope(duties.duty_for(action)),
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
        facts = await self.sources.gather(context, case, spec)
        draft = await self._draft(context, spec, case, facts, draft_id, content_sha256)
        missing = await _missing_paper(self.papers, context, spec, facts, action)
        if missing is not None:
            raise ConflictError(
                f"{action.value} cần {missing.value} trên hồ sơ này trước",
                details={"case_id": str(case_id), "missing_document_type": missing.value},
            )
        payment = self._payment(spec, case, facts, typed)
        counted = _counted(spec, case, facts, counts)
        before = case.state
        apply_action(case, action=action, reason=typed.get("reason"))
        # The paper of the outcome taken becomes the case's; one drafted for
        # an outcome not taken (a rework request when QC passed) is closed.
        taken = draft if draft is not None and _draft_serves(spec, action) else None
        document, confirmation = await self._document(context, case, taken, counted)
        receipts = [
            NewLineReceipt(
                id=self.ids.new_uuid(),
                po_case_id=case.id.value,
                line=line,
                document_id=None if document is None else document.id.value,
            )
            for line in counted
        ]
        differing = discrepancies(counted)
        closed = (
            None
            if draft is None or taken is not None
            else NewDraftDecision(
                id=self.ids.new_uuid(),
                draft_id=draft.id,
                decision=DraftDecision.REJECTED,
                reason=f"Không dùng: bước đã đi theo {action.value}",
            )
        )
        shipping = _shipping(typed)
        audits = [
            po_case_audit(
                context,
                self.ids,
                self.clock,
                case.id,
                action.value,
                {
                    "from_state": before.value,
                    "to_state": case.state.value,
                    "via": _VIA,
                    "step": kind.value,
                    "draft_id": None if draft is None else str(draft.id),
                    "document_id": None if document is None else str(document.id),
                    **({"reason": typed["reason"]} if "reason" in typed else {}),
                    **(
                        {"container_number": shipping.container_number}
                        if shipping is not None and shipping.container_number
                        else {}
                    ),
                    **(
                        {"counted_lines": len(counted), "differing_lines": len(differing)}
                        if counted
                        else {}
                    ),
                },
            )
        ]
        if taken is not None and document is not None:
            draft = taken
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
            draft_id=None if taken is None else taken.id,
            payment=payment,
            audits=audits,
            closed=closed,
            shipping=shipping,
            receipts=receipts,
        )
        duty, title, body = _NEXT_DUTY_NOTICE[action]
        if differing:
            body += (
                f" {len(differing)} dòng có số đếm khác số NCC giao: AI soạn biên bản chênh lệch."
            )

        await notify_duty_holders(
            context,
            holders=self.holders,
            notifier=self.notifier,
            duty=duty,
            source_key=f"supply_chain.po_step:{case.id}:{action.value}:{case.version}",
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
        if draft_id is None and spec.draft_optional:
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
            if spec.writes_prices and (draft.fields.get(name) or {}).get("value") in (None, "")
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
        self,
        context: AccessContext,
        case: POCase,
        draft: DocumentDraft | None,
        counted: Sequence[LineCount] = (),
    ) -> tuple[NewCaseDocument | None, NewDraftDecision | None]:
        if draft is None:
            return None, None
        template = await self.templates.resolve(context, draft.template_id, draft.template_version)
        rendered = self.renderer.render(
            template, template_values(_with_counts(draft.fields, counted), hide_prices=False)
        )
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
    "DISCREPANCY_WINDOW",
    "PO_STEPS_LANE",
    "RECIPES",
    "ApprovePOStep",
    "CountLineView",
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
