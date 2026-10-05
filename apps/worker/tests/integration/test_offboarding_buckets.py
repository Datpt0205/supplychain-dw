"""Integration: the offboarding lane against the local S3 server.

The lane's unit test proves its orchestration with fake buckets. This proves the
prefixes against real ones: tenant A's feedback attachments and case documents
are exported into the bundle and deleted, and tenant B's objects in the same
buckets are untouched, as is a key whose first segment merely starts with A's
id (only a prefix without its trailing slash would take it). The
Postgres half (`SqlTenantOffboarding`) is faked here; it has its own
integration tests in dw_platform and dw_supply_chain.
"""

from __future__ import annotations

import asyncio
import uuid
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

import pytest
from minio import Minio
from runtime_harness import runtime_urls

from dw_knowledge.adapters.minio_storage import MinioObjectStorageAdapter
from dw_worker.consumers.offboarding import TenantOffboardingLane

pytestmark = pytest.mark.integration

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()
# A's id followed by more characters in the same segment: `supply_chain/{A}`
# without the slash is a prefix of this, `supply_chain/{A}/` is not.
LOOKALIKE = f"{TENANT_A}x"


@dataclass
class _Store:
    calls: list[tuple[Any, ...]] = field(default_factory=list)

    async def claim_requested(self) -> list[uuid.UUID]:
        return []

    async def export_rows(self, tenant_id: uuid.UUID) -> list[Any]:
        return []

    async def purge_rows(self, tenant_id: uuid.UUID) -> None:
        self.calls.append(("purge_rows", tenant_id))

    async def mark_status(
        self,
        tenant_id: uuid.UUID,
        status: str,
        *,
        export_key: str | None = None,
        error: str | None = None,
    ) -> None:
        self.calls.append(("mark_status", status))


@dataclass
class _Vectors:
    async def delete_by_tenant(self, tenant_id: uuid.UUID) -> None:
        return None


@dataclass(frozen=True)
class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


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
def buckets(client: Minio) -> Iterator[dict[str, MinioObjectStorageAdapter]]:
    made = {
        role: MinioObjectStorageAdapter(
            client=client, bucket=f"dw-off-{role}-{uuid.uuid4().hex[:10]}"
        )
        for role in ("artifacts", "exports", "feedback", "documents")
    }
    # Every bucket exists before the lane runs, as compose's s3-setup makes
    # them: offboarding lists each one for every tenant.
    for adapter in made.values():
        client.make_bucket(adapter.bucket)
    yield made
    for adapter in made.values():
        if client.bucket_exists(adapter.bucket):
            for obj in client.list_objects(adapter.bucket, recursive=True):
                client.remove_object(adapter.bucket, obj.object_name)
            client.remove_bucket(adapter.bucket)


def _keys(adapter: MinioObjectStorageAdapter) -> set[str]:
    return set(asyncio.run(adapter.list_objects("")))


def test_offboarding_empties_tenant_a_in_both_buckets_and_keeps_tenant_b(
    buckets: dict[str, MinioObjectStorageAdapter],
) -> None:
    feedback, documents = buckets["feedback"], buckets["documents"]
    a_feedback = f"feedback/{TENANT_A}/ws/fb/att"
    b_feedback = f"feedback/{TENANT_B}/ws/fb/att"
    a_docs = [
        f"supply_chain/{TENANT_A}/{uuid.uuid4()}/po/{uuid.uuid4()}/{uuid.uuid4()}" for _ in range(2)
    ]
    b_doc = f"supply_chain/{TENANT_B}/{uuid.uuid4()}/po/{uuid.uuid4()}/{uuid.uuid4()}"
    lookalike_feedback = f"feedback/{LOOKALIKE}/ws/fb/att"
    lookalike_doc = f"supply_chain/{LOOKALIKE}/{uuid.uuid4()}/po/{uuid.uuid4()}/{uuid.uuid4()}"
    for adapter, key in [
        (feedback, a_feedback),
        (feedback, b_feedback),
        (feedback, lookalike_feedback),
        *((documents, k) for k in a_docs),
        (documents, b_doc),
        (documents, lookalike_doc),
    ]:
        asyncio.run(adapter.put_object(key, f"bytes of {key}".encode(), "application/pdf"))
    lane = TenantOffboardingLane(
        store=_Store(),
        artifacts=buckets["artifacts"],
        exports=buckets["exports"],
        attachments=feedback,
        case_documents=documents,
        vector_index=_Vectors(),
        clock=_Clock(),
    )

    asyncio.run(lane.run(TENANT_A))

    assert _keys(feedback) == {b_feedback, lookalike_feedback}
    assert _keys(documents) == {b_doc, lookalike_doc}
    (export_key,) = _keys(buckets["exports"])
    bundle = asyncio.run(buckets["exports"].get_object(export_key))
    with zipfile.ZipFile(BytesIO(bundle)) as zf:
        names = set(zf.namelist())
        assert {f"blobs/case_documents/{k}" for k in a_docs} <= names
        assert f"blobs/attachments/{a_feedback}" in names
        assert not any(str(TENANT_B) in n or LOOKALIKE in n for n in names)
        assert zf.read(f"blobs/case_documents/{a_docs[0]}") == f"bytes of {a_docs[0]}".encode()
