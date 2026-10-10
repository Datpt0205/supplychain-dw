"""Maps kernel error taxonomy onto the unified HTTP error schema."""

from __future__ import annotations

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from dw_api.dependencies.idempotency import IDEMPOTENCY_HEADER, ReplayedResponse
from dw_api.errors import ErrorResponse, status_for
from dw_kernel.errors import DWError, ErrorCode


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ReplayedResponse)
    async def handle_replay(request: Request, exc: ReplayedResponse) -> Response:
        """Return what the first request returned, byte for byte.

        The handler never ran, so there is nothing to serialise: the stored body
        is sent back as it was stored. The header is echoed so a client (or a
        proxy log) can tell a replay from a first attempt.
        """
        stored = exc.response
        headers = {IDEMPOTENCY_HEADER: request.headers.get(IDEMPOTENCY_HEADER, "")}
        if stored.body is None:
            return Response(status_code=stored.status_code, headers=headers)
        return JSONResponse(status_code=stored.status_code, content=stored.body, headers=headers)

    @app.exception_handler(DWError)
    async def handle_dw_error(request: Request, exc: DWError) -> JSONResponse:
        body = ErrorResponse(
            code=exc.code.value,
            message=exc.message,
            details={k: str(v) for k, v in exc.details.items()},
            request_id=getattr(request.state, "request_id", None),
        )
        status = status_for(exc.code)
        # Every 401 names the scheme that would satisfy it (RFC 9110 15.5.2).
        headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
        return JSONResponse(status_code=status, content=body.model_dump(), headers=headers)

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Never leak internals; taxonomy code only.
        body = ErrorResponse(
            code=ErrorCode.INTERNAL.value,
            message="internal error",
            request_id=getattr(request.state, "request_id", None),
        )
        return JSONResponse(status_code=500, content=body.model_dump())
