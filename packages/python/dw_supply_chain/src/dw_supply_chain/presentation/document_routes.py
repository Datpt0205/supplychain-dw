"""HTTP surface of case documents (ADR 0021). Decisions live in the handlers.

A router of its own, mounted by the composition root on its own guard (the
three document handlers), rather than added to the Supply Chain router's
argument list.

The upload's size is bounded twice. FastAPI parses a multipart form before any
dependency runs, authentication included, and Starlette spools a file part to
a temporary file however large it is. So the upload route refuses a body over
the cap (plus room for the multipart framing) or of undeclared length from its
headers, before anything is parsed; the handler then refuses a file over the
exact cap.

No `from __future__ import annotations`, for the reason `routes.py` gives: the
dependency annotations close over `build_documents_router`'s parameters.
"""

import hashlib
import re
import unicodedata
import uuid
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from datetime import datetime
from typing import Annotated, Any, Protocol, TypeVar
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.routing import APIRoute
from pydantic import BaseModel

from dw_kernel.errors import PayloadTooLargeError
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.application.case_documents import (
    DownloadCaseDocument,
    ListCaseDocuments,
    UploadCaseDocument,
)
from dw_supply_chain.domain.case_document import (
    CaseDocument,
    CaseDocumentId,
    CaseKind,
    DocumentType,
)

AccessContextResolver = Callable[..., Awaitable[AccessContext]]
IdempotencyResolver = Callable[..., object]

_ResultT = TypeVar("_ResultT", bound=BaseModel)


class SupportsFormIdempotency(Protocol):
    """What a multipart route needs from `dw_api`'s `IdempotentOperation`:
    claim the key from the parsed fields, then record the answer."""

    async def claim_fields(self, fields: Mapping[str, str]) -> None: ...

    async def record(self, result: _ResultT, *, status_code: int = 200) -> _ResultT: ...


class CaseDocumentView(BaseModel):
    """A document as the API shows it. The object key stays on the server."""

    id: uuid.UUID
    # Which case the document belongs to: a PO case or a product-development case.
    case_kind: CaseKind
    case_id: uuid.UUID
    doc_type: DocumentType
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    version: int
    uploaded_by: uuid.UUID
    uploaded_at: datetime


def _view(document: CaseDocument) -> CaseDocumentView:
    return CaseDocumentView(
        id=document.id.value,
        case_kind=document.case_kind,
        case_id=document.case_id,
        doc_type=document.doc_type,
        filename=document.filename,
        content_type=document.content_type,
        size_bytes=document.size_bytes,
        sha256=document.sha256,
        version=document.version,
        uploaded_by=document.uploaded_by,
        uploaded_at=document.uploaded_at,
    )


_NOT_IN_A_QUOTED_STRING = re.compile(r'[\x00-\x1f\x7f"\\]')


def content_disposition(filename: str) -> str:
    """`attachment`, always: a stored file is never rendered inline.

    `filename*` (RFC 5987) carries the real name, percent-encoded UTF-8; the
    plain `filename` is an ASCII fallback with diacritics folded and every
    character that could end the quoted string or the header line removed."""
    folded = unicodedata.normalize("NFKD", filename.replace("đ", "d").replace("Đ", "D"))
    ascii_name = folded.encode("ascii", "ignore").decode("ascii")
    fallback = _NOT_IN_A_QUOTED_STRING.sub("", ascii_name).strip() or "document"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


# Room for what a multipart body carries besides the file: the boundary lines,
# each part's headers (the file name among them) and the `doc_type` field.
# Generous, because it only has to keep the body bounded; the handler holds the
# file itself to the exact cap.
MULTIPART_OVERHEAD_BYTES = 64 * 1024


def body_capped_route(limit: int) -> type[APIRoute]:
    """A route class that refuses a request body over `limit` bytes, or of
    undeclared length, from its headers alone: before FastAPI parses the form
    and before any dependency (authentication included) runs.

    A declared `Content-Length` is what bounds the body, because the server
    reads exactly that many bytes as this request. A chunked body declares
    none, so it is refused rather than counted; browsers and HTTP clients send
    a length for a form they built in memory."""

    class BodyCappedRoute(APIRoute):
        def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
            handle = super().get_route_handler()

            async def capped(request: Request) -> Response:
                declared = request.headers.get("content-length", "")
                if "transfer-encoding" in request.headers or not declared.isdigit():
                    raise PayloadTooLargeError(
                        "yêu cầu tải lên phải khai báo Content-Length",
                        details={"max_bytes": limit},
                    )
                if int(declared) > limit:
                    raise PayloadTooLargeError(
                        f"yêu cầu tối đa {limit} byte", details={"max_bytes": limit}
                    )
                return await handle(request)

            return capped

    return BodyCappedRoute


# Each kind of case and the path its documents hang under, with the name its
# routes take (the PO routes keep the names slice D gave them, so their
# operation ids do not move).
_CASE_ROUTES: dict[CaseKind, tuple[str, str]] = {
    CaseKind.PO: ("po-cases", "case_document"),
    CaseKind.PRODUCT: ("product-cases", "product_case_document"),
}


def _add_case_routes(
    router: APIRouter,
    kind: CaseKind,
    *,
    segment: str,
    name: str,
    upload: UploadCaseDocument,
    list_documents: ListCaseDocuments,
    require_access_context: Any,
    require_idempotency: Any,
) -> None:
    """Upload and list for one kind of case: the same handlers, told which kind."""

    async def upload_document(
        case_id: uuid.UUID,
        context: require_access_context,
        idempotency: require_idempotency,
        doc_type: Annotated[DocumentType, Form()],
        file: Annotated[UploadFile, File()],
    ) -> CaseDocumentView:
        # One byte past the cap is enough for the handler to know it is over,
        # so no more than that is ever held in memory. The parser has already
        # spooled the part to a temporary file, bounded by the route's cap.
        data = await file.read(upload.max_bytes + 1)
        await idempotency.claim_fields(
            {
                "case_id": str(case_id),
                "doc_type": doc_type.value,
                "filename": file.filename or "",
                "content_type": file.content_type or "",
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
        document = await upload.handle(
            context,
            kind,
            case_id,
            doc_type=doc_type,
            filename=file.filename or "",
            declared_content_type=file.content_type or "",
            data=data,
        )
        recorded: CaseDocumentView = await idempotency.record(_view(document), status_code=201)
        return recorded

    async def list_documents_route(
        case_id: uuid.UUID, context: require_access_context
    ) -> list[CaseDocumentView]:
        return [_view(d) for d in await list_documents.handle(context, kind, case_id)]

    router.add_api_route(
        f"/{segment}/{{case_id}}/documents",
        upload_document,
        methods=["POST"],
        name=f"upload_{name}",
        response_model=CaseDocumentView,
        status_code=201,
        route_class_override=body_capped_route(upload.max_bytes + MULTIPART_OVERHEAD_BYTES),
    )
    router.add_api_route(
        f"/{segment}/{{case_id}}/documents",
        list_documents_route,
        methods=["GET"],
        name=f"list_{name}s",
        response_model=list[CaseDocumentView],
    )


def build_documents_router(
    upload: UploadCaseDocument,
    list_documents: ListCaseDocuments,
    download: DownloadCaseDocument,
    *,
    resolve_access_context: AccessContextResolver,
    resolve_form_idempotency: IdempotencyResolver,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/supply-chain", tags=["supply_chain"])
    require_access_context = Annotated[AccessContext, Depends(resolve_access_context)]
    require_idempotency = Annotated[SupportsFormIdempotency, Depends(resolve_form_idempotency)]

    for kind, (segment, name) in _CASE_ROUTES.items():
        _add_case_routes(
            router,
            kind,
            segment=segment,
            name=name,
            upload=upload,
            list_documents=list_documents,
            require_access_context=require_access_context,
            require_idempotency=require_idempotency,
        )

    @router.get(
        "/documents/{document_id}/content",
        response_class=Response,
        responses={200: {"description": "The file, as an attachment of its stored type."}},
    )
    async def download_case_document(
        document_id: uuid.UUID, context: require_access_context
    ) -> Response:
        document, data = await download.handle(context, CaseDocumentId(document_id))
        return Response(
            content=data,
            media_type=document.content_type,
            headers={
                "Content-Disposition": content_disposition(document.filename),
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
            },
        )

    return router
