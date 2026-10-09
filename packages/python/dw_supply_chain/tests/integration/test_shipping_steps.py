"""Integration: steps 13-15 prepared by code (ticket ai-automation/17).

What only the real database can show:

- the seven new document types and the `production_progress` purpose are
  accepted by the CHECKs, and `ck_po_cases_container_number` refuses a
  container number that is not ISO 6346 from any role;
- QC's fail writes, in ONE transaction, the rework request's confirmation,
  the `rework_request` document (`origin = ai_prepared`) and the step to
  `rework`; QC's pass on another case closes the rework draft (a `rejected`
  decision) and writes the container number with the step;
- the arrival writes the ETA typed with the step, under the caller's RLS.

Owed when written (2026-10-10): not run, no Docker on the machine that wrote it.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls
from test_place_order import _fresh
from test_po_steps import _approver, _lane, _po_created
from test_product_cases import _count, _Db
from test_purchase_orders import _as, _audit

from dw_kernel.errors import NotFoundError
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.action_duties import CaseDuty
from dw_supply_chain.adapters.persistence.case_document_repository import (
    SqlCaseDocumentRepository,
)
from dw_supply_chain.adapters.persistence.document_draft_repository import (
    SqlDocumentDraftRepository,
)
from dw_supply_chain.adapters.persistence.document_extraction_repository import (
    SqlDocumentExtractionRepository,
)
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.application.document_extraction import NewExtraction
from dw_supply_chain.application.handlers import PO_CASE_READ, duty_scope
from dw_supply_chain.application.po_steps import po_steps_lane_context
from dw_supply_chain.application.ports import NewCaseDocument
from dw_supply_chain.domain.case_document import (
    CaseDocumentId,
    CaseKind,
    DocumentType,
    ObjectKey,
)
from dw_supply_chain.domain.extraction import ExtractionStatus
from dw_supply_chain.domain.po_case import CaseState, POCaseId
from dw_supply_chain.domain.po_step import PO_STEPS, POStepKind

pytestmark = pytest.mark.integration

QC = duty_scope(CaseDuty.QC)
LOGISTICS = duty_scope(CaseDuty.LOGISTICS)
NEW_DOC_TYPES = (
    "production_schedule",
    "qc_report",
    "packing_list",
    "bill_of_lading",
    "arrival_notice",
    "certificate_of_origin",
    "rework_request",
)


@pytest.fixture
async def db(db_urls: DatabaseUrls) -> AsyncIterator[_Db]:
    app = create_async_engine(db_urls.app, poolclass=NullPool)
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    yield _Db(async_sessionmaker(app, expire_on_commit=False), migrator)
    await app.dispose()
    await migrator.dispose()


async def _at(db: _Db, case_id: POCaseId, state: CaseState) -> None:
    """The case moved to `state` by hand: steps 11-12 are AI-15/16's tests."""
    async with db.migrator.begin() as conn:
        await conn.execute(
            sa.text("UPDATE supply_chain.po_cases SET state = :s WHERE id = :p"),
            {"s": state.value, "p": case_id.value},
        )


async def _qc_case(db: _Db, owner: AccessContext) -> POCaseId:
    case_id = await _po_created(db, owner)
    await _at(db, case_id, CaseState.QC)
    return case_id


def _cited(value: str) -> dict[str, str]:
    return {"value": value, "quote": value}


async def _failing_report(db: _Db, owner: AccessContext, case_id: POCaseId) -> None:
    """A QC report on the case and its reading: 9 major defects over Ac 7."""
    document_id = uuid.uuid4()
    document = await SqlCaseDocumentRepository(db.sessions).add(
        owner,
        NewCaseDocument(
            id=CaseDocumentId(document_id),
            case_kind=CaseKind.PO,
            case_id=case_id.value,
            doc_type=DocumentType.QC_REPORT,
            object_key=ObjectKey.build(
                tenant_id=owner.tenant_id,
                workspace_id=owner.workspace_id,
                case_kind=CaseKind.PO,
                case_id=case_id.value,
                document_id=document_id,
            ).value,
            filename="qc.pdf",
            content_type="application/pdf",
            size_bytes=10,
            sha256=hashlib.sha256(document_id.bytes).hexdigest(),
        ),
        audit=_audit(owner),
    )
    await SqlDocumentExtractionRepository(db.sessions).add(
        owner,
        NewExtraction(
            id=uuid.uuid4(),
            document_id=document.id.value,
            doc_type=document.doc_type,
            sha256=document.sha256,
            prompt_id="supply_chain.extract_qc_report",
            prompt_version="1.0.0",
            model_profile="luna",
            status=ExtractionStatus.EXTRACTED,
            text="Major: 9 (Ac 7)",
            fields={"major_found": _cited("9"), "major_accept": _cited("7")},
            gaps=[],
        ),
        audit=_audit(owner),
    )


async def _rework_draft(db: _Db, owner: AccessContext, case_id: POCaseId) -> object:
    """The rework request the lane drafts from the failing report."""
    await _failing_report(db, owner, case_id)
    lane_context = po_steps_lane_context(owner.tenant_id, owner.workspace_id)
    assert await _lane(db, owner).draft(lane_context, PO_STEPS[POStepKind.QC], case_id) == 1
    (draft,) = [
        d
        for d in await SqlDocumentDraftRepository(db.sessions).latest_for_case(
            owner, CaseKind.PO, case_id.value
        )
        if d.doc_type is DocumentType.REWORK_REQUEST
    ]
    return draft


async def test_the_new_papers_and_the_weekly_chase_are_values_the_checks_accept(
    db: _Db,
) -> None:
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
            for doc_type in NEW_DOC_TYPES:
                assert f"'{doc_type}'" in definition
        purposes = (
            await conn.execute(
                sa.text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                    " WHERE conname = 'ck_supplier_messages_purpose'"
                )
            )
        ).scalar_one()
        assert "'production_progress'" in purposes


async def test_a_container_number_not_iso_6346_is_refused_by_the_table(db: _Db) -> None:
    owner = _fresh()
    case_id = await _po_created(db, owner)
    with pytest.raises(IntegrityError, match="ck_po_cases_container_number"):
        async with db.migrator.begin() as conn:
            await conn.execute(
                sa.text("UPDATE supply_chain.po_cases SET container_number = :c WHERE id = :p"),
                {"c": "MSCU 1234567", "p": case_id.value},
            )


async def test_qc_fail_files_the_rework_request_with_the_step(db: _Db) -> None:
    owner = _fresh()
    case_id = await _qc_case(db, owner)
    draft = await _rework_draft(db, owner, case_id)
    case = await _approver(db).handle(
        _as(owner, QC, PO_CASE_READ),
        case_id,
        kind=POStepKind.QC,
        draft_id=draft.id,  # type: ignore[attr-defined]
        content_sha256=draft.content_sha256,  # type: ignore[attr-defined]
        results={"qc_result": "fail", "reason": "Lỗi nặng vượt Ac"},
    )
    assert case.state is CaseState.REWORK
    query = (
        "SELECT count(*) FROM supply_chain.case_documents WHERE po_case_id = :p"
        " AND doc_type = 'rework_request' AND origin = 'ai_prepared' AND draft_id IS NOT NULL"
    )
    assert await _count(db, query, p=case_id.value) == 1


async def test_qc_pass_closes_the_rework_draft_and_records_the_container(db: _Db) -> None:
    owner = _fresh()
    case_id = await _qc_case(db, owner)
    draft = await _rework_draft(db, owner, case_id)
    case = await _approver(db).handle(
        _as(owner, QC, PO_CASE_READ),
        case_id,
        kind=POStepKind.QC,
        draft_id=draft.id,  # type: ignore[attr-defined]
        content_sha256=draft.content_sha256,  # type: ignore[attr-defined]
        results={"qc_result": "pass", "container_number": "mscu1234567"},
    )
    assert case.state is CaseState.IN_TRANSIT
    stored = await SqlPOCaseRepository(db.sessions).get(owner, case_id)
    assert stored is not None and stored.shipping.container_number == "MSCU1234567"
    closed = (
        "SELECT count(*) FROM supply_chain.document_draft_decisions WHERE draft_id = :d"
        " AND decision = 'rejected'"
    )
    assert await _count(db, closed, d=draft.id) == 1  # type: ignore[attr-defined]
    no_paper = (
        "SELECT count(*) FROM supply_chain.case_documents WHERE po_case_id = :p"
        " AND doc_type = 'rework_request'"
    )
    assert await _count(db, no_paper, p=case_id.value) == 0


async def test_the_arrival_writes_the_eta_typed_and_no_other_tenant_reaches_it(
    db: _Db,
) -> None:
    owner = _fresh()
    case_id = await _po_created(db, owner)
    await _at(db, case_id, CaseState.IN_TRANSIT)
    other = _fresh()
    with pytest.raises(NotFoundError):
        await _approver(db).handle(
            _as(other, LOGISTICS, PO_CASE_READ),
            case_id,
            kind=POStepKind.ARRIVAL,
            draft_id=None,
            content_sha256=None,
            results={"eta": "2026-12-02"},
        )
    case = await _approver(db).handle(
        _as(owner, LOGISTICS, PO_CASE_READ),
        case_id,
        kind=POStepKind.ARRIVAL,
        draft_id=None,
        content_sha256=None,
        results={"eta": "2026-12-02"},
    )
    assert case.state is CaseState.ARRIVED_PORT
    eta = "SELECT count(*) FROM supply_chain.po_cases WHERE id = :p AND eta = DATE '2026-12-02'"
    assert await _count(db, eta, p=case_id.value) == 1
