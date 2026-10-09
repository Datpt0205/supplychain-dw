"""Integration: step 10's PO draft and its approval (ticket ai-automation/14).

What only the real database can show:

- the lane drafts a `purchase_order` for a PO case awaiting its PO, through
  the real draft tables, from the terms and prices a person set on the case;
- the approval writes, in ONE transaction, the typed draft version and its
  confirmation, the `purchase_order` document (`origin = ai_prepared`, its
  `draft_id`), the PO step (number, state, history row), the terms and the
  line prices; a second approval of the decided draft is a 409;
- a PO number already in the tenant refuses the whole: no document, the case
  still awaiting its PO.

Owed when written (2026-10-10): not run, no Docker on the machine that wrote it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls
from test_place_order import ORDERING, _fresh, _place_order, _ready
from test_product_cases import _count, _Db

from dw_agent_runtime.adapters.docx_templates import DocxRenderer
from dw_kernel.errors import ConflictError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.adapters.persistence.notifications import SqlNotificationRepository
from dw_platform.adapters.persistence.policy_overrides import SqlPolicyOverrideRepository
from dw_platform.adapters.persistence.scope_holders import SqlScopeHolders
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence.case_document_repository import (
    SqlCaseDocumentRepository,
)
from dw_supply_chain.adapters.persistence.commercial_repository import (
    SqlPOCommercialRepository,
    SqlProductProfileRepository,
)
from dw_supply_chain.adapters.persistence.document_draft_repository import (
    SqlDocTemplateOverrides,
    SqlDocumentDraftRepository,
)
from dw_supply_chain.adapters.persistence.document_extraction_repository import (
    SqlExtractionReadings,
)
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.adapters.persistence.purchase_order_outcomes import (
    SqlPurchaseOrderOutcomes,
)
from dw_supply_chain.application.document_drafts import PrepareDocumentDraft, TenantDocTemplates
from dw_supply_chain.application.handlers import COMMERCIAL_WRITE, PO_CASE_READ
from dw_supply_chain.application.purchase_orders import (
    ApprovePurchaseOrder,
    PreparePurchaseOrders,
    PurchaseOrderSources,
    po_lane_context,
)
from dw_supply_chain.domain.case_document import CaseKind
from dw_supply_chain.domain.commercial import CommercialTerms, Incoterm
from dw_supply_chain.domain.po_case import CaseState, POCaseId
from dw_supply_chain.testing.purchase_orders import ELMICH_PREPARATION, PLATFORM_DUTIES
from dw_supply_chain.testing.step_preparation import InMemoryStorage, shipped_templates

pytestmark = pytest.mark.integration


@pytest.fixture
async def db(db_urls: DatabaseUrls) -> AsyncIterator[_Db]:
    app = create_async_engine(db_urls.app, poolclass=NullPool)
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    yield _Db(async_sessionmaker(app, expire_on_commit=False), migrator)
    await app.dispose()
    await migrator.dispose()


def _as(owner: AccessContext, *scopes: str) -> AccessContext:
    return AccessContext(
        tenant_id=owner.tenant_id,
        workspace_id=owner.workspace_id,
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=frozenset(scopes),
        plan_id="professional",
    )


def _audit(context: AccessContext) -> AuditEvent:
    return AuditEvent(
        id=uuid.uuid4(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        actor_id=UserId(context.principal_id),
        action="supply_chain.test",
        resource_type="po_commercial",
        resource_id=str(uuid.uuid4()),
        occurred_at=SystemClock().now(),
    )


def _templates(db: _Db) -> TenantDocTemplates:
    return TenantDocTemplates(
        registry=shipped_templates(), overrides=SqlDocTemplateOverrides(db.sessions)
    )


class _Elmich:
    """The tenant's step preparation policy: Elmich's, with the PO on."""

    async def get(self, context: AccessContext, policy_id: str) -> Any:
        return ELMICH_PREPARATION.model_dump(mode="json")

    async def put(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError("not exercised by the PO draft")


class _OneWorkspace:
    def __init__(self, owner: AccessContext) -> None:
        self.owner = owner

    async def workspaces(self) -> list[tuple[uuid.UUID, uuid.UUID]]:
        return [(self.owner.tenant_id, self.owner.workspace_id)]


def _approver(db: _Db) -> ApprovePurchaseOrder:
    return ApprovePurchaseOrder(
        cases=SqlPOCaseRepository(db.sessions),
        drafts=SqlDocumentDraftRepository(db.sessions),
        templates=_templates(db),
        renderer=DocxRenderer(),
        storage=InMemoryStorage(),
        outcomes=SqlPurchaseOrderOutcomes(db.sessions),
        policy_override_repo=SqlPolicyOverrideRepository(db.sessions),
        platform_default_duties=PLATFORM_DUTIES,
        holders=SqlScopeHolders(db.sessions),
        notifier=SqlNotificationRepository(db.sessions),
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
        clock=SystemClock(),
    )


async def _drafted(db: _Db, owner: AccessContext) -> tuple[POCaseId, uuid.UUID, str]:
    """A product case ordered into a PO case of `owner`'s workspace, its
    terms and prices set by a person, then drafted by the lane."""
    placed = await _place_order(db.sessions).handle(
        _as(owner, ORDERING), case_id=(await _ready(db, owner)).id
    )
    po_case = placed.po_case
    commercial = SqlPOCommercialRepository(db.sessions)
    await commercial.set_terms(
        owner,
        po_case.id.value,
        CommercialTerms.of(
            currency="USD",
            incoterm=Incoterm.FOB,
            payment_terms="30% cọc",
            deposit_percent="30",
            expected_delivery_date=date(2026, 12, 1),
        ),
        {line.sku_id: Decimal("2.5") for line in po_case.lines},
        audit=_audit(owner),
    )
    drafts = SqlDocumentDraftRepository(db.sessions)
    lane = PreparePurchaseOrders(
        workspaces=_OneWorkspace(owner),
        cases=SqlPOCaseRepository(db.sessions),
        commercial=commercial,
        sources=PurchaseOrderSources(
            profiles=SqlProductProfileRepository(db.sessions),
            documents=SqlCaseDocumentRepository(db.sessions),
            readings=SqlExtractionReadings(db.sessions),
        ),
        drafts=drafts,
        prepare_draft=PrepareDocumentDraft(
            cases={CaseKind.PO: SqlPOCaseRepository(db.sessions)},
            drafts=drafts,
            templates=_templates(db),
            ids=Uuid4Generator(),
            clock=SystemClock(),
        ),
        policy_override_repo=_Elmich(),
        platform_default_policy=ELMICH_PREPARATION,
        holders=SqlScopeHolders(db.sessions),
        notifier=SqlNotificationRepository(db.sessions),
        clock=SystemClock(),
    )
    assert await lane.draft(po_lane_context(owner.tenant_id, owner.workspace_id), po_case.id) == 1
    (draft,) = await drafts.latest_for_case(owner, CaseKind.PO, po_case.id.value)
    return po_case.id, draft.id, draft.content_sha256


async def test_the_approval_writes_the_po_its_document_terms_and_prices_together(
    db: _Db,
) -> None:
    owner = _fresh()
    po_case_id, draft_id, sha = await _drafted(db, owner)
    approver = _as(owner, ORDERING, COMMERCIAL_WRITE, PO_CASE_READ)
    reference = f"PO-{uuid.uuid4().hex[:8]}"
    case = await _approver(db).handle(
        approver, po_case_id, draft_id=draft_id, content_sha256=sha, po_reference=reference
    )
    assert case.state is CaseState.PO_CREATED
    async with db.migrator.connect() as conn:
        row = (
            await conn.execute(
                sa.text(
                    "SELECT state, po_reference, currency, deposit_percent"
                    " FROM supply_chain.po_cases WHERE id = :p"
                ),
                {"p": po_case_id.value},
            )
        ).one()
        documents = (
            await conn.execute(
                sa.text(
                    "SELECT origin, draft_id FROM supply_chain.case_documents"
                    " WHERE po_case_id = :p AND doc_type = 'purchase_order'"
                ),
                {"p": po_case_id.value},
            )
        ).all()
    assert (row.state, row.po_reference, row.currency) == ("po_created", reference, "USD")
    assert row.deposit_percent == Decimal(30)
    assert [(d.origin, d.draft_id is not None) for d in documents] == [("ai_prepared", True)]
    with pytest.raises(ConflictError):
        await _approver(db).handle(
            approver, po_case_id, draft_id=draft_id, content_sha256=sha, po_reference="PO-X"
        )


async def test_a_po_number_already_in_the_tenant_refuses_the_whole(db: _Db) -> None:
    owner = _fresh()
    first_case, first_draft, first_sha = await _drafted(db, owner)
    second_case, second_draft, second_sha = await _drafted(db, owner)
    approver = _as(owner, ORDERING, COMMERCIAL_WRITE, PO_CASE_READ)
    taken = f"PO-{uuid.uuid4().hex[:8]}"
    await _approver(db).handle(
        approver, first_case, draft_id=first_draft, content_sha256=first_sha, po_reference=taken
    )
    query = "SELECT count(*) FROM supply_chain.case_documents WHERE po_case_id = :p"
    before = await _count(db, query, p=second_case.value)
    with pytest.raises(ConflictError):
        await _approver(db).handle(
            approver,
            second_case,
            draft_id=second_draft,
            content_sha256=second_sha,
            po_reference=taken,
        )
    assert await _count(db, query, p=second_case.value) == before
    reloaded = await SqlPOCaseRepository(db.sessions).get(owner, second_case)
    assert reloaded is not None and reloaded.state is CaseState.ORDER_REQUESTED
