"""Integration: case documents on the real database (slice D, ADR 0021).

What only Postgres can prove: the table's RLS narrows by tenant AND workspace,
the composite FK keeps a document in its case's workspace, the CHECKs agree
with the domain's lists, `dw_app` may only read and insert, two concurrent
uploads never share a version, and offboarding exports and purges a tenant's
documents in every workspace while leaving another tenant's alone.

The bucket is an in-memory fake here; `test_case_document_storage.py` runs the
adapter against the real local S3.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

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

from dw_kernel.errors import ConflictError, NotFoundError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.adapters.persistence.offboarding import SqlTenantOffboarding
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence.case_document_repository import (
    SqlCaseDocumentRepository,
)
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.adapters.persistence.product_case_repository import (
    SqlProductCaseRepository,
)
from dw_supply_chain.application.case_documents import UploadCaseDocument
from dw_supply_chain.application.handlers import DOCUMENT_READ, DOCUMENT_WRITE
from dw_supply_chain.application.ports import NewCaseDocument
from dw_supply_chain.domain.case_document import (
    ALLOWED_CONTENT_TYPES,
    CaseDocument,
    CaseDocumentId,
    CaseKind,
    DocumentType,
    ObjectKey,
)
from dw_supply_chain.domain.po_case import POCase, POCaseId

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.7\n" + b"0" * 64
SHA = "0" * 64
TABLE = "supply_chain.case_documents"


@dataclass(frozen=True)
class _Db:
    sessions: async_sessionmaker[AsyncSession]
    migrator: AsyncEngine


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
        scopes=frozenset({DOCUMENT_READ, DOCUMENT_WRITE}),
        plan_id="professional",
    )


async def _case(db: _Db, context: AccessContext) -> POCase:
    case = POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        po_reference=f"PO-DOC-{uuid.uuid4().hex[:8]}",
        supplier_name="NCC Tài liệu",
    )
    await SqlPOCaseRepository(db.sessions).add(context, case)
    return case


def _new(
    context: AccessContext, case: POCase, doc_type: DocumentType = DocumentType.PURCHASE_ORDER
) -> NewCaseDocument:
    document_id = CaseDocumentId(uuid.uuid4())
    return NewCaseDocument(
        id=document_id,
        case_kind=CaseKind.PO,
        case_id=case.id.value,
        doc_type=doc_type,
        object_key=ObjectKey.build(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            case_kind=CaseKind.PO,
            case_id=case.id.value,
            document_id=document_id.value,
        ).value,
        filename="PO.pdf",
        content_type="application/pdf",
        size_bytes=len(PDF),
        sha256=SHA,
    )


def _audit(context: AccessContext, document: NewCaseDocument) -> AuditEvent:
    return AuditEvent(
        id=uuid.uuid4(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        actor_id=UserId(context.principal_id),
        action="supply_chain.document.upload",
        resource_type="case_document",
        resource_id=str(document.id),
        occurred_at=SystemClock().now(),
    )


async def _add(
    db: _Db,
    context: AccessContext,
    case: POCase,
    doc_type: DocumentType = DocumentType.PURCHASE_ORDER,
) -> CaseDocument:
    new = _new(context, case, doc_type)
    return await SqlCaseDocumentRepository(db.sessions).add(
        context, new, audit=_audit(context, new)
    )


async def _all_rows(db: _Db, case: POCase) -> list[sa.Row[tuple[object, ...]]]:
    """Through the migrator, which bypasses RLS: what is really in the table."""
    async with db.migrator.connect() as conn:
        return list(
            (
                await conn.execute(
                    sa.text(f"SELECT doc_type, version FROM {TABLE} WHERE po_case_id = :c"),
                    {"c": case.id.value},
                )
            ).all()
        )


# --- RLS -----------------------------------------------------------------------


async def test_another_tenant_reads_none_of_the_documents(db: _Db) -> None:
    mine = _context(uuid.uuid4(), uuid.uuid4())
    theirs = _context(uuid.uuid4(), uuid.uuid4())
    case = await _case(db, mine)
    document = await _add(db, mine, case)
    repo = SqlCaseDocumentRepository(db.sessions)

    assert await repo.get(theirs, document.id) is None
    assert await repo.list_for_case(theirs, CaseKind.PO, case.id.value) == []
    async with tenant_session(db.sessions, TenantScope.from_access_context(theirs)) as session:
        seen = await session.scalar(
            sa.text(f"SELECT count(*) FROM {TABLE} WHERE id = :id"), {"id": document.id.value}
        )
    assert seen == 0
    assert (await repo.get(mine, document.id)) == document


async def test_another_workspace_of_the_same_tenant_reads_none(db: _Db) -> None:
    tenant = uuid.uuid4()
    mine = _context(tenant, uuid.uuid4())
    other_workspace = _context(tenant, uuid.uuid4())
    case = await _case(db, mine)
    document = await _add(db, mine, case)
    repo = SqlCaseDocumentRepository(db.sessions)

    assert await repo.get(other_workspace, document.id) is None
    assert await repo.list_for_case(other_workspace, CaseKind.PO, case.id.value) == []
    assert await repo.existing_keys(other_workspace, [document.object_key]) == set()
    async with tenant_session(
        db.sessions, TenantScope.from_access_context(other_workspace)
    ) as session:
        seen = await session.scalar(sa.text(f"SELECT count(*) FROM {TABLE}"))
    assert seen == 0
    assert await repo.existing_keys(mine, [document.object_key]) == {document.object_key}


async def test_the_repository_filters_by_workspace_even_where_rls_does_not_apply(
    db: _Db,
) -> None:
    """The second layer on its own: on the migrator's connection, which
    bypasses RLS, the repository's own tenant and workspace filter is all that
    is left, and it still returns nothing for another workspace."""
    tenant = uuid.uuid4()
    mine, other_workspace = _context(tenant, uuid.uuid4()), _context(tenant, uuid.uuid4())
    case = await _case(db, mine)
    document = await _add(db, mine, case)
    unprotected = SqlCaseDocumentRepository(async_sessionmaker(db.migrator, expire_on_commit=False))

    assert await unprotected.get(other_workspace, document.id) is None
    assert await unprotected.list_for_case(other_workspace, CaseKind.PO, case.id.value) == []
    assert await unprotected.existing_keys(other_workspace, [document.object_key]) == set()
    assert await unprotected.get(mine, document.id) == document


async def test_a_connection_that_never_scopes_itself_reads_nothing(db: _Db) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    await _add(db, context, await _case(db, context))
    async with db.sessions() as session:
        assert await session.scalar(sa.text(f"SELECT count(*) FROM {TABLE}")) == 0


async def test_a_document_cannot_be_written_into_another_workspace_than_its_case(
    db: _Db,
) -> None:
    """RLS lets the row through (its own workspace is bound); the composite FK
    to `po_cases (tenant_id, workspace_id, id)` refuses it."""
    tenant = uuid.uuid4()
    case_owner = _context(tenant, uuid.uuid4())
    elsewhere = _context(tenant, uuid.uuid4())
    case = await _case(db, case_owner)
    new = _new(elsewhere, case)

    with pytest.raises(IntegrityError, match="fk_case_documents_tenant_id_po_cases"):
        await SqlCaseDocumentRepository(db.sessions).add(
            elsewhere, new, audit=_audit(elsewhere, new)
        )
    assert await _all_rows(db, case) == []


async def test_a_key_outside_the_rows_own_tenant_and_workspace_is_refused(db: _Db) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _case(db, context)
    new = _new(context, case)
    forged = NewCaseDocument(
        **{
            **{f: getattr(new, f) for f in new.__slots__},
            "object_key": new.object_key.replace(str(context.tenant_id), str(uuid.uuid4())),
        }
    )

    with pytest.raises(IntegrityError, match="ck_case_documents_object_key"):
        await SqlCaseDocumentRepository(db.sessions).add(
            context, forged, audit=_audit(context, forged)
        )


# --- one owner per fixed set ---------------------------------------------------


async def _check_values(db: _Db, constraint: str) -> set[str]:
    async with db.migrator.connect() as conn:
        definition = await conn.scalar(
            sa.text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :n"),
            {"n": constraint},
        )
    assert definition, constraint
    return set(re.findall(r"'([^']+)'::text", definition))


async def test_the_doc_type_check_is_exactly_the_domains_list(db: _Db) -> None:
    assert await _check_values(db, "ck_case_documents_doc_type") == {t.value for t in DocumentType}


async def test_the_content_type_check_is_exactly_what_an_upload_accepts(db: _Db) -> None:
    assert await _check_values(db, "ck_case_documents_content_type") == set(ALLOWED_CONTENT_TYPES)


# --- privileges ----------------------------------------------------------------


async def test_the_application_may_only_read_and_add_documents(db: _Db) -> None:
    """No edit, no delete by hand: the cascade from po_cases is the only way a
    row leaves. Asked of the catalog."""
    async with db.migrator.connect() as conn:
        granted = {
            verb
            for verb in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
            if await conn.scalar(
                sa.text("SELECT has_table_privilege('dw_app', :t, :v)"), {"t": TABLE, "v": verb}
            )
        }
    assert granted == {"SELECT", "INSERT"}

    context = _context(uuid.uuid4(), uuid.uuid4())
    await _add(db, context, await _case(db, context))
    for statement in (f"UPDATE {TABLE} SET filename = 'x'", f"DELETE FROM {TABLE}"):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with tenant_session(
                db.sessions, TenantScope.from_access_context(context)
            ) as session:
                await session.execute(sa.text(statement))


# --- versions ------------------------------------------------------------------


async def test_versions_count_per_case_and_type_and_the_audit_commits_with_the_row(
    db: _Db,
) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _case(db, context)

    first = await _add(db, context, case)
    second = await _add(db, context, case)
    deposit = await _add(db, context, case, DocumentType.DEPOSIT_DOCS)

    assert (first.version, second.version, deposit.version) == (1, 2, 1)
    listed = await SqlCaseDocumentRepository(db.sessions).list_for_case(
        context, CaseKind.PO, case.id.value
    )
    assert [(d.doc_type, d.version) for d in listed] == [
        (DocumentType.DEPOSIT_DOCS, 1),
        (DocumentType.PURCHASE_ORDER, 2),
        (DocumentType.PURCHASE_ORDER, 1),
    ]
    async with db.migrator.connect() as conn:
        audited = await conn.scalar(
            sa.text(
                "SELECT count(*) FROM platform.audit_events"
                " WHERE action = 'supply_chain.document.upload' AND tenant_id = :t"
            ),
            {"t": context.tenant_id},
        )
    assert audited == 3


async def _audited(db: _Db, document: NewCaseDocument) -> int:
    async with db.migrator.connect() as conn:
        found = await conn.scalar(
            sa.text(
                "SELECT count(*) FROM platform.audit_events"
                " WHERE action = 'supply_chain.document.upload' AND resource_id = :r"
            ),
            {"r": str(document.id)},
        )
    return int(found or 0)


async def test_a_refused_row_leaves_no_audit_and_a_refused_audit_leaves_no_row(
    db: _Db,
) -> None:
    """One transaction, both ways round. An audit committed on its own before
    the insert would survive the insert's refusal; a row committed before an
    audit written on its own would survive the audit's."""
    tenant = uuid.uuid4()
    context = _context(tenant, uuid.uuid4())
    case = await _case(db, context)
    repo = SqlCaseDocumentRepository(db.sessions)

    # The insert refused (the case is in another workspace): no audit either.
    elsewhere = _context(tenant, uuid.uuid4())
    refused_row = _new(elsewhere, case)
    with pytest.raises(IntegrityError):
        await repo.add(elsewhere, refused_row, audit=_audit(elsewhere, refused_row))
    assert await _audited(db, refused_row) == 0

    # The audit refused (it names another tenant, which RLS will not let this
    # session write): no row either.
    refused_audit = _new(context, case)
    foreign_audit = _audit(_context(uuid.uuid4(), context.workspace_id), refused_audit)
    with pytest.raises(DBAPIError, match="row-level security"):
        await repo.add(context, refused_audit, audit=foreign_audit)
    assert await _all_rows(db, case) == []


async def test_two_concurrent_uploads_never_share_a_version(db: _Db) -> None:
    """Two real transactions, released at the same moment.

    A migrator connection holds an EXCLUSIVE lock on the table while both
    inserts queue behind it; when it lets go, both compute `max + 1` from the
    same state. The partial UNIQUE is all that stands between them: either
    they land as two versions or one gets a 409, and never two rows with one
    version. Several rounds, so a lucky ordering cannot pass for the guard."""
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _case(db, context)
    repo = SqlCaseDocumentRepository(db.sessions)
    conflicts = 0

    for _ in range(5):
        async with db.migrator.begin() as lock:
            await lock.execute(sa.text(f"LOCK TABLE {TABLE} IN EXCLUSIVE MODE"))
            news = [_new(context, case), _new(context, case)]
            racing = [
                asyncio.create_task(repo.add(context, n, audit=_audit(context, n))) for n in news
            ]
            await _wait_until_blocked(db, waiting=2)
        outcomes = await asyncio.gather(*racing, return_exceptions=True)
        for outcome in outcomes:
            assert isinstance(outcome, CaseDocument | ConflictError), outcome
        conflicts += sum(isinstance(o, ConflictError) for o in outcomes)

    versions = [row.version for row in await _all_rows(db, case)]
    assert len(versions) == len(set(versions)), f"two rows share a version: {sorted(versions)}"
    assert len(versions) + conflicts == 10


async def _wait_until_blocked(db: _Db, *, waiting: int) -> None:
    async with db.migrator.connect() as conn:
        for _ in range(200):
            blocked = await conn.scalar(
                sa.text(
                    "SELECT count(*) FROM pg_locks"
                    " WHERE relation = CAST(:t AS regclass) AND NOT granted"
                ),
                {"t": TABLE},
            )
            if blocked >= waiting:
                return
            await asyncio.sleep(0.02)
    raise AssertionError("the two inserts never queued behind the lock")


# --- the upload handler on the real repositories -------------------------------


@dataclass
class _MemoryBucket:
    objects: dict[str, bytes] = field(default_factory=dict)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = data

    async def get(self, key: str) -> bytes:
        return self.objects[key]

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)


def _upload(db: _Db, bucket: _MemoryBucket) -> UploadCaseDocument:
    return UploadCaseDocument(
        cases={
            CaseKind.PO: SqlPOCaseRepository(db.sessions),
            CaseKind.PRODUCT: SqlProductCaseRepository(db.sessions),
        },
        documents=SqlCaseDocumentRepository(db.sessions),
        storage=bucket,
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
        clock=SystemClock(),
        max_bytes=1024,
    )


@pytest.mark.parametrize("other", ["tenant", "workspace"])
async def test_uploading_to_another_tenants_or_workspaces_case_writes_nothing(
    db: _Db, other: str
) -> None:
    owner = _context(uuid.uuid4(), uuid.uuid4())
    case = await _case(db, owner)
    caller = _context(uuid.uuid4() if other == "tenant" else owner.tenant_id, uuid.uuid4())
    bucket = _MemoryBucket()

    with pytest.raises(NotFoundError):
        await _upload(db, bucket).handle(
            caller,
            CaseKind.PO,
            case.id.value,
            doc_type=DocumentType.PURCHASE_ORDER,
            filename="PO.pdf",
            declared_content_type="application/pdf",
            data=PDF,
        )
    assert bucket.objects == {}
    assert await _all_rows(db, case) == []


async def test_an_upload_lands_under_the_callers_prefix(db: _Db) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _case(db, context)
    bucket = _MemoryBucket()

    document = await _upload(db, bucket).handle(
        context,
        CaseKind.PO,
        case.id.value,
        doc_type=DocumentType.PURCHASE_ORDER,
        filename="../../x/PO.pdf",
        declared_content_type="application/pdf",
        data=PDF,
    )

    prefix = f"supply_chain/{context.tenant_id}/{context.workspace_id}/po/{case.id}/"
    assert list(bucket.objects) == [document.object_key]
    assert document.object_key == f"{prefix}{document.id}"


# --- offboarding ---------------------------------------------------------------


async def test_offboarding_exports_every_workspace_and_purges_only_that_tenant(
    db: _Db,
) -> None:
    """The purge deletes `po_cases` (dw_app may), which cascades the
    documents (dw_app may not delete them); another tenant keeps its own."""
    tenant = uuid.uuid4()
    first, second = _context(tenant, uuid.uuid4()), _context(tenant, uuid.uuid4())
    other = _context(uuid.uuid4(), uuid.uuid4())
    kept_case = await _case(db, other)
    kept = await _add(db, other, kept_case)
    gone = [await _add(db, ctx, await _case(db, ctx)) for ctx in (first, second)]

    offboarding = SqlTenantOffboarding(db.sessions)
    exported = await offboarding.export_rows(tenant)
    (table,) = [t for t in exported if (t.schema, t.table) == ("supply_chain", "case_documents")]
    assert {row["id"] for row in table.rows} == {d.id.value for d in gone}

    await offboarding.purge_rows(tenant)

    async with db.migrator.connect() as conn:
        left = set(
            await conn.scalars(
                sa.text(f"SELECT id FROM {TABLE} WHERE tenant_id IN (:a, :b)"),
                {"a": tenant, "b": other.tenant_id},
            )
        )
    assert left == {kept.id.value}


async def test_the_policy_is_the_one_workspace_shape_on_both_sides(db: _Db) -> None:
    """`test_rls_coverage.py` holds every workspace-narrowed policy to
    `tenant AND (workspace OR scope)`; this pins that the table IS narrowed by
    workspace, so that rule is not passing vacuously over it."""
    async with db.migrator.connect() as conn:
        policy = (
            await conn.execute(
                sa.text(
                    "SELECT policyname, qual, with_check FROM pg_policies"
                    " WHERE schemaname = 'supply_chain' AND tablename = 'case_documents'"
                )
            )
        ).one()
        forced = await conn.scalar(
            sa.text(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class"
                " WHERE oid = CAST(:t AS regclass)"
            ),
            {"t": TABLE},
        )
    assert forced is True
    assert policy.policyname == "tenant_isolation_case_documents"
    assert policy.qual == policy.with_check
    for setting in ("app.tenant_id", "app.workspace_id", "app.workspace_scope"):
        assert f"current_setting('{setting}'" in policy.qual, setting
