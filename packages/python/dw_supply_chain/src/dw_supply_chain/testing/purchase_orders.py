"""An in-memory world for step 10's PO draft (ticket ai-automation/14): its
unit tests and the `supply_chain.purchase_order` eval grader run the REAL
`PreparePurchaseOrders`, `GetPurchaseOrderProposal` and `ApprovePurchaseOrder`
over it, with the shipped templates and the real DOCX renderer.

Every store keeps its port's promise: a reader sees only its own tenant AND
workspace (RLS), a PO case is saved only over the version it was read at, a
PO number is the tenant's once, a draft version is decided once, and
`apply` is all or nothing.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from dw_agent_runtime.adapters.docx_templates import DocxRenderer
from dw_kernel.errors import ConflictError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.pagination import CursorPosition, Page, PageRequest, build_page
from dw_kernel.ports import FixedClock, Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.action_duties import load_supply_chain_action_duties
from dw_supply_chain.application.document_drafts import (
    NewDocumentDraft,
    NewDraftDecision,
    PrepareDocumentDraft,
)
from dw_supply_chain.application.handlers import COMMERCIAL_READ, COMMERCIAL_WRITE, PO_CASE_READ
from dw_supply_chain.application.ports import NewCaseDocument, POCaseListFilter
from dw_supply_chain.application.purchase_orders import (
    ApprovePurchaseOrder,
    GetPurchaseOrderProposal,
    PreparePurchaseOrders,
    PurchaseOrderSources,
    po_lane_context,
)
from dw_supply_chain.application.step_preparation import StoredReading
from dw_supply_chain.domain.case_document import (
    CaseDocument,
    CaseDocumentId,
    CaseKind,
    DocumentType,
)
from dw_supply_chain.domain.commercial import (
    CommercialTerms,
    Incoterm,
    POCommercial,
    POPayment,
    PricedLine,
    ProductProfile,
    ProfileCommercial,
)
from dw_supply_chain.domain.extraction import EXTRACTION_SPECS, ExtractionStatus
from dw_supply_chain.domain.po_case import POCase, POCaseId, POCaseLine
from dw_supply_chain.step_preparation_policy import load_supply_chain_step_preparation
from dw_supply_chain.testing.step_preparation import (
    REPO_ROOT,
    InMemoryDocuments,
    InMemoryDrafts,
    InMemoryProfiles,
    InMemoryReadings,
    InMemoryStorage,
    PlatformTemplates,
)

NOW = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
ORDERING = "supply_chain.duty.ordering"
FINANCE = "supply_chain.duty.finance"
ELMICH_PREPARATION = load_supply_chain_step_preparation(
    REPO_ROOT / "scripts" / "elmich_step_preparation_override.yaml"
)
PLATFORM_DUTIES = load_supply_chain_action_duties(
    REPO_ROOT / "configs" / "policies" / "supply_chain_action_duties@1.3.0.yaml"
)


def _sees(context: AccessContext, case: POCase) -> bool:
    return (case.tenant_id.value, case.workspace_id.value) == (
        context.tenant_id,
        context.workspace_id,
    )


@dataclass
class InMemoryPOStore:
    """`POCaseStorePort`, the PO kind of `CaseLookups`, and the commercial
    reads, under RLS."""

    cases: dict[uuid.UUID, POCase] = field(default_factory=dict)
    terms: dict[uuid.UUID, CommercialTerms] = field(default_factory=dict)
    prices: dict[uuid.UUID, dict[uuid.UUID, Decimal]] = field(default_factory=dict)
    # Recorded deposits and final payments (ticket ai-automation/15).
    payments: dict[uuid.UUID, list[POPayment]] = field(default_factory=dict)

    async def get(self, context: AccessContext, case_id: POCaseId) -> POCase | None:
        case = self.cases.get(case_id.value)
        if case is None or not _sees(context, case):
            return None
        return replace(case, _pending_transitions=[], _pending_line_quantities={})

    async def case_workspace(self, context: AccessContext, case_id: uuid.UUID) -> uuid.UUID | None:
        case = self.cases.get(case_id)
        if case is None or case.tenant_id.value != context.tenant_id:
            return None
        return case.workspace_id.value

    async def list_page(
        self, context: AccessContext, request: PageRequest, case_filter: POCaseListFilter
    ) -> Page[POCase]:
        found = [
            replace(c, lines=())
            for c in self.cases.values()
            if _sees(context, c) and (case_filter.state is None or c.state is case_filter.state)
        ]
        found.sort(key=lambda c: c.id.value)
        return build_page(
            found[: request.fetch_limit],
            request=request,
            position_of=lambda c: CursorPosition(sort_value=NOW, tiebreaker=c.id.value),
        )

    async def read(self, context: AccessContext, case_id: uuid.UUID) -> POCommercial:
        case = self.cases.get(case_id)
        if case is None or not _sees(context, case):
            return POCommercial(terms=CommercialTerms())
        prices = self.prices.get(case_id, {})
        return POCommercial(
            terms=self.terms.get(case_id, CommercialTerms()),
            lines=tuple(
                PricedLine(
                    sku_id=line.sku_id,
                    quantity=line.quantity,
                    unit_price=prices.get(line.sku_id),
                    sku_code=line.sku_code,
                    variant_label=line.variant_label,
                )
                for line in case.lines
            ),
            payments=tuple(self.payments.get(case_id, [])),
        )


@dataclass
class RecordingNotifier:
    sent: list[dict[str, Any]] = field(default_factory=list)

    async def deliver(self, context: AccessContext, **kwargs: Any) -> None:
        self.sent.append({"workspace": context.workspace_id, **kwargs})


@dataclass
class StaticHolders:
    by_scope: dict[str, list[uuid.UUID]] = field(default_factory=dict)

    async def holding(
        self, tenant_id: uuid.UUID, workspace_id: uuid.UUID, scopes: frozenset[str]
    ) -> list[uuid.UUID]:
        return [u for s in scopes for u in self.by_scope.get(s, [])]


@dataclass
class InMemoryPOOutcomes:
    """`PurchaseOrderOutcomesPort`: all or nothing."""

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
        new_draft: NewDocumentDraft,
        confirmation: NewDraftDecision,
        document: NewCaseDocument,
        terms: CommercialTerms,
        line_prices: Mapping[uuid.UUID, Decimal],
        audits: Sequence[AuditEvent],
    ) -> None:
        stored = self.store.cases[case.id.value]
        if stored.version != case.version - 1:
            raise ConflictError("PO case was modified concurrently")
        taken = {
            c.po_reference
            for c in self.store.cases.values()
            if c.tenant_id == case.tenant_id and c.id != case.id
        }
        if case.po_reference in taken:
            raise ConflictError("số PO đã có trong công ty")
        if any(
            d.lineage_id == new_draft.lineage_id and d.version == new_draft.version
            for d in self.drafts.rows
        ):
            raise ConflictError("this draft version already exists")
        self.drafts.insert(context, new_draft)
        self.drafts.record_decision(context, confirmation)
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
                version=1,
                uploaded_by=context.principal_id,
                uploaded_at=NOW,
            )
        )
        case.pop_pending_transitions()
        quantities = case.pop_pending_line_quantities()
        self.store.cases[case.id.value] = replace(
            case,
            lines=tuple(
                replace(line, quantity=quantities.get(line.sku_id, line.quantity))
                for line in case.lines
            ),
        )
        self.store.terms[case.id.value] = terms
        self.store.prices[case.id.value] = dict(line_prices)
        self.audits.extend(audits)
        self.applied += 1


@dataclass
class POWorld:
    tenant_id: uuid.UUID = field(default_factory=uuid.uuid4)
    workspace_id: uuid.UUID = field(default_factory=uuid.uuid4)
    store: InMemoryPOStore = field(default_factory=InMemoryPOStore)
    profiles: InMemoryProfiles = field(default_factory=InMemoryProfiles)
    documents: InMemoryDocuments = field(default_factory=InMemoryDocuments)
    readings: InMemoryReadings = field(default_factory=InMemoryReadings)
    drafts: InMemoryDrafts = field(default_factory=InMemoryDrafts)
    storage: InMemoryStorage = field(default_factory=InMemoryStorage)
    templates: PlatformTemplates = field(default_factory=PlatformTemplates)
    notifier: RecordingNotifier = field(default_factory=RecordingNotifier)
    clock: FixedClock = field(default_factory=lambda: FixedClock(NOW))
    approver: uuid.UUID = field(default_factory=uuid.uuid4)
    accountant: uuid.UUID = field(default_factory=uuid.uuid4)
    outcomes: InMemoryPOOutcomes = field(init=False)

    def __post_init__(self) -> None:
        self.outcomes = InMemoryPOOutcomes(self.store, self.drafts, self.documents)

    def context(
        self,
        scopes: frozenset[str] = frozenset(
            {ORDERING, COMMERCIAL_READ, COMMERCIAL_WRITE, PO_CASE_READ}
        ),
        *,
        tenant: uuid.UUID | None = None,
        workspace: uuid.UUID | None = None,
    ) -> AccessContext:
        return AccessContext(
            tenant_id=tenant or self.tenant_id,
            workspace_id=workspace or self.workspace_id,
            principal_id=self.approver,
            roles=frozenset(),
            scopes=scopes,
            plan_id="professional",
        )

    def lane_context(self) -> AccessContext:
        return po_lane_context(self.tenant_id, self.workspace_id)

    def sources(self) -> PurchaseOrderSources:
        return PurchaseOrderSources(
            profiles=self.profiles, documents=self.documents, readings=self.readings
        )

    def lane(self) -> PreparePurchaseOrders:
        ids = Uuid4Generator()
        return PreparePurchaseOrders(
            workspaces=_Workspaces([(self.tenant_id, self.workspace_id)]),
            cases=self.store,
            commercial=self.store,
            sources=self.sources(),
            drafts=self.drafts,
            prepare_draft=PrepareDocumentDraft(
                cases={CaseKind.PO: self.store},
                drafts=self.drafts,
                templates=self.templates,
                ids=ids,
                clock=self.clock,
            ),
            policy_override_repo=PolicyOverrides(ELMICH_PREPARATION.model_dump(mode="json")),
            platform_default_policy=ELMICH_PREPARATION,
            holders=StaticHolders(
                {ORDERING: [self.approver], "supply_chain.duty.finance": [self.accountant]}
            ),
            notifier=self.notifier,
            clock=self.clock,
        )

    def page(self) -> GetPurchaseOrderProposal:
        return GetPurchaseOrderProposal(
            cases=self.store,
            commercial=self.store,
            sources=self.sources(),
            drafts=self.drafts,
            policy_override_repo=PolicyOverrides(None),
            platform_default_duties=PLATFORM_DUTIES,
            authz=ScopeAuthorizationService(),
            clock=self.clock,
        )

    def approver_handler(self) -> ApprovePurchaseOrder:
        return ApprovePurchaseOrder(
            cases=self.store,
            drafts=self.drafts,
            templates=self.templates,
            renderer=DocxRenderer(),
            storage=self.storage,
            outcomes=self.outcomes,
            policy_override_repo=PolicyOverrides(None),
            platform_default_duties=PLATFORM_DUTIES,
            holders=StaticHolders({FINANCE: [self.accountant]}),
            notifier=self.notifier,
            authz=ScopeAuthorizationService(),
            ids=Uuid4Generator(),
            clock=self.clock,
        )

    # -- seeding -------------------------------------------------------------

    def add_case(
        self,
        quantities: Sequence[int | None] = (500, 1200),
        *,
        tenant: uuid.UUID | None = None,
        workspace: uuid.UUID | None = None,
    ) -> POCase:
        product = uuid.uuid4()
        case = POCase.requested(
            id=POCaseId(uuid.uuid4()),
            tenant_id=TenantId(tenant or self.tenant_id),
            workspace_id=WorkspaceId(workspace or self.workspace_id),
            supplier_name="Công ty Gia dụng Minh Phát",
            product_dev_case_id=product,
            pic_user_id=uuid.uuid4(),
            category="noi",
            lines=tuple(
                POCaseLine(
                    sku_id=uuid.uuid4(),
                    quantity=q,
                    sku_code=f"EL-00001-{i:02d}",
                    variant_label=label,
                )
                for i, (q, label) in enumerate(
                    zip(
                        quantities,
                        ("Đỏ 24cm", "Xanh 24cm", "Kem 24cm")[: len(quantities)],
                        strict=True,
                    ),
                    start=1,
                )
            ),
        )
        self.store.cases[case.id.value] = case
        return case

    def add_profile(
        self,
        case: POCase,
        *,
        unit_price: str | None = "2.50",
        currency: str | None = "USD",
        incoterm: Incoterm | None = Incoterm.FOB,
        workspace: uuid.UUID | None = None,
    ) -> ProductProfile:
        assert case.product_dev_case_id is not None
        profile = ProductProfile(
            id=uuid.uuid4(),
            product_dev_case_id=case.product_dev_case_id,
            version=1,
            commercial=ProfileCommercial.of(
                unit_price=unit_price,
                currency=currency,
                moq=500,
                lead_time_days=45,
                incoterm=incoterm,
            ),
            attributes={"product_name": "Nồi inox 3 đáy 24cm"},
            schema_version="1.0.0",
            created_by=uuid.uuid4(),
            created_at=NOW - timedelta(days=3),
        )
        self.profiles.rows.append(profile)
        self.profiles.scopes[profile.id] = (
            case.tenant_id.value,
            workspace or case.workspace_id.value,
        )
        return profile

    def add_confirmation(
        self, case: POCase, fields: Mapping[str, Any], *, workspace: uuid.UUID | None = None
    ) -> None:
        """The supplier's confirmation on the product case, read at step 8."""
        assert case.product_dev_case_id is not None
        data = b"thu xac nhan"
        scope = (case.tenant_id.value, workspace or case.workspace_id.value)
        document = CaseDocument(
            id=CaseDocumentId(uuid.uuid4()),
            tenant_id=scope[0],
            workspace_id=scope[1],
            case_kind=CaseKind.PRODUCT,
            case_id=case.product_dev_case_id,
            doc_type=DocumentType.SUPPLIER_CONFIRMATION_EMAIL,
            object_key=f"k/{uuid.uuid4()}",
            filename="xac-nhan.eml",
            content_type="message/rfc822",
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            version=1,
            uploaded_by=uuid.uuid4(),
            uploaded_at=NOW - timedelta(days=1),
        )
        self.documents.rows.append(document)
        spec = EXTRACTION_SPECS[DocumentType.SUPPLIER_CONFIRMATION_EMAIL]
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
                    status=ExtractionStatus.EXTRACTED,
                    fields=dict(fields),
                    gaps=[],
                ),
            )
        )

    def set_terms(self, case: POCase, **terms: Any) -> None:
        self.store.terms[case.id.value] = CommercialTerms.of(
            currency=terms.get("currency"),
            incoterm=terms.get("incoterm"),
            payment_terms=terms.get("payment_terms"),
            deposit_percent=terms.get("deposit_percent"),
            expected_delivery_date=terms.get("expected_delivery_date"),
        )


@dataclass
class _Workspaces:
    pairs: list[tuple[uuid.UUID, uuid.UUID]]

    async def workspaces(self) -> list[tuple[uuid.UUID, uuid.UUID]]:
        return self.pairs


@dataclass
class PolicyOverrides:
    """The tenant's step preparation policy (or none)."""

    stored: dict[str, Any] | None

    async def get(self, context: AccessContext, policy_id: str) -> dict[str, Any] | None:
        return self.stored if policy_id == "supply_chain_step_preparation" else None

    async def put(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("not exercised by the PO draft")


__all__ = ["NOW", "POWorld", "PolicyOverrides", "RecordingNotifier", "StaticHolders"]
