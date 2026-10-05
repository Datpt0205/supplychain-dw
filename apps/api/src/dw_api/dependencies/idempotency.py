"""The ``Idempotency-Key`` header, enforced.

A dependency rather than middleware, because the key is scoped to a tenant and
the tenant comes from the resolved :class:`AccessContext` — which middleware
runs too early to have. The dependency also gets to be per-route, which is the
point: idempotency belongs on the mutations where a duplicate does damage, not
on every write in the API.

How a route uses it, in one line::

    async def decide(..., idempotency: RequireIdempotency) -> ApprovalView:
        view = ApprovalView(...)
        return await idempotency.record(view)

and that is the whole contract. Everything else happens around the handler:

* **No header.** Nothing is stored and ``record`` returns what it was given. The
  header is opt-in; making it mandatory would break every client integrated
  against the API as it stands.
* **First use of a key.** The dependency reserves it before the handler runs.
  ``record`` stores the status and body it returns.
* **Replay.** The stored response is raised as :class:`ReplayedResponse` from the
  dependency, so the handler never runs at all. This is the only way to return a
  response from a dependency, and it is what ``HTTPException`` does too.
* **Same key, different request.** 409 ``idempotency_conflict``, raised by the
  application service.
* **The handler fails.** ``record`` is never reached, so the exit below releases
  the reservation and a retry gets a fresh attempt. A 5xx is not a decision the
  server made about the request, it is a failure to reach one; storing it would
  turn a transient fault into a permanent answer and defeat the retry the header
  exists to make safe. The same applies to a 4xx raised out of the handler:
  nothing was created, so there is nothing to protect.
* **The handler succeeds but the route forgot to call ``record``.** The
  reservation is released, and idempotency silently degrades to the behaviour
  this module replaced. Failing open on our own wiring mistake is the safe
  direction; failing closed would reject the client's legitimate retry.

A multipart route (a file upload) cannot be fingerprinted from the raw body:
FastAPI parses a form through ``request.form()``, which consumes the stream, so
``request.body()`` in a dependency raises "Stream consumed"; and the body holds
a boundary the browser picks afresh on every send, so a retry's bytes never
match the first attempt's. Such a route depends on
:func:`get_form_idempotent_operation` and claims the key itself, from the
fields it parsed::

    await idempotency.claim_fields({"doc_type": ..., "sha256": ...})

before it acts. The rest (replay, conflict, release on failure) is the same.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Annotated, TypeVar

from fastapi import Depends, Header, Request
from pydantic import BaseModel

from dw_api.dependencies.auth import RequireAccessContext
from dw_api.dependencies.services import RequireContainer
from dw_kernel.errors import DomainError
from dw_platform.application.access_context import AccessContext
from dw_platform.application.idempotency import (
    HttpIdempotency,
    RequestFingerprint,
    StoredResponse,
)

IDEMPOTENCY_HEADER = "Idempotency-Key"

# Long enough for a UUID, a ULID or a request id from the client's own system;
# short enough that the header cannot be used as free storage. Anything longer
# is refused rather than truncated, because two keys that differ only past the
# cut would silently become one.
MAX_KEY_LENGTH = 255

ModelT = TypeVar("ModelT", bound=BaseModel)


class ReplayedResponse(Exception):  # noqa: N818 — a control-flow signal, not a failure
    """Carries a stored response out of the dependency, past the handler.

    Named without an ``Error`` suffix on purpose: nothing went wrong. The client
    asked the same question twice and is getting the same answer.
    """

    def __init__(self, response: StoredResponse) -> None:
        super().__init__("idempotent replay")
        self.response = response


class IdempotentOperation:
    """A key held for the length of one request, or an inert stand-in.

    The inert form — no header, or no database — is what keeps the routes free
    of ``if``: ``record`` returns its argument and nothing is persisted.
    """

    def __init__(
        self,
        idempotency: HttpIdempotency | None,
        context: AccessContext,
        key: str | None,
        *,
        method: str,
        target: str,
    ) -> None:
        self._idempotency = idempotency
        self._context = context
        self._key = key
        self._method = method
        self._target = target
        self._claimed = False
        self._recorded = False

    async def claim(self, fingerprint: RequestFingerprint) -> None:
        """Reserve the key, or raise the stored response past the handler."""
        if self._idempotency is None or self._key is None:
            return
        stored = await self._idempotency.claim(
            self._context, key=self._key, fingerprint=fingerprint
        )
        if stored is not None:
            raise ReplayedResponse(stored)
        self._claimed = True

    async def claim_fields(self, fields: Mapping[str, str]) -> None:
        """Reserve the key for a request identified by its parsed ``fields``.

        For a route whose body is not stable bytes (multipart). The route
        passes every field that makes the request what it is, a file as its
        hash; the same fields in any order fingerprint alike, and the method,
        path and workspace are part of it as for every other route."""
        body = json.dumps(dict(fields), sort_keys=True, ensure_ascii=False).encode()
        await self.claim(
            RequestFingerprint.of(
                method=self._method,
                path=self._target,
                workspace_id=self._context.workspace_id,
                body=body,
            )
        )

    async def record(self, result: ModelT, *, status_code: int = 200) -> ModelT:
        """Store ``result`` as this key's answer and hand it back unchanged.

        Returning the argument is what lets a route end with
        ``return await idempotency.record(view)`` instead of growing a branch.
        ``status_code`` mirrors the one declared on the route, because a replay
        has to reproduce the whole answer and 201 is part of it.
        """
        body = result.model_dump(mode="json")
        await self._store(StoredResponse(status_code=status_code, body=body))
        return result

    async def record_no_content(self) -> None:
        """Store a 204 — the answer to a route that succeeds without a body."""
        await self._store(StoredResponse(status_code=204, body=None))

    async def _store(self, response: StoredResponse) -> None:
        # Only a key this request reserved: one it never claimed may be
        # another request's reservation, and is not this request's to fill.
        if not self._claimed:
            return
        assert self._idempotency is not None and self._key is not None
        await self._idempotency.complete(self._context, key=self._key, response=response)
        self._recorded = True

    async def abandon_unless_recorded(self) -> None:
        # Never release a key this request did not reserve: that would free
        # another in-flight request's reservation.
        if not self._claimed or self._recorded:
            return
        assert self._idempotency is not None and self._key is not None
        await self._idempotency.release(self._context, key=self._key)


def _key(idempotency_key: str | None) -> str | None:
    key = (idempotency_key or "").strip()
    if len(key) > MAX_KEY_LENGTH:
        # Refused rather than truncated: the key becomes part of a primary key,
        # and two keys differing only past the cut would collapse into one.
        raise DomainError(
            f"{IDEMPOTENCY_HEADER} must be at most {MAX_KEY_LENGTH} characters",
            details={"header": IDEMPOTENCY_HEADER, "length": len(key)},
        )
    return key or None


def _target(request: Request) -> str:
    # The query string is part of what identifies the request, not decoration:
    # `DELETE /admin/members/{id}?workspace_id=A` and the same path with
    # `workspace_id=B` are two different mutations. Compared as sent — a retry
    # that reorders its parameters is answered with a conflict, which is the
    # conservative half of the two ways to be wrong.
    target = request.url.path
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return target


# Declared rather than read off ``request.headers`` so that the header appears
# in the OpenAPI schema, and therefore in the generated client, on exactly the
# routes that honour it. A contract clients are told to rely on should be
# discoverable from the contract.
_KEY_HEADER = Header(
    default=None,
    alias=IDEMPOTENCY_HEADER,
    description=(
        "Optional. Retrying with the same key returns the first response "
        "instead of acting twice; reusing it for a different request is a 409."
    ),
)


async def get_idempotent_operation(
    request: Request,
    context: RequireAccessContext,
    container: RequireContainer,
    idempotency_key: str | None = _KEY_HEADER,
) -> AsyncIterator[IdempotentOperation]:
    target = _target(request)
    operation = IdempotentOperation(
        container.idempotency,
        context,
        _key(idempotency_key),
        method=request.method,
        target=target,
    )
    # For a JSON (or empty) body FastAPI has already read it through
    # ``request.body()``, which caches the bytes, so reading it here costs
    # nothing and leaves the handler its parameters. Not for a form: FastAPI
    # parses that through ``request.form()``, which consumes the stream without
    # caching it, and this call would raise "Stream consumed". A form route
    # depends on ``get_form_idempotent_operation`` below instead.
    await operation.claim(
        RequestFingerprint.of(
            method=request.method,
            path=target,
            workspace_id=context.workspace_id,
            body=await request.body(),
        )
    )
    try:
        yield operation
    finally:
        await operation.abandon_unless_recorded()


async def get_form_idempotent_operation(
    request: Request,
    context: RequireAccessContext,
    container: RequireContainer,
    idempotency_key: str | None = _KEY_HEADER,
) -> AsyncIterator[IdempotentOperation]:
    """The same key, claimed by the route through ``claim_fields`` once it has
    parsed the form, rather than here from bytes that cannot be read again."""
    operation = IdempotentOperation(
        container.idempotency,
        context,
        _key(idempotency_key),
        method=request.method,
        target=_target(request),
    )
    try:
        yield operation
    finally:
        await operation.abandon_unless_recorded()


RequireIdempotency = Annotated[IdempotentOperation, Depends(get_idempotent_operation)]
