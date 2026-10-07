"""Integration: product-development cases on the real database (stage-1 tickets
01 and 03).

What only Postgres can prove: the four tables' RLS narrows by tenant AND
workspace on read and write, a child cannot sit in another workspace than its
case, the CHECKs equal the domain's enums, the proposal code is unique per
tenant and refused by its constraint's name, a round is closed once whatever
the statement says, a round's paper and a step's paper (BM04, the supplier's
email) must be a document of its own case, when the case reached its step is
read back from the history (a resume does not count), the
grants are what the migration says, and offboarding purges a tenant's
product cases, rounds and documents in every workspace and nobody else's.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls

from dw_kernel.errors import ConflictError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.pagination import PageQuery, page_request
from dw_kernel.ports import SystemClock
from dw_platform.adapters.persistence.offboarding import SqlTenantOffboarding
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence.case_document_repository import (
    SqlCaseDocumentRepository,
)
from dw_supply_chain.adapters.persistence.product_case_repository import (
    ITEM_CODE_CONSTRAINT,
    PROPOSAL_CODE_CONSTRAINT,
    SKU_CODE_CONSTRAINT,
    SqlProductCaseRepository,
)
from dw_supply_chain.application.ports import NewCaseDocument, ProductCaseListFilter
from dw_supply_chain.domain.case_document import (
    CaseDocument,
    CaseDocumentId,
    CaseKind,
    DocumentType,
    ObjectKey,
)
from dw_supply_chain.domain.product_development_case import (
    ProductAction,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
    SampleResult,
    SkuDraft,
)

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.7\n"
TABLES = (
    "product_dev_cases",
    "product_dev_case_state_transitions",
    "product_sample_rounds",
    "sample_revision_requests",
)


@dataclass(frozen=True)
class _Db:
    sessions: async_sessionmaker[AsyncSession]
    migrator: AsyncEngine

    @property
    def cases(self) -> SqlProductCaseRepository:
        return SqlProductCaseRepository(self.sessions)

    @property
    def documents(self) -> SqlCaseDocumentRepository:
        return SqlCaseDocumentRepository(self.sessions)


@pytest.fixture
async def db(db_urls: DatabaseUrls) -> AsyncIterator[_Db]:
    app = create_async_engine(db_urls.app, poolclass=NullPool)
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    yield _Db(async_sessionmaker(app, expire_on_commit=False), migrator)
    await app.dispose()
    await migrator.dispose()


def _context(tenant: uuid.UUID, workspace: uuid.UUID) -> AccessContext:
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=frozenset(),
        plan_id="professional",
    )


def _audit(context: AccessContext, case: ProductDevelopmentCase, action: str) -> AuditEvent:
    return AuditEvent(
        id=uuid.uuid4(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        actor_id=UserId(context.principal_id),
        action=f"supply_chain.product_case.{action}",
        resource_type="product_dev_case",
        resource_id=str(case.id),
        occurred_at=SystemClock().now(),
    )


async def _proposed(
    db: _Db, context: AccessContext, *, code: str | None = None
) -> ProductDevelopmentCase:
    case = ProductDevelopmentCase.propose(
        id=ProductDevelopmentCaseId(uuid.uuid4()),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        proposal_code=code or f"DX-{uuid.uuid4().hex[:8]}",
        product_name="Nồi inox 3 đáy 24cm",
        category="Nồi",
        actor_id=context.principal_id,
    )
    await db.cases.add(context, case, audit=_audit(context, case, "propose"))
    return case


async def _reloaded(
    db: _Db, context: AccessContext, case: ProductDevelopmentCase
) -> ProductDevelopmentCase:
    found = await db.cases.get(context, case.id)
    assert found is not None
    return found


async def _testing(db: _Db, context: AccessContext) -> ProductDevelopmentCase:
    case = await _proposed(db, context)
    case.request_sample(actor_id=context.principal_id, supplier_name="NCC Minh Long")
    await db.cases.save(context, case, audit=_audit(context, case, "request_sample"))
    case.receive_sample(actor_id=context.principal_id)
    await db.cases.save(context, case, audit=_audit(context, case, "receive_sample"))
    return await _reloaded(db, context, case)


async def _document(
    db: _Db, context: AccessContext, case: ProductDevelopmentCase, doc_type: DocumentType
) -> CaseDocument:
    document_id = CaseDocumentId(uuid.uuid4())
    new = NewCaseDocument(
        id=document_id,
        case_kind=CaseKind.PRODUCT,
        case_id=case.id.value,
        doc_type=doc_type,
        object_key=ObjectKey.build(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            case_kind=CaseKind.PRODUCT,
            case_id=case.id.value,
            document_id=document_id.value,
        ).value,
        filename="bien-ban.pdf",
        content_type="application/pdf",
        size_bytes=len(PDF),
        sha256="0" * 64,
    )
    return await db.documents.add(
        context,
        new,
        audit=AuditEvent(
            id=uuid.uuid4(),
            tenant_id=TenantId(context.tenant_id),
            workspace_id=WorkspaceId(context.workspace_id),
            actor_id=UserId(context.principal_id),
            action="supply_chain.document.upload",
            resource_type="case_document",
            resource_id=str(document_id),
            occurred_at=SystemClock().now(),
        ),
    )


async def _count(db: _Db, sql: str, **params: object) -> int:
    async with db.migrator.connect() as conn:
        return int(await conn.scalar(sa.text(sql), params) or 0)


# --- the steps, saved ------------------------------------------------------------


async def test_steps_one_to_five_are_saved_with_history_rounds_and_audit(db: _Db) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _testing(db, context)
    assert case.round_opened_at is not None

    revision = await _document(db, context, case, DocumentType.SAMPLE_REVISION_REQUEST)
    case.request_revision(
        actor_id=context.principal_id, reason="Tay cầm lỏng", revision_request=revision
    )
    await db.cases.save(context, case, audit=_audit(context, case, "request_revision"))
    case.receive_revised_sample(actor_id=context.principal_id)
    await db.cases.save(context, case, audit=_audit(context, case, "receive_revised_sample"))
    case = await _reloaded(db, context, case)
    evaluation = await _document(db, context, case, DocumentType.SAMPLE_EVALUATION)
    case.pass_sample(actor_id=context.principal_id, evaluation=evaluation)
    await db.cases.save(context, case, audit=_audit(context, case, "pass_sample"))

    stored = await _reloaded(db, context, case)
    assert (stored.state, stored.sample_round, stored.supplier_name) == (
        ProductDevState.PENDING_BOD_REVIEW,
        2,
        "NCC Minh Long",
    )
    history = await db.cases.list_transitions(context, case.id)
    assert [t.action for t in history] == [
        ProductAction.PROPOSE,
        ProductAction.REQUEST_SAMPLE,
        ProductAction.RECEIVE_SAMPLE,
        ProductAction.REQUEST_REVISION,
        ProductAction.RECEIVE_REVISED_SAMPLE,
        ProductAction.PASS_SAMPLE,
    ]
    assert history[0].from_state is None
    assert history[3].reason == "Tay cầm lỏng"
    rounds = await db.cases.list_rounds(context, case.id)
    assert [(r.round_no, r.result) for r in rounds] == [
        (1, SampleResult.NEEDS_REVISION),
        (2, SampleResult.PASSED),
    ]
    assert rounds[0].revision_document_id == revision.id.value
    assert rounds[0].requested_changes == "Tay cầm lỏng"
    assert rounds[1].evaluation_document_id == evaluation.id.value
    assert (
        await _count(
            db,
            "SELECT count(*) FROM platform.audit_events WHERE resource_id = :r",
            r=str(case.id),
        )
        == 6
    )


async def test_a_round_one_report_is_refused_for_round_two_by_its_upload_time(db: _Db) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _testing(db, context)
    round_one_report = await _document(db, context, case, DocumentType.SAMPLE_EVALUATION)
    revision = await _document(db, context, case, DocumentType.SAMPLE_REVISION_REQUEST)
    case.request_revision(actor_id=context.principal_id, reason="x", revision_request=revision)
    await db.cases.save(context, case, audit=_audit(context, case, "request_revision"))
    case.receive_revised_sample(actor_id=context.principal_id)
    await db.cases.save(context, case, audit=_audit(context, case, "receive_revised_sample"))
    case = await _reloaded(db, context, case)

    assert case.round_opened_at is not None
    assert round_one_report.uploaded_at < case.round_opened_at
    with pytest.raises(ConflictError, match="sample_evaluation"):
        case.pass_sample(actor_id=context.principal_id, evaluation=round_one_report)


# --- the proposal code -------------------------------------------------------------


async def test_a_proposal_code_is_unique_per_tenant_by_its_constraint(db: _Db) -> None:
    tenant = uuid.uuid4()
    first = _context(tenant, uuid.uuid4())
    await _proposed(db, first, code="DX-2026-001")

    # Another workspace of the same tenant: refused, by the constraint's name.
    with pytest.raises(ConflictError) as raised:
        await _proposed(db, _context(tenant, uuid.uuid4()), code="DX-2026-001")
    assert raised.value.details["constraint"] == PROPOSAL_CODE_CONSTRAINT

    # Another tenant: its own code space.
    other = await _proposed(db, _context(uuid.uuid4(), uuid.uuid4()), code="DX-2026-001")
    assert other.proposal_code == "DX-2026-001"


# --- RLS: four tables, read and write -----------------------------------------------


async def _all_four(db: _Db, context: AccessContext) -> ProductDevelopmentCase:
    """A case with a row in every one of the four tables."""
    case = await _testing(db, context)
    revision = await _document(db, context, case, DocumentType.SAMPLE_REVISION_REQUEST)
    case.request_revision(actor_id=context.principal_id, reason="x", revision_request=revision)
    await db.cases.save(context, case, audit=_audit(context, case, "request_revision"))
    return case


@pytest.mark.parametrize("other", ["tenant", "workspace"])
async def test_another_tenant_or_workspace_reads_none_of_the_four_tables(
    db: _Db, other: str
) -> None:
    owner = _context(uuid.uuid4(), uuid.uuid4())
    case = await _all_four(db, owner)
    caller = _context(uuid.uuid4() if other == "tenant" else owner.tenant_id, uuid.uuid4())

    assert await db.cases.get(caller, case.id) is None
    assert await db.cases.case_workspace(caller, case.id.value) is None
    assert await db.cases.list_transitions(caller, case.id) == []
    assert await db.cases.list_rounds(caller, case.id) == []
    page = await db.cases.list_page(
        caller,
        page_request(limit=50, cursor=None, query=PageQuery(key="t")),
        ProductCaseListFilter(),
    )
    assert case.id.value not in {c.id.value for c in page.items}
    async with tenant_session(db.sessions, TenantScope.from_access_context(caller)) as session:
        for table in TABLES:
            column = "id" if table == "product_dev_cases" else "product_dev_case_id"
            seen = await session.scalar(
                sa.text(f"SELECT count(*) FROM supply_chain.{table} WHERE {column} = :c"),
                {"c": case.id.value},
            )
            assert seen == 0, table
    # The owner sees every one.
    assert len(await db.cases.list_rounds(owner, case.id)) == 1


async def test_a_connection_that_never_scopes_itself_reads_nothing(db: _Db) -> None:
    await _all_four(db, _context(uuid.uuid4(), uuid.uuid4()))
    async with db.sessions() as session:
        for table in TABLES:
            assert await session.scalar(sa.text(f"SELECT count(*) FROM supply_chain.{table}")) == 0


@pytest.mark.parametrize("other", ["tenant", "workspace"])
async def test_the_repository_filters_by_tenant_and_workspace_even_where_rls_does_not_apply(
    db: _Db, other: str
) -> None:
    """The second layer on its own, as D's documents test it: on the
    migrator's connection, which bypasses RLS, the repository's own tenant and
    workspace predicates are all that is left, on every read and on the save."""
    owner = _context(uuid.uuid4(), uuid.uuid4())
    case = await _all_four(db, owner)
    caller = _context(uuid.uuid4() if other == "tenant" else owner.tenant_id, uuid.uuid4())
    unprotected = SqlProductCaseRepository(async_sessionmaker(db.migrator, expire_on_commit=False))

    assert await unprotected.get(caller, case.id) is None
    assert await unprotected.case_workspace(caller, case.id.value) is None
    assert await unprotected.list_transitions(caller, case.id) == []
    assert await unprotected.list_rounds(caller, case.id) == []
    page = await unprotected.list_page(
        caller,
        page_request(limit=50, cursor=None, query=PageQuery(key="t")),
        ProductCaseListFilter(),
    )
    assert case.id.value not in {c.id.value for c in page.items}
    moved = await _reloaded(db, owner, case)
    moved.receive_revised_sample(actor_id=caller.principal_id)
    with pytest.raises(ConflictError):
        await unprotected.save(caller, moved, audit=_audit(caller, moved, "receive_revised_sample"))
    # The owner, on the same unprotected connection, still reads its case.
    assert await unprotected.get(owner, case.id) is not None
    assert (await _reloaded(db, owner, case)).state is ProductDevState.REVISION_REQUESTED


@pytest.mark.parametrize("other", ["tenant", "workspace"])
async def test_another_tenant_or_workspace_cannot_move_the_case(db: _Db, other: str) -> None:
    """The save's UPDATE reaches no row under the caller's RLS: a refusal,
    and nothing about the case changes."""
    owner = _context(uuid.uuid4(), uuid.uuid4())
    case = await _proposed(db, owner)
    caller = _context(uuid.uuid4() if other == "tenant" else owner.tenant_id, uuid.uuid4())
    case.request_sample(actor_id=caller.principal_id, supplier_name="NCC")

    with pytest.raises(ConflictError):
        await db.cases.save(caller, case, audit=_audit(caller, case, "request_sample"))
    assert (await _reloaded(db, owner, case)).state is ProductDevState.PROPOSED


@pytest.mark.parametrize("table", TABLES[1:])
async def test_a_child_cannot_be_written_into_another_tenant_or_workspace(
    db: _Db, table: str
) -> None:
    """A child row naming the CALLER's own tenant and workspace and a case
    that is not there: the composite FK finds no case (the row itself passes
    WITH CHECK). The row naming the case's real owner is the next test's."""
    owner = _context(uuid.uuid4(), uuid.uuid4())
    case = await _testing(db, owner)
    revision = await _document(db, owner, case, DocumentType.SAMPLE_REVISION_REQUEST)
    values = {
        "product_dev_case_state_transitions": (
            "(id, tenant_id, workspace_id, product_dev_case_id, action, from_state, to_state,"
            " actor_id) VALUES (gen_random_uuid(), :t, :w, :c, 'resume', 'blocked',"
            " 'proposed', gen_random_uuid())"
        ),
        "product_sample_rounds": (
            "(id, tenant_id, workspace_id, product_dev_case_id, round_no, opened_by)"
            " VALUES (gen_random_uuid(), :t, :w, :c, 9, gen_random_uuid())"
        ),
        "sample_revision_requests": (
            "(id, tenant_id, workspace_id, product_dev_case_id, round_no,"
            " revision_document_id, requested_changes, sent_by)"
            f" VALUES (gen_random_uuid(), :t, :w, :c, 1, '{revision.id}', 'x', gen_random_uuid())"
        ),
    }[table]
    for caller in (
        _context(uuid.uuid4(), uuid.uuid4()),
        _context(owner.tenant_id, uuid.uuid4()),
    ):
        with pytest.raises(IntegrityError):
            async with tenant_session(
                db.sessions, TenantScope.from_access_context(caller)
            ) as session:
                await session.execute(
                    sa.text(f"INSERT INTO supply_chain.{table} {values}"),
                    {"t": caller.tenant_id, "w": caller.workspace_id, "c": case.id.value},
                )


_CHILD_ROWS = {
    "product_dev_case_state_transitions": (
        "(id, tenant_id, workspace_id, product_dev_case_id, action, from_state, to_state,"
        " actor_id) VALUES (gen_random_uuid(), :t, :w, :c, 'resume', 'blocked',"
        " 'proposed', :p)"
    ),
    "product_sample_rounds": (
        "(id, tenant_id, workspace_id, product_dev_case_id, round_no, opened_by)"
        " VALUES (gen_random_uuid(), :t, :w, :c, 9, :p)"
    ),
    "sample_revision_requests": (
        "(id, tenant_id, workspace_id, product_dev_case_id, round_no,"
        " revision_document_id, requested_changes, sent_by)"
        " VALUES (gen_random_uuid(), :t, :w, :c, 1, :d, 'x', :p)"
    ),
}


@pytest.mark.parametrize("other", ["tenant", "workspace"])
@pytest.mark.parametrize("table", TABLES[1:])
async def test_a_child_naming_the_owners_case_is_refused_by_rls(
    db: _Db, table: str, other: str
) -> None:
    """The real attack: another tenant (or another workspace of the same
    tenant) writes a child row naming the OWNER's tenant, workspace and case,
    with valid round and document references. The composite FK accepts that
    row (referential checks do not pass through RLS), so only the policy's
    WITH CHECK stands between it and the owner's case."""
    owner = _context(uuid.uuid4(), uuid.uuid4())
    case = await _testing(db, owner)
    revision = await _document(db, owner, case, DocumentType.SAMPLE_REVISION_REQUEST)
    caller = _context(uuid.uuid4() if other == "tenant" else owner.tenant_id, uuid.uuid4())
    count = f"SELECT count(*) FROM supply_chain.{table} WHERE product_dev_case_id = :c"
    before = await _count(db, count, c=case.id.value)

    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(db.sessions, TenantScope.from_access_context(caller)) as session:
            await session.execute(
                sa.text(f"INSERT INTO supply_chain.{table} {_CHILD_ROWS[table]}"),
                {
                    "t": owner.tenant_id,
                    "w": owner.workspace_id,
                    "c": case.id.value,
                    "d": revision.id.value,
                    "p": caller.principal_id,
                },
            )
    assert await _count(db, count, c=case.id.value) == before, table


@pytest.mark.parametrize("other", ["tenant", "workspace"])
async def test_another_tenant_or_workspace_updates_no_case_or_round_row(
    db: _Db, other: str
) -> None:
    """Raw UPDATEs naming the case by id alone, so nothing but the policy's
    USING narrows them: they reach no row, and the case and its open round
    are as they were."""
    owner = _context(uuid.uuid4(), uuid.uuid4())
    case = await _testing(db, owner)
    caller = _context(uuid.uuid4() if other == "tenant" else owner.tenant_id, uuid.uuid4())

    async with tenant_session(db.sessions, TenantScope.from_access_context(caller)) as session:
        moved = await session.execute(
            sa.text(
                "UPDATE supply_chain.product_dev_cases SET state = 'cancelled',"
                " version = version + 1 WHERE id = :c"
            ),
            {"c": case.id.value},
        )
        closed = await session.execute(
            sa.text(
                "UPDATE supply_chain.product_sample_rounds SET result = 'rejected',"
                " closed_at = now(), closed_by = :p WHERE product_dev_case_id = :c"
            ),
            {"c": case.id.value, "p": caller.principal_id},
        )
        rowcounts = (moved.rowcount, closed.rowcount)  # type: ignore[attr-defined]
    assert rowcounts == (0, 0)
    stored = await _reloaded(db, owner, case)
    assert (stored.state, stored.version) == (ProductDevState.SAMPLE_TESTING, case.version)
    assert [r.result for r in await db.cases.list_rounds(owner, case.id)] == [None]


async def test_a_row_naming_another_tenant_is_refused_by_rls(db: _Db) -> None:
    caller = _context(uuid.uuid4(), uuid.uuid4())
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(db.sessions, TenantScope.from_access_context(caller)) as session:
            await session.execute(
                sa.text(
                    "INSERT INTO supply_chain.product_dev_cases (id, tenant_id, workspace_id,"
                    " proposal_code, product_name, category, pic_user_id, created_by) VALUES"
                    " (gen_random_uuid(), :t, :w, 'DX-RLS', 'x', 'x', :p, :p)"
                ),
                {"t": uuid.uuid4(), "w": caller.workspace_id, "p": caller.principal_id},
            )


# --- a round's paper and its result ------------------------------------------------


async def _close_round_sql(db: _Db, context: AccessContext, sql: str, **params: object) -> None:
    async with tenant_session(db.sessions, TenantScope.from_access_context(context)) as session:
        await session.execute(sa.text(sql), params)


async def test_a_round_cannot_be_passed_on_another_cases_evaluation(db: _Db) -> None:
    """Past the domain: the composite FK to the case's own documents."""
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _testing(db, context)
    other = await _proposed(db, context)
    foreign = await _document(db, context, other, DocumentType.SAMPLE_EVALUATION)

    with pytest.raises(IntegrityError, match="fk_product_sample_rounds_tenant_id_case_documents"):
        await _close_round_sql(
            db,
            context,
            "UPDATE supply_chain.product_sample_rounds SET result = 'passed',"
            " evaluation_document_id = :d, closed_at = now(), closed_by = :p"
            " WHERE product_dev_case_id = :c",
            d=foreign.id.value,
            p=context.principal_id,
            c=case.id.value,
        )


async def test_a_passed_round_needs_its_evaluation(db: _Db) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _testing(db, context)
    with pytest.raises(IntegrityError, match="ck_product_sample_rounds_passed_on_an_evaluation"):
        await _close_round_sql(
            db,
            context,
            "UPDATE supply_chain.product_sample_rounds SET result = 'passed',"
            " closed_at = now(), closed_by = :p WHERE product_dev_case_id = :c",
            p=context.principal_id,
            c=case.id.value,
        )


async def test_a_round_closes_once_whatever_the_statement_says(db: _Db) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _testing(db, context)
    case.reject_sample(actor_id=context.principal_id, reason="Sai chất liệu")
    await db.cases.save(context, case, audit=_audit(context, case, "reject_sample"))

    with pytest.raises(IntegrityError, match="already closed") as raised:
        await _close_round_sql(
            db,
            context,
            "UPDATE supply_chain.product_sample_rounds SET result = 'needs_revision'"
            " WHERE product_dev_case_id = :c",
            c=case.id.value,
        )
    # The trigger names itself the way a constraint would, for an adapter to map.
    cause = raised.value.orig.__cause__  # type: ignore[union-attr]
    assert getattr(cause, "constraint_name", None) == "ck_product_sample_rounds_closes_once"
    rounds = await db.cases.list_rounds(context, case.id)
    assert [r.result for r in rounds] == [SampleResult.REJECTED]


async def test_one_evaluation_cannot_close_two_rounds(db: _Db) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _testing(db, context)
    evaluation = await _document(db, context, case, DocumentType.SAMPLE_EVALUATION)
    await _close_round_sql(
        db,
        context,
        "UPDATE supply_chain.product_sample_rounds SET result = 'rejected',"
        " evaluation_document_id = :d, closed_at = now(), closed_by = :p"
        " WHERE product_dev_case_id = :c",
        d=evaluation.id.value,
        p=context.principal_id,
        c=case.id.value,
    )
    with pytest.raises(IntegrityError, match="uq_product_sample_rounds_evaluation_document_id"):
        await _close_round_sql(
            db,
            context,
            "INSERT INTO supply_chain.product_sample_rounds (id, tenant_id, workspace_id,"
            " product_dev_case_id, round_no, opened_by, result, evaluation_document_id,"
            " closed_at, closed_by) VALUES (gen_random_uuid(), :t, :w, :c, 2, :p, 'passed',"
            " :d, now(), :p)",
            t=context.tenant_id,
            w=context.workspace_id,
            c=case.id.value,
            p=context.principal_id,
            d=evaluation.id.value,
        )


async def test_a_concurrent_second_close_of_the_same_round_is_a_conflict(db: _Db) -> None:
    """Two copies of the case read at once; the second save meets the version
    guard before it can reach the round."""
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _testing(db, context)
    first, second = await _reloaded(db, context, case), await _reloaded(db, context, case)
    first.reject_sample(actor_id=context.principal_id, reason="a")
    await db.cases.save(context, first, audit=_audit(context, first, "reject_sample"))
    second.reject_sample(actor_id=context.principal_id, reason="b")
    with pytest.raises(ConflictError):
        await db.cases.save(context, second, audit=_audit(context, second, "reject_sample"))


async def test_cancelling_a_case_in_test_closes_its_round_in_the_same_save(db: _Db) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _testing(db, context)
    case.cancel(actor_id=context.principal_id, reason="NCC ngừng hợp tác")
    await db.cases.save(context, case, audit=_audit(context, case, "cancel"))

    (closed,) = await db.cases.list_rounds(context, case.id)
    assert (closed.result, closed.closed_by) == (SampleResult.REJECTED, context.principal_id)
    assert closed.closed_at is not None
    assert (await _reloaded(db, context, case)).state is ProductDevState.CANCELLED


async def test_a_database_refusal_of_a_rejects_evaluation_names_reject_sample(db: _Db) -> None:
    """Past the domain: the evaluation already closed another round. The
    409 names the step being written, not `pass_sample`."""
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _testing(db, context)
    evaluation = await _document(db, context, case, DocumentType.SAMPLE_EVALUATION)
    await _close_round_sql(
        db,
        context,
        "INSERT INTO supply_chain.product_sample_rounds (id, tenant_id, workspace_id,"
        " product_dev_case_id, round_no, opened_by, result, evaluation_document_id,"
        " closed_at, closed_by) VALUES (gen_random_uuid(), :t, :w, :c, 9, :p, 'rejected',"
        " :d, now(), :p)",
        t=context.tenant_id,
        w=context.workspace_id,
        c=case.id.value,
        p=context.principal_id,
        d=evaluation.id.value,
    )
    case.reject_sample(actor_id=context.principal_id, reason="Sai chất liệu", evaluation=evaluation)

    with pytest.raises(ConflictError) as raised:
        await db.cases.save(context, case, audit=_audit(context, case, "reject_sample"))
    assert raised.value.details["action"] == "reject_sample"
    assert raised.value.details["missing_document_type"] == "sample_evaluation"
    assert raised.value.details["constraint"] == "uq_product_sample_rounds_evaluation_document_id"


# --- one owner per fixed set ---------------------------------------------------------


async def _check_values(db: _Db, constraint: str) -> set[str]:
    async with db.migrator.connect() as conn:
        definition = await conn.scalar(
            sa.text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :n"),
            {"n": constraint},
        )
    assert definition, constraint
    return set(re.findall(r"'([^']+)'::text", definition))


async def test_the_state_and_action_checks_are_exactly_the_domains(db: _Db) -> None:
    states = {s.value for s in ProductDevState}
    assert await _check_values(db, "ck_product_dev_cases_state") == states
    assert await _check_values(db, "ck_product_dev_case_state_transitions_to_state") == states
    assert await _check_values(db, "ck_product_dev_case_state_transitions_from_state") == states
    assert await _check_values(db, "ck_product_dev_cases_interrupted_state") == states
    assert await _check_values(db, "ck_product_dev_case_state_transitions_action") == {
        a.value for a in ProductAction
    }
    assert await _check_values(db, "ck_product_sample_rounds_result") == {
        r.value for r in SampleResult
    }


# --- privileges ------------------------------------------------------------------------


async def _granted(db: _Db, table: str) -> set[str]:
    async with db.migrator.connect() as conn:
        return {
            verb
            for verb in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
            if await conn.scalar(
                sa.text("SELECT has_table_privilege('dw_app', :t, :v)"),
                {"t": f"supply_chain.{table}", "v": verb},
            )
        }


async def test_the_application_holds_exactly_the_grants_each_table_needs(db: _Db) -> None:
    """Asked of the catalog. The case keeps DELETE (offboarding's purge
    deletes only what dw_app may, and the case's cascade carries the rest);
    history and revision requests are append-only; a round is INSERTed and
    then closed through its column grant alone."""
    assert await _granted(db, "product_dev_cases") == {"SELECT", "INSERT", "UPDATE", "DELETE"}
    assert await _granted(db, "product_dev_case_state_transitions") == {"SELECT", "INSERT"}
    assert await _granted(db, "sample_revision_requests") == {"SELECT", "INSERT"}
    assert await _granted(db, "product_sample_rounds") == {"SELECT", "INSERT"}
    # Step 9: an item code is issued and corrected (its `code` only), never
    # deleted but by the case's cascade; a SKU is added and removed, never
    # edited.
    assert await _granted(db, "item_codes") == {"SELECT", "INSERT"}
    assert await _granted(db, "skus") == {"SELECT", "INSERT", "DELETE"}
    async with db.migrator.connect() as conn:
        updatable = {
            column
            for column in ("code", "id", "tenant_id", "workspace_id", "product_dev_case_id")
            if await conn.scalar(
                sa.text("SELECT has_column_privilege('dw_app', :t, :c, 'UPDATE')"),
                {"t": "supply_chain.item_codes", "c": column},
            )
        }
    assert updatable == {"code"}


# --- documents of a product case ----------------------------------------------------------


async def test_a_product_document_versions_on_its_own_case_and_lists_by_kind(db: _Db) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _proposed(db, context)
    first = await _document(db, context, case, DocumentType.PRODUCT_IMAGE)
    second = await _document(db, context, case, DocumentType.PRODUCT_IMAGE)

    assert (first.case_kind, first.case_id, second.version) == (CaseKind.PRODUCT, case.id.value, 2)
    assert first.object_key.startswith(
        f"supply_chain/{context.tenant_id}/{context.workspace_id}/product/{case.id}/"
    )
    listed = await db.documents.list_for_case(context, CaseKind.PRODUCT, case.id.value)
    assert [d.version for d in listed] == [2, 1]
    assert await db.documents.list_for_case(context, CaseKind.PO, case.id.value) == []


async def test_a_document_names_exactly_one_case(db: _Db) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _proposed(db, context)
    document = await _document(db, context, case, DocumentType.PRODUCT_IMAGE)
    async with db.migrator.begin() as conn:
        with pytest.raises(IntegrityError, match="ck_case_documents_one_case"):
            await conn.execute(
                sa.text(
                    "UPDATE supply_chain.case_documents SET product_dev_case_id = NULL"
                    " WHERE id = :d"
                ),
                {"d": document.id.value},
            )


async def test_a_product_key_outside_the_rows_own_ids_is_refused(db: _Db) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _proposed(db, context)
    document_id = uuid.uuid4()
    forged = NewCaseDocument(
        id=CaseDocumentId(document_id),
        case_kind=CaseKind.PRODUCT,
        case_id=case.id.value,
        doc_type=DocumentType.PRODUCT_IMAGE,
        # The PO kind's segment for a product case's row.
        object_key=f"supply_chain/{context.tenant_id}/{context.workspace_id}/po/{case.id}/{document_id}",
        filename="a.png",
        content_type="image/png",
        size_bytes=1,
        sha256="0" * 64,
    )
    with pytest.raises(IntegrityError, match="ck_case_documents_object_key"):
        await db.documents.add(
            context,
            forged,
            audit=AuditEvent(
                id=uuid.uuid4(),
                tenant_id=TenantId(context.tenant_id),
                workspace_id=WorkspaceId(context.workspace_id),
                actor_id=UserId(context.principal_id),
                action="supply_chain.document.upload",
                resource_type="case_document",
                resource_id=str(document_id),
                occurred_at=SystemClock().now(),
            ),
        )


async def test_the_orphan_sweep_finds_a_product_documents_key(db: _Db) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _proposed(db, context)
    document = await _document(db, context, case, DocumentType.PRODUCT_IMAGE)
    parsed = ObjectKey.parse(document.object_key)

    assert parsed is not None and parsed.case_kind is CaseKind.PRODUCT
    sweep_context = _context(parsed.tenant_id, parsed.workspace_id)
    assert await db.documents.existing_keys(sweep_context, [document.object_key]) == {
        document.object_key
    }


# --- offboarding -----------------------------------------------------------------------------


async def test_offboarding_purges_product_cases_rounds_and_documents_of_one_tenant(
    db: _Db,
) -> None:
    """Two workspaces of the leaving tenant, each with a case that has a
    history, a passed round on its evaluation, a revision request and
    documents; another tenant with the same. The purge deletes the cases
    (dw_app may) and the cascade takes everything else (dw_app may not
    delete it), across both workspaces, and touches nothing of the other
    tenant's."""
    tenant = uuid.uuid4()
    leaving = [_context(tenant, uuid.uuid4()), _context(tenant, uuid.uuid4())]
    staying = _context(uuid.uuid4(), uuid.uuid4())

    async def full_case(context: AccessContext) -> ProductDevelopmentCase:
        case = await _all_four(db, context)
        case.receive_revised_sample(actor_id=context.principal_id)
        await db.cases.save(context, case, audit=_audit(context, case, "receive_revised_sample"))
        case = await _reloaded(db, context, case)
        evaluation = await _document(db, context, case, DocumentType.SAMPLE_EVALUATION)
        case.pass_sample(actor_id=context.principal_id, evaluation=evaluation)
        await db.cases.save(context, case, audit=_audit(context, case, "pass_sample"))
        # On to item coding, so history rows hold papers too (NO ACTION FKs),
        # and coded: an item code with SKUs (the SKU's NO ACTION FK to it).
        return await _coded(db, context, await _to_item_coding(db, context, case))

    for context in leaving:
        await full_case(context)
    kept = await full_case(staying)

    offboarding = SqlTenantOffboarding(db.sessions)
    exported = {(t.schema, t.table): t.rows for t in await offboarding.export_rows(tenant)}
    assert len(exported[("supply_chain", "product_dev_cases")]) == 2
    assert len(exported[("supply_chain", "product_sample_rounds")]) == 4

    await offboarding.purge_rows(tenant)

    assert len(exported[("supply_chain", "skus")]) == 4
    for table in (*TABLES, "case_documents", "item_codes", "skus"):
        left = await _count(
            db, f"SELECT count(*) FROM supply_chain.{table} WHERE tenant_id = :t", t=tenant
        )
        assert left == 0, table
    still = await _reloaded(db, staying, kept)
    assert still.state is ProductDevState.ITEM_CODING
    assert still.item_code is not None and len(still.skus) == 2
    assert len(await db.cases.list_rounds(staying, kept.id)) == 2
    assert len(await db.documents.list_for_case(staying, CaseKind.PRODUCT, kept.id.value)) == 4


# --- audit and provenance (reviewing-feature-security §5) ----------------------------


async def test_a_refused_step_leaves_no_audit_and_no_history(db: _Db) -> None:
    """One transaction: a step the version guard refuses writes neither its
    history row nor its audit event."""
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _proposed(db, context)
    stale = await _reloaded(db, context, case)
    case.request_sample(actor_id=context.principal_id, supplier_name="NCC")
    await db.cases.save(context, case, audit=_audit(context, case, "request_sample"))
    stale.cancel(actor_id=context.principal_id, reason="x")
    refused_audit = _audit(context, stale, "cancel")

    with pytest.raises(ConflictError):
        await db.cases.save(context, stale, audit=refused_audit)
    assert (
        await _count(
            db, "SELECT count(*) FROM platform.audit_events WHERE id = :i", i=refused_audit.id
        )
        == 0
    )
    assert [t.action for t in await db.cases.list_transitions(context, case.id)] == [
        ProductAction.PROPOSE,
        ProductAction.REQUEST_SAMPLE,
    ]


async def _history_and_audit(
    db: _Db, case: ProductDevelopmentCase, audit_id: uuid.UUID
) -> tuple[int, int]:
    return (
        await _count(
            db,
            "SELECT count(*) FROM supply_chain.product_dev_case_state_transitions"
            " WHERE product_dev_case_id = :c",
            c=case.id.value,
        ),
        await _count(db, "SELECT count(*) FROM platform.audit_events WHERE id = :i", i=audit_id),
    )


async def test_a_step_refused_after_its_history_row_rolls_back_state_history_and_audit(
    db: _Db,
) -> None:
    """The refusal comes from the round's close, AFTER the case UPDATE and the
    history INSERT have run: none of the three survives, so the audit was
    written nowhere but in that transaction."""
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _testing(db, context)
    evaluation = await _document(db, context, case, DocumentType.SAMPLE_EVALUATION)
    case.pass_sample(actor_id=context.principal_id, evaluation=evaluation)
    # Another writer closes the round underneath, without touching the case row.
    await _close_round_sql(
        db,
        context,
        "UPDATE supply_chain.product_sample_rounds SET result = 'rejected',"
        " closed_at = now(), closed_by = :p WHERE product_dev_case_id = :c",
        p=context.principal_id,
        c=case.id.value,
    )
    history_before, _ = await _history_and_audit(db, case, uuid.uuid4())
    refused = _audit(context, case, "pass_sample")

    with pytest.raises(ConflictError, match="already closed"):
        await db.cases.save(context, case, audit=refused)
    stored = await _reloaded(db, context, case)
    assert (stored.state, stored.version) == (ProductDevState.SAMPLE_TESTING, case.version - 1)
    assert await _history_and_audit(db, case, refused.id) == (history_before, 0)


async def test_a_step_whose_audit_is_refused_leaves_no_state_and_no_history(db: _Db) -> None:
    """The other direction: the audit event is the LAST write and the
    database refuses it (its id is taken). The state and the history row
    written before it roll back with it."""
    context = _context(uuid.uuid4(), uuid.uuid4())
    earlier = await _proposed(db, context)
    taken = _audit(context, earlier, "request_sample")
    earlier.request_sample(actor_id=context.principal_id, supplier_name="NCC")
    await db.cases.save(context, earlier, audit=taken)

    case = await _proposed(db, context)
    history_before, _ = await _history_and_audit(db, case, uuid.uuid4())
    case.request_sample(actor_id=context.principal_id, supplier_name="NCC")
    with pytest.raises(IntegrityError):
        await db.cases.save(context, case, audit=replace(taken, resource_id=str(case.id)))
    stored = await _reloaded(db, context, case)
    assert (stored.state, stored.version, stored.supplier_name) == (
        ProductDevState.PROPOSED,
        1,
        None,
    )
    assert (await _history_and_audit(db, case, uuid.uuid4()))[0] == history_before


async def test_a_document_a_round_was_closed_on_cannot_be_deleted_on_its_own(db: _Db) -> None:
    """Even by the migrator: the round's FK keeps the evaluation it passed on.
    Only the case's own cascade takes both."""
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _testing(db, context)
    evaluation = await _document(db, context, case, DocumentType.SAMPLE_EVALUATION)
    case.pass_sample(actor_id=context.principal_id, evaluation=evaluation)
    await db.cases.save(context, case, audit=_audit(context, case, "pass_sample"))

    async with db.migrator.begin() as conn:
        with pytest.raises(
            IntegrityError, match="fk_product_sample_rounds_tenant_id_case_documents"
        ):
            await conn.execute(
                sa.text("DELETE FROM supply_chain.case_documents WHERE id = :d"),
                {"d": evaluation.id.value},
            )


# --- steps 7-8: BM04 and the supplier's confirmation (ticket 03) ----------------------


async def _approved(
    db: _Db, context: AccessContext, case: ProductDevelopmentCase
) -> ProductDevelopmentCase:
    """BGĐ approved, saved as the review graph saves it."""
    case = await _reloaded(db, context, case)
    case.bod_approve(actor_id=uuid.uuid4())
    await db.cases.save(context, case, audit=_audit(context, case, "bod_approve"))
    return await _reloaded(db, context, case)


async def _profiling(db: _Db, context: AccessContext) -> ProductDevelopmentCase:
    case = await _testing(db, context)
    evaluation = await _document(db, context, case, DocumentType.SAMPLE_EVALUATION)
    case.pass_sample(actor_id=context.principal_id, evaluation=evaluation)
    await db.cases.save(context, case, audit=_audit(context, case, "pass_sample"))
    return await _approved(db, context, case)


async def _to_item_coding(
    db: _Db, context: AccessContext, case: ProductDevelopmentCase
) -> ProductDevelopmentCase:
    case = await _approved(db, context, case)
    bm04 = await _document(db, context, case, DocumentType.PRODUCT_PROFILE_BM04)
    case.complete_profile(actor_id=context.principal_id, profile=bm04)
    await db.cases.save(context, case, audit=_audit(context, case, "complete_profile"))
    case = await _reloaded(db, context, case)
    email = await _document(db, context, case, DocumentType.SUPPLIER_CONFIRMATION_EMAIL)
    case.confirm_with_supplier(actor_id=context.principal_id, confirmation=email)
    await db.cases.save(context, case, audit=_audit(context, case, "confirm_with_supplier"))
    return await _reloaded(db, context, case)


async def test_steps_seven_and_eight_are_saved_with_their_paper_on_the_history(db: _Db) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _profiling(db, context)
    assert case.state is ProductDevState.PROFILE_IN_PROGRESS
    approved_at = case.stage_entered_at
    assert approved_at is not None

    bm04 = await _document(db, context, case, DocumentType.PRODUCT_PROFILE_BM04)
    case.complete_profile(actor_id=context.principal_id, profile=bm04)
    await db.cases.save(context, case, audit=_audit(context, case, "complete_profile"))
    case = await _reloaded(db, context, case)
    assert case.state is ProductDevState.SUPPLIER_CONFIRMATION
    assert case.stage_entered_at is not None
    assert case.stage_entered_at > approved_at

    email = await _document(db, context, case, DocumentType.SUPPLIER_CONFIRMATION_EMAIL)
    case.confirm_with_supplier(actor_id=context.principal_id, confirmation=email)
    await db.cases.save(context, case, audit=_audit(context, case, "confirm_with_supplier"))

    assert (await _reloaded(db, context, case)).state is ProductDevState.ITEM_CODING
    history = await db.cases.list_transitions(context, case.id)
    assert [(t.action, t.document_id) for t in history[-3:]] == [
        (ProductAction.BOD_APPROVE, None),
        (ProductAction.COMPLETE_PROFILE, bm04.id.value),
        (ProductAction.CONFIRM_WITH_SUPPLIER, email.id.value),
    ]
    # The round's paper stays on the round, not on the pass's history row.
    assert all(t.document_id is None for t in history[:-2])


async def test_a_bm04_uploaded_before_bgd_approved_is_refused_by_its_upload_time(
    db: _Db,
) -> None:
    """The bound is when the case reached step 7, read from the history: a
    profile drafted while the sample was still in test is not this step's."""
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _testing(db, context)
    early = await _document(db, context, case, DocumentType.PRODUCT_PROFILE_BM04)
    evaluation = await _document(db, context, case, DocumentType.SAMPLE_EVALUATION)
    case.pass_sample(actor_id=context.principal_id, evaluation=evaluation)
    await db.cases.save(context, case, audit=_audit(context, case, "pass_sample"))
    case = await _approved(db, context, case)

    assert case.stage_entered_at is not None
    assert early.uploaded_at < case.stage_entered_at
    with pytest.raises(ConflictError, match="product_profile_bm04"):
        case.complete_profile(actor_id=context.principal_id, profile=early)


async def test_a_bm04_uploaded_before_a_pause_still_counts_after_the_resume(db: _Db) -> None:
    """A resume returns the case to step 7; it does not reach it anew, so the
    bound read back is still BGĐ's approval, not the resume."""
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _profiling(db, context)
    approved_at = case.stage_entered_at
    bm04 = await _document(db, context, case, DocumentType.PRODUCT_PROFILE_BM04)
    case.flag_blocked(actor_id=context.principal_id, reason="Chờ thông số NCC")
    await db.cases.save(context, case, audit=_audit(context, case, "flag_blocked"))
    case = await _reloaded(db, context, case)
    case.resume(actor_id=context.principal_id)
    await db.cases.save(context, case, audit=_audit(context, case, "resume"))
    case = await _reloaded(db, context, case)

    assert case.stage_entered_at == approved_at
    case.complete_profile(actor_id=context.principal_id, profile=bm04)
    await db.cases.save(context, case, audit=_audit(context, case, "complete_profile"))
    assert (await _reloaded(db, context, case)).state is ProductDevState.SUPPLIER_CONFIRMATION


async def _history_sql(db: _Db, context: AccessContext, **params: object) -> None:
    await _close_round_sql(
        db,
        context,
        "INSERT INTO supply_chain.product_dev_case_state_transitions (id, tenant_id,"
        " workspace_id, product_dev_case_id, action, from_state, to_state, actor_id,"
        " document_id) VALUES (gen_random_uuid(), :t, :w, :c, :a, :f, :s, :p, :d)",
        t=context.tenant_id,
        w=context.workspace_id,
        p=context.principal_id,
        **params,
    )


@pytest.mark.parametrize("owner", ["another_case", "another_workspace"])
async def test_a_steps_paper_must_be_a_document_of_its_own_case(db: _Db, owner: str) -> None:
    """Past the domain: the composite FK to the case's own documents. A
    document of another case, or of another workspace's case of the same
    tenant (written there by its own member), cannot be named."""
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _profiling(db, context)
    elsewhere = context if owner == "another_case" else _context(context.tenant_id, uuid.uuid4())
    foreign = await _document(
        db, elsewhere, await _proposed(db, elsewhere), DocumentType.PRODUCT_PROFILE_BM04
    )

    with pytest.raises(
        IntegrityError, match="fk_product_dev_case_state_transitions_tenant_id_case_documents"
    ):
        await _history_sql(
            db,
            context,
            c=case.id.value,
            a="complete_profile",
            f="profile_in_progress",
            s="supplier_confirmation",
            d=foreign.id.value,
        )


@pytest.mark.parametrize(
    ("action", "from_state", "to_state", "with_paper"),
    [
        ("complete_profile", "profile_in_progress", "supplier_confirmation", False),
        ("confirm_with_supplier", "supplier_confirmation", "item_coding", False),
        ("resume", "blocked", "profile_in_progress", True),
    ],
)
async def test_only_steps_seven_and_eight_carry_a_paper_and_both_must(
    db: _Db, action: str, from_state: str, to_state: str, with_paper: bool
) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _profiling(db, context)
    bm04 = await _document(db, context, case, DocumentType.PRODUCT_PROFILE_BM04)
    with pytest.raises(
        IntegrityError, match="ck_product_dev_case_state_transitions_document_steps"
    ):
        await _history_sql(
            db,
            context,
            c=case.id.value,
            a=action,
            f=from_state,
            s=to_state,
            d=bm04.id.value if with_paper else None,
        )


async def test_a_database_refusal_of_a_steps_paper_is_a_409_naming_its_type(db: _Db) -> None:
    """The repository turns the FK's refusal into the same 409 the domain
    gives, by the constraint's name, for the step being written."""
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _profiling(db, context)
    other = await _proposed(db, context)
    foreign = await _document(db, context, other, DocumentType.PRODUCT_PROFILE_BM04)
    # A paper the domain was told is this case's (as a buggy caller might).
    case.complete_profile(
        actor_id=context.principal_id,
        profile=replace(foreign, case_id=case.id.value),
    )

    with pytest.raises(ConflictError) as raised:
        await db.cases.save(context, case, audit=_audit(context, case, "complete_profile"))
    assert raised.value.details["action"] == "complete_profile"
    assert raised.value.details["missing_document_type"] == "product_profile_bm04"
    assert (
        raised.value.details["constraint"]
        == "fk_product_dev_case_state_transitions_tenant_id_case_documents"
    )
    assert (await _reloaded(db, context, case)).state is ProductDevState.PROFILE_IN_PROGRESS


async def test_a_document_a_step_was_taken_on_cannot_be_deleted_on_its_own(db: _Db) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _to_item_coding(db, context, await _testing_passed(db, context))
    bm04 = next(
        t.document_id
        for t in await db.cases.list_transitions(context, case.id)
        if t.action is ProductAction.COMPLETE_PROFILE
    )
    async with db.migrator.begin() as conn:
        with pytest.raises(
            IntegrityError, match="fk_product_dev_case_state_transitions_tenant_id_case_documents"
        ):
            await conn.execute(
                sa.text("DELETE FROM supply_chain.case_documents WHERE id = :d"), {"d": bm04}
            )


async def _testing_passed(db: _Db, context: AccessContext) -> ProductDevelopmentCase:
    case = await _testing(db, context)
    evaluation = await _document(db, context, case, DocumentType.SAMPLE_EVALUATION)
    case.pass_sample(actor_id=context.principal_id, evaluation=evaluation)
    await db.cases.save(context, case, audit=_audit(context, case, "pass_sample"))
    return case


@pytest.mark.parametrize("other", ["tenant", "workspace"])
async def test_another_tenant_or_workspace_cannot_take_steps_seven_or_eight(
    db: _Db, other: str
) -> None:
    """The case reads as absent, and a save under the other context moves
    nothing (RLS and the repository's own filter)."""
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _profiling(db, context)
    bm04 = await _document(db, context, case, DocumentType.PRODUCT_PROFILE_BM04)
    intruder = (
        _context(uuid.uuid4(), context.workspace_id)
        if other == "tenant"
        else _context(context.tenant_id, uuid.uuid4())
    )
    assert await db.cases.get(intruder, case.id) is None
    assert await db.documents.get(intruder, bm04.id) is None

    case.complete_profile(actor_id=intruder.principal_id, profile=bm04)
    with pytest.raises(ConflictError):
        await db.cases.save(intruder, case, audit=_audit(intruder, case, "complete_profile"))
    assert (await _reloaded(db, context, case)).state is ProductDevState.PROFILE_IN_PROGRESS


# --- step 9: item codes and SKUs (ticket 04, ADR 0018) --------------------------------------


async def _coded(
    db: _Db,
    context: AccessContext,
    case: ProductDevelopmentCase,
    *,
    code: str | None = None,
    skus: tuple[str, ...] | None = None,
) -> ProductDevelopmentCase:
    """An item code and its SKUs, each saved as its own step."""
    code = code or f"MH-{uuid.uuid4().hex[:8]}"
    case.issue_item_code(actor_id=context.principal_id, new_id=uuid.uuid4(), code=code)
    await db.cases.save(context, case, audit=_audit(context, case, "issue_item_code"))
    for sku_code in skus if skus is not None else (f"{code}-RED", f"{code}-BLUE"):
        case.add_sku(
            actor_id=context.principal_id, new_id=uuid.uuid4(), sku=SkuDraft(sku_code, "Màu", 50)
        )
        await db.cases.save(context, case, audit=_audit(context, case, "add_sku"))
    return await _reloaded(db, context, case)


async def _coding(db: _Db, context: AccessContext) -> ProductDevelopmentCase:
    return await _to_item_coding(db, context, await _profiling_from_scratch(db, context))


async def _profiling_from_scratch(db: _Db, context: AccessContext) -> ProductDevelopmentCase:
    case = await _testing(db, context)
    evaluation = await _document(db, context, case, DocumentType.SAMPLE_EVALUATION)
    case.pass_sample(actor_id=context.principal_id, evaluation=evaluation)
    await db.cases.save(context, case, audit=_audit(context, case, "pass_sample"))
    return case


async def test_step_nine_is_saved_with_its_history_and_read_back(db: _Db) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _coded(db, context, await _coding(db, context), code="MH-0001")

    red = next(s for s in case.skus if s.sku_code.endswith("RED"))
    case.remove_sku(actor_id=context.principal_id, sku_id=red.id)
    await db.cases.save(context, case, audit=_audit(context, case, "remove_sku"))
    case = await _reloaded(db, context, case)
    case.submit_for_signoff(actor_id=context.principal_id)
    await db.cases.save(context, case, audit=_audit(context, case, "submit_for_signoff"))

    stored = await _reloaded(db, context, case)
    assert (stored.state, stored.signoff_round) == (ProductDevState.PENDING_SIGNOFF, 1)
    assert stored.item_code is not None and stored.item_code.code == "MH-0001"
    assert [(s.sku_code, s.planned_quantity) for s in stored.skus] == [("MH-0001-BLUE", 50)]
    history = await db.cases.list_transitions(context, case.id)
    assert [t.action for t in history[-5:]] == [
        ProductAction.ISSUE_ITEM_CODE,
        ProductAction.ADD_SKU,
        ProductAction.ADD_SKU,
        ProductAction.REMOVE_SKU,
        ProductAction.SUBMIT_FOR_SIGNOFF,
    ]
    page = await db.cases.list_page(
        context,
        page_request(limit=50, cursor=None, query=PageQuery(key="t")),
        ProductCaseListFilter(),
    )
    (listed,) = [c for c in page.items if c.id == case.id]
    assert (listed.item_code, listed.skus) == (stored.item_code, stored.skus)


async def test_coding_does_not_move_when_the_case_reached_item_coding(db: _Db) -> None:
    """Coding steps stay where they are; `stage_entered_at` is when step 8
    moved the case here, not the latest SKU."""
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _coding(db, context)
    entered = case.stage_entered_at
    assert entered is not None
    assert (await _coded(db, context, case)).stage_entered_at == entered


async def test_a_signoff_reject_keeps_the_item_code_and_skus(db: _Db) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _coded(db, context, await _coding(db, context))
    case.submit_for_signoff(actor_id=context.principal_id)
    await db.cases.save(context, case, audit=_audit(context, case, "submit_for_signoff"))
    case.signoff_reject(actor_id=uuid.uuid4(), reason="Giá vốn chưa khớp")
    await db.cases.save(context, case, audit=_audit(context, case, "signoff_reject"))

    stored = await _reloaded(db, context, case)
    assert stored.state is ProductDevState.ITEM_CODING
    assert stored.item_code == case.item_code
    assert stored.skus == case.skus and len(stored.skus) == 2


async def test_correcting_the_item_code_keeps_its_row_and_skus(db: _Db) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _coded(db, context, await _coding(db, context))
    before = case.item_code
    assert before is not None
    case.issue_item_code(actor_id=context.principal_id, new_id=uuid.uuid4(), code="MH-FIXED-1")
    await db.cases.save(context, case, audit=_audit(context, case, "issue_item_code"))

    stored = await _reloaded(db, context, case)
    assert stored.item_code is not None
    assert (stored.item_code.id, stored.item_code.code) == (before.id, "MH-FIXED-1")
    assert len(stored.skus) == 2


async def _race(db: _Db, steps: list[tuple[AccessContext, ProductDevelopmentCase]]) -> list[object]:
    """Each save in its own transaction, at once."""
    return list(
        await asyncio.gather(
            *(
                db.cases.save(context, case, audit=_audit(context, case, "coding"))
                for context, case in steps
            ),
            return_exceptions=True,
        )
    )


async def test_two_transactions_issuing_one_item_code_at_once_one_is_a_409(db: _Db) -> None:
    """ADR 0018: SELECT-before-INSERT would let both see the code free. The
    UNIQUE refuses whichever commits second, by its name, naming the code;
    the loser's step, history and audit roll back."""
    tenant = uuid.uuid4()
    first, second = _context(tenant, uuid.uuid4()), _context(tenant, uuid.uuid4())
    one, two = await _coding(db, first), await _coding(db, second)
    for context, case in ((first, one), (second, two)):
        case.issue_item_code(actor_id=context.principal_id, new_id=uuid.uuid4(), code="MH-RACE")

    results = await _race(db, [(first, one), (second, two)])

    refused = [r for r in results if isinstance(r, ConflictError)]
    assert len(refused) == 1 and results.count(None) == 1, results
    assert refused[0].details == {"constraint": ITEM_CODE_CONSTRAINT, "item_code": "MH-RACE"}
    holders = [
        c
        for c, ctx in ((one, first), (two, second))
        if (await _reloaded(db, ctx, c)).item_code is not None
    ]
    assert len(holders) == 1
    assert (
        await _count(
            db, "SELECT count(*) FROM supply_chain.item_codes WHERE tenant_id = :t", t=tenant
        )
        == 1
    )


async def test_two_transactions_adding_one_sku_code_at_once_one_is_a_409(db: _Db) -> None:
    tenant = uuid.uuid4()
    first, second = _context(tenant, uuid.uuid4()), _context(tenant, uuid.uuid4())
    one = await _coded(db, first, await _coding(db, first), skus=())
    two = await _coded(db, second, await _coding(db, second), skus=())
    for context, case in ((first, one), (second, two)):
        case.add_sku(
            actor_id=context.principal_id, new_id=uuid.uuid4(), sku=SkuDraft("SKU-RACE", "Đỏ")
        )

    results = await _race(db, [(first, one), (second, two)])

    refused = [r for r in results if isinstance(r, ConflictError)]
    assert len(refused) == 1 and results.count(None) == 1, results
    assert refused[0].details == {"constraint": SKU_CODE_CONSTRAINT, "sku_code": "SKU-RACE"}


async def test_another_tenant_may_use_the_same_item_and_sku_codes(db: _Db) -> None:
    alpha, beta = _context(uuid.uuid4(), uuid.uuid4()), _context(uuid.uuid4(), uuid.uuid4())
    await _coded(db, alpha, await _coding(db, alpha), code="MH-SAME", skus=("SKU-SAME",))
    other = await _coded(db, beta, await _coding(db, beta), code="MH-SAME", skus=("SKU-SAME",))
    assert other.item_code is not None and other.item_code.code == "MH-SAME"
    assert [s.sku_code for s in other.skus] == ["SKU-SAME"]


async def _insert_sku(
    db: _Db,
    context: AccessContext,
    case: ProductDevelopmentCase,
    item_code_id: object,
    *,
    as_caller: AccessContext | None = None,
) -> None:
    """A SKU row naming `context`'s tenant and workspace, written in
    `as_caller`'s session (the same context unless given)."""
    scope = TenantScope.from_access_context(as_caller or context)
    async with tenant_session(db.sessions, scope) as session:
        await session.execute(
            sa.text(
                "INSERT INTO supply_chain.skus (id, tenant_id, workspace_id, product_dev_case_id,"
                " item_code_id, sku_code, variant_label, added_by) VALUES (gen_random_uuid(),"
                " :t, :w, :c, :i, :s, 'x', :p)"
            ),
            {
                "t": context.tenant_id,
                "w": context.workspace_id,
                "c": case.id.value,
                "i": item_code_id,
                "s": f"SKU-{uuid.uuid4().hex[:6]}",
                "p": (as_caller or context).principal_id,
            },
        )


async def test_a_sku_written_directly_needs_an_item_code_of_its_own_case(db: _Db) -> None:
    """Past the aggregate: NOT NULL, and the composite FK to the item code of
    the SAME case, workspace and tenant."""
    context = _context(uuid.uuid4(), uuid.uuid4())
    uncoded = await _coding(db, context)
    coded = await _coded(db, context, await _coding(db, context))
    assert coded.item_code is not None

    with pytest.raises(IntegrityError, match=r"not-null|null value"):
        await _insert_sku(db, context, uncoded, None)
    with pytest.raises(IntegrityError, match="fk_skus_tenant_id_item_codes"):
        await _insert_sku(db, context, uncoded, uuid.uuid4())
    with pytest.raises(IntegrityError, match="fk_skus_tenant_id_item_codes"):
        await _insert_sku(db, context, uncoded, coded.item_code.id)
    assert (await _reloaded(db, context, uncoded)).skus == ()


@pytest.mark.parametrize(
    "check",
    [
        ("code", "' MH-1'"),
        ("code", "''"),
    ],
)
async def test_an_item_code_is_stored_trimmed_and_non_blank(
    db: _Db, check: tuple[str, str]
) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _coding(db, context)
    with pytest.raises(IntegrityError, match="ck_item_codes_code"):
        async with tenant_session(db.sessions, TenantScope.from_access_context(context)) as s:
            await s.execute(
                sa.text(
                    "INSERT INTO supply_chain.item_codes (id, tenant_id, workspace_id,"
                    f" product_dev_case_id, code, issued_by) VALUES (gen_random_uuid(), :t, :w,"
                    f" :c, {check[1]}, :p)"
                ),
                {
                    "t": context.tenant_id,
                    "w": context.workspace_id,
                    "c": case.id.value,
                    "p": context.principal_id,
                },
            )


async def test_an_item_code_with_skus_cannot_be_deleted(db: _Db) -> None:
    """Even by the migrator: NO ACTION refuses it as RESTRICT would. Only the
    case's own cascade takes both (the offboarding test)."""
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _coded(db, context, await _coding(db, context))
    assert case.item_code is not None
    async with db.migrator.begin() as conn:
        with pytest.raises(IntegrityError, match="fk_skus_tenant_id_item_codes"):
            await conn.execute(
                sa.text("DELETE FROM supply_chain.item_codes WHERE id = :i"),
                {"i": case.item_code.id},
            )


@pytest.mark.parametrize("other", ["tenant", "workspace"])
async def test_another_tenant_or_workspace_reads_no_item_code_or_sku(db: _Db, other: str) -> None:
    owner = _context(uuid.uuid4(), uuid.uuid4())
    case = await _coded(db, owner, await _coding(db, owner))
    caller = _context(uuid.uuid4() if other == "tenant" else owner.tenant_id, uuid.uuid4())

    async with tenant_session(db.sessions, TenantScope.from_access_context(caller)) as session:
        for table in ("item_codes", "skus"):
            seen = await session.scalar(
                sa.text(
                    f"SELECT count(*) FROM supply_chain.{table} WHERE product_dev_case_id = :c"
                ),
                {"c": case.id.value},
            )
            assert seen == 0, table
    async with db.sessions() as session:
        for table in ("item_codes", "skus"):
            assert await session.scalar(sa.text(f"SELECT count(*) FROM supply_chain.{table}")) == 0
    assert await db.cases.get(caller, case.id) is None


@pytest.mark.parametrize("other", ["tenant", "workspace"])
async def test_another_tenant_or_workspace_cannot_write_or_remove_the_owners_codes(
    db: _Db, other: str
) -> None:
    """Rows naming the OWNER's tenant, workspace, case and item code: the FKs
    accept them (referential checks do not pass through RLS), so the policy's
    WITH CHECK refuses them; a raw DELETE or UPDATE by id reaches nothing."""
    owner = _context(uuid.uuid4(), uuid.uuid4())
    case = await _coded(db, owner, await _coding(db, owner))
    assert case.item_code is not None
    caller = _context(uuid.uuid4() if other == "tenant" else owner.tenant_id, uuid.uuid4())

    with pytest.raises(DBAPIError, match="row-level security"):
        await _insert_sku(db, owner, case, case.item_code.id, as_caller=caller)
    async with tenant_session(db.sessions, TenantScope.from_access_context(caller)) as session:
        removed = await session.execute(
            sa.text("DELETE FROM supply_chain.skus WHERE product_dev_case_id = :c"),
            {"c": case.id.value},
        )
        renamed = await session.execute(
            sa.text("UPDATE supply_chain.item_codes SET code = 'MH-STOLEN' WHERE id = :i"),
            {"i": case.item_code.id},
        )
        rowcounts = (removed.rowcount, renamed.rowcount)  # type: ignore[attr-defined]
    assert rowcounts == (0, 0)
    stored = await _reloaded(db, owner, case)
    assert stored.item_code == case.item_code and len(stored.skus) == 2
