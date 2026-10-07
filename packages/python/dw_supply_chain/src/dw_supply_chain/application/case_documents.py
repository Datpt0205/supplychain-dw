"""Case documents: upload, list, download (ADR 0021, slice D).

Every decision here is made before a byte is stored:

- **Who:** `supply_chain.document.write` to upload, `.read` to list or
  download, checked here where the read or write happens.
- **Which case:** a PO case or a product-development case (`CaseKind`), each
  looked up through its own repository, chosen from one table keyed by kind.
  The case is read under the caller's tenant (RLS) and must be in the caller's
  workspace. Both `po_cases` and `product_dev_cases` are narrowed by
  workspace in RLS as well (`62cdcf3bf2d2` for PO cases), so this check is the
  second layer, and the composite FK refuses it again in the database.
- **What:** at most `max_bytes` (the deployment's setting), not empty, and a
  content type `accepted_content_type` lets through.
- **Where:** the object key is built from the context's tenant and workspace
  and the case and document ids. Nothing from the request reaches it.

Then the object is written first and the row second. A row without its bytes
cannot be repaired; bytes without a row can, so when the insert fails the
object just written is deleted, best effort, and the orphan sweep collects
whatever a crash between the two left behind.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from dw_kernel.errors import DomainError, NotFoundError, PayloadTooLargeError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import AuthorizationPort
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.application.handlers import DOCUMENT_READ, DOCUMENT_WRITE
from dw_supply_chain.application.ports import (
    CaseDocumentRepositoryPort,
    CaseDocumentStoragePort,
    NewCaseDocument,
)
from dw_supply_chain.domain.case_document import (
    SNIFF_BYTES,
    CaseDocument,
    CaseDocumentId,
    CaseKind,
    DocumentType,
    ObjectKey,
    accepted_content_type,
    clean_filename,
)

logger = logging.getLogger(__name__)

_RESOURCE = "case_document"
UPLOAD_ACTION = "supply_chain.document.upload"


class CaseLookupPort(Protocol):
    """The one thing these handlers ask of a case repository: the workspace
    of the caller's case, read under the caller's RLS. `SqlPOCaseRepository`
    and `SqlProductCaseRepository` each answer it for their kind."""

    async def case_workspace(
        self, context: AccessContext, case_id: uuid.UUID
    ) -> uuid.UUID | None: ...


CaseLookups = Mapping[CaseKind, CaseLookupPort]


async def _require_case_in_workspace(
    cases: CaseLookups, context: AccessContext, case_kind: CaseKind, case_id: uuid.UUID
) -> None:
    """Refuses unless the case is in the caller's tenant AND workspace. Either
    miss reads as not found: a 403 would confirm the case exists."""
    workspace = await cases[case_kind].case_workspace(context, case_id)
    if workspace is None or workspace != context.workspace_id:
        raise NotFoundError(
            "case not found", details={"case_kind": case_kind.value, "case_id": str(case_id)}
        )


@dataclass(frozen=True)
class UploadCaseDocument:
    cases: CaseLookups
    documents: CaseDocumentRepositoryPort
    storage: CaseDocumentStoragePort
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock
    # The deployment's cap (`case_document_max_bytes`). The route refuses a
    # body much over it from its headers and reads at most one byte past it
    # into memory; this is the exact check on the file.
    max_bytes: int

    async def handle(
        self,
        context: AccessContext,
        case_kind: CaseKind,
        case_id: uuid.UUID,
        *,
        doc_type: DocumentType,
        filename: str,
        declared_content_type: str,
        data: bytes,
    ) -> CaseDocument:
        await self.authz.require(
            context=context,
            action=DOCUMENT_WRITE,
            resource_type=_RESOURCE,
            resource_id=str(case_id),
        )
        await _require_case_in_workspace(self.cases, context, case_kind, case_id)
        if len(data) > self.max_bytes:
            raise PayloadTooLargeError(
                f"file tối đa {self.max_bytes // (1024 * 1024)} MB",
                details={"max_bytes": self.max_bytes},
            )
        if not data:
            raise DomainError("file rỗng", details={"case_id": str(case_id)})
        content_type = accepted_content_type(declared_content_type, data[:SNIFF_BYTES])
        name = clean_filename(filename)

        document_id = CaseDocumentId(self.ids.new_uuid())
        key = ObjectKey.build(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            case_kind=case_kind,
            case_id=case_id,
            document_id=document_id.value,
        ).value
        sha256 = hashlib.sha256(data).hexdigest()
        await self.storage.put(key, data, content_type)
        try:
            return await self.documents.add(
                context,
                NewCaseDocument(
                    id=document_id,
                    case_kind=case_kind,
                    case_id=case_id,
                    doc_type=doc_type,
                    object_key=key,
                    filename=name,
                    content_type=content_type,
                    size_bytes=len(data),
                    sha256=sha256,
                ),
                audit=AuditEvent(
                    id=self.ids.new_uuid(),
                    tenant_id=TenantId(context.tenant_id),
                    workspace_id=WorkspaceId(context.workspace_id),
                    actor_id=UserId(context.principal_id),
                    action=UPLOAD_ACTION,
                    resource_type=_RESOURCE,
                    resource_id=str(document_id),
                    occurred_at=self.clock.now(),
                    details={
                        "case_kind": case_kind.value,
                        "case_id": str(case_id),
                        "doc_type": doc_type.value,
                        "sha256": sha256,
                        "size_bytes": len(data),
                    },
                ),
            )
        except Exception:
            await self._forget(key)
            raise

    async def _forget(self, key: str) -> None:
        try:
            await self.storage.delete(key)
        except Exception:
            # The insert's error is the one the caller must see; the sweep
            # removes this object once it is a day old and still has no row.
            logger.warning("could not delete an object whose row was not written: %s", key)


@dataclass(frozen=True)
class ListCaseDocuments:
    cases: CaseLookups
    documents: CaseDocumentRepositoryPort
    authz: AuthorizationPort

    async def handle(
        self, context: AccessContext, case_kind: CaseKind, case_id: uuid.UUID
    ) -> list[CaseDocument]:
        await self.authz.require(
            context=context,
            action=DOCUMENT_READ,
            resource_type=_RESOURCE,
            resource_id=str(case_id),
        )
        await _require_case_in_workspace(self.cases, context, case_kind, case_id)
        return await self.documents.list_for_case(context, case_kind, case_id)


@dataclass(frozen=True)
class DownloadCaseDocument:
    """The record, read under RLS, then its bytes. No signed URL: the bucket
    is reached only through here, so the scope check runs on every read."""

    documents: CaseDocumentRepositoryPort
    storage: CaseDocumentStoragePort
    authz: AuthorizationPort

    async def handle(
        self, context: AccessContext, document_id: CaseDocumentId
    ) -> tuple[CaseDocument, bytes]:
        await self.authz.require(
            context=context,
            action=DOCUMENT_READ,
            resource_type=_RESOURCE,
            resource_id=str(document_id),
        )
        # The repository reads under the caller's tenant AND workspace (RLS,
        # plus its own filter), so another workspace's document is None here.
        document = await self.documents.get(context, document_id)
        if document is None:
            raise NotFoundError("document not found", details={"document_id": str(document_id)})
        return document, await self.storage.get(document.object_key)
