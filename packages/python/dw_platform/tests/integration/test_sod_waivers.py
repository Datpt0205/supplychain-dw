"""Integration: a tenant's waiver of a separation-of-duty rule (6b26771e549d).

A waiver lifts one waivable rule for one tenant, on the record. What these
tests hold the database and the service to:

- a waiver lets one membership hold both sides, in that tenant only, once a
  second admin has confirmed it (f381f1694395) - never the one who proposed it;
- a role or permission set cannot gain a scope that would make a membership
  holding it break a rule its tenant has not waived;
- a rule its author did not mark waivable is a floor, for every writer;
- every waiver decision needs a reason, a scope, and leaves an audit row
  written in the same transaction;
- a waiver cannot be revoked while memberships rely on it, including when the
  revoke and the membership write race each other;
- a closed waiver is history: it cannot be reopened, rewritten or deleted.

The rules, roles and users are this file's own, with a per-test suffix.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from pg_harness import DatabaseUrls
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from dw_kernel.errors import ConflictError, DomainError, NotFoundError, PermissionDeniedError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.identity_provisioning import SqlIdentityBootstrap
from dw_platform.adapters.persistence.membership_admin import SqlMembershipAdminRepository
from dw_platform.adapters.persistence.separation_of_duties_repo import (
    SqlSeparationOfDutiesRepository,
)
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.separation_of_duties import (
    SOD_WAIVERS_WRITE,
    ConfirmWaiver,
    RevokeWaiver,
    SeparationOfDutiesService,
    WaiveRule,
)
from dw_platform.domain.audit import AuditEvent
from dw_platform.testing.seed_env import seed_test_env

pytestmark = pytest.mark.integration

ALPHA = uuid.UUID("d6b43d0e-c3c6-5dbc-bc08-150621bd9a5d")
ALPHA_WS = uuid.UUID("64764894-718d-5558-ba17-9a2949214063")
BETA = uuid.UUID("6634f09a-d1d7-54a6-aa23-f3f018f41f28")
BETA_WS = uuid.UUID("eda6af16-a0c4-55d3-be0b-163414390572")
REASON = "Công ty 3 người, một người vừa đặt hàng vừa thanh toán"


@dataclass(frozen=True)
class _Catalogue:
    rule: str
    floor: str
    orderer: str
    payer: str


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
    """A waivable rule and a floor over the same two scopes, and one role on
    each side."""
    suffix = uuid.uuid4().hex[:10]
    made = _Catalogue(
        rule=f"sod_test_waivable_{suffix}",
        floor=f"sod_test_floor_{suffix}",
        orderer=f"test_orderer_{suffix}",
        payer=f"test_payer_{suffix}",
    )
    order, pay = f"test.{suffix}.order", f"test.{suffix}.pay"
    async with migrator_engine.begin() as conn:
        for key, scopes in ((made.orderer, [order]), (made.payer, [pay])):
            await conn.execute(sa.insert(tables.roles).values(key=key, name=key, scopes=scopes))
        await conn.execute(
            sa.insert(tables.sod_rules).values(
                key=made.rule,
                description="orders are not paid by who placed them",
                left_scopes=[order],
                right_scopes=[pay],
                waivable=True,
            )
        )
    yield made
    async with migrator_engine.begin() as conn:
        rule_keys = [made.rule, made.floor]
        await conn.execute(
            sa.delete(tables.sod_waivers).where(tables.sod_waivers.c.rule_key.in_(rule_keys))
        )
        await conn.execute(sa.delete(tables.sod_rules).where(tables.sod_rules.c.key.in_(rule_keys)))
        await conn.execute(
            sa.delete(tables.roles).where(tables.roles.c.key.in_([made.orderer, made.payer]))
        )


async def _add_floor(engine: AsyncEngine, made: _Catalogue) -> None:
    """The same conflict as `made.rule`, with its author not letting any
    tenant waive it."""
    async with engine.begin() as conn:
        rule = (
            await conn.execute(
                sa.select(tables.sod_rules).where(tables.sod_rules.c.key == made.rule)
            )
        ).one()
        await conn.execute(
            sa.insert(tables.sod_rules).values(
                key=made.floor,
                description="a floor",
                left_scopes=rule.left_scopes,
                right_scopes=rule.right_scopes,
            )
        )


def _admin(tenant: uuid.UUID = ALPHA, workspace: uuid.UUID = ALPHA_WS) -> AccessContext:
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset({"org_admin"}),
        scopes=frozenset({"platform.members.write", "platform.roles.read", SOD_WAIVERS_WRITE}),
        plan_id="professional",
    )


def _service(engine: AsyncEngine) -> SeparationOfDutiesService:
    return SeparationOfDutiesService(
        SqlSeparationOfDutiesRepository(async_sessionmaker(engine, expire_on_commit=False)),
        ScopeAuthorizationService(),
        SystemClock(),
        Uuid4Generator(),
    )


async def _user(engine: AsyncEngine, tenant: uuid.UUID = ALPHA) -> uuid.UUID:
    bootstrap = SqlIdentityBootstrap(
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        default_tenant_id=tenant,
        default_workspace_id=ALPHA_WS if tenant == ALPHA else BETA_WS,
    )
    view = await bootstrap.bootstrap(
        _Identity(subject=f"sub-{uuid.uuid4()}", email=f"{uuid.uuid4().hex[:8]}@waiver.test")
    )
    return view.principal_id


async def _grant(
    engine: AsyncEngine, user_id: uuid.UUID, *roles: str, tenant: uuid.UUID = ALPHA
) -> None:
    context = _admin(tenant, ALPHA_WS if tenant == ALPHA else BETA_WS)
    await SqlMembershipAdminRepository(async_sessionmaker(engine, expire_on_commit=False)).grant(
        context,
        user_id=user_id,
        workspace_id=context.workspace_id,
        role_keys=frozenset(roles),
        department="general",
        audit=AuditEvent(
            id=uuid.uuid4(),
            tenant_id=TenantId(context.tenant_id),
            workspace_id=WorkspaceId(context.workspace_id),
            actor_id=UserId(context.principal_id),
            action="platform.membership.grant",
            resource_type="membership",
            resource_id=str(user_id),
            occurred_at=datetime.now(UTC),
        ),
    )


async def _waived(engine: AsyncEngine, rule_key: str, reason: str = REASON) -> None:
    """Proposed by one admin and confirmed by another: a waiver in effect."""
    service = _service(engine)
    await service.waive(_admin(), WaiveRule(rule_key, reason))
    await service.confirm(_admin(), ConfirmWaiver(rule_key, "đã xem, đồng ý"))


async def _scoped(conn: AsyncConnection, tenant: uuid.UUID = ALPHA) -> None:
    await conn.execute(sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant)})


async def _audit_actions(engine: AsyncEngine, rule_key: str) -> list[tuple[str, object]]:
    async with engine.begin() as conn:
        await _scoped(conn)
        rows = (
            await conn.execute(
                sa.text(
                    "SELECT action, details FROM platform.audit_events"
                    " WHERE resource_type = 'sod_rule' AND resource_id = :k ORDER BY occurred_at"
                ),
                {"k": rule_key},
            )
        ).all()
    return [(row.action, row.details.get("reason")) for row in rows]


async def _open_waivers(engine: AsyncEngine, rule_key: str, tenant: uuid.UUID = ALPHA) -> int:
    async with engine.begin() as conn:
        await _scoped(conn, tenant)
        return int(
            await conn.scalar(
                sa.text(
                    "SELECT count(*) FROM platform.sod_waivers"
                    " WHERE rule_key = :k AND revoked_at IS NULL"
                ),
                {"k": rule_key},
            )
            or 0
        )


# ---- what a waiver lifts, and for whom ------------------------------------


async def test_a_waived_rule_lets_one_membership_hold_both_sides(
    app_engine: AsyncEngine, catalogue: _Catalogue
) -> None:
    user_id = await _user(app_engine)
    with pytest.raises(ConflictError):
        await _grant(app_engine, user_id, catalogue.orderer, catalogue.payer)

    proposer, confirmer = _admin(), _admin()
    await _service(app_engine).waive(proposer, WaiveRule(catalogue.rule, REASON))
    # Proposed, not confirmed: it lifts nothing yet.
    with pytest.raises(ConflictError):
        await _grant(app_engine, user_id, catalogue.orderer, catalogue.payer)
    listed = {r.key: r for r in await _service(app_engine).list_rules(_admin())}
    pending = listed[catalogue.rule].waiver
    assert pending is not None and pending.confirmed_by is None

    await _service(app_engine).confirm(confirmer, ConfirmWaiver(catalogue.rule, "đồng ý"))
    await _grant(app_engine, user_id, catalogue.orderer, catalogue.payer)

    listed = {r.key: r for r in await _service(app_engine).list_rules(_admin())}
    waiver = listed[catalogue.rule].waiver
    assert waiver is not None and waiver.reason == REASON
    assert (waiver.granted_by, waiver.confirmed_by) == (
        proposer.principal_id,
        confirmer.principal_id,
    )
    assert await _audit_actions(app_engine, catalogue.rule) == [
        ("platform.sod.waive", REASON),
        ("platform.sod.waiver_confirm", "đồng ý"),
    ]


async def test_one_tenants_waiver_lifts_nothing_for_another(
    app_engine: AsyncEngine, catalogue: _Catalogue
) -> None:
    await _service(app_engine).waive(_admin(), WaiveRule(catalogue.rule, REASON))
    beta_admin = _admin(BETA, BETA_WS)

    beta_user = await _user(app_engine, BETA)
    with pytest.raises(ConflictError):
        await _grant(app_engine, beta_user, catalogue.orderer, catalogue.payer, tenant=BETA)
    # Beta sees the rule and no waiver, and cannot revoke Alpha's.
    listed = {r.key: r for r in await _service(app_engine).list_rules(beta_admin)}
    assert listed[catalogue.rule].waiver is None
    with pytest.raises(NotFoundError):
        await _service(app_engine).revoke(beta_admin, RevokeWaiver(catalogue.rule, "not ours"))
    assert await _open_waivers(app_engine, catalogue.rule, BETA) == 0
    assert await _open_waivers(app_engine, catalogue.rule, ALPHA) == 1


# ---- floors ------------------------------------------------------------------


async def test_a_rule_its_author_did_not_mark_waivable_cannot_be_waived(
    app_engine: AsyncEngine, migrator_engine: AsyncEngine, catalogue: _Catalogue
) -> None:
    await _add_floor(migrator_engine, catalogue)

    with pytest.raises(ConflictError, match="cannot be waived"):
        await _service(app_engine).waive(_admin(), WaiveRule(catalogue.floor, REASON))
    async with app_engine.begin() as conn:
        await _scoped(conn)
        assert (
            await conn.scalar(
                sa.text("SELECT count(*) FROM platform.sod_waivers WHERE rule_key = :k"),
                {"k": catalogue.floor},
            )
            == 0
        )
    assert await _audit_actions(app_engine, catalogue.floor) == []
    # Not for the migrator either: the guard is the database's.
    with pytest.raises(IntegrityError, match="cannot be waived"):
        async with migrator_engine.begin() as conn:
            await conn.execute(
                sa.insert(tables.sod_waivers).values(
                    id=uuid.uuid4(),
                    tenant_id=ALPHA,
                    rule_key=catalogue.floor,
                    reason=REASON,
                    granted_by=uuid.uuid4(),
                )
            )


async def test_waiving_the_rule_lifts_nothing_while_a_floor_forbids_the_same_pair(
    app_engine: AsyncEngine, migrator_engine: AsyncEngine, catalogue: _Catalogue
) -> None:
    await _add_floor(migrator_engine, catalogue)
    await _waived(app_engine, catalogue.rule)
    user_id = await _user(app_engine)

    with pytest.raises(ConflictError) as refused:
        await _grant(app_engine, user_id, catalogue.orderer, catalogue.payer)
    assert refused.value.details["rule"] == catalogue.floor


# ---- who may decide, and what a decision needs --------------------------------


async def test_waiving_needs_the_waiver_scope_and_a_reason(
    app_engine: AsyncEngine, catalogue: _Catalogue
) -> None:
    member = AccessContext(
        tenant_id=ALPHA,
        workspace_id=ALPHA_WS,
        principal_id=uuid.uuid4(),
        roles=frozenset({"org_admin"}),
        scopes=frozenset({"platform.members.write", "platform.roles.read"}),
        plan_id="professional",
    )
    with pytest.raises(PermissionDeniedError):
        await _service(app_engine).waive(member, WaiveRule(catalogue.rule, REASON))
    with pytest.raises(DomainError, match="reason"):
        await _service(app_engine).waive(_admin(), WaiveRule(catalogue.rule, "   "))
    assert await _open_waivers(app_engine, catalogue.rule) == 0


async def test_org_admin_holds_the_waiver_scope(app_engine: AsyncEngine) -> None:
    """Otherwise nobody in a tenant could reach the feature at all."""
    async with app_engine.connect() as conn:
        scopes = await conn.scalar(
            sa.text("SELECT scopes FROM platform.roles WHERE key = 'org_admin'")
        )
    assert SOD_WAIVERS_WRITE in scopes


async def test_a_second_open_waiver_of_one_rule_is_a_conflict(
    app_engine: AsyncEngine, catalogue: _Catalogue
) -> None:
    await _service(app_engine).waive(_admin(), WaiveRule(catalogue.rule, REASON))
    with pytest.raises(ConflictError, match="already waived"):
        await _service(app_engine).waive(_admin(), WaiveRule(catalogue.rule, REASON))
    assert await _audit_actions(app_engine, catalogue.rule) == [("platform.sod.waive", REASON)]


# ---- revoking ----------------------------------------------------------------


async def test_a_waiver_memberships_rely_on_cannot_be_revoked(
    app_engine: AsyncEngine, catalogue: _Catalogue
) -> None:
    service = _service(app_engine)
    await _waived(app_engine, catalogue.rule)
    user_id = await _user(app_engine)
    await _grant(app_engine, user_id, catalogue.orderer, catalogue.payer)

    with pytest.raises(ConflictError) as refused:
        await service.revoke(_admin(), RevokeWaiver(catalogue.rule, "đã tuyển thêm người"))
    assert refused.value.details["memberships"] == 1
    assert await _open_waivers(app_engine, catalogue.rule) == 1

    # Once the membership no longer holds both sides, the revoke goes through
    # and the rule applies again.
    await _grant(app_engine, user_id, catalogue.orderer)
    await service.revoke(_admin(), RevokeWaiver(catalogue.rule, "đã tuyển thêm người"))
    assert await _open_waivers(app_engine, catalogue.rule) == 0
    with pytest.raises(ConflictError):
        await _grant(app_engine, user_id, catalogue.orderer, catalogue.payer)
    assert await _audit_actions(app_engine, catalogue.rule) == [
        ("platform.sod.waive", REASON),
        ("platform.sod.waiver_confirm", "đã xem, đồng ý"),
        ("platform.sod.waiver_revoke", "đã tuyển thêm người"),
    ]


async def test_revoking_without_an_open_waiver_is_not_found(
    app_engine: AsyncEngine, catalogue: _Catalogue
) -> None:
    with pytest.raises(NotFoundError):
        await _service(app_engine).revoke(_admin(), RevokeWaiver(catalogue.rule, "x"))
    assert await _audit_actions(app_engine, catalogue.rule) == []


async def test_a_revoke_waits_for_a_grant_relying_on_the_waiver_then_refuses(
    app_engine: AsyncEngine, catalogue: _Catalogue
) -> None:
    await _waived(app_engine, catalogue.rule)
    user_id = await _user(app_engine)
    await _grant(app_engine, user_id, catalogue.orderer)

    async with app_engine.connect() as grant_conn:
        grant = await grant_conn.begin()
        await _scoped(grant_conn)
        await grant_conn.execute(
            sa.update(tables.memberships)
            .where(tables.memberships.c.user_id == user_id)
            .values(role_keys=[catalogue.orderer, catalogue.payer])
        )
        revoke = asyncio.create_task(
            _service(app_engine).revoke(_admin(), RevokeWaiver(catalogue.rule, "race"))
        )
        await asyncio.sleep(1.0)
        assert not revoke.done(), "the revoke did not wait for the grant relying on the waiver"
        await grant.commit()
    with pytest.raises(ConflictError):
        await revoke
    assert await _open_waivers(app_engine, catalogue.rule) == 1


async def test_a_grant_arriving_during_a_revoke_waits_then_is_refused(
    app_engine: AsyncEngine, catalogue: _Catalogue
) -> None:
    await _waived(app_engine, catalogue.rule)
    user_id = await _user(app_engine)
    await _grant(app_engine, user_id, catalogue.orderer)

    async with app_engine.connect() as revoke_conn:
        revoking = await revoke_conn.begin()
        await _scoped(revoke_conn)
        await revoke_conn.execute(
            sa.text(
                "UPDATE platform.sod_waivers"
                " SET revoked_at = now(), revoked_by = :who, revoke_reason = 'race'"
                " WHERE rule_key = :k AND revoked_at IS NULL"
            ),
            {"who": uuid.uuid4(), "k": catalogue.rule},
        )
        grant = asyncio.create_task(_grant(app_engine, user_id, catalogue.orderer, catalogue.payer))
        await asyncio.sleep(1.0)
        assert not grant.done(), "the grant did not wait for the revoke in flight"
        await revoking.commit()
    with pytest.raises(ConflictError):
        await grant


# ---- a decision is history ---------------------------------------------------


async def test_a_waiver_can_be_revoked_once_and_never_rewritten_or_deleted(
    app_engine: AsyncEngine, catalogue: _Catalogue
) -> None:
    service = _service(app_engine)
    await service.waive(_admin(), WaiveRule(catalogue.rule, REASON))

    rewrites = (
        "UPDATE platform.sod_waivers SET reason = 'rewritten' WHERE rule_key = :k",
        "UPDATE platform.sod_waivers SET granted_by = gen_random_uuid() WHERE rule_key = :k",
        # Revoking is the one update allowed; it may not rewrite the decision
        # it closes on the way.
        "UPDATE platform.sod_waivers SET revoked_at = now(), revoked_by = gen_random_uuid(),"
        " revoke_reason = 'closing', reason = 'rewritten' WHERE rule_key = :k",
    )
    for statement in rewrites:
        with pytest.raises(IntegrityError, match="revoked, once"):
            async with app_engine.begin() as conn:
                await _scoped(conn)
                await conn.execute(sa.text(statement), {"k": catalogue.rule})

    await service.revoke(_admin(), RevokeWaiver(catalogue.rule, "không cần nữa"))
    with pytest.raises(IntegrityError, match="revoked, once"):
        async with app_engine.begin() as conn:
            await _scoped(conn)
            await conn.execute(
                sa.text(
                    "UPDATE platform.sod_waivers"
                    " SET revoked_at = NULL, revoked_by = NULL, revoke_reason = NULL"
                    " WHERE rule_key = :k"
                ),
                {"k": catalogue.rule},
            )
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with app_engine.begin() as conn:
            await _scoped(conn)
            await conn.execute(
                sa.text("DELETE FROM platform.sod_waivers WHERE rule_key = :k"),
                {"k": catalogue.rule},
            )
    # Reopening means a new decision, with its own reason and audit row.
    await service.waive(_admin(), WaiveRule(catalogue.rule, "lại thiếu người"))
    assert [a for a, _ in await _audit_actions(app_engine, catalogue.rule)] == [
        "platform.sod.waive",
        "platform.sod.waiver_revoke",
        "platform.sod.waive",
    ]


async def test_a_rule_made_a_floor_later_stops_honouring_its_open_waivers(
    app_engine: AsyncEngine, migrator_engine: AsyncEngine, catalogue: _Catalogue
) -> None:
    """Clearing `waivable` is the rule author's call, made in a migration.
    From then on the tenant's open waiver lifts nothing: fail closed."""
    await _waived(app_engine, catalogue.rule)
    async with migrator_engine.begin() as conn:
        await conn.execute(
            sa.update(tables.sod_rules)
            .where(tables.sod_rules.c.key == catalogue.rule)
            .values(waivable=False)
        )
    user_id = await _user(app_engine)

    with pytest.raises(ConflictError) as refused:
        await _grant(app_engine, user_id, catalogue.orderer, catalogue.payer)
    assert refused.value.details["rule"] == catalogue.rule


# ---- a second person (f381f1694395) --------------------------------------------


async def test_the_admin_who_proposed_a_waiver_cannot_confirm_it(
    app_engine: AsyncEngine, migrator_engine: AsyncEngine, catalogue: _Catalogue
) -> None:
    proposer = _admin()
    service = _service(app_engine)
    await service.waive(proposer, WaiveRule(catalogue.rule, REASON))

    with pytest.raises(ConflictError, match="second person"):
        await service.confirm(proposer, ConfirmWaiver(catalogue.rule, "tôi tự đồng ý"))

    listed = {r.key: r for r in await service.list_rules(_admin())}
    waiver = listed[catalogue.rule].waiver
    assert waiver is not None and waiver.confirmed_by is None
    assert await _audit_actions(app_engine, catalogue.rule) == [("platform.sod.waive", REASON)]
    # The database's rule, for every writer: not even the migrator confirms a
    # waiver in its proposer's name, or creates one already confirmed.
    with pytest.raises(IntegrityError, match="second_person"):
        async with migrator_engine.begin() as conn:
            await conn.execute(
                sa.update(tables.sod_waivers)
                .where(tables.sod_waivers.c.rule_key == catalogue.rule)
                .values(
                    confirmed_by=tables.sod_waivers.c.granted_by,
                    confirmed_at=sa.func.now(),
                    confirm_reason="x",
                )
            )
    with pytest.raises(IntegrityError, match="never on creation"):
        async with migrator_engine.begin() as conn:
            await conn.execute(
                sa.insert(tables.sod_waivers).values(
                    id=uuid.uuid4(),
                    tenant_id=BETA,
                    rule_key=catalogue.rule,
                    reason=REASON,
                    granted_by=uuid.uuid4(),
                    confirmed_by=uuid.uuid4(),
                    confirmed_at=sa.func.now(),
                    confirm_reason="x",
                )
            )


async def test_confirming_needs_the_scope_a_reason_and_a_waiver_waiting_for_it(
    app_engine: AsyncEngine, catalogue: _Catalogue
) -> None:
    service = _service(app_engine)
    with pytest.raises(NotFoundError):
        await service.confirm(_admin(), ConfirmWaiver(catalogue.rule, "ok"))
    await service.waive(_admin(), WaiveRule(catalogue.rule, REASON))
    without_scope = AccessContext(
        tenant_id=ALPHA,
        workspace_id=ALPHA_WS,
        principal_id=uuid.uuid4(),
        roles=frozenset({"org_admin"}),
        scopes=frozenset({"platform.roles.read"}),
        plan_id="professional",
    )
    with pytest.raises(PermissionDeniedError):
        await service.confirm(without_scope, ConfirmWaiver(catalogue.rule, "ok"))
    with pytest.raises(DomainError, match="reason"):
        await service.confirm(_admin(), ConfirmWaiver(catalogue.rule, "  "))

    await service.confirm(_admin(), ConfirmWaiver(catalogue.rule, "ok"))
    # Confirmed once; a second confirmation finds nothing waiting, and the
    # confirmation itself cannot be rewritten.
    with pytest.raises(NotFoundError):
        await service.confirm(_admin(), ConfirmWaiver(catalogue.rule, "again"))
    with pytest.raises(IntegrityError, match="confirmed, once"):
        async with app_engine.begin() as conn:
            await _scoped(conn)
            await conn.execute(
                sa.text(
                    "UPDATE platform.sod_waivers SET confirmed_by = gen_random_uuid()"
                    " WHERE rule_key = :k"
                ),
                {"k": catalogue.rule},
            )


# ---- a role cannot change under a membership (f381f1694395) --------------------


async def _membership_id(engine: AsyncEngine, user_id: uuid.UUID) -> uuid.UUID:
    async with engine.connect() as conn:
        found = await conn.scalar(
            sa.select(tables.memberships.c.id).where(tables.memberships.c.user_id == user_id)
        )
    assert isinstance(found, uuid.UUID)
    return found


async def _set_scopes(engine: AsyncEngine, table: sa.Table, key: str, scopes: list[str]) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.update(table).where(table.c.key == key).values(scopes=scopes))


async def _scopes_of(engine: AsyncEngine, table: sa.Table, key: str) -> list[str]:
    async with engine.connect() as conn:
        found = await conn.scalar(sa.select(table.c.scopes).where(table.c.key == key))
    return list(found or [])


async def test_widening_a_role_past_a_rule_is_refused_naming_the_memberships(
    app_engine: AsyncEngine, migrator_engine: AsyncEngine, catalogue: _Catalogue
) -> None:
    user_id = await _user(app_engine)
    await _grant(app_engine, user_id, catalogue.orderer)
    membership = await _membership_id(migrator_engine, user_id)
    before = await _scopes_of(migrator_engine, tables.roles, catalogue.orderer)
    pay = (await _scopes_of(migrator_engine, tables.roles, catalogue.payer))[0]

    with pytest.raises(IntegrityError, match="would break") as refused:
        await _set_scopes(migrator_engine, tables.roles, catalogue.orderer, [*before, pay])
    assert str(membership) in str(refused.value)
    assert catalogue.rule in str(refused.value)
    assert await _scopes_of(migrator_engine, tables.roles, catalogue.orderer) == before

    # Narrowing, or widening by a scope no rule pairs, is not a conflict.
    await _set_scopes(migrator_engine, tables.roles, catalogue.orderer, [*before, "test.unpaired"])
    await _set_scopes(migrator_engine, tables.roles, catalogue.orderer, before)


async def test_a_confirmed_waiver_lets_the_role_change(
    app_engine: AsyncEngine, migrator_engine: AsyncEngine, catalogue: _Catalogue
) -> None:
    user_id = await _user(app_engine)
    await _grant(app_engine, user_id, catalogue.orderer)
    before = await _scopes_of(migrator_engine, tables.roles, catalogue.orderer)
    pay = (await _scopes_of(migrator_engine, tables.roles, catalogue.payer))[0]
    await _service(app_engine).waive(_admin(), WaiveRule(catalogue.rule, REASON))
    # Proposed only: still refused.
    with pytest.raises(IntegrityError, match="would break"):
        await _set_scopes(migrator_engine, tables.roles, catalogue.orderer, [*before, pay])

    await _service(app_engine).confirm(_admin(), ConfirmWaiver(catalogue.rule, "ok"))
    await _set_scopes(migrator_engine, tables.roles, catalogue.orderer, [*before, pay])
    await _set_scopes(migrator_engine, tables.roles, catalogue.orderer, before)


async def test_widening_a_permission_set_past_a_rule_is_refused(
    app_engine: AsyncEngine, migrator_engine: AsyncEngine, catalogue: _Catalogue
) -> None:
    extra = f"test_set_{uuid.uuid4().hex[:10]}"
    async with migrator_engine.begin() as conn:
        await conn.execute(sa.insert(tables.permission_sets).values(key=extra, name=extra))
    try:
        user_id = await _user(app_engine)
        await _grant(app_engine, user_id, catalogue.orderer)
        async with migrator_engine.begin() as conn:
            await conn.execute(
                sa.update(tables.memberships)
                .where(tables.memberships.c.user_id == user_id)
                .values(permission_set_keys=[extra])
            )
        membership = await _membership_id(migrator_engine, user_id)
        pay = (await _scopes_of(migrator_engine, tables.roles, catalogue.payer))[0]

        with pytest.raises(IntegrityError, match="would break") as refused:
            await _set_scopes(migrator_engine, tables.permission_sets, extra, [pay])
        assert str(membership) in str(refused.value)
    finally:
        async with migrator_engine.begin() as conn:
            await conn.execute(
                sa.update(tables.memberships)
                .where(tables.memberships.c.permission_set_keys.op("?")(extra))
                .values(permission_set_keys=[])
            )
            await conn.execute(
                sa.delete(tables.permission_sets).where(tables.permission_sets.c.key == extra)
            )
