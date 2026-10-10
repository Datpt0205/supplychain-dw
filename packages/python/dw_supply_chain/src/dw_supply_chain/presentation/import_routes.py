"""HTTP surface of the one-time import (ADR 0027; ticket onboarding/01).
Decisions live in `application.data_import`.

`GET /imports/template` — the empty Excel template. `POST /imports/dry-run` —
a workbook (multipart) settled row by row without writing anything.
`POST /imports` — the same workbook written for real (an `Idempotency-Key`
replays the first answer; running the same file again adds nothing either
way). Each answers the report: counts per sheet, then every row.

No `from __future__ import annotations`, for the reason `routes.py` gives.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, File, Response, UploadFile
from pydantic import BaseModel

from dw_platform.application.access_context import AccessContext
from dw_supply_chain.application.data_import import GetImportTemplate, ImportSupplyChainData
from dw_supply_chain.domain.data_import import (
    MAX_IMPORT_BYTES,
    SHEETS,
    TEMPLATE_CONTENT_TYPE,
    TEMPLATE_FILENAME,
    ImportReport,
    ImportSheet,
    RowStatus,
)
from dw_supply_chain.presentation.document_routes import (
    MULTIPART_OVERHEAD_BYTES,
    SupportsFormIdempotency,
    body_capped_route,
)

AccessContextResolver = Callable[..., object]
IdempotencyResolver = Callable[..., object]


class ImportRowView(BaseModel):
    sheet: ImportSheet
    # The Excel row number; the header is row 1.
    row: int
    key: str
    status: RowStatus
    messages: list[str]


class ImportProblemView(BaseModel):
    sheet: ImportSheet
    message: str


class ImportSheetCountView(BaseModel):
    sheet: ImportSheet
    title: str
    created: int
    exists: int
    partial: int
    rejected: int


class ImportReportView(BaseModel):
    # True: nothing was written; `created` reads "would be created".
    dry_run: bool
    sheets: list[ImportSheetCountView]
    problems: list[ImportProblemView]
    rows: list[ImportRowView]


def _report(report: ImportReport) -> ImportReportView:
    return ImportReportView(
        dry_run=report.dry_run,
        sheets=[
            ImportSheetCountView(
                sheet=spec.sheet,
                title=spec.title,
                created=report.count(spec.sheet, RowStatus.CREATED),
                exists=report.count(spec.sheet, RowStatus.EXISTS),
                partial=report.count(spec.sheet, RowStatus.PARTIAL),
                rejected=report.count(spec.sheet, RowStatus.REJECTED),
            )
            for spec in SHEETS
        ],
        problems=[ImportProblemView(sheet=p.sheet, message=p.message) for p in report.problems],
        rows=[
            ImportRowView(
                sheet=r.sheet, row=r.row, key=r.key, status=r.status, messages=list(r.messages)
            )
            for r in report.rows
        ],
    )


@dataclass(frozen=True)
class ImportHandlers:
    run: ImportSupplyChainData
    template: GetImportTemplate


def build_import_router(
    handlers: ImportHandlers,
    *,
    resolve_access_context: AccessContextResolver,
    resolve_form_idempotency: IdempotencyResolver,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/supply-chain", tags=["supply_chain"])
    require_access_context = Annotated[AccessContext, Depends(resolve_access_context)]
    require_form_idempotency = Annotated[SupportsFormIdempotency, Depends(resolve_form_idempotency)]
    h = handlers
    capped = body_capped_route(MAX_IMPORT_BYTES + MULTIPART_OVERHEAD_BYTES)

    @router.get(
        "/imports/template",
        response_class=Response,
        responses={200: {"content": {TEMPLATE_CONTENT_TYPE: {}}}},
    )
    async def get_import_template(context: require_access_context) -> Response:
        return Response(
            content=await h.template.handle(context),
            media_type=TEMPLATE_CONTENT_TYPE,
            headers={"Content-Disposition": f'attachment; filename="{TEMPLATE_FILENAME}"'},
        )

    async def dry_run_import(
        context: require_access_context,
        file: Annotated[UploadFile, File()],
    ) -> ImportReportView:
        data = await file.read(MAX_IMPORT_BYTES + 1)
        return _report(await h.run.handle(context, data, apply=False))

    router.add_api_route(
        "/imports/dry-run",
        dry_run_import,
        methods=["POST"],
        name="dry_run_import",
        response_model=ImportReportView,
        route_class_override=capped,
    )

    async def apply_import(
        context: require_access_context,
        idempotency: require_form_idempotency,
        file: Annotated[UploadFile, File()],
    ) -> ImportReportView:
        data = await file.read(MAX_IMPORT_BYTES + 1)
        await idempotency.claim_fields({"sha256": hashlib.sha256(data).hexdigest()})
        view = _report(await h.run.handle(context, data, apply=True))
        recorded: ImportReportView = await idempotency.record(view)
        return recorded

    router.add_api_route(
        "/imports",
        apply_import,
        methods=["POST"],
        name="apply_import",
        response_model=ImportReportView,
        route_class_override=capped,
    )

    return router
