"""Integration: static separation of duties, enforced by the database.

A rule says two sets of scopes must never meet in one membership. The trigger
on `platform.memberships` is the one place that decides. These tests reach it
through every kind of writer: the admin grant, the permission-set update, raw
SQL as the application, and raw SQL as the migrator. They show that no writer
gets around it, and that the application reads the refusal back as a 409
naming the rule.

The rule, roles and permission set here are this file's own, with a per-test
suffix. The shared catalogue's real rules are checked for consistency, not
used as fixtures.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from pg_harness import DatabaseUrls
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dw_kernel.errors import ConflictError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.admin_console_repo import SqlAdminConsoleRepository
from dw_platform.adapters.persistence.identity_provisioning import SqlIdentityBootstrap
from dw_platform.adapters.persistence.membership_admin import SqlMembershipAdminRepository
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_platform.testing.seed_env import seed_test_env

pytestmark = pytest.mark.integration

ALPHA = uuid.UUID("d6b43d0e-c3c6-5dbc-bc08-150621bd9a5d")
ALPHA_WS = uuid.UUID("64764894-718d-5558-ba17-9a2949214063")


@dataclass(frozen=True)
class _Catalogue:
    rule: str
    orderer: str
    payer: str
    bystander: str
    pay_set: str


@dataclass(frozen=True)
class _Identity:
    subject: str
    email: str | None
    issuer: str = "https://issuer.test/realms/dw"
    name: str | None = None
    auth_methods: frozenset[str] = frozenset()
    acr: str | None = None


@pytest.fixture
async def app_engine(db_urls: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    await seed_test_env(db_urls.migrator)
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def migrator_engine(db_urls: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(db_urls.migrator, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def catalogue(migrator_engine: AsyncEngine) -> AsyncIterator[_Catalogue]:
    suffix = uuid.uuid4().hex[:10]
    made = _Catalogue(
        rule=f"sod_test_order_vs_pay_{suffix}",
        orderer=f"test_orderer_{suffix}",
        payer=f"test_payer_{suffix}",
        bystander=f"test_bystander_{suffix}",
        pay_set=f"test_pay_set_{suffix}",
    )
    order, pay = f"test.{suffix}.order", f"test.{suffix}.pay"
    async with migrator_engine.begin() as conn:
        for key, scopes in (
            (made.orderer, [order]),
            (made.payer, [pay]),
            (made.bystander, [f"test.{suffix}.read"]),
        ):
            await conn.execute(sa.insert(tables.roles).values(key=key, name=key, scopes=scopes))
        await conn.execute(
            sa.insert(tables.permission_sets).values(
                key=made.pay_set, name=made.pay_set, scopes=[pay]
            )
        )
        await conn.execute(
            sa.text(
                "INSERT INTO platform.sod_rules (key, description, left_scopes, right_scopes)"
                " VALUES (:key, 'orders are not paid by who placed them',"
                " CAST(:left AS jsonb), CAST(:right AS jsonb))"
            ),
            {"key": made.rule, "left": json.dumps([order]), "right": json.dumps([pay])},
        )
    yield made
    async with migrator_engine.begin() as conn:
        await conn.execute(
            sa.text("DELETE FROM platform.sod_rules WHERE key = :k"), {"k": made.rule}
        )
        await conn.execute(
            sa.delete(tables.permission_sets).where(tables.permission_sets.c.key == made.pay_set)
        )
        await conn.execute(
            sa.delete(tables.roles).where(
                tables.roles.c.key.in_([made.orderer, made.payer, made.bystander])
            )
        )


def _admin() -> AccessContext:
    return AccessContext(
        tenant_id=ALPHA,
        workspace_id=ALPHA_WS,
        principal_id=uuid.uuid4(),
        roles=frozenset({"org_admin"}),
        scopes=frozenset({"platform.members.write"}),
        plan_id="professional",
    )


def _audit(context: AccessContext, user_id: uuid.UUID) -> AuditEvent:
    return AuditEvent(
        id=uuid.uuid4(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        actor_id=UserId(context.principal_id),
        action="platform.membership.grant",
        resource_type="membership",
        resource_id=str(user_id),
        occurred_at=datetime.now(UTC),
    )


async def _user(engine: AsyncEngine) -> uuid.UUID:
    bootstrap = SqlIdentityBootstrap(
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        default_tenant_id=ALPHA,
        default_workspace_id=ALPHA_WS,
    )
    view = await bootstrap.bootstrap(
        _Identity(subject=f"sub-{uuid.uuid4()}", email=f"{uuid.uuid4().hex[:8]}@sod.test")
    )
    return view.principal_id


async def _grant(engine: AsyncEngine, user_id: uuid.UUID, *roles: str) -> None:
    context = _admin()
    await SqlMembershipAdminRepository(async_sessionmaker(engine, expire_on_commit=False)).grant(
        context,
        user_id=user_id,
        workspace_id=ALPHA_WS,
        role_keys=frozenset(roles),
        department="general",
        audit=_audit(context, user_id),
    )


async def _roles_of(engine: AsyncEngine, user_id: uuid.UUID) -> list[str] | None:
    async with engine.begin() as conn:
        await conn.execute(
            sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(ALPHA)}
        )
        row = (
            await conn.execute(
                sa.select(tables.memberships.c.role_keys).where(
                    tables.memberships.c.user_id == user_id
                )
            )
        ).first()
    return None if row is None else list(row.role_keys)


async def test_granting_both_sides_of_a_rule_is_refused_as_a_conflict(
    app_engine: AsyncEngine, catalogue: _Catalogue
) -> None:
    user_id = await _user(app_engine)

    with pytest.raises(ConflictError) as refused:
        await _grant(app_engine, user_id, catalogue.orderer, catalogue.payer)

    assert refused.value.details["rule"] == catalogue.rule
    assert refused.value.details["reason"] == "orders are not paid by who placed them"
    assert await _roles_of(app_engine, user_id) is None


async def test_a_regrant_that_would_add_the_other_side_is_refused_and_changes_nothing(
    app_engine: AsyncEngine, catalogue: _Catalogue
) -> None:
    """The grant is an upsert: its update half is judged too."""
    user_id = await _user(app_engine)
    await _grant(app_engine, user_id, catalogue.orderer)

    with pytest.raises(ConflictError):
        await _grant(app_engine, user_id, catalogue.orderer, catalogue.payer)

    assert await _roles_of(app_engine, user_id) == [catalogue.orderer]


async def test_roles_that_break_no_rule_are_granted(
    app_engine: AsyncEngine, catalogue: _Catalogue
) -> None:
    user_id = await _user(app_engine)
    await _grant(app_engine, user_id, catalogue.orderer, catalogue.bystander)
    assert sorted(await _roles_of(app_engine, user_id) or []) == sorted(
        [catalogue.orderer, catalogue.bystander]
    )


async def test_a_permission_set_cannot_carry_the_other_side_past_the_rule(
    app_engine: AsyncEngine, catalogue: _Catalogue
) -> None:
    user_id = await _user(app_engine)
    await _grant(app_engine, user_id, catalogue.orderer)
    context = _admin()
    repo = SqlAdminConsoleRepository(async_sessionmaker(app_engine, expire_on_commit=False))

    with pytest.raises(ConflictError) as refused:
        await repo.set_permission_sets(
            context,
            user_id=user_id,
            permission_set_keys=frozenset({catalogue.pay_set}),
            audit=_audit(context, user_id),
        )
    assert refused.value.details["rule"] == catalogue.rule


@pytest.mark.parametrize("writer", ["app", "migrator"])
async def test_raw_sql_is_refused_too_whoever_writes_it(
    app_engine: AsyncEngine,
    migrator_engine: AsyncEngine,
    catalogue: _Catalogue,
    writer: str,
) -> None:
    """The rule lives in the database: a writer that skips the adapter, even
    the migrator that bypasses RLS, meets the same refusal."""
    user_id = await _user(app_engine)
    engine = app_engine if writer == "app" else migrator_engine

    with pytest.raises(IntegrityError, match=catalogue.rule):
        async with engine.begin() as conn:
            await conn.execute(
                sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(ALPHA)}
            )
            await conn.execute(
                sa.insert(tables.memberships).values(
                    id=uuid.uuid4(),
                    tenant_id=ALPHA,
                    workspace_id=ALPHA_WS,
                    user_id=user_id,
                    role_keys=[catalogue.orderer],
                    permission_set_keys=[catalogue.pay_set],
                )
            )


async def test_the_application_cannot_rewrite_the_rules(app_engine: AsyncEngine) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with app_engine.begin() as conn:
            await conn.execute(sa.text("DELETE FROM platform.sod_rules"))


async def test_no_single_role_or_permission_set_in_the_catalogue_breaks_a_rule(
    app_engine: AsyncEngine,
) -> None:
    """Every rule in the shared catalogue, context rules included, can be met
    by assigning roles carefully: no role or set breaks one on its own."""
    async with app_engine.begin() as conn:
        broken_roles = (
            (
                await conn.execute(
                    sa.text(
                        "SELECT r.key FROM platform.roles r"
                        " WHERE EXISTS (SELECT 1 FROM platform.sod_violation("
                        "   NULL, jsonb_build_array(r.key), '[]'::jsonb))"
                    )
                )
            )
            .scalars()
            .all()
        )
        broken_sets = (
            (
                await conn.execute(
                    sa.text(
                        "SELECT p.key FROM platform.permission_sets p"
                        " WHERE EXISTS (SELECT 1 FROM platform.sod_violation("
                        "   NULL, '[]'::jsonb, jsonb_build_array(p.key)))"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert (list(broken_roles), list(broken_sets)) == ([], [])
