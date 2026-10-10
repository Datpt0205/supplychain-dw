"""Step 10: the purchase order drafted by code, approved by Cung ứng (ticket
ai-automation/14; ADR 0017, ADR 0025, ADR 0026).

- **The lane** (`PreparePurchaseOrders`, `supply_chain_purchase_orders`): for
  each workspace whose tenant's step preparation policy says
  `purchase_order`, each PO case awaiting its PO gets ONE draft
  (`purchase_order`), filled by code from the PO case (supplier, lines,
  quantities, any term or price a person already set), the product's latest
  BM04 version (price, currency, Incoterm) and the supplier's confirmation
  read at step 8 (a term it states differently is a conflict and an empty
  field). Totals are code's. Cung ứng (the `create_po` duty) and Kế toán (the
  `finance` duty) are told the draft is there, once, without a price. No
  model is asked. A case whose draft was rejected is not drafted again.
- **The page** (`GetPurchaseOrderProposal`): the latest draft of the case and
  what code finds in it now: each term and line cell still missing, each
  total that is not code's, each conflict with the confirmation; whether the
  caller may approve. No price in any of it (the draft's fields are read
  through the drafts API, which hides prices without the scope).
- **The approval** (`ApprovePurchaseOrder`): `create_po`'s duty scope AND
  `supply_chain.commercial.write` (it writes prices). The draft must be the
  open latest version the person saw (its content hash). The PO number is
  typed here, into a new version of the draft; every total must be code's,
  every line must have a quantity and a price, the currency must be set. Then
  ONE transaction: the version confirmed, the PO rendered from it as the
  `purchase_order` document, `create_po` taken (state, number, quantities),
  the case's commercial terms and line prices set, every audit event. Kế toán
  is told after it commits, without a price.
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
from dw_kernel.pagination import MAX_PAGE_SIZE, Page, PageRequest, page_request
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import AuthorizationPort, PolicyOverridePort
from dw_platform.domain.audit import AuditEvent, system_actor
from dw_supply_chain.action_duties import CaseDuty, SupplyChainActionDuties
from dw_supply_chain.application.bm04_prefill import Bm04ProfileReadPort
from dw_supply_chain.application.commercial import TERMS_SET, allows
from dw_supply_chain.application.document_drafts import (
    DraftTemplatesPort,
    FieldInput,
    NewDocumentDraft,
    NewDraftDecision,
    PrepareDocumentDraft,
    compute_gaps,
    template_values,
)
from dw_supply_chain.application.handlers import (
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
from dw_supply_chain.application.step_preparation import (
    CaseDocumentListPort,
    CaseDraftsPort,
    ExtractionReadingsPort,
    WorkspacesWithCasesPort,
    resolve_step_preparation,
)
from dw_supply_chain.domain.case_document import CaseDocumentId, CaseKind, DocumentType, ObjectKey
from dw_supply_chain.domain.commercial import CommercialTerms, Incoterm, POCommercial
from dw_supply_chain.domain.document_draft import (
    DocumentDraft,
    DraftDecision,
    DraftStatus,
    content_sha256,
    field_value,
)
from dw_supply_chain.domain.extraction import EXTRACTION_SPECS, ExtractionStatus, parse_quantity
from dw_supply_chain.domain.po_case import CaseAction, CaseState, POCase, POCaseId
from dw_supply_chain.domain.purchase_order_draft import (
    TERM_WORDS,
    decimal_text,
    missing_terms,
    missing_words,
    po_terms,
    po_totals,
    stated_totals_differ,
    to_decimal,
)
from dw_supply_chain.domain.step_proposal import Finding
from dw_supply_chain.domain.supplier_terms import TermRow, bm04_terms, compare_terms
from dw_supply_chain.step_preparation_policy import SupplyChainStepPreparation

logger = logging.getLogger(__name__)

PURCHASE_ORDER_LANE = "supply_chain_purchase_orders"
PO_DRAFT_CONFIRMED = "supply_chain.document_draft.confirmed"
PO_DOCUMENT_ADDED = "supply_chain.case_document.ai_prepared"
_VIA = "purchase_order_approved"


def po_lane_context(tenant_id: uuid.UUID, workspace_id: uuid.UUID) -> AccessContext:
    """The lane in one workspace: its own actor, no role, no scope."""
    return AccessContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        principal_id=system_actor(PURCHASE_ORDER_LANE).value,
        roles=frozenset(),
        scopes=frozenset(),
        plan_id="system",
    )


# ------------------------------------------------------------------ ports --


class POCaseStorePort(Protocol):
    async def get(self, context: AccessContext, case_id: POCaseId) -> POCase | None: ...

    async def list_page(
        self, context: AccessContext, request: PageRequest, case_filter: POCaseListFilter
    ) -> Page[POCase]: ...


class POCommercialReadPort(Protocol):
    async def read(self, context: AccessContext, case_id: uuid.UUID) -> POCommercial: ...


class PurchaseOrderOutcomesPort(Protocol):
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
        """The draft version and its confirmation, the document (its
        `draft_id`), the PO step (optimistic on the case's version, its line
        quantities), the terms and line prices, every audit event: all or
        nothing. A PO number already in the tenant, or a version already
        decided, is a `ConflictError`."""
        ...


# ------------------------------------------------------------------ facts --


@dataclass(frozen=True)
class PurchaseOrderSources:
    """What a PO is made from besides the PO case: the product's BM04 and
    the supplier's confirmation read at step 8, both under the caller's
    tenant and workspace."""

    profiles: Bm04ProfileReadPort
    documents: CaseDocumentListPort
    readings: ExtractionReadingsPort

    async def reply_rows(
        self, context: AccessContext, product_case_id: uuid.UUID, profile: Any
    ) -> list[TermRow]:
        """The newest confirmation's terms against the BM04 (step 8's
        comparison), or none when either is missing or unread."""
        if profile is None:
            return []
        documents = [
            d
            for d in await self.documents.list_for_case(context, CaseKind.PRODUCT, product_case_id)
            if d.doc_type is DocumentType.SUPPLIER_CONFIRMATION_EMAIL
        ]
        if not documents:
            return []
        newest = max(documents, key=lambda d: d.version)
        spec = EXTRACTION_SPECS[DocumentType.SUPPLIER_CONFIRMATION_EMAIL]
        reading = next(
            (
                r
                for r in await self.readings.readings(context, [newest.id.value])
                if r.sha256 == newest.sha256
                and (r.prompt_id, r.prompt_version) == (spec.prompt_id, spec.prompt_version)
                and r.status is ExtractionStatus.EXTRACTED
            ),
            None,
        )
        if reading is None:
            return []
        return compare_terms(reading.fields, bm04_terms(profile))


def _number(text: str | None) -> str | None:
    if text is None:
        return None
    parsed = parse_quantity(text)
    return None if parsed is None else decimal_text(parsed)


def po_values(
    case: POCase,
    commercial: POCommercial,
    profile: Any,
    reply: Sequence[TermRow],
    today: date,
) -> tuple[dict[str, FieldInput], list[Finding]]:
    """The draft's fields, all code's, and what code found making them: a
    term the confirmation states differently from the BM04, no BM04 at all."""
    terms = commercial.terms
    case_terms = {
        "currency": terms.currency,
        "incoterm": None if terms.incoterm is None else terms.incoterm.value,
        "payment_terms": terms.payment_terms,
        "deposit_percent": None
        if terms.deposit_percent is None
        else decimal_text(terms.deposit_percent),
        "delivery_date": None
        if terms.expected_delivery_date is None
        else terms.expected_delivery_date.isoformat(),
    }
    bm04: dict[str, str | None] = {}
    if profile is not None:
        c = profile.commercial
        bm04 = {
            "currency": c.currency,
            "incoterm": None if c.incoterm is None else c.incoterm.value,
            "unit_price": None if c.unit_price is None else decimal_text(c.unit_price),
        }
    chosen = po_terms(case_terms, bm04, reply)
    values: dict[str, FieldInput] = {
        "po_date": FieldInput(value=today.isoformat()),
        "supplier_name": FieldInput(value=case.supplier_name),
    }
    for name in ("currency", "incoterm", "payment_terms", "deposit_percent", "delivery_date"):
        choice = chosen[name]
        value = _number(choice.value) if name == "deposit_percent" else choice.value
        if value is not None:
            values[name] = FieldInput(value=value)
    unit_price = chosen["unit_price"]
    default_price = _number(unit_price.value)
    own_prices = {line.sku_id: line.unit_price for line in commercial.lines}
    rows: list[tuple[int | None, Decimal | None]] = []
    line_rows: list[dict[str, str | None]] = []
    for line in case.lines:
        own = own_prices.get(line.sku_id)
        price = decimal_text(own) if own is not None else default_price
        rows.append((line.quantity, to_decimal(price)))
        line_rows.append(
            {
                "sku_code": line.sku_code,
                "description": line.variant_label,
                "quantity": None if line.quantity is None else str(line.quantity),
                "unit_price": price,
                "line_total": None,
            }
        )
    totals = po_totals(
        rows, to_decimal(values["deposit_percent"].value) if "deposit_percent" in values else None
    )
    for row, total in zip(line_rows, totals.line_totals, strict=True):
        row["line_total"] = None if total is None else decimal_text(total)
    values["lines"] = FieldInput(value=line_rows)
    if totals.order_total is not None:
        values["order_total"] = FieldInput(value=decimal_text(totals.order_total))
    if totals.deposit_amount is not None:
        values["deposit_amount"] = FieldInput(value=decimal_text(totals.deposit_amount))
    findings: list[Finding] = []
    if profile is None:
        findings.append(
            Finding(
                "bm04_missing",
                DocumentType.PRODUCT_PROFILE_BM04.value,
                "Sản phẩm chưa có BM04 trong ứng dụng: đơn giá, tiền tệ do người điền",
            )
        )
    for name, choice in chosen.items():
        if choice.source == "conflict":
            findings.append(
                Finding(
                    "term_conflict",
                    name,
                    f"Thư NCC xác nhận khác BM04 ở {TERM_WORDS.get(name, name)}; ô để trống,"
                    " người kiểm chứng từ rồi điền",
                )
            )
    return values, findings


def draft_findings(fields: Mapping[str, Any]) -> list[Finding]:
    """What code finds in a PO draft as it stands (a person may have edited
    it): what is still missing, and every total that is not code's."""
    found = [
        Finding(
            "term_missing", name, f"PO còn thiếu {missing_words(name)}; người điền trước khi duyệt"
        )
        for name in missing_terms(fields)
    ]
    found += [
        Finding(
            "total_differs",
            name,
            "Tổng ghi trong bản nháp khác số hệ thống tính; sửa trước khi duyệt",
        )
        for name in stated_totals_differ(fields)
    ]
    return found


# ------------------------------------------------------------------- lane --


@dataclass(slots=True)
class PurchaseOrderCount:
    drafted: int = 0
    failed_workspaces: int = 0


@dataclass(frozen=True)
class PreparePurchaseOrders:
    """The worker lane `supply_chain_purchase_orders` (module docstring)."""

    workspaces: WorkspacesWithCasesPort
    cases: POCaseStorePort
    commercial: POCommercialReadPort
    sources: PurchaseOrderSources
    drafts: CaseDraftsPort
    prepare_draft: PrepareDocumentDraft
    policy_override_repo: PolicyOverridePort
    platform_default_policy: SupplyChainStepPreparation
    holders: ScopeHoldersPort
    notifier: ReviewNotifierPort
    clock: UtcClock

    async def run(self) -> PurchaseOrderCount:
        count = PurchaseOrderCount()
        for tenant_id, workspace_id in await self.workspaces.workspaces():
            context = po_lane_context(tenant_id, workspace_id)
            try:
                policy = await resolve_step_preparation(
                    context, self.policy_override_repo, self.platform_default_policy
                )
                if policy.purchase_order:
                    count.drafted += await self._workspace(context)
            except Exception:
                logger.exception(
                    "purchase order drafting failed for a workspace",
                    extra={"tenant_id": str(tenant_id), "workspace_id": str(workspace_id)},
                )
                count.failed_workspaces += 1
        return count

    async def _workspace(self, context: AccessContext) -> int:
        case_filter = POCaseListFilter(state=CaseState.ORDER_REQUESTED)
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
                drafted += await self.draft(context, listed.id)
            if page.next_cursor is None:
                return drafted
            cursor = page.next_cursor

    async def draft(self, context: AccessContext, case_id: POCaseId) -> int:
        """One draft for a PO case awaiting its PO that has none (whatever
        became of an earlier one: a rejected draft is a person's no)."""
        case = await self.cases.get(context, case_id)
        if case is None or case.state is not CaseState.ORDER_REQUESTED:
            return 0
        existing = await self.drafts.latest_for_case(context, CaseKind.PO, case.id.value)
        if any(d.doc_type is DocumentType.PURCHASE_ORDER for d in existing):
            return 0
        profile = (
            None
            if case.product_dev_case_id is None
            else await self.sources.profiles.latest(context, case.product_dev_case_id)
        )
        reply = (
            []
            if case.product_dev_case_id is None
            else await self.sources.reply_rows(context, case.product_dev_case_id, profile)
        )
        values, _ = po_values(
            case,
            await self.commercial.read(context, case.id.value),
            profile,
            reply,
            self.clock.now().date(),
        )
        draft = await self.prepare_draft.handle(
            context,
            case_kind=CaseKind.PO,
            case_id=case.id.value,
            doc_type=DocumentType.PURCHASE_ORDER,
            values=values,
        )
        # Cung ứng approves it, Kế toán prepares the deposit: both are told
        # it is there, once, by the case alone (no price, no line).
        for duty in (CaseDuty.ORDERING, CaseDuty.FINANCE):
            await notify_duty_holders(
                context,
                holders=self.holders,
                notifier=self.notifier,
                duty=duty,
                source_key=f"supply_chain.po_draft:{draft.lineage_id}:{duty.value}",
                title=f"AI đã soạn PO nháp: {case.supplier_name}",
                body="PO nháp chờ Cung ứng kiểm, nhập số PO và duyệt trên hồ sơ PO.",
                link=po_case_link(case.id.value),
            )
        return 1


# ------------------------------------------------------------------- page --


@dataclass(frozen=True, slots=True)
class PurchaseOrderProposal:
    draft: DocumentDraft | None
    findings: tuple[Finding, ...]
    can_approve: bool
    # Why the caller may not approve, in words; None when they may.
    blocked: str | None


async def _po_in_workspace(
    cases: POCaseStorePort, context: AccessContext, case_id: POCaseId
) -> POCase:
    case = await cases.get(context, case_id)
    if case is None or case.workspace_id.value != context.workspace_id:
        raise NotFoundError("PO case not found", details={"case_id": str(case_id)})
    return case


def _latest_po_draft(drafts: Sequence[DocumentDraft]) -> DocumentDraft | None:
    mine = [d for d in drafts if d.doc_type is DocumentType.PURCHASE_ORDER]
    return max(mine, key=lambda d: d.created_at) if mine else None


@dataclass(frozen=True)
class GetPurchaseOrderProposal:
    cases: POCaseStorePort
    commercial: POCommercialReadPort
    sources: PurchaseOrderSources
    drafts: CaseDraftsPort
    policy_override_repo: PolicyOverridePort
    platform_default_duties: SupplyChainActionDuties
    authz: AuthorizationPort
    clock: UtcClock

    async def handle(self, context: AccessContext, case_id: POCaseId) -> PurchaseOrderProposal:
        await self.authz.require(
            context=context, action=PO_CASE_READ, resource_type="po_case", resource_id=str(case_id)
        )
        case = await _po_in_workspace(self.cases, context, case_id)
        draft = _latest_po_draft(
            await self.drafts.latest_for_case(context, CaseKind.PO, case.id.value)
        )
        if draft is None:
            return PurchaseOrderProposal(None, (), False, "Chưa có PO nháp cho hồ sơ này")
        findings = list(draft_findings(draft.fields))
        if case.product_dev_case_id is not None:
            profile = await self.sources.profiles.latest(context, case.product_dev_case_id)
            reply = await self.sources.reply_rows(context, case.product_dev_case_id, profile)
            _, made = po_values(
                case,
                await self.commercial.read(context, case.id.value),
                profile,
                reply,
                self.clock.now().date(),
            )
            findings = [*made, *findings]
        duties = await resolve_action_duties(
            context, self.policy_override_repo, self.platform_default_duties
        )
        blocked = None
        if case.state is not CaseState.ORDER_REQUESTED:
            blocked = "Hồ sơ đã qua bước tạo PO"
        elif draft.status is not DraftStatus.OPEN:
            blocked = "Bản nháp này đã được quyết"
        elif not await allows(
            self.authz, context, duty_scope(duties.duty_for(CaseAction.CREATE_PO)), "po_case"
        ):
            blocked = "Duyệt PO cần duty tạo PO (Cung ứng)"
        elif not await allows(self.authz, context, COMMERCIAL_WRITE, "po_case"):
            blocked = "Duyệt PO ghi giá: cần quyền ghi dữ liệu thương mại"
        return PurchaseOrderProposal(draft, tuple(findings), blocked is None, blocked)


# --------------------------------------------------------------- approval --


def _terms_of(fields: Mapping[str, Any]) -> CommercialTerms:
    def text(name: str) -> str | None:
        value = field_value(fields, name)
        return value if isinstance(value, str) and value.strip() else None

    incoterm = text("incoterm")
    delivery = text("delivery_date")
    try:
        return CommercialTerms.of(
            currency=text("currency"),
            incoterm=None if incoterm is None else Incoterm(incoterm),
            payment_terms=text("payment_terms"),
            deposit_percent=text("deposit_percent"),
            expected_delivery_date=None if delivery is None else date.fromisoformat(delivery),
        )
    except ValueError as exc:
        raise DomainError("điều khoản PO không hợp lệ", details={"error": str(exc)}) from exc


@dataclass(frozen=True)
class ApprovePurchaseOrder:
    cases: POCaseStorePort
    drafts: CaseDraftsPort
    templates: DraftTemplatesPort
    renderer: DocumentRendererPort
    storage: CaseDocumentStoragePort
    outcomes: PurchaseOrderOutcomesPort
    policy_override_repo: PolicyOverridePort
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
        draft_id: uuid.UUID,
        content_sha256: str,
        po_reference: str,
    ) -> POCase:
        duties = await resolve_action_duties(
            context, self.policy_override_repo, self.platform_default_duties
        )
        await self.authz.require(
            context=context,
            action=duty_scope(duties.duty_for(CaseAction.CREATE_PO)),
            resource_type="po_case",
            resource_id=str(case_id),
        )
        await self.authz.require(
            context=context,
            action=COMMERCIAL_WRITE,
            resource_type="po_case",
            resource_id=str(case_id),
        )
        case = await _po_in_workspace(self.cases, context, case_id)
        draft = await self.drafts.get(context, draft_id)
        if (
            draft is None
            or draft.case_kind is not CaseKind.PO
            or draft.case_id != case.id.value
            or draft.doc_type is not DocumentType.PURCHASE_ORDER
        ):
            raise NotFoundError("no PO draft by that id on this case")
        if draft.status is not DraftStatus.OPEN or draft.content_sha256 != content_sha256:
            raise ConflictError(
                "bản nháp PO đã đổi hoặc đã được quyết; mở lại để xem bản mới nhất",
                details={"draft_id": str(draft_id)},
            )
        reference = po_reference.strip()
        if not reference:
            raise DomainError("cần nhập số PO", details={"field": "po_reference"})
        template = await self.templates.resolve(context, draft.template_id, draft.template_version)
        fields = dict(draft.fields)
        fields["po_reference"] = {
            "value": reference,
            "source": {"edited_by": str(context.principal_id)},
        }
        refused = [n for n in missing_terms(fields) if n.startswith("lines") or n == "currency"]
        differ = stated_totals_differ(fields)
        if refused or differ:
            raise DomainError(
                "PO chưa duyệt được: còn thiếu ô bắt buộc hoặc có tổng khác số hệ thống tính",
                details={"missing": refused, "totals_differ": differ},
            )
        by_code = {line.sku_code: line for line in case.lines}
        quantities: dict[uuid.UUID, int] = {}
        prices: dict[uuid.UUID, Decimal] = {}
        rows = field_value(fields, "lines")
        for row in rows if isinstance(rows, list) else []:
            line = by_code.get(str(row.get("sku_code") or ""))
            if line is None:
                raise DomainError(
                    "dòng PO không khớp SKU nào của hồ sơ",
                    details={"sku_code": row.get("sku_code")},
                )
            quantities[line.sku_id] = int(str(row["quantity"]))
            price = to_decimal(row.get("unit_price"))
            assert price is not None  # `missing_terms` refused a line without one
            prices[line.sku_id] = price
        terms = _terms_of(fields)
        before = case.state
        case.create_po(po_reference=reference, order_kind=case.order_kind, quantities=quantities)
        new_id = self.ids.new_uuid()
        new_draft = NewDocumentDraft(
            id=new_id,
            lineage_id=draft.lineage_id,
            version=draft.version + 1,
            case_kind=CaseKind.PO,
            case_id=case.id.value,
            doc_type=DocumentType.PURCHASE_ORDER,
            template_id=draft.template_id,
            template_version=draft.template_version,
            prompt_id=None,
            prompt_version=None,
            fields=fields,
            gaps=compute_gaps(template.spec, fields),
            sources=[s.as_json() for s in draft.sources],
            content_sha256=content_sha256_of(template.spec.ref, fields),
        )
        rendered = self.renderer.render(template, template_values(fields, hide_prices=False))
        document_id = self.ids.new_uuid()
        key = ObjectKey.build(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            case_kind=CaseKind.PO,
            case_id=case.id.value,
            document_id=document_id,
        ).value
        sha = hashlib.sha256(rendered.data).hexdigest()
        # Outside the transaction, as an upload is: a row that then fails
        # leaves an object the orphan sweep removes.
        await self.storage.put(key, rendered.data, rendered.content_type)
        document = NewCaseDocument(
            id=CaseDocumentId(document_id),
            case_kind=CaseKind.PO,
            case_id=case.id.value,
            doc_type=DocumentType.PURCHASE_ORDER,
            object_key=key,
            filename=f"{template.spec.title}.{rendered.extension}",
            content_type=rendered.content_type,
            size_bytes=len(rendered.data),
            sha256=sha,
        )
        audits = [
            po_case_audit(
                context,
                self.ids,
                self.clock,
                case.id,
                CaseAction.CREATE_PO.value,
                {
                    "from_state": before.value,
                    "to_state": case.state.value,
                    "po_reference": reference,
                    "order_kind": case.order_kind.value,
                    "via": _VIA,
                    "draft_id": str(new_id),
                    "document_id": str(document_id),
                },
            ),
            self._event(
                context,
                TERMS_SET,
                "po_commercial",
                case.id.value,
                {"lines_priced": len(prices), "via": _VIA},
            ),
            self._event(
                context,
                PO_DRAFT_CONFIRMED,
                "document_draft",
                new_id,
                {"lineage_id": str(draft.lineage_id), "document_id": str(document_id)},
            ),
            self._event(
                context,
                PO_DOCUMENT_ADDED,
                "case_document",
                document_id,
                {
                    "case_kind": CaseKind.PO.value,
                    "case_id": str(case.id),
                    "doc_type": DocumentType.PURCHASE_ORDER.value,
                    "draft_id": str(new_id),
                    "sha256": sha,
                },
            ),
        ]
        await self.outcomes.apply(
            context,
            case=case,
            new_draft=new_draft,
            confirmation=NewDraftDecision(
                id=self.ids.new_uuid(),
                draft_id=new_id,
                decision=DraftDecision.CONFIRMED,
                reason=None,
            ),
            document=document,
            terms=terms,
            line_prices=prices,
            audits=audits,
        )
        await notify_duty_holders(
            context,
            holders=self.holders,
            notifier=self.notifier,
            duty=CaseDuty.FINANCE,
            source_key=f"supply_chain.po_created:{case.id}",
            title=f"Đã tạo PO {reference}",
            body=(
                f"Cung ứng đã duyệt PO {reference} với {case.supplier_name}; đơn đặt hàng là"
                " chứng từ của hồ sơ PO."
            ),
            link=po_case_link(case.id.value),
        )
        return case

    def _event(
        self,
        context: AccessContext,
        action: str,
        resource_type: str,
        resource_id: uuid.UUID,
        details: dict[str, Any],
    ) -> AuditEvent:
        return po_audit_event(
            context, self.ids, self.clock, action, resource_type, resource_id, details
        )


def content_sha256_of(template_ref: str, fields: Mapping[str, Any]) -> str:
    return content_sha256(DocumentType.PURCHASE_ORDER, template_ref, fields)


def po_audit_event(
    context: AccessContext,
    ids: IdGenerator,
    clock: UtcClock,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID,
    details: dict[str, Any],
) -> AuditEvent:
    return AuditEvent(
        id=ids.new_uuid(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        actor_id=UserId(context.principal_id),
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id),
        occurred_at=clock.now(),
        details=details,
    )


__all__ = [
    "PURCHASE_ORDER_LANE",
    "ApprovePurchaseOrder",
    "GetPurchaseOrderProposal",
    "POCaseStorePort",
    "POCommercialReadPort",
    "PreparePurchaseOrders",
    "PurchaseOrderCount",
    "PurchaseOrderOutcomesPort",
    "PurchaseOrderProposal",
    "PurchaseOrderSources",
    "draft_findings",
    "po_lane_context",
    "po_values",
]
