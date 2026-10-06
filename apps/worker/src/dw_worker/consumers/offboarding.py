"""Tenant offboarding lane: claim a request, export, purge, report back.

The Postgres half (`SqlTenantOffboarding` in `dw_platform`) is catalog-driven
and knows nothing about Qdrant or object storage — both live behind ports
`dw_platform` may not import (import-linter's "Vector/object-storage SDKs
only inside knowledge adapters"). This is the composition root, so it is the
one place allowed to hold both halves at once: the generic Postgres rows, the
storage buckets and the two vector collections (knowledge chunks and the memory
ranker's points), orchestrated into one pass.

Every step here is safe to re-run from the top: `export_rows` only reads,
uploading overwrites the same key, `purge_rows`/`delete_object`/
`delete_by_tenant` delete rows or objects that may already be gone (not an
error), and `mark_status` overwrites the same row. That is what lets
`claim_requested`'s stale-claim window (`SqlTenantOffboarding`'s own
docstring) resume a request a dead worker abandoned mid-pass, rather than
needing its own separate recovery path.
"""

from __future__ import annotations

import json
import logging
import zipfile
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from typing import Any, Protocol
from uuid import UUID

from dw_kernel.ports import UtcClock
from dw_supply_chain.domain.case_document import ObjectKey

logger = logging.getLogger("dw_worker.offboarding")

# Not latency-sensitive - an operator just clicked a button that starts a
# process with a human on the other end of it, not a request waiting on a
# response. Five minutes keeps the poll cheap without keeping anyone wondering
# why nothing happened for the length of a coffee break.
INTERVAL_SECONDS = 300.0


class _ExportedTable(Protocol):
    """Structurally `dw_platform`'s `ExportedTable` — a frozen dataclass, so
    read-only properties here rather than plain attributes: a Protocol's
    plain attribute is implicitly settable, which a frozen dataclass can
    never satisfy no matter how exactly its field types match."""

    @property
    def schema(self) -> str: ...
    @property
    def table(self) -> str: ...
    @property
    def rows(self) -> list[dict[str, Any]]: ...


class OffboardingStorePort(Protocol):
    """Structurally `SqlTenantOffboarding` - declared here rather than
    imported, the same way every other consumer in this package names only
    the method it calls (see `reaper.py`'s `StaleReaperPort`)."""

    async def claim_requested(self) -> list[UUID]: ...

    async def export_rows(self, tenant_id: UUID) -> Sequence[_ExportedTable]: ...

    async def purge_rows(self, tenant_id: UUID) -> None: ...

    async def mark_status(
        self,
        tenant_id: UUID,
        status: str,
        *,
        export_key: str | None = None,
        error: str | None = None,
    ) -> None: ...


class BucketPort(Protocol):
    """Structurally `dw_knowledge.ports.ObjectStoragePort` - one instance per
    bucket this lane touches (knowledge artifacts, feedback attachments, case
    documents, the export destination). All four are plain MinIO buckets
    holding blobs keyed by path, so one adapter shape covers them all; `dw_api`'s
    `MinioAttachmentStorage` also exists and does the same thing under
    different method names (`get`/`put`/`delete`, no `list` until this
    feature added one) for its one bucket — reusing it here would make
    `dw_worker` depend on `dw_api`, which this composition root does not do
    for anything else. `dw_knowledge`'s adapter, pointed at each bucket in
    turn, is the one this lane uses for all four.
    """

    async def put_object(self, key: str, data: bytes, content_type: str) -> str: ...

    async def get_object(self, key: str) -> bytes: ...

    async def list_objects(self, prefix: str) -> list[str]: ...

    async def delete_object(self, key: str) -> None: ...


class VectorPurgePort(Protocol):
    """Structurally `dw_knowledge.ports.VectorIndexPort.delete_by_tenant`, and
    `dw_memory.ranking.MemoryVectorPurgePort.delete_by_tenant`."""

    async def delete_by_tenant(self, tenant_id: UUID) -> None: ...


def _json_default(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    raise TypeError(f"cannot export a {type(value).__name__}")


@dataclass(frozen=True)
class TenantOffboardingLane:
    """One tenant's export-then-purge, holding every port the pass needs.

    All in memory, deliberately not streamed: this platform has no tenant
    running real traffic yet (see .claude/PLAN.md), so bounding memory use
    against a document library nobody has built is a problem to solve with
    real sizes in hand, not guessed ahead of them.
    """

    store: OffboardingStorePort
    artifacts: BucketPort
    exports: BucketPort
    attachments: BucketPort
    # Supply Chain's case documents, in their own bucket (ADR 0021).
    case_documents: BucketPort
    vector_index: VectorPurgePort
    # The memory ranker's collection. The SQL purge deletes `memory.items`, and
    # each row's point is an embedding of its content; leaving them would keep
    # the departed tenant's text in the one store this pass never named. No
    # default: `None` is the composition root saying there is no vector store.
    memory_vectors: VectorPurgePort | None
    clock: UtcClock

    async def run(self, tenant_id: UUID) -> None:
        rows = await self.store.export_rows(tenant_id)
        # Three key shapes, all tenant-prefixed but not the same way:
        # knowledge artifacts key as `{tenant_id}/{workspace_id}/...`
        # (dw_knowledge's KnowledgeGateway), feedback attachments as
        # `feedback/{tenant_id}/{workspace_id}/...`
        # (dw_platform.application.feedback_dto.attachment_key), case documents
        # as `supply_chain/{tenant_id}/{workspace_id}/...` (named by their one
        # owner, `ObjectKey`) - checked against each call site rather than
        # assumed, since one wrong prefix here means silently exporting and
        # purging nothing for that bucket.
        artifact_keys = await self.artifacts.list_objects(f"{tenant_id}/")
        artifact_blobs = {key: await self.artifacts.get_object(key) for key in artifact_keys}
        attachment_keys = await self.attachments.list_objects(f"feedback/{tenant_id}/")
        attachment_blobs = {key: await self.attachments.get_object(key) for key in attachment_keys}
        document_keys = await self.case_documents.list_objects(ObjectKey.tenant_prefix(tenant_id))
        document_blobs = {key: await self.case_documents.get_object(key) for key in document_keys}

        bundle = _build_bundle(rows, artifact_blobs, attachment_blobs, document_blobs)
        stamp = self.clock.now().strftime("%Y%m%dT%H%M%SZ")
        export_key = f"{tenant_id}/offboarding-{stamp}.zip"
        await self.exports.put_object(export_key, bundle, "application/zip")
        await self.store.mark_status(tenant_id, "exporting", export_key=export_key)

        await self.store.mark_status(tenant_id, "purging", export_key=export_key)
        await self.store.purge_rows(tenant_id)
        for key in artifact_keys:
            await self.artifacts.delete_object(key)
        for key in attachment_keys:
            await self.attachments.delete_object(key)
        for key in document_keys:
            await self.case_documents.delete_object(key)
        await self.vector_index.delete_by_tenant(tenant_id)
        if self.memory_vectors is not None:
            await self.memory_vectors.delete_by_tenant(tenant_id)

        await self.store.mark_status(tenant_id, "completed", export_key=export_key)


def _build_bundle(
    rows: Sequence[_ExportedTable],
    artifact_blobs: dict[str, bytes],
    attachment_blobs: dict[str, bytes],
    document_blobs: dict[str, bytes],
) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for table in rows:
            name = f"tables/{table.schema}.{table.table}.ndjson"
            body = "\n".join(
                json.dumps(row, default=_json_default, ensure_ascii=False) for row in table.rows
            )
            zf.writestr(name, body)
        for key, data in artifact_blobs.items():
            zf.writestr(f"blobs/artifacts/{key}", data)
        for key, data in attachment_blobs.items():
            zf.writestr(f"blobs/attachments/{key}", data)
        for key, data in document_blobs.items():
            zf.writestr(f"blobs/case_documents/{key}", data)
    return buf.getvalue()


def build_offboarding_consumer(lane: TenantOffboardingLane) -> Callable[[], Awaitable[None]]:
    """One tenant failing does not stop the others claimed on the same tick -
    `mark_status(..., "failed", ...)` leaves it for a human to look at rather
    than retrying it forever, and the request row (never purged - see
    `SqlTenantOffboarding._NEVER_PURGE`) is where that failure is visible.
    """

    async def consume() -> None:
        for tenant_id in await lane.store.claim_requested():
            try:
                await lane.run(tenant_id)
            except Exception as exc:
                logger.exception("offboarding failed for tenant %s", tenant_id)
                try:
                    await lane.store.mark_status(
                        tenant_id, "failed", error=f"{type(exc).__name__}: {exc}"[:500]
                    )
                except Exception:
                    logger.exception(
                        "offboarding for tenant %s failed AND could not record the failure",
                        tenant_id,
                    )

    return consume
