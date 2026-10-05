"""The case-document routes, through the real app wiring (slice D, ADR 0021).

The case lookup, the document records and the bucket are faked; auth, the
access context, the multipart parsing, the idempotency dependency, the route
and the handlers run for real. The container here carries ONLY the document
handlers, which is itself one of the assertions: `create_app` mounts the
documents router on its own guard, apart from the Supply Chain router's.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import pytest
from asgi_lifespan import LifespanManager
from minio.error import MinioException, S3Error
from starlette.datastructures import UploadFile as StarletteUploadFile

from dw_api.bootstrap import ApiContainer
from dw_api.health import CheckState, HealthService
from dw_api.main import create_app
from dw_api.settings import ApiSettings
from dw_kernel.errors import NotFoundError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.ports import FixedClock, SystemClock, Uuid4Generator
from dw_platform.adapters.identity.dev_token import DevTokenVerifier
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.entitlement import DEFAULT_PLANS, PlanEntitlementService
from dw_platform.application.idempotency import (
    HttpIdempotency,
    RequestFingerprint,
    ReservedKey,
    StoredResponse,
)
from dw_platform.application.identity import DbAccessContextFactory, MembershipAccess
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.storage.minio_case_documents import MinioCaseDocumentStorage
from dw_supply_chain.application.case_documents import (
    DownloadCaseDocument,
    ListCaseDocuments,
    UploadCaseDocument,
)
from dw_supply_chain.application.handlers import DOCUMENT_READ, DOCUMENT_WRITE
from dw_supply_chain.application.ports import NewCaseDocument
from dw_supply_chain.domain.case_document import (
    CaseDocument,
    CaseDocumentId,
    CaseKind,
    DocumentType,
)
from dw_supply_chain.domain.po_case import POCase, POCaseId
from dw_supply_chain.presentation.document_routes import MULTIPART_OVERHEAD_BYTES

pytestmark = pytest.mark.unit

SECRET = "unit-test-secret-0123456789abcdef"
TENANT, WORKSPACE, OTHER_TENANT, OTHER_WORKSPACE = (uuid.uuid4() for _ in range(4))
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
PDF = b"%PDF-1.7\n" + b"x" * 200
MAX_BYTES = 2048
BOTH = frozenset({DOCUMENT_READ, DOCUMENT_WRITE})


class FakeMembershipLookup:
    def __init__(self, scopes: frozenset[str]) -> None:
        self._scopes = scopes

    async def find_access(
        self, subject: str, issuer: str, tenant_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> MembershipAccess | None:
        if tenant_id != TENANT or workspace_id != WORKSPACE:
            return None
        return MembershipAccess(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            principal_id=uuid.uuid4(),
            roles=frozenset({"member"}),
            scopes=self._scopes,
            groups=frozenset(),
            clearance="internal",
            plan_id="professional",
            feature_flags=frozenset(),
        )


class FakeCases:
    def __init__(self, *cases: POCase) -> None:
        self.by_id = {case.id.value: case for case in cases}

    async def get(self, context: AccessContext, case_id: POCaseId) -> POCase | None:
        case = self.by_id.get(case_id.value)
        return case if case is not None and case.tenant_id.value == context.tenant_id else None


@dataclass
class FakeDocuments:
    rows: list[CaseDocument] = field(default_factory=list)

    def _visible(self, context: AccessContext, row: CaseDocument) -> bool:
        return (row.tenant_id, row.workspace_id) == (context.tenant_id, context.workspace_id)

    async def add(
        self, context: AccessContext, document: NewCaseDocument, *, audit: AuditEvent
    ) -> CaseDocument:
        version = 1 + max(
            (
                r.version
                for r in self.rows
                if r.case_id == document.case_id and r.doc_type is document.doc_type
            ),
            default=0,
        )
        row = CaseDocument(
            id=document.id,
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            case_kind=CaseKind.PO,
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
        return row

    async def list_for_case(self, context: AccessContext, case_id: uuid.UUID) -> list[CaseDocument]:
        return [r for r in self.rows if self._visible(context, r) and r.case_id == case_id]

    async def get(self, context: AccessContext, document_id: CaseDocumentId) -> CaseDocument | None:
        return next(
            (r for r in self.rows if r.id == document_id and self._visible(context, r)), None
        )


@dataclass
class FakeBucket:
    objects: dict[str, bytes] = field(default_factory=dict)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = data

    async def get(self, key: str) -> bytes:
        if key not in self.objects:
            raise NotFoundError("object not found")
        return self.objects[key]

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)


class MemoryStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[uuid.UUID, str], ReservedKey] = {}

    async def reserve(
        self, context: AccessContext, *, key: str, fingerprint: RequestFingerprint
    ) -> ReservedKey | None:
        existing = self.rows.get((context.tenant_id, key))
        if existing is not None:
            return existing
        self.rows[(context.tenant_id, key)] = ReservedKey(fingerprint=fingerprint, response=None)
        return None

    async def take_over(self, context: AccessContext, *, key: str, older_than: object) -> bool:
        return False

    async def complete(self, context: AccessContext, *, key: str, response: StoredResponse) -> None:
        row = self.rows[(context.tenant_id, key)]
        self.rows[(context.tenant_id, key)] = ReservedKey(
            fingerprint=row.fingerprint, response=response
        )

    async def release(self, context: AccessContext, *, key: str) -> None:
        row = self.rows.get((context.tenant_id, key))
        if row is not None and row.response is None:
            del self.rows[(context.tenant_id, key)]


def _case(tenant: uuid.UUID = TENANT, workspace: uuid.UUID = WORKSPACE) -> POCase:
    return POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(tenant),
        workspace_id=WorkspaceId(workspace),
        po_reference="PO-1",
        supplier_name="NCC",
    )


@dataclass
class World:
    cases: FakeCases
    documents: FakeDocuments = field(default_factory=FakeDocuments)
    bucket: FakeBucket = field(default_factory=FakeBucket)
    store: MemoryStore = field(default_factory=MemoryStore)

    def container(self, scopes: frozenset[str] = BOTH, *, mount: bool = True) -> ApiContainer:
        async def ok_probe() -> CheckState:
            return "ok"

        authz = ScopeAuthorizationService()
        container = ApiContainer(
            settings=ApiSettings(profile="test", dev_secret=SECRET),
            engine=None,
            health_service=HealthService(probes={"database": ok_probe}),
            token_verifier=DevTokenVerifier(SECRET),
            access_context_factory=DbAccessContextFactory(FakeMembershipLookup(scopes)),
            identity_bootstrap=None,
            uow_factory=None,
            authorization=authz,
            entitlement=PlanEntitlementService(DEFAULT_PLANS),
            idempotency=HttpIdempotency(store=self.store, clock=SystemClock()),
        )
        if mount:
            container.supply_chain_upload_case_document = UploadCaseDocument(
                cases=self.cases,
                documents=self.documents,
                storage=self.bucket,
                authz=authz,
                ids=Uuid4Generator(),
                clock=FixedClock(NOW),
                max_bytes=MAX_BYTES,
            )
            container.supply_chain_list_case_documents = ListCaseDocuments(
                cases=self.cases, documents=self.documents, authz=authz
            )
            container.supply_chain_download_case_document = DownloadCaseDocument(
                documents=self.documents, storage=self.bucket, authz=authz
            )
        return container


def _headers(key: str | None = None) -> dict[str, str]:
    token = DevTokenVerifier(SECRET).issue("dev|member", email="member@fpt.com")
    sent = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": str(TENANT),
        "X-Workspace-Id": str(WORKSPACE),
    }
    if key is not None:
        sent["Idempotency-Key"] = key
    return sent


def _multipart(
    *,
    boundary: str,
    doc_type: str = "purchase_order",
    filename: str = "PO 12.pdf",
    content_type: str = "application/pdf",
    data: bytes = PDF,
) -> tuple[bytes, str]:
    """A multipart body built by hand, so the boundary is the test's choice."""
    b = boundary.encode()
    body = (
        b"--" + b + b"\r\n"
        b'Content-Disposition: form-data; name="doc_type"\r\n\r\n'
        + doc_type.encode()
        + b"\r\n--"
        + b
        + b"\r\n"
        + b'Content-Disposition: form-data; name="file"; filename="'
        + filename.encode()
        + b'"\r\nContent-Type: '
        + content_type.encode()
        + b"\r\n\r\n"
        + data
        + b"\r\n--"
        + b
        + b"--\r\n"
    )
    return body, f"multipart/form-data; boundary={boundary}"


async def _send(
    container: ApiContainer, requests: list[tuple[str, str, dict[str, object]]]
) -> list[httpx.Response]:
    app = create_app(container)
    responses = []
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for method, path, kw in requests:
                responses.append(await client.request(method, path, **kw))  # type: ignore[arg-type]
    return responses


def _upload(
    case: POCase, *, key: str | None = None, boundary: str = "b0undary", **fields: object
) -> tuple[str, str, dict[str, object]]:
    body, content_type = _multipart(boundary=boundary, **fields)  # type: ignore[arg-type]
    return (
        "POST",
        f"/api/v1/supply-chain/po-cases/{case.id}/documents",
        {"content": body, "headers": {**_headers(key), "Content-Type": content_type}},
    )


def _get(path: str) -> tuple[str, str, dict[str, object]]:
    return ("GET", path, {"headers": _headers()})


# --- upload ------------------------------------------------------------------


async def test_an_upload_is_201_and_lands_under_the_callers_prefix() -> None:
    case = _case()
    world = World(FakeCases(case))

    (response,) = await _send(world.container(), [_upload(case, filename="../../x/PO.pdf")])

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["doc_type"] == "purchase_order" and body["version"] == 1
    assert body["filename"] == "PO.pdf"
    assert "object_key" not in body
    (key,) = world.bucket.objects
    assert key == f"supply_chain/{TENANT}/{WORKSPACE}/po/{case.id}/{body['id']}"


@pytest.mark.parametrize("owner", [(OTHER_TENANT, WORKSPACE), (TENANT, OTHER_WORKSPACE)])
async def test_uploading_to_another_tenants_or_workspaces_case_is_404_and_stores_nothing(
    owner: tuple[uuid.UUID, uuid.UUID],
) -> None:
    case = _case(*owner)
    world = World(FakeCases(case))

    (response,) = await _send(world.container(), [_upload(case)])

    assert response.status_code == 404
    assert world.bucket.objects == {} and world.documents.rows == []


async def test_uploading_without_the_write_scope_is_403() -> None:
    case = _case()
    world = World(FakeCases(case))

    (response,) = await _send(world.container(frozenset({DOCUMENT_READ})), [_upload(case)])

    assert response.status_code == 403
    assert world.bucket.objects == {}


async def _post_counting_body_reads(
    container: ApiContainer,
    path: str,
    headers: list[tuple[bytes, bytes]],
    body: bytes | None = None,
) -> tuple[int, int]:
    """POST straight through the ASGI app, recording every read of the body.
    Returns the status and how many times the body was read. Without `body`
    the body never ends, so a route that starts reading it is caught."""
    app = create_app(container)
    reads = 0
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        nonlocal reads
        reads += 1
        if body is not None:
            if reads == 1:
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}
        if reads > 64:
            raise AssertionError("the body is being read")
        return {"type": "http.request", "body": b"x" * 65536, "more_body": True}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test"), *headers],
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
    }
    async with LifespanManager(app):
        await app(scope, receive, send)  # type: ignore[arg-type]
    (start,) = [m for m in sent if m["type"] == "http.response.start"]
    return int(start["status"]), reads  # type: ignore[call-overload]


# What the upload route's header check allows: the file cap plus the framing.
BODY_CAP = MAX_BYTES + MULTIPART_OVERHEAD_BYTES


@pytest.mark.parametrize(
    "length_headers",
    [
        [(b"content-length", str(10 * 1024 * 1024 * 1024).encode())],
        [(b"content-length", str(BODY_CAP + 1).encode())],
        [(b"transfer-encoding", b"chunked")],
        # h11 reads a body carrying both as chunked, so the small declared
        # length would bound nothing: the pair is refused, not the length read.
        [(b"content-length", b"10"), (b"transfer-encoding", b"chunked")],
    ],
    ids=[
        "declared-far-over-the-cap",
        "declared-one-byte-over-the-cap",
        "undeclared-length",
        "declared-length-and-chunked",
    ],
)
async def test_an_oversized_or_unbounded_body_is_413_before_auth_and_never_read(
    length_headers: list[tuple[bytes, bytes]],
) -> None:
    """Form parsing runs before every dependency, authentication included, and
    spools a file part to disk however large; the route's cap comes first."""
    case = _case()
    world = World(FakeCases(case))

    status, reads = await _post_counting_body_reads(
        world.container(),
        f"/api/v1/supply-chain/po-cases/{case.id}/documents",
        [(b"content-type", b"multipart/form-data; boundary=x"), *length_headers],
    )

    assert status == 413
    assert reads == 0
    assert world.bucket.objects == {}


async def test_a_body_declared_at_exactly_the_cap_is_read() -> None:
    """The other side of the boundary: a body of exactly the cap passes the
    header check and reaches the parser (which then rejects this one's
    framing, which is beside the point)."""
    case = _case()
    world = World(FakeCases(case))

    status, reads = await _post_counting_body_reads(
        world.container(),
        f"/api/v1/supply-chain/po-cases/{case.id}/documents",
        [
            (b"content-type", b"multipart/form-data; boundary=x"),
            (b"content-length", str(BODY_CAP).encode()),
        ],
        body=b"x" * BODY_CAP,
    )

    assert status != 413
    assert reads >= 1
    assert world.bucket.objects == {}


async def test_the_route_holds_at_most_one_byte_past_the_cap_in_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    world = World(FakeCases(case))
    asked: list[int] = []
    real_read = StarletteUploadFile.read

    async def recording_read(self: StarletteUploadFile, size: int = -1) -> bytes:
        asked.append(size)
        return await real_read(self, size)

    monkeypatch.setattr(StarletteUploadFile, "read", recording_read)

    (response,) = await _send(world.container(), [_upload(case)])

    assert response.status_code == 201, response.text
    assert asked == [MAX_BYTES + 1]


async def test_a_file_over_the_cap_is_413() -> None:
    case = _case()
    world = World(FakeCases(case))

    (response,) = await _send(world.container(), [_upload(case, data=b"%PDF-" + b"x" * MAX_BYTES)])

    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"
    assert world.bucket.objects == {}


@pytest.mark.parametrize(
    ("content_type", "data"),
    [("text/html", b"<html></html>"), ("application/pdf", b"<script>alert(1)</script>")],
)
async def test_a_type_off_the_list_or_against_its_bytes_is_415(
    content_type: str, data: bytes
) -> None:
    case = _case()
    world = World(FakeCases(case))

    (response,) = await _send(
        world.container(), [_upload(case, content_type=content_type, data=data)]
    )

    assert response.status_code == 415
    assert response.json()["code"] == "unsupported_media_type"
    assert world.bucket.objects == {}


async def test_an_unknown_doc_type_is_422() -> None:
    case = _case()
    world = World(FakeCases(case))

    (response,) = await _send(world.container(), [_upload(case, doc_type="invoice")])

    assert response.status_code == 422
    assert world.bucket.objects == {}


# --- idempotency on a multipart route ----------------------------------------


async def test_a_retry_with_another_boundary_replays_the_first_answer_and_adds_nothing() -> None:
    """A browser picks a new boundary on every send, so the raw body of a retry
    never matches; the fingerprint is the parsed fields and the file's hash."""
    case = _case()
    world = World(FakeCases(case))

    first, second = await _send(
        world.container(),
        [
            _upload(case, key="upload-1", boundary="first-boundary-aaaa"),
            _upload(case, key="upload-1", boundary="second-boundary-bbbb"),
        ],
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 201, "the replay reproduces the original status"
    assert second.json() == first.json()
    assert second.headers["Idempotency-Key"] == "upload-1"
    assert len(world.documents.rows) == 1
    assert len(world.bucket.objects) == 1


async def test_the_same_key_with_other_bytes_is_a_conflict() -> None:
    case = _case()
    world = World(FakeCases(case))

    first, clashing = await _send(
        world.container(),
        [
            _upload(case, key="upload-2"),
            _upload(case, key="upload-2", data=PDF + b"changed"),
        ],
    )

    assert first.status_code == 201
    assert clashing.status_code == 409
    assert clashing.json()["code"] == "idempotency_conflict"
    assert len(world.documents.rows) == 1


async def test_without_a_key_two_uploads_are_two_versions() -> None:
    case = _case()
    world = World(FakeCases(case))

    first, second = await _send(world.container(), [_upload(case), _upload(case)])

    assert (first.json()["version"], second.json()["version"]) == (1, 2)


# --- list and download -------------------------------------------------------


async def test_the_list_shows_the_cases_documents() -> None:
    case = _case()
    world = World(FakeCases(case))

    _, listed = await _send(
        world.container(),
        [_upload(case), _get(f"/api/v1/supply-chain/po-cases/{case.id}/documents")],
    )

    assert listed.status_code == 200
    assert [d["version"] for d in listed.json()] == [1]


@pytest.mark.parametrize("owner", [(OTHER_TENANT, WORKSPACE), (TENANT, OTHER_WORKSPACE)])
async def test_listing_another_tenants_or_workspaces_case_is_404(
    owner: tuple[uuid.UUID, uuid.UUID],
) -> None:
    case = _case(*owner)
    world = World(FakeCases(case))

    (response,) = await _send(
        world.container(), [_get(f"/api/v1/supply-chain/po-cases/{case.id}/documents")]
    )

    assert response.status_code == 404


async def test_a_download_is_an_attachment_of_the_stored_type_never_sniffed() -> None:
    case = _case()
    world = World(FakeCases(case))
    (uploaded,) = await _send(world.container(), [_upload(case, filename='Biên "bản" 1.pdf')])
    document_id = uploaded.json()["id"]

    (response,) = await _send(
        world.container(), [_get(f"/api/v1/supply-chain/documents/{document_id}/content")]
    )

    assert response.status_code == 200
    assert response.content == PDF
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["x-content-type-options"] == "nosniff"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert 'filename="Bien ban 1.pdf"' in disposition
    assert "filename*=UTF-8''Bi%C3%AAn%20%22b%E1%BA%A3n%22%201.pdf" in disposition


@pytest.mark.parametrize("owner", [(OTHER_TENANT, WORKSPACE), (TENANT, OTHER_WORKSPACE)])
async def test_downloading_another_tenants_or_workspaces_document_is_404(
    owner: tuple[uuid.UUID, uuid.UUID],
) -> None:
    case = _case(*owner)
    world = World(FakeCases(case))
    tenant, workspace = owner
    foreign = CaseDocument(
        id=CaseDocumentId(uuid.uuid4()),
        tenant_id=tenant,
        workspace_id=workspace,
        case_kind=CaseKind.PO,
        case_id=case.id.value,
        doc_type=DocumentType.PURCHASE_ORDER,
        object_key="supply_chain/elsewhere",
        filename="x.pdf",
        content_type="application/pdf",
        size_bytes=len(PDF),
        sha256="0" * 64,
        version=1,
        uploaded_by=uuid.uuid4(),
        uploaded_at=NOW,
    )
    world.documents.rows.append(foreign)
    world.bucket.objects["supply_chain/elsewhere"] = PDF

    (response,) = await _send(
        world.container(), [_get(f"/api/v1/supply-chain/documents/{foreign.id}/content")]
    )

    assert response.status_code == 404
    assert response.content != PDF


class _FailingMinio:
    """The S3 client under the real adapter, answering every read with
    `error`: what the API says when the bucket does not have the bytes."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def get_object(self, bucket: str, key: str) -> object:
        raise self.error


def _missing() -> S3Error:
    return S3Error(None, "NoSuchKey", "gone", "/case-documents/k", "r", "h")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("error", "status"),
    [(_missing(), 404), (MinioException("connection reset"), 503)],
    ids=["object-missing", "storage-down"],
)
async def test_a_storage_failure_on_download_keeps_the_object_key_on_the_server(
    error: Exception, status: int
) -> None:
    case = _case()
    world = World(FakeCases(case))
    (uploaded,) = await _send(world.container(), [_upload(case)])
    container = world.container()
    assert container.supply_chain_download_case_document is not None
    container.supply_chain_download_case_document = DownloadCaseDocument(
        documents=world.documents,
        storage=MinioCaseDocumentStorage(client=_FailingMinio(error), bucket="case-documents"),  # type: ignore[arg-type]
        authz=ScopeAuthorizationService(),
    )

    (response,) = await _send(
        container, [_get(f"/api/v1/supply-chain/documents/{uploaded.json()['id']}/content")]
    )

    assert response.status_code == status
    assert "supply_chain/" not in response.text
    assert str(TENANT) not in response.text


# --- mounting ----------------------------------------------------------------


async def test_the_documents_router_mounts_on_its_own_guard() -> None:
    """With only the document handlers wired, the documents routes answer and
    the rest of the Supply Chain API is absent; without them, the reverse."""
    case = _case()
    world = World(FakeCases(case))
    path = f"/api/v1/supply-chain/po-cases/{case.id}/documents"

    listed, cases = await _send(
        world.container(), [_get(path), _get("/api/v1/supply-chain/po-cases")]
    )
    assert listed.status_code == 200
    assert cases.status_code == 404

    (unmounted,) = await _send(world.container(mount=False), [_get(path)])
    assert unmounted.status_code == 404
