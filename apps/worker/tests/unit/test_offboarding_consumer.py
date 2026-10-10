"""TenantOffboardingLane orchestration, against fakes for every port.

The Postgres logic (catalog discovery, FK-ordered purge, RLS isolation) is
already proven against a real database in dw_platform's own
test_offboarding.py. What is under test here is the orchestration this
package owns: the order export/upload/purge/delete happen in, that one
tenant's failure does not stop another claimed on the same tick, and that a
failure is reported rather than swallowed.
"""

from __future__ import annotations

import json
import uuid
import zipfile
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from io import BytesIO
from typing import Any

import pytest

from dw_worker.consumers.offboarding import (
    TenantOffboardingLane,
    _build_bundle,
    _json_default,
    build_offboarding_consumer,
)

pytestmark = pytest.mark.unit

TENANT = uuid.UUID(int=1)
OTHER_TENANT = uuid.UUID(int=2)


@dataclass(frozen=True)
class _ExportedTable:
    schema: str
    table: str
    rows: list[dict[str, Any]]


@dataclass
class _FakeStore:
    claimed: list[uuid.UUID]
    rows_by_tenant: dict[uuid.UUID, list[_ExportedTable]] = field(default_factory=dict)
    fail_export_for: set[uuid.UUID] = field(default_factory=set)
    calls: list[tuple[Any, ...]] = field(default_factory=list)

    async def claim_requested(self) -> list[uuid.UUID]:
        return self.claimed

    async def export_rows(self, tenant_id: uuid.UUID) -> list[_ExportedTable]:
        self.calls.append(("export_rows", tenant_id))
        if tenant_id in self.fail_export_for:
            raise RuntimeError("export blew up")
        return self.rows_by_tenant.get(tenant_id, [])

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
        self.calls.append(("mark_status", tenant_id, status, export_key, error))


@dataclass
class _FakeBucket:
    name: str
    objects: dict[str, bytes] = field(default_factory=dict)
    calls: list[tuple[Any, ...]] = field(default_factory=list)

    async def put_object(self, key: str, data: bytes, content_type: str) -> str:
        self.calls.append((self.name, "put_object", key))
        self.objects[key] = data
        return f"s3://{self.name}/{key}"

    async def get_object(self, key: str) -> bytes:
        self.calls.append((self.name, "get_object", key))
        return self.objects[key]

    async def list_objects(self, prefix: str) -> list[str]:
        self.calls.append((self.name, "list_objects", prefix))
        return [k for k in self.objects if k.startswith(prefix)]

    async def delete_object(self, key: str) -> None:
        self.calls.append((self.name, "delete_object", key))
        self.objects.pop(key, None)


@dataclass
class _FakeVectorIndex:
    calls: list[uuid.UUID] = field(default_factory=list)

    async def delete_by_tenant(self, tenant_id: uuid.UUID) -> None:
        self.calls.append(tenant_id)


@dataclass(frozen=True)
class _FrozenClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def _lane(
    store: _FakeStore,
    artifacts: _FakeBucket,
    exports: _FakeBucket,
    attachments: _FakeBucket,
    case_documents: _FakeBucket | None = None,
) -> TenantOffboardingLane:
    return TenantOffboardingLane(
        store=store,
        artifacts=artifacts,
        exports=exports,
        attachments=attachments,
        case_documents=case_documents or _FakeBucket("case-documents"),
        vector_index=_FakeVectorIndex(),
        # A deployment without Qdrant has no memory points; the real-store case
        # is `tests/integration/test_offboarding_memory_vectors.py`.
        memory_vectors=None,
        clock=_FrozenClock(),
    )


async def test_run_exports_uploads_then_purges_in_order() -> None:
    store = _FakeStore(
        claimed=[TENANT],
        rows_by_tenant={
            TENANT: [_ExportedTable("platform", "workspaces", [{"tenant_id": str(TENANT)}])]
        },
    )
    artifacts = _FakeBucket("artifacts", objects={f"{TENANT}/ws/doc-1": b"pdf-bytes"})
    exports = _FakeBucket("exports")
    attachments = _FakeBucket(
        "attachments", objects={f"feedback/{TENANT}/ws/fb-1/att-1": b"png-bytes"}
    )
    lane = _lane(store, artifacts, exports, attachments)

    await lane.run(TENANT)

    # export before purge, upload before purge, purge before blob deletes
    kinds = [c[0] for c in store.calls]
    assert kinds.index("export_rows") < kinds.index("purge_rows")
    upload_calls = [c for c in exports.calls if c[1] == "put_object"]
    assert len(upload_calls) == 1
    assert not artifacts.objects, "the artifact must be deleted after purge"
    assert not attachments.objects, "the attachment must be deleted after purge"

    statuses = [c[2] for c in store.calls if c[0] == "mark_status"]
    assert statuses == ["exporting", "purging", "completed"]


async def test_both_tenant_prefixed_buckets_are_exported_and_emptied_for_that_tenant_only() -> None:
    """Feedback attachments key as `feedback/{tenant}/...`, case documents as
    `supply_chain/{tenant}/...` in their own bucket. Each is listed by its own
    prefix, exported into the bundle and deleted; the other tenant's objects
    stay, and so does a key that carries this tenant's id only as the start of
    a longer segment, which a prefix without its trailing slash would take."""
    mine_feedback = f"feedback/{TENANT}/ws/fb-1/att-1"
    mine_doc = f"supply_chain/{TENANT}/ws/po/case-1/doc-1"
    theirs = {
        f"feedback/{OTHER_TENANT}/ws/fb-9/att-9": b"b-png",
        f"feedback/{TENANT}x/ws/fb-8/att-8": b"b2-png",
        f"supply_chain/{OTHER_TENANT}/ws/po/case-9/doc-9": b"b-pdf",
        f"supply_chain/{TENANT}x/ws/po/case-8/doc-8": b"b2-pdf",
    }
    attachments = _FakeBucket(
        "attachments",
        objects={mine_feedback: b"a-png", **{k: v for k, v in theirs.items() if "feedback" in k}},
    )
    documents = _FakeBucket(
        "case-documents",
        objects={mine_doc: b"a-pdf", **{k: v for k, v in theirs.items() if "supply" in k}},
    )
    exports = _FakeBucket("exports")
    lane = _lane(_FakeStore(claimed=[TENANT]), _FakeBucket("a"), exports, attachments, documents)

    await lane.run(TENANT)

    assert ("attachments", "list_objects", f"feedback/{TENANT}/") in attachments.calls
    assert ("case-documents", "list_objects", f"supply_chain/{TENANT}/") in documents.calls
    assert set(attachments.objects) | set(documents.objects) == set(theirs)
    deleted = {c[2] for c in attachments.calls + documents.calls if c[1] == "delete_object"}
    assert deleted == {mine_feedback, mine_doc}
    (bundle,) = exports.objects.values()
    with zipfile.ZipFile(BytesIO(bundle)) as zf:
        names = set(zf.namelist())
    assert f"blobs/attachments/{mine_feedback}" in names
    assert f"blobs/case_documents/{mine_doc}" in names
    assert not any(str(OTHER_TENANT) in n or f"{TENANT}x" in n for n in names)


async def test_the_export_key_is_recorded_on_every_status_update_from_upload_onward() -> None:
    store = _FakeStore(claimed=[TENANT])
    lane = _lane(store, _FakeBucket("a"), _FakeBucket("e"), _FakeBucket("f"))

    await lane.run(TENANT)

    export_keys = [c[3] for c in store.calls if c[0] == "mark_status"]
    assert all(k is not None for k in export_keys), "every status update carries the export key"
    assert len(set(export_keys)) == 1, "the same key throughout, not regenerated"


async def test_a_failing_tenant_does_not_stop_another_claimed_on_the_same_tick() -> None:
    store = _FakeStore(claimed=[TENANT, OTHER_TENANT], fail_export_for={TENANT})
    lane = _lane(store, _FakeBucket("a"), _FakeBucket("e"), _FakeBucket("f"))
    consume = build_offboarding_consumer(lane)

    await consume()  # must not raise

    failed = [c for c in store.calls if c[0] == "mark_status" and c[1] == TENANT]
    assert failed and failed[-1][2] == "failed"
    assert failed[-1][4] is not None, "the error message is recorded"
    completed_other = [c for c in store.calls if c[0] == "mark_status" and c[1] == OTHER_TENANT]
    assert completed_other[-1][2] == "completed", "the other tenant's pass still finished"


async def test_a_memory_vector_purge_that_fails_is_reported_not_completed() -> None:
    """A tenant whose embeddings are still in the memory store is not gone,
    and a `completed` status would tell an operator it was."""

    @dataclass
    class _DownMemoryVectors:
        async def delete_by_tenant(self, tenant_id: uuid.UUID) -> None:
            raise RuntimeError("qdrant xuống")

    store = _FakeStore(claimed=[TENANT])
    lane = replace(
        _lane(store, _FakeBucket("a"), _FakeBucket("e"), _FakeBucket("f")),
        memory_vectors=_DownMemoryVectors(),
    )

    await build_offboarding_consumer(lane)()

    statuses = [c[2] for c in store.calls if c[0] == "mark_status"]
    assert statuses[-1] == "failed"
    assert "completed" not in statuses


def test_json_default_handles_every_type_a_row_can_hold() -> None:
    assert _json_default(uuid.UUID(int=1)) == str(uuid.UUID(int=1))
    assert _json_default(datetime(2026, 1, 1, tzinfo=UTC)) == "2026-01-01T00:00:00+00:00"
    assert _json_default(date(2026, 1, 1)) == "2026-01-01"
    assert _json_default(Decimal("1.50")) == "1.50"
    assert _json_default(b"hi") == "hi"
    with pytest.raises(TypeError):
        _json_default(object())


def test_build_bundle_contains_table_ndjson_and_both_blob_kinds() -> None:
    tables = [_ExportedTable("platform", "workspaces", [{"id": str(uuid.uuid4())}])]
    bundle = _build_bundle(
        tables, {"art-1": b"artifact-bytes"}, {"att-1": b"attachment-bytes"}, {"doc-1": b"pdf"}
    )

    with zipfile.ZipFile(BytesIO(bundle)) as zf:
        names = set(zf.namelist())
        assert "tables/platform.workspaces.ndjson" in names
        assert "blobs/artifacts/art-1" in names
        assert "blobs/attachments/att-1" in names
        assert "blobs/case_documents/doc-1" in names
        assert zf.read("blobs/artifacts/art-1") == b"artifact-bytes"
        row = json.loads(zf.read("tables/platform.workspaces.ndjson").decode().splitlines()[0])
        assert "id" in row
