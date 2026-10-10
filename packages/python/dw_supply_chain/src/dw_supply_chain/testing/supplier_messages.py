"""An in-memory world for messages to a supplier (ticket ai-automation/07): its
unit tests and the `supply_chain.supplier_message` eval grader run the REAL
`DraftSupplierMessage`, `DraftSupplierMessages` and `MarkSupplierMessageSent`
over it, with the shipped prompt, skills and templates.

Every store keeps its port's promise: a reader sees only its own tenant AND
workspace (RLS), a trigger is drafted once per workspace and a message sent
once (the UNIQUEs). `leaky=True` on the cases stands in for an adapter that
forgot the workspace, so the drafter's own check can be exercised.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any

from dw_kernel.errors import ConflictError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.pagination import CursorPosition, Page, PageRequest, build_page
from dw_kernel.ports import FixedClock, Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.application.ports import FollowUpRecord, POCaseListFilter
from dw_supply_chain.application.supplier_messages import (
    DraftSupplierMessage,
    DraftSupplierMessages,
    NewMessageSend,
    NewSupplierMessage,
    SupplierMessage,
)
from dw_supply_chain.domain.case_document import CaseKind
from dw_supply_chain.domain.commercial import SupplierContact
from dw_supply_chain.domain.follow_up import FollowUpKind, FollowUpStatus
from dw_supply_chain.domain.po_case import POCase, POCaseId
from dw_supply_chain.domain.product_development_case import (
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
)
from dw_supply_chain.domain.supplier_message import MessagePurpose
from dw_supply_chain.step_preparation_policy import SupplyChainStepPreparation
from dw_supply_chain.supplier_message_policy import load_supply_chain_supplier_messages
from dw_supply_chain.testing.po_steps import InMemoryLineReceipts
from dw_supply_chain.testing.step_preparation import (
    NOW,
    REPO_ROOT,
    InMemoryDocuments,
    InMemoryDrafts,
    InMemoryProfiles,
    InMemoryReadings,
    InMemoryStepCases,
)

TEMPLATES = load_supply_chain_supplier_messages(
    REPO_ROOT / "configs" / "policies" / "supply_chain_supplier_messages@1.3.0.yaml"
)
PLATFORM_POLICY = SupplyChainStepPreparation.model_validate(
    {
        "schema_version": "1.0",
        "policy_id": "supply_chain_step_preparation",
        "policy_version": "1.0.0",
    }
)


def _sees(context: AccessContext, tenant: uuid.UUID, workspace: uuid.UUID) -> bool:
    return (tenant, workspace) == (context.tenant_id, context.workspace_id)


@dataclass
class LeakyCases(InMemoryStepCases):
    """Product cases; `leaky` answers whoever asks, as an adapter that forgot
    RLS would."""

    leaky: bool = False

    async def get(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> ProductDevelopmentCase | None:
        if self.leaky:
            case = self.cases.get(case_id.value)
            return None if case is None else replace(case, _pending_steps=[])
        return await super().get(context, case_id)


@dataclass
class InMemoryPOCases:
    cases: dict[uuid.UUID, POCase] = field(default_factory=dict)
    # An adapter that forgot RLS: the lane's and the drafter's own checks are
    # what an eval case grades then.
    leaky: bool = False

    def _visible(self, context: AccessContext, case: POCase) -> bool:
        return self.leaky or _sees(context, case.tenant_id.value, case.workspace_id.value)

    async def get(self, context: AccessContext, case_id: POCaseId) -> POCase | None:
        case = self.cases.get(case_id.value)
        if case is None or not self._visible(context, case):
            return None
        return case

    async def case_workspace(self, context: AccessContext, case_id: uuid.UUID) -> uuid.UUID | None:
        case = await self.get(context, POCaseId(case_id))
        return None if case is None else case.workspace_id.value

    async def list_page(
        self, context: AccessContext, request: PageRequest, case_filter: POCaseListFilter
    ) -> Page[POCase]:
        found = sorted(
            (
                c
                for c in self.cases.values()
                if self._visible(context, c)
                and (case_filter.state is None or c.state is case_filter.state)
            ),
            key=lambda c: c.id.value,
        )
        return build_page(
            found[: request.fetch_limit],
            request=request,
            position_of=lambda c: CursorPosition(sort_value=NOW, tiebreaker=c.id.value),
        )


@dataclass
class InMemoryContacts:
    by_name: dict[tuple[uuid.UUID, uuid.UUID, str], SupplierContact] = field(default_factory=dict)

    async def contact_for(
        self, context: AccessContext, supplier_name: str
    ) -> SupplierContact | None:
        return self.by_name.get(
            (context.tenant_id, context.workspace_id, supplier_name.strip().casefold())
        )


@dataclass
class InMemoryMessages:
    rows: list[SupplierMessage] = field(default_factory=list)
    audits: list[AuditEvent] = field(default_factory=list)

    async def has_source(self, context: AccessContext, source_key: str) -> bool:
        return any(
            _sees(context, m.tenant_id, m.workspace_id) and m.source_key == source_key
            for m in self.rows
        )

    async def add(
        self, context: AccessContext, message: NewSupplierMessage, *, audit: AuditEvent
    ) -> None:
        if await self.has_source(context, message.source_key):
            raise ConflictError("this trigger already has a message")
        self.rows.append(
            SupplierMessage(
                id=message.id,
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                case_kind=message.case_kind,
                case_id=message.case_id,
                purpose=message.purpose,
                status=message.status,
                source_key=message.source_key,
                supplier_name=message.supplier_name,
                recipient_name=message.recipient_name,
                recipient_email=message.recipient_email,
                subject=message.subject,
                body=message.body,
                attachments=message.attachments,
                citations=message.citations,
                dropped=message.dropped,
                template_version=message.template_version,
                prompt_id=message.prompt_id,
                prompt_version=message.prompt_version,
                model_profile=message.model_profile,
                content_sha256=message.content_sha256,
                created_by=context.principal_id,
                created_at=NOW,
            )
        )
        self.audits.append(audit)

    async def get(self, context: AccessContext, message_id: uuid.UUID) -> SupplierMessage | None:
        return next(
            (
                m
                for m in self.rows
                if m.id == message_id and _sees(context, m.tenant_id, m.workspace_id)
            ),
            None,
        )

    async def list_for_case(
        self, context: AccessContext, case_kind: CaseKind, case_id: uuid.UUID
    ) -> list[SupplierMessage]:
        return [
            m
            for m in reversed(self.rows)
            if _sees(context, m.tenant_id, m.workspace_id)
            and (m.case_kind, m.case_id) == (case_kind, case_id)
        ]

    async def mark_sent(
        self, context: AccessContext, send: NewMessageSend, *, audit: AuditEvent
    ) -> None:
        for index, m in enumerate(self.rows):
            if m.id == send.message_id and _sees(context, m.tenant_id, m.workspace_id):
                if m.sent_by is not None:
                    raise ConflictError("already sent")
                self.rows[index] = replace(m, sent_by=context.principal_id, sent_at=NOW)
                self.audits.append(audit)
                return
        raise ConflictError("no such message")


@dataclass
class InMemoryFollowUps:
    rows: list[tuple[uuid.UUID, FollowUpRecord]] = field(default_factory=list)

    async def list_open(self, context: AccessContext) -> list[FollowUpRecord]:
        return [
            r
            for tenant, r in self.rows
            if tenant == context.tenant_id
            and r.workspace_id == context.workspace_id
            and r.status is FollowUpStatus.OPEN
        ]


@dataclass
class Overrides:
    by_tenant: dict[uuid.UUID, dict[str, dict[str, Any]]] = field(default_factory=dict)

    async def get(self, context: AccessContext, policy_id: str) -> dict[str, Any] | None:
        return self.by_tenant.get(context.tenant_id, {}).get(policy_id)

    async def put(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("not exercised by supplier messages")


@dataclass
class RecordingNotifier:
    sent: list[dict[str, Any]] = field(default_factory=list)

    async def deliver(self, context: AccessContext, **kwargs: Any) -> None:
        self.sent.append({"tenant": context.tenant_id, **kwargs})


@dataclass
class StaticPlans:
    plans: dict[uuid.UUID, str] = field(default_factory=dict)

    async def plan_of(self, tenant_id: uuid.UUID) -> str | None:
        return self.plans.get(tenant_id)


@dataclass
class Workspaces:
    pairs: list[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=list)

    async def workspaces(self) -> list[tuple[uuid.UUID, uuid.UUID]]:
        return self.pairs


def elmich_policy(*purposes: MessagePurpose) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "policy_id": "supply_chain_step_preparation",
        "policy_version": "1.1.0",
        "supplier_messages": [p.value for p in purposes],
    }


@dataclass
class MessageWorld:
    gateway: Any
    tenant_id: uuid.UUID = field(default_factory=uuid.uuid4)
    workspace_id: uuid.UUID = field(default_factory=uuid.uuid4)
    cases: LeakyCases = field(default_factory=LeakyCases)
    po_cases: InMemoryPOCases = field(default_factory=InMemoryPOCases)
    contacts: InMemoryContacts = field(default_factory=InMemoryContacts)
    messages: InMemoryMessages = field(default_factory=InMemoryMessages)
    follow_ups: InMemoryFollowUps = field(default_factory=InMemoryFollowUps)
    overrides: Overrides = field(default_factory=Overrides)
    notifier: RecordingNotifier = field(default_factory=RecordingNotifier)
    plans: StaticPlans = field(default_factory=StaticPlans)
    clock: FixedClock = field(default_factory=lambda: FixedClock(NOW))
    model_profile: str | None = None
    documents: InMemoryDocuments = field(default_factory=InMemoryDocuments)
    drafts: InMemoryDrafts = field(default_factory=InMemoryDrafts)
    profiles: InMemoryProfiles = field(default_factory=InMemoryProfiles)
    readings: InMemoryReadings = field(default_factory=InMemoryReadings)
    receipts: InMemoryLineReceipts = field(default_factory=InMemoryLineReceipts)

    def __post_init__(self) -> None:
        self.plans.plans[self.tenant_id] = "professional"

    def context(
        self,
        principal: uuid.UUID | None = None,
        *,
        scopes: frozenset[str] = frozenset(),
        workspace: uuid.UUID | None = None,
    ) -> AccessContext:
        return AccessContext(
            tenant_id=self.tenant_id,
            workspace_id=workspace or self.workspace_id,
            principal_id=principal or uuid.uuid4(),
            roles=frozenset(),
            scopes=scopes,
            plan_id="professional",
        )

    def drafter(self) -> DraftSupplierMessage:
        return DraftSupplierMessage(
            product_cases=self.cases,
            po_cases=self.po_cases,
            contacts=self.contacts,
            messages=self.messages,
            plans=self.plans,
            gateway=self.gateway,
            policy_override_repo=self.overrides,
            platform_default_templates=TEMPLATES,
            notifier=self.notifier,
            ids=Uuid4Generator(),
            clock=self.clock,
            model_profile=self.model_profile,
        )

    def lane(self) -> DraftSupplierMessages:
        return DraftSupplierMessages(
            workspaces=Workspaces([(self.tenant_id, self.workspace_id)]),
            cases=self.cases,
            follow_ups=self.follow_ups,
            drafter=self.drafter(),
            policy_override_repo=self.overrides,
            platform_default_policy=PLATFORM_POLICY,
            documents=self.documents,
            drafts=self.drafts,
            profiles=self.profiles,
            po_listing=self.po_cases,
            readings=self.readings,
            receipts=self.receipts,
        )

    def enable(self, *purposes: MessagePurpose) -> None:
        self.overrides.by_tenant.setdefault(self.tenant_id, {})["supply_chain_step_preparation"] = (
            elmich_policy(*purposes)
        )

    # -- seeding -----------------------------------------------------------

    def add_case(
        self,
        state: ProductDevState,
        *,
        tenant: uuid.UUID | None = None,
        workspace: uuid.UUID | None = None,
        product_name: str = "Nồi inox 3 đáy 24cm",
        supplier_name: str | None = "Công ty Gia dụng Minh Phát",
    ) -> ProductDevelopmentCase:
        entered = NOW - timedelta(days=1)
        case = ProductDevelopmentCase(
            id=ProductDevelopmentCaseId(uuid.uuid4()),
            tenant_id=TenantId(tenant or self.tenant_id),
            workspace_id=WorkspaceId(workspace or self.workspace_id),
            proposal_code="DX-2026-041",
            product_name=product_name,
            category="kitchen",
            pic_user_id=uuid.uuid4(),
            created_by=uuid.uuid4(),
            supplier_name=supplier_name,
            state=state,
            sample_round=1,
            round_opened_at=entered,
            stage_entered_at=entered,
            version=3,
            created_at=entered - timedelta(days=10),
        )
        self.cases.cases[case.id.value] = case
        self.cases.enter(case, entered)
        return case

    def add_contact(self, supplier_name: str, name: str, email: str | None) -> None:
        self.contacts.by_name[(self.tenant_id, self.workspace_id, supplier_name.casefold())] = (
            SupplierContact(
                id=uuid.uuid4(),
                supplier_id=uuid.uuid4(),
                version=1,
                name=name,
                email=email,
                phone=None,
                created_by=uuid.uuid4(),
                created_at=NOW,
            )
        )

    def add_follow_up(
        self,
        case: ProductDevelopmentCase,
        *,
        kind: FollowUpKind = FollowUpKind.SLA_BREACH,
        milestone: str | None = "sample_collection",
        days: int = 12,
        limit_days: int | None = 10,
    ) -> FollowUpRecord:
        record = FollowUpRecord(
            id=uuid.uuid4(),
            case_kind=CaseKind.PRODUCT,
            case_id=case.id.value,
            workspace_id=case.workspace_id.value,
            reference=case.proposal_code,
            supplier_name=case.supplier_name,
            kind=kind,
            episode=f"{milestone}@{NOW.isoformat()}",
            milestone=milestone,
            days=days,
            limit_days=limit_days,
            recipient_scopes=frozenset(),
            recipient_user_id=case.pic_user_id,
            status=FollowUpStatus.OPEN,
            opened_at=NOW,
            notified_at=None,
            closed_at=None,
            closed_by=None,
            close_note=None,
        )
        self.follow_ups.rows.append((case.tenant_id.value, record))
        return record


__all__ = [
    "TEMPLATES",
    "InMemoryMessages",
    "MessageWorld",
    "elmich_policy",
]
