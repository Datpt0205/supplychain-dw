"""An in-memory world for step 12's papers and MKT's steps (ticket
ai-automation/16): their unit tests and the `supply_chain.packaging_proof`
eval grader run the REAL `PreparePackagingPapers`, `GetPackagingProof` and
`TakePackagingStep` over it, with the shipped templates.

Every store keeps its port's promise: a reader sees only its own tenant AND
workspace (RLS); a design is saved over the version it was read at.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from dw_kernel.errors import ConflictError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.ports import FixedClock, Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.action_duties import CaseDuty
from dw_supply_chain.application.document_drafts import PrepareDocumentDraft
from dw_supply_chain.application.handlers import PO_CASE_READ, duty_scope
from dw_supply_chain.application.packaging_designs import GetPackagingDesign, TakePackagingStep
from dw_supply_chain.application.packaging_papers import (
    GetPackagingProof,
    PackagingSources,
    PreparePackagingPapers,
    packaging_lane_context,
)
from dw_supply_chain.application.step_preparation import StoredReading
from dw_supply_chain.domain.case_document import (
    CaseDocument,
    CaseDocumentId,
    CaseKind,
    DocumentType,
)
from dw_supply_chain.domain.commercial import ProductProfile, ProfileCommercial
from dw_supply_chain.domain.extraction import (
    BARCODES_FIELD,
    EXTRACTION_SPECS,
    ExtractionStatus,
)
from dw_supply_chain.domain.packaging_design import PackagingDesign, PackagingHistoryEntry
from dw_supply_chain.domain.po_case import CaseState, OrderKind, POCase, POCaseId, POCaseLine
from dw_supply_chain.packaging_policy import load_supply_chain_packaging_policy
from dw_supply_chain.testing.po_steps import Overrides
from dw_supply_chain.testing.purchase_orders import (
    ELMICH_PREPARATION,
    NOW,
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
    InMemoryProfiles,
    InMemoryReadings,
    PlatformTemplates,
)

ELMICH_PACKAGING = load_supply_chain_packaging_policy(
    REPO_ROOT / "scripts" / "elmich_packaging_override.yaml"
)
ORDERING = duty_scope(CaseDuty.ORDERING)
MKT = duty_scope(CaseDuty.MKT)
BM04 = {
    "product_name": "Nồi inox 3 đáy 24cm",
    "material": "Inox 304",
    "dimensions": "Đường kính 24 cm, cao 13 cm",
    "origin_country": "Trung Quốc",
    "colours": "Bạc",
}


@dataclass
class InMemoryDesigns:
    """`PackagingDesignRepositoryPort` under RLS, optimistic on the version."""

    rows: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID, PackagingDesign]] = field(
        default_factory=dict
    )
    events: list[PackagingHistoryEntry] = field(default_factory=list)
    audits: list[AuditEvent] = field(default_factory=list)

    async def get(self, context: AccessContext, po_case_id: uuid.UUID) -> PackagingDesign | None:
        held = self.rows.get(po_case_id)
        if held is None or held[:2] != (context.tenant_id, context.workspace_id):
            return None
        return replace(held[2], _pending=[])

    async def history(
        self, context: AccessContext, po_case_id: uuid.UUID
    ) -> list[PackagingHistoryEntry]:
        return list(self.events)

    async def save(
        self,
        context: AccessContext,
        design: PackagingDesign,
        *,
        actor_id: uuid.UUID,
        audit: AuditEvent,
    ) -> None:
        held = self.rows.get(design.po_case_id)
        if (held is None and design.version != 1) or (
            held is not None and held[2].version != design.version - 1
        ):
            raise ConflictError("design saved meanwhile")
        for event in design.pop_pending_events():
            self.events.append(
                PackagingHistoryEntry(
                    action=event.action,
                    reason=event.reason,
                    document_id=event.document_id,
                    note=event.note,
                    actor_id=actor_id,
                    occurred_at=NOW,
                )
            )
        self.rows[design.po_case_id] = (context.tenant_id, context.workspace_id, design)
        self.audits.append(audit)


@dataclass
class PackagingWorld:
    tenant_id: uuid.UUID = field(default_factory=uuid.uuid4)
    workspace_id: uuid.UUID = field(default_factory=uuid.uuid4)
    store: InMemoryPOStore = field(default_factory=InMemoryPOStore)
    designs: InMemoryDesigns = field(default_factory=InMemoryDesigns)
    profiles: InMemoryProfiles = field(default_factory=InMemoryProfiles)
    documents: InMemoryDocuments = field(default_factory=InMemoryDocuments)
    readings: InMemoryReadings = field(default_factory=InMemoryReadings)
    drafts: InMemoryDrafts = field(default_factory=InMemoryDrafts)
    templates: PlatformTemplates = field(default_factory=PlatformTemplates)
    notifier: RecordingNotifier = field(default_factory=RecordingNotifier)
    clock: FixedClock = field(default_factory=lambda: FixedClock(NOW))
    person: uuid.UUID = field(default_factory=uuid.uuid4)
    buyer: uuid.UUID = field(default_factory=uuid.uuid4)
    marketer: uuid.UUID = field(default_factory=uuid.uuid4)
    overrides: Overrides = field(init=False)

    def __post_init__(self) -> None:
        self.overrides = Overrides(
            {
                "supply_chain_step_preparation": ELMICH_PREPARATION.model_dump(mode="json"),
                "supply_chain_packaging": ELMICH_PACKAGING.model_dump(mode="json"),
            }
        )

    def context(
        self,
        scopes: frozenset[str] = frozenset({ORDERING, PO_CASE_READ}),
        *,
        workspace: uuid.UUID | None = None,
    ) -> AccessContext:
        return AccessContext(
            tenant_id=self.tenant_id,
            workspace_id=workspace or self.workspace_id,
            principal_id=self.person,
            roles=frozenset(),
            scopes=scopes,
            plan_id="professional",
        )

    def lane_context(self) -> AccessContext:
        return packaging_lane_context(self.tenant_id, self.workspace_id)

    def sources(self) -> PackagingSources:
        return PackagingSources(
            profiles=self.profiles, documents=self.documents, readings=self.readings
        )

    def holders(self) -> StaticHolders:
        return StaticHolders({ORDERING: [self.buyer], MKT: [self.marketer]})

    def lane(self) -> PreparePackagingPapers:
        return PreparePackagingPapers(
            workspaces=_Workspaces([(self.tenant_id, self.workspace_id)]),
            cases=self.store,
            designs=self.designs,
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
            platform_default_policy=ELMICH_PREPARATION,
            platform_default_packaging=ELMICH_PACKAGING,
            platform_default_duties=PLATFORM_DUTIES,
            holders=self.holders(),
            notifier=self.notifier,
            clock=self.clock,
        )

    def proof_page(self) -> GetPackagingProof:
        return GetPackagingProof(
            cases=self.store,
            sources=self.sources(),
            policy_override_repo=self.overrides,
            platform_default_policy=ELMICH_PREPARATION,
            authz=ScopeAuthorizationService(),
        )

    def take(self) -> TakePackagingStep:
        return TakePackagingStep(
            po_cases=self.store,  # type: ignore[arg-type]
            designs=self.designs,
            documents=self.documents,
            authz=ScopeAuthorizationService(),
            policy_override_repo=self.overrides,
            platform_default_duties=PLATFORM_DUTIES,
            platform_default_policy=ELMICH_PACKAGING,
            holders=self.holders(),
            notifier=self.notifier,
            ids=Uuid4Generator(),
            clock=self.clock,
        )

    def page(self) -> GetPackagingDesign:
        return GetPackagingDesign(
            po_cases=self.store,  # type: ignore[arg-type]
            designs=self.designs,
            authz=ScopeAuthorizationService(),
            policy_override_repo=self.overrides,
            platform_default_duties=PLATFORM_DUTIES,
            platform_default_policy=ELMICH_PACKAGING,
            documents=self.documents,
        )

    # -- seeding -------------------------------------------------------------

    def add_case(
        self, *, tenant: uuid.UUID | None = None, workspace: uuid.UUID | None = None
    ) -> POCase:
        case = POCase(
            id=POCaseId(uuid.uuid4()),
            tenant_id=TenantId(tenant or self.tenant_id),
            workspace_id=WorkspaceId(workspace or self.workspace_id),
            po_reference="PO-2026-0101",
            supplier_name="Công ty Gia dụng Minh Phát",
            state=CaseState.PRE_PRODUCTION,
            order_kind=OrderKind.NEW,
            product_dev_case_id=uuid.uuid4(),
            lines=(
                POCaseLine(sku_id=uuid.uuid4(), quantity=500, sku_code="EL-00001-01"),
                POCaseLine(sku_id=uuid.uuid4(), quantity=1200, sku_code="EL-00001-02"),
            ),
        )
        self.store.cases[case.id.value] = case
        return case

    def add_profile(
        self,
        case: POCase,
        attributes: Mapping[str, Any] = BM04,
        *,
        workspace: uuid.UUID | None = None,
    ) -> None:
        assert case.product_dev_case_id is not None
        profile = ProductProfile(
            id=uuid.uuid4(),
            product_dev_case_id=case.product_dev_case_id,
            version=1,
            commercial=ProfileCommercial(),
            attributes=dict(attributes),
            schema_version="1.0.0",
            created_by=uuid.uuid4(),
            created_at=NOW - timedelta(days=5),
        )
        self.profiles.rows.append(profile)
        self.profiles.scopes[profile.id] = (
            case.tenant_id.value,
            workspace or case.workspace_id.value,
        )

    def set_design(self, case: POCase, **state: Any) -> PackagingDesign:
        design = PackagingDesign(
            po_case_id=case.id.value,
            tenant_id=case.tenant_id,
            workspace_id=case.workspace_id,
            version=1,
            **state,
        )
        self.designs.rows[case.id.value] = (
            case.tenant_id.value,
            case.workspace_id.value,
            design,
        )
        return design

    def add_document(
        self,
        case: POCase,
        doc_type: DocumentType,
        reading: Mapping[str, Any] | None = None,
        *,
        status: ExtractionStatus | None = ExtractionStatus.EXTRACTED,
        workspace: uuid.UUID | None = None,
        uploaded_at: datetime | None = None,
    ) -> CaseDocument:
        """A document on the case and, for a type the lane reads, its reading
        (`reading` is `{field: value}`, `sku_codes` a list; None `status`: not
        read yet)."""
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
                    fields=proof_fields(reading or {})
                    if status is ExtractionStatus.EXTRACTED
                    else {},
                    gaps=[],
                ),
            )
        )
        return document

    def drafted(self, doc_type: DocumentType) -> list[Any]:
        return [d for d in self.drafts.rows if d.doc_type is doc_type]


def proof_fields(reading: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, value in reading.items():
        if name == "barcode":
            # Code's reading, as the extraction lane keeps it.
            out[BARCODES_FIELD] = [] if value is None else [str(value)]
        elif isinstance(value, list):
            out[name] = [{"value": str(v), "quote": str(v)} for v in value]
        elif value is not None:
            out[name] = {"value": str(value), "quote": str(value)}
    return out


FULL_PROOF: Mapping[str, Any] = {
    "product_name": "Nồi inox 3 đáy 24cm",
    "sku_codes": ["EL-00001-01", "EL-00001-02"],
    "barcode": "8935001800019",
    "dimensions": "Đường kính 24 cm, cao 13 cm",
    "material": "Inox 304",
    "origin": "Sản xuất tại Trung Quốc",
    "responsible_party": "Công ty Elmich, 123 Đường A, Hà Nội",
    "usage_instructions": "Rửa sạch trước khi dùng lần đầu",
    "warnings": "Không để nồi rỗng trên bếp đang bật",
}


def drafted_values(drafts: Sequence[Any]) -> list[dict[str, Any]]:
    return [{k: v.get("value") for k, v in d.fields.items() if isinstance(v, dict)} for d in drafts]


__all__ = [
    "BM04",
    "ELMICH_PACKAGING",
    "FULL_PROOF",
    "MKT",
    "ORDERING",
    "InMemoryDesigns",
    "PackagingWorld",
    "drafted_values",
    "proof_fields",
]
