"""Integration: step 17 prepared by code (ticket ai-automation/18).

What only the real database can show:

- the counts are written with the step, in ONE transaction: the
  goods-received note (`warehouse_receipt`, `origin = ai_prepared`), one
  `po_case_line_receipts` row per PO line citing it, the step to `completed`;
- `SqlLineReceipts` reads them back under RLS: another tenant and another
  workspace of the same tenant read nothing, and a case whose count differs
  is found in the window only by its own workspace;
- the table refuses a negative count (`ck_po_case_line_receipts_counted`) and
  a second version 1 of a line (`uq_..._version`) from any role;
- the new document types and the `discrepancy_claim` purpose are values the
  CHECKs accept.

Owed when written (2026-10-10): not run, no Docker on the machine that wrote it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls
from test_place_order import _fresh
from test_po_steps import _approver, _lane, _po_created
from test_product_cases import _count, _Db
from test_purchase_orders import _as

from dw_platform.application.access_context import AccessContext
from dw_supply_chain.action_duties import CaseDuty
from dw_supply_chain.adapters.persistence.document_draft_repository import (
    SqlDocumentDraftRepository,
)
from dw_supply_chain.adapters.persistence.line_receipts import SqlLineReceipts
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.application.handlers import PO_CASE_READ, duty_scope
from dw_supply_chain.application.po_steps import po_steps_lane_context
from dw_supply_chain.domain.case_document import CaseKind, DocumentType
from dw_supply_chain.domain.po_case import CaseState, POCaseId
from dw_supply_chain.domain.po_step import PO_STEPS, POStepKind

pytestmark = pytest.mark.integration

WAREHOUSE = duty_scope(CaseDuty.WAREHOUSE)


@pytest.fixture
async def db(db_urls: DatabaseUrls) -> AsyncIterator[_Db]:
    app = create_async_engine(db_urls.app, poolclass=NullPool)
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    yield _Db(async_sessionmaker(app, expire_on_commit=False), migrator)
    await app.dispose()
    await migrator.dispose()


def _elsewhere(owner: AccessContext) -> AccessContext:
    return owner.model_copy(update={"workspace_id": uuid.uuid4()})


async def _counted(db: _Db, owner: AccessContext, first: int) -> POCaseId:
    """A PO case at the warehouse, its note drafted, counted and approved:
    `first` for the first line, the ordered quantity for the others."""
    case_id = await _po_created(db, owner)
    async with db.migrator.begin() as conn:
        await conn.execute(
            sa.text("UPDATE supply_chain.po_cases SET state = 'warehouse_receiving' WHERE id = :p"),
            {"p": case_id.value},
        )
    lane_context = po_steps_lane_context(owner.tenant_id, owner.workspace_id)
    spec = PO_STEPS[POStepKind.WAREHOUSE]
    assert await _lane(db, owner).draft(lane_context, spec, case_id) == 1
    (note,) = [
        d
        for d in await SqlDocumentDraftRepository(db.sessions).latest_for_case(
            owner, CaseKind.PO, case_id.value
        )
        if d.doc_type is DocumentType.WAREHOUSE_RECEIPT
    ]
    case = await SqlPOCaseRepository(db.sessions).get(owner, case_id)
    assert case is not None
    counts = {line.sku_id: (line.quantity or 0) for line in case.lines}
    counts[case.lines[0].sku_id] = first
    moved = await _approver(db).handle(
        _as(owner, WAREHOUSE, PO_CASE_READ),
        case_id,
        kind=POStepKind.WAREHOUSE,
        draft_id=note.id,
        content_sha256=note.content_sha256,
        results={},
        counts=counts,
    )
    assert moved.state is CaseState.COMPLETED
    return case_id


async def test_the_counts_are_written_with_the_step_citing_the_note(db: _Db) -> None:
    owner = _fresh()
    case_id = await _counted(db, owner, first=1)
    query = (
        "SELECT count(*) FROM supply_chain.po_case_line_receipts r"
        " JOIN supply_chain.case_documents d ON d.id = r.document_id"
        " WHERE r.po_case_id = :p AND d.doc_type = 'warehouse_receipt'"
        " AND d.origin = 'ai_prepared'"
    )
    case = await SqlPOCaseRepository(db.sessions).get(owner, case_id)
    assert case is not None
    assert await _count(db, query, p=case_id.value) == len(case.lines)


async def test_the_counts_stay_in_their_workspace(db: _Db) -> None:
    owner = _fresh()
    case_id = await _counted(db, owner, first=1)
    receipts = SqlLineReceipts(db.sessions)
    since = datetime.now(UTC) - timedelta(days=1)
    assert case_id.value in await receipts.discrepant_cases(owner, since=since)
    assert await receipts.for_case(owner, case_id.value)
    for stranger in (_elsewhere(owner), _fresh()):
        assert await receipts.for_case(stranger, case_id.value) == []
        assert case_id.value not in await receipts.discrepant_cases(stranger, since=since)


async def test_the_table_refuses_a_negative_count_and_a_second_version_one(db: _Db) -> None:
    owner = _fresh()
    case_id = await _counted(db, owner, first=1)
    async with db.migrator.connect() as conn:
        row = (
            await conn.execute(
                sa.text(
                    "SELECT tenant_id, workspace_id, sku_id, sku_code"
                    " FROM supply_chain.po_case_line_receipts WHERE po_case_id = :p LIMIT 1"
                ),
                {"p": case_id.value},
            )
        ).one()
    insert = sa.text(
        "INSERT INTO supply_chain.po_case_line_receipts (id, tenant_id, workspace_id,"
        " po_case_id, sku_id, version, sku_code, counted, recorded_by)"
        " VALUES (:id, :t, :w, :p, :s, :v, :c, :n, :by)"
    )
    values = {
        "t": row.tenant_id,
        "w": row.workspace_id,
        "p": case_id.value,
        "s": row.sku_id,
        "c": row.sku_code,
        "by": owner.principal_id,
    }
    with pytest.raises(IntegrityError, match="ck_po_case_line_receipts_counted"):
        async with db.migrator.begin() as conn:
            await conn.execute(insert, {**values, "id": uuid.uuid4(), "v": 2, "n": -1})
    with pytest.raises(IntegrityError, match="uq_po_case_line_receipts_tenant_id_po_case_id"):
        async with db.migrator.begin() as conn:
            await conn.execute(insert, {**values, "id": uuid.uuid4(), "v": 1, "n": 5})


async def test_the_new_papers_and_the_claim_are_values_the_checks_accept(db: _Db) -> None:
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
            for doc_type in ("warehouse_receipt", "discrepancy_report"):
                assert f"'{doc_type}'" in definition
        purposes = (
            await conn.execute(
                sa.text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                    " WHERE conname = 'ck_supplier_messages_purpose'"
                )
            )
        ).scalar_one()
        assert "'discrepancy_claim'" in purposes
