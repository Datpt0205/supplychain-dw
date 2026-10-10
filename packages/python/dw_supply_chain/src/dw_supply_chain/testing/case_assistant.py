"""An in-memory world for the read-only case assistant (ticket
ai-automation/19): its unit tests and the `supply_chain.case_answer` eval
grader run the REAL `AskAboutCase` over it, with the shipped prompt rendered
through the real registry.

Every store keeps its port's promise: a reader sees only its own tenant AND
workspace (RLS), unless a case asks for an adapter that forgot it (`leaky`),
which is what the handler's own check is graded against.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.pagination import CursorPosition, Page, PageRequest, build_page
from dw_kernel.ports import Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_supply_chain.application.case_assistant import AnswerChannel, AskAboutCase, CitedAnswer
from dw_supply_chain.application.step_preparation import SamplePreparation, StoredReading
from dw_supply_chain.domain.case_document import (
    CaseDocument,
    CaseDocumentId,
    CaseKind,
    DocumentType,
)
from dw_supply_chain.domain.commercial import ProductProfile, ProfileCommercial
from dw_supply_chain.domain.extraction import EXTRACTION_SPECS, ExtractionStatus
from dw_supply_chain.domain.po_case import (
    CaseState,
    CaseTransition,
    OrderKind,
    POCase,
    POCaseId,
    POCaseLine,
    Shipping,
)
from dw_supply_chain.domain.product_development_case import (
    ProductAction,
    ProductCaseTransition,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
)
from dw_supply_chain.domain.sample_evaluation import Measurement
from dw_supply_chain.testing.purchase_orders import NOW, InMemoryPOStore
from dw_supply_chain.testing.step_preparation import (
    ELMICH_CRITERIA,
    InMemoryDocuments,
    InMemoryMeasurements,
    InMemoryProfiles,
    InMemoryReadings,
    _NoOverrides,
)
from dw_supply_chain.testing.supplier_messages import LeakyCases


@dataclass
class AssistantPOStore(InMemoryPOStore):
    """The PO store with the case's own timeline, under RLS."""

    transitions: dict[uuid.UUID, list[CaseTransition]] = field(default_factory=dict)

    async def list_transitions(
        self, context: AccessContext, case_id: POCaseId, request: PageRequest
    ) -> Page[CaseTransition]:
        visible = await self.get(context, case_id) is not None
        rows = sorted(
            self.transitions.get(case_id.value, []) if visible else [],
            key=lambda t: t.occurred_at,
            reverse=True,
        )
        return build_page(
            rows[: request.fetch_limit],
            request=request,
            position_of=lambda t: CursorPosition(sort_value=t.occurred_at, tiebreaker=uuid.uuid4()),
        )


@dataclass
class AssistantWorld:
    tenant_id: uuid.UUID = field(default_factory=uuid.uuid4)
    workspace_id: uuid.UUID = field(default_factory=uuid.uuid4)
    po: AssistantPOStore = field(default_factory=AssistantPOStore)
    products: LeakyCases = field(default_factory=LeakyCases)
    documents: InMemoryDocuments = field(default_factory=InMemoryDocuments)
    readings: InMemoryReadings = field(default_factory=InMemoryReadings)
    profiles: InMemoryProfiles = field(default_factory=InMemoryProfiles)
    measurements: InMemoryMeasurements = field(default_factory=InMemoryMeasurements)
    # A gateway whose `generate_structured` answers (a script or a live model).
    gateway: Any = None
    model_profile: str | None = None

    def context(
        self,
        scopes: frozenset[str],
        *,
        tenant: uuid.UUID | None = None,
        workspace: uuid.UUID | None = None,
    ) -> AccessContext:
        return AccessContext(
            tenant_id=tenant or self.tenant_id,
            workspace_id=workspace or self.workspace_id,
            principal_id=uuid.uuid4(),
            roles=frozenset(),
            scopes=scopes,
            plan_id="professional",
        )

    def ask(self) -> AskAboutCase:
        return AskAboutCase(
            po_cases=self.po,  # type: ignore[arg-type]
            product_cases=self.products,  # type: ignore[arg-type]
            documents=self.documents,
            readings=self.readings,
            profiles=self.profiles,
            sample=SamplePreparation(
                measurements=self.measurements,
                policy_override_repo=_NoOverrides(),
                platform_default_criteria=ELMICH_CRITERIA,
            ),
            gateway=self.gateway,
            authz=ScopeAuthorizationService(),
            ids=Uuid4Generator(),
            model_profile=self.model_profile,
        )

    async def answer(
        self,
        context: AccessContext,
        kind: CaseKind,
        case_id: uuid.UUID,
        question: str,
        channel: AnswerChannel = AnswerChannel.WEB,
    ) -> CitedAnswer:
        return await self.ask().handle(
            context, case_kind=kind, case_id=case_id, question=question, channel=channel
        )

    # -- seeding -------------------------------------------------------------

    def add_po_case(
        self,
        *,
        tenant: uuid.UUID | None = None,
        workspace: uuid.UUID | None = None,
        state: CaseState = CaseState.PRODUCTION,
    ) -> POCase:
        case = POCase(
            id=POCaseId(uuid.uuid4()),
            tenant_id=TenantId(tenant or self.tenant_id),
            workspace_id=WorkspaceId(workspace or self.workspace_id),
            po_reference="PO-2026-0101",
            supplier_name="Công ty Gia dụng Minh Phát",
            state=state,
            order_kind=OrderKind.NEW,
            lines=(
                POCaseLine(sku_id=uuid.uuid4(), quantity=500, sku_code="EL-00001-01"),
                POCaseLine(sku_id=uuid.uuid4(), quantity=1200, sku_code="EL-00001-02"),
            ),
            shipping=Shipping(etd=date(2026, 11, 20)),
        )
        self.po.cases[case.id.value] = case
        return case

    def po_history(
        self, case: POCase, before: CaseState | None, after: CaseState, reason: str | None
    ) -> None:
        self.po.transitions.setdefault(case.id.value, []).append(
            CaseTransition(
                from_state=before,
                to_state=after,
                reason=reason,
                occurred_at=NOW - timedelta(days=len(self.po.transitions.get(case.id.value, []))),
            )
        )

    def add_product(
        self,
        *,
        tenant: uuid.UUID | None = None,
        workspace: uuid.UUID | None = None,
        state: ProductDevState = ProductDevState.REVISION_REQUESTED,
        sample_round: int = 2,
    ) -> ProductDevelopmentCase:
        case = ProductDevelopmentCase(
            id=ProductDevelopmentCaseId(uuid.uuid4()),
            tenant_id=TenantId(tenant or self.tenant_id),
            workspace_id=WorkspaceId(workspace or self.workspace_id),
            proposal_code="DX-2026-041",
            product_name="Nồi inox 3 đáy 24cm",
            category="noi",
            pic_user_id=uuid.uuid4(),
            created_by=uuid.uuid4(),
            supplier_name="Công ty Gia dụng Minh Phát",
            state=state,
            sample_round=sample_round,
            created_at=NOW - timedelta(days=40),
        )
        self.products.cases[case.id.value] = case
        return case

    def product_history(
        self,
        case: ProductDevelopmentCase,
        action: ProductAction,
        before: ProductDevState | None,
        after: ProductDevState,
        reason: str | None,
    ) -> None:
        rows = self.products.transitions.setdefault(case.id.value, [])
        rows.append(
            ProductCaseTransition(
                action=action,
                from_state=before,
                to_state=after,
                reason=reason,
                actor_id=uuid.uuid4(),
                occurred_at=NOW - timedelta(days=10 - len(rows)),
                id=uuid.uuid4(),
            )
        )

    def add_profile(
        self,
        case: ProductDevelopmentCase,
        attributes: dict[str, Any],
        *,
        moq: int | None = None,
        unit_price: str | None = None,
        workspace: uuid.UUID | None = None,
    ) -> None:
        profile = ProductProfile(
            id=uuid.uuid4(),
            product_dev_case_id=case.id.value,
            version=1,
            commercial=ProfileCommercial(
                unit_price=None if unit_price is None else Decimal(unit_price),
                currency=None if unit_price is None else "USD",
                moq=moq,
            ),
            attributes=attributes,
            schema_version="1.0.0",
            created_by=uuid.uuid4(),
            created_at=NOW - timedelta(days=5),
        )
        self.profiles.rows.append(profile)
        self.profiles.scopes[profile.id] = (
            case.tenant_id.value,
            workspace or case.workspace_id.value,
        )

    def add_reading(
        self,
        kind: CaseKind,
        case_id: uuid.UUID,
        doc_type: DocumentType,
        fields: dict[str, Any],
        *,
        workspace: uuid.UUID | None = None,
    ) -> CaseDocument:
        data = f"{doc_type.value}:{uuid.uuid4()}".encode()
        scope = (self.tenant_id, workspace or self.workspace_id)
        document = CaseDocument(
            id=CaseDocumentId(uuid.uuid4()),
            tenant_id=scope[0],
            workspace_id=scope[1],
            case_kind=kind,
            case_id=case_id,
            doc_type=doc_type,
            object_key=f"k/{uuid.uuid4()}",
            filename=f"{doc_type.value}.pdf",
            content_type="application/pdf",
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            version=1,
            uploaded_by=uuid.uuid4(),
            uploaded_at=NOW - timedelta(hours=len(self.documents.rows) + 1),
        )
        self.documents.rows.append(document)
        spec = EXTRACTION_SPECS[doc_type]
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
                    fields={
                        k: (v if isinstance(v, list) else {"value": v, "quote": v})
                        for k, v in fields.items()
                    },
                    gaps=[],
                ),
            )
        )
        return document

    def measure(self, case: ProductDevelopmentCase, criterion: str, value: str) -> None:
        self.measurements.rows.append(
            (
                case.tenant_id.value,
                case.workspace_id.value,
                case.id.value,
                Measurement(
                    id=uuid.uuid4(),
                    sample_round=case.sample_round,
                    criterion=criterion,
                    value=value,
                    note=None,
                    entered_by=uuid.uuid4(),
                    entered_at=NOW - timedelta(days=3, minutes=len(self.measurements.rows)),
                ),
            )
        )


__all__ = ["AssistantPOStore", "AssistantWorld"]
