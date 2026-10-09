"""HTTP surface of document drafts and the tenant's templates (ADR 0025 point
4; ticket ai-automation/03). Decisions live in `application.document_drafts`.

`GET /po-cases/{id}/drafts`, `GET /product-cases/{id}/drafts` — the latest
version of each draft of a case. `GET /drafts/{id}` — one version.
`POST /drafts/{id}/revisions` — a person's edit, the next version.
`POST /drafts/{id}/rejection` — rejected, with its reason.
`GET /drafts/{id}/file` — the draft as its template prints it (DOCX), prices
hidden for a caller who may not read them.
`GET /doc-templates`, `PUT /doc-templates` — the platform's templates and the
tenant's own versions (a declaration and a DOCX, multipart).

A price the caller may not read is `value: null, redacted: true` (a table: its
price columns listed in `redacted_columns`), never a number.

No `from __future__ import annotations`, for the reason `routes.py` gives.
"""

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from dw_agent_runtime.doc_templates import TemplateFieldKind
from dw_kernel.errors import PayloadTooLargeError
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.application.document_drafts import (
    DraftReading,
    GetDocumentDraft,
    ListCaseDrafts,
    ListDocTemplates,
    RejectDocumentDraft,
    RenderDocumentDraft,
    ReviseDocumentDraft,
    SetDocTemplateOverride,
)
from dw_supply_chain.domain.case_document import CaseKind, DocumentType
from dw_supply_chain.domain.document_draft import DraftStatus
from dw_supply_chain.presentation.document_routes import (
    MULTIPART_OVERHEAD_BYTES,
    body_capped_route,
    content_disposition,
)
from dw_supply_chain.presentation.routes import SupportsIdempotentRecord

AccessContextResolver = Callable[..., object]
IdempotencyResolver = Callable[..., object]

# A template upload: a declaration and a DOCX, each well under this.
TEMPLATE_MAX_BYTES = 5 * 1024 * 1024


class DraftColumnView(BaseModel):
    name: str
    label: str
    kind: TemplateFieldKind


class DraftFieldSourceView(BaseModel):
    """Where a value came from: a document and the quote read in it, or the
    person who typed it. Null: computed by code from stored data."""

    document_id: uuid.UUID | None
    quote: str | None
    edited_by: uuid.UUID | None


class DraftFieldView(BaseModel):
    name: str
    label: str
    kind: TemplateFieldKind
    required: bool
    value: str | None
    rows: list[dict[str, str | None]] | None
    columns: list[DraftColumnView] | None
    redacted: bool
    redacted_columns: list[str]
    # The field, or one of its table's cells, has no value.
    gap: bool
    source: DraftFieldSourceView | None


class DraftSourceView(BaseModel):
    document_id: uuid.UUID
    sha256: str
    extraction_id: uuid.UUID | None


class DraftView(BaseModel):
    id: uuid.UUID
    lineage_id: uuid.UUID
    version: int
    case_kind: CaseKind
    case_id: uuid.UUID
    doc_type: DocumentType
    template_id: str
    template_version: str
    title: str
    status: DraftStatus
    decision_reason: str | None
    prompt_id: str | None
    prompt_version: str | None
    content_sha256: str
    gaps: list[str]
    sources: list[DraftSourceView]
    fields: list[DraftFieldView]
    prices_visible: bool
    can_edit: bool
    created_by: uuid.UUID
    created_at: datetime


def _uuid(raw: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(raw)) if raw else None
    except ValueError:
        return None


def _source(raw: object) -> DraftFieldSourceView | None:
    if not isinstance(raw, Mapping):
        return None
    return DraftFieldSourceView(
        document_id=_uuid(raw.get("document_id")),
        quote=raw.get("quote") if isinstance(raw.get("quote"), str) else None,
        edited_by=_uuid(raw.get("edited_by")),
    )


def _draft_view(reading: DraftReading) -> DraftView:
    draft, spec = reading.draft, reading.spec
    gap_roots = {gap.split("[", 1)[0] for gap in draft.gaps}
    fields = []
    for f in spec.fields:
        entry = draft.fields.get(f.name)
        entry = entry if isinstance(entry, Mapping) else {}
        value = entry.get("value")
        is_table = f.kind is TemplateFieldKind.TABLE
        fields.append(
            DraftFieldView(
                name=f.name,
                label=f.label,
                kind=f.kind,
                required=f.required,
                value=None if is_table or not isinstance(value, str) else value,
                rows=[dict(r) for r in value if isinstance(r, Mapping)]
                if is_table and isinstance(value, list)
                else ([] if is_table else None),
                columns=None
                if f.columns is None
                else [DraftColumnView(name=c.name, label=c.label, kind=c.kind) for c in f.columns],
                redacted=bool(entry.get("redacted")),
                redacted_columns=list(entry.get("redacted_columns") or []),
                gap=f.name in gap_roots,
                source=_source(entry.get("source")),
            )
        )
    return DraftView(
        id=draft.id,
        lineage_id=draft.lineage_id,
        version=draft.version,
        case_kind=draft.case_kind,
        case_id=draft.case_id,
        doc_type=draft.doc_type,
        template_id=draft.template_id,
        template_version=draft.template_version,
        title=spec.title,
        status=draft.status,
        decision_reason=draft.decision_reason,
        prompt_id=draft.prompt_id,
        prompt_version=draft.prompt_version,
        content_sha256=draft.content_sha256,
        gaps=list(draft.gaps),
        sources=[
            DraftSourceView(
                document_id=s.document_id, sha256=s.sha256, extraction_id=s.extraction_id
            )
            for s in draft.sources
        ],
        fields=fields,
        prices_visible=reading.prices_visible,
        can_edit=reading.can_edit and draft.status is DraftStatus.OPEN,
        created_by=draft.created_by,
        created_at=draft.created_at,
    )


class ReviseDraftRequest(BaseModel):
    """The fields to replace, by name; a table is its full list of rows."""

    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any] = Field(min_length=1, max_length=200)


class RejectDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=1000)


class DocTemplateView(BaseModel):
    template_id: str
    version: str
    title: str
    doc_type: str
    # The tenant has its own version of this one.
    overridden: bool


@dataclass(frozen=True)
class DraftHandlers:
    """What the composition root builds for this router."""

    list_drafts: ListCaseDrafts
    get_draft: GetDocumentDraft
    revise: ReviseDocumentDraft
    reject: RejectDocumentDraft
    render: RenderDocumentDraft
    list_templates: ListDocTemplates
    set_template: SetDocTemplateOverride


_CASE_SEGMENTS = {
    CaseKind.PO: ("po-cases", "po_case"),
    CaseKind.PRODUCT: ("product-cases", "product_case"),
}


def build_draft_router(
    handlers: DraftHandlers,
    *,
    resolve_access_context: AccessContextResolver,
    resolve_idempotency: IdempotencyResolver,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/supply-chain", tags=["supply_chain"])
    require_access_context = Annotated[AccessContext, Depends(resolve_access_context)]
    require_idempotency = Annotated[SupportsIdempotentRecord, Depends(resolve_idempotency)]
    h = handlers

    for kind, (segment, name) in _CASE_SEGMENTS.items():

        async def list_case_drafts(
            case_id: uuid.UUID, context: require_access_context, kind: CaseKind = kind
        ) -> list[DraftView]:
            return [_draft_view(r) for r in await h.list_drafts.handle(context, kind, case_id)]

        router.add_api_route(
            f"/{segment}/{{case_id}}/drafts",
            list_case_drafts,
            methods=["GET"],
            name=f"list_{name}_drafts",
            response_model=list[DraftView],
        )

    @router.get("/drafts/{draft_id}", response_model=DraftView)
    async def get_document_draft(draft_id: uuid.UUID, context: require_access_context) -> DraftView:
        return _draft_view(await h.get_draft.handle(context, draft_id))

    @router.post("/drafts/{draft_id}/revisions", response_model=DraftView, status_code=201)
    async def revise_document_draft(
        draft_id: uuid.UUID,
        body: ReviseDraftRequest,
        context: require_access_context,
        idempotency: require_idempotency,
    ) -> DraftView:
        revised = await h.revise.handle(context, draft_id, values=body.values)
        view = _draft_view(await h.get_draft.handle(context, revised.id))
        return await idempotency.record(view, status_code=201)

    @router.post("/drafts/{draft_id}/rejection", response_model=DraftView)
    async def reject_document_draft(
        draft_id: uuid.UUID,
        body: RejectDraftRequest,
        context: require_access_context,
        idempotency: require_idempotency,
    ) -> DraftView:
        await h.reject.handle(context, draft_id, reason=body.reason)
        view = _draft_view(await h.get_draft.handle(context, draft_id))
        return await idempotency.record(view)

    @router.get(
        "/drafts/{draft_id}/file",
        response_class=Response,
        responses={200: {"description": "The draft rendered by its template, as an attachment."}},
    )
    async def render_document_draft(
        draft_id: uuid.UUID, context: require_access_context
    ) -> Response:
        rendered = await h.render.handle(context, draft_id)
        return Response(
            content=rendered.file.data,
            media_type=rendered.file.content_type,
            headers={
                "Content-Disposition": content_disposition(rendered.filename),
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
            },
        )

    @router.get("/doc-templates", response_model=list[DocTemplateView])
    async def list_doc_templates(context: require_access_context) -> list[DocTemplateView]:
        return [
            DocTemplateView(
                template_id=t.spec.template_id,
                version=t.spec.version,
                title=t.spec.title,
                doc_type=t.spec.doc_type,
                overridden=t.overridden,
            )
            for t in await h.list_templates.handle(context)
        ]

    async def set_doc_template(
        context: require_access_context,
        spec: Annotated[UploadFile, File()],
        document: Annotated[UploadFile, File()],
    ) -> DocTemplateView:
        """The tenant's own version of a platform template: its declaration
        (YAML) and its DOCX. A version is never replaced; add a new one."""
        spec_bytes = await spec.read(TEMPLATE_MAX_BYTES + 1)
        docx = await document.read(TEMPLATE_MAX_BYTES + 1)
        if len(spec_bytes) > TEMPLATE_MAX_BYTES or len(docx) > TEMPLATE_MAX_BYTES:
            raise PayloadTooLargeError("mẫu tối đa 5 MB", details={"max_bytes": TEMPLATE_MAX_BYTES})
        stored = await h.set_template.handle(context, spec=spec_bytes, docx=docx)
        return DocTemplateView(
            template_id=stored.template_id,
            version=stored.version,
            title=stored.title,
            doc_type=stored.doc_type,
            overridden=True,
        )

    router.add_api_route(
        "/doc-templates",
        set_doc_template,
        methods=["PUT"],
        name="set_doc_template",
        response_model=DocTemplateView,
        route_class_override=body_capped_route(2 * TEMPLATE_MAX_BYTES + MULTIPART_OVERHEAD_BYTES),
    )

    return router
