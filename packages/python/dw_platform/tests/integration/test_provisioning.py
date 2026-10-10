"""Integration: platform provisioning as dw_provisioner (ADR-002).

Two properties matter here. The happy path: an operator creates a tenant and it
shows up with its plan. And the security invariant of ADR-001 §3 / ADR-002 D4:
the provisioning role can write the platform tables but **cannot read any
business schema** — proven by a SELECT on sales_crm.accounts being refused.

Migrations run as dw_migrator, which has no CREATEROLE, and the migration's
grants are guarded on the role existing — so a fixture creates dw_provisioner as
the superuser and calls `platform.grant_provisioner_privileges()`, the one owner
of its grants (migration f38f027d8342), as `scripts/create_provisioner_role.py`
does: no list of its own to drift.

It ALTERs the password when the role is already there. `infra/compose/init/
init-databases.sh` creates dw_provisioner at cluster init with the deployment
password, so on any cluster provisioned that way a plain CREATE-if-absent leaves
the role holding a password this fixture does not know, and every test here fails
to authenticate. Setting it on both branches is what makes the fixture true of a
real cluster rather than only of an empty one.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from pg_harness import DatabaseUrls
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dw_kernel.errors import ConflictError, DomainError, NotFoundError
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.adapters.persistence.identity_provisioning import SqlIdentityBootstrap
from dw_platform.adapters.persistence.membership_lookup import SqlMembershipLookup
from dw_platform.adapters.persistence.provisioning_repo import SqlProvisioningRepository
from dw_platform.application.provisioning import ProvisioningContext, ProvisioningService
from dw_platform.testing.seed_env import seed_test_env

pytestmark = pytest.mark.integration

PROVISIONER_PW = "test-provisioner-pw"


def _admin_dw_url(db_urls: DatabaseUrls) -> str:
    # db_urls.admin points at /postgres; provisioning lives in /dw_test.
    db = db_urls.migrator.rpartition("/")[2]
    admin_base = db_urls.admin.rpartition("/")[0]
    return f"{admin_base}/{db}"


def _provisioner_url(db_urls: DatabaseUrls) -> str:
    base = db_urls.app.rpartition("/")[0]  # postgresql+asyncpg://dw_app:pw@host:port
    host_db = base.split("@", 1)[1]
    db = db_urls.migrator.rpartition("/")[2]
    return f"postgresql+asyncpg://dw_provisioner:{PROVISIONER_PW}@{host_db}/{db}"


@pytest.fixture
async def provisioner_engine(db_urls: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    await seed_test_env(db_urls.migrator)

    admin = create_async_engine(_admin_dw_url(db_urls), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(
                sa.text(
                    "DO $$ BEGIN "
                    "IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='dw_provisioner') THEN "
                    f"CREATE ROLE dw_provisioner LOGIN PASSWORD '{PROVISIONER_PW}' "
                    "NOSUPERUSER BYPASSRLS NOCREATEDB NOCREATEROLE; "
                    "ELSE "
                    f"ALTER ROLE dw_provisioner LOGIN PASSWORD '{PROVISIONER_PW}'; "
                    "END IF; END $$;"
                )
            )
            await conn.execute(sa.text("SELECT platform.grant_provisioner_privileges()"))
    finally:
        await admin.dispose()

    engine = create_async_engine(_provisioner_url(db_urls), poolclass=NullPool)
    yield engine
    await engine.dispose()


def _service(engine: AsyncEngine) -> ProvisioningService:
    repo = SqlProvisioningRepository(async_sessionmaker(engine, expire_on_commit=False))
    return ProvisioningService(repo=repo, clock=SystemClock(), ids=Uuid4Generator())


def _ctx() -> ProvisioningContext:
    return ProvisioningContext(principal_id=uuid.uuid4())


async def test_operator_creates_a_tenant_and_it_lists(provisioner_engine: AsyncEngine) -> None:
    service = _service(provisioner_engine)
    # A slug unique to this run. The database is recreated once per session, not
    # per test, and test_identity_provisioning.py creates a tenant of its own
    # earlier in the same session — a shared literal slug makes this test pass
    # alone and fail in the suite, which is the least useful way for it to fail.
    slug = f"fis-{uuid.uuid4().hex[:8]}"
    created = await service.create_tenant(_ctx(), slug=slug, name="FPT IS", plan_id="professional")

    tenants = await service.list_tenants(_ctx())
    match = [t for t in tenants if t.id == created.tenant_id]
    assert match and match[0].slug == slug
    assert match[0].plan_id == "professional"
    assert match[0].workspace_count == 1  # the default 'main' workspace


async def test_operator_renames_a_tenant(provisioner_engine: AsyncEngine) -> None:
    service = _service(provisioner_engine)
    created = await service.create_tenant(_ctx(), slug="rentco", name="Old Name", plan_id="basic")

    updated = await service.rename_tenant(_ctx(), tenant_id=created.tenant_id, name="New Name")
    assert updated.name == "New Name"
    assert updated.slug == "rentco"  # the identity slug is untouched

    listed = [t for t in await service.list_tenants(_ctx()) if t.id == created.tenant_id]
    assert listed and listed[0].name == "New Name"


async def test_renaming_an_unknown_tenant_is_refused(provisioner_engine: AsyncEngine) -> None:
    service = _service(provisioner_engine)
    with pytest.raises(NotFoundError):
        await service.rename_tenant(_ctx(), tenant_id=uuid.uuid4(), name="ghost")


async def test_assign_org_admin_after_sign_in(
    provisioner_engine: AsyncEngine, db_urls: DatabaseUrls
) -> None:
    service = _service(provisioner_engine)
    created = await service.create_tenant(_ctx(), slug="fso", name="FSOFT", plan_id="basic")

    # The person signs in once (identity created, default-deny → no membership).
    app_engine = create_async_engine(db_urls.app, poolclass=NullPool)
    try:
        bootstrap = SqlIdentityBootstrap(
            session_factory=async_sessionmaker(app_engine, expire_on_commit=False),
            default_tenant_id=created.tenant_id,
            default_workspace_id=created.workspace_id,
        )

        class _Id:
            subject = f"sub-{uuid.uuid4()}"
            email = "newlead@fpt.com"
            issuer = "https://issuer.test/realms/dw"
            name = "New Lead"
            auth_methods: frozenset[str] = frozenset()
            acr: str | None = None

        await bootstrap.bootstrap(_Id())
    finally:
        await app_engine.dispose()

    ref = await service.assign_org_admin(
        _ctx(), tenant_id=created.tenant_id, email="newlead@fpt.com"
    )
    assert ref.email == "newlead@fpt.com"


async def test_locking_a_tenant_denies_its_members(
    provisioner_engine: AsyncEngine, db_urls: DatabaseUrls
) -> None:
    """P1b: locking must actually revoke access, not just set a column."""
    issuer = "https://issuer.test/realms/dw"
    service = _service(provisioner_engine)
    created = await service.create_tenant(_ctx(), slug="lockco", name="Lock Co", plan_id="basic")

    app_engine = create_async_engine(db_urls.app, poolclass=NullPool)
    try:
        sessions = async_sessionmaker(app_engine, expire_on_commit=False)
        bootstrap = SqlIdentityBootstrap(
            session_factory=sessions,
            default_tenant_id=created.tenant_id,
            default_workspace_id=created.workspace_id,
        )

        class _Id:
            subject = f"sub-{uuid.uuid4()}"
            email = "member@lockco.com"
            issuer = "https://issuer.test/realms/dw"
            name = "Member"
            auth_methods: frozenset[str] = frozenset()
            acr: str | None = None

        view = await bootstrap.bootstrap(_Id())
        await service.assign_org_admin(
            _ctx(), tenant_id=created.tenant_id, email="member@lockco.com"
        )

        lookup = SqlMembershipLookup(sessions)
        active = await lookup.find_access(
            view.subject, issuer, created.tenant_id, created.workspace_id
        )
        assert active is not None  # a member of an active tenant gets access

        await service.set_tenant_status(_ctx(), tenant_id=created.tenant_id, status="locked")
        denied = await lookup.find_access(
            view.subject, issuer, created.tenant_id, created.workspace_id
        )
        assert denied is None  # the same member is frozen out once locked
    finally:
        await app_engine.dispose()


async def test_initiate_offboarding_flags_the_tenant_and_files_a_request(
    provisioner_engine: AsyncEngine,
) -> None:
    service = _service(provisioner_engine)
    created = await service.create_tenant(_ctx(), slug="offco", name="Off Co", plan_id="basic")

    status = await service.initiate_offboarding(_ctx(), tenant_id=created.tenant_id)

    assert status.tenant_id == created.tenant_id
    assert status.status == "requested"
    tenants = await service.list_tenants(_ctx())
    match = [t for t in tenants if t.id == created.tenant_id]
    assert match and match[0].status == "offboarding"


async def test_a_second_initiate_while_one_is_in_flight_is_a_conflict(
    provisioner_engine: AsyncEngine,
) -> None:
    service = _service(provisioner_engine)
    created = await service.create_tenant(_ctx(), slug="offco2", name="Off Co 2", plan_id="basic")
    await service.initiate_offboarding(_ctx(), tenant_id=created.tenant_id)

    with pytest.raises(ConflictError):
        await service.initiate_offboarding(_ctx(), tenant_id=created.tenant_id)

    # The first request's "offboarding" status must survive the conflict —
    # the bug a rollback-to-active on ConflictError would have reintroduced.
    tenants = await service.list_tenants(_ctx())
    match = [t for t in tenants if t.id == created.tenant_id]
    assert match and match[0].status == "offboarding"


async def test_initiate_offboarding_for_an_unknown_tenant_is_refused(
    provisioner_engine: AsyncEngine,
) -> None:
    service = _service(provisioner_engine)
    with pytest.raises(NotFoundError):
        await service.initiate_offboarding(_ctx(), tenant_id=uuid.uuid4())


async def test_get_offboarding_status_with_no_request_is_refused(
    provisioner_engine: AsyncEngine,
) -> None:
    service = _service(provisioner_engine)
    created = await service.create_tenant(_ctx(), slug="offco3", name="Off Co 3", plan_id="basic")

    with pytest.raises(NotFoundError):
        await service.get_offboarding_status(_ctx(), tenant_id=created.tenant_id)


async def test_finalize_before_the_worker_completes_it_is_refused(
    provisioner_engine: AsyncEngine,
) -> None:
    service = _service(provisioner_engine)
    created = await service.create_tenant(_ctx(), slug="offco4", name="Off Co 4", plan_id="basic")
    await service.initiate_offboarding(_ctx(), tenant_id=created.tenant_id)

    with pytest.raises(DomainError):
        await service.finalize_offboarding(_ctx(), tenant_id=created.tenant_id)

    # Still "offboarding", not "offboarded" — finalize must not have moved it.
    tenants = await service.list_tenants(_ctx())
    match = [t for t in tenants if t.id == created.tenant_id]
    assert match and match[0].status == "offboarding"


async def test_finalize_after_the_worker_reports_completed_offboards_the_tenant(
    provisioner_engine: AsyncEngine, db_urls: DatabaseUrls
) -> None:
    """The worker lane reports "completed" by updating its own row as dw_app,
    scoped to the target tenant — simulated here directly against the table
    rather than through the (not yet built) worker lane itself."""
    service = _service(provisioner_engine)
    created = await service.create_tenant(_ctx(), slug="offco5", name="Off Co 5", plan_id="basic")
    await service.initiate_offboarding(_ctx(), tenant_id=created.tenant_id)

    app_engine = create_async_engine(db_urls.app, poolclass=NullPool)
    try:
        async with app_engine.connect() as conn, conn.begin():
            await conn.execute(
                sa.text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": str(created.tenant_id)},
            )
            await conn.execute(
                sa.text(
                    "UPDATE platform.tenant_offboarding_requests"
                    " SET status = 'completed', export_key = :key"
                    " WHERE tenant_id = :t"
                ),
                {"t": str(created.tenant_id), "key": f"{created.tenant_id}/exports/x.zip"},
            )
    finally:
        await app_engine.dispose()

    await service.finalize_offboarding(_ctx(), tenant_id=created.tenant_id)

    tenants = await service.list_tenants(_ctx())
    match = [t for t in tenants if t.id == created.tenant_id]
    assert match and match[0].status == "offboarded"


async def test_provisioner_cannot_read_business_data(provisioner_engine: AsyncEngine) -> None:
    """ADR-001 §3 / ADR-002 D4: no grant on any business schema, so the read is
    refused at the database — even though the role has BYPASSRLS."""
    async with provisioner_engine.connect() as conn:
        with pytest.raises(ProgrammingError):
            await conn.execute(sa.text("SELECT count(*) FROM sales_crm.accounts"))


# What the provisioner may do, table by table, as the catalog answers it. A
# security expectation stated by the test (as `test_privileges.py` states its
# own), not a copy the code reads: the code reads the function.
_HELD = {
    "platform.tenants": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "platform.workspaces": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "platform.memberships": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "platform.entitlements": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "platform.platform_operators": {"SELECT", "INSERT", "DELETE"},
    "platform.support_staff": {"SELECT", "INSERT", "DELETE"},
    "platform.provisioning_audit": {"SELECT", "INSERT"},
    "platform.audit_events": {"INSERT"},
    "platform.tenant_offboarding_requests": {"SELECT", "INSERT", "UPDATE"},
    "platform.support_grants": {"SELECT"},
    "platform.roles": {"SELECT"},
    "platform.users": {"SELECT"},
    "platform.plans": {"SELECT"},
}


async def test_the_provisioner_holds_exactly_its_list_and_nothing_on_a_business_table(
    provisioner_engine: AsyncEngine, db_urls: DatabaseUrls
) -> None:
    """After the one owner ran (twice: it is idempotent), the catalog holds
    exactly `_HELD` at table level, the assignment columns of a support grant,
    and nothing on any other table of any schema — a business table above all.
    A widening the function did not make (a stray GRANT in a later migration)
    goes red here."""
    admin = create_async_engine(_admin_dw_url(db_urls), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(sa.text("SELECT platform.grant_provisioner_privileges()"))
            rows = (
                await conn.execute(
                    sa.text(
                        "SELECT table_schema || '.' || table_name AS tbl, privilege_type AS priv"
                        " FROM information_schema.role_table_grants"
                        " WHERE grantee = 'dw_provisioner'"
                    )
                )
            ).all()
            columns = (
                await conn.execute(
                    sa.text(
                        "SELECT table_schema || '.' || table_name AS tbl, column_name AS col,"
                        " privilege_type AS priv FROM information_schema.column_privileges"
                        " WHERE grantee = 'dw_provisioner' AND privilege_type = 'UPDATE'"
                        " AND table_name = 'support_grants'"
                    )
                )
            ).all()
            business = await conn.scalar(
                sa.text(
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n"
                    " ON n.oid = c.relnamespace WHERE c.relkind IN ('r', 'p', 'v', 'm')"
                    " AND n.nspname NOT IN ('platform', 'pg_catalog', 'information_schema')"
                    " AND n.nspname NOT LIKE 'pg\\_%'"
                    " AND has_any_column_privilege('dw_provisioner', c.oid, 'SELECT')"
                )
            )
    finally:
        await admin.dispose()

    held: dict[str, set[str]] = {}
    for row in rows:
        held.setdefault(row.tbl, set()).add(row.priv)
    assert held == _HELD
    assert {row.col for row in columns} == {
        "status",
        "staff_user_id",
        "assigned_by",
        "activated_at",
        "expires_at",
    }
    assert business == 0
