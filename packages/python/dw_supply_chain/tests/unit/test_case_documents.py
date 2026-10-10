"""Unit: UploadCaseDocument / ListCaseDocuments / DownloadCaseDocument.

Fakes stand in for the case lookup, the document records and the bucket; each
honours its port's contract (the record fake computes versions and refuses
another tenant's or workspace's rows the way RLS does). What is under test is
the handlers' own decisions: who may, which case, what is accepted, where the
bytes go, and that a failed insert leaves no object behind.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from dw_kernel.errors import (
    ConflictError,
    DomainError,
    InfrastructureError,
    NotFoundError,
    PayloadTooLargeError,
    PermissionDeniedError,
    UnsupportedMediaTypeError,
)
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.ports import FixedClock, Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.application.case_documents import (
    DownloadCaseDocument,
    ListCaseDocuments,
    UploadCaseDocument,
)
from dw_supply_chain.application.handlers import (
    COMMERCIAL_READ,
    DOCUMENT_READ,
    DOCUMENT_WRITE,
    PACKAGING_DOCUMENT_WRITE,
)
from dw_supply_chain.application.ports import NewCaseDocument
from dw_supply_chain.domain.case_document import (
    MKT_DOCUMENT_TYPES,
    CaseDocument,
    CaseDocumentId,
    CaseKind,
    DocumentType,
)
from dw_supply_chain.domain.po_case import POCase, POCaseId

pytestmark = pytest.mark.unit

TENANT, WORKSPACE, OTHER_TENANT, OTHER_WORKSPACE = (uuid.uuid4() for _ in range(4))
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
PDF = b"%PDF-1.7\n" + b"x" * 100
MAX_BYTES = 1024


def _context(
    *,
    tenant: uuid.UUID = TENANT,
    workspace: uuid.UUID = WORKSPACE,
    scopes: frozenset[str] = frozenset({DOCUMENT_READ, DOCUMENT_WRITE}),
) -> AccessContext:
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=scopes,
        plan_id="professional",
    )


def _case(tenant: uuid.UUID = TENANT, workspace: uuid.UUID = WORKSPACE) -> POCase:
    return POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(tenant),
        workspace_id=WorkspaceId(workspace),
        po_reference=f"PO-{uuid.uuid4().hex[:6]}",
        supplier_name="NCC A",
    )


class FakeCases:
    """`case_workspace` the way the PO repository answers it: tenant-scoped
    only, so the handler's own workspace check is what is under test."""

    def __init__(self, *cases: POCase) -> None:
        self.by_id = {case.id.value: case for case in cases}

    async def case_workspace(self, context: AccessContext, case_id: uuid.UUID) -> uuid.UUID | None:
        case = self.by_id.get(case_id)
        if case is None or case.tenant_id.value != context.tenant_id:
            return None
        return case.workspace_id.value


@dataclass
class FakeDocuments:
    rows: list[CaseDocument] = field(default_factory=list)
    audits: list[AuditEvent] = field(default_factory=list)
    fail_with: Exception | None = None

    def _visible(self, context: AccessContext, row: CaseDocument) -> bool:
        return row.tenant_id == context.tenant_id and row.workspace_id == context.workspace_id

    async def add(
        self, context: AccessContext, document: NewCaseDocument, *, audit: AuditEvent
    ) -> CaseDocument:
        if self.fail_with is not None:
            raise self.fail_with
        version = 1 + max(
            (
                r.version
                for r in self.rows
                if self._visible(context, r)
                and r.case_id == document.case_id
                and r.doc_type is document.doc_type
            ),
            default=0,
        )
        row = CaseDocument(
            id=document.id,
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            case_kind=document.case_kind,
            case_id=document.case_id,
            doc_type=document.doc_type,
            object_key=document.object_key,
            filename=document.filename,
            content_type=document.content_type,
            size_bytes=document.size_bytes,
            sha256=document.sha256,
            version=version,
            uploaded_by=context.principal_id,
            uploaded_at=NOW,
        )
        self.rows.append(row)
        self.audits.append(audit)
        return row

    async def list_for_case(
        self, context: AccessContext, case_kind: CaseKind, case_id: uuid.UUID
    ) -> list[CaseDocument]:
        rows = [
            r
            for r in self.rows
            if self._visible(context, r) and r.case_kind is case_kind and r.case_id == case_id
        ]
        return sorted(rows, key=lambda r: (r.doc_type.value, -r.version))

    async def get(self, context: AccessContext, document_id: CaseDocumentId) -> CaseDocument | None:
        return next(
            (r for r in self.rows if r.id == document_id and self._visible(context, r)), None
        )


@dataclass
class FakeBucket:
    objects: dict[str, tuple[bytes, str]] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)
    fail_delete: bool = False

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = (data, content_type)

    async def get(self, key: str) -> bytes:
        if key not in self.objects:
            raise NotFoundError("object not found")
        return self.objects[key][0]

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        if self.fail_delete:
            raise InfrastructureError("bucket unreachable")
        self.objects.pop(key, None)


@dataclass
class Stack:
    cases: FakeCases
    documents: FakeDocuments
    bucket: FakeBucket
    upload: UploadCaseDocument
    list: ListCaseDocuments
    download: DownloadCaseDocument


def _stack(*cases: POCase) -> Stack:
    lookup, documents, bucket = FakeCases(*cases), FakeDocuments(), FakeBucket()
    authz = ScopeAuthorizationService()
    return Stack(
        cases=lookup,
        documents=documents,
        bucket=bucket,
        upload=UploadCaseDocument(
            cases={CaseKind.PO: lookup},
            documents=documents,
            storage=bucket,
            authz=authz,
            ids=Uuid4Generator(),
            clock=FixedClock(NOW),
            max_bytes=MAX_BYTES,
        ),
        list=ListCaseDocuments(cases={CaseKind.PO: lookup}, documents=documents, authz=authz),
        download=DownloadCaseDocument(documents=documents, storage=bucket, authz=authz),
    )


async def _upload(
    stack: Stack,
    case: POCase,
    *,
    context: AccessContext | None = None,
    doc_type: DocumentType = DocumentType.PURCHASE_ORDER,
    filename: str = "PO.pdf",
    content_type: str = "application/pdf",
    data: bytes = PDF,
) -> CaseDocument:
    return await stack.upload.handle(
        context or _context(),
        CaseKind.PO,
        case.id.value,
        doc_type=doc_type,
        filename=filename,
        declared_content_type=content_type,
        data=data,
    )


async def test_an_upload_stores_the_bytes_under_the_callers_tenant_and_workspace() -> None:
    case = _case()
    stack = _stack(case)

    document = await _upload(stack, case)

    expected = f"supply_chain/{TENANT}/{WORKSPACE}/po/{case.id}/{document.id}"
    assert document.object_key == expected
    assert stack.bucket.objects == {expected: (PDF, "application/pdf")}
    assert document.sha256 == hashlib.sha256(PDF).hexdigest()
    assert document.size_bytes == len(PDF)
    assert document.version == 1


async def test_a_filename_that_climbs_directories_does_not_move_the_key() -> None:
    case = _case()
    stack = _stack(case)

    document = await _upload(stack, case, filename="../../../other-tenant/x.pdf")

    assert document.object_key.startswith(f"supply_chain/{TENANT}/{WORKSPACE}/po/{case.id}/")
    assert ".." not in document.object_key
    assert document.filename == "x.pdf"


async def test_versions_count_per_case_and_type() -> None:
    case = _case()
    stack = _stack(case)

    first = await _upload(stack, case)
    second = await _upload(stack, case)
    other_type = await _upload(stack, case, doc_type=DocumentType.DEPOSIT_DOCS)

    assert (first.version, second.version, other_type.version) == (1, 2, 1)


async def test_the_audit_event_names_the_uploader_and_the_document() -> None:
    case = _case()
    stack = _stack(case)
    context = _context()

    document = await _upload(stack, case, context=context)

    (audit,) = stack.documents.audits
    assert audit.action == "supply_chain.document.upload"
    assert audit.actor_id.value == context.principal_id
    assert audit.tenant_id.value == TENANT and audit.workspace_id.value == WORKSPACE
    assert audit.resource_id == str(document.id)
    assert audit.details["case_id"] == str(case.id)
    assert audit.details["case_kind"] == "po"
    assert audit.details["doc_type"] == "purchase_order"
    assert audit.occurred_at == NOW


async def test_uploading_needs_the_document_write_scope() -> None:
    case = _case()
    stack = _stack(case)

    with pytest.raises(PermissionDeniedError):
        await _upload(stack, case, context=_context(scopes=frozenset({DOCUMENT_READ})))
    assert stack.bucket.objects == {} and stack.documents.rows == []


async def test_uploading_to_another_tenants_case_is_not_found_and_writes_nothing() -> None:
    theirs = _case(tenant=OTHER_TENANT)
    stack = _stack(theirs)

    with pytest.raises(NotFoundError):
        await _upload(stack, theirs)
    assert stack.bucket.objects == {} and stack.documents.rows == []


async def test_uploading_to_another_workspaces_case_is_not_found_and_writes_nothing() -> None:
    """The fake returns the case as if RLS had failed open: the handler's own
    workspace check is what this asserts, the layer behind RLS."""
    elsewhere = _case(workspace=OTHER_WORKSPACE)
    stack = _stack(elsewhere)

    with pytest.raises(NotFoundError):
        await _upload(stack, elsewhere)
    assert stack.bucket.objects == {} and stack.documents.rows == []


async def test_a_file_over_the_cap_is_413_and_one_at_the_cap_is_taken() -> None:
    case = _case()
    stack = _stack(case)
    at_cap = b"%PDF-" + b"x" * (MAX_BYTES - 5)

    with pytest.raises(PayloadTooLargeError):
        await _upload(stack, case, data=at_cap + b"x")
    assert stack.bucket.objects == {}

    assert (await _upload(stack, case, data=at_cap)).size_bytes == MAX_BYTES


@pytest.mark.parametrize(
    ("content_type", "data"),
    [("text/html", b"<html></html>"), ("application/pdf", b"<html><script>")],
)
async def test_a_type_off_the_list_or_against_its_bytes_is_415(
    content_type: str, data: bytes
) -> None:
    case = _case()
    stack = _stack(case)

    with pytest.raises(UnsupportedMediaTypeError):
        await _upload(stack, case, content_type=content_type, data=data)
    assert stack.bucket.objects == {}


async def test_an_empty_file_is_refused() -> None:
    case = _case()
    stack = _stack(case)

    with pytest.raises(DomainError):
        await _upload(stack, case, data=b"")
    assert stack.bucket.objects == {}


async def test_a_failed_insert_removes_the_object_it_just_wrote() -> None:
    case = _case()
    stack = _stack(case)
    stack.documents.fail_with = ConflictError("version taken")

    with pytest.raises(ConflictError):
        await _upload(stack, case)
    assert len(stack.bucket.deleted) == 1
    assert stack.bucket.objects == {}


async def test_a_failed_cleanup_still_surfaces_the_insert_error() -> None:
    """Best-effort: the orphan sweep finishes what this could not."""
    case = _case()
    stack = _stack(case)
    stack.documents.fail_with = ConflictError("version taken")
    stack.bucket.fail_delete = True

    with pytest.raises(ConflictError):
        await _upload(stack, case)
    assert len(stack.bucket.deleted) == 1


async def test_listing_returns_the_cases_documents() -> None:
    case = _case()
    stack = _stack(case)
    await _upload(stack, case)
    await _upload(stack, case)

    listed = await stack.list.handle(
        _context(scopes=frozenset({DOCUMENT_READ})), CaseKind.PO, case.id.value
    )

    assert [d.version for d in listed] == [2, 1]


async def test_listing_needs_the_read_scope() -> None:
    case = _case()
    stack = _stack(case)

    with pytest.raises(PermissionDeniedError):
        await stack.list.handle(_context(scopes=frozenset()), CaseKind.PO, case.id.value)


@pytest.mark.parametrize(
    "case_owner", [(OTHER_TENANT, WORKSPACE), (TENANT, OTHER_WORKSPACE)], ids=["tenant", "ws"]
)
async def test_listing_another_tenants_or_workspaces_case_is_not_found(
    case_owner: tuple[uuid.UUID, uuid.UUID],
) -> None:
    case = _case(*case_owner)
    stack = _stack(case)

    with pytest.raises(NotFoundError):
        await stack.list.handle(_context(), CaseKind.PO, case.id.value)


async def test_a_download_returns_the_record_and_its_bytes() -> None:
    case = _case()
    stack = _stack(case)
    document = await _upload(stack, case)

    # A PO prints prices: its file needs the price scope too (ticket
    # ai-automation/14, `PRICED_DOCUMENT_TYPES`).
    with pytest.raises(PermissionDeniedError):
        await stack.download.handle(_context(scopes=frozenset({DOCUMENT_READ})), document.id)
    record, data = await stack.download.handle(
        _context(scopes=frozenset({DOCUMENT_READ, COMMERCIAL_READ})), document.id
    )

    assert record == document
    assert data == PDF


@pytest.mark.parametrize(
    "doc_type",
    [
        DocumentType.DEPOSIT_DOCS,
        DocumentType.PAYMENT_DOCS,
        DocumentType.PROFORMA_INVOICE,
        DocumentType.COMMERCIAL_INVOICE,
        DocumentType.BANK_TRANSFER_RECEIPT,
    ],
)
async def test_a_payment_paper_needs_the_price_scope_to_be_read(doc_type: DocumentType) -> None:
    # Ticket ai-automation/15: requests code drafted, the supplier's invoices
    # and the bank's transfer receipt print amounts and a beneficiary account.
    case = _case()
    stack = _stack(case)
    document = await _upload(stack, case, doc_type=doc_type)

    with pytest.raises(PermissionDeniedError):
        await stack.download.handle(_context(scopes=frozenset({DOCUMENT_READ})), document.id)
    record, _ = await stack.download.handle(
        _context(scopes=frozenset({DOCUMENT_READ, COMMERCIAL_READ})), document.id
    )
    assert record.doc_type is doc_type


async def test_a_download_needs_the_read_scope() -> None:
    case = _case()
    stack = _stack(case)
    document = await _upload(stack, case)

    with pytest.raises(PermissionDeniedError):
        await stack.download.handle(_context(scopes=frozenset()), document.id)


@pytest.mark.parametrize(
    ("tenant", "workspace"), [(OTHER_TENANT, WORKSPACE), (TENANT, OTHER_WORKSPACE)]
)
async def test_downloading_another_tenants_or_workspaces_document_is_not_found(
    tenant: uuid.UUID, workspace: uuid.UUID
) -> None:
    case = _case()
    stack = _stack(case)
    document = await _upload(stack, case)

    with pytest.raises(NotFoundError):
        await stack.download.handle(_context(tenant=tenant, workspace=workspace), document.id)


@pytest.mark.parametrize("doc_type", sorted(MKT_DOCUMENT_TYPES, key=str))
async def test_mkt_uploads_its_four_papers_and_nothing_else(doc_type: DocumentType) -> None:
    # ADR 0028 (ticket ai-automation/16): sc_mkt holds the packaging write,
    # not the document write.
    case = _case()
    stack = _stack(case)
    marketer = _context(scopes=frozenset({PACKAGING_DOCUMENT_WRITE, DOCUMENT_READ}))
    document = await _upload(stack, case, context=marketer, doc_type=doc_type)
    assert document.doc_type is doc_type
    with pytest.raises(PermissionDeniedError):
        await _upload(stack, case, context=marketer, doc_type=DocumentType.DEPOSIT_DOCS)
    with pytest.raises(PermissionDeniedError):
        await _upload(
            stack, case, context=_context(scopes=frozenset({DOCUMENT_READ})), doc_type=doc_type
        )
