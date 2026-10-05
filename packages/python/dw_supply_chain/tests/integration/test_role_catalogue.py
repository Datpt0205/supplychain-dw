"""Integration: the role catalogue grants every scope this context checks, and
the database keeps conflicting duties out of one membership.

Read from the migrated database, not from the migration's text: a check over
the source cannot see a later migration that rewrote the rows. The scopes are
collected from the code itself (the handlers' constants and every duty), so
a scope a handler checks and no role grants fails here, before a deployed
tenant finds a step nobody can take. Which role pairs conflict is asked of
`platform.sod_violation`, the function the membership trigger itself calls.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls

from dw_supply_chain.action_duties import CaseDuty
from dw_supply_chain.application import handlers

pytestmark = pytest.mark.integration

_OPERATING_ROLES = ("sc_operator", "sc_finance", "sc_qc", "sc_logistics", "sc_warehouse")
_SC_ROLES = ("sc_viewer", *_OPERATING_ROLES, "sc_process_admin")
_OPERATIONS = {
    "supply_chain.po_case.write",
    "supply_chain.supplier_update.write",
    "supply_chain.delay_impact.write",
    handlers.DOCUMENT_WRITE,
} | {handlers.duty_scope(duty) for duty in CaseDuty}
_POLICY_WRITES = {
    "supply_chain.sla_policy.write",
    "supply_chain.approval_matrix.write",
    "supply_chain.brief_policy.write",
    "supply_chain.action_duties.write",
}


def _checked_scopes() -> set[str]:
    constants = {
        value
        for value in vars(handlers).values()
        if isinstance(value, str) and re.fullmatch(r"supply_chain\.[a-z_]+\.(read|write)", value)
    }
    return constants | {handlers.duty_scope(duty) for duty in CaseDuty}


@pytest.fixture
async def engine(db_urls: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield engine
    await engine.dispose()


async def _catalogue(engine: AsyncEngine) -> dict[str, set[str]]:
    async with engine.connect() as conn:
        rows = (await conn.execute(text("SELECT key, scopes FROM platform.roles"))).all()
    return {row.key: set(row.scopes) for row in rows}


async def _violation(engine: AsyncEngine, *roles: str) -> str | None:
    async with engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT rule_key FROM platform.sod_violation("
                    "NULL, CAST(:roles AS jsonb), '[]'::jsonb)"
                ),
                {"roles": json.dumps(list(roles))},
            )
        ).scalar()


def test_the_scope_collector_sees_what_the_handlers_check() -> None:
    """A collector that found nothing would make every test below pass."""
    checked = _checked_scopes()
    assert {"supply_chain.po_case.read", "supply_chain.duty.finance"} <= checked
    assert checked >= _OPERATIONS | _POLICY_WRITES


async def test_every_scope_the_context_checks_is_granted_by_a_supply_chain_role(
    engine: AsyncEngine,
) -> None:
    catalogue = await _catalogue(engine)
    granted = set().union(*(catalogue[key] for key in _SC_ROLES))
    assert _checked_scopes() - granted == set()


async def test_every_supply_chain_role_can_at_least_read(engine: AsyncEngine) -> None:
    catalogue = await _catalogue(engine)
    viewer = catalogue["sc_viewer"]
    assert viewer, "sc_viewer grants nothing"
    assert all(scope.endswith(".read") for scope in viewer)
    for key in _SC_ROLES:
        assert viewer <= catalogue[key], key


async def test_everyone_running_cases_can_raise_and_clear_an_exception(
    engine: AsyncEngine,
) -> None:
    """A QC inspector who finds the line blocked must be able to say so."""
    catalogue = await _catalogue(engine)
    exceptions = handlers.duty_scope(CaseDuty.EXCEPTIONS)
    for key in _OPERATING_ROLES:
        assert exceptions in catalogue[key], key


async def test_every_role_reads_documents_and_only_the_operating_roles_add_them(
    engine: AsyncEngine,
) -> None:
    """Whoever does a step uploads its paperwork; the process admin, who sets
    the rules, does not (sod_sc_rules_vs_operations)."""
    catalogue = await _catalogue(engine)
    for key in _SC_ROLES:
        assert handlers.DOCUMENT_READ in catalogue[key], key
    writers = {key for key, scopes in catalogue.items() if handlers.DOCUMENT_WRITE in scopes}
    assert writers == set(_OPERATING_ROLES)


async def test_the_process_admin_cannot_also_hold_the_document_write(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        right = await conn.scalar(
            text("SELECT right_scopes FROM platform.sod_rules WHERE key = :k"),
            {"k": "sod_sc_rules_vs_operations"},
        )
    assert handlers.DOCUMENT_WRITE in right


async def test_supply_chain_roles_grant_nothing_outside_the_context(engine: AsyncEngine) -> None:
    """Approval authority stays on the platform ladder: an operator is not
    thereby an approver of anything."""
    catalogue = await _catalogue(engine)
    for key in _SC_ROLES:
        assert all(scope.startswith("supply_chain.") for scope in catalogue[key]), key


async def test_no_single_role_both_sets_the_rules_and_runs_cases(engine: AsyncEngine) -> None:
    for key, scopes in (await _catalogue(engine)).items():
        assert not (scopes & _OPERATIONS and scopes & _POLICY_WRITES), key


@pytest.mark.parametrize(
    ("roles", "rule"),
    [
        (("sc_operator", "sc_finance"), "sod_sc_ordering_vs_payment"),
        (("sc_warehouse", "sc_finance"), "sod_sc_receiving_vs_payment"),
        (("sc_operator", "sc_warehouse"), "sod_sc_ordering_vs_receiving"),
        (("sc_operator", "sc_qc"), "sod_sc_ordering_vs_qc"),
        *((("sc_process_admin", role), "sod_sc_rules_vs_operations") for role in _OPERATING_ROLES),
    ],
)
async def test_conflicting_roles_cannot_meet_in_one_membership(
    engine: AsyncEngine, roles: tuple[str, ...], rule: str
) -> None:
    assert await _violation(engine, *roles) == rule


@pytest.mark.parametrize(
    "roles",
    [
        ("sc_viewer", "sc_operator"),
        ("sc_finance", "sc_qc"),
        ("sc_logistics", "sc_warehouse"),
        ("sc_qc", "sc_logistics"),
        ("sc_viewer", "sc_process_admin"),
    ],
)
async def test_roles_without_a_conflict_can_be_held_together(
    engine: AsyncEngine, roles: tuple[str, ...]
) -> None:
    assert await _violation(engine, *roles) is None


async def test_a_small_tenant_may_waive_every_supply_chain_rule(engine: AsyncEngine) -> None:
    """They are business segregation, not platform floors (6b26771e549d): a
    company too small to staff both sides lifts one on the record. A rule
    left unmarked would be a floor nobody decided on."""
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT key, waivable FROM platform.sod_rules WHERE left(key, 7) = 'sod_sc_'")
            )
        ).all()
    assert len(rows) == 5
    assert all(row.waivable for row in rows), [row.key for row in rows if not row.waivable]
