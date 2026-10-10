"""Integration: steps 11 and 16 prepared by code (ticket ai-automation/15).

What only the real database can show:

- the supplier's master account is found by the case's supplier name under
  the caller's tenant AND workspace (RLS), and not from another workspace;
- the lane drafts the deposit request through the real draft tables from the
  PO's own terms and prices;
- approving the request writes, in ONE transaction, the draft's confirmation,
  the `deposit_docs` document (`origin = ai_prepared`, its `draft_id`) and the
  step; a second approval of the decided draft is a 409 and writes nothing;
- confirming the deposit records the `po_payments` row (citing the deposit
  papers) in the step's transaction; the new document types are accepted by
  both CHECKs.

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
from test_purchase_orders import _as, _audit, _templates

from dw_agent_runtime.adapters.docx_templates import DocxRenderer
from dw_kernel.errors import ConflictError
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.adapters.persistence.notifications import SqlNotificationRepository
from dw_platform.adapters.persistence.scope_holders import SqlScopeHolders
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_supply_chain.action_duties import CaseDuty
from dw_supply_chain.adapters.persistence.case_document_repository import (
    SqlCaseDocumentRepository,
)
from dw_supply_chain.adapters.persistence.commercial_repository import (
    SqlPOCommercialRepository,
    SqlSupplierAccountLookup,
    SqlSupplierRecords,
)
from dw_supply_chain.adapters.persistence.document_draft_repository import (
    SqlDocumentDraftRepository,
)
from dw_supply_chain.adapters.persistence.document_extraction_repository import (
    SqlExtractionReadings,
)
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.adapters.persistence.po_step_outcomes import SqlPOStepOutcomes
from dw_supply_chain.application.document_drafts import PrepareDocumentDraft
from dw_supply_chain.application.handlers import COMMERCIAL_WRITE, PO_CASE_READ, duty_scope
from dw_supply_chain.application.po_papers import PaperGateResolver
from dw_supply_chain.application.po_steps import (
    ApprovePOStep,
    POStepSources,
    PreparePOSteps,
    po_steps_lane_context,
)
from dw_supply_chain.domain.case_document import CaseKind, DocumentType
from dw_supply_chain.domain.commercial import CommercialTerms, Incoterm, NewSupplierBankAccount
from dw_supply_chain.domain.po_case import CaseState, POCaseId
from dw_supply_chain.domain.po_step import PO_STEPS, POStepKind
from dw_supply_chain.testing.po_steps import ELMICH_PO_DOCUMENTS
from dw_supply_chain.testing.purchase_orders import ELMICH_PREPARATION, PLATFORM_DUTIES
from dw_supply_chain.testing.step_preparation import InMemoryStorage

pytestmark = pytest.mark.integration

FINANCE = duty_scope(CaseDuty.FINANCE)
MASTER = "0071000123456"


@pytest.fixture
async def db(db_urls: DatabaseUrls) -> AsyncIterator[_Db]:
    app = create_async_engine(db_urls.app, poolclass=NullPool)
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    yield _Db(async_sessionmaker(app, expire_on_commit=False), migrator)
    await app.dispose()
    await migrator.dispose()


class _Elmich:
    """The tenant's stored policies: Elmich's step preparation (PO steps on)
    and its PO papers rule."""

    async def get(self, context: AccessContext, policy_id: str) -> Any:
        if policy_id == "supply_chain_po_documents":
            return ELMICH_PO_DOCUMENTS.model_dump(mode="json")
        if policy_id == "supply_chain_step_preparation":
            return ELMICH_PREPARATION.model_dump(mode="json")
        return None

    async def put(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError("not exercised by the PO steps")


class _OneWorkspace:
    def __init__(self, owner: AccessContext) -> None:
        self.owner = owner

    async def workspaces(self) -> list[tuple[uuid.UUID, uuid.UUID]]:
        return [(self.owner.tenant_id, self.owner.workspace_id)]


def _sources(db: _Db) -> POStepSources:
    return POStepSources(
        commercial=SqlPOCommercialRepository(db.sessions),
        accounts=SqlSupplierAccountLookup(db.sessions),
        documents=SqlCaseDocumentRepository(db.sessions),
        readings=SqlExtractionReadings(db.sessions),
    )


def _papers(db: _Db) -> PaperGateResolver:
    return PaperGateResolver(
        documents=SqlCaseDocumentRepository(db.sessions),
        policy_override_repo=_Elmich(),
        platform_default=ELMICH_PO_DOCUMENTS,
    )


def _lane(db: _Db, owner: AccessContext) -> PreparePOSteps:
    drafts = SqlDocumentDraftRepository(db.sessions)
    return PreparePOSteps(
        workspaces=_OneWorkspace(owner),
        cases=SqlPOCaseRepository(db.sessions),
        sources=_sources(db),
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
        platform_default_duties=PLATFORM_DUTIES,
        holders=SqlScopeHolders(db.sessions),
        notifier=SqlNotificationRepository(db.sessions),
        clock=SystemClock(),
    )


def _approver(db: _Db) -> ApprovePOStep:
    return ApprovePOStep(
        cases=SqlPOCaseRepository(db.sessions),
        sources=_sources(db),
        drafts=SqlDocumentDraftRepository(db.sessions),
        templates=_templates(db),
        renderer=DocxRenderer(),
        storage=InMemoryStorage(),
        outcomes=SqlPOStepOutcomes(db.sessions),
        papers=_papers(db),
        policy_override_repo=_Elmich(),
        platform_default_policy=ELMICH_PREPARATION,
        platform_default_duties=PLATFORM_DUTIES,
        holders=SqlScopeHolders(db.sessions),
        notifier=SqlNotificationRepository(db.sessions),
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
        clock=SystemClock(),
    )


async def _po_created(db: _Db, owner: AccessContext) -> POCaseId:
    """A PO case of `owner`'s workspace with its PO created through step 10,
    its terms and prices set, and its supplier's master account saved."""
    placed = await _place_order(db.sessions).handle(
        _as(owner, ORDERING), case_id=(await _ready(db, owner)).id
    )
    po_case = placed.po_case
    await SqlPOCommercialRepository(db.sessions).set_terms(
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
    async with db.migrator.connect() as conn:
        supplier_id = (
            await conn.execute(
                sa.text(
                    "SELECT id FROM supply_chain.suppliers WHERE tenant_id = :t"
                    " AND workspace_id = :w AND normalized_name ="
                    " supply_chain.normalize_supplier_name(:n)"
                ),
                {"t": owner.tenant_id, "w": owner.workspace_id, "n": po_case.supplier_name},
            )
        ).scalar_one()
    await SqlSupplierRecords(db.sessions).add_bank_account(
        owner,
        NewSupplierBankAccount.of(
            id=uuid.uuid4(),
            supplier_id=supplier_id,
            bank_name="Vietcombank",
            account_number=MASTER,
            account_holder="MINH PHAT",
        ),
        audit=_audit(owner),
    )
    po_reference = f"PO-{uuid.uuid4().hex[:8]}"
    # Step 10 taken by hand (its own approval is test_purchase_orders').
    case = await SqlPOCaseRepository(db.sessions).get(owner, po_case.id)
    assert case is not None
    case.create_po(po_reference=po_reference, order_kind=case.order_kind)
    await SqlPOCaseRepository(db.sessions).save(owner, case, audit=_audit(owner))
    return po_case.id


async def test_the_master_account_is_found_by_name_in_the_callers_workspace_only(
    db: _Db,
) -> None:
    owner = _fresh()
    case_id = await _po_created(db, owner)
    case = await SqlPOCaseRepository(db.sessions).get(owner, case_id)
    assert case is not None
    lookup = SqlSupplierAccountLookup(db.sessions)
    found = await lookup.account_for(owner, case.supplier_name.upper())
    assert found is not None and found.account_number == MASTER
    elsewhere = AccessContext(
        tenant_id=owner.tenant_id,
        workspace_id=uuid.uuid4(),
        principal_id=owner.principal_id,
        roles=owner.roles,
        scopes=owner.scopes,
        plan_id=owner.plan_id,
    )
    assert await lookup.account_for(elsewhere, case.supplier_name) is None
    other_tenant = _fresh()
    assert await lookup.account_for(other_tenant, case.supplier_name) is None


async def test_the_deposit_request_and_its_confirmation_write_together(db: _Db) -> None:
    owner = _fresh()
    case_id = await _po_created(db, owner)
    lane_context = po_steps_lane_context(owner.tenant_id, owner.workspace_id)
    spec = PO_STEPS[POStepKind.DEPOSIT_REQUEST]
    assert await _lane(db, owner).draft(lane_context, spec, case_id) == 1
    drafts = SqlDocumentDraftRepository(db.sessions)
    (draft,) = [
        d
        for d in await drafts.latest_for_case(owner, CaseKind.PO, case_id.value)
        if d.doc_type is DocumentType.DEPOSIT_DOCS
    ]
    assert draft.fields["account_number"]["value"] == MASTER
    buyer = _as(owner, ORDERING, COMMERCIAL_WRITE, PO_CASE_READ)
    case = await _approver(db).handle(
        buyer,
        case_id,
        kind=POStepKind.DEPOSIT_REQUEST,
        draft_id=draft.id,
        content_sha256=draft.content_sha256,
        results={},
    )
    assert case.state is CaseState.WAITING_DEPOSIT
    query = (
        "SELECT count(*) FROM supply_chain.case_documents WHERE po_case_id = :p"
        " AND doc_type = 'deposit_docs' AND origin = 'ai_prepared' AND draft_id IS NOT NULL"
    )
    assert await _count(db, query, p=case_id.value) == 1
    with pytest.raises(ConflictError):
        await _approver(db).handle(
            buyer,
            case_id,
            kind=POStepKind.DEPOSIT_REQUEST,
            draft_id=draft.id,
            content_sha256=draft.content_sha256,
            results={},
        )
    assert await _count(db, query, p=case_id.value) == 1

    accountant = _as(owner, FINANCE, COMMERCIAL_WRITE, PO_CASE_READ)
    case = await _approver(db).handle(
        accountant,
        case_id,
        kind=POStepKind.DEPOSIT_PAYMENT,
        draft_id=None,
        content_sha256=None,
        results={"paid_amount": "1275", "paid_on": "2026-10-11"},
    )
    assert case.state is CaseState.DEPOSIT_CONFIRMED
    async with db.migrator.connect() as conn:
        payment = (
            await conn.execute(
                sa.text(
                    "SELECT p.kind, p.amount, d.doc_type FROM supply_chain.po_payments p"
                    " JOIN supply_chain.case_documents d ON d.id = p.document_id"
                    " WHERE p.po_case_id = :p"
                ),
                {"p": case_id.value},
            )
        ).one()
    assert (payment.kind, payment.amount, payment.doc_type) == (
        "deposit",
        Decimal("1275.00"),
        "deposit_docs",
    )


async def test_the_payment_papers_are_document_types_both_checks_accept(db: _Db) -> None:
    async with db.migrator.connect() as conn:
        for constraint in ("ck_case_documents_doc_type", "ck_document_drafts_doc_type"):
            definition = (
                await conn.execute(
                    sa.text(
                        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :c"
                    ),
                    {"c": constraint},
                )
            ).scalar_one()
            for doc_type in ("proforma_invoice", "commercial_invoice", "bank_transfer_receipt"):
                assert f"'{doc_type}'" in definition
