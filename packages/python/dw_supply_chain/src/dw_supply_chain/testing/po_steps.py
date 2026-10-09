"""An in-memory world for a PO case's steps 11-17 prepared by code (tickets
ai-automation/15-18): their unit tests and the `supply_chain.po_step` eval
grader run the REAL `PreparePOSteps`, `GetPOStepProposal` and `ApprovePOStep`
over it, with the shipped templates and the real DOCX renderer.

Every store keeps its port's promise: a reader sees only its own tenant AND
workspace (RLS), a PO case is saved only over the version it was read at, a
draft version is decided once, and `apply` is all or nothing.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from dw_agent_runtime.adapters.docx_templates import DocxRenderer
from dw_kernel.errors import ConflictError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.ports import FixedClock, Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.application.document_drafts import NewDraftDecision, PrepareDocumentDraft
from dw_supply_chain.application.handlers import COMMERCIAL_READ, COMMERCIAL_WRITE, PO_CASE_READ
from dw_supply_chain.application.po_papers import PaperGateResolver
from dw_supply_chain.application.po_steps import (
    ApprovePOStep,
    GetPOStepProposal,
    POStepSources,
    PreparePOSteps,
    po_steps_lane_context,
)
from dw_supply_chain.application.ports import NewCaseDocument
from dw_supply_chain.application.step_preparation import StoredReading
from dw_supply_chain.domain.case_document import (
    CaseDocument,
    CaseDocumentId,
    CaseKind,
    DocumentType,
)
from dw_supply_chain.domain.commercial import (
    CommercialTerms,
    NewPOPayment,
    PaymentKind,
    POPayment,
    SupplierBankAccount,
    account_digest,
)
from dw_supply_chain.domain.extraction import ACCOUNTS_FIELD, EXTRACTION_SPECS, ExtractionStatus
from dw_supply_chain.domain.po_case import CaseState, OrderKind, POCase, POCaseId, POCaseLine
from dw_supply_chain.po_documents_policy import load_supply_chain_po_documents
from dw_supply_chain.step_preparation_policy import SupplyChainStepPreparation
from dw_supply_chain.testing.purchase_orders import (
    ELMICH_PREPARATION,
    FINANCE,
    NOW,
    ORDERING,
    PLATFORM_DUTIES,
    InMemoryPOStore,
    RecordingNotifier,
    StaticHolders,
    _Workspaces,
)
from dw_supply_chain.testing.step_preparation import (
    REPO_ROOT,
    InMemoryDocuments,
    InMemoryDrafts,
    InMemoryReadings,
    InMemoryStorage,
    PlatformTemplates,
)

ELMICH_PO_DOCUMENTS = load_supply_chain_po_documents(
    REPO_ROOT / "scripts" / "elmich_po_documents_override.yaml"
)
MASTER_ACCOUNT = "0071000123456"
OTHER_ACCOUNT = "0071000999888"
PO_STEP_SCOPES = frozenset({ORDERING, FINANCE, COMMERCIAL_READ, COMMERCIAL_WRITE, PO_CASE_READ})


@dataclass
class Overrides:
    """`PolicyOverridePort`: the tenant's stored policies by id."""

    stored: dict[str, dict[str, Any]] = field(default_factory=dict)

    async def get(self, context: AccessContext, policy_id: str) -> dict[str, Any] | None:
        return self.stored.get(policy_id)

    async def put(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("not exercised by the PO steps")


@dataclass
class InMemoryAccounts:
    """`SupplierAccountPort` under RLS: (tenant, workspace, supplier name,
    account)."""

    rows: list[tuple[uuid.UUID, uuid.UUID, str, SupplierBankAccount]] = field(default_factory=list)

    async def account_for(
        self, context: AccessContext, supplier_name: str
    ) -> SupplierBankAccount | None:
        mine = [
            a
            for tenant, workspace, name, a in self.rows
            if (tenant, workspace) == (context.tenant_id, context.workspace_id)
            and name.casefold() == supplier_name.casefold()
        ]
        return max(mine, key=lambda a: a.version) if mine else None


@dataclass
class InMemoryPOStepOutcomes:
    """`POStepOutcomesPort`: all or nothing."""

    store: InMemoryPOStore
    drafts: InMemoryDrafts
    documents: InMemoryDocuments
    audits: list[AuditEvent] = field(default_factory=list)
    applied: int = 0

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
        stored = self.store.cases[case.id.value]
        if stored.version != case.version - 1:
            raise ConflictError("PO case was modified concurrently")
        if confirmation is not None:
            if confirmation.draft_id in self.drafts.decisions:
                raise ConflictError("this draft version already has a decision")
            self.drafts.record_decision(context, confirmation)
        if document is not None:
            version = 1 + sum(
                1
                for d in self.documents.rows
                if d.case_id == document.case_id and d.doc_type is document.doc_type
            )
            self.documents.rows.append(
                CaseDocument(
                    id=document.id,
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    case_kind=document.case_kind,
                    case_id=document.case_id,
                    doc_type=document.doc_type,
                    object_key=document.object_key,
                    filename=document.filename,
                    content_type=document.content_type,
                    size_bytes=document.size_bytes,
                    sha256=document.sha256,
                    version=version,
                    uploaded_by=context.principal_id,
                    uploaded_at=NOW,
                )
            )
        case.pop_pending_transitions()
        case.pop_pending_line_quantities()
        self.store.cases[case.id.value] = case
        if payment is not None:
            held = self.store.payments.setdefault(case.id.value, [])
            held.append(
                POPayment(
                    id=payment.id,
                    po_case_id=payment.po_case_id,
                    kind=payment.kind,
                    version=1 + sum(1 for p in held if p.kind is payment.kind),
                    amount=payment.amount,
                    currency=payment.currency,
                    due_date=payment.due_date,
                    paid_on=payment.paid_on,
                    document_id=payment.document_id,
                    recorded_by=context.principal_id,
                    recorded_at=NOW,
                )
            )
        self.audits.extend(audits)
        self.applied += 1


@dataclass
class POStepWorld:
    tenant_id: uuid.UUID = field(default_factory=uuid.uuid4)
    workspace_id: uuid.UUID = field(default_factory=uuid.uuid4)
    store: InMemoryPOStore = field(default_factory=InMemoryPOStore)
    accounts: InMemoryAccounts = field(default_factory=InMemoryAccounts)
    documents: InMemoryDocuments = field(default_factory=InMemoryDocuments)
    readings: InMemoryReadings = field(default_factory=InMemoryReadings)
    drafts: InMemoryDrafts = field(default_factory=InMemoryDrafts)
    storage: InMemoryStorage = field(default_factory=InMemoryStorage)
    templates: PlatformTemplates = field(default_factory=PlatformTemplates)
    notifier: RecordingNotifier = field(default_factory=RecordingNotifier)
    clock: FixedClock = field(default_factory=lambda: FixedClock(NOW))
    person: uuid.UUID = field(default_factory=uuid.uuid4)
    buyer: uuid.UUID = field(default_factory=uuid.uuid4)
    accountant: uuid.UUID = field(default_factory=uuid.uuid4)
    preparation: SupplyChainStepPreparation = ELMICH_PREPARATION
    overrides: Overrides = field(init=False)
    outcomes: InMemoryPOStepOutcomes = field(init=False)

    def __post_init__(self) -> None:
        self.outcomes = InMemoryPOStepOutcomes(self.store, self.drafts, self.documents)
        self.overrides = Overrides(
            {
                "supply_chain_step_preparation": self.preparation.model_dump(mode="json"),
                "supply_chain_po_documents": ELMICH_PO_DOCUMENTS.model_dump(mode="json"),
            }
        )

    def context(
        self,
        scopes: frozenset[str] = PO_STEP_SCOPES,
        *,
        tenant: uuid.UUID | None = None,
        workspace: uuid.UUID | None = None,
    ) -> AccessContext:
        return AccessContext(
            tenant_id=tenant or self.tenant_id,
            workspace_id=workspace or self.workspace_id,
            principal_id=self.person,
            roles=frozenset(),
            scopes=scopes,
            plan_id="professional",
        )

    def lane_context(self) -> AccessContext:
        return po_steps_lane_context(self.tenant_id, self.workspace_id)

    def sources(self) -> POStepSources:
        return POStepSources(
            commercial=self.store,
            accounts=self.accounts,
            documents=self.documents,
            readings=self.readings,
        )

    def papers(self) -> PaperGateResolver:
        return PaperGateResolver(
            documents=self.documents,
            policy_override_repo=self.overrides,
            platform_default=ELMICH_PO_DOCUMENTS,
        )

    def holders(self) -> StaticHolders:
        return StaticHolders({ORDERING: [self.buyer], FINANCE: [self.accountant]})

    def lane(self) -> PreparePOSteps:
        return PreparePOSteps(
            workspaces=_Workspaces([(self.tenant_id, self.workspace_id)]),
            cases=self.store,
            sources=self.sources(),
            drafts=self.drafts,
            prepare_draft=PrepareDocumentDraft(
                cases={CaseKind.PO: self.store},
                drafts=self.drafts,
                templates=self.templates,
                ids=Uuid4Generator(),
                clock=self.clock,
            ),
            policy_override_repo=self.overrides,
            platform_default_policy=self.preparation,
            platform_default_duties=PLATFORM_DUTIES,
            holders=self.holders(),
            notifier=self.notifier,
            clock=self.clock,
        )

    def page(self) -> GetPOStepProposal:
        return GetPOStepProposal(
            cases=self.store,
            sources=self.sources(),
            drafts=self.drafts,
            papers=self.papers(),
            policy_override_repo=self.overrides,
            platform_default_policy=self.preparation,
            platform_default_duties=PLATFORM_DUTIES,
            authz=ScopeAuthorizationService(),
        )

    def approver(self) -> ApprovePOStep:
        return ApprovePOStep(
            cases=self.store,
            sources=self.sources(),
            drafts=self.drafts,
            templates=self.templates,
            renderer=DocxRenderer(),
            storage=self.storage,
            outcomes=self.outcomes,
            papers=self.papers(),
            policy_override_repo=self.overrides,
            platform_default_policy=self.preparation,
            platform_default_duties=PLATFORM_DUTIES,
            holders=self.holders(),
            notifier=self.notifier,
            authz=ScopeAuthorizationService(),
            ids=Uuid4Generator(),
            clock=self.clock,
        )

    # -- seeding -------------------------------------------------------------

    def add_case(
        self,
        state: CaseState,
        *,
        lines: Sequence[tuple[int, str]] = ((500, "2.50"), (1200, "2.50")),
        currency: str | None = "USD",
        deposit_percent: str | None = "30",
        tenant: uuid.UUID | None = None,
        workspace: uuid.UUID | None = None,
    ) -> POCase:
        case = POCase(
            id=POCaseId(uuid.uuid4()),
            tenant_id=TenantId(tenant or self.tenant_id),
            workspace_id=WorkspaceId(workspace or self.workspace_id),
            po_reference="PO-2026-0101",
            supplier_name="Công ty Gia dụng Minh Phát",
            state=state,
            order_kind=OrderKind.NEW,
            lines=tuple(
                POCaseLine(
                    sku_id=uuid.uuid4(),
                    quantity=quantity,
                    sku_code=f"EL-00001-{i:02d}",
                    variant_label=f"Biến thể {i}",
                )
                for i, (quantity, _) in enumerate(lines, start=1)
            ),
        )
        self.store.cases[case.id.value] = case
        self.store.terms[case.id.value] = CommercialTerms.of(
            currency=currency,
            incoterm=None,
            payment_terms="30% cọc, 70% khi hàng đến cảng",
            deposit_percent=deposit_percent,
            expected_delivery_date=None,
        )
        self.store.prices[case.id.value] = {
            line.sku_id: Decimal(price) for line, (_, price) in zip(case.lines, lines, strict=True)
        }
        return case

    def add_master_account(
        self, number: str = MASTER_ACCOUNT, *, workspace: uuid.UUID | None = None
    ) -> None:
        version = 1 + len(self.accounts.rows)
        self.accounts.rows.append(
            (
                self.tenant_id,
                workspace or self.workspace_id,
                "Công ty Gia dụng Minh Phát",
                SupplierBankAccount(
                    id=uuid.uuid4(),
                    supplier_id=uuid.uuid4(),
                    version=version,
                    bank_name="Vietcombank",
                    account_number=number,
                    account_holder="CONG TY GIA DUNG MINH PHAT",
                    created_by=uuid.uuid4(),
                    created_at=NOW - timedelta(days=30),
                ),
            )
        )

    def add_document(
        self,
        case: POCase,
        doc_type: DocumentType,
        reading: Mapping[str, Any] | None = None,
        *,
        accounts: Sequence[str] = (),
        status: ExtractionStatus | None = ExtractionStatus.EXTRACTED,
        workspace: uuid.UUID | None = None,
        uploaded_at: datetime | None = None,
    ) -> CaseDocument:
        """A document on the case and the extraction lane's reading of it
        (None `status`: not read yet). `reading` is `{field: value}` (each
        quoted as itself) or, for lines, `{"lines": [{...}]}`."""
        data = f"{doc_type.value}:{uuid.uuid4()}".encode()
        scope = (case.tenant_id.value, workspace or case.workspace_id.value)
        version = 1 + sum(
            1 for d in self.documents.rows if d.case_id == case.id.value and d.doc_type is doc_type
        )
        document = CaseDocument(
            id=CaseDocumentId(uuid.uuid4()),
            tenant_id=scope[0],
            workspace_id=scope[1],
            case_kind=CaseKind.PO,
            case_id=case.id.value,
            doc_type=doc_type,
            object_key=f"k/{uuid.uuid4()}",
            filename=f"{doc_type.value}.pdf",
            content_type="application/pdf",
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            version=version,
            uploaded_by=uuid.uuid4(),
            uploaded_at=uploaded_at or NOW - timedelta(hours=1),
        )
        self.documents.rows.append(document)
        spec = EXTRACTION_SPECS.get(doc_type)
        if spec is None or status is None:
            return document
        fields = cited(reading or {}) if status is ExtractionStatus.EXTRACTED else {}
        if status is ExtractionStatus.EXTRACTED and spec.reads_accounts:
            fields[ACCOUNTS_FIELD] = [{"digest": account_digest(a)} for a in accounts]
        self.readings.rows.append(
            (
                scope[0],
                scope[1],
                StoredReading(
                    id=uuid.uuid4(),
                    document_id=document.id.value,
                    sha256=document.sha256,
                    prompt_id=spec.prompt_id,
                    prompt_version=spec.prompt_version,
                    status=status,
                    fields=fields,
                    gaps=[],
                ),
            )
        )
        return document

    def add_payment(self, case: POCase, kind: PaymentKind, amount: str) -> None:
        held = self.store.payments.setdefault(case.id.value, [])
        held.append(
            POPayment(
                id=uuid.uuid4(),
                po_case_id=case.id.value,
                kind=kind,
                version=1 + sum(1 for p in held if p.kind is kind),
                amount=Decimal(amount),
                currency="USD",
                due_date=None,
                paid_on=NOW.date(),
                document_id=None,
                recorded_by=uuid.uuid4(),
                recorded_at=NOW,
            )
        )

    def case(self, case: POCase) -> POCase:
        return replace(self.store.cases[case.id.value])


def cited(reading: Mapping[str, Any]) -> dict[str, Any]:
    """A grounded reading as the extraction lane stores it: each value with
    a quote that writes it."""
    out: dict[str, Any] = {}
    for name, value in reading.items():
        if name == "lines" and isinstance(value, list):
            out[name] = [
                {k: {"value": str(v), "quote": f"{k}: {v}"} for k, v in row.items()}
                for row in value
            ]
        elif value is not None:
            out[name] = {"value": str(value), "quote": f"{name}: {value}"}
    return out


__all__ = [
    "MASTER_ACCOUNT",
    "OTHER_ACCOUNT",
    "PO_STEP_SCOPES",
    "InMemoryAccounts",
    "InMemoryPOStepOutcomes",
    "Overrides",
    "POStepWorld",
    "cited",
]
