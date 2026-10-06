"""The `Idempotency-Key` dependency, over the real app with an in-memory store.

Same shape as ``test_admin_members_endpoint``: the real routes, the real
handlers, only the database faked. What is asserted is what a client integrating
against the published API is told to expect — a replay does not re-run the
handler, a reused key with a different body is refused, and a handler that blew
up leaves the key spendable.
"""

import hashlib
import uuid
from typing import Annotated

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import Depends, FastAPI, File, Form, UploadFile
from pydantic import BaseModel

from dw_api.bootstrap import ApiContainer
from dw_api.dependencies.idempotency import (
    IdempotentOperation,
    ReplayedResponse,
    get_form_idempotent_operation,
)
from dw_api.health import CheckState, HealthService
from dw_api.main import create_app
from dw_api.settings import ApiSettings
from dw_kernel.errors import IdempotencyConflictError, InfrastructureError
from dw_kernel.ports import SystemClock, Uuid4Generator
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
from dw_platform.application.membership_admin import (
    GrantMembershipHandler,
    RevokeMembershipHandler,
    UserRef,
)
from dw_platform.domain.audit import AuditEvent

pytestmark = pytest.mark.unit

SECRET = "unit-test-secret-0123456789abcdef"
TENANT = uuid.uuid4()
WORKSPACE = uuid.uuid4()
PRINCIPAL = uuid.uuid4()
TARGET = uuid.uuid4()
GRANT_BODY = {"email": "newhire@fpt.com", "workspace_id": str(WORKSPACE), "role_keys": ["sales"]}


class FakeMembershipLookup:
    async def find_access(
        self, subject: str, issuer: str, tenant_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> MembershipAccess | None:
        return MembershipAccess(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            principal_id=PRINCIPAL,
            roles=frozenset({"org_admin"}),
            scopes=frozenset({"platform.members.write"}),
            groups=frozenset(),
            clearance="internal",
            plan_id="professional",
            feature_flags=frozenset(),
        )


class CountingRepo:
    """Counts grants, so "the handler did not run again" is a real assertion."""

    def __init__(self, *, fail: bool = False) -> None:
        self.grants = 0
        self.revokes = 0
        self.fail = fail

    async def find_user_by_email(self, email: str) -> UserRef | None:
        return UserRef(user_id=TARGET, email=email, display_name="New Hire")

    async def known_roles(self, role_keys: frozenset[str]) -> frozenset[str]:
        return role_keys & {"sales"}

    async def scopes_for_roles(self, role_keys: frozenset[str]) -> frozenset[str]:
        return frozenset()

    async def grant(self, context: AccessContext, *, audit: AuditEvent, **kw: object) -> None:
        self.grants += 1
        if self.fail:
            raise InfrastructureError("the database went away mid-write")

    async def revoke(
        self,
        context: AccessContext,
        *,
        user_id: uuid.UUID,
        workspace_id: uuid.UUID,
        audit: AuditEvent,
    ) -> bool:
        self.revokes += 1
        return True


class MemoryStore:
    """The store port, backed by a dict. Reservation semantics preserved."""

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
        # Nothing in these tests is old enough to be abandoned.
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


def make_container(repo: CountingRepo, store: MemoryStore) -> ApiContainer:
    async def ok_probe() -> CheckState:
        return "ok"

    authz = ScopeAuthorizationService()
    clock, ids = SystemClock(), Uuid4Generator()
    return ApiContainer(
        settings=ApiSettings(profile="test", dev_secret=SECRET),
        engine=None,
        health_service=HealthService(probes={"database": ok_probe}),
        token_verifier=DevTokenVerifier(SECRET),
        access_context_factory=DbAccessContextFactory(FakeMembershipLookup()),
        identity_bootstrap=None,
        uow_factory=None,
        authorization=authz,
        entitlement=PlanEntitlementService(DEFAULT_PLANS),
        grant_membership=GrantMembershipHandler(repo, authz, clock, ids),
        revoke_membership=RevokeMembershipHandler(repo, authz, clock, ids),
        idempotency=HttpIdempotency(store=store, clock=clock),
    )


def headers(key: str | None = None) -> dict[str, str]:
    token = DevTokenVerifier(SECRET).issue("dev|admin", email="admin@fpt.com")
    sent = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": str(TENANT),
        "X-Workspace-Id": str(WORKSPACE),
    }
    if key is not None:
        sent["Idempotency-Key"] = key
    return sent


async def test_a_replay_returns_the_stored_response_without_running_the_handler() -> None:
    repo, store = CountingRepo(), MemoryStore()
    app = create_app(make_container(repo, store))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post(
                "/api/v1/admin/members", json=GRANT_BODY, headers=headers("key-1")
            )
            second = await client.post(
                "/api/v1/admin/members", json=GRANT_BODY, headers=headers("key-1")
            )

    assert first.status_code == 201
    assert second.status_code == 201, "the replay reproduces the original status, not a bare 200"
    assert second.json() == first.json()
    assert repo.grants == 1, "the handler must run exactly once"


async def test_the_same_key_with_a_different_body_is_a_conflict() -> None:
    repo, store = CountingRepo(), MemoryStore()
    app = create_app(make_container(repo, store))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/v1/admin/members", json=GRANT_BODY, headers=headers("key-2"))
            clashing = await client.post(
                "/api/v1/admin/members",
                json={**GRANT_BODY, "email": "someone.else@fpt.com"},
                headers=headers("key-2"),
            )

    assert clashing.status_code == 409
    assert clashing.json()["code"] == "idempotency_conflict"
    assert repo.grants == 1, "the second request must not have reached the handler"


async def test_without_the_header_nothing_is_stored_and_nothing_is_replayed() -> None:
    """The header is opt-in: an existing client keeps today's behaviour exactly."""
    repo, store = CountingRepo(), MemoryStore()
    app = create_app(make_container(repo, store))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/v1/admin/members", json=GRANT_BODY, headers=headers())
            await client.post("/api/v1/admin/members", json=GRANT_BODY, headers=headers())

    assert repo.grants == 2
    assert store.rows == {}


async def test_a_failed_request_releases_its_key_so_the_retry_can_work() -> None:
    """A 5xx is a failure to decide, not a decision. Storing it would be permanent."""
    repo, store = CountingRepo(fail=True), MemoryStore()
    app = create_app(make_container(repo, store))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            failed = await client.post(
                "/api/v1/admin/members", json=GRANT_BODY, headers=headers("key-3")
            )

    assert failed.status_code >= 500
    assert store.rows == {}, "the key must be spendable again"


async def test_a_successful_retry_after_a_failure_is_stored() -> None:
    repo, store = CountingRepo(fail=True), MemoryStore()
    container = make_container(repo, store)
    app = create_app(container)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/v1/admin/members", json=GRANT_BODY, headers=headers("key-4"))
            repo.fail = False
            retried = await client.post(
                "/api/v1/admin/members", json=GRANT_BODY, headers=headers("key-4")
            )

    assert retried.status_code == 201
    assert repo.grants == 2, "the retry ran, because the first attempt decided nothing"
    assert store.rows[(TENANT, "key-4")].response is not None


async def test_a_204_route_replays_as_a_204_with_no_body() -> None:
    repo, store = CountingRepo(), MemoryStore()
    app = create_app(make_container(repo, store))
    url = f"/api/v1/admin/members/{TARGET}?workspace_id={WORKSPACE}"
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.delete(url, headers=headers("key-5"))
            second = await client.delete(url, headers=headers("key-5"))

    assert (first.status_code, second.status_code) == (204, 204)
    assert second.content == b""
    assert repo.revokes == 1


async def test_the_query_string_is_part_of_what_the_key_was_spent_on() -> None:
    """Two revokes differ only by query parameter; one key must not cover both."""
    repo, store = CountingRepo(), MemoryStore()
    app = create_app(make_container(repo, store))
    other_workspace = uuid.uuid4()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.delete(
                f"/api/v1/admin/members/{TARGET}?workspace_id={WORKSPACE}",
                headers=headers("key-6"),
            )
            clashing = await client.delete(
                f"/api/v1/admin/members/{TARGET}?workspace_id={other_workspace}",
                headers=headers("key-6"),
            )

    assert clashing.status_code == 409
    assert repo.revokes == 1


async def test_an_over_long_key_is_refused_rather_than_truncated() -> None:
    repo, store = CountingRepo(), MemoryStore()
    app = create_app(make_container(repo, store))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/admin/members", json=GRANT_BODY, headers=headers("k" * 256)
            )

    assert response.status_code == 422
    assert repo.grants == 0


async def test_an_operation_that_never_claimed_the_key_never_releases_it() -> None:
    """A form route claims its key itself, after parsing. One that failed
    before claiming must not free the reservation another in-flight request
    holds under the same key, nor store an answer into it."""
    store = MemoryStore()
    idempotency = HttpIdempotency(store=store, clock=SystemClock())
    context = AccessContext(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        principal_id=PRINCIPAL,
        roles=frozenset(),
        plan_id="professional",
    )
    holder = IdempotentOperation(idempotency, context, "shared", method="POST", target="/x")
    await holder.claim_fields({"a": "1"})
    bystander = IdempotentOperation(idempotency, context, "shared", method="POST", target="/x")

    await bystander.abandon_unless_recorded()
    assert (TENANT, "shared") in store.rows, "the holder's reservation must survive"

    await bystander.record_no_content()
    assert store.rows[(TENANT, "shared")].response is None, "nothing stored by a non-holder"


async def test_claim_fields_fingerprints_the_fields_not_their_order() -> None:
    store = MemoryStore()
    idempotency = HttpIdempotency(store=store, clock=SystemClock())
    context = AccessContext(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        principal_id=PRINCIPAL,
        roles=frozenset(),
        plan_id="professional",
    )
    first = IdempotentOperation(idempotency, context, "k", method="POST", target="/x")
    await first.claim_fields({"a": "1", "b": "2"})
    await first.record_no_content()

    again = IdempotentOperation(idempotency, context, "k", method="POST", target="/x")
    with pytest.raises(ReplayedResponse):
        await again.claim_fields({"b": "2", "a": "1"})
    other = IdempotentOperation(idempotency, context, "k", method="POST", target="/x")
    with pytest.raises(IdempotencyConflictError):
        await other.claim_fields({"a": "1", "b": "3"})


class UploadView(BaseModel):
    title: str
    size: int


def with_upload_route(app: FastAPI, calls: list[str]) -> FastAPI:
    """A multipart route of the shape the form dependency is for: the key is
    claimed from the parsed fields, the file as its hash. The platform ships
    no such route yet; this one stands in for the first context's."""
    form_idempotency = Annotated[IdempotentOperation, Depends(get_form_idempotent_operation)]

    @app.post("/api/v1/probe/uploads", status_code=201)
    async def upload(
        idempotency: form_idempotency,
        title: Annotated[str, Form()],
        file: Annotated[UploadFile, File()],
    ) -> UploadView:
        content = await file.read()
        await idempotency.claim_fields(
            {"title": title, "sha256": hashlib.sha256(content).hexdigest()}
        )
        calls.append(title)
        return await idempotency.record(UploadView(title=title, size=len(content)), status_code=201)

    return app


async def test_a_multipart_retry_replays_and_another_file_under_the_key_conflicts() -> None:
    """Each send of a form gets a fresh boundary, so its raw bytes never match
    the first attempt's; and reading them in a dependency would consume the
    stream the form parser needs. Claimed from the parsed fields instead, a
    retry of the same file replays, and a different file under the same key is
    a conflict, not a second upload."""
    calls: list[str] = []
    app = with_upload_route(create_app(make_container(CountingRepo(), MemoryStore())), calls)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:

            async def send(content: bytes) -> httpx.Response:
                return await client.post(
                    "/api/v1/probe/uploads",
                    data={"title": "report"},
                    files={"file": ("report.pdf", content, "application/pdf")},
                    headers=headers("upload-1"),
                )

            first = await send(b"%PDF-1 first")
            retry = await send(b"%PDF-1 first")
            other = await send(b"%PDF-1 another file")

    assert first.status_code == 201, first.text
    assert retry.status_code == 201
    assert retry.json() == first.json()
    assert other.status_code == 409
    assert other.json()["code"] == "idempotency_conflict"
    assert calls == ["report"], "the upload ran exactly once"
