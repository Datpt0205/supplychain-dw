"""Integration: the case-document bucket adapter and the orphan sweep, on the
local S3 server and the real database.

The handlers' and the sweep's unit tests use fakes; this asks the real server
whether the adapter keeps their promises (a missing bucket is created on the
first write, a missing key reads as NotFoundError, a listing carries each
object's last-modified time and pages in key order) and runs the sweep over
real objects and real rows. S3 cannot back-date an object, so "over a day old"
is reached by running the sweep with a clock two days ahead.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import pytest
from minio import Minio
from runtime_harness import runtime_urls
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls

from dw_kernel.errors import NotFoundError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import FixedClock
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence.case_document_repository import (
    SqlCaseDocumentRepository,
)
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.adapters.storage.minio_case_documents import MinioCaseDocumentStorage
from dw_supply_chain.application.document_orphan_sweep import SweepOrphanDocuments
from dw_supply_chain.application.ports import NewCaseDocument
from dw_supply_chain.domain.case_document import (
    CaseDocumentId,
    CaseKind,
    DocumentType,
    ObjectKey,
)
from dw_supply_chain.domain.po_case import POCase, POCaseId

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.7\n" + b"1" * 32


@pytest.fixture(scope="module")
def client() -> Minio:
    urls = runtime_urls()
    return Minio(
        urls.minio_endpoint,
        access_key=urls.minio_access_key,
        secret_key=urls.minio_secret_key,
        secure=False,
    )


@pytest.fixture
def storage(client: Minio) -> Iterator[MinioCaseDocumentStorage]:
    """A bucket of its own that does not exist yet, removed afterwards."""
    bucket = f"dw-case-docs-{uuid.uuid4().hex[:12]}"
    yield MinioCaseDocumentStorage(client=client, bucket=bucket)
    if client.bucket_exists(bucket):
        for obj in client.list_objects(bucket, recursive=True):
            client.remove_object(bucket, obj.object_name)
        client.remove_bucket(bucket)


@pytest.fixture
async def sessions(db_urls: DatabaseUrls) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _key(tenant: uuid.UUID, workspace: uuid.UUID, case: uuid.UUID, document: uuid.UUID) -> str:
    return ObjectKey.build(
        tenant_id=tenant,
        workspace_id=workspace,
        case_kind=CaseKind.PO,
        case_id=case,
        document_id=document,
    ).value


async def test_a_write_creates_the_bucket_and_the_bytes_round_trip(
    storage: MinioCaseDocumentStorage,
) -> None:
    key = _key(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

    await storage.put(key, PDF, "application/pdf")

    assert await storage.get(key) == PDF


async def test_a_missing_key_and_a_missing_bucket_read_as_not_found(
    storage: MinioCaseDocumentStorage,
) -> None:
    with pytest.raises(NotFoundError):
        await storage.get("supply_chain/missing")  # the bucket does not exist yet
    await storage.put("supply_chain/present", PDF, "application/pdf")
    with pytest.raises(NotFoundError):
        await storage.get("supply_chain/missing")


async def test_a_listing_pages_in_key_order_with_last_modified(
    storage: MinioCaseDocumentStorage,
) -> None:
    assert await storage.list_after("supply_chain/", start_after=None, limit=10) == []
    keys = sorted(f"supply_chain/{uuid.uuid4()}/x" for _ in range(3))
    for key in [*keys, "feedback/elsewhere"]:
        await storage.put(key, PDF, "application/pdf")

    first = await storage.list_after("supply_chain/", start_after=None, limit=2)
    rest = await storage.list_after("supply_chain/", start_after=first[-1].key, limit=2)

    assert [o.key for o in first] == keys[:2]
    assert [o.key for o in rest] == keys[2:]
    now = datetime.now(UTC)
    assert all(
        now - timedelta(minutes=5) < o.last_modified <= now + timedelta(minutes=5) for o in first
    )


async def test_a_delete_removes_the_object(storage: MinioCaseDocumentStorage) -> None:
    await storage.put("supply_chain/x", PDF, "application/pdf")
    await storage.delete("supply_chain/x")
    with pytest.raises(NotFoundError):
        await storage.get("supply_chain/x")


async def test_the_sweep_deletes_only_old_keys_no_row_holds(
    storage: MinioCaseDocumentStorage, sessions: async_sessionmaker[AsyncSession]
) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    context = AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset(),
        plan_id="professional",
    )
    case = POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(tenant),
        workspace_id=WorkspaceId(workspace),
        po_reference=f"PO-SWEEP-{uuid.uuid4().hex[:6]}",
        supplier_name="NCC",
    )
    await SqlPOCaseRepository(sessions).add(context, case)
    document_id = CaseDocumentId(uuid.uuid4())
    held = _key(tenant, workspace, case.id.value, document_id.value)
    await SqlCaseDocumentRepository(sessions).add(
        context,
        NewCaseDocument(
            id=document_id,
            case_id=case.id.value,
            doc_type=DocumentType.PURCHASE_ORDER,
            object_key=held,
            filename="PO.pdf",
            content_type="application/pdf",
            size_bytes=len(PDF),
            sha256="a" * 64,
        ),
        audit=AuditEvent(
            id=uuid.uuid4(),
            tenant_id=TenantId(tenant),
            workspace_id=WorkspaceId(workspace),
            actor_id=UserId(context.principal_id),
            action="supply_chain.document.upload",
            resource_type="case_document",
            resource_id=str(document_id),
            occurred_at=datetime.now(UTC),
        ),
    )
    orphan = _key(tenant, workspace, case.id.value, uuid.uuid4())
    malformed = f"supply_chain/{tenant}/not-a-workspace/po/{case.id}/{uuid.uuid4()}"
    for key in (held, orphan, malformed):
        await storage.put(key, PDF, "application/pdf")
    keys = SqlCaseDocumentRepository(sessions)

    # Today: every object is fresh, so nothing goes.
    await SweepOrphanDocuments(
        objects=storage, keys=keys, clock=FixedClock(datetime.now(UTC))
    ).prune()
    left = {o.key for o in await storage.list_after("supply_chain/", start_after=None, limit=10)}
    assert left == {held, orphan, malformed}

    # Two days on: the orphan goes, the held key and the unreadable one stay.
    await SweepOrphanDocuments(
        objects=storage, keys=keys, clock=FixedClock(datetime.now(UTC) + timedelta(days=2))
    ).prune()
    left = {o.key for o in await storage.list_after("supply_chain/", start_after=None, limit=10)}
    assert left == {held, malformed}
